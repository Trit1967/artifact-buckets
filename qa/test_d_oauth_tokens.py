"""D. Claude Code sign-in (OAuth 2.1 + PKCE), devices and personal tokens."""
import datetime as dt
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import MCP, PW, bearer, oauth_sign_in, pkce, save, uid_of

REDIR = "http://127.0.0.1:33418/callback"


def register(c, uris=(REDIR,)):
    r = c.post("/oauth/register", json={"client_name": "Claude Code", "redirect_uris": list(uris)})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def approve(web, cid, redirect=REDIR, challenge=None, decision="allow", state="st"):
    verifier, ch = pkce()
    ch = challenge or ch
    q = {"client_id": cid, "redirect_uri": redirect, "response_type": "code", "state": state,
         "code_challenge": ch, "code_challenge_method": "S256"}
    r = web.c.post("/oauth/approve", data={**q, "decision": decision, "csrf": web.csrf})
    return r, verifier


def code_from(r):
    return parse_qs(urlparse(r.headers["location"]).query)


# ---- D1 -----------------------------------------------------------------------------
def test_D1_discovery(raw):
    pr = raw.get("/.well-known/oauth-protected-resource").json()
    assert pr["resource"].endswith("/mcp") and pr["authorization_servers"]
    md = raw.get("/.well-known/oauth-authorization-server").json()
    assert md["code_challenge_methods_supported"] == ["S256"] and md["registration_endpoint"].endswith("/oauth/register")
    r = raw.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401 and 'resource_metadata="' in r.headers["www-authenticate"]


# ---- D2 -----------------------------------------------------------------------------
@pytest.mark.parametrize("uri", ["http://evil.example/cb", "http://localhost.evil.example/cb", "https://ok.example/cb#frag",
                                 "javascript:alert(1)", "file:///etc/passwd", "https:///nohost", "localhost/cb",
                                 "http://localhost@evil.example/cb", "custom-scheme://cb"])
def test_D2_bad_redirects(raw, uri):
    assert raw.post("/oauth/register", json={"redirect_uris": [uri]}).status_code == 400


@pytest.mark.parametrize("uri", ["http://localhost:1234/cb", "http://127.0.0.1/cb", "http://[::1]:9/cb"])
def test_D2_good_redirects(raw, uri):
    assert raw.post("/oauth/register", json={"redirect_uris": [uri]}).status_code == 201


def test_D2_https_redirect_only_when_admin_listed(raw, monkeypatch):
    """Spec change after UI-1 (consent phishing): an https redirect must be on AB_OAUTH_REDIRECT_HOSTS."""
    from app import config
    assert raw.post("/oauth/register", json={"redirect_uris": ["https://app.example/cb"]}).status_code == 400
    monkeypatch.setattr(config.settings, "oauth_redirect_hosts", ("app.example",))
    assert raw.post("/oauth/register", json={"redirect_uris": ["https://app.example/cb"]}).status_code == 201
    assert raw.post("/oauth/register", json={"redirect_uris": ["https://evil.example/cb"]}).status_code == 400


def test_D2_register_garbage_is_not_500(raw):
    for body in (b"[]", b'"x"', b"null", b'{"redirect_uris": "http://localhost/cb"}', b"\xff\xfe"):
        r = raw.post("/oauth/register", content=body, headers={"Content-Type": "application/json"})
        assert r.status_code == 400, (body, r.status_code)


# ---- D3 -----------------------------------------------------------------------------
def test_D3_authorize_flow(app, raw, make_user):
    alice = make_user("alice")
    cid = register(raw)
    _, ch = pkce()
    q = {"client_id": cid, "redirect_uri": REDIR, "response_type": "code", "code_challenge": ch, "code_challenge_method": "S256"}
    r = raw.get("/oauth/authorize", params=q)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=")
    nxt = parse_qs(urlparse(r.headers["location"]).query)["next"][0]
    assert nxt.startswith("/oauth/authorize?")
    r = alice.get(nxt)
    assert r.status_code == 302 and r.headers["location"].startswith("/consent?")
    # any loopback port works
    r, _ = approve(alice, cid, redirect="http://127.0.0.1:5555/callback")
    assert r.status_code == 302 and r.headers["location"].startswith("http://127.0.0.1:5555/callback?code=")
    # unregistered redirect: no redirect at all
    r, _ = approve(alice, cid, redirect="https://evil.example/callback")
    assert r.status_code == 400
    r = alice.get("/oauth/authorize", params={**q, "redirect_uri": "https://evil.example/callback"})
    assert r.status_code == 400
    # deny
    r, _ = approve(alice, cid, decision="deny")
    assert code_from(r)["error"] == ["access_denied"]


def test_D3_approve_without_session(app, raw):
    cid = register(raw)
    _, ch = pkce()
    r = raw.post("/oauth/approve", data={"client_id": cid, "redirect_uri": REDIR, "code_challenge": ch, "decision": "allow", "csrf": "x"})
    assert r.status_code == 403


# ---- D4 -----------------------------------------------------------------------------
def test_D4_pkce_s256_only_and_verifier(app, raw, make_user):
    alice = make_user("alice")
    cid = register(raw)
    q = {"client_id": cid, "redirect_uri": REDIR, "response_type": "code", "code_challenge": "abc", "code_challenge_method": "plain"}
    r = alice.get("/oauth/authorize", params=q)
    assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]
    r, verifier = approve(alice, cid)
    code = code_from(r)["code"][0]
    bad = raw.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                         "redirect_uri": REDIR, "code_verifier": "wrong" * 10})
    assert bad.status_code == 400
    ok = raw.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                        "redirect_uri": REDIR, "code_verifier": verifier})
    assert ok.status_code == 200
    again = raw.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                           "redirect_uri": REDIR, "code_verifier": verifier})
    assert again.status_code == 400


def test_D4_code_bound_to_client_and_redirect(app, raw, make_user):
    alice = make_user("alice")
    cid, other = register(raw), register(raw)
    r, verifier = approve(alice, cid)
    code = code_from(r)["code"][0]
    assert raw.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": other,
                                          "redirect_uri": REDIR, "code_verifier": verifier}).status_code == 400
    assert raw.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                          "redirect_uri": "http://127.0.0.1:1/callback", "code_verifier": verifier}).status_code == 400


def test_D4_code_expires_after_5_minutes(app, raw, make_user):
    alice = make_user("alice")
    cid = register(raw)
    r, verifier = approve(alice, cid)
    code = code_from(r)["code"][0]
    from app import db as dbm
    from app.db import OAuthCode
    from app.security import sha256_hex
    with dbm.new_session() as s:
        row = s.get(OAuthCode, sha256_hex(code))
        assert dt.timedelta(minutes=4) < row.expires_at - dt.datetime.utcnow() <= dt.timedelta(minutes=5, seconds=5)
        row.expires_at = dt.datetime.utcnow() - dt.timedelta(seconds=1)
        s.commit()
    assert raw.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                          "redirect_uri": REDIR, "code_verifier": verifier}).status_code == 400


def test_D4_long_pkce_challenge_is_not_500(app, raw, make_user):
    """code_challenge is stored in a 128-char column; an over-long one must be refused, not crash (PostgreSQL)."""
    alice = make_user("alice")
    alice.c.raise_server_exceptions = False
    cid = register(raw)
    r, _ = approve(alice, cid, challenge="A" * 300)
    assert r.status_code in (302, 400), r.status_code
    if r.status_code == 302:
        assert "error=" in r.headers["location"] or "code=" in r.headers["location"]


# ---- D5 -----------------------------------------------------------------------------
def test_D5_lifetimes(app, make_user):
    alice = make_user("alice")
    t = oauth_sign_in(app, alice)
    assert t["expires_in"] == 3600
    from app import db as dbm
    from app.db import Token
    from app.security import sha256_hex
    with dbm.new_session() as s:
        a = s.scalar(select(Token).where(Token.token_hash == sha256_hex(t["access_token"])))
        r = s.scalar(select(Token).where(Token.token_hash == sha256_hex(t["refresh_token"])))
        assert abs((a.expires_at - a.created_at) - dt.timedelta(minutes=60)) < dt.timedelta(seconds=5)
        assert abs((r.expires_at - r.created_at) - dt.timedelta(days=30)) < dt.timedelta(seconds=5)


def test_D5_rotation_reuse_and_revoke(app, raw, make_user):
    alice = make_user("alice")
    t = oauth_sign_in(app, alice)
    r1 = raw.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": t["client_id"]})
    assert r1.status_code == 200
    new = r1.json()
    # old access token died with the rotation? (not required) ; new one works
    ok = raw.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": f"Bearer {new['access_token']}"})
    assert ok.status_code == 200
    # reuse of the old refresh token revokes the whole sign-in
    r2 = raw.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": t["client_id"]})
    assert r2.status_code == 400
    dead = raw.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": f"Bearer {new['access_token']}"})
    assert dead.status_code == 401
    # revoke endpoint
    t2 = oauth_sign_in(app, alice)
    assert raw.post("/oauth/revoke", data={"token": t2["refresh_token"]}).status_code == 200
    assert raw.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": f"Bearer {t2['access_token']}"}).status_code == 401


def test_D5_refresh_token_not_usable_as_access(app, raw, make_user):
    alice = make_user("alice")
    t = oauth_sign_in(app, alice)
    assert raw.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": f"Bearer {t['refresh_token']}"}).status_code == 401
    assert raw.get("/api/fs/list", headers={"Authorization": f"Bearer {t['refresh_token']}"}).status_code == 401


def test_D5_access_token_expires(app, raw, make_user):
    alice = make_user("alice")
    t = oauth_sign_in(app, alice)
    from app import db as dbm
    from app.db import Token
    from app.security import sha256_hex
    with dbm.new_session() as s:
        a = s.scalar(select(Token).where(Token.token_hash == sha256_hex(t["access_token"])))
        a.expires_at = dt.datetime.utcnow() - dt.timedelta(seconds=1)
        s.commit()
    assert raw.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": f"Bearer {t['access_token']}"}).status_code == 401


# ---- D6 -----------------------------------------------------------------------------
def test_D6_devices(app, admin, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    t = oauth_sign_in(app, alice)
    devs = [d for d in alice.get("/api/tokens").json()["tokens"] if d["kind"] == "device"]
    assert len(devs) == 1
    # after a refresh still one device
    TestClient(app).post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": t["client_id"]})
    devs = [d for d in alice.get("/api/tokens").json()["tokens"] if d["kind"] == "device"]
    assert len(devs) == 1
    assert bob.delete(f"/api/tokens/{devs[0]['id']}").status_code == 404
    assert any(d["user"] == "alice" for d in admin.get("/api/tokens", params={"all": True}).json()["tokens"])
    assert admin.delete(f"/api/tokens/{devs[0]['id']}").status_code == 200
    assert [d for d in alice.get("/api/tokens").json()["tokens"] if d["kind"] == "device"] == []


# ---- D7 personal tokens ---------------------------------------------------------------
def test_D7_create_rules(app, make_user):
    alice = make_user("alice")
    for days in (0, 366, -1):
        assert alice.post("/api/tokens", json={"name": "x", "days": days}).status_code == 400
    for days in (1, 365):
        assert alice.post("/api/tokens", json={"name": "x", "days": days}).status_code == 200
    assert alice.post("/api/tokens", json={"name": "x", "scope": "admin"}).status_code == 400
    pat = alice.post("/api/tokens", json={"name": "x"}).json()["token"]
    assert bearer(app, pat).post("/api/tokens", json={"name": "y"}).status_code == 403
    oa = oauth_sign_in(app, alice)
    assert bearer(app, oa["access_token"]).post("/api/tokens", json={"name": "y"}).status_code == 403


def test_D7_read_scope_caps_at_viewer_on_every_write(app, make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/projects/p/f.txt", "1")
    save(alice, "/users/alice/projects/p/f.txt", "2")
    tok = alice.post("/api/tokens", json={"name": "ro", "scope": "read"}).json()["token"]
    c = bearer(app, tok)
    calls = [("post", "/api/fs/save-text", {"path": "/users/alice/inbox/x", "content": "x"}),
             ("post", "/api/fs/mkdir", {"path": "/users/alice/inbox/d"}),
             ("post", "/api/fs/move", {"src": "/users/alice/projects/p/f.txt", "dst": "/users/alice/inbox/f.txt"}),
             ("post", "/api/fs/delete", {"path": "/users/alice/projects/p/f.txt"}),
             ("post", "/api/fs/restore-version", {"path": "/users/alice/projects/p/f.txt", "version": 1}),
             ("post", "/api/fs/share", {"path": "/users/alice/projects/p", "kind": "user", "name": "admin", "role": "viewer"}),
             ("put", "/api/folders/settings", {"path": "/users/alice/projects/p", "keep_versions": 1}),
             ("post", "/api/routing", {"match": "project", "pattern": "*", "target": "/users/alice/inbox"})]
    for m, url, body in calls:
        r = getattr(c, m)(url, json=body)
        assert r.status_code in (403, 404), (url, r.status_code, r.text)
    r = c.post("/api/fs/upload", data={"folder": "/users/alice/inbox"}, files={"file": ("u.txt", b"x")})
    assert r.status_code == 403
    assert c.get("/api/fs/download", params={"path": "/users/alice/projects/p/f.txt"}).text == "2"


def test_D7_folder_list_limits_every_path(app, make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/projects/p/f.txt")
    save(alice, "/users/alice/projects/p2/g.txt")
    save(alice, "/users/alice/inbox/h.txt")
    tok = alice.post("/api/tokens", json={"name": "w", "scope": "write", "folders": ["/users/alice/projects/p"]}).json()["token"]
    c = bearer(app, tok)
    assert c.get("/api/fs/download", params={"path": "/users/alice/projects/p/f.txt"}).status_code == 200
    assert c.get("/api/fs/download", params={"path": "/users/alice/projects/p2/g.txt"}).status_code in (403, 404)
    assert c.post("/api/fs/move", json={"src": "/users/alice/projects/p/f.txt", "dst": "/users/alice/inbox/f.txt"}).status_code in (403, 404)
    assert c.post("/api/fs/move", json={"src": "/users/alice/inbox/h.txt", "dst": "/users/alice/projects/p/h.txt"}).status_code in (403, 404)
    assert c.get("/api/fs/access", params={"path": "/users/alice/inbox"}).status_code in (403, 404)
    lst = c.get("/api/fs/list", params={"path": "/users/alice/projects/p"}).json()
    assert [f["name"] for f in lst["files"]] == ["f.txt"]
    assert c.get("/api/fs/list", params={"path": "/users/alice/projects/p2"}).status_code == 404
    # trash restore outside the folder list
    tid = alice.post("/api/fs/delete", json={"path": "/users/alice/inbox/h.txt"}).json()["trash_id"]
    assert c.post("/api/trash/restore", json={"id": tid}).status_code in (403, 404)


# Admin powers through a personal token ------------------------------------------------------
@pytest.fixture()
def admin_pat(app, admin, make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/diary.txt", "alice-private")
    tok = admin.post("/api/tokens", json={"name": "build", "scope": "write"}).json()["token"]
    return bearer(app, tok)


def test_D7_admin_pat_cannot_read_private_homes(admin_pat):
    r = admin_pat.get("/api/fs/download", params={"path": "/users/alice/inbox/diary.txt"})
    assert r.status_code in (403, 404) and "alice-private" not in r.text, "admin's personal token reads anyone's home"


def test_D7_admin_pat_cannot_read_private_homes_over_mcp(app, admin, make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/diary.txt", "alice-private")
    tok = admin.post("/api/tokens", json={"name": "build"}).json()["token"]
    res = MCP(app, tok).call("get_artifact", path="/users/alice/inbox/diary.txt")
    assert res["isError"], "admin's personal token reads anyone's home over MCP"


def test_D7_admin_pat_cannot_share_anywhere(admin_pat):
    r = admin_pat.post("/api/fs/share", json={"path": "/users/alice", "kind": "user", "name": "admin", "role": "owner"})
    assert r.status_code in (403, 404)


@pytest.mark.parametrize("method,url,body", [
    ("get", "/api/users", None), ("get", "/api/audit", None), ("get", "/api/access-check?path=/users/alice", None),
    ("get", "/api/directory", None), ("get", "/api/smb", None), ("get", "/api/storage", None), ("get", "/api/overview", None),
    ("post", "/api/routing", {"match": "project", "pattern": "*", "target": "/users/alice/inbox"}),
])
def test_D7_K3_admin_pat_has_no_admin_powers(admin_pat, method, url, body):
    r = getattr(admin_pat, method)(url, **({"json": body} if body else {}))
    assert r.status_code in (403, 404), (url, r.status_code)


def test_D7_K3_admin_pat_cannot_revoke_other_peoples_tokens(app, admin, make_user):
    bob = make_user("bob")
    oauth_sign_in(app, bob)
    tid = next(d["id"] for d in bob.get("/api/tokens").json()["tokens"])
    pat = admin.post("/api/tokens", json={"name": "build"}).json()["token"]
    assert bearer(app, pat).delete(f"/api/tokens/{tid}").status_code in (403, 404)
