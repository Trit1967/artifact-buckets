"""Protected storage (ZFS) through the app, against the helper running in its pretend-ZFS mode."""
import importlib.util
import os
import threading
import time
import uuid

import pytest

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("bucketsd", os.path.join(HERE, "..", "..", "deploy", "zfs", "bucketsd.py"))
bucketsd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bucketsd)

SPARE1 = "/dev/disk/by-id/scsi-0QEMU_HARDDISK_spare-1"
SPARE2 = "/dev/disk/by-id/scsi-0QEMU_HARDDISK_spare-2"


@pytest.fixture()
def helper(env, monkeypatch):
    sock = f"/tmp/abz-{uuid.uuid4().hex[:8]}.sock"
    monkeypatch.setenv("AB_ZFS_SOCKET", sock)
    from app import config
    config.reload_settings()
    tree = str(env / "tree")
    os.makedirs(tree, exist_ok=True)
    z = bucketsd.FakeZfs(str(env / "fakezfs"), tree)
    h = bucketsd.Helper(z, bucketsd.State(str(env / "fakezfs" / "state.json")), tree)
    threading.Thread(target=bucketsd.serve, args=(h, sock, None), daemon=True).start()
    for _ in range(50):
        if os.path.exists(sock):
            break
        time.sleep(0.05)
    yield h
    if os.path.exists(sock):
        os.unlink(sock)


def save(web, path, content):
    r = web.post("/api/fs/save-text", json={"path": path, "content": content})
    assert r.status_code == 200, r.text
    return r.json()


def test_status_and_overview(helper, admin):
    st = admin.get("/api/storage/protected").json()
    assert st["health"] == "ONLINE" and st["summary"]["healthy"] and st["summary"]["file_backed"]
    assert len(st["free_disks"]) == 2
    assert admin.get("/api/auth/me").json()["protected_storage"] is True


def test_members_cannot_touch_storage(helper, make_user):
    maya = make_user("maya")
    assert maya.get("/api/storage/protected").status_code == 403
    assert maya.post("/api/storage/protected/point").status_code == 403
    assert maya.post("/api/storage/protected/restore", json={"point": "x", "path": "/users/maya/a"}).status_code == 403


def test_restore_file_as_new_version_and_folder_beside(helper, admin, make_user):
    maya = make_user("maya")
    save(maya, "/users/maya/projects/p/notes.md", "first")
    save(maya, "/users/maya/projects/p/other.md", "other")
    point = admin.post("/api/storage/protected/point").json()["name"]
    save(maya, "/users/maya/projects/p/notes.md", "second")
    assert maya.post("/api/fs/delete", json={"path": "/users/maya/projects/p/other.md"}).status_code == 200

    items = admin.get("/api/storage/protected/browse", params={"point": point, "path": "/users/maya/projects/p"}).json()["items"]
    assert {i["name"] for i in items} == {"notes.md", "other.md"}

    r = admin.post("/api/storage/protected/restore", json={"point": point, "path": "/users/maya/projects/p/notes.md"})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 3
    assert maya.get("/api/fs/download", params={"path": "/users/maya/projects/p/notes.md"}).text == "first"

    r = admin.post("/api/storage/protected/restore", json={"point": point, "path": "/users/maya/projects/p"})
    assert r.status_code == 200, r.text
    target = r.json()["restored_to"]
    assert target.startswith("/users/maya/projects/p (restored ") and r.json()["files"] == 2
    assert maya.get("/api/fs/download", params={"path": target + "/other.md"}).text == "other"
    # Nothing is left in the staging area.
    from app import paths
    assert not [n for n in os.listdir(os.path.join(paths.tree_root(), ".tmp")) if n.startswith("restore-")]


def test_restore_refuses_bad_points_and_paths(helper, admin):
    point = admin.post("/api/storage/protected/point").json()["name"]
    assert admin.post("/api/storage/protected/restore", json={"point": "ab-manual.20990101-0000", "path": "/users/admin/x"}).status_code == 400
    assert admin.post("/api/storage/protected/restore", json={"point": point, "path": "/users/../etc/passwd"}).status_code == 400
    assert admin.post("/api/storage/protected/restore", json={"point": point, "path": "/users/admin/nothing-here.md"}).status_code == 400


def test_disks_mirror_replace_grow(helper, admin):
    st = admin.get("/api/storage/protected").json()
    first = st["vdevs"][0]["disks"][0]["id"]
    r = admin.post("/api/storage/protected/disks/mirror", json={"existing": first, "new": SPARE1})
    assert r.status_code == 200 and r.json()["vdevs"][0]["type"] == "mirror"
    helper.z.fault(first)
    st = admin.get("/api/storage/protected").json()
    assert not st["summary"]["healthy"] and st["summary"]["problems"][0]["disk"] == first
    assert any(a["kind"] == "disk" for a in admin.get("/api/overview").json()["attention"])
    r = admin.post("/api/storage/protected/disks/replace", json={"old": first, "new": SPARE2})
    assert r.status_code == 200 and admin.get("/api/storage/protected").json()["summary"]["healthy"]
    # A disk that isn't free can't be used twice.
    assert admin.post("/api/storage/protected/disks/add", json={"disks": [SPARE1], "layout": "single"}).status_code == 400


def test_grow_file_backed_space(helper, admin):
    st = admin.get("/api/storage/protected").json()
    dev = st["vdevs"][0]["disks"][0]["id"]
    before = st["size"]
    r = admin.post("/api/storage/protected/disks/grow", json={"disk": dev, "gigabytes": 5})
    assert r.status_code == 200 and r.json()["size"] == before + 5 * 1024 ** 3


def test_schedule_and_backup(helper, admin):
    r = admin.put("/api/storage/protected/schedule", json={"hourly": 6, "daily": 7, "weekly": 4, "monthly": 3})
    assert r.status_code == 200 and r.json()["hourly"] == 6
    assert admin.put("/api/storage/protected/backup", json={"kind": "ssh", "host": "bad host;rm", "dataset": "b/x"}).status_code == 400
    assert admin.put("/api/storage/protected/backup", json={"kind": "ssh", "host": "unreachable.example", "dataset": "backup/buckets"}).status_code == 200
    res = admin.post("/api/storage/protected/backup").json()
    assert "No route to host" in res["last_error"]
    assert any(a["kind"] == "backup" for a in admin.get("/api/overview").json()["attention"])
    assert admin.put("/api/storage/protected/backup", json={"kind": "local", "dataset": "backup/buckets"}).status_code == 200
    res = admin.post("/api/storage/protected/backup").json()
    assert res["last_error"] is None and res["last"]


def test_schedule_tick_and_prune(helper):
    import datetime as dt
    t = dt.datetime(2026, 11, 1, 3, 30, tzinfo=dt.timezone.utc)  # a Sunday, first of the month
    helper.s.data["keep"] = {"hourly": 2, "daily": 1, "weekly": 1, "monthly": 1}
    for h in range(5):
        helper.tick(t + dt.timedelta(hours=h))
    kinds = [s["kind"] for s in helper.z.snapshots()]
    assert kinds.count("hourly") == 2 and kinds.count("daily") == 1 and kinds.count("monthly") == 1


def test_unavailable_without_helper(app, admin):
    assert admin.get("/api/storage/protected").status_code == 503
    assert admin.get("/api/auth/me").json()["protected_storage"] is False
