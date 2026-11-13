"""A. Accounts and sign-in, K. Security baseline."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import ADMIN_PW, PW, audit_events, bearer, login, save, uid_of


# ---- A1 ------------------------------------------------------------------------------
def test_A1_bootstrap_admin_created(admin):
    me = admin.get("/api/auth/me").json()["user"]
    assert me["username"] == "admin" and me["system_role"] == "admin"


def test_A1_no_bootstrap_when_users_exist(env, app):
    """Restarting with a different bootstrap password must not reset or add an admin."""
    import os
    from app import config
    from app.main import create_app
    os.environ["AB_BOOTSTRAP_PASSWORD"] = "another-long-password"
    config.reload_settings()
    with TestClient(create_app()):
        pass
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "admin", "password": "another-long-password"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).status_code == 200


# ---- A2 ------------------------------------------------------------------------------
def test_A2_cookie_flags_and_csrf_value(raw):
    r = raw.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert r.status_code == 200 and r.json()["csrf"]
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc and "secure" not in sc


def test_A2_secure_cookie_and_hsts_in_prod(env, monkeypatch):
    monkeypatch.setenv("AB_SECURE_COOKIES", "true")
    from app import config
    config.reload_settings()
    from app.main import create_app
    a = create_app()
    with TestClient(a) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
        assert "secure" in r.headers["set-cookie"].lower()
        assert "max-age" in r.headers["strict-transport-security"].lower()


def test_A2_wrong_password_401_and_audited(raw, admin):
    r = raw.post("/api/auth/login", json={"username": "admin", "password": "wrong-password-123"})
    assert r.status_code == 401
    r = raw.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password-123"})
    assert r.status_code == 401
    ev = [e for e in audit_events(admin, action="signin.password") if not e["ok"]]
    assert {e["target"] for e in ev} >= {"admin", "nobody"}


def test_A2_suspended_cannot_sign_in(app, admin, make_user):
    make_user("alice")
    admin.patch(f"/api/users/{uid_of(admin, 'alice')}", json={"status": "suspended"})
    r = TestClient(app).post("/api/auth/login", json={"username": "alice", "password": PW})
    assert r.status_code == 401


# ---- A3 CSRF on every unsafe cookie-authenticated route --------------------------------
def _unsafe_api_routes(app):
    from app import api
    out = []
    for r in api.router.routes:
        p = getattr(r, "path", "")
        for m in getattr(r, "methods", set()) or set():
            if m in ("POST", "PUT", "PATCH", "DELETE") and p.startswith("/api/") and p not in ("/api/auth/login", "/api/auth/logout"):
                out.append((m, p))
    return sorted(set(out))


def test_A3_every_unsafe_route_needs_csrf(app, admin):
    """Cookie present, X-CSRF-Token missing -> 403 on every POST/PUT/PATCH/DELETE under /api."""
    c = admin.c
    bad = []
    assert len(_unsafe_api_routes(app)) > 20
    for m, p in _unsafe_api_routes(app):
        url = (p.replace("{grant_id}", "1").replace("{gid}", "1").replace("{user_id}", "1").replace("{tid}", "1")
                .replace("{rid}", "1").replace("{vid}", "1").replace("{action}", "add").replace("{username}", "x"))
        r = c.request(m, url, json={})
        if r.status_code != 403:
            bad.append((m, p, r.status_code))
    assert not bad, f"routes that accept a cookie without the CSRF header: {bad}"


def test_A3_logout_needs_csrf(admin):
    """POST /api/auth/logout is a cookie-authenticated POST; without the header it must be 403 and the session must survive."""
    r = admin.c.post("/api/auth/logout")
    assert r.status_code == 403
    assert admin.get("/api/auth/me").json()["user"] is not None


def test_A3_wrong_csrf_rejected(admin):
    r = admin.c.post("/api/fs/mkdir", json={"path": "/users/admin/projects/x"}, headers={"X-CSRF-Token": "nope"})
    assert r.status_code == 403


def test_A3_oauth_approve_needs_csrf(app, admin):
    from conftest import pkce
    c = TestClient(app, follow_redirects=False)
    cid = c.post("/oauth/register", json={"redirect_uris": ["http://127.0.0.1:5000/cb"]}).json()["client_id"]
    _, ch = pkce()
    form = {"client_id": cid, "redirect_uri": "http://127.0.0.1:5000/cb", "code_challenge": ch,
            "code_challenge_method": "S256", "response_type": "code", "decision": "allow", "csrf": "wrong"}
    assert admin.c.post("/oauth/approve", data=form).status_code == 403


# ---- A4 --------------------------------------------------------------------------------
def test_A4_logout_kills_cookie_server_side(app, admin):
    cookie = admin.c.cookies.get("ab_session")
    assert admin.post("/api/auth/logout").status_code == 200
    c = TestClient(app)
    c.cookies.set("ab_session", cookie)
    assert c.get("/api/fs/list").status_code == 401


# ---- A5 (Entra) - logic that can be exercised without Entra --------------------------
def test_A5_oidc_match_prefers_sid_then_upn(app, admin, make_user):
    from app import db as dbm, oauth
    from app.db import User
    make_user("alice")
    with dbm.new_session() as s:
        a = s.scalar(select(User).where(User.username == "alice"))
        a.ad_sid, a.source, a.email = "S-1-5-21-1-2-3-1001", "ad", "alice@corp.example.com"
        s.commit()
        assert oauth.match_oidc_user(s, {"onprem_sid": "S-1-5-21-1-2-3-1001"}).username == "alice"
        assert oauth.match_oidc_user(s, {"preferred_username": "ALICE@corp.example.com"}).username == "alice"
        assert oauth.match_oidc_user(s, {"preferred_username": "zed@corp.example.com"}) is None


class _FakeEntra:
    def __init__(self, claims):
        self.claims = claims

    async def authorize_redirect(self, request, redirect_uri):
        from starlette.responses import RedirectResponse
        return RedirectResponse("https://login.example/authorize", status_code=302)

    async def authorize_access_token(self, request):
        return {"userinfo": self.claims}


@pytest.fixture()
def oidc_on(app, monkeypatch):
    from app import config, oauth
    monkeypatch.setattr(config.settings, "oidc_issuer", "https://login.example/tenant/v2.0")
    monkeypatch.setattr(config.settings, "oidc_client_id", "cid")
    fake = _FakeEntra({})
    monkeypatch.setattr(oauth, "_oidc", lambda: fake)
    return fake


@pytest.mark.parametrize("nxt", ["/\\evil.example", "/\\/evil.example", "//evil.example", "https://evil.example"])
def test_A5_oidc_next_is_not_an_open_redirect(app, admin, make_user, oidc_on, nxt):
    """ab_next is replayed as the Location after sign-in. '/\\evil.example' is treated as '//evil.example' by browsers."""
    from app import db as dbm
    from app.db import User
    make_user("alice")
    with dbm.new_session() as s:
        u = s.scalar(select(User).where(User.username == "alice"))
        u.ad_sid = "S-1-5-21-9-9-9-1"
        s.commit()
    oidc_on.claims = {"onprem_sid": "S-1-5-21-9-9-9-1"}
    c = TestClient(app, follow_redirects=False)
    r = c.get("/auth/oidc/login", params={"next": nxt})
    assert r.status_code == 302
    r = c.get("/auth/oidc/callback")
    loc = r.headers["location"]
    # Starlette percent-encodes the backslash, so the browser stays on this origin.
    assert not loc.startswith(("//", "/\\", "/\t")) and "://" not in loc, f"redirected to {loc!r}"


def test_A5_oidc_unknown_and_suspended_refused_and_audited(app, admin, make_user, oidc_on):
    make_user("bob")
    admin.patch(f"/api/users/{uid_of(admin, 'bob')}", json={"status": "suspended"})
    c = TestClient(app, follow_redirects=False)
    oidc_on.claims = {"preferred_username": "nobody@corp.example.com"}
    assert "not_allowed" in c.get("/auth/oidc/callback").headers["location"]
    from app import db as dbm
    from app.db import User
    with dbm.new_session() as s:
        u = s.scalar(select(User).where(User.username == "bob"))
        u.source = "ad"
        s.commit()
    oidc_on.claims = {"preferred_username": "bob@corp.example.com"}
    assert "not_allowed" in c.get("/auth/oidc/callback").headers["location"]
    ev = [e for e in audit_events(admin, action="signin.oidc") if not e["ok"]]
    assert len(ev) == 2


# ---- A6 ------------------------------------------------------------------------------
def test_A6_argon2id_and_min_length(admin):
    r = admin.post("/api/users", json={"username": "shorty", "password": "elevenchars"})
    assert r.status_code == 400
    r = admin.post("/api/users", json={"username": "longy", "password": "twelve-chars"})
    assert r.status_code == 200
    assert admin.patch(f"/api/users/{r.json()['id']}", json={"password": "short"}).status_code == 400
    from app import db as dbm
    from app.db import User
    with dbm.new_session() as s:
        assert s.scalar(select(User.password_hash).where(User.username == "longy")).startswith("$argon2id$")


# ---- K1 ------------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["/api/auth/me", "/api/fs/list", "/mcp", "/oauth/token", "/healthz", "/nope"])
def test_K1_security_headers(raw, url):
    r = raw.post(url) if url in ("/oauth/token",) else raw.get(url)
    h = r.headers
    assert "frame-ancestors 'none'" in h.get("content-security-policy", "")
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("referrer-policy") == "no-referrer"
    if url.startswith(("/api/", "/oauth/", "/mcp")):
        assert h.get("cache-control") == "no-store"


# ---- K2 ------------------------------------------------------------------------------
def test_K2_tokens_only_stored_as_sha256(app, admin, make_user):
    from conftest import oauth_sign_in
    alice = make_user("alice")
    pat = alice.post("/api/tokens", json={"name": "ci"}).json()["token"]
    oa = oauth_sign_in(app, alice)
    from app import db as dbm
    from app.db import OAuthCode, Token, WebSession
    from app.security import sha256_hex
    with dbm.new_session() as s:
        hashes = set(s.scalars(select(Token.token_hash)))
        assert sha256_hex(pat) in hashes and sha256_hex(oa["access_token"]) in hashes and sha256_hex(oa["refresh_token"]) in hashes
        for t in s.scalars(select(Token)):
            assert pat not in (t.name, t.hint) and len(t.hint) <= 4
        assert all(len(x) == 64 for x in s.scalars(select(OAuthCode.code_hash)))
        sess = alice.c.cookies.get("ab_session")
        assert s.get(WebSession, sess) is None and s.get(WebSession, sha256_hex(sess)) is not None
    # shown once: the list never includes it
    assert pat not in alice.get("/api/tokens").text
