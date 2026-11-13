"""Active Directory sync (against an in-memory LDAP server), SMB permissions (real POSIX ACLs), storage pool."""
import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server

from conftest import MCP, oauth_sign_in

DOM = "S-1-5-21-1111-2222-3333"
BASE = "DC=corp,DC=example,DC=com"


def fake_ad(users, groups):
    """users: {sam: (rid, disabled)}, groups: {cn: (rid, [sam...])}"""
    conn = Connection(Server("fake-dc", get_info=OFFLINE_AD_2012_R2), user="CN=svc,DC=corp,DC=example,DC=com", password="x",
                      client_strategy=MOCK_SYNC)
    conn.strategy.add_entry("CN=svc,DC=corp,DC=example,DC=com", {"userPassword": "x", "sAMAccountName": "svc"})
    for sam, (rid, disabled) in users.items():
        conn.strategy.add_entry(f"CN={sam},OU=People,{BASE}", {
            "objectClass": ["top", "person", "user"], "sAMAccountName": sam, "mail": f"{sam}@corp.example.com",
            "displayName": sam.title(), "objectSid": f"{DOM}-{rid}", "userAccountControl": 514 if disabled else 512})
    for cn, (rid, members) in groups.items():
        conn.strategy.add_entry(f"CN={cn},OU=Groups,{BASE}", {
            "objectClass": ["top", "group"], "cn": cn, "description": f"{cn} group", "objectSid": f"{DOM}-{rid}",
            "member": [f"CN={m},OU=People,{BASE}" for m in members]})
    conn.bind()
    return conn


@pytest.fixture()
def ad_env(env, monkeypatch):
    monkeypatch.setenv("AB_LDAP_URL", "ldaps://dc.corp.example.com")
    monkeypatch.setenv("AB_LDAP_BASE_DN", BASE)
    from app import config
    config.reload_settings()


def run_sync(conn):
    from app import db as dbm, directory
    with dbm.new_session() as s:
        return directory.sync(s, None, conn=conn)


def test_ad_sync_creates_people_groups_roles(ad_env, app, admin):
    conn = fake_ad({"dana": (1101, False), "eli": (1102, False), "fay": (1103, True), "gus": (1104, False)},
                   {"AB-Platform": (2001, ["dana", "eli", "fay"]), "AB-Admins": (2002, ["dana"]),
                    "AB-Auditors": (2003, ["gus"]), "Domain Users": (513, ["dana", "eli", "fay", "gus"])})
    stats = run_sync(conn)
    assert stats["groups"] == 3 and stats["created"] == 4
    users = {u["username"]: u for u in admin.get("/api/users").json()["users"]}
    assert users["dana"]["system_role"] == "admin" and users["gus"]["system_role"] == "auditor"
    assert users["eli"]["system_role"] == "member" and users["fay"]["status"] == "suspended"
    assert users["dana"]["uid_number"] == 100000 + 1101
    groups = {g["name"]: g for g in admin.get("/api/groups").json()["groups"]}
    assert "platform" in groups and groups["platform"]["source"] == "ad" and groups["platform"]["members"] == 3
    assert "domain-users" not in groups  # only AB- prefixed groups (or mapped ones) are synced
    # AD members can't be edited in the app.
    gid = groups["platform"]["id"]
    assert admin.post(f"/api/groups/{gid}/members", json={"username": "gus"}).status_code == 400


def test_ad_sync_follows_changes(ad_env, app, admin):
    conn = fake_ad({"dana": (1101, False), "eli": (1102, False)}, {"AB-Platform": (2001, ["dana", "eli"])})
    run_sync(conn)
    # Eli removed from every synced group -> suspended. Dana disabled in AD -> suspended.
    conn2 = fake_ad({"dana": (1101, True), "eli": (1102, False)}, {"AB-Platform": (2001, ["dana"])})
    stats = run_sync(conn2)
    users = {u["username"]: u for u in admin.get("/api/users").json()["users"]}
    assert users["eli"]["status"] == "suspended" and users["dana"]["status"] == "suspended"
    assert stats["suspended"] == 2
    # Both come back -> reactivated (they were suspended by sync, not by an admin).
    conn3 = fake_ad({"dana": (1101, False), "eli": (1102, False)}, {"AB-Platform": (2001, ["dana", "eli"])})
    run_sync(conn3)
    users = {u["username"]: u for u in admin.get("/api/users").json()["users"]}
    assert users["dana"]["status"] == "active"


def test_ad_suspended_by_admin_stays_suspended(ad_env, app, admin):
    conn = fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"])})
    run_sync(conn)
    uid = next(u["id"] for u in admin.get("/api/users").json()["users"] if u["username"] == "dana")
    admin.patch(f"/api/users/{uid}", json={"status": "suspended"})
    run_sync(conn)
    assert next(u for u in admin.get("/api/users").json()["users"] if u["username"] == "dana")["status"] == "suspended"


def test_ad_mapped_extra_group(ad_env, app, admin):
    conn = fake_ad({"dana": (1101, False)}, {"Finance-Analysts": (2100, ["dana"])})
    am = MCP(app, oauth_sign_in(app, admin)["access_token"])
    am.ok("map_ad_group", ad_group="Finance-Analysts")
    run_sync(conn)
    groups = {g["name"] for g in admin.get("/api/groups").json()["groups"]}
    assert "finance-analysts" in groups


acl_tools = pytest.mark.skipif(shutil.which("setfacl") is None, reason="needs the acl package")


def getfacl(path):
    return subprocess.run(["getfacl", "--omit-header", "--numeric", path], capture_output=True, text=True).stdout


@acl_tools
def test_smb_acls_follow_grants(ad_env, app, admin, monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "smb_enabled", True)
    conn = fake_ad({"dana": (1101, False), "eli": (1102, False)},
                   {"AB-Platform": (2001, ["dana"]), "AB-Design": (2002, ["eli"]), "AB-Admins": (2003, [])})
    run_sync(conn)
    tree = config.settings.tree_root
    plat = os.path.join(tree, "shared", "platform")
    acl = getfacl(plat)
    assert "group:102001:rwx" in acl and "group:102003:rwx" in acl  # team = editor, AB-Admins = owner
    assert "default:group:102001:rwx" in acl and "other::---" in acl
    home = getfacl(os.path.join(tree, "users", "dana"))
    assert "user:101101:rwx" in home
    # Share a sub-folder read-only with the design team -> r-x on folders, r-- on files.
    admin.post("/api/fs/save-text", json={"path": "/shared/platform/projects/spec.md", "content": "x"})
    r = admin.post("/api/fs/share", json={"path": "/shared/platform/projects", "kind": "group", "name": "design", "role": "viewer"})
    assert r.status_code == 200, r.text
    assert "group:102002:r-x" in getfacl(os.path.join(plat, "projects"))
    fa = getfacl(os.path.join(plat, "projects", "spec.md"))
    assert "group:102002:r--" in fa and "group:102001:rw-" in fa
    # A file saved later gets the folder's permissions too.
    admin.post("/api/fs/save-text", json={"path": "/shared/platform/projects/later.md", "content": "y"})
    assert "group:102002:r--" in getfacl(os.path.join(plat, "projects", "later.md"))
    # Remove the share -> gone from disk.
    access = admin.get("/api/fs/access", params={"path": "/shared/platform/projects"}).json()["access"]
    gid = next(a["grant_id"] for a in access if a["principal"]["name"] == "design")
    assert admin.delete(f"/api/grants/{gid}").status_code == 200
    assert "102002" not in getfacl(os.path.join(plat, "projects"))
    # No drift after all that; a manual change on the server is reported and then put back.
    smb = admin.get("/api/smb").json()
    assert smb["drift"] == []
    subprocess.run(["setfacl", "-m", "g:999999:rwx", plat], check=True)
    assert any(d["path"] == "/shared/platform" for d in admin.get("/api/smb").json()["drift"])
    admin.post("/api/smb/reconcile")
    assert "999999" not in getfacl(plat)


def test_storage_pool_direct_mode(env, app, admin):
    from app import config
    vroot = config.settings.volumes_root
    os.makedirs(os.path.join(vroot, "disk1"), exist_ok=True)
    os.makedirs(os.path.join(vroot, "disk2"), exist_ok=True)
    st = admin.get("/api/storage").json()
    vols = {v["name"]: v for v in st["volumes"]}
    assert vols["disk1"]["status"] == "pending"
    assert admin.post(f"/api/storage/{vols['disk1']['id']}/add").status_code == 200
    # Can't drain the only active volume.
    assert admin.post(f"/api/storage/{vols['disk1']['id']}/drain").status_code in (400, 409)
    assert admin.post(f"/api/storage/{vols['disk2']['id']}/add").status_code == 200
    # Put a file on disk1 and drain it to disk2.
    os.makedirs(os.path.join(vroot, "disk1", "users", "admin"), exist_ok=True)
    with open(os.path.join(vroot, "disk1", "users", "admin", "f.bin"), "wb") as fh:
        fh.write(b"x" * 1000)
    assert admin.post(f"/api/storage/{vols['disk1']['id']}/drain").status_code == 200
    from app import db as dbm, storage
    with dbm.new_session() as s:
        moved = storage.drain_step(s, vols["disk1"]["id"])
    assert moved == 1
    assert os.path.isfile(os.path.join(vroot, "disk2", "users", "admin", "f.bin"))
    vols = {v["name"]: v for v in admin.get("/api/storage").json()["volumes"]}
    assert vols["disk1"]["status"] == "drained"
    assert admin.put("/api/storage/policy", json={"policy": "ff"}).status_code == 200
    assert admin.put("/api/storage/policy", json={"policy": "bogus"}).status_code == 400
    # Survives restarts: marker file means "already added".
    assert os.path.exists(os.path.join(vroot, "disk2", storage.MARKER))


def test_background_jobs_index_smb_drops(env, app, admin):
    from app import config, main
    p = os.path.join(config.settings.tree_root, "users", "admin", "inbox", "dropped.txt")
    with open(p, "w") as fh:
        fh.write("from explorer")
    out = main.run_jobs_once()
    assert out["index"] == 1, out
    res = admin.get("/api/fs/search", params={"q": "dropped"}).json()
    res = res.get("results", res)
    assert res and res[0]["source"] == "smb"


def test_overview_and_audit(env, app, admin, make_user):
    alice = make_user("alice")
    alice.post("/api/fs/save-text", json={"path": "/users/alice/inbox/a.txt", "content": "a"})
    ov = admin.get("/api/overview")
    assert ov.status_code == 200, ov.text
    au = admin.get("/api/audit", params={"user": "alice"}).json()
    events = au.get("events", au)
    assert any(e["action"] == "file.save" for e in events)


def test_spa_fallback(env, app):
    from app import config
    web = config.settings.static_dir
    os.makedirs(os.path.join(web, "assets"), exist_ok=True)
    open(os.path.join(web, "index.html"), "w").write("<!doctype html><title>AB</title>")
    open(os.path.join(web, "assets", "app.js"), "w").write("1")
    c = TestClient(app)
    assert "<title>AB</title>" in c.get("/files").text
    assert c.get("/assets/app.js").text == "1"
    assert c.get("/api/nope").status_code == 404
    assert c.get("/../../etc/passwd").status_code in (200, 404)
    assert "root:" not in c.get("/..%2f..%2fetc/passwd").text
