"""Fixes from the independent re-test (N-1 to N-15): moved files, protected folders, names, delete preview,
quota notes and the development key."""
from conftest import MCP, oauth_sign_in


def save(web, path, content):
    return web.post("/api/fs/save-text", json={"path": path, "content": content})


def test_if_version_catches_moved_file(app, make_user):
    alice = make_user("alice")
    m = MCP(app, oauth_sign_in(app, alice)["access_token"])
    m.ok("save_artifact", path="/users/alice/inbox/a.md", content="one")
    m.ok("rename", path="/users/alice/inbox/a.md", new_name="b.md")
    res = m.call("save_artifact", path="/users/alice/inbox/a.md", content="two", if_version=1)
    assert res["isError"] and "no longer exists" in res["content"][0]["text"]
    assert m.call("get_artifact", path="/users/alice/inbox/a.md")["isError"]
    # Without if_version a new file is still allowed.
    m.ok("save_artifact", path="/users/alice/inbox/a.md", content="fresh")


def test_get_artifact_reports_role_and_truncation(app, make_user):
    alice = make_user("alice")
    m = MCP(app, oauth_sign_in(app, alice)["access_token"])
    m.ok("save_artifact", path="/users/alice/inbox/big.txt", content="x" * (2 * 1024 * 1024 + 10))
    got = m.ok("get_artifact", path="/users/alice/inbox/big.txt")
    assert got["truncated"] is True and "Don't save this text back" in got["note"] and got["role"] == "owner"


def test_inbox_and_projects_folders_stay_put(make_user):
    alice = make_user("alice")
    assert save(alice, "/users/alice/inbox/x.txt", "x").status_code == 200
    for p in ("/users/alice/inbox", "/users/alice/projects"):
        assert alice.post("/api/fs/move", json={"src": p, "dst": "/users/alice/elsewhere"}).status_code == 403
        assert alice.post("/api/fs/move", json={"src": p, "dst": "/users/alice/projects/moved"}).status_code in (400, 403)
        assert alice.post("/api/fs/delete", json={"path": p, "confirm": True}).status_code == 403
    # Folders inside them are still the person's to manage.
    assert save(alice, "/users/alice/projects/p1/y.txt", "y").status_code == 200
    assert alice.post("/api/fs/move", json={"src": "/users/alice/projects/p1", "dst": "/users/alice/projects/p2"}).status_code == 200


def test_invisible_and_reserved_names_refused(make_user):
    alice = make_user("alice")
    for name in ("a​b.txt", "﻿x.txt", "x⁠y.md", "CON.txt", "nul", "com1.log", "LPT9"):
        r = save(alice, f"/users/alice/inbox/{name}", "x")
        assert r.status_code == 400, name
    assert save(alice, "/users/alice/inbox/console.txt", "fine").status_code == 200


def test_delete_preview_counts_without_deleting(make_user):
    alice = make_user("alice")
    for i in range(3):
        save(alice, f"/users/alice/projects/p/f{i}.txt", "abc")
    r = alice.post("/api/fs/delete", json={"path": "/users/alice/projects/p", "preview": True})
    assert r.status_code == 200 and r.json()["files"] == 3 and r.json()["folder"] is True
    assert alice.get("/api/fs/list", params={"path": "/users/alice/projects/p"}).status_code == 200


def test_quota_drops_are_reported_and_audited(make_user, admin):
    alice = make_user("alice")
    uid = alice.get("/api/auth/me").json()["user"]["id"]
    assert admin.patch(f"/api/users/{uid}", json={"quota_bytes": 2500}).status_code == 200
    for i in range(3):
        r = save(alice, "/users/alice/inbox/q.txt", str(i) * 1000)
        assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 3 and body["dropped_versions"] and "quota" in body["note"]
    from app import db as dbm, mcp
    with dbm.new_session() as s:
        ev = mcp.audit_query(s, action="file.save", user="alice")
    assert any(e["detail"].get("dropped_versions") for e in ev)


def test_unset_secret_key_gets_a_random_dev_key(monkeypatch):
    from app import config
    monkeypatch.delenv("AB_SECRET_KEY", raising=False)
    monkeypatch.setenv("AB_SECURE_COOKIES", "false")
    s = config.Settings()
    s.check()
    assert s.secret_key and len(s.secret_key) >= 32 and s.secret_key != config.Settings().secret_key
    monkeypatch.setenv("AB_SECURE_COOKIES", "true")
    s2 = config.Settings()
    try:
        s2.check()
    except SystemExit:
        pass
    else:
        raise AssertionError("secure cookies with no key must refuse to start")
