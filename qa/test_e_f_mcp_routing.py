"""E. MCP server, F. Routing rules."""
import base64
import os

import pytest
from fastapi.testclient import TestClient

from conftest import MCP, PW, audit_events, bearer, oauth_sign_in, save


def mcp_for(app, web):
    return MCP(app, oauth_sign_in(app, web)["access_token"])


def raw_post(app, token, body, headers=None, content=None):
    c = TestClient(app, raise_server_exceptions=False)
    h = {"Authorization": f"Bearer {token}", **(headers or {})}
    if content is not None:
        return c.post("/mcp", content=content, headers={**h, "Content-Type": "application/json"})
    return c.post("/mcp", json=body, headers=h)


# ---- E1 ----------------------------------------------------------------------------------
def test_E1_transport(app, make_user):
    alice = make_user("alice")
    tok = oauth_sign_in(app, alice)["access_token"]
    c = TestClient(app)
    assert c.get("/mcp").status_code == 405 and c.delete("/mcp").status_code == 405
    r = raw_post(app, tok, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.json()["result"]["protocolVersion"] == "2025-06-18"
    assert raw_post(app, tok, {"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    assert raw_post(app, tok, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Origin": "https://evil.example"}).status_code == 403
    assert raw_post(app, tok, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Origin": "http://testserver"}).status_code == 200
    batch = raw_post(app, tok, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "ping"}])
    assert [x["id"] for x in batch.json()] == [1, 2]


# ---- E2 ----------------------------------------------------------------------------------
def test_E2_tools_by_role(app, admin, make_user):
    alice, aud = make_user("alice"), make_user("audrey", role="auditor")
    t_member = set(mcp_for(app, alice).tools())
    t_admin = set(mcp_for(app, admin).tools())
    t_aud = set(mcp_for(app, aud).tools())
    assert "save_artifact" in t_member and "list_users" not in t_member and "audit_search" not in t_member
    assert {"list_users", "suspend_user", "drain_volume", "audit_search"} <= t_admin
    assert "audit_search" in t_aud and "list_users" not in t_aud
    ro = alice.post("/api/tokens", json={"name": "ro", "scope": "read"}).json()["token"]
    m = MCP(app, ro)
    assert all(t in ("get_artifact", "search_artifacts", "list_folder", "list_writable_folders", "list_versions",
                     "list_trash", "who_has_access") for t in m.tools())
    assert m.call("save_artifact", name="x.txt", content="x")["isError"]
    # admin's PAT: no admin tools, and calling one directly fails
    pat = admin.post("/api/tokens", json={"name": "b"}).json()["token"]
    mp = MCP(app, pat)
    assert "list_users" not in mp.tools() and mp.call("list_users")["isError"]


# ---- E3 ----------------------------------------------------------------------------------
def test_E3_save_routing_order(app, admin, make_user, make_group):
    alice = make_user("alice")
    make_group("eng", ["alice"])
    m = mcp_for(app, alice)
    r = m.ok("save_artifact", name="a.md", content="a", folder="/shared/eng/projects")
    assert r["path"] == "/shared/eng/projects/a.md" and r["routed_by"] == "named"
    assert admin.post("/api/routing", json={"match": "git_remote", "pattern": "git.lab/eng/*", "target": "/shared/eng/projects/{project}"}).status_code == 200
    r = m.ok("save_artifact", name="b.md", content="b", project="Svc One", git_remote="git@git.lab:eng/svc.git")
    assert r["path"] == "/shared/eng/projects/svc-one/b.md" and r["routed_by"].startswith("rule")
    r = m.ok("save_artifact", name="c.md", content="c", project="mine")
    assert r["path"] == "/users/alice/projects/mine/c.md" and r["routed_by"] == "project"
    r = m.ok("save_artifact", name="d.md", content="d")
    assert r["path"] == "/users/alice/inbox/d.md" and r["routed_by"] == "inbox"
    r = m.ok("save_artifact", path="/users/alice/inbox/e.bin", content_base64=base64.b64encode(b"\x00\x01").decode())
    assert r["size"] == 2
    assert m.call("save_artifact", name="../x", content="x")["isError"]
    assert m.call("save_artifact", name="x", content="x", content_base64="eA==")["isError"]
    assert m.call("save_artifact", name="x", content_base64="!!!")["isError"]


# ---- E4 ----------------------------------------------------------------------------------
def test_E4_file_tools(app, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    m = mcp_for(app, alice)
    m.ok("create_folder", path="/users/alice/projects/p")
    m.ok("save_artifact", path="/users/alice/projects/p/f.md", content="hello world", tags=["t1"], project="pr")
    assert m.ok("get_artifact", path="/users/alice/projects/p/f.md")["text"] == "hello world"
    assert m.ok("search_artifacts", query="f.md")["results"][0]["name"] == "f.md"
    assert m.ok("list_folder", path="/users/alice/projects/p")["files"][0]["name"] == "f.md"
    assert any(f["path"] == "/users/alice/inbox" for f in m.ok("list_writable_folders")["folders"])
    assert m.call("edit_file", path="/users/alice/projects/p/f.md", old_text="o", new_text="0")["isError"]  # two matches
    m.ok("edit_file", path="/users/alice/projects/p/f.md", old_text="world", new_text="there")
    assert len(m.ok("list_versions", path="/users/alice/projects/p/f.md")["versions"]) == 2
    m.ok("restore_version", path="/users/alice/projects/p/f.md", version=1)
    assert m.ok("get_artifact", path="/users/alice/projects/p/f.md")["text"] == "hello world"
    m.ok("rename", path="/users/alice/projects/p/f.md", new_name="g.md")
    m.ok("move", **{"from": "/users/alice/projects/p/g.md", "to": "/users/alice/inbox/g.md"})
    m.ok("share_folder", path="/users/alice/projects/p", user="bob", role="viewer")
    assert any(a["principal"]["name"] == "bob" for a in m.ok("who_has_access", path="/users/alice/projects/p")["access"])
    m.ok("unshare_folder", path="/users/alice/projects/p", user="bob")
    d = m.ok("delete", path="/users/alice/inbox/g.md")
    assert m.ok("list_trash")["items"][0]["id"] == d["trash_id"]
    m.ok("restore_from_trash", id=d["trash_id"])
    assert m.ok("get_artifact", path="/users/alice/inbox/g.md")["text"] == "hello world"


def test_E4_mcp_unshare_home_owner_refused(app, make_user):
    alice = make_user("alice")
    m = mcp_for(app, alice)
    assert m.call("unshare_folder", path="/users/alice", user="alice")["isError"]


def test_E4_mcp_bob_cannot_touch_alice(app, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    save(alice, "/users/alice/inbox/s.md", "secret")
    m = mcp_for(app, bob)
    for tool, args in [("get_artifact", {"path": "/users/alice/inbox/s.md"}), ("list_folder", {"path": "/users/alice"}),
                       ("list_versions", {"path": "/users/alice/inbox/s.md"}), ("delete", {"path": "/users/alice/inbox/s.md"}),
                       ("move", {"from": "/users/alice/inbox/s.md", "to": "/users/bob/inbox/s.md"}),
                       ("rename", {"path": "/users/alice/inbox/s.md", "new_name": "t.md"}),
                       ("edit_file", {"path": "/users/alice/inbox/s.md", "old_text": "secret", "new_text": "x"}),
                       ("who_has_access", {"path": "/users/alice/inbox"}),
                       ("share_folder", {"path": "/users/alice", "user": "bob", "role": "owner"}),
                       ("save_artifact", {"path": "/users/alice/inbox/planted.md", "content": "x"}),
                       ("create_folder", {"path": "/users/alice/inbox/x"})]:
        r = m.call(tool, **args)
        assert r["isError"], tool
        assert "secret" not in r["content"][0]["text"]
    assert m.ok("search_artifacts", query="s.md")["results"] == []


# ---- E5 ----------------------------------------------------------------------------------
def test_E5_admin_tools(app, admin, make_user, make_group, env):
    make_user("alice")
    make_group("eng")
    m = mcp_for(app, admin)
    assert any(u["username"] == "alice" for u in m.ok("list_users", query="ali")["users"])
    m.ok("set_quota", username="alice", gigabytes=1)
    m.ok("set_quota", folder="/shared/eng", gigabytes=2)
    assert m.call("set_quota", folder="/shared/eng/projects", gigabytes=2)["isError"]
    m.ok("grant_folder", path="/shared/eng", user="alice", role="viewer")
    m.ok("add_routing_rule", match="project", pattern="x*", target="/shared/eng/projects/{project}")
    assert m.ok("list_routing_rules")["rules"]
    assert "volumes" in m.ok("storage_status")
    m.ok("map_ad_group", ad_group="Finance")
    assert m.call("sync_directory")["isError"]  # no AD configured here: readable error
    from app import config
    os.makedirs(os.path.join(config.settings.volumes_root, "d1"))
    os.makedirs(os.path.join(config.settings.volumes_root, "d2"))
    m.ok("add_volume_to_pool", volume="d1")
    assert m.call("drain_volume", volume="d1")["isError"]
    m.ok("add_volume_to_pool", volume="d2")
    m.ok("drain_volume", volume="d1")
    devs = m.ok("list_devices")["devices"]
    assert devs
    m.ok("suspend_user", username="alice", suspend=True)
    m.ok("suspend_user", username="alice", suspend=False)
    assert m.call("suspend_user", username="admin", suspend=True)["isError"]
    assert m.ok("audit_search", action="user.*")["events"]


def test_E5_set_quota_zero_or_negative_refused(app, admin, make_group):
    make_group("eng")
    m = mcp_for(app, admin)
    for g in (0, -1):
        assert m.call("set_quota", folder="/shared/eng", gigabytes=g)["isError"], g
        assert m.call("set_quota", username="admin", gigabytes=g)["isError"], g


# ---- E6 ----------------------------------------------------------------------------------
def test_E6_errors(app, make_user):
    alice = make_user("alice")
    tok = oauth_sign_in(app, alice)["access_token"]
    j = raw_post(app, tok, {"jsonrpc": "2.0", "id": 1, "method": "nope"}).json()
    assert j["error"]["code"] == -32601
    j = raw_post(app, tok, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope"}}).json()
    assert j["error"]["code"] == -32602
    r = raw_post(app, tok, None, content=b"{not json")
    assert r.json()["error"]["code"] == -32700
    j = raw_post(app, tok, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "get_artifact", "arguments": {"path": 5}}}).json()
    assert j["result"]["isError"]
    j = raw_post(app, tok, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "get_artifact", "arguments": {}}}).json()
    assert j["result"]["isError"] and "Missing" in j["result"]["content"][0]["text"]


@pytest.mark.parametrize("payload", [
    b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":[1,2]}',
    b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":"x"}',
    b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_artifact","arguments":{"path":"/users/alice/inbox/\\u0000"}}}',
    b'\xff\xfe\x00{',
])
def test_E6_malformed_requests_get_jsonrpc_errors_not_500(app, make_user, payload):
    alice = make_user("alice")
    tok = oauth_sign_in(app, alice)["access_token"]
    r = raw_post(app, tok, None, content=payload)
    assert r.status_code != 500, (payload, r.status_code)
    j = r.json()
    assert "error" in j or j.get("result", {}).get("isError") is True


@pytest.mark.parametrize("tool,args", [
    ("restore_version", {"path": "/users/alice/inbox/f.txt", "version": 10**20}),
    ("revoke_device", {"token_id": 10**20}),
    ("set_quota", {"username": "alice", "gigabytes": 1e300}),
])
def test_E6_out_of_range_numbers_are_tool_errors(app, admin, make_user, tool, args):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/f.txt", "1")
    save(alice, "/users/alice/inbox/f.txt", "2")
    who = admin if tool in ("revoke_device", "set_quota") else alice
    tok = oauth_sign_in(app, who)["access_token"]
    r = raw_post(app, tok, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    assert r.status_code == 200, (tool, r.status_code)
    assert r.json()["result"]["isError"]


# ---- E7 ----------------------------------------------------------------------------------
def test_E7_pause_blocks_every_mcp_write_of_content(app, admin, make_user):
    alice = make_user("alice")
    save(alice, "/users/alice/inbox/f.txt", "one")
    save(alice, "/users/alice/inbox/f.txt", "two")
    m = mcp_for(app, alice)
    admin.put("/api/ingest/settings", json={"paused": True})
    assert m.call("save_artifact", name="x.txt", content="x")["isError"]
    assert m.call("edit_file", path="/users/alice/inbox/f.txt", old_text="two", new_text="three")["isError"]
    assert m.call("restore_version", path="/users/alice/inbox/f.txt", version=1)["isError"]
    assert alice.post("/api/fs/save-text", json={"path": "/users/alice/inbox/w.txt", "content": "w"}).status_code == 200
    ev = [e for e in audit_events(admin, action="file.save") if not e["ok"]]
    assert any(e["detail"].get("reason") == "ingest paused" for e in ev)
    assert alice.put("/api/ingest/settings", json={"paused": False}).status_code == 403


# ---- F1 F2 F3 ----------------------------------------------------------------------------
@pytest.mark.parametrize("remote", ["git@git.lab:Eng/Svc.git", "https://git.lab/eng/svc", "https://git.lab/eng/svc.git",
                                    "ssh://git@git.lab/eng/svc.git", "https://user:pw@git.lab/eng/svc", "git.lab:eng/svc",
                                    "ssh://git@git.lab:22/eng/svc.git", "https://git.lab/eng/svc.git/"])
def test_F1_remote_normalisation(remote):
    from app.routing import normalize_remote
    assert normalize_remote(remote) == "git.lab/eng/svc", (remote, normalize_remote(remote))


def test_F1_priority_glob_and_placeholders(app, admin, make_user, make_group):
    alice = make_user("alice")
    make_group("eng", ["alice"])
    admin.post("/api/routing", json={"match": "project", "pattern": "web-*", "target": "/shared/eng/projects/web/{project}", "priority": 50})
    admin.post("/api/routing", json={"match": "project", "pattern": "*", "target": "/shared/eng/projects/{user}", "priority": 10})
    r = admin.post("/api/routing/preview", json={"project": "web-app"})
    assert r.json()["folder"] == "/shared/eng/projects/admin"
    r = alice.post("/api/routing/preview", json={"project": "web-app"})
    assert r.json()["folder"] == "/shared/eng/projects/alice"


def test_F2_who_can_add_rules(app, admin, make_user, make_group):
    alice, bob = make_user("alice"), make_user("bob")
    make_group("eng", ["alice", "bob"])
    admin.post("/api/fs/share", json={"path": "/shared/eng", "kind": "user", "name": "alice", "role": "owner"})
    assert alice.post("/api/routing", json={"match": "project", "pattern": "x", "target": "/shared/eng/projects/{project}"}).status_code == 200
    assert bob.post("/api/routing", json={"match": "project", "pattern": "x", "target": "/shared/eng/projects/{project}"}).status_code == 403
    rid = admin.get("/api/routing").json()["rules"][0]["id"]
    assert bob.delete(f"/api/routing/{rid}").status_code == 403
    assert alice.delete(f"/api/routing/{rid}").status_code == 200


def test_F2_member_cannot_route_into_own_home(app, admin, make_user):
    """Only admins or Owners of a *team* folder may add rules. A member who targets their own home with pattern '*'
    captures every admin's Claude Code saves (admins can write anywhere)."""
    bob = make_user("bob")
    r = bob.post("/api/routing", json={"match": "project", "pattern": "*", "target": "/users/bob/inbox/loot", "priority": -1000})
    m = mcp_for(app, admin)
    res = m.ok("save_artifact", name="board-minutes.md", content="confidential", project="board")
    assert r.status_code == 403, f"member rule accepted; admin's save landed at {res['path']}"


def test_F3_preview(app, make_user):
    alice = make_user("alice")
    r = alice.post("/api/routing/preview", json={"project": "My Proj"})
    assert r.json() == {"folder": "/users/alice/projects/my-proj", "reason": "project"}
