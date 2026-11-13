"""Files, folders, permissions, versions, trash, quotas and sharing through the REST API."""
from conftest import PW, login


def save(web, path, content="hello"):
    return web.post("/api/fs/save-text", json={"path": path, "content": content})


def test_bootstrap_admin_and_home(admin):
    me = admin.get("/api/auth/me").json()["user"]
    assert me["system_role"] == "admin" and me["home"] == "/users/admin"
    listing = admin.get("/api/fs/list", params={"path": "/users/admin"}).json()
    assert {f["name"] for f in listing["folders"]} == {"inbox", "projects"}


def test_wrong_password_and_audit(app, admin):
    from fastapi.testclient import TestClient
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "admin", "password": "nope-nope-nope"}).status_code == 401
    ev = admin.get("/api/audit", params={"action": "signin.password"}).json()
    events = ev.get("events", ev)
    assert any(not e["ok"] for e in events)


def test_csrf_required_for_cookie_writes(admin):
    r = admin.c.post("/api/fs/mkdir", json={"path": "/users/admin/x"})
    assert r.status_code == 403
    assert admin.post("/api/fs/mkdir", json={"path": "/users/admin/x"}).status_code == 200


def test_unauthenticated_is_401(app):
    from fastapi.testclient import TestClient
    assert TestClient(app).get("/api/fs/list").status_code == 401


def test_home_is_private(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    assert save(alice, "/users/alice/notes.md").status_code == 200
    # Bob cannot see Alice's home: not found, not forbidden, so names don't leak.
    assert bob.get("/api/fs/list", params={"path": "/users/alice"}).status_code == 404
    assert bob.get("/api/fs/download", params={"path": "/users/alice/notes.md"}).status_code == 404
    assert save(bob, "/users/alice/evil.md").status_code in (403, 404)
    # Bob's listing of /users shows only his own folder.
    names = {f["name"] for f in bob.get("/api/fs/list", params={"path": "/users"}).json()["folders"]}
    assert names == {"bob"}


def test_group_folder_and_roles(make_user, make_group, admin):
    alice, bob, carol = make_user("alice"), make_user("bob"), make_user("carol")
    make_group("platform", ["alice", "bob"])
    assert save(alice, "/shared/platform/projects/plan.md", "v1").status_code == 200
    assert bob.get("/api/fs/download", params={"path": "/shared/platform/projects/plan.md"}).text == "v1"
    assert carol.get("/api/fs/list", params={"path": "/shared/platform"}).status_code == 404
    # Members are editors, not owners: they can't share the team folder.
    r = alice.post("/api/fs/share", json={"path": "/shared/platform", "kind": "user", "name": "carol", "role": "viewer"})
    assert r.status_code == 403
    # Admin shares a sub-folder read-only with Carol; she can read but not write, and sees the path to it.
    r = admin.post("/api/fs/share", json={"path": "/shared/platform/projects", "kind": "user", "name": "carol", "role": "viewer"})
    assert r.status_code == 200, r.text
    assert carol.get("/api/fs/download", params={"path": "/shared/platform/projects/plan.md"}).text == "v1"
    assert save(carol, "/shared/platform/projects/x.md").status_code == 403
    names = {f["name"] for f in carol.get("/api/fs/list", params={"path": "/shared"}).json()["folders"]}
    assert names == {"platform"}


def test_owner_can_share_own_folder(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/demo/index.html", "<h1>hi</h1>")
    r = alice.post("/api/fs/share", json={"path": "/users/alice/projects/demo", "kind": "user", "name": "bob", "role": "editor"})
    assert r.status_code == 200, r.text
    assert save(bob, "/users/alice/projects/demo/more.html").status_code == 200
    acc = alice.get("/api/fs/access", params={"path": "/users/alice/projects/demo"}).json()["access"]
    assert any(a["principal"]["name"] == "bob" and a["role"] == "editor" and not a["inherited"] for a in acc)
    # The owner grant on a person's own home can't be removed, even by them.
    home = next(a for a in acc if a["via"] == "/users/alice" and a["principal"]["name"] == "alice")
    assert home["inherited"] and home["role"] == "owner"
    assert alice.delete(f"/api/grants/{home['grant_id']}").status_code in (400, 403)
    # Bob (editor) can't remove grants or re-share.
    bob_grant = next(a for a in acc if a["principal"]["name"] == "bob")
    assert bob.delete(f"/api/grants/{bob_grant['grant_id']}").status_code in (403, 404)
    assert alice.delete(f"/api/grants/{bob_grant['grant_id']}").status_code == 200
    assert save(bob, "/users/alice/projects/demo/again.html").status_code == 404


def test_path_safety(make_user):
    alice = make_user("alice")
    for bad in ["/users/alice/../bob/x", "/users/alice/.versions/x", "/etc/passwd", "/users/alice/a:b",
                "/users/alice/trail.", "/users/alice/con\x00"]:
        r = save(alice, bad)
        assert r.status_code in (400, 403, 404), (bad, r.status_code, r.text)


def test_versions_and_restore(make_user):
    alice = make_user("alice")
    p = "/users/alice/inbox/a.txt"
    for v in ("one", "two", "three"):
        assert save(alice, p, v).status_code == 200
    vs = alice.get("/api/fs/versions", params={"path": p}).json()
    vs = vs.get("versions", vs)
    assert len(vs) == 3
    r = alice.post("/api/fs/restore-version", json={"path": p, "version": 1})
    assert r.status_code == 200, r.text
    assert alice.get("/api/fs/download", params={"path": p}).text == "one"
    vs = alice.get("/api/fs/versions", params={"path": p}).json()
    assert len(vs.get("versions", vs)) == 4


def test_trash_and_restore(make_user, make_group, admin):
    alice = make_user("alice")
    save(alice, "/users/alice/projects/p1/a.txt", "a")
    save(alice, "/users/alice/projects/p1/b.txt", "b")
    bob = make_user("bob")
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p1", "kind": "user", "name": "bob", "role": "viewer"})
    assert alice.post("/api/fs/delete", json={"path": "/users/alice/projects/p1"}).status_code == 200
    assert alice.get("/api/fs/list", params={"path": "/users/alice/projects/p1"}).status_code == 404
    items = alice.get("/api/trash").json()
    items = items.get("items", items)
    assert len(items) == 1
    # Someone else can't restore it.
    assert bob.post("/api/trash/restore", json={"id": items[0]["id"]}).status_code in (403, 404)
    assert alice.post("/api/trash/restore", json={"id": items[0]["id"]}).status_code == 200
    assert alice.get("/api/fs/download", params={"path": "/users/alice/projects/p1/b.txt"}).text == "b"
    # The grant came back with the folder.
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/p1/a.txt"}).text == "a"


def test_cannot_delete_protected(make_user, admin):
    alice = make_user("alice")
    assert alice.post("/api/fs/delete", json={"path": "/users/alice"}).status_code in (400, 403)
    assert admin.post("/api/fs/delete", json={"path": "/users"}).status_code in (400, 403)


def test_move_and_rename(make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/a.txt", "a")
    r = alice.post("/api/fs/move", json={"src": "/users/alice/inbox/a.txt", "dst": "/users/alice/projects/a.txt"})
    assert r.status_code == 200, r.text
    assert alice.get("/api/fs/download", params={"path": "/users/alice/projects/a.txt"}).text == "a"
    assert alice.get("/api/fs/download", params={"path": "/users/alice/inbox/a.txt"}).status_code == 404
    # Moving a folder into itself is refused.
    alice.post("/api/fs/mkdir", json={"path": "/users/alice/projects/f"})
    r = alice.post("/api/fs/move", json={"src": "/users/alice/projects/f", "dst": "/users/alice/projects/f/g"})
    assert r.status_code in (400, 409)


def test_quota(make_user, admin):
    alice = make_user("alice")
    uid = alice.get("/api/auth/me").json()["user"]["id"]
    assert admin.patch(f"/api/users/{uid}", json={"quota_bytes": 100}).status_code == 200
    assert save(alice, "/users/alice/inbox/small.txt", "x" * 60).status_code == 200
    r = save(alice, "/users/alice/inbox/big.txt", "x" * 60)
    assert r.status_code == 413 and r.json()["error"] == "quota_exceeded"
    # Overwriting with the same size still fits.
    assert save(alice, "/users/alice/inbox/small.txt", "y" * 60).status_code == 200


def test_upload_download_and_preview(make_user):
    alice = make_user("alice")
    r = alice.post("/api/fs/upload", data={"folder": "/users/alice/inbox", "tags": "demo"},
                   files={"file": ("page.html", b"<script>alert(1)</script>", "text/html")})
    assert r.status_code == 200, r.text
    d = alice.get("/api/fs/download", params={"path": "/users/alice/inbox/page.html"})
    assert d.status_code == 200 and "attachment" in d.headers.get("content-disposition", "")
    meta = alice.get("/api/fs/meta", params={"path": "/users/alice/inbox/page.html"}).json()
    url = meta["preview_url"]
    from fastapi.testclient import TestClient
    # Works in alice's own browser session (an iframe sends the cookie), and nowhere else.
    assert TestClient(alice.c.app).get(url).status_code == 403
    p = alice.c.get(url)
    assert p.status_code == 200
    assert "sandbox" in p.headers["content-security-policy"]
    assert TestClient(alice.c.app).get(url.replace("/preview/", "/preview/x")).status_code in (403, 404)


def test_search(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/inbox/latency-dashboard.html")
    save(bob, "/users/bob/inbox/latency-notes.md")
    res = alice.get("/api/fs/search", params={"q": "latency"}).json()
    res = res.get("results", res)
    assert [r["name"] for r in res] == ["latency-dashboard.html"]


def test_admin_user_management(app, admin, make_user):
    make_user("alice")
    users = admin.get("/api/users").json()["users"]
    aid = next(u["id"] for u in users if u["username"] == "alice")
    me = next(u["id"] for u in users if u["username"] == "admin")
    assert admin.patch(f"/api/users/{me}", json={"status": "suspended"}).status_code == 400
    assert admin.patch(f"/api/users/{me}", json={"system_role": "member"}).status_code == 400
    assert admin.patch(f"/api/users/{aid}", json={"status": "suspended"}).status_code == 200
    from fastapi.testclient import TestClient
    r = TestClient(app).post("/api/auth/login", json={"username": "alice", "password": PW})
    assert r.status_code in (401, 403)


def test_member_cannot_admin(make_user):
    alice = make_user("alice")
    assert alice.get("/api/users").status_code == 403
    assert alice.post("/api/users", json={"username": "eve", "password": PW}).status_code == 403
    assert alice.post("/api/groups", json={"name": "x"}).status_code == 403
    assert alice.get("/api/audit").status_code == 403
    assert alice.post("/api/storage/1/add").status_code in (403, 404)


def test_auditor_reads_audit_not_files(make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/secret.txt", "s")
    aud = make_user("audrey", role="auditor")
    assert aud.get("/api/audit").status_code == 200
    assert aud.get("/api/fs/download", params={"path": "/users/alice/inbox/secret.txt"}).status_code == 404
    assert aud.post("/api/users", json={"username": "eve", "password": PW}).status_code == 403


def test_personal_token_limits(make_user, app):
    alice = make_user("alice")
    save(alice, "/users/alice/projects/a/x.txt", "x")
    save(alice, "/users/alice/inbox/y.txt", "y")
    r = alice.post("/api/tokens", json={"name": "ci", "scope": "read", "folders": ["/users/alice/projects"]})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    from fastapi.testclient import TestClient
    c = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/fs/download", params={"path": "/users/alice/projects/a/x.txt"}, headers=h).text == "x"
    assert c.get("/api/fs/download", params={"path": "/users/alice/inbox/y.txt"}, headers=h).status_code == 404
    assert c.post("/api/fs/save-text", json={"path": "/users/alice/projects/a/z.txt", "content": "z"}, headers=h).status_code == 403
    # Tokens can't mint tokens.
    assert c.post("/api/tokens", json={"name": "x"}, headers=h).status_code == 403
    tid = r.json()["id"]
    assert alice.delete(f"/api/tokens/{tid}").status_code == 200
    assert c.get("/api/fs/list", headers=h).status_code == 401


def test_admin_personal_token_cannot_admin(admin, app):
    tok = admin.post("/api/tokens", json={"name": "ci"}).json()["token"]
    from fastapi.testclient import TestClient
    r = TestClient(app).post("/api/users", json={"username": "eve", "password": PW}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_security_headers(admin):
    r = admin.get("/api/auth/me")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_session_cookie_flags(app):
    from fastapi.testclient import TestClient
    from conftest import ADMIN_PW
    r = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc


def test_logout_ends_session(admin):
    assert admin.post("/api/auth/logout").status_code == 200
    assert admin.get("/api/auth/me").json()["user"] is None
    assert admin.get("/api/fs/list").status_code == 401
