"""Fill a running Artifact Buckets with a believable demo team, through the public API and MCP.

  python scripts/seed_demo.py http://localhost:8080 <admin-password>

Creates people and groups, shares folders, and saves files the way Claude Code would.
Every demo account gets the password DEMO_PASSWORD below.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import httpx

DEMO_PASSWORD = "demo-password-123"
BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8080"
ADMIN_PW = sys.argv[2] if len(sys.argv) > 2 else "change-me-now-please"
# Set AB_SEED_INSECURE=1 to accept a test certificate (Caddy's internal CA).
import os
VERIFY = os.environ.get("AB_SEED_INSECURE") != "1"

PEOPLE = [("maya", "Maya Okafor", "member"), ("jonas", "Jonas Lindqvist", "member"), ("priya", "Priya Raman", "member"),
          ("sam", "Sam Whitfield", "member"), ("lena", "Lena Hartmann", "auditor"), ("diego", "Diego Alvarez", "member")]
GROUPS = [("platform-eng", "Platform engineering", ["maya", "jonas", "sam"]), ("design", "Product design", ["priya", "diego"]),
          ("research", "User research", ["priya", "jonas"]), ("finance", "Finance and planning", ["diego"])]

HTML = """<!doctype html><html><head><meta charset="utf-8"><title>{t}</title><style>body{{font:14px system-ui;margin:0;background:#0f172a;color:#e2e8f0}}
.w{{padding:22px}}h1{{font-size:18px;margin:0 0 14px}}.g{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.c{{background:#1e293b;border-radius:10px;padding:12px}}.v{{font-size:24px;font-weight:600}}.l{{color:#94a3b8;font-size:12px}}
.bar{{height:70px;display:flex;align-items:flex-end;gap:4px;margin-top:14px}}.bar i{{flex:1;background:#38bdf8;border-radius:3px 3px 0 0}}</style></head>
<body><div class="w"><h1>{t}</h1><div class="g"><div class="c"><div class="v">{a}</div><div class="l">p50 ms</div></div>
<div class="c"><div class="v">{b}</div><div class="l">p95 ms</div></div><div class="c"><div class="v">{c}</div><div class="l">p99 ms</div></div></div>
<div class="bar">{bars}</div></div></body></html>"""


def web(username, password):
    c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=30, verify=VERIFY)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    r.raise_for_status()
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


def claude_code(c, device):
    """Sign in like Claude Code does: register, authorize, consent, exchange the code."""
    redirect = "http://127.0.0.1:47213/callback"
    cid = httpx.post(BASE + "/oauth/register", verify=VERIFY, json={"client_name": "Claude Code", "redirect_uris": [redirect]}).json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": cid, "redirect_uri": redirect, "response_type": "code", "code_challenge": challenge,
         "code_challenge_method": "S256", "state": "s"}
    c.get("/oauth/authorize", params=q)
    r = c.post("/oauth/approve", data={**q, "decision": "allow", "csrf": c.headers["X-CSRF-Token"], "device_name": device})
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = httpx.post(BASE + "/oauth/token", verify=VERIFY, data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                                  "redirect_uri": redirect, "code_verifier": verifier}).json()
    return tok["access_token"]


def mcp(token, tool, **args):
    r = httpx.post(BASE + "/mcp", verify=VERIFY, headers={"Authorization": f"Bearer {token}"}, timeout=30,
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    res = r.json()["result"]
    if res.get("isError"):
        print("  !", tool, res["content"][0]["text"])
    return res


def main():
    admin = web("admin", ADMIN_PW)
    for u, name, role in PEOPLE:
        admin.post("/api/users", json={"username": u, "display_name": name, "password": DEMO_PASSWORD, "system_role": role,
                                       "email": f"{u}@corp.example.com"})
    gids = {}
    for g, desc, members in GROUPS:
        r = admin.post("/api/groups", json={"name": g, "description": desc})
        gid = r.json().get("id") or next(x["id"] for x in admin.get("/api/groups").json()["groups"] if x["name"] == g)
        gids[g] = gid
        for m in members:
            admin.post(f"/api/groups/{gid}/members", json={"username": m})
    admin.post("/api/fs/mkdir", json={"path": "/shared/platform-eng/dashboards"})
    admin.post("/api/fs/mkdir", json={"path": "/shared/platform-eng/runbooks"})
    admin.post("/api/fs/mkdir", json={"path": "/shared/research/interviews"})
    admin.post("/api/fs/share", json={"path": "/shared/platform-eng/dashboards", "kind": "group", "name": "design", "role": "viewer"})
    admin.post("/api/fs/share", json={"path": "/shared/research", "kind": "group", "name": "platform-eng", "role": "viewer"})
    admin.post("/api/routing", json={"match": "git_remote", "pattern": "git.example.com/platform/*",
                                     "target": "/shared/platform-eng/projects/{project}", "priority": 10})

    maya = web("maya", DEMO_PASSWORD)
    tm = claude_code(maya, "Claude Code on Maya's MacBook")
    import random
    random.seed(4)
    for name, title, nums in [("latency-dashboard.html", "latency-svc: request latency", (38, 112, 240)),
                              ("error-budget.html", "Error budget, last 30 days", (12, 48, 90))]:
        bars = "".join(f'<i style="height:{random.randint(20, 100)}%"></i>' for _ in range(24))
        mcp(tm, "save_artifact", folder="/shared/platform-eng/dashboards", name=name,
            content=HTML.format(t=title, a=nums[0], b=nums[1], c=nums[2], bars=bars), project="latency-svc",
            git_remote="git@git.example.com:platform/latency-svc.git", git_commit="9f3c2a17be04d1c8")
    mcp(tm, "save_artifact", name="capacity-plan.md", project="latency-svc", git_remote="git@git.example.com:platform/latency-svc.git",
        content="# Capacity plan: latency-svc\n\n## Summary\nTraffic grows ~6% a month. At current growth we hit the p95 budget in **Q2**.\n\n"
                "| Quarter | Peak RPS | Nodes |\n|---|---|---|\n| Q1 | 4,200 | 6 |\n| Q2 | 5,000 | 8 |\n| Q3 | 5,900 | 9 |\n\n"
                "## Actions\n1. Add two nodes before April.\n2. Move the cache to its own pool.\n")
    mcp(tm, "save_artifact", name="incident-0412.md", folder="/shared/platform-eng/runbooks",
        content="# Incident 0412: cache stampede\n\nStart 09:12, mitigated 09:31.\n\n## Timeline\n- 09:12 alerts on p99\n- 09:20 rolled back cache TTL change\n")
    mcp(tm, "save_artifact", name="scratch-notes.txt", content="things to try:\n- connection pooling\n- smaller batches\n")
    mcp(tm, "save_artifact", name="perf-notes.md", project="perf-tests", content="# Perf test notes\n\nBaseline: 112 ms p95.\n")
    mcp(tm, "edit_file", path="/shared/platform-eng/projects/latency-svc/capacity-plan.md",
        old_text="Add two nodes before April.", new_text="Add two nodes before March.")

    priya = web("priya", DEMO_PASSWORD)
    tp = claude_code(priya, "Claude Code on Priya's ThinkPad")
    mcp(tp, "save_artifact", name="onboarding-flow.svg", folder="/shared/design",
        content='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160"><rect width="400" height="160" fill="#faf7f2"/>'
                '<g font-family="system-ui" font-size="12" fill="#222"><rect x="20" y="55" width="90" height="50" rx="8" fill="#f2a93b"/>'
                '<text x="38" y="84">Sign in</text><rect x="155" y="55" width="90" height="50" rx="8" fill="#8aa4ff"/><text x="170" y="84">Connect</text>'
                '<rect x="290" y="55" width="90" height="50" rx="8" fill="#4cc38a"/><text x="312" y="84">Save</text>'
                '<path d="M110 80h45M245 80h45" stroke="#222" stroke-width="2"/></g></svg>', project="onboarding")
    mcp(tp, "save_artifact", name="interview-synthesis.md", folder="/shared/research/interviews",
        content="# Interview synthesis\n\n12 interviews. Top theme: people lose generated files between sessions.\n")
    mcp(tp, "save_artifact", name="brand-tokens.json", project="design-system",
        content='{\n  "color": {"amber": "#F2A93B", "ink": "#0D0E10"},\n  "radius": {"card": 14, "button": 9}\n}\n')
    mcp(tp, "share_folder", path="/users/priya/projects/design-system", group="platform-eng", role="viewer")

    jonas = web("jonas", DEMO_PASSWORD)
    jonas.post("/api/fs/upload", data={"folder": "/shared/platform-eng/runbooks"},
               files={"file": ("on-call-checklist.md", b"# On-call checklist\n\n- [ ] Pager test\n- [ ] Dashboards open\n", "text/markdown")})
    jonas.post("/api/fs/save-text", json={"path": "/users/jonas/inbox/q3-forecast.csv", "content": "month,revenue\njul,120\naug,131\nsep,140\n"})
    diego = web("diego", DEMO_PASSWORD)
    diego.post("/api/fs/save-text", json={"path": "/shared/finance/budget-2027.csv", "content": "team,amount\nplatform,1200000\ndesign,640000\n"})
    td = claude_code(diego, "Claude Code on DIEGO-WS01")
    mcp(td, "save_artifact", path="/shared/platform-eng/dashboards/cost.html", content="<h1>no</h1>")  # refused: Diego can't write there
    print("Demo data ready. People sign in with password:", DEMO_PASSWORD)


if __name__ == "__main__":
    main()
