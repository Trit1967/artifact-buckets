"""M. Auto-sync and first-run welcome (independent QA).

Tests marked xfail(strict=True) document a known finding: they assert what the
spec (or plain safety) expects, and currently fail. If one starts passing, pytest reports XPASS(strict) so
the finding can be closed.
"""
from __future__ import annotations

import base64
import datetime as dt
import re
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from conftest import ADMIN_PW, PW, login, save, uid_of


def b(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def start(c, computer="QA-LAPTOP"):
    r = c.post("/api/sync/link/start", json={"computer": computer})
    assert r.status_code == 200, r.text
    return r.json()


def poll(c, p):
    return c.post("/api/sync/link/poll", json={"poll": p}).json()


def link(web, computer="QA-LAPTOP"):
    """Start a link on 'the computer', approve it with this web session, return the raw sync token."""
    s = start(web.c, computer)
    r = web.post(f"/api/sync/link/{s['code']}/allow")
    assert r.status_code == 200, r.text
    p = poll(web.c, s["poll"])
    assert p["status"] == "approved", p
    return p["token"]


def put(c, tok, rel, data=b"hello", where="projects", project="proj", raw_headers=None):
    h = {"authorization": f"Bearer {tok}", "x-sync-where": where, "x-sync-path": b(rel)}
    if where == "projects" and project is not None:
        h["x-sync-project"] = b(project)
    if raw_headers:
        h.update(raw_headers)
    return c.put("/api/sync/file", content=data, headers=h)


@pytest.fixture()
def raw(app):
    return TestClient(app, follow_redirects=False, raise_server_exceptions=False)


@pytest.fixture()
def alice(make_user):
    return make_user("alice")


def _db():
    from app import db as dbm
    return dbm.new_session()


# ---------------------------------------------------------------------------------------------
# M4: a sync token can do one thing only
# ---------------------------------------------------------------------------------------------

PUBLIC_PREFIXES = ("/claude/", "/.well-known/", "/healthz", "/oauth/register", "/oauth/authorize", "/oauth/token",
                   "/oauth/revoke", "/api/sync/link/start", "/api/sync/link/poll", "/api/auth/login",
                   "/api/auth/logout", "/api/auth/me", "/auth/oidc/", "/preview/")


def _all_routes(app):
    out = []
    for r in app.routes:
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        if not path or path == "/{full_path:path}" or "{full_path" in path:
            continue
        for m in methods - {"HEAD", "OPTIONS"}:
            out.append((m, path))
    return sorted(set(out))


def _fill(path):
    return (path.replace("{code}", "ABCD-EFGH").replace("{decision}", "allow").replace("{tid}", "1")
            .replace("{grant_id}", "1").replace("{user_id}", "1").replace("{gid}", "1").replace("{username}", "admin")
            .replace("{rid}", "1").replace("{vid}", "1").replace("{action}", "scrub").replace("{token}", "x")
            .replace("{name}", "x").replace("{path:path}", "x"))


def test_sync_token_refused_by_every_other_route(app, admin, raw):
    tok = link(admin)
    hdr = {"authorization": f"Bearer {tok}"}
    leaks = []
    for m, path in _all_routes(app):
        if path == "/api/sync/file" or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            continue
        url = _fill(path)
        r = raw.request(m, url, headers={**hdr, "content-type": "application/json"}, content=b"{}",
                        params={"path": "/users/admin"})
        if 200 <= r.status_code < 300 and not url.startswith("/api/") and "text/html" in r.headers.get("content-type", ""):
            continue  # SPA shell
        if r.status_code < 400 or r.status_code >= 500:
            leaks.append((m, url, r.status_code, r.text[:120]))
    assert not leaks, leaks


@pytest.mark.parametrize("method,url,body", [
    ("GET", "/api/fs/list?path=/users/admin", None),
    ("GET", "/api/fs/download?path=/users/admin/projects/proj/a.md", None),
    ("GET", "/api/fs/meta?path=/users/admin/projects/proj/a.md", None),
    ("GET", "/api/fs/search?q=a", None),
    ("POST", "/api/fs/delete", {"paths": ["/users/admin/projects/proj/a.md"]}),
    ("POST", "/api/fs/save-text", {"path": "/users/admin/inbox/x.md", "content": "x"}),
    ("POST", "/api/fs/share", {"path": "/users/admin/projects", "principal": "alice", "role": "editor"}),
    ("GET", "/api/tokens", None),
    ("POST", "/api/tokens", {"name": "x"}),
    ("GET", "/api/users", None),
    ("GET", "/api/audit", None),
    ("GET", "/api/sync/status", None),
    ("POST", "/api/sync/welcome/done", None),
    ("GET", "/api/sync/link/ABCD-EFGH", None),
])
def test_sync_token_cannot_read_list_delete_share(admin, raw, method, url, body):
    tok = link(admin)
    assert put(raw, tok, "a.md").status_code == 200
    r = raw.request(method, url, headers={"authorization": f"Bearer {tok}"}, json=body)
    assert r.status_code in (401, 403), (url, r.status_code, r.text[:200])


def test_sync_token_cannot_use_mcp(admin, raw):
    tok = link(admin)
    for m in ("GET", "POST", "DELETE"):
        r = raw.request(m, "/mcp", headers={"authorization": f"Bearer {tok}", "accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"} if m == "POST" else None)
        assert r.status_code in (401, 403, 405), (m, r.status_code)
    r = raw.post("/mcp", headers={"authorization": f"Bearer {tok}"},
                 json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_files", "arguments": {}}})
    assert r.status_code == 401


def test_sync_token_cannot_refresh_or_mint_oauth(admin, raw):
    tok = link(admin)
    r = raw.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok, "client_id": "x"})
    assert r.status_code in (400, 401)
    assert "access_token" not in r.text


def test_admin_sync_token_has_no_admin_powers(admin, alice, raw):
    tok = link(admin)
    hdr = {"authorization": f"Bearer {tok}"}
    for url in ("/api/users", "/api/overview", "/api/storage", "/api/audit", "/api/tokens?all=true", "/api/directory"):
        assert raw.get(url, headers=hdr).status_code == 401, url
    # Can't write into alice's home or /shared even though the admin could on the web.
    save(alice, "/users/alice/projects/p/keep.md", "alice's")
    for where, project, rel in [("projects", "p", "../../../alice/projects/p/keep.md"),
                                ("projects", "../../alice/projects/p", "keep.md"),
                                ("inbox", None, "../../alice/inbox/x.md"),
                                ("inbox", None, "/users/alice/inbox/x.md")]:
        r = put(raw, tok, rel, b"pwned", where=where, project=project)
        if r.status_code == 200:
            assert r.json()["path"].startswith("/users/admin/"), r.json()
    assert alice.get("/api/fs/download", params={"path": "/users/alice/projects/p/keep.md"}).content == b"alice's"


def test_sync_lands_where_spec_says(alice, raw):
    tok = link(alice)
    r = put(raw, tok, "docs/q3 summary.md", b"# Q3", project="report-site")
    assert r.status_code == 200 and r.json()["path"] == "/users/alice/projects/report-site/docs/q3 summary.md"
    assert r.json()["version"] == 1
    assert put(raw, tok, "docs/q3 summary.md", b"# Q3", project="report-site").json()["unchanged"] is True
    assert put(raw, tok, "docs/q3 summary.md", b"# Q3 v2", project="report-site").json()["version"] == 2
    r = put(raw, tok, "loose.txt", b"x", where="inbox")
    assert r.json()["path"] == "/users/alice/inbox/loose.txt"
    meta = alice.get("/api/fs/meta", params={"path": "/users/alice/projects/report-site/docs/q3 summary.md"}).json()
    assert meta.get("source") == "sync" and "synced" in (meta.get("tags") or [])
    # The where header is required to be exactly projects/inbox.
    assert put(raw, tok, "x.md", where="Projects").status_code == 400
    assert put(raw, tok, "x.md", where="trash").status_code == 400


BAD_REL = [
    "../x.md", "a/../../x.md", "..\\..\\x.md", "a\\..\\..\\x.md", "./x.md", "a/./x.md", ".hidden.md", "a/.git/config",
    "a//b.md", "x.md.", "x.md ", " x.md", "con.txt", "a/nul", "C:\\Windows\\x.md", "a:b.md", "a?b.md", "a*b.md",
    "a<b>.md", 'a"b.md', "a|b.md", "a\x00b.md", "a\nb.md", "a\tb.md", "a\u202egnp.exe", "a\u200bb.md", "a\u2066b.md",
    "a\ufeffb.md", "x" * 256 + ".md", "/".join(["d"] * 600), "", "/", "\\",
]


@pytest.mark.parametrize("rel", BAD_REL, ids=[repr(x)[:40] for x in BAD_REL])
def test_bad_file_paths_refused(alice, raw, rel):
    tok = link(alice)
    r = put(raw, tok, rel)
    assert r.status_code in (400, 403), (rel, r.status_code, r.text[:200])


@pytest.mark.parametrize("project", [".", "..", "", " ", "a/b", "a\\b", ".git", "con", "x.", "a\u202eb", "a:b", "x" * 300,
                                     "../../alice", "%2e%2e", None])
def test_bad_project_names_refused_or_contained(alice, raw, project):
    tok = link(alice)
    r = put(raw, tok, "x.md", project=project)
    if project == "%2e%2e":  # a literal name made of %, 2, e: allowed, but must stay a plain folder name
        assert r.status_code == 200 and r.json()["path"] == "/users/alice/projects/%2e%2e/x.md"
        return
    assert r.status_code in (400, 403), (project, r.status_code, r.text[:200])


def test_absolute_paths_stay_inside(alice, raw):
    tok = link(alice)
    r = put(raw, tok, "/etc/passwd")
    assert r.status_code == 200 and r.json()["path"] == "/users/alice/projects/proj/etc/passwd"
    r = put(raw, tok, "/users/admin/inbox/x.md", where="inbox")
    assert r.status_code == 200 and r.json()["path"].startswith("/users/alice/inbox/")


@pytest.mark.parametrize("hdr", [{"x-sync-path": "!!!not base64!!!"}, {"x-sync-path": base64.b64encode(b"\xff\xfe").decode()},
                                 {"x-sync-path": ""}, {"x-sync-project": ""}])
def test_bad_headers(alice, raw, hdr):
    tok = link(alice)
    r = put(raw, tok, "x.md", raw_headers=hdr)
    assert r.status_code == 400, (hdr, r.status_code, r.text[:200])


def test_urlsafe_and_unpadded_base64_accepted(alice, raw):
    tok = link(alice)
    rel = "ünïcode ~~ ??.md".replace("?", "q")
    enc = base64.urlsafe_b64encode(rel.encode()).decode().rstrip("=")
    r = put(raw, tok, "x", raw_headers={"x-sync-path": enc})
    assert r.status_code == 200 and r.json()["path"].endswith(rel)


def test_inbox_takes_only_a_file_name(alice, raw):
    """M2: 'Anything else goes to /users/<me>/inbox/<file name>'. The server accepts any depth under inbox."""
    tok = link(alice)
    r = put(raw, tok, "deep/er/x.md", where="inbox")
    assert r.status_code == 200  # informational: inbox subfolders are accepted (see report, low)
    assert r.json()["path"] == "/users/alice/inbox/deep/er/x.md"


def test_folder_in_the_way(alice, raw):
    tok = link(alice)
    assert put(raw, tok, "dir/x.md").status_code == 200
    r = put(raw, tok, "dir")
    assert r.status_code == 409


def test_max_file_size(alice, raw, monkeypatch):
    from app import config
    monkeypatch.setenv("AB_MAX_FILE_BYTES", "1000")
    config.reload_settings()
    tok = link(alice)
    assert put(raw, tok, "ok.bin", b"x" * 1000).status_code == 200
    assert put(raw, tok, "big.bin", b"x" * 1001).status_code == 413


def test_quota(admin, alice, raw):
    uid = uid_of(admin, "alice")
    assert admin.patch(f"/api/users/{uid}", json={"quota_bytes": 5000}).status_code == 200
    tok = link(alice)
    assert put(raw, tok, "a.bin", b"x" * 3000).status_code == 200
    r = put(raw, tok, "b.bin", b"x" * 3000)
    assert r.status_code in (413, 507), r.status_code


def test_sync_cannot_destroy_existing_file(admin, alice, raw):
    uid = uid_of(admin, "alice")
    assert admin.patch(f"/api/users/{uid}", json={"quota_bytes": 4000}).status_code == 200
    save(alice, "/users/alice/projects/proj/plan.md", "A" * 3000)  # made on the web, not synced
    tok = link(alice)
    r = put(raw, tok, "plan.md", b"B" * 3000)
    # Either refuse, or keep the original as a version that can be restored.
    if r.status_code == 200:
        vers = alice.get("/api/fs/versions", params={"path": "/users/alice/projects/proj/plan.md"}).json()
        assert any(v.get("version") == 1 for v in vers.get("versions", [])), vers


def test_sync_cannot_change_files_it_did_not_make(alice, raw):
    """Fixed S3: the web file stays as it is; the synced copy goes beside it."""
    save(alice, "/users/alice/projects/proj/plan.md", "made on the web")
    tok = link(alice)
    r = put(raw, tok, "plan.md", b"replaced by a computer")
    assert r.status_code == 200 and r.json()["path"] != "/users/alice/projects/proj/plan.md", r.text
    assert alice.get("/api/fs/download", params={"path": "/users/alice/projects/proj/plan.md"}).content == b"made on the web"


def test_revoked_token_stops_at_once(alice, raw):
    tok = link(alice)
    assert put(raw, tok, "a.md").status_code == 200
    t = next(t for t in alice.get("/api/tokens").json()["tokens"] if t["kind"] == "sync")
    assert alice.delete(f"/api/tokens/{t['id']}").status_code == 200
    assert put(raw, tok, "b.md").status_code == 401


def test_admin_can_turn_off_someone_elses_computer(admin, alice, raw):
    tok = link(alice, "ALICE-PC")
    t = next(t for t in admin.get("/api/tokens", params={"all": True}).json()["tokens"] if t["kind"] == "sync")
    assert t["name"] == "Sync on ALICE-PC"
    assert admin.delete(f"/api/tokens/{t['id']}").status_code == 200
    assert put(raw, tok, "b.md").status_code == 401


def test_other_member_cannot_turn_off_my_computer(alice, make_user, raw):
    tok = link(alice)
    bob = make_user("bob")
    t = next(t for t in alice.get("/api/tokens").json()["tokens"] if t["kind"] == "sync")
    assert bob.delete(f"/api/tokens/{t['id']}").status_code == 404
    assert put(raw, tok, "b.md").status_code == 200


def test_suspended_user_token_stops(admin, alice, raw):
    tok = link(alice)
    uid = uid_of(admin, "alice")
    assert admin.patch(f"/api/users/{uid}", json={"status": "suspended"}).status_code == 200
    assert put(raw, tok, "b.md").status_code == 401
    # Reactivating doesn't bring the old link back.
    assert admin.patch(f"/api/users/{uid}", json={"status": "active"}).status_code == 200
    assert put(raw, tok, "c.md").status_code == 401


def test_expired_token_and_one_year_lifetime(alice, raw):
    tok = link(alice)
    from app.db import Token
    from app.security import sha256_hex
    with _db() as s:
        t = s.scalar(select(Token).where(Token.token_hash == sha256_hex(tok)))
        days = (t.expires_at - t.created_at).days
        assert 364 <= days <= 366
        t.expires_at = dt.datetime.utcnow() - dt.timedelta(seconds=1)
        s.commit()
    assert put(raw, tok, "late.md").status_code == 401


def test_garbage_and_other_token_kinds_refused(alice, raw):
    r = alice.post("/api/tokens", json={"name": "pat", "days": 1})
    pat = r.json()["token"]
    for t in (pat, "ab_sync_" + "A" * 40, "", "null"):
        assert put(raw, t, "x.md").status_code == 401, t
    r = raw.put("/api/sync/file", content=b"x", headers={"x-sync-path": b("x.md"), "x-sync-project": b("p")})
    assert r.status_code == 401
    # Cookie session alone is not enough either (no CSRF-less upload from a web page).
    r = alice.c.put("/api/sync/file", content=b"x", headers={"x-sync-path": b("x.md"), "x-sync-project": b("p")})
    assert r.status_code == 401


def test_session_header_is_bounded(alice, raw):
    tok = link(alice)
    r = put(raw, tok, "s.md", raw_headers={"x-sync-session": "s" * 600})
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        meta = alice.get("/api/fs/meta", params={"path": "/users/alice/projects/proj/s.md"}).json()
        assert len(meta.get("session_id") or "") <= 128, len(meta.get("session_id") or "")


# ---------------------------------------------------------------------------------------------
# M3: linking
# ---------------------------------------------------------------------------------------------

def test_code_shape_and_alphabet(raw):
    seen = set()
    for _ in range(40):
        s = start(raw)
        assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", s["code"]), s["code"]
        assert len(s["poll"]) >= 40 and s["expires_in"] == 600 and s["link"].endswith("/link?code=" + s["code"])
        seen.add(s["code"])
    assert len(seen) == 40


def test_link_needs_session_and_csrf(alice, raw):
    s = start(raw)
    assert raw.get(f"/api/sync/link/{s['code']}").status_code == 401
    assert raw.post(f"/api/sync/link/{s['code']}/allow").status_code == 401
    assert alice.c.post(f"/api/sync/link/{s['code']}/allow").status_code == 403  # no CSRF
    assert alice.c.post(f"/api/sync/link/{s['code']}/allow", headers={"x-csrf-token": "wrong"}).status_code == 403
    # A personal token can't approve it (not the web app).
    pat = alice.post("/api/tokens", json={"name": "pat", "days": 1}).json()["token"]
    r = raw.post(f"/api/sync/link/{s['code']}/allow", headers={"authorization": f"Bearer {pat}"})
    assert r.status_code == 403
    assert poll(raw, s["poll"])["status"] == "pending"


def test_pat_can_not_read_pending_code_info(alice, raw):
    """Fixed S22: only the web session can look up a pending code."""
    s = start(raw, "SECRET-HOSTNAME")
    pat = alice.post("/api/tokens", json={"name": "pat", "days": 1}).json()["token"]
    r = raw.get(f"/api/sync/link/{s['code']}", headers={"authorization": f"Bearer {pat}"})
    assert r.status_code in (401, 403)


def test_code_expires_after_ten_minutes(alice, raw):
    from app.sync import SyncLink
    s = start(raw)
    with _db() as db:
        db.execute(update(SyncLink).where(SyncLink.user_code == s["code"])
                   .values(created_at=dt.datetime.utcnow() - dt.timedelta(minutes=10, seconds=5)))
        db.commit()
    assert alice.get(f"/api/sync/link/{s['code']}").status_code == 404
    assert alice.post(f"/api/sync/link/{s['code']}/allow").status_code == 404
    assert poll(raw, s["poll"])["status"] == "expired"


def test_approved_but_polled_too_late_gives_nothing(alice, raw):
    from app.sync import SyncLink
    s = start(raw)
    assert alice.post(f"/api/sync/link/{s['code']}/allow").status_code == 200
    with _db() as db:
        db.execute(update(SyncLink).where(SyncLink.user_code == s["code"])
                   .values(created_at=dt.datetime.utcnow() - dt.timedelta(minutes=11)))
        db.commit()
    assert poll(raw, s["poll"])["status"] == "expired"
    assert not [t for t in alice.get("/api/tokens").json()["tokens"] if t["kind"] == "sync"]


def test_single_use_and_token_once(alice, raw):
    s = start(raw)
    assert alice.post(f"/api/sync/link/{s['code']}/allow").json()["status"] == "approved"
    assert alice.post(f"/api/sync/link/{s['code']}/allow").status_code == 400
    assert alice.post(f"/api/sync/link/{s['code']}/deny").status_code == 400
    p1 = poll(raw, s["poll"])
    assert p1["status"] == "approved" and p1["token"].startswith("ab_sync_")
    for _ in range(3):
        p = poll(raw, s["poll"])
        assert p == {"status": "done"}, p
    assert len([t for t in alice.get("/api/tokens").json()["tokens"] if t["kind"] == "sync"]) == 1
    # The page now reports the code as used.
    assert alice.get(f"/api/sync/link/{s['code']}").json()["status"] == "done"


def test_deny(alice, raw):
    s = start(raw)
    assert alice.post(f"/api/sync/link/{s['code']}/deny").json()["status"] == "denied"
    for _ in range(2):
        assert poll(raw, s["poll"]) == {"status": "denied"}
    assert alice.post(f"/api/sync/link/{s['code']}/allow").status_code == 400
    assert not [t for t in alice.get("/api/tokens").json()["tokens"] if t["kind"] == "sync"]


def test_unknown_decision(alice, raw):
    s = start(raw)
    assert alice.post(f"/api/sync/link/{s['code']}/maybe").status_code == 404
    assert poll(raw, s["poll"])["status"] == "pending"


def test_code_case_and_spacing(alice, raw):
    s = start(raw)
    assert alice.get(f"/api/sync/link/{s['code'].lower()}").status_code == 200
    assert alice.get(f"/api/sync/link/%20{s['code']}%20").status_code == 200
    assert alice.get(f"/api/sync/link/{s['code'].replace('-', '')}").status_code == 404


def test_poll_garbage(raw):
    assert poll(raw, "")["status"] == "expired"
    assert poll(raw, "x" * 10000)["status"] == "expired"
    assert raw.post("/api/sync/link/poll", json={}).status_code == 422


def test_approver_owns_the_token(alice, make_user, raw):
    """One person's code approved by another: the token belongs to whoever pressed Allow."""
    bob = make_user("bob")
    s = start(raw, "ALICE-LAPTOP")
    assert bob.post(f"/api/sync/link/{s['code']}/allow").status_code == 200
    p = poll(raw, s["poll"])
    assert p["user"] == "bob" and p["home"] == "/users/bob"
    r = put(raw, p["token"], "x.md")
    assert r.json()["path"].startswith("/users/bob/")


def test_approver_suspended_before_poll(admin, alice, raw):
    s = start(raw)
    assert alice.post(f"/api/sync/link/{s['code']}/allow").status_code == 200
    assert admin.patch(f"/api/users/{uid_of(admin, 'alice')}", json={"status": "suspended"}).status_code == 200
    assert poll(raw, s["poll"])["status"] == "denied"


@pytest.mark.parametrize("name,expect", [
    ("<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"),  # stored as text; the UI must escape it
    ("PC\u202egnp.exe", "PCgnp.exe"),
    ("A\u2066B\u2069C", "ABC"),
    ("A\u200bB\ufeffC", "ABC"),
    ("line1\nline2\r\t\x00\x1b[31m", "line1line2[31m"),
    ("   ", "This computer"),
    ("\u202e\u202e", "This computer"),
    ("x" * 500, "x" * 64),
])
def test_computer_name_cleaned(alice, raw, name, expect):
    s = start(raw, name)
    assert alice.get(f"/api/sync/link/{s['code']}").json()["computer"] == expect


@pytest.mark.parametrize("ch", ["\u061c", "\u2028", "\u2029", "\x85", "\u00ad", "\u180e", "\x9b"])
def test_computer_name_other_invisibles(alice, raw, ch):
    s = start(raw, f"A{ch}B")
    assert alice.get(f"/api/sync/link/{s['code']}").json()["computer"] == "AB"


def test_start_cleans_up_old_rows(raw):
    from app.sync import SyncLink
    s = start(raw)
    with _db() as db:
        db.execute(update(SyncLink).where(SyncLink.user_code == s["code"])
                   .values(created_at=dt.datetime.utcnow() - dt.timedelta(hours=2)))
        db.commit()
    start(raw)
    with _db() as db:
        assert db.scalar(select(SyncLink).where(SyncLink.user_code == s["code"])) is None


def test_relink_same_computer_replaces_old_token(alice, raw):
    t1 = link(alice, "LAPTOP")
    t2 = link(alice, "LAPTOP")
    assert put(raw, t2, "a.md").status_code == 200
    syncers = [t for t in alice.get("/api/tokens").json()["tokens"] if t["kind"] == "sync"]
    assert len(syncers) == 1 or put(raw, t1, "b.md").status_code == 401


def test_audit_trail(admin, alice, raw):
    s = start(raw, "AUD-PC")
    alice.post(f"/api/sync/link/{s['code']}/allow")
    poll(raw, s["poll"])
    s2 = start(raw, "DENY-PC")
    alice.post(f"/api/sync/link/{s2['code']}/deny")
    ev = admin.get("/api/audit", params={"limit": 200}).json()["events"]
    acts = {(e["action"], e.get("target")) for e in ev}
    assert ("sync.approve", "AUD-PC") in acts and ("sync.link", "AUD-PC") in acts and ("sync.deny", "DENY-PC") in acts


# ---------------------------------------------------------------------------------------------
# Status and welcome
# ---------------------------------------------------------------------------------------------

def test_status_is_per_person(alice, make_user, raw):
    tok = link(alice, "ALICE-PC")
    put(raw, tok, "a.md", b"1")
    put(raw, tok, "b.md", b"22", where="inbox")
    bob = make_user("bob")
    st = bob.get("/api/sync/status").json()
    assert st["computers"] == [] and st["files"] == 0 and st["recent"] == []
    st = alice.get("/api/sync/status").json()
    assert st["files"] == 2 and st["bytes"] == 3 and st["computers"][0]["name"] == "ALICE-PC"
    assert {g["folder"] for g in st["folders"]} == {"/users/alice/projects/proj", "/users/alice/inbox"}


def test_welcome_per_person_and_login_flag(app, admin, alice):
    assert alice.get("/api/auth/me").json()["welcome"] is True
    assert alice.post("/api/sync/welcome/done").status_code == 200
    assert alice.get("/api/auth/me").json()["welcome"] is False
    assert admin.get("/api/auth/me").json()["welcome"] is True
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "alice", "password": PW}).json()["welcome"] is False


def test_welcome_done_needs_csrf(alice):
    assert alice.c.post("/api/sync/welcome/done").status_code == 403


# ---------------------------------------------------------------------------------------------
# Race: the token is handed out exactly once even when two polls arrive together
# ---------------------------------------------------------------------------------------------

def test_concurrent_polls_issue_one_token(alice, app):
    """Uses the in-process app with real threads: two polls racing on an approved code."""
    issued = []
    for _ in range(8):
        s = start(alice.c)
        alice.post(f"/api/sync/link/{s['code']}/allow")
        barrier = threading.Barrier(4)
        res = []

        def go():
            c = TestClient(app, raise_server_exceptions=False)
            barrier.wait()
            r = c.post("/api/sync/link/poll", json={"poll": s["poll"]})
            res.append(r.json() if r.status_code == 200 else {"status": f"http {r.status_code}"})

        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        issued.append(sum(1 for r in res if r.get("token")))
    toks = [t for t in alice.get("/api/tokens").json()["tokens"] if t["kind"] == "sync"]
    assert all(n == 1 for n in issued) and len(toks) == 1, (issued, len(toks))  # re-linking 'QA-LAPTOP' replaces its link (S5)
