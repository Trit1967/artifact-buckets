"""Auto-sync: link a computer, upload what Claude made, and what a sync token can't do."""
from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from conftest import ADMIN_PW


def _b(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _app(env):
    from app.main import create_app
    c = TestClient(create_app())
    c.__enter__()
    return c


def _login(c, user="admin", pw=ADMIN_PW):
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return c.get("/api/auth/me").json()["csrf"]


def _link(c, csrf, computer="ALDAVIS"):
    s = c.post("/api/sync/link/start", json={"computer": computer}).json()
    assert len(s["code"]) == 9 and s["link"].endswith(s["code"])
    assert c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()["status"] == "pending"
    info = c.get(f"/api/sync/link/{s['code'].lower()}").json()
    assert info["computer"] == computer
    r = c.post(f"/api/sync/link/{s['code']}/allow", headers={"x-csrf-token": csrf})
    assert r.json()["status"] == "approved"
    p = c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()
    assert p["status"] == "approved" and p["token"].startswith("ab_sync_") and p["home"] == "/users/admin"
    # The token is handed out once.
    assert c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()["status"] == "done"
    return p["token"]


def _put(c, tok, rel, data=b"hello", where="projects", project="report-site"):
    h = {"authorization": f"Bearer {tok}", "x-sync-where": where, "x-sync-path": _b(rel)}
    if where == "projects":
        h["x-sync-project"] = _b(project)
    return c.put("/api/sync/file", content=data, headers=h)


def test_link_upload_and_status(env):
    c = _app(env)
    csrf = _login(c)
    tok = _link(c, csrf)
    r = _put(c, tok, "docs/q3 summary.md", b"# Q3\n")
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "/users/admin/projects/report-site/docs/q3 summary.md" and r.json()["version"] == 1
    # Same bytes again: no new version.
    assert _put(c, tok, "docs/q3 summary.md", b"# Q3\n").json()["unchanged"] is True
    assert _put(c, tok, "docs/q3 summary.md", b"# Q3 v2\n").json()["version"] == 2
    assert _put(c, tok, "notes.txt", b"loose", where="inbox").json()["path"] == "/users/admin/inbox/notes.txt"
    st = c.get("/api/sync/status").json()
    assert st["files"] == 2 and st["computers"][0]["name"] == "ALDAVIS" and st["computers"][0]["last_sync"]
    assert {g["folder"] for g in st["folders"]} == {"/users/admin/projects/report-site", "/users/admin/inbox"}
    f = c.get("/api/fs/meta", params={"path": "/users/admin/projects/report-site/docs/q3 summary.md"})
    if f.status_code == 200:
        assert f.json().get("source") == "sync"
    # It shows on the Connect page and can be turned off.
    toks = c.get("/api/tokens").json()["tokens"]
    comp = [t for t in toks if t["kind"] == "sync"]
    assert comp and comp[0]["name"] == "Sync on ALDAVIS"
    assert c.delete(f"/api/tokens/{comp[0]['id']}", headers={"x-csrf-token": csrf}).status_code == 200
    assert _put(c, tok, "after.md").status_code == 401


def test_sync_token_is_narrow(env):
    c = _app(env)
    csrf = _login(c)
    tok = _link(c, csrf)
    bearer = {"authorization": f"Bearer {tok}"}
    # Not a general API token: no reading, listing or admin pages.
    assert c.get("/api/fs/list", params={"path": "/users/admin"}, headers=bearer).status_code == 401
    assert c.get("/api/users", headers=bearer).status_code == 401
    assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=bearer).status_code == 401
    # Only into projects/ and inbox/ of its own home.
    for rel, where, project in [("../../other/x.md", "projects", "p"), ("x.md", "projects", "../shared"),
                                ("x.md", "projects", "a/b"), (".hidden/x.md", "projects", "p"),
                                ("x.md", "trash", "p"), ("x.md", "projects", "")]:
        r = _put(c, tok, rel, where=where, project=project)
        assert r.status_code in (400, 403), (rel, where, project, r.status_code)
    assert not any(t["kind"] == "personal" for t in c.get("/api/tokens").json()["tokens"])


def test_link_codes(env):
    c = _app(env)
    csrf = _login(c)
    s = c.post("/api/sync/link/start", json={"computer": "PC‮gnp.exe"}).json()
    assert c.get(f"/api/sync/link/{s['code']}").json()["computer"] == "PCgnp.exe"
    # Approving needs the web session's CSRF token.
    assert c.post(f"/api/sync/link/{s['code']}/allow").status_code == 403
    assert c.post(f"/api/sync/link/{s['code']}/deny", headers={"x-csrf-token": csrf}).json()["status"] == "denied"
    assert c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()["status"] == "denied"
    assert c.post(f"/api/sync/link/{s['code']}/allow", headers={"x-csrf-token": csrf}).status_code == 400
    assert c.get("/api/sync/link/ZZZZ-ZZZZ").status_code == 404
    assert c.post("/api/sync/link/poll", json={"poll": "nope"}).json()["status"] == "expired"
    # Someone not signed in can't see or approve a code.
    anon = TestClient(c.app)
    s2 = c.post("/api/sync/link/start", json={"computer": "X"}).json()
    assert anon.get(f"/api/sync/link/{s2['code']}").status_code == 401


def test_welcome_once(env):
    c = _app(env)
    csrf = _login(c)
    assert c.get("/api/auth/me").json()["welcome"] is True
    assert c.post("/api/sync/welcome/done").status_code == 403  # needs CSRF
    assert c.post("/api/sync/welcome/done", headers={"x-csrf-token": csrf}).json()["ok"] is True
    assert c.post("/api/sync/welcome/done", headers={"x-csrf-token": csrf}).json()["ok"] is True
    assert c.get("/api/auth/me").json()["welcome"] is False
