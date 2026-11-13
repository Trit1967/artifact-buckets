"""Shared fixtures: every test gets a fresh database, folder tree and volumes directory."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADMIN_PW = "correct-horse-battery-staple"
PW = "a-long-enough-password"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    pg = os.environ.get("AB_TEST_DATABASE_URL")  # e.g. postgresql+psycopg://postgres@/abtest?host=/tmp/pg&port=5433
    monkeypatch.setenv("AB_DATABASE_URL", pg or f"sqlite:///{tmp_path}/ab.db")
    if pg:
        from sqlalchemy import create_engine
        from app.db import Base
        eng = create_engine(pg)
        Base.metadata.drop_all(eng)
        eng.dispose()
    monkeypatch.setenv("AB_TREE_ROOT", str(tmp_path / "tree"))
    monkeypatch.setenv("AB_VOLUMES_ROOT", str(tmp_path / "volumes"))
    monkeypatch.setenv("AB_BOOTSTRAP_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("AB_BACKGROUND_JOBS", "false")
    monkeypatch.setenv("AB_SECRET_KEY", "not-a-secret-only-for-tests")
    monkeypatch.setenv("AB_BASE_URL", "http://testserver")
    monkeypatch.setenv("AB_STATIC_DIR", str(tmp_path / "web"))
    from app import config
    config.reload_settings()
    yield tmp_path
    config.reload_settings()


class Web:
    """A signed-in browser: keeps the cookie and sends the CSRF header."""

    def __init__(self, client: TestClient, csrf: str):
        self.c, self.csrf = client, csrf

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def _h(self, kw):
        kw.setdefault("headers", {})["X-CSRF-Token"] = self.csrf
        return kw

    def post(self, url, **kw):
        return self.c.post(url, **self._h(kw))

    def put(self, url, **kw):
        return self.c.put(url, **self._h(kw))

    def patch(self, url, **kw):
        return self.c.patch(url, **self._h(kw))

    def delete(self, url, **kw):
        return self.c.delete(url, **self._h(kw))


@pytest.fixture()
def app(env):
    from app.main import create_app
    a = create_app()
    with TestClient(a):  # runs startup (bootstrap admin)
        pass
    return a


def login(app, username, password) -> Web:
    c = TestClient(app, follow_redirects=False)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return Web(c, r.json()["csrf"])


@pytest.fixture()
def admin(app) -> Web:
    return login(app, "admin", ADMIN_PW)


@pytest.fixture()
def make_user(app, admin):
    def _make(username, role="member", groups=()):
        r = admin.post("/api/users", json={"username": username, "password": PW, "system_role": role,
                                           "display_name": username.title(), "groups": list(groups)})
        assert r.status_code in (200, 201), r.text
        return login(app, username, PW)
    return _make


@pytest.fixture()
def make_group(admin):
    def _make(name, members=()):
        r = admin.post("/api/groups", json={"name": name, "description": f"{name} team"})
        assert r.status_code in (200, 201), r.text
        gid = r.json()["id"]
        for m in members:
            rr = admin.post(f"/api/groups/{gid}/members", json={"username": m})
            assert rr.status_code == 200, rr.text
        return gid
    return _make


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def oauth_sign_in(app, web: Web, redirect="http://127.0.0.1:33418/callback"):
    """Run the whole Claude Code sign-in: register, authorize, consent, token. Returns the token response."""
    c = TestClient(app, follow_redirects=False)
    reg = c.post("/oauth/register", json={"client_name": "Claude Code", "redirect_uris": [redirect]})
    assert reg.status_code == 201, reg.text
    cid = reg.json()["client_id"]
    verifier, challenge = pkce()
    q = {"client_id": cid, "redirect_uri": redirect, "response_type": "code", "state": "xyz",
         "code_challenge": challenge, "code_challenge_method": "S256"}
    r = web.get("/oauth/authorize", params=q)
    assert r.status_code == 302 and r.headers["location"].startswith("/consent?"), r.text
    r = web.c.post("/oauth/approve", data={**q, "decision": "allow", "csrf": web.csrf, "device_name": "laptop"})
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    params = parse_qs(loc.query)
    assert params["state"] == ["xyz"]
    tok = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": params["code"][0], "client_id": cid,
                                       "redirect_uri": redirect, "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    return {**tok.json(), "client_id": cid}


class MCP:
    def __init__(self, app, token):
        self.c = TestClient(app)
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
        self.n = 0

    def rpc(self, method, params=None):
        self.n += 1
        r = self.c.post("/mcp", json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}, headers=self.h)
        assert r.status_code == 200, r.text
        return r.json()

    def tools(self):
        return [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]

    def call(self, _tool, **args):
        res = self.rpc("tools/call", {"name": _tool, "arguments": args})["result"]
        return res

    def ok(self, _tool, **args):
        res = self.call(_tool, **args)
        assert not res["isError"], res["content"][0]["text"]
        return res["structuredContent"]
