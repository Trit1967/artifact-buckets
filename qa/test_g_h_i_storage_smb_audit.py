"""G. Storage pool, H. SMB permissions on disk (real POSIX ACLs), I. Admin and audit."""
import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from conftest import MCP, PW, audit_events, bearer, fake_ad, oauth_sign_in, run_sync, save, uid_of


# ---- G -----------------------------------------------------------------------------------
def vroot():
    from app import config
    return config.settings.volumes_root


def vols(admin):
    return {v["name"]: v for v in admin.get("/api/storage").json()["volumes"]}


def test_G1_states_marker_missing(admin):
    os.makedirs(os.path.join(vroot(), "d1"))
    os.makedirs(os.path.join(vroot(), ".hidden"))
    v = vols(admin)
    assert set(v) == {"d1"} and v["d1"]["status"] == "pending" and v["d1"]["checks"]["writable"]
    assert admin.post(f"/api/storage/{v['d1']['id']}/add").status_code == 200
    assert admin.post(f"/api/storage/{v['d1']['id']}/add").status_code == 409
    from app import storage
    assert os.path.exists(os.path.join(vroot(), "d1", storage.MARKER))
    shutil.move(os.path.join(vroot(), "d1"), os.path.join(vroot(), ".gone"))
    assert vols(admin)["d1"]["status"] == "missing"
    shutil.move(os.path.join(vroot(), ".gone"), os.path.join(vroot(), "d1"))
    assert vols(admin)["d1"]["status"] == "active"


def test_G1_marker_survives_new_database(env, admin):
    os.makedirs(os.path.join(vroot(), "d1"))
    admin.post(f"/api/storage/{vols(admin)['d1']['id']}/add")
    from app import db as dbm
    from app.db import Volume
    with dbm.new_session() as s:
        s.query(Volume).delete()
        s.commit()
    assert vols(admin)["d1"]["status"] == "active"


def test_G2_add_needs_admin_session(app, admin, make_user):
    os.makedirs(os.path.join(vroot(), "d1"))
    vid = vols(admin)["d1"]["id"]
    alice = make_user("alice")
    assert alice.post(f"/api/storage/{vid}/add").status_code == 403
    assert admin.post("/api/storage/999/add").status_code == 404
    assert admin.post(f"/api/storage/{vid}/explode").status_code == 404


def _two_active(admin):
    for n in ("d1", "d2"):
        os.makedirs(os.path.join(vroot(), n), exist_ok=True)
    v = vols(admin)
    for n in ("d1", "d2"):
        assert admin.post(f"/api/storage/{v[n]['id']}/add").status_code == 200
    return vols(admin)


def _drain_all(vid):
    from app import db as dbm, storage
    total = 0
    for _ in range(20):
        with dbm.new_session() as s:
            n = storage.drain_step(s, vid, max_files=2)
        total += n
        if n == 0:
            break
    return total


def test_G3_drain_moves_everything_and_reports_progress(admin):
    v = _two_active(admin)
    for i in range(5):
        p = os.path.join(vroot(), "d1", "shared", "eng", f"f{i}.bin")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(bytes([i]) * 100)
    assert admin.post(f"/api/storage/{v['d1']['id']}/drain").status_code == 200
    assert admin.post(f"/api/storage/{v['d2']['id']}/drain").status_code == 409  # last active can't drain
    _drain_all(v["d1"]["id"])
    after = vols(admin)["d1"]
    assert after["status"] == "drained"
    for i in range(5):
        assert open(os.path.join(vroot(), "d2", "shared", "eng", f"f{i}.bin"), "rb").read() == bytes([i]) * 100
    assert after["drain"]["moved"] == after["drain"]["total"], after["drain"]


def test_G3_drain_keeps_owner_and_acl(admin):
    if shutil.which("setfacl") is None:
        pytest.fail("setfacl missing")
    v = _two_active(admin)
    p = os.path.join(vroot(), "d1", "shared", "eng", "f.bin")
    os.makedirs(os.path.dirname(p))
    open(p, "wb").write(b"x")
    os.chown(p, 101101, 102001)
    subprocess.run(["setfacl", "-m", "g:102001:rw-,o::---", p], check=True)
    admin.post(f"/api/storage/{v['d1']['id']}/drain")
    _drain_all(v["d1"]["id"])
    q = os.path.join(vroot(), "d2", "shared", "eng", "f.bin")
    st = os.stat(q)
    acl = subprocess.run(["getfacl", "--numeric", q], capture_output=True, text=True).stdout
    assert "group:102001:rw-" in acl, acl
    assert st.st_uid == 101101, f"owner after drain is uid {st.st_uid} (was 101101)"


def test_G3_drain_does_not_overwrite_existing_file_on_target(admin):
    """Same relative path exists on both volumes (mergerfs shows one of them). Drain must not silently overwrite."""
    v = _two_active(admin)
    for n, data in (("d1", b"old-from-d1"), ("d2", b"newer-on-d2")):
        p = os.path.join(vroot(), n, "users", "a", "f.txt")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
    admin.post(f"/api/storage/{v['d1']['id']}/drain")
    _drain_all(v["d1"]["id"])
    assert open(os.path.join(vroot(), "d2", "users", "a", "f.txt"), "rb").read() == b"newer-on-d2"


def test_G3_undrain(admin):
    v = _two_active(admin)
    admin.post(f"/api/storage/{v['d1']['id']}/drain")
    assert admin.post(f"/api/storage/{v['d1']['id']}/undrain").status_code == 200
    assert vols(admin)["d1"]["status"] == "active"


def test_G4_policy(admin, make_user):
    for p in ("mfs", "ff", "epmfs"):
        assert admin.put("/api/storage/policy", json={"policy": p}).json()["policy"] == p
    assert admin.put("/api/storage/policy", json={"policy": "rand"}).status_code == 400
    assert make_user("alice").put("/api/storage/policy", json={"policy": "ff"}).status_code == 403


# ---- H -----------------------------------------------------------------------------------
def getfacl(p):
    return subprocess.run(["getfacl", "--omit-header", "--numeric", p], capture_output=True, text=True).stdout


@pytest.fixture()
def smb(ad_env, app, admin, monkeypatch):
    if shutil.which("setfacl") is None:
        pytest.fail("setfacl missing")
    from app import config
    monkeypatch.setattr(config.settings, "smb_enabled", True)
    run_sync(fake_ad({"dana": (1101, False), "eli": (1102, False)},
                     {"AB-Platform": (2001, ["dana"]), "AB-Design": (2002, ["eli"]), "AB-Admins": (2003, [])}))
    return config.settings.tree_root


def T(tree, p):
    return os.path.join(tree, p.lstrip("/"))


def test_H1_mapping(smb, admin, make_user):
    make_user("lou")  # local user: no ACL entry
    save(admin, "/shared/platform/projects/spec.md", "x")
    admin.post("/api/fs/share", json={"path": "/shared/platform/projects", "kind": "group", "name": "design", "role": "viewer"})
    admin.post("/api/fs/share", json={"path": "/shared/platform/projects", "kind": "user", "name": "lou", "role": "editor"})
    d = getfacl(T(smb, "/shared/platform/projects"))
    f = getfacl(T(smb, "/shared/platform/projects/spec.md"))
    assert "group:102002:r-x" in d and "group:102001:rwx" in d and "other::---" in d
    assert "group:102002:r--" in f and "group:102001:rw-" in f and "other::---" in f
    home = getfacl(T(smb, "/users/dana"))
    assert "user:101101:rwx" in home and "group:102001" not in home


def test_H1_C6_manual_suspension_removes_smb_access(smb, admin):
    """Suspending dana in the app must take her off the share's ACLs (she keeps a valid AD/Kerberos login)."""
    assert "user:101101:rwx" in getfacl(T(smb, "/users/dana"))
    admin.patch(f"/api/users/{uid_of(admin, 'dana')}", json={"status": "suspended"})
    assert "101101" not in getfacl(T(smb, "/users/dana")), "suspended person still has an ACL entry on the share"


def test_H2_new_and_uploaded_files_get_folder_acl(smb, admin):
    admin.post("/api/fs/upload", data={"folder": "/shared/platform/projects"}, files={"file": ("u.bin", b"x")})
    assert "group:102001:rw-" in getfacl(T(smb, "/shared/platform/projects/u.bin"))
    m = MCP(admin.c.app, oauth_sign_in(admin.c.app, admin)["access_token"])
    m.ok("save_artifact", path="/shared/platform/projects/m.md", content="m")
    assert "group:102001:rw-" in getfacl(T(smb, "/shared/platform/projects/m.md"))


def test_H2_moved_file_gets_new_folder_acl(smb, admin):
    save(admin, "/shared/platform/projects/plan.md", "p")
    r = admin.post("/api/fs/move", json={"src": "/shared/platform/projects/plan.md", "dst": "/shared/design/projects/plan.md"})
    assert r.status_code == 200
    acl = getfacl(T(smb, "/shared/design/projects/plan.md"))
    assert "group:102002:rw-" in acl and "102001" not in acl, acl


def test_H2_restored_file_gets_current_folder_acl(smb, admin):
    save(admin, "/shared/platform/projects/plan.md", "p")
    tid = admin.post("/api/fs/delete", json={"path": "/shared/platform/projects/plan.md"}).json()["trash_id"]
    admin.post("/api/fs/share", json={"path": "/shared/platform/projects", "kind": "group", "name": "design", "role": "viewer"})
    admin.post("/api/trash/restore", json={"id": tid})
    assert "group:102002:r--" in getfacl(T(smb, "/shared/platform/projects/plan.md"))


def test_H2_acl_after_unshare_and_folder_move(smb, admin):
    save(admin, "/shared/platform/projects/x/f.md")
    admin.post("/api/fs/share", json={"path": "/shared/platform/projects/x", "kind": "group", "name": "design", "role": "editor"})
    assert "group:102002:rw-" in getfacl(T(smb, "/shared/platform/projects/x/f.md"))
    admin.post("/api/fs/move", json={"src": "/shared/platform/projects/x", "dst": "/shared/design/projects/x"})
    acl = getfacl(T(smb, "/shared/design/projects/x/f.md"))
    assert "group:102002:rw-" in acl and "102001" not in acl
    acc = admin.get("/api/fs/access", params={"path": "/shared/design/projects/x"}).json()["access"]
    gid = next(a["grant_id"] for a in acc if a["via"] == "/shared/design/projects/x")
    admin.delete(f"/api/grants/{gid}")
    assert "group:102002:rw-" in getfacl(T(smb, "/shared/design/projects/x/f.md"))  # still via team folder


def test_H3_drift_on_files_and_subfolders_is_reported(smb, admin):
    save(admin, "/shared/platform/projects/sub/f.md")
    assert admin.get("/api/smb").json()["drift"] == []
    subprocess.run(["setfacl", "-m", "u:999999:rwx", T(smb, "/shared/platform/projects/sub")], check=True)
    subprocess.run(["setfacl", "-m", "u:999998:rw-", T(smb, "/shared/platform/projects/sub/f.md")], check=True)
    drift = admin.get("/api/smb").json()["drift"]
    assert drift, "a manual ACL change inside a team folder is not reported"
    admin.post("/api/smb/reconcile")
    assert "999999" not in getfacl(T(smb, "/shared/platform/projects/sub"))
    assert "999998" not in getfacl(T(smb, "/shared/platform/projects/sub/f.md"))


def test_H3_drift_reported_and_reconciled_on_team_root(smb, admin):
    subprocess.run(["setfacl", "-m", "g:999999:rwx", T(smb, "/shared/platform")], check=True)
    assert any(d["path"] == "/shared/platform" for d in admin.get("/api/smb").json()["drift"])
    admin.post("/api/smb/reconcile")
    assert "999999" not in getfacl(T(smb, "/shared/platform"))


def test_H4_dropped_files_indexed(env, admin):
    from app import config, main
    d = os.path.join(config.settings.tree_root, "users", "admin", "projects", "dropped")
    os.makedirs(d)
    open(os.path.join(d, "a.txt"), "w").write("a")
    open(os.path.join(d, ".hidden"), "w").write("h")
    out = main.run_jobs_once()
    assert out["index"] == 1
    r = admin.get("/api/fs/search", params={"q": "a.txt"}).json()["results"]
    assert r[0]["source"] == "smb"


# ---- I -----------------------------------------------------------------------------------
def test_I1_audit_covers_the_basics(app, admin, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/p/f.md", "f")
    alice.get("/api/fs/download", params={"path": "/users/alice/projects/p/f.md"})
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "user", "name": "bob", "role": "viewer"})
    alice.post("/api/fs/move", json={"src": "/users/alice/projects/p/f.md", "dst": "/users/alice/projects/p/g.md"})
    bob.get("/api/fs/download", params={"path": "/users/alice/inbox/nope.md"})  # denial
    m = MCP(app, oauth_sign_in(app, alice)["access_token"])
    m.ok("get_artifact", path="/users/alice/projects/p/g.md")
    m.ok("delete", path="/users/alice/projects/p/g.md")
    pat = alice.post("/api/tokens", json={"name": "ci-box"}).json()["token"]
    bearer(app, pat).post("/api/fs/save-text", json={"path": "/users/alice/inbox/ci.md", "content": "c"})
    ev = audit_events(admin)
    acts = {e["action"] for e in ev}
    assert {"signin.password", "file.save", "file.download", "grant.set", "item.move", "denied", "file.read", "item.delete",
            "token.create", "user.create", "oauth.approve", "oauth.token"} <= acts, acts
    mcp_read = next(e for e in ev if e["action"] == "file.read")
    assert mcp_read["via"] == "mcp" and "token" in mcp_read["detail"] and mcp_read["ip"]
    api_save = next(e for e in ev if e["action"] == "file.save" and e["target"] == "/users/alice/inbox/ci.md")
    assert api_save["via"] == "api" and "ci-box" in api_save["detail"]["token"]


def test_I1_preview_reads_are_audited(app, admin, make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/x.html", "<b>x</b>")
    url = alice.get("/api/fs/meta", params={"path": "/users/alice/inbox/x.html"}).json()["preview_url"]
    before = len([e for e in audit_events(admin) if e["target"] == "/users/alice/inbox/x.html"])
    alice.c.get(url)  # previews open inside the person's own session (spec change after UI-7)
    after = len([e for e in audit_events(admin) if e["target"] == "/users/alice/inbox/x.html"])
    assert after > before, "opening a file through a preview link leaves no audit entry"


def test_I1_admin_denials_are_audited(app, admin, make_user):
    alice = make_user("alice")
    alice.post("/api/users", json={"username": "eve", "password": PW, "system_role": "admin"})
    alice.patch(f"/api/users/{uid_of(admin, 'admin')}", json={"status": "suspended"})
    alice.put("/api/ingest/settings", json={"paused": True})
    ev = [e for e in audit_events(admin, user="alice") if not e["ok"]]
    assert len(ev) >= 3, f"denied admin attempts by a member are not in the audit log: {ev}"


def test_I1_trash_restore_and_version_restore_audited(admin, make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/f.md", "1")
    save(alice, "/users/alice/inbox/f.md", "2")
    alice.post("/api/fs/restore-version", json={"path": "/users/alice/inbox/f.md", "version": 1})
    tid = alice.post("/api/fs/delete", json={"path": "/users/alice/inbox/f.md"}).json()["trash_id"]
    alice.post("/api/trash/restore", json={"id": tid})
    acts = {e["action"] for e in audit_events(admin, user="alice")}
    assert {"file.restore_version", "item.restore"} <= acts


def test_I1_audit_only_for_auditors(make_user):
    alice = make_user("alice")
    assert alice.get("/api/audit").status_code == 403


def test_I2_overview(admin, make_user):
    ov = admin.get("/api/overview").json()
    assert {"people", "files", "series", "pool", "attention"} <= set(ov)
    assert len(ov["series"]) == 14, f"overview shows {len(ov['series'])} days of saves, spec says the last 14"
    assert make_user("alice").get("/api/overview").status_code == 403


def test_I3_access_check_answers_for_a_person(admin, make_user, make_group):
    """'Can X reach path Y, and why?' The endpoint must take a person and answer for that person."""
    make_user("alice")
    make_user("bob")
    make_group("eng", ["bob"])
    r = admin.get("/api/access-check", params={"path": "/shared/eng/projects", "user": "alice"}).json()
    r2 = admin.get("/api/access-check", params={"path": "/shared/eng/projects", "user": "bob"}).json()
    assert r != r2, "access-check ignores the person and returns the same list for everyone"
