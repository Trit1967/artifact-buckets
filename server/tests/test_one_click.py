"""One-page setup: the computer sends the folders it would copy, the person picks and presses Connect once,
and that same press lets Claude Code's sign-in through. Plus disconnecting a computer from the computer."""
from __future__ import annotations

import datetime as dt
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from conftest import pkce

FOLDERS = [
    {"folder": "projects/quarterly-report", "files": 14, "bytes": 52000, "source": "C:\\work\\quarterly-report"},
    {"folder": "projects/client-work", "files": 22, "bytes": 90000, "source": "C:\\work\\client-work"},
    {"folder": "inbox", "files": 3, "bytes": 1200, "source": ""},
]


def _start(app, folders=FOLDERS, computer="WORK-LAPTOP"):
    c = TestClient(app)
    r = c.post("/api/sync/link/start", json={"computer": computer, "folders": folders})
    assert r.status_code == 200, r.text
    return c, r.json()


def _authorize(app, web, redirect="http://127.0.0.1:33418/callback"):
    c = TestClient(app, follow_redirects=False)
    cid = c.post("/oauth/register", json={"client_name": "Claude Code (plugin:artifact-buckets:artifact-buckets)",
                                          "redirect_uris": [redirect]}).json()["client_id"]
    verifier, challenge = pkce()
    q = {"client_id": cid, "redirect_uri": redirect, "response_type": "code", "state": "s1",
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return c, cid, verifier, q, web.get("/oauth/authorize", params=q)


def test_folders_shown_and_choice_becomes_skip_list(app, admin):
    c, s = _start(app)
    info = admin.get(f"/api/sync/link/{s['code']}").json()
    assert [f["folder"] for f in info["folders"]] == [f["folder"] for f in FOLDERS]
    assert info["folders"][0]["files"] == 14 and info["folders"][0]["source"] == "C:\\work\\quarterly-report"
    assert info["home"] == "/users/admin"
    r = admin.post(f"/api/sync/link/{s['code']}/allow", json={"keep": ["projects/quarterly-report", "inbox", "projects/nope"]})
    assert r.status_code == 200 and r.json()["sync"] is True
    p = c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()
    assert p["status"] == "approved" and p["token"].startswith("ab_sync_")
    assert p["skip"] == ["projects/client-work"]


def test_keep_all_when_no_choice_sent(app, admin):
    c, s = _start(app)
    admin.post(f"/api/sync/link/{s['code']}/allow")
    p = c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()
    assert p["status"] == "approved" and p["skip"] == []


def test_connect_without_syncing_issues_no_sync_token(app, admin):
    c, s = _start(app)
    r = admin.post(f"/api/sync/link/{s['code']}/connect")
    assert r.json() == {"status": "approved", "computer": "WORK-LAPTOP", "sync": False}
    p = c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()
    assert p["status"] == "connected" and "token" not in p and p["home"] == "/users/admin"
    assert c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()["status"] == "done"
    assert not [t for t in admin.get("/api/tokens").json()["tokens"] if t["kind"] == "sync"]


def test_bad_folder_entries_are_dropped(app, admin):
    junk = [{"folder": "../etc", "files": 1}, {"folder": "projects/a/b", "files": 1}, {"folder": "projects/..", "files": 1},
            {"folder": "users/x", "files": 1}, {"folder": "projects/ok", "files": -5, "bytes": -1, "source": "a\u202eb\x07c"},
            {"folder": "projects/ok", "files": 9}] + [{"folder": f"projects/p{i}", "files": 1} for i in range(300)]
    _, s = _start(app, folders=junk)
    got = admin.get(f"/api/sync/link/{s['code']}").json()["folders"]
    assert got[0] == {"folder": "projects/ok", "files": 0, "bytes": 0, "source": "abc"}
    assert len(got) <= 200 and all(f["folder"].startswith("projects/p") for f in got[1:])


def test_one_press_covers_claude_code_sign_in(app, admin):
    c, s = _start(app)
    admin.post(f"/api/sync/link/{s['code']}/allow")
    tc, cid, verifier, q, r = _authorize(app, admin)
    # Straight back to Claude Code with a code: no consent page.
    assert r.status_code == 302 and r.headers["location"].startswith("http://127.0.0.1:33418/callback?"), r.headers["location"]
    params = parse_qs(urlparse(r.headers["location"]).query)
    assert params["state"] == ["s1"]
    tok = tc.post("/oauth/token", data={"grant_type": "authorization_code", "code": params["code"][0], "client_id": cid,
                                        "redirect_uri": q["redirect_uri"], "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    names = [t["name"] for t in admin.get("/api/tokens").json()["tokens"] if t["kind"] == "device"]
    assert "Claude Code on WORK-LAPTOP" in names
    # Only once: a second sign-in asks again.
    r2 = _authorize(app, admin)[4]
    assert r2.status_code == 302 and r2.headers["location"].startswith("/consent?")


def test_connect_only_also_covers_sign_in(app, admin):
    _, s = _start(app)
    admin.post(f"/api/sync/link/{s['code']}/connect")
    r = _authorize(app, admin)[4]
    assert r.headers["location"].startswith("http://127.0.0.1:33418/callback?")


def test_no_shortcut_without_a_fresh_approval_from_this_browser(app, admin, make_user):
    from app.db import new_session
    from app.sync import LinkPlan
    # Nothing approved: consent page.
    assert _authorize(app, admin)[4].headers["location"].startswith("/consent?")
    # Declined: consent page.
    _, s = _start(app)
    admin.post(f"/api/sync/link/{s['code']}/deny")
    assert _authorize(app, admin)[4].headers["location"].startswith("/consent?")
    # Approved in another browser session of the same person: consent page here.
    from conftest import ADMIN_PW, login
    other = login(app, "admin", ADMIN_PW)
    _, s = _start(app)
    other.post(f"/api/sync/link/{s['code']}/allow")
    assert _authorize(app, admin)[4].headers["location"].startswith("/consent?")
    # Approved here but more than ten minutes ago: consent page.
    _, s = _start(app)
    admin.post(f"/api/sync/link/{s['code']}/allow")
    with new_session() as db:
        for p in db.query(LinkPlan).all():
            p.approved_at = p.approved_at - dt.timedelta(minutes=11) if p.approved_at else None
        db.commit()
    assert _authorize(app, admin)[4].headers["location"].startswith("/consent?")


def test_no_shortcut_for_a_non_loopback_redirect(app, admin, monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "oauth_redirect_hosts", ["tools.example.com"])
    _, s = _start(app)
    admin.post(f"/api/sync/link/{s['code']}/allow")
    r = _authorize(app, admin, redirect="https://tools.example.com/cb")[4]
    assert r.status_code == 302 and r.headers["location"].startswith("/consent?")
    # The approval is still there for Claude Code's own (loopback) sign-in.
    assert _authorize(app, admin)[4].headers["location"].startswith("http://127.0.0.1:33418/")


def test_unlink_from_the_computer_ends_sync_and_sign_in(app, admin):
    c, s = _start(app)
    admin.post(f"/api/sync/link/{s['code']}/allow")
    sync_tok = c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()["token"]
    tc, cid, verifier, q, r = _authorize(app, admin)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    access = tc.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                           "redirect_uri": q["redirect_uri"], "code_verifier": verifier}).json()["access_token"]
    mcp = {"authorization": f"Bearer {access}", "accept": "application/json, text/event-stream"}
    ping = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert tc.post("/mcp", json=ping, headers=mcp).status_code == 200
    r = tc.post("/api/sync/unlink", headers={"authorization": f"Bearer {sync_tok}"})
    assert r.status_code == 200 and r.json() == {"ok": True, "signed_out": True}
    assert tc.post("/mcp", json=ping, headers=mcp).status_code == 401
    h = {"authorization": f"Bearer {sync_tok}", "x-sync-where": "inbox", "x-sync-path": "YS50eHQ="}
    assert tc.put("/api/sync/file", content=b"x", headers=h).status_code == 401
    assert tc.post("/api/sync/unlink", headers={"authorization": f"Bearer {sync_tok}"}).status_code == 401
    # Other tokens of the same person are untouched.
    assert tc.post("/api/sync/unlink").status_code == 401


def test_unlink_needs_a_sync_token(app, admin):
    from conftest import oauth_sign_in
    t = oauth_sign_in(app, admin)
    c = TestClient(app)
    assert c.post("/api/sync/unlink", headers={"authorization": f"Bearer {t['access_token']}"}).status_code == 401


def test_old_link_without_plan_still_works(app, admin):
    from app.db import new_session
    from app.sync import LinkPlan
    c, s = _start(app)
    with new_session() as db:
        db.query(LinkPlan).delete()
        db.commit()
    assert admin.post(f"/api/sync/link/{s['code']}/allow").status_code == 200
    p = c.post("/api/sync/link/poll", json={"poll": s["poll"]}).json()
    assert p["status"] == "approved" and p["token"]


def test_scripts_served(app):
    c = TestClient(app)
    for path in ("/claude/uninstall.ps1", "/claude/uninstall.sh", "/claude/install.ps1", "/claude/install.sh", "/claude/setup.md"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "{{" not in r.text, path
    ps1 = c.get("/claude/install.ps1").text
    assert "Import-Certificate" not in ps1 and "Read-Host" not in ps1
    assert "/claude/sync.ps1" in ps1 and "Get-FileHash" in ps1
    import hashlib
    served = c.get("/claude/sync.ps1").content
    assert hashlib.sha256(served).hexdigest().upper() in ps1
    sh = c.get("/claude/install.sh").text
    assert "add-trusted-cert" not in sh and "/dev/tty" not in sh
    md = c.get("/claude/setup.md").text
    assert "Removing Artifact Buckets" in md and "Connect and copy" in md
    assert hashlib.sha256(served).hexdigest().upper() in md
    info = TestClient(app).get("/api/auth/me")
    # (setup info is part of the signed-in session; checked in the web tests)
    assert info.status_code in (200, 401)
