"""B. Folders, files and permissions - functional and attacker tests."""
import os

import pytest
from fastapi.testclient import TestClient

from conftest import PW, audit_events, bearer, oauth_sign_in, save, uid_of


def tree():
    from app import config
    return config.settings.tree_root


def disk(p):
    return os.path.join(tree(), p.lstrip("/"))


# ---- B1 B2 B3 B4 -------------------------------------------------------------------
def test_B1_layout(make_user, make_group):
    make_user("alice")
    make_group("eng", ["alice"])
    for p in ("users/alice/inbox", "users/alice/projects", "shared/eng/projects"):
        assert os.path.isdir(disk(p))


def test_B2_roles_inherit_and_highest_wins(admin, make_user, make_group):
    alice, bob = make_user("alice"), make_user("bob")
    make_group("eng", ["bob"])
    save(admin, "/shared/eng/projects/x/doc.md", "d")
    # group gives editor on /shared/eng; a direct viewer grant lower down must not reduce it
    admin.post("/api/fs/share", json={"path": "/shared/eng/projects/x", "kind": "user", "name": "bob", "role": "viewer"})
    assert bob.post("/api/fs/save-text", json={"path": "/shared/eng/projects/x/b.md", "content": "b"}).status_code == 200
    # viewer can read, not write/delete
    save(alice, "/users/alice/projects/p/a.md", "a")
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "user", "name": "bob", "role": "viewer"})
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/p/a.md"}).text == "a"
    assert bob.post("/api/fs/save-text", json={"path": "/users/alice/projects/p/b.md", "content": "b"}).status_code == 403
    assert bob.post("/api/fs/delete", json={"path": "/users/alice/projects/p/a.md"}).status_code == 403
    # editor can write and delete but not share
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "user", "name": "bob", "role": "editor"})
    assert bob.post("/api/fs/save-text", json={"path": "/users/alice/projects/p/b.md", "content": "b"}).status_code == 200
    assert bob.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "group", "name": "eng", "role": "viewer"}).status_code == 403
    assert bob.post("/api/fs/delete", json={"path": "/users/alice/projects/p/b.md"}).status_code == 200


def test_B3_admin_owner_everywhere_auditor_no_files(admin, make_user):
    alice = make_user("alice")
    aud = make_user("audrey", role="auditor")
    save(alice, "/users/alice/inbox/s.txt", "secret")
    assert admin.get("/api/fs/download", params={"path": "/users/alice/inbox/s.txt"}).text == "secret"
    assert aud.get("/api/fs/download", params={"path": "/users/alice/inbox/s.txt"}).status_code == 404
    assert aud.get("/api/fs/list", params={"path": "/users/alice"}).status_code == 404
    assert aud.get("/api/fs/search", params={"q": "s"}).json()["results"] == []
    assert aud.get("/api/users").status_code == 200 and aud.get("/api/audit").status_code == 200
    assert aud.get("/api/access-check", params={"path": "/users/alice"}).status_code == 200


def test_B4_private_homes(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/inbox/s.txt")
    assert bob.get("/api/fs/list", params={"path": "/users/alice"}).status_code == 404
    assert bob.get("/api/fs/meta", params={"path": "/users/alice/inbox/s.txt"}).status_code == 404
    names = [f["name"] for f in bob.get("/api/fs/list", params={"path": "/users"}).json()["folders"]]
    assert names == ["bob"]


# ---- B5 hidden when no access: no existence oracle ----------------------------------
def test_B5_move_does_not_reveal_existence(make_user):
    """bob probes alice's home with /api/fs/move. Existing and missing paths must answer the same."""
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/secret-plan.md")
    a = bob.post("/api/fs/move", json={"src": "/users/alice/projects/secret-plan.md", "dst": "/users/x"})
    b = bob.post("/api/fs/move", json={"src": "/users/alice/projects/no-such.md", "dst": "/users/x"})
    assert (a.status_code, a.json()["message"]) == (b.status_code, b.json()["message"]), (a.text, b.text)
    assert a.status_code == 404


def test_B5_share_does_not_reveal_existence(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    os.makedirs(disk("/users/alice/projects/acquisition"), exist_ok=True)
    a = bob.post("/api/fs/share", json={"path": "/users/alice/projects/acquisition", "kind": "user", "name": "bob", "role": "owner"})
    b = bob.post("/api/fs/share", json={"path": "/users/alice/projects/nothing-here", "kind": "user", "name": "bob", "role": "owner"})
    assert (a.status_code, a.json()["message"]) == (b.status_code, b.json()["message"]), (a.text, b.text)


def test_B5_delete_does_not_reveal_usernames(make_user):
    make_user("alice")
    bob = make_user("bob")
    a = bob.post("/api/fs/delete", json={"path": "/users/alice"})
    b = bob.post("/api/fs/delete", json={"path": "/users/nobody-here"})
    assert (a.status_code, a.json()["message"]) == (b.status_code, b.json()["message"]), (a.text, b.text)


def test_B5_404_for_unreachable_and_listing_only_shows_reachable(make_user, make_group):
    alice, bob = make_user("alice"), make_user("bob")
    make_group("eng", ["alice"])
    make_group("ops", ["bob"])
    save(alice, "/users/alice/projects/p/deep/f.md")
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p/deep", "kind": "user", "name": "bob", "role": "viewer"})
    for url, params in [("/api/fs/list", {"path": "/shared/eng"}), ("/api/fs/versions", {"path": "/shared/eng/x"}),
                        ("/api/fs/access", {"path": "/users/alice/inbox"})]:
        assert bob.get(url, params=params).status_code == 404, url
    shared = [f["name"] for f in bob.get("/api/fs/list", params={"path": "/shared"}).json()["folders"]]
    assert shared == ["ops"]
    # leads-to listing: alice -> projects -> p -> deep, but no files and no siblings
    os.makedirs(disk("/users/alice/projects/p/other"), exist_ok=True)
    lst = bob.get("/api/fs/list", params={"path": "/users/alice/projects/p"}).json()
    assert [f["name"] for f in lst["folders"]] == ["deep"] and lst["files"] == []


# ---- B6 path safety ---------------------------------------------------------------------
BAD = ["/users/alice/inbox/../bob/x.txt", "/users/alice/inbox/./x.txt", "/users/alice/inbox/.hidden",
       "/users/alice/inbox/a<b.txt", "/users/alice/inbox/a:b.txt", "/users/alice/inbox/a|b", "/users/alice/inbox/a?b",
       "/users/alice/inbox/a*b", '/users/alice/inbox/a"b', "/users/alice/inbox/a\\b", "/users/alice/inbox/trail.",
       "/users/alice/inbox/trail ", "/users/alice/inbox/ lead", "/users/alice/inbox/nul\x00.txt",
       "/etc/passwd", "/tmp/x", "/.versions/1/x", "/.trash/x", "/users/alice/inbox//x", "/users/alice/inbox/" + "a" * 256,
       "/users/alice/inbox/tab\tname"]


@pytest.mark.parametrize("bad", BAD)
def test_B6_bad_paths_refused(make_user, bad):
    alice = make_user("alice")
    r = alice.post("/api/fs/save-text", json={"path": bad, "content": "x"})
    assert r.status_code == 400, (bad, r.status_code, r.text)


def test_B6_url_encoded_traversal(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/inbox/s.txt", "secret")
    for p in ["/users/bob/..%2falice/inbox/s.txt", "/users/bob/%2e%2e/alice/inbox/s.txt", "/users/bob/%252e%252e/alice/inbox/s.txt"]:
        r = bob.c.get("/api/fs/download?path=" + p)
        assert r.status_code in (400, 404) and "secret" not in r.text, p


def test_B6_symlink_outside_tree_refused(make_user):
    alice = make_user("alice")
    os.symlink("/etc", disk("/users/alice/inbox/etc"))
    r = alice.get("/api/fs/download", params={"path": "/users/alice/inbox/etc/passwd"})
    assert r.status_code in (400, 404) and "root:" not in r.text


def test_B6_symlink_inside_tree_cannot_reach_other_home(make_user):
    """A symlink in bob's home that points at alice's home (e.g. made over SMB or by a dropped archive)
    must not let bob read or write alice's private files."""
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/inbox/diary.txt", "alice-private")
    os.symlink(disk("/users/alice"), disk("/users/bob/projects/link"))
    r = bob.get("/api/fs/download", params={"path": "/users/bob/projects/link/inbox/diary.txt"})
    assert "alice-private" not in r.text, "bob read alice's private file through a symlink"
    r = bob.post("/api/fs/save-text", json={"path": "/users/bob/projects/link/inbox/planted.txt", "content": "x"})
    assert not os.path.exists(disk("/users/alice/inbox/planted.txt")), "bob wrote into alice's home through a symlink"


# ---- B7 B8 -------------------------------------------------------------------------------
def test_B7_save_versions_and_noop(make_user):
    alice = make_user("alice")
    assert save(alice, "/users/alice/inbox/a.txt", "1")["version"] == 1
    assert save(alice, "/users/alice/inbox/a.txt", "2")["version"] == 2
    r = save(alice, "/users/alice/inbox/a.txt", "2")
    assert r["version"] == 2 and r.get("unchanged") is True
    up = alice.post("/api/fs/upload", data={"folder": "/users/alice/inbox"}, files={"file": ("a.txt", b"3")})
    assert up.status_code == 200 and up.json()["version"] == 3


def test_B8_versions_restore_and_prune(make_user):
    alice = make_user("alice")
    for i in range(1, 6):
        save(alice, "/users/alice/projects/p/f.txt", f"v{i}")
    vs = alice.get("/api/fs/versions", params={"path": "/users/alice/projects/p/f.txt"}).json()["versions"]
    assert [v["version"] for v in vs] == [5, 4, 3, 2, 1]
    r = alice.post("/api/fs/restore-version", json={"path": "/users/alice/projects/p/f.txt", "version": 2})
    assert r.status_code == 200 and r.json()["version"] == 6
    assert alice.get("/api/fs/download", params={"path": "/users/alice/projects/p/f.txt"}).text == "v2"
    assert alice.put("/api/folders/settings", json={"path": "/users/alice/projects/p", "keep_versions": 3}).status_code == 200
    save(alice, "/users/alice/projects/p/f.txt", "v7")
    vs = alice.get("/api/fs/versions", params={"path": "/users/alice/projects/p/f.txt"}).json()["versions"]
    assert len(vs) == 3 and vs[0]["version"] == 7
    assert alice.post("/api/fs/restore-version", json={"path": "/users/alice/projects/p/f.txt", "version": 1}).status_code == 404


def test_B8_viewer_cannot_restore_version(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/p/f.txt", "1")
    save(alice, "/users/alice/projects/p/f.txt", "2")
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "user", "name": "bob", "role": "viewer"})
    assert bob.post("/api/fs/restore-version", json={"path": "/users/alice/projects/p/f.txt", "version": 1}).status_code == 403


def test_B8_huge_version_number_is_not_a_500(make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/f.txt", "1")
    r = alice.post("/api/fs/restore-version", json={"path": "/users/alice/inbox/f.txt", "version": 10**20})
    assert r.status_code in (400, 404, 422), r.status_code


# ---- B9 move -------------------------------------------------------------------------
def test_B9_move_keeps_grants_and_versions(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/p/f.txt", "1")
    save(alice, "/users/alice/projects/p/f.txt", "2")
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "user", "name": "bob", "role": "viewer"})
    r = alice.post("/api/fs/move", json={"src": "/users/alice/projects/p", "dst": "/users/alice/projects/q"})
    assert r.status_code == 200
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/q/f.txt"}).text == "2"
    assert len(alice.get("/api/fs/versions", params={"path": "/users/alice/projects/q/f.txt"}).json()["versions"]) == 2
    assert alice.post("/api/fs/move", json={"src": "/users/alice/projects/q", "dst": "/users/alice/projects/q/sub"}).status_code == 400


def test_B9_move_into_self_case_variants(make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/projects/q/f.txt")
    r = alice.post("/api/fs/move", json={"src": "/users/alice/projects/q", "dst": "/users/alice/projects/q/"})
    assert r.status_code == 400


def test_B9_case_twin_folders_are_not_mixed_up(make_user):
    """Linux allows /p/a and /p/A side by side. Moving 'a' must not touch the grants or files of 'A'."""
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/a/sub/lower.txt", "lower")
    save(alice, "/users/alice/projects/A/sub/upper.txt", "upper")
    assert alice.post("/api/fs/share", json={"path": "/users/alice/projects/A/sub", "kind": "user", "name": "bob", "role": "viewer"}).status_code == 200
    assert alice.post("/api/fs/move", json={"src": "/users/alice/projects/a", "dst": "/users/alice/projects/c"}).status_code == 200
    # bob was given 'A/sub' only; he must not have gained 'c/sub' (which is the old 'a/sub').
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/c/sub/lower.txt"}).status_code == 404, "bob gained the moved twin folder"
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/A/sub/upper.txt"}).text == "upper", "bob lost his grant"


def test_B9_case_twin_delete_keeps_sibling_grants(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/a/sub/lower.txt", "lower")
    save(alice, "/users/alice/projects/A/sub/upper.txt", "upper")
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/A/sub", "kind": "user", "name": "bob", "role": "viewer"})
    assert alice.post("/api/fs/delete", json={"path": "/users/alice/projects/a"}).status_code == 200
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/A/sub/upper.txt"}).text == "upper"


def test_B9_move_out_of_shared_needs_owner_when_subgrants(admin, make_user, make_group):
    alice, bob, carol = make_user("alice"), make_user("bob"), make_user("carol")
    make_group("eng", ["alice", "bob"])
    save(admin, "/shared/eng/projects/x/f.txt")
    admin.post("/api/fs/share", json={"path": "/shared/eng/projects/x", "kind": "user", "name": "carol", "role": "viewer"})
    assert bob.post("/api/fs/move", json={"src": "/shared/eng/projects/x", "dst": "/users/bob/projects/x"}).status_code == 403
    assert bob.post("/api/fs/move", json={"src": "/shared/eng/projects", "dst": "/users/bob/projects/all"}).status_code == 403
    assert bob.post("/api/fs/delete", json={"path": "/shared/eng/projects"}).status_code == 403


def test_B9_move_into_path_through_file_is_not_500(make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/a.txt")
    save(alice, "/users/alice/inbox/b.txt")
    r = alice.post("/api/fs/move", json={"src": "/users/alice/inbox/b.txt", "dst": "/users/alice/inbox/a.txt/x/b.txt"})
    assert r.status_code in (400, 409), r.status_code


def test_B7_save_under_a_file_is_not_500(make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/a.txt")
    r = alice.post("/api/fs/save-text", json={"path": "/users/alice/inbox/a.txt/x/y.txt", "content": "x"})
    assert r.status_code in (400, 409), r.status_code
    r = alice.post("/api/fs/mkdir", json={"path": "/users/alice/inbox/a.txt/x/y"})
    assert r.status_code in (400, 409), r.status_code


# ---- B10 trash -----------------------------------------------------------------------
def test_B10_trash_restore_with_grants_and_privacy(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/p/sub/f.txt", "f")
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p/sub", "kind": "user", "name": "bob", "role": "viewer"})
    tid = alice.post("/api/fs/delete", json={"path": "/users/alice/projects/p"}).json()["trash_id"]
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/p/sub/f.txt"}).status_code == 404
    assert bob.get("/api/trash").json()["items"] == []
    assert bob.post("/api/trash/restore", json={"id": tid}).status_code in (403, 404)
    assert alice.post("/api/trash/restore", json={"id": tid}).status_code == 200
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/p/sub/f.txt"}).text == "f"


def test_B10_only_deleter_or_admin_restores(admin, make_user, make_group):
    """Spec: only the person who deleted it (or an admin) sees or restores it. A folder Owner who did not delete it must not."""
    alice, carol = make_user("alice"), make_user("carol")
    make_group("eng", ["alice"])
    admin.post("/api/fs/share", json={"path": "/shared/eng", "kind": "user", "name": "carol", "role": "owner"})
    save(alice, "/shared/eng/projects/f.txt")
    tid = alice.post("/api/fs/delete", json={"path": "/shared/eng/projects/f.txt"}).json()["trash_id"]
    assert carol.post("/api/trash/restore", json={"id": tid}).status_code in (403, 404)


def test_B10_grants_do_not_survive_delete(make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/p/f.txt")
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "user", "name": "bob", "role": "viewer"})
    alice.post("/api/fs/delete", json={"path": "/users/alice/projects/p"})
    save(alice, "/users/alice/projects/p/new.txt", "new")
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/p/new.txt"}).status_code == 404


def test_B10_purge_after_retention(make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/f.txt")
    alice.post("/api/fs/delete", json={"path": "/users/alice/inbox/f.txt"})
    from app import db as dbm, fs
    with dbm.new_session() as s:
        assert fs.purge_trash(s) == 0
        assert fs.purge_trash(s, older_than_days=0) == 1
    assert alice.get("/api/trash").json()["items"] == []


def test_B10_restore_after_losing_access_refused(admin, make_user, make_group):
    alice = make_user("alice")
    gid = make_group("eng", ["alice"])
    save(alice, "/shared/eng/projects/f.txt")
    tid = alice.post("/api/fs/delete", json={"path": "/shared/eng/projects/f.txt"}).json()["trash_id"]
    admin.delete(f"/api/groups/{gid}/members/alice")
    assert alice.post("/api/trash/restore", json={"id": tid}).status_code in (403, 404)


# ---- B11 ---------------------------------------------------------------------------------
@pytest.mark.parametrize("p", ["/users", "/shared", "/users/alice", "/shared/eng"])
def test_B11_protected_cannot_be_deleted(admin, make_user, make_group, p):
    make_user("alice")
    make_group("eng")
    assert admin.post("/api/fs/delete", json={"path": p}).status_code in (400, 403)


@pytest.mark.parametrize("src,dst", [("/users/alice", "/users/admin/projects/alice-home"), ("/shared/eng", "/shared/eng2"),
                                     ("/users/alice", "/users/alice2")])
def test_B11_protected_cannot_be_moved_even_by_admin(admin, make_user, make_group, src, dst):
    make_user("alice")
    make_group("eng")
    r = admin.post("/api/fs/move", json={"src": src, "dst": dst})
    assert r.status_code in (400, 403), r.text
    assert os.path.isdir(disk(src))


# ---- B12 B13 ----------------------------------------------------------------------------
def test_B12_home_owner_grant_cannot_be_removed(admin, make_user):
    alice = make_user("alice")
    access = admin.get("/api/fs/access", params={"path": "/users/alice"}).json()["access"]
    gid = next(a["grant_id"] for a in access if a["principal"].get("name") == "alice")
    assert admin.delete(f"/api/grants/{gid}").status_code == 403
    assert alice.delete(f"/api/grants/{gid}").status_code == 403


def test_B12_home_owner_grant_cannot_be_downgraded(make_user):
    """Alice shares her home with bob as Owner. Bob must not be able to demote Alice to Viewer of her own home."""
    alice, bob = make_user("alice"), make_user("bob")
    assert alice.post("/api/fs/share", json={"path": "/users/alice", "kind": "user", "name": "bob", "role": "owner"}).status_code == 200
    r = bob.post("/api/fs/share", json={"path": "/users/alice", "kind": "user", "name": "alice", "role": "viewer"})
    assert r.status_code in (400, 403), r.text
    assert alice.post("/api/fs/save-text", json={"path": "/users/alice/inbox/mine.txt", "content": "x"}).status_code == 200


def test_B12_share_unshare(make_user, make_group):
    alice, bob = make_user("alice"), make_user("bob")
    make_group("eng", ["bob"])
    save(alice, "/users/alice/projects/p/f.txt")
    assert alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "group", "name": "eng", "role": "viewer"}).status_code == 200
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/p/f.txt"}).status_code == 200
    assert alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "group", "name": "nope", "role": "viewer"}).status_code == 404
    assert alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "group", "name": "eng", "role": "god"}).status_code == 400
    assert alice.post("/api/fs/share", json={"path": "/users/alice/projects/p/f.txt", "kind": "group", "name": "eng", "role": "viewer"}).status_code == 404
    access = alice.get("/api/fs/access", params={"path": "/users/alice/projects/p"}).json()["access"]
    gid = next(a["grant_id"] for a in access if a["principal"].get("name") == "eng")
    assert bob.delete(f"/api/grants/{gid}").status_code == 403
    assert alice.delete(f"/api/grants/{gid}").status_code == 200
    assert bob.get("/api/fs/download", params={"path": "/users/alice/projects/p/f.txt"}).status_code == 404


def test_B13_who_has_access(admin, make_user, make_group):
    alice = make_user("alice")
    make_group("eng", ["alice"])
    save(alice, "/shared/eng/projects/p/f.txt")
    admin.post("/api/fs/share", json={"path": "/shared/eng/projects/p", "kind": "user", "name": "alice", "role": "owner"})
    acc = alice.get("/api/fs/access", params={"path": "/shared/eng/projects/p"}).json()["access"]
    by = {(a["principal"]["name"], a["via"]): a for a in acc}
    assert by[("eng", "/shared/eng")]["inherited"] is True and by[("eng", "/shared/eng")]["role"] == "editor"
    assert by[("alice", "/shared/eng/projects/p")]["inherited"] is False
    assert any(a["principal"]["type"] == "role" and a["principal"]["name"] == "admins" for a in acc)


# ---- B14 B15 ---------------------------------------------------------------------------
def _set_quota(admin, username, n):
    assert admin.patch(f"/api/users/{uid_of(admin, username)}", json={"quota_bytes": n}).status_code == 200


def test_B14_user_quota(admin, make_user):
    alice = make_user("alice")
    _set_quota(admin, "alice", 1000)
    save(alice, "/users/alice/inbox/a.bin", "x" * 900)
    r = alice.post("/api/fs/save-text", json={"path": "/users/alice/inbox/b.bin", "content": "x" * 200})
    assert r.status_code == 413 and r.json()["error"] == "quota_exceeded"
    # overwriting with a smaller file is fine
    save(alice, "/users/alice/inbox/a.bin", "x" * 950)


def test_B14_team_quota_and_admin_change(admin, make_user, make_group):
    alice = make_user("alice")
    make_group("eng", ["alice"])
    m = MCP_admin(admin)
    m.ok("set_quota", folder="/shared/eng", gigabytes=1000 / 1024**3)
    save(alice, "/shared/eng/projects/a.bin", "x" * 900)
    r = alice.post("/api/fs/save-text", json={"path": "/shared/eng/projects/b.bin", "content": "x" * 200})
    assert r.status_code == 413


def MCP_admin(admin):
    from conftest import MCP
    return MCP(admin.c.app, oauth_sign_in(admin.c.app, admin)["access_token"])


def test_B14_move_between_quota_roots_checked(admin, make_user, make_group):
    alice = make_user("alice")
    make_group("eng", ["alice"])
    _set_quota(admin, "alice", 1000)
    save(alice, "/users/alice/inbox/small.bin", "x" * 500)
    save(alice, "/shared/eng/projects/big.bin", "x" * 800)
    r = alice.post("/api/fs/move", json={"src": "/shared/eng/projects/big.bin", "dst": "/users/alice/inbox/big.bin"})
    assert r.status_code == 413


def test_B14_restore_from_trash_cannot_exceed_quota(admin, make_user):
    alice = make_user("alice")
    _set_quota(admin, "alice", 1000)
    save(alice, "/users/alice/inbox/a.bin", "x" * 900)
    tid = alice.post("/api/fs/delete", json={"path": "/users/alice/inbox/a.bin"}).json()["trash_id"]
    save(alice, "/users/alice/inbox/b.bin", "y" * 900)
    r = alice.post("/api/trash/restore", json={"id": tid})
    used = next(u for u in admin.get("/api/users").json()["users"] if u["username"] == "alice")["used_bytes"]
    assert r.status_code == 413 or used <= 1000, f"restore answered {r.status_code}; alice now uses {used} of 1000 bytes"


def test_B14_folder_settings_quota_validation(admin, make_user, make_group):
    make_group("eng")
    for bad in (-1, 0):
        r = admin.put("/api/folders/settings", json={"path": "/shared/eng", "quota_gb": bad})
        assert r.status_code == 400, (bad, r.status_code)


def test_B14_quota_on_non_root_folder_is_rejected_not_ignored(admin, make_group):
    make_group("eng")
    r = admin.put("/api/folders/settings", json={"path": "/shared/eng/projects", "quota_gb": 0.000001})
    assert r.status_code == 400, "quota on a sub-folder is stored but never enforced"


def test_B14_storage_quota_nan_inf_not_500(admin):
    r = admin.c.put("/api/storage/quotas", content=b'{"user_gb": Infinity}',
                    headers={"X-CSRF-Token": admin.csrf, "Content-Type": "application/json"})
    assert r.status_code in (400, 422), r.status_code
    r = admin.c.put("/api/storage/quotas", content=b'{"user_gb": NaN}',
                    headers={"X-CSRF-Token": admin.csrf, "Content-Type": "application/json"})
    assert r.status_code in (400, 422), r.status_code


def test_B15_size_limit(make_user, monkeypatch, app):
    from app import config
    monkeypatch.setattr(config.settings, "max_file_bytes", 100)
    alice = make_user("alice")
    r = alice.post("/api/fs/save-text", json={"path": "/users/alice/inbox/a.txt", "content": "x" * 101})
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    r = alice.post("/api/fs/upload", data={"folder": "/users/alice/inbox"}, files={"file": ("b.bin", b"x" * 101)})
    assert r.status_code == 413
    m = MCP_admin_like(app, alice)
    res = m.call("save_artifact", name="c.txt", content="x" * 101)
    assert res["isError"] and "too_large" in res["content"][0]["text"]


def MCP_admin_like(app, web):
    from conftest import MCP
    return MCP(app, oauth_sign_in(app, web)["access_token"])


# ---- B16 search ------------------------------------------------------------------------
def test_B16_search_only_visible(app, make_user, make_group):
    alice, bob = make_user("alice"), make_user("bob")
    make_group("eng", ["alice"])
    alice.post("/api/fs/save-text", json={"path": "/users/alice/inbox/plan-secret.md", "content": "x", "project": "zeus", "tags": ["Q3"]})
    alice.post("/api/fs/save-text", json={"path": "/shared/eng/projects/plan-team.md", "content": "x", "project": "zeus"})
    assert bob.get("/api/fs/search", params={"q": "plan"}).json()["results"] == []
    assert bob.get("/api/fs/search", params={"project": "zeus"}).json()["results"] == []
    assert bob.get("/api/fs/search", params={"folder": "/users/alice"}).json()["results"] == []
    assert bob.get("/api/fs/search", params={"tag": "q3"}).json()["results"] == []
    assert {r["name"] for r in alice.get("/api/fs/search", params={"q": "PLAN"}).json()["results"]} == {"plan-secret.md", "plan-team.md"}
    assert [r["name"] for r in alice.get("/api/fs/search", params={"tag": "q3"}).json()["results"]] == ["plan-secret.md"]
    assert [r["name"] for r in alice.get("/api/fs/search", params={"folder": "/shared/eng"}).json()["results"]] == ["plan-team.md"]
    # deleted files are not found
    alice.post("/api/fs/delete", json={"path": "/users/alice/inbox/plan-secret.md"})
    assert {r["name"] for r in alice.get("/api/fs/search", params={"q": "plan"}).json()["results"]} == {"plan-team.md"}
    # wildcards are literal
    assert alice.get("/api/fs/search", params={"q": "%"}).json()["results"] == []
    assert alice.get("/api/fs/search", params={"q": "_"}).json()["results"] == []


def test_B16_search_respects_token_folders(app, make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/a-doc.md")
    save(alice, "/users/alice/projects/p/b-doc.md")
    tok = alice.post("/api/tokens", json={"name": "t", "scope": "read", "folders": ["/users/alice/projects"]}).json()["token"]
    res = bearer(app, tok).get("/api/fs/search", params={"q": "doc"}).json()["results"]
    assert [r["name"] for r in res] == ["b-doc.md"]


# ---- B17 previews ---------------------------------------------------------------------
def test_B17_preview_sandboxed_and_download_attachment(app, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/inbox/x.html", "<script>alert(1)</script>")
    meta = alice.get("/api/fs/meta", params={"path": "/users/alice/inbox/x.html"}).json()
    c = TestClient(app)
    # Spec change after UI-7: the link works only in the session it was made for.
    assert c.get(meta["preview_url"]).status_code == 403
    assert bob.c.get(meta["preview_url"]).status_code == 403
    r = alice.c.get(meta["preview_url"])
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox") and "allow-same-origin" not in csp
    d = alice.get("/api/fs/download", params={"path": "/users/alice/inbox/x.html"})
    assert d.headers["content-disposition"].startswith("attachment")
    # tampered token
    bad = meta["preview_url"].replace("/preview/", "/preview/x")
    assert c.get(bad).status_code == 403


def test_B17_preview_expires(app, make_user, monkeypatch):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/x.html", "<b>x</b>")
    url = alice.get("/api/fs/meta", params={"path": "/users/alice/inbox/x.html"}).json()["preview_url"]
    import time
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    assert TestClient(app).get(url).status_code == 403


def test_B17_preview_stops_working_after_access_removed(app, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/projects/p/x.html", "<b>x</b>")
    alice.post("/api/fs/share", json={"path": "/users/alice/projects/p", "kind": "user", "name": "bob", "role": "viewer"})
    url = bob.get("/api/fs/meta", params={"path": "/users/alice/projects/p/x.html"}).json()["preview_url"]
    access = alice.get("/api/fs/access", params={"path": "/users/alice/projects/p"}).json()["access"]
    alice.delete(f"/api/grants/{next(a['grant_id'] for a in access if a['principal'].get('name') == 'bob')}")
    assert TestClient(app, raise_server_exceptions=False).get(url).status_code in (403, 404)


# ---- B18 --------------------------------------------------------------------------------
def test_B18_mcp_large_delete_boundary(app, make_user):
    alice = make_user("alice")
    from app import db as dbm, fs
    from app.perms import Actor
    from app.db import User
    from sqlalchemy import select
    with dbm.new_session() as s:
        u = s.scalar(select(User).where(User.username == "alice"))
        a = Actor(user=u)
        for i in range(50):
            fs.save_file(s, a, f"/users/alice/projects/fifty/{i}.txt", b"x")
        for i in range(51):
            fs.save_file(s, a, f"/users/alice/projects/fiftyone/{i}.txt", b"x")
    from conftest import MCP
    m = MCP(app, oauth_sign_in(app, alice)["access_token"])
    assert not m.call("delete", path="/users/alice/projects/fifty")["isError"]
    r = m.call("delete", path="/users/alice/projects/fiftyone")
    assert r["isError"] and "web app" in r["content"][0]["text"]


def test_B14_versions_count_towards_quota(admin, make_user):
    """Each new version keeps the old bytes on disk. With no version limit set (the default), a person with a 1000-byte
    quota can store unlimited data as versions. Expected: old versions (and trash) count, or saves are refused."""
    alice = make_user("alice")
    _set_quota(admin, "alice", 1000)
    codes = [alice.post("/api/fs/save-text", json={"path": "/users/alice/inbox/a.bin", "content": str(i) * 900}).status_code
             for i in range(10)]
    vers = alice.get("/api/fs/versions", params={"path": "/users/alice/inbox/a.bin"}).json()["versions"]
    stored = sum(v["size"] for v in vers)
    assert stored <= 1000 or 413 in codes, f"{stored} bytes stored as versions under a 1000-byte quota; every save answered {set(codes)}"
