"""Sign-in for Claude Code: a small OAuth 2.1 authorization server (authorization code + PKCE,
dynamic client registration, rotating refresh tokens), as the MCP authorization spec expects.

Flow: Claude Code calls /mcp, gets 401, reads /.well-known/oauth-protected-resource, registers
itself at /oauth/register, then opens the browser at /oauth/authorize. The person signs in to
the web app (Entra SSO or local account) and approves on the consent page. Claude Code swaps
the code for tokens at /oauth/token. Each approval is one "device" an admin can revoke.

Also: Entra ID (OIDC) sign-in for the web app itself, when configured.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
import secrets
import uuid
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import accounts, audit, config
from .db import OAuthClient, OAuthCode, Token, User, new_session, utcnow
from .perms import Actor
from .security import SESSION_COOKIE, consteq, create_web_session, issue_token, load_web_session, resolve_token, sha256_hex

router = APIRouter()


def _base() -> str:
    return config.settings.base_url


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource() -> dict[str, Any]:
    return {"resource": f"{_base()}/mcp", "authorization_servers": [_base()],
            "bearer_methods_supported": ["header"], "scopes_supported": ["buckets"],
            "resource_name": "Artifact Buckets"}


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
def as_metadata() -> dict[str, Any]:
    b = _base()
    return {"issuer": b, "authorization_endpoint": f"{b}/oauth/authorize", "token_endpoint": f"{b}/oauth/token",
            "registration_endpoint": f"{b}/oauth/register", "revocation_endpoint": f"{b}/oauth/revoke",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["buckets"]}


def _redirect_ok(uri: str) -> bool:
    try:
        p = urlparse(uri)
    except ValueError:
        return False
    if p.fragment:
        return False
    if p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    # Any other site must be listed by the admin, so nobody can register a client that sends a person's
    # sign-in to their own server (consent phishing).
    return p.scheme == "https" and bool(p.hostname) and p.hostname.lower() in config.settings.oauth_redirect_hosts


def _same_redirect(registered: list[str], uri: str) -> bool:
    """Exact match, except loopback redirects may use any port (RFC 8252 section 7.3)."""
    if uri in registered:
        return True
    p = urlparse(uri)
    if p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1"):
        for r in registered:
            q = urlparse(r)
            if q.scheme == "http" and q.hostname == p.hostname and q.path == p.path:
                return True
    return False


@router.post("/oauth/register")
async def register(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    uris = body.get("redirect_uris") or []
    if not isinstance(uris, list) or not uris or len(uris) > 10 or not all(isinstance(u, str) and len(u) <= 1024 and _redirect_ok(u) for u in uris):
        return JSONResponse({"error": "invalid_redirect_uri",
                             "error_description": "Use http://localhost or https redirect URIs"}, status_code=400)
    name = str(body.get("client_name") or "Claude Code")[:128]
    cid = "abc_" + secrets.token_urlsafe(16)
    with new_session() as db:
        db.add(OAuthClient(client_id=cid, name=name, redirect_uris=uris))
        db.commit()
    return JSONResponse({"client_id": cid, "client_name": name, "redirect_uris": uris,
                         "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                         "token_endpoint_auth_method": "none", "client_id_issued_at": int(utcnow().timestamp())},
                        status_code=201)


def _check_authorize(db: Session, q: dict[str, str]) -> tuple[OAuthClient | None, str | None]:
    client = db.get(OAuthClient, q.get("client_id", ""))
    if client is None:
        return None, "Unknown client"
    if not _same_redirect(client.redirect_uris, q.get("redirect_uri", "")):
        return None, "Redirect URI is not registered for this client"
    if q.get("response_type") != "code":
        return client, "unsupported_response_type"
    if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
        return client, "PKCE with S256 is required"
    if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", q["code_challenge"]):
        return client, "code_challenge is not a valid S256 challenge"
    return client, None


def _err_redirect(redirect_uri: str, state: str | None, error: str, desc: str = "") -> RedirectResponse:
    params = {"error": error, **({"error_description": desc} if desc else {}), **({"state": state} if state else {})}
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(redirect_uri + sep + urlencode(params), status_code=302)


@router.get("/oauth/authorize")
def authorize(request: Request) -> Response:
    q = dict(request.query_params)
    with new_session() as db:
        client, err = _check_authorize(db, q)
        if client is None:
            return JSONResponse({"error": "invalid_request", "error_description": err}, status_code=400)
        if err:
            return _err_redirect(q["redirect_uri"], q.get("state"), "invalid_request", err)
        sess = load_web_session(db, request.cookies.get(SESSION_COOKIE))
        if sess is None:
            nxt = "/oauth/authorize?" + urlencode(q)
            return RedirectResponse("/login?" + urlencode({"next": nxt}), status_code=302)
        # The web app's consent page reads these and posts back to /oauth/approve.
        return RedirectResponse("/consent?" + urlencode({**q, "client_name": client.name}), status_code=302)


@router.post("/oauth/approve")
def approve(request: Request, client_id: str = Form(...), redirect_uri: str = Form(...), state: str = Form(""),
            code_challenge: str = Form(...), code_challenge_method: str = Form("S256"), response_type: str = Form("code"),
            decision: str = Form(...), csrf: str = Form(...), device_name: str = Form("")) -> Response:
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "code_challenge": code_challenge,
         "code_challenge_method": code_challenge_method, "response_type": response_type}
    with new_session() as db:
        client, err = _check_authorize(db, q)
        if client is None:
            return JSONResponse({"error": "invalid_request", "error_description": err}, status_code=400)
        sess = load_web_session(db, request.cookies.get(SESSION_COOKIE))
        if sess is None or not consteq(sess.csrf, csrf):
            return JSONResponse({"error": "access_denied", "error_description": "Sign in again"}, status_code=403)
        if err:
            return _err_redirect(redirect_uri, state, "invalid_request", err)
        user = db.get(User, sess.user_id)
        if user is None or user.status != "active":
            return JSONResponse({"error": "access_denied"}, status_code=403)
        actor = Actor(user=user, via="web", ip=request.client.host if request.client else None)
        if decision != "allow":
            audit.log(db, actor, "oauth.deny", client.name)
            db.commit()
            return _err_redirect(redirect_uri, state, "access_denied")
        code = secrets.token_urlsafe(32)
        db.add(OAuthCode(code_hash=sha256_hex(code), client_id=client.client_id, user_id=user.id, redirect_uri=redirect_uri,
                         code_challenge=code_challenge, expires_at=utcnow() + dt.timedelta(minutes=5)))
        if device_name:
            client.name = device_name[:128]
        audit.log(db, actor, "oauth.approve", client.name)
        db.commit()
    params = {"code": code, **({"state": state} if state else {})}
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(redirect_uri + sep + urlencode(params), status_code=302)


def _tokens(db: Session, user: User, client: OAuthClient, grant_id: str) -> dict[str, Any]:
    s = config.settings
    access, _ = issue_token(db, user, name=client.name, kind="oauth_access", client_id=client.client_id, grant_id=grant_id,
                            ttl=dt.timedelta(minutes=s.oauth_access_minutes))
    refresh, _ = issue_token(db, user, name=client.name, kind="oauth_refresh", client_id=client.client_id, grant_id=grant_id,
                             ttl=dt.timedelta(days=s.oauth_refresh_days))
    return {"access_token": access, "token_type": "Bearer", "expires_in": s.oauth_access_minutes * 60,
            "refresh_token": refresh, "scope": "buckets"}


def _tok_err(error: str, desc: str = "", status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, **({"error_description": desc} if desc else {})}, status_code=status,
                        headers={"Cache-Control": "no-store"})


@router.post("/oauth/token")
async def token(request: Request) -> Response:
    form = dict(await request.form())
    gt = form.get("grant_type")
    with new_session() as db:
        client = db.get(OAuthClient, str(form.get("client_id", "")))
        if client is None:
            return _tok_err("invalid_client", status=401)
        if gt == "authorization_code":
            code = db.get(OAuthCode, sha256_hex(str(form.get("code", ""))))
            if code is None or code.used or code.expires_at < utcnow() or code.client_id != client.client_id:
                return _tok_err("invalid_grant", "Code is invalid or expired")
            if code.redirect_uri != form.get("redirect_uri"):
                return _tok_err("invalid_grant", "redirect_uri does not match")
            verifier = str(form.get("code_verifier", ""))
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not verifier or not consteq(digest, code.code_challenge):
                return _tok_err("invalid_grant", "PKCE check failed")
            code.used = True
            user = db.get(User, code.user_id)
            if user is None or user.status != "active":
                return _tok_err("invalid_grant", "Account is not active")
            out = _tokens(db, user, client, uuid.uuid4().hex)
            audit.log(db, Actor(user=user, via="api"), "oauth.token", client.name)
            db.commit()
            return JSONResponse(out, headers={"Cache-Control": "no-store"})
        if gt == "refresh_token":
            old = resolve_token(db, str(form.get("refresh_token", "")), kinds=("oauth_refresh",))
            if old is None or old.client_id != client.client_id:
                # Reuse of a rotated refresh token: revoke the whole sign-in.
                stale = db.scalar(select(Token).where(Token.token_hash == sha256_hex(str(form.get("refresh_token", "")))))
                if stale is not None and stale.grant_id:
                    for t in db.scalars(select(Token).where(Token.grant_id == stale.grant_id)):
                        t.revoked = True
                    db.commit()
                return _tok_err("invalid_grant", "Refresh token is invalid")
            user = db.get(User, old.user_id)
            if user is None or user.status != "active":
                return _tok_err("invalid_grant", "Account is not active")
            for t in db.scalars(select(Token).where(Token.grant_id == old.grant_id, Token.revoked.is_(False))):
                t.revoked = True
            out = _tokens(db, user, client, old.grant_id or uuid.uuid4().hex)
            db.commit()
            return JSONResponse(out, headers={"Cache-Control": "no-store"})
        return _tok_err("unsupported_grant_type")


@router.post("/oauth/revoke")
async def revoke(request: Request) -> Response:
    form = dict(await request.form())
    with new_session() as db:
        t = db.scalar(select(Token).where(Token.token_hash == sha256_hex(str(form.get("token", "")))))
        if t is not None and t.kind.startswith("oauth"):
            for x in db.scalars(select(Token).where(Token.grant_id == t.grant_id)):
                x.revoked = True
            db.commit()
    return Response(status_code=200)


# ---- Entra ID sign-in for the web app -------------------------------------------

_oidc_client = None


def _oidc():
    global _oidc_client
    if _oidc_client is None:
        from authlib.integrations.starlette_client import OAuth
        oauth = OAuth()
        s = config.settings
        oauth.register("entra", client_id=s.oidc_client_id, client_secret=s.oidc_client_secret,
                       server_metadata_url=s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
                       client_kwargs={"scope": "openid profile email"})
        _oidc_client = oauth.entra
    return _oidc_client


def match_oidc_user(db: Session, claims: dict[str, Any]) -> User | None:
    """Find the person behind an Entra sign-in: on-prem SID first (set by Entra Connect), then UPN/email."""
    sid = claims.get("onprem_sid")
    if sid:
        u = db.scalar(select(User).where(User.ad_sid == sid))
        if u:
            return u
    for key in ("preferred_username", "upn", "email"):
        v = (claims.get(key) or "").lower()
        if not v:
            continue
        u = db.scalar(select(User).where(User.email == v))
        if u:
            return u
        local = v.split("@")[0]
        u = db.scalar(select(User).where(User.username == local, User.source == "ad"))
        if u:
            return u
    return None


@router.get("/auth/oidc/login")
async def oidc_login(request: Request, next: str = "/") -> Response:
    if not config.settings.oidc_enabled:
        return JSONResponse({"error": "Entra sign-in is not configured"}, status_code=404)
    from starlette.responses import RedirectResponse as R
    resp: R = await _oidc().authorize_redirect(request, f"{_base()}/auth/oidc/callback")
    resp.set_cookie("ab_next", next if next.startswith("/") and not next.startswith("//") else "/",
                    httponly=True, samesite="lax", secure=config.settings.secure_cookies, max_age=600)
    return resp


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request) -> Response:
    if not config.settings.oidc_enabled:
        return JSONResponse({"error": "Entra sign-in is not configured"}, status_code=404)
    tok = await _oidc().authorize_access_token(request)
    claims = tok.get("userinfo") or {}
    with new_session() as db:
        user = match_oidc_user(db, claims)
        if user is None or user.status != "active":
            audit.log(db, None, "signin.oidc", str(claims.get("preferred_username", "")), ok=False)
            db.commit()
            return RedirectResponse("/login?error=not_allowed", status_code=302)
        raw, _ = create_web_session(db, user)
        accounts.touch(db, user)
        audit.log(db, Actor(user=user, via="web"), "signin.oidc", user.username)
        db.commit()
    nxt = request.cookies.get("ab_next") or "/"
    resp = RedirectResponse(nxt, status_code=302)
    set_session_cookie(resp, raw)
    resp.delete_cookie("ab_next")
    return resp


def set_session_cookie(resp: Response, raw: str) -> None:
    # Lax so the browser keeps the session when Claude Code's sign-in link opens the app.
    resp.set_cookie(SESSION_COOKIE, raw, httponly=True, samesite="lax", secure=config.settings.secure_cookies,
                    max_age=config.settings.session_hours * 3600, path="/")
