"""Claude Code sign-in (OAuth 2.1 + PKCE) and the MCP tools."""
from fastapi.testclient import TestClient

from conftest import MCP, oauth_sign_in, pkce


def test_metadata(app):
    c = TestClient(app)
    pr = c.get("/.well-known/oauth-protected-resource").json()
    assert pr["resource"].endswith("/mcp") and pr["authorization_servers"]
    asm = c.get("/.well-known/oauth-authorization-server").json()
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["registration_endpoint"].endswith("/oauth/register")


def test_mcp_requires_token(app):
    c = TestClient(app)
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    assert c.get("/mcp").status_code == 405
    r = c.post("/mcp", json={}, headers={"Authorization": "Bearer ab_at_nope"})
    assert r.status_code == 401


def test_mcp_rejects_foreign_origin(app, make_user):
    alice = make_user("alice")
    t = oauth_sign_in(app, alice)
    r = TestClient(app).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             headers={"Authorization": f"Bearer {t['access_token']}", "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_full_sign_in_and_save(app, make_user):
    alice = make_user("alice")
    t = oauth_sign_in(app, alice)
    assert t["access_token"].startswith("ab_at_") and t["refresh_token"].startswith("ab_rt_")
    m = MCP(app, t["access_token"])
    init = m.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert init["result"]["serverInfo"]["name"] == "artifact-buckets"
    tools = m.tools()
    assert "save_artifact" in tools and "list_users" not in tools and "audit_search" not in tools
    res = m.ok("save_artifact", name="report.md", content="# hi", project="Billing API")
    assert res["path"] == "/users/alice/projects/billing-api/report.md" and res["routed_by"] == "project"
    res = m.ok("save_artifact", name="loose.txt", content="x")
    assert res["path"] == "/users/alice/inbox/loose.txt"
    got = m.ok("get_artifact", path="/users/alice/projects/billing-api/report.md")
    assert got["text"] == "# hi"
    m.ok("edit_file", path="/users/alice/projects/billing-api/report.md", old_text="hi", new_text="hello")
    assert len(m.ok("list_versions", path="/users/alice/projects/billing-api/report.md")["versions"]) == 2
    m.ok("rename", path="/users/alice/inbox/loose.txt", new_name="kept.txt")
    m.ok("delete", path="/users/alice/inbox/kept.txt")
    items = m.ok("list_trash")["items"]
    m.ok("restore_from_trash", id=items[0]["id"])
    assert m.ok("get_artifact", path="/users/alice/inbox/kept.txt")["text"] == "x"
    # Every save is in the audit log with the device name.
    from app import db as dbm, mcp
    with dbm.new_session() as s:
        ev = mcp.audit_query(s, action="file.save", user="alice")
    assert ev and ev[0]["via"] == "mcp" and "laptop" in ev[0]["detail"]["token"]


def test_mcp_errors_and_denials(app, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    m = MCP(app, oauth_sign_in(app, bob)["access_token"])
    r = m.call("save_artifact", path="/users/alice/inbox/x.txt", content="no")
    assert r["isError"] and ("not_found" in r["content"][0]["text"] or "forbidden" in r["content"][0]["text"])
    r = m.call("list_users")
    assert r["isError"]
    r = m.call("save_artifact", name="x.txt")
    assert r["isError"]
    bad = m.rpc("tools/call", {"name": "nope", "arguments": {}})
    assert "error" in bad
    r = m.call("save_artifact", name="../x.txt", content="a")
    assert r["isError"]


def test_admin_tools_need_sign_in_not_pat(app, admin, make_user):
    make_user("alice")
    t = oauth_sign_in(app, admin)
    m = MCP(app, t["access_token"])
    tools = m.tools()
    assert {"list_users", "suspend_user", "audit_search", "storage_status"} <= set(tools)
    users = m.ok("list_users")["users"]
    assert any(u["username"] == "alice" for u in users)
    pat = admin.post("/api/tokens", json={"name": "ci"}).json()["token"]
    assert "list_users" not in MCP(app, pat).tools()


def test_suspend_revokes_devices(app, admin, make_user):
    alice = make_user("alice")
    t = oauth_sign_in(app, alice)
    MCP(app, t["access_token"]).ok("list_folder")
    am = MCP(app, oauth_sign_in(app, admin)["access_token"])
    am.ok("suspend_user", username="alice", suspend=True)
    r = TestClient(app).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             headers={"Authorization": f"Bearer {t['access_token']}"})
    assert r.status_code == 401


def test_refresh_rotation_and_reuse(app, make_user):
    alice = make_user("alice")
    t = oauth_sign_in(app, alice)
    c = TestClient(app)
    r1 = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": t["client_id"]})
    assert r1.status_code == 200
    new = r1.json()
    # The old access token stops working after rotation; the new one works.
    assert MCP(app, new["access_token"]).rpc("ping")["result"] == {}
    # Reusing the old refresh token is treated as theft: the whole sign-in is revoked.
    r2 = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": t["client_id"]})
    assert r2.status_code == 400
    r3 = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": f"Bearer {new['access_token']}"})
    assert r3.status_code == 401


def test_pkce_and_code_rules(app, make_user):
    alice = make_user("alice")
    c = TestClient(app, follow_redirects=False)
    redirect = "http://127.0.0.1:5000/cb"
    cid = c.post("/oauth/register", json={"redirect_uris": [redirect]}).json()["client_id"]
    verifier, challenge = pkce()
    q = {"client_id": cid, "redirect_uri": redirect, "response_type": "code", "code_challenge": challenge,
         "code_challenge_method": "S256", "state": "s"}
    # Not signed in -> sent to the login page, which comes back here.
    r = c.get("/oauth/authorize", params=q)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=")
    # Loopback redirect may use another port; a different host may not.
    r = alice.get("/oauth/authorize", params={**q, "redirect_uri": "http://127.0.0.1:6000/cb"})
    assert r.status_code == 302 and r.headers["location"].startswith("/consent")
    r = alice.get("/oauth/authorize", params={**q, "redirect_uri": "https://evil.example/cb"})
    assert r.status_code == 400
    # plain PKCE is refused
    r = alice.get("/oauth/authorize", params={**q, "code_challenge_method": "plain"})
    assert "error=invalid_request" in r.headers["location"]
    # Approve without the CSRF value is refused.
    r = alice.c.post("/oauth/approve", data={**q, "decision": "allow", "csrf": "wrong"})
    assert r.status_code == 403
    r = alice.c.post("/oauth/approve", data={**q, "decision": "allow", "csrf": alice.csrf})
    code = r.headers["location"].split("code=")[1].split("&")[0]
    bad = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                       "redirect_uri": redirect, "code_verifier": "wrong" * 10})
    assert bad.status_code == 400
    good = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                        "redirect_uri": redirect, "code_verifier": verifier})
    # The failed attempt doesn't burn the code, but a second exchange is refused.
    assert good.status_code == 200
    again = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                         "redirect_uri": redirect, "code_verifier": verifier})
    assert again.status_code == 400
    # Deny comes back as access_denied.
    r = alice.c.post("/oauth/approve", data={**q, "decision": "deny", "csrf": alice.csrf})
    assert "error=access_denied" in r.headers["location"]


def test_register_rejects_bad_redirects(app):
    c = TestClient(app)
    for uris in (["http://evil.example/cb"], ["javascript:alert(1)"], [], ["https://x.example/cb#frag"]):
        assert c.post("/oauth/register", json={"redirect_uris": uris}).status_code == 400


def test_devices_listed_and_revoked_from_web(app, make_user):
    alice = make_user("alice")
    t = oauth_sign_in(app, alice)
    toks = alice.get("/api/tokens").json()["tokens"]
    dev = [x for x in toks if x["kind"] == "device"]
    assert dev and dev[0]["name"] == "laptop"
    assert alice.delete(f"/api/tokens/{dev[0]['id']}").status_code == 200
    r = TestClient(app).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             headers={"Authorization": f"Bearer {t['access_token']}"})
    assert r.status_code == 401


def test_read_scope_hides_write_tools(app, make_user):
    alice = make_user("alice")
    tok = alice.post("/api/tokens", json={"name": "ro", "scope": "read"}).json()["token"]
    tools = MCP(app, tok).tools()
    assert "get_artifact" in tools and "save_artifact" not in tools and "delete" not in tools


def test_routing_rules(app, admin, make_user, make_group):
    alice = make_user("alice")
    make_group("platform", ["alice"])
    r = admin.post("/api/routing", json={"match": "git_remote", "pattern": "git.lab.local/platform/*",
                                          "target": "/shared/platform/projects/{project}"})
    assert r.status_code == 200, r.text
    m = MCP(app, oauth_sign_in(app, alice)["access_token"])
    res = m.ok("save_artifact", name="d.html", content="x", project="ingest", git_remote="git@git.lab.local:platform/ingest.git")
    assert res["path"] == "/shared/platform/projects/ingest/d.html" and res["routed_by"].startswith("rule")
    # Someone who can't write to the rule's target falls back to their own projects folder.
    bob = make_user("bob")
    mb = MCP(app, oauth_sign_in(app, bob)["access_token"])
    res = mb.ok("save_artifact", name="d.html", content="x", project="ingest", git_remote="https://git.lab.local/platform/ingest")
    assert res["path"] == "/users/bob/projects/ingest/d.html"
    # A member can't add rules for a team folder they don't own.
    assert alice.post("/api/routing", json={"match": "project", "pattern": "*", "target": "/shared/platform"}).status_code == 403
    prev = admin.post("/api/routing/preview", json={"git_remote": "git@git.lab.local:platform/x.git", "project": "x"})
    assert prev.status_code == 200


def test_ingest_pause(app, admin, make_user):
    alice = make_user("alice")
    m = MCP(app, oauth_sign_in(app, alice)["access_token"])
    assert admin.put("/api/ingest/settings", json={"paused": True}).status_code == 200
    r = m.call("save_artifact", name="x.txt", content="x")
    assert r["isError"] and "paused" in r["content"][0]["text"].lower()
    # Web uploads still work while Claude Code saves are paused.
    assert alice.post("/api/fs/save-text", json={"path": "/users/alice/inbox/w.txt", "content": "w"}).status_code == 200
    admin.put("/api/ingest/settings", json={"paused": False})
    m.ok("save_artifact", name="x.txt", content="x")


def test_large_folder_delete_needs_web_confirm(app, make_user, monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "mcp_delete_confirm_threshold", 3)
    alice = make_user("alice")
    for i in range(5):
        alice.post("/api/fs/save-text", json={"path": f"/users/alice/projects/big/{i}.txt", "content": "x"})
    m = MCP(app, oauth_sign_in(app, alice)["access_token"])
    r = m.call("delete", path="/users/alice/projects/big")
    assert r["isError"] and "confirm" in r["content"][0]["text"].lower()
    # The web app (which shows its own confirmation) can delete it.
    assert alice.post("/api/fs/delete", json={"path": "/users/alice/projects/big", "confirm": True}).status_code == 200
