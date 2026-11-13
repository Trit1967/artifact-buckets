"""C. Groups and directory (local groups, AD sync against an in-memory LDAP server)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import ADMIN_PW, DOM, BASE, PW, fake_ad, login, oauth_sign_in, run_sync, save, uid_of, user_row, bearer


def users(admin):
    return {u["username"]: u for u in admin.get("/api/users").json()["users"]}


def groups(admin):
    return {g["name"]: g for g in admin.get("/api/groups").json()["groups"]}


# ---- C1 -----------------------------------------------------------------------------
def test_C1_local_groups(admin, make_user, make_group):
    alice, bob = make_user("alice"), make_user("bob")
    gid = make_group("eng", ["alice"])
    assert alice.post("/api/fs/save-text", json={"path": "/shared/eng/projects/a.md", "content": "a"}).status_code == 200
    assert bob.get("/api/fs/list", params={"path": "/shared/eng"}).status_code == 404
    assert admin.post(f"/api/groups/{gid}/members", json={"username": "bob"}).status_code == 200
    assert bob.get("/api/fs/download", params={"path": "/shared/eng/projects/a.md"}).text == "a"
    assert admin.delete(f"/api/groups/{gid}/members/bob").status_code == 200
    assert bob.get("/api/fs/download", params={"path": "/shared/eng/projects/a.md"}).status_code == 404
    assert alice.post("/api/groups", json={"name": "x"}).status_code == 403
    assert alice.post(f"/api/groups/{gid}/members", json={"username": "bob"}).status_code == 403
    assert admin.post("/api/groups", json={"name": "Bad Name!"}).status_code == 400
    assert admin.post("/api/groups", json={"name": "eng"}).status_code == 409


# ---- C2 -----------------------------------------------------------------------------
def test_C2_sync_prefix_roles_readonly(ad_env, app, admin):
    conn = fake_ad({"dana": (1101, False), "eli": (1102, False), "gus": (1104, False)},
                   {"AB-Platform": (2001, ["dana", "eli"]), "ab-lowercase": (2005, ["eli"]), "AB-Admins": (2002, ["dana"]),
                    "AB-Auditors": (2003, ["gus", "dana"]), "Other": (2004, ["gus"])})
    before = len(conn.strategy.entries)
    run_sync(conn)
    u = users(admin)
    assert u["dana"]["system_role"] == "admin" and u["gus"]["system_role"] == "auditor" and u["eli"]["system_role"] == "member"
    assert "other" not in groups(admin) and "platform" in groups(admin)
    assert len(conn.strategy.entries) == before  # nothing written to AD


def test_C2_user_in_several_groups(ad_env, app, admin):
    conn = fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"]), "AB-Design": (2002, ["dana"])})
    run_sync(conn)
    assert sorted(users(admin)["dana"]["groups"]) == ["design", "platform"]


def test_C2_one_odd_account_does_not_stop_the_sync(ad_env, app, admin):
    """A computer account (sAMAccountName 'PC01$', objectClass user) or a name with a space in a synced group
    must not abort the whole directory sync for everyone else."""
    extra = [(f"CN=PC01,OU=Computers,{BASE}", {"objectClass": ["top", "person", "user", "computer"], "sAMAccountName": "PC01$",
                                                "objectSid": f"{DOM}-3001", "userAccountControl": 4096})]
    conn = fake_ad({"dana": (1101, False), "John Smith": (1102, False)},
                   {"AB-Platform": (2001, ["dana", "John Smith"])}, extra_entries=extra)
    conn.strategy.entries  # noqa
    # add the computer as a member too
    from ldap3 import MODIFY_ADD
    conn.modify(f"CN=AB-Platform,OU=Groups,{BASE}", {"member": [(MODIFY_ADD, [f"CN=PC01,OU=Computers,{BASE}"])]})
    try:
        run_sync(conn)
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"sync aborted: {type(e).__name__}: {e}")
    assert users(admin)["dana"]["status"] == "active"


# ---- C3 lifecycle --------------------------------------------------------------------
def test_C3_disable_remove_and_return(ad_env, app, admin):
    run_sync(fake_ad({"dana": (1101, False), "eli": (1102, False)}, {"AB-Platform": (2001, ["dana", "eli"])}))
    run_sync(fake_ad({"dana": (1101, True), "eli": (1102, False)}, {"AB-Platform": (2001, ["dana"])}))
    u = users(admin)
    assert u["dana"]["status"] == "suspended" and u["eli"]["status"] == "suspended"
    run_sync(fake_ad({"dana": (1101, False), "eli": (1102, False)}, {"AB-Platform": (2001, ["dana", "eli"])}))
    u = users(admin)
    assert u["dana"]["status"] == "active" and u["eli"]["status"] == "active"


def test_C3_renamed_ad_user_stays_active(ad_env, app, admin):
    """Same objectSid, new sAMAccountName (a name change). The person is still in the group and must stay active."""
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"])}))
    run_sync(fake_ad({"dana.smith": (1101, False)}, {"AB-Platform": (2001, ["dana.smith"])}))
    u = users(admin)
    person = [x for x in u.values() if x["uid_number"] == 100000 + 1101]
    assert len(person) == 1
    assert person[0]["status"] == "active", f"renamed person is {person[0]['status']} (username {person[0]['username']})"


def test_C3_new_person_reusing_a_name_does_not_inherit_old_home(ad_env, app, admin):
    """dana (SID ...-1101) leaves; months later a different dana (SID ...-1200) joins. The new person must not get the old
    person's account, home folder and files."""
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"])}))
    save(admin, "/users/dana/inbox/old-dana-private.txt", "old")
    run_sync(fake_ad({}, {"AB-Platform": (2001, [])}))
    assert users(admin)["dana"]["status"] == "suspended"
    run_sync(fake_ad({"dana": (1200, False)}, {"AB-Platform": (2001, ["dana"])}))
    u = users(admin)
    # Either a separate account, or the old one left alone; never the old account handed to the new SID.
    old = [x for x in u.values() if x["home"] == "/users/dana"][0]
    assert not (old["status"] == "active" and old["uid_number"] == 100000 + 1200), \
        "new AD person with a reused name took over the old person's account and files"


def test_C3_ad_name_matching_local_account_does_not_take_it_over(ad_env, app, admin):
    """An AD account whose sAMAccountName equals a local account (here the bootstrap 'admin') must not convert the
    local account into an AD one and strip its admin role."""
    run_sync(fake_ad({"admin": (1500, False)}, {"AB-Platform": (2001, ["admin"])}))
    from app import db as dbm
    from app.db import User
    with dbm.new_session() as s:
        me = s.scalar(select(User).where(User.username == "admin"))
        got = (me.source, me.system_role, me.ad_sid)
    assert got[0] == "local" and got[1] == "admin", f"bootstrap admin is now source={got[0]} role={got[1]} sid={got[2]}"
    assert admin.get("/api/users").status_code == 200


def test_C3_admin_suspension_survives_ad_disable_enable(ad_env, app, admin):
    """Admin suspends by hand. Later AD disables and re-enables the account. Admin's suspension must stand."""
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"])}))
    admin.patch(f"/api/users/{uid_of(admin, 'dana')}", json={"status": "suspended"})
    run_sync(fake_ad({"dana": (1101, True)}, {"AB-Platform": (2001, ["dana"])}))
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"])}))
    assert users(admin)["dana"]["status"] == "suspended"


def test_C3_admin_suspension_after_manual_reactivation(ad_env, app, admin):
    """Sync suspends (disabled). Admin reactivates by hand, later suspends by hand. Next sync must not undo that."""
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"])}))
    run_sync(fake_ad({"dana": (1101, True)}, {"AB-Platform": (2001, ["dana"])}))
    uid = uid_of(admin, "dana")
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"])}))  # back -> active
    admin.patch(f"/api/users/{uid}", json={"status": "suspended"})
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"])}))
    assert users(admin)["dana"]["status"] == "suspended"


def test_C3_admin_suspends_while_sync_flag_set(ad_env, app, admin):
    """Removed from all groups -> sync suspends. Admin reactivates by hand (person needs access), then suspends by hand.
    Person is re-added in AD. The hand suspension must stand."""
    run_sync(fake_ad({"dana": (1101, False), "eli": (1102, False)}, {"AB-Platform": (2001, ["dana", "eli"])}))
    run_sync(fake_ad({"dana": (1101, False), "eli": (1102, False)}, {"AB-Platform": (2001, ["eli"])}))
    uid = uid_of(admin, "dana")
    admin.patch(f"/api/users/{uid}", json={"status": "active"})
    admin.patch(f"/api/users/{uid}", json={"status": "suspended"})
    run_sync(fake_ad({"dana": (1101, False), "eli": (1102, False)}, {"AB-Platform": (2001, ["dana", "eli"])}))
    assert users(admin)["dana"]["status"] == "suspended"


def test_C3_group_renamed_in_ad_keeps_one_group(ad_env, app, admin):
    """AB-Eng renamed to AB-Engineering (same objectSid). Removing eli afterwards in AD must remove his access to the
    team folder; the old app group must not keep stale members."""
    run_sync(fake_ad({"dana": (1101, False), "eli": (1102, False)}, {"AB-Eng": (2001, ["dana", "eli"])}))
    save(admin, "/shared/eng/projects/plan.md", "p")
    run_sync(fake_ad({"dana": (1101, False), "eli": (1102, False)}, {"AB-Engineering": (2001, ["dana"]), "AB-Other": (2002, ["eli"])}))
    g = users(admin)["eli"]["groups"]
    assert "eng" not in g, f"eli is still in the old app group 'eng' after being removed in AD: {g}"


def test_C3_group_deleted_in_ad_loses_members(ad_env, app, admin):
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Old": (2001, ["dana"]), "AB-Platform": (2002, ["dana"])}))
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Platform": (2002, ["dana"])}))
    assert "old" not in users(admin)["dana"]["groups"]


def test_C3_nested_group_members_observation(ad_env, app, admin):
    """Observation (spec is silent): members of a group nested inside an AB- group."""
    run_sync(fake_ad({"dana": (1101, False), "eli": (1102, False)},
                     {"AB-Platform": (2001, ["dana", "group:Contractors"]), "Contractors": (2002, ["eli"])}))
    u = users(admin)
    assert "dana" in u
    # recorded in the report; eli is currently ignored
    assert "eli" not in u or True


# ---- C4 -----------------------------------------------------------------------------
def test_C4_ad_groups_are_read_only(ad_env, app, admin, make_user):
    run_sync(fake_ad({"dana": (1101, False)}, {"AB-Platform": (2001, ["dana"])}))
    make_user("zed")
    gid = groups(admin)["platform"]["id"]
    assert admin.post(f"/api/groups/{gid}/members", json={"username": "zed"}).status_code == 400
    assert admin.delete(f"/api/groups/{gid}/members/dana").status_code == 400
    assert admin.post("/api/users", json={"username": "yan", "password": PW, "groups": ["platform"]}).status_code == 400
    assert "yan" not in users(admin)


# ---- C5 -----------------------------------------------------------------------------
def test_C5_map_extra_group_rest(ad_env, app, admin):
    assert admin.post("/api/directory/map", json={"ad_group": "Finance"}).status_code == 200
    run_sync(fake_ad({"dana": (1101, False)}, {"Finance": (2100, ["dana"])}))
    assert "finance" in groups(admin)


# ---- C6 -----------------------------------------------------------------------------
def test_C6_suspension_ends_everything(app, admin, make_user):
    alice = make_user("alice")
    pat = alice.post("/api/tokens", json={"name": "ci"}).json()["token"]
    oa = oauth_sign_in(app, alice)
    admin.patch(f"/api/users/{uid_of(admin, 'alice')}", json={"status": "suspended"})
    assert alice.get("/api/fs/list").status_code == 401
    assert bearer(app, pat).get("/api/fs/list").status_code == 401
    c = TestClient(app)
    assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": f"Bearer {oa['access_token']}"}).status_code == 401
    r = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": oa["refresh_token"], "client_id": oa["client_id"]})
    assert r.status_code == 400
    # reactivating does not resurrect old credentials
    admin.patch(f"/api/users/{uid_of(admin, 'alice')}", json={"status": "active"})
    assert bearer(app, pat).get("/api/fs/list").status_code == 401
