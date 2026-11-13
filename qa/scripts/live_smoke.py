"""Re-checks the top findings against a running server (default http://127.0.0.1:8099).
Usage: python live_smoke.py [base_url] [bootstrap_password]"""
import base64, hashlib, secrets, sys, threading, time
from urllib.parse import parse_qs, urlparse
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8099"
APW = sys.argv[2] if len(sys.argv) > 2 else "qa-bootstrap-pass-123"
PW = "a-long-enough-password"


def login(u, p):
    c = httpx.Client(base_url=BASE, follow_redirects=False)
    r = c.post("/api/auth/login", json={"username": u, "password": p}); r.raise_for_status()
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


def oauth(web):
    c = httpx.Client(base_url=BASE, follow_redirects=False)
    redir = "http://127.0.0.1:33418/callback"
    cid = c.post("/oauth/register", json={"redirect_uris": [redir]}).json()["client_id"]
    v = secrets.token_urlsafe(48)
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": cid, "redirect_uri": redir, "response_type": "code", "code_challenge": ch, "code_challenge_method": "S256"}
    r = web.post("/oauth/approve", data={**q, "decision": "allow", "csrf": web.headers["X-CSRF-Token"]})
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    return c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid, "redirect_uri": redir, "code_verifier": v}).json()["access_token"]


def mcp(tok, body, raw=None):
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    return httpx.post(BASE + "/mcp", content=raw, headers=h) if raw is not None else httpx.post(BASE + "/mcp", json=body, headers=h)


admin = login("admin", APW)
tag = secrets.token_hex(3)
alice_u, bob_u = f"alice{tag}", f"bob{tag}"
for u in (alice_u, bob_u):
    admin.post("/api/users", json={"username": u, "password": PW}).raise_for_status()
alice, bob = login(alice_u, PW), login(bob_u, PW)
alice.post("/api/fs/save-text", json={"path": f"/users/{alice_u}/inbox/diary.txt", "content": "alice-private"}).raise_for_status()

# 1. admin personal token reads a private home
pat = admin.post("/api/tokens", json={"name": "build"}).json()["token"]
r = httpx.get(BASE + "/api/fs/download", params={"path": f"/users/{alice_u}/inbox/diary.txt"}, headers={"Authorization": f"Bearer {pat}"})
print("[admin PAT reads private home]", r.status_code, r.text[:30])
r = httpx.get(BASE + "/api/audit", headers={"Authorization": f"Bearer {pat}"})
print("[admin PAT reads audit log]", r.status_code)

# 2. member routing rule into own home captures admin's Claude Code saves
r = bob.post("/api/routing", json={"match": "project", "pattern": "*", "target": f"/users/{bob_u}/inbox/loot", "priority": -1000})
print("[member adds rule to own home]", r.status_code)
at = oauth(admin)
res = mcp(at, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "save_artifact", "arguments": {"name": f"minutes-{tag}.md", "content": "board-secret", "project": "board"}}}).json()
print("[admin save routed to]", res["result"]["structuredContent"]["path"])
print("[bob reads it]", bob.get("/api/fs/download", params={"path": f"/users/{bob_u}/inbox/loot/minutes-{tag}.md"}).text)
bob.delete(f"/api/routing/{r.json()['id']}")

# 3. MCP malformed params -> 500
r = mcp(at, None, raw=b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":[1]}')
print("[MCP params as list]", r.status_code)

# 4. bootstrap admin moves a home folder
r = admin.post("/api/fs/move", json={"src": f"/users/{alice_u}", "dst": f"/users/{alice_u}-moved"})
print("[admin moves a home folder]", r.status_code)
admin.post("/api/fs/move", json={"src": f"/users/{alice_u}-moved", "dst": f"/users/{alice_u}"})

