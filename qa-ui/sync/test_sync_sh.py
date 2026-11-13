"""Shell-level tests for plugin-marketplace/artifact-buckets/sync/sync.sh and hooks/hooks.json (M1, M2, M5, M6).

Runs against a live server (default http://127.0.0.1:8096, override with AB_QA_SERVER). Each test builds a fake
HOME under /home/claude/qa-sync/t/ (not /tmp: sync.sh deliberately skips files under /tmp), writes realistic
Claude Code transcript lines (Write/Edit results with cwd + toolUseResult.filePath + structuredPatch, and Read
results that must be ignored), links it with `sync.sh link <server>` approved through the web API, then runs it.

    cd /home/claude/ab/qa-ui/sync && ../../.venv/bin/python -m pytest -q test_sync_sh.py

Tests marked xfail(strict=True) document findings in SYNC_QA_REPORT.md.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid

import httpx
import pytest

SERVER = os.environ.get("AB_QA_SERVER", "http://127.0.0.1:8096")
ADMIN = ("admin", os.environ.get("AB_QA_ADMIN_PW", "admin-pass-123456"))
MEMBER = ("syncqa", "syncqa-pass-1234567")
PLUGIN = "/home/claude/ab/plugin-marketplace/artifact-buckets"
SYNC = f"{PLUGIN}/sync/sync.sh"
ROOT = "/home/claude/qa-sync/t"


# ---- helpers ------------------------------------------------------------------------------

def web(user, pw):
    c = httpx.Client(base_url=SERVER, timeout=30)
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    r.raise_for_status()
    c.headers["x-csrf-token"] = r.json()["csrf"]
    return c


@pytest.fixture(scope="session")
def member():
    a = web(*ADMIN)
    r = a.post("/api/users", json={"username": MEMBER[0], "password": MEMBER[1], "system_role": "member",
                                   "display_name": "Sync QA"})
    assert r.status_code in (200, 201, 409), r.text
    return web(*MEMBER)


class Home:
    def __init__(self, name):
        self.root = os.path.join(ROOT, f"{name}-{uuid.uuid4().hex[:6]}")
        self.home = os.path.join(self.root, "home")
        self.work = os.path.join(self.home, "work")
        os.makedirs(self.work)
        self.claude = os.path.join(self.home, ".claude")
        self.dir = os.path.join(self.home, ".artifact-buckets")
        self.project = uuid.uuid4().hex[:8]  # unique project folder name on the server

    def env(self, **extra):
        e = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        e.update(extra)
        return e

    def sh(self, *args, timeout=120, env=None, cwd=None):
        return subprocess.run(["sh", SYNC, *args], env=env or self.env(), capture_output=True, text=True,
                              timeout=timeout, cwd=cwd or self.root)

    def write(self, path, data="hello\n"):
        p = path if os.path.isabs(path) else os.path.join(self.work, path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(data)
        return p

    def transcript(self, entries, name=None):
        """entries: [(cwd, filePath, kind)] kind in create|update|read."""
        d = os.path.join(self.claude, "projects", "-" + self.work.strip("/").replace("/", "-"))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, (name or str(uuid.uuid4())) + ".jsonl")
        with open(p, "a", encoding="utf-8") as f:
            for cwd, fp, kind in entries:
                f.write(tline(cwd, fp, kind) + "\n")
        return p

    def conf(self):
        out = {}
        with open(os.path.join(self.dir, "sync.conf")) as f:
            for line in f:
                k, _, v = line.rstrip("\n").partition("=")
                out[k] = v
        return out

    def set_conf(self, key, value):
        p = os.path.join(self.dir, "sync.conf")
        lines = [l for l in open(p).read().splitlines() if not l.startswith(key + "=")] + [f"{key}={value}"]
        open(p, "w").write("\n".join(lines) + "\n")

    def link(self, who=MEMBER, decision="allow"):
        p = subprocess.Popen(["sh", SYNC, "link", SERVER], env=self.env(), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, cwd=self.root)
        out, code = "", None
        while code is None:
            line = p.stdout.readline()
            out += line
            m = re.search(r"code matches: (\S+)", line)
            if m:
                code = m.group(1)
            if not line and p.poll() is not None:
                raise AssertionError(out)
        w = web(*who)
        r = w.post(f"/api/sync/link/{code}/{decision}")
        assert r.status_code == 200, r.text
        rest, _ = p.communicate(timeout=30)
        return p.returncode, out + rest

    def token_id(self, w):
        hint = self.conf()["token"][-4:]
        return next(t["id"] for t in w.get("/api/tokens").json()["tokens"] if t["kind"] == "sync" and t["hint"] == hint)


def tline(cwd, fp, kind="create", content="x"):
    """One transcript line shaped like Claude Code 2.1's (checked against a real one)."""
    if kind == "read":
        tur = {"type": "text", "file": {"filePath": fp, "content": content, "numLines": 1, "startLine": 1, "totalLines": 1}}
        msg = "     1\t" + content
    elif kind == "create":
        tur = {"type": "create", "filePath": fp, "content": content, "structuredPatch": [], "originalFile": None}
        msg = "File created successfully at: " + fp
    else:
        tur = {"filePath": fp, "oldString": "a", "newString": "b", "originalFile": content,
               "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1, "lines": ["-a", "+b"]}],
               "userModified": False, "replaceAll": False}
        msg = f"The file {fp} has been updated."
    d = {"parentUuid": str(uuid.uuid4()), "isSidechain": False, "userType": "external", "cwd": cwd,
         "sessionId": str(uuid.uuid4()), "version": "2.1.323", "gitBranch": "", "type": "user",
         "message": {"role": "user", "content": [{"tool_use_id": "toolu_1", "type": "tool_result", "content": msg}]},
         "uuid": str(uuid.uuid4()), "timestamp": "2026-11-13T10:00:00.000Z", "toolUseResult": tur}
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


def server_files(w, folder):
    """All file paths under a folder (recursive), or [] if it doesn't exist."""
    out, todo = [], [folder]
    while todo:
        f = todo.pop()
        r = w.get("/api/fs/list", params={"path": f})
        if r.status_code != 200:
            continue
        for e in r.json().get("items", r.json().get("entries", [])):
            p = e.get("path") or f"{f}/{e['name']}"
            (todo if e.get("type") in ("folder", "dir") else out).append(p)
    return sorted(out)


def git(*a):
    subprocess.run(["git", *a], check=True, capture_output=True)


@pytest.fixture()
def home(request):
    h = Home(request.node.name[:40].replace("[", "_").replace("]", ""))
    yield h


# ---- scan ---------------------------------------------------------------------------------

def test_scan_before_linking_changes_nothing(home, member):
    f = home.write(f"{home.project}/a.md")
    home.transcript([(os.path.dirname(f), f, "create")])
    r = home.sh("scan")
    assert r.returncode == 0, r.stderr
    assert "Would copy 1 file(s)" in r.stdout and f"projects/{home.project}" in r.stdout
    assert not os.path.exists(os.path.join(home.dir, "sync.conf"))
    assert server_files(member, f"/users/{MEMBER[0]}/projects/{home.project}") == []


def test_scan_after_linking_changes_nothing(home, member):
    assert home.link()[0] == 0
    f = home.write(f"{home.project}/a.md")
    home.transcript([(os.path.dirname(f), f, "create")])
    before = member.get("/api/sync/status").json()["files"]
    r = home.sh("scan")
    assert r.returncode == 0 and "Would copy 1 file(s)" in r.stdout
    assert member.get("/api/sync/status").json()["files"] == before
    assert not os.path.exists(os.path.join(home.dir, "state", "last-run"))
    assert "Last complete run" not in home.sh("status").stdout


# ---- what goes where ---------------------------------------------------------------------

def test_places_project_plain_folder_and_inbox(home, member):
    assert home.link()[0] == 0
    repo = os.path.join(home.work, home.project)
    os.makedirs(repo)
    git("init", "-q", repo)
    plain = os.path.join(home.work, home.project + "-plain")
    a = home.write(f"{repo}/docs/report one.md", "r1\n")
    sub = os.path.join(repo, "src", "deep")
    b = home.write(f"{sub}/ünï cødé ✓.md", "u\n")        # cwd is a subfolder of the repo: project is the repo
    c = home.write(f"{plain}/it's here.txt", "p\n")
    d = home.write(f"{home.home}/loose-{home.project}.md", "l\n")     # cwd is HOME: inbox
    e = home.write(f"{home.work}/elsewhere/out-{home.project}.md", "o\n")  # outside the cwd project: inbox
    home.transcript([(repo, a, "create"), (sub, b, "update"), (plain, c, "create"), (home.home, d, "create"),
                     (repo, e, "create")])
    r = home.sh("run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Done: 5 copied." in r.stdout, r.stdout
    u = f"/users/{MEMBER[0]}"
    got = server_files(member, f"{u}/projects/{home.project}") + server_files(member, f"{u}/projects/{home.project}-plain")
    assert f"{u}/projects/{home.project}/docs/report one.md" in got
    assert f"{u}/projects/{home.project}/src/deep/ünï cødé ✓.md" in got
    assert f"{u}/projects/{home.project}-plain/it's here.txt" in got
    inbox = server_files(member, f"{u}/inbox")
    assert f"{u}/inbox/loose-{home.project}.md" in inbox and f"{u}/inbox/out-{home.project}.md" in inbox
    meta = member.get("/api/fs/meta", params={"path": f"{u}/projects/{home.project}/docs/report one.md"}).json()
    assert meta.get("source") == "sync" and meta.get("project") == home.project


def test_skips(home, member):
    assert home.link()[0] == 0
    home.set_conf("max", "100")
    repo = os.path.join(home.work, home.project)
    os.makedirs(repo)
    git("init", "-q", repo)
    home.write(f"{repo}/.gitignore", "*.log\n")
    home.write(f"{repo}/tracked.md")
    git("-C", repo, "add", ".")
    git("-C", repo, "-c", "user.email=q@a", "-c", "user.name=q", "commit", "-qm", "i")
    skip = {
        "tracked.md": "in git", "out.log": "ignored by git", "node_modules/x/i.js": "build", "dist/a.js": "build",
        "build/a.js": "build", ".venv/x.py": "build", "target/x": "build", "__pycache__/x.pyc": "build",
        ".env": "secret", ".env.local": "secret", "key.pem": "secret", "id_rsa": "secret", "my_secret.txt": "secret",
        "aws_credentials.ini": "secret", "prod.tfstate": "secret", "x.tfvars": "secret", ".hidden/x.md": "hidden",
        "sub/.dot.md": "hidden", "empty.md": "empty", "big.md": "too big",
    }
    entries = []
    for rel, why in skip.items():
        p = os.path.join(repo, rel)
        home.write(p, "" if why == "empty" else ("x" * 200 if why == "too big" else "s\n"))
        entries.append((repo, p, "create"))
    keep = home.write(f"{repo}/keep.md", "k\n")
    readonly = home.write(f"{repo}/only-read.md", "r\n")
    sysf = [home.write(f"{home.claude}/CLAUDE.md"), home.write(f"{home.dir}/note.md")]
    entries += [(repo, keep, "create"), (repo, readonly, "read")] + [(repo, s, "create") for s in sysf]
    entries.append((repo, os.path.join(repo, "gone.md"), "create"))  # deleted since
    home.transcript(entries)
    r = home.sh("run")
    assert r.returncode == 0 and "Done: 1 copied." in r.stdout, r.stdout
    plan = open(os.path.join(home.dir, "state", "plan.tsv")).read()
    for rel, why in skip.items():
        assert f"skip\t{why}\t{os.path.join(repo, rel)}" in plan, (rel, why)
    assert "only-read.md" not in plan  # Read results are not candidates at all
    assert server_files(member, f"/users/{MEMBER[0]}/projects/{home.project}") == [
        f"/users/{MEMBER[0]}/projects/{home.project}/keep.md"]


@pytest.mark.xfail(strict=True, reason="FINDING S1: files in hidden folders outside a project (~/.ssh/config, "
                                        "~/.kube/config, ~/.docker/config.json, ~/.config/gh/hosts.yml) go to the inbox")
def test_hidden_folders_outside_a_project_are_skipped(home, member):
    assert home.link()[0] == 0
    repo = os.path.join(home.work, home.project)
    os.makedirs(repo)
    leaks = [home.write(f"{home.home}/.ssh/config", "Host prod\n  IdentityFile ~/.ssh/prod\n"),
             home.write(f"{home.home}/.kube/config", "users:\n- user: {token: abc}\n"),
             home.write(f"{home.home}/.docker/config.json", '{"auths":{"x":{"auth":"dXNlcjpwYXNz"}}}\n'),
             home.write(f"{home.home}/.config/gh/hosts.yml", "github.com:\n  oauth_token: gho_x\n")]
    home.transcript([(repo, f, "update") for f in leaks])
    r = home.sh("scan")
    assert "Would copy" not in r.stdout, r.stdout


@pytest.mark.xfail(strict=True, reason="FINDING S2: secret rules are case-sensitive and miss certificates "
                                        "(.crt/.cer/.der) and Terraform state backups")
@pytest.mark.parametrize("name", ["SECRET.md", "Prod-SECRETS.yaml", "CREDENTIALS.json", "server.crt", "client.cer",
                                  "ca.der", "terraform.tfstate.backup"])
def test_secret_names(home, name):
    repo = os.path.join(home.work, "p")
    f = home.write(f"{repo}/{name}", "s\n")
    home.transcript([(repo, f, "create")])
    home.sh("scan")
    plan = open(os.path.join(home.dir, "state", "plan.tsv")).read()
    assert f"skip\tsecret\t{f}" in plan, plan


def test_real_claude_code_transcript(member):
    """A transcript written by the real `claude` 2.1 CLI (created in /home/claude/qa-sync/cc by setup)."""
    cc = "/home/claude/qa-sync/cc"
    if not os.path.isdir(f"{cc}/home/.claude/projects"):
        pytest.skip("no real transcript; see report for how it was made")
    h = Home("real")
    shutil.copytree(f"{cc}/home/.claude", h.claude, dirs_exist_ok=True)
    env = h.env(CLAUDE_CONFIG_DIR=h.claude)
    r = subprocess.run(["sh", SYNC, "scan"], env=env, capture_output=True, text=True)
    assert "Would copy 2 file(s)" in r.stdout and "projects/myproj" in r.stdout, r.stdout
    plan = open(os.path.join(h.dir, "state", "plan.tsv")).read()
    assert "notes ünï.md" in plan and "sub/data.csv" in plan and "/etc/hostname" not in plan


# ---- runs: unchanged, versions, retries ---------------------------------------------------

def test_unchanged_not_resent_changed_is_v2(home, member):
    assert home.link()[0] == 0
    repo = os.path.join(home.work, home.project)
    f = home.write(f"{repo}/a.md", "one\n")
    home.transcript([(repo, f, "create")])
    assert "Done: 1 copied." in home.sh("run").stdout
    assert os.path.exists(os.path.join(home.dir, "state", "last-run"))
    r = home.sh("run")
    assert "Done: 0 copied." in r.stdout and "Checking" not in r.stdout  # old history isn't read again
    time.sleep(1.1)
    home.write(f, "two\n")
    home.transcript([(repo, f, "update")])
    r = home.sh("run")
    assert "Done: 1 copied." in r.stdout, r.stdout
    path = f"/users/{MEMBER[0]}/projects/{home.project}/a.md"
    assert max(v["version"] for v in member.get("/api/fs/versions", params={"path": path}).json()["versions"]) == 2
    # Same content touched again by Claude: not resent.
    time.sleep(1.1)
    home.transcript([(repo, f, "update")])
    assert "Done: 0 copied." in home.sh("run").stdout


def test_failures_are_retried(home, member):
    assert home.link()[0] == 0
    good = home.conf()["server"]
    repo = os.path.join(home.work, home.project)
    f = home.write(f"{repo}/a.md", "one\n")
    home.transcript([(repo, f, "create")])
    home.set_conf("server", "http://127.0.0.1:9")
    r = home.sh("run")
    assert "Couldn't reach the server" in r.stdout and "tried again next time" in r.stdout
    assert not os.path.exists(os.path.join(home.dir, "state", "last-run"))
    home.set_conf("server", good)
    r = home.sh("run")
    assert "Done: 1 copied." in r.stdout, r.stdout


@pytest.mark.xfail(strict=True, reason="FINDING S4: a file the server always refuses (aux.md, a:b.md, name ending "
                                        "in '.') blocks the 'last complete run' marker forever")
def test_permanently_refused_file_does_not_block_progress(home, member):
    assert home.link()[0] == 0
    repo = os.path.join(home.work, home.project)
    bad = home.write(f"{repo}/aux.md", "x\n")
    ok = home.write(f"{repo}/ok.md", "x\n")
    home.transcript([(repo, bad, "create"), (repo, ok, "create")])
    home.sh("run")
    home.sh("run")
    assert os.path.exists(os.path.join(home.dir, "state", "last-run"))


def test_turned_off_mid_run(home, member):
    assert home.link()[0] == 0
    repo = os.path.join(home.work, home.project)
    f = home.write(f"{repo}/a.md")
    home.transcript([(repo, f, "create")])
    assert member.delete(f"/api/tokens/{home.token_id(member)}").status_code == 200
    r = home.sh("run")
    assert r.returncode == 1 and "link was turned off" in r.stderr
    assert not os.path.isdir(os.path.join(home.dir, "lock"))
    # Quiet hook run after 401: exits 0, leaves no lock.
    r = home.sh("hook-run")
    assert r.returncode == 0 and r.stdout == "" and not os.path.isdir(os.path.join(home.dir, "lock"))


def test_inbox_name_collision_keeps_only_last(home, member):
    """Informational (S8): two different loose files with the same name share one inbox file."""
    assert home.link()[0] == 0
    n = f"notes-{home.project}.md"
    a = home.write(f"{home.home}/a/{n}", "first\n")
    b = home.write(f"{home.home}/b/{n}", "second\n")
    home.transcript([(home.home, a, "create"), (home.home, b, "create")])
    assert "Done: 2 copied." in home.sh("run").stdout
    vers = member.get("/api/fs/versions", params={"path": f"/users/{MEMBER[0]}/inbox/{n}"}).json()["versions"]
    assert len(vers) == 2


@pytest.mark.xfail(strict=True, reason="FINDING S7: re-linking to another account/server keeps sent.tsv, so files "
                                        "already sent elsewhere are never sent to the new account")
def test_relink_to_another_account_sends_again(home, member):
    assert home.link()[0] == 0
    repo = os.path.join(home.work, home.project)
    f = home.write(f"{repo}/a.md")
    home.transcript([(repo, f, "create")])
    assert "Done: 1 copied." in home.sh("run").stdout
    assert home.link(who=ADMIN)[0] == 0
    time.sleep(1.1)
    home.transcript([(repo, f, "update")])
    r = home.sh("run")
    assert "Done: 1 copied." in r.stdout, r.stdout


@pytest.mark.xfail(strict=True, reason="FINDING S10: the sync token is passed on curl's command line, readable by "
                                        "other local users via ps / /proc")
def test_token_not_on_command_line(home, member):
    assert home.link()[0] == 0
    bindir = os.path.join(home.root, "bin")
    os.makedirs(bindir)
    log = os.path.join(home.root, "argv.log")
    real = shutil.which("curl")
    with open(os.path.join(bindir, "curl"), "w") as fh:
        fh.write(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexec "{real}" "$@"\n')
    os.chmod(os.path.join(bindir, "curl"), 0o755)
    repo = os.path.join(home.work, home.project)
    f = home.write(f"{repo}/a.md")
    home.transcript([(repo, f, "create")])
    home.sh("run", env=home.env(PATH=f"{bindir}:{os.environ['PATH']}"))
    assert home.conf()["token"] not in open(log).read()


def test_odd_file_names_dont_break_the_run(home, member):
    """Tab/newline in a file name, a double quote, and a backslash: the others still sync."""
    assert home.link()[0] == 0
    repo = os.path.join(home.work, home.project)
    odd = [home.write(f"{repo}/tab\there.md"), home.write(f"{repo}/new\nline.md"), home.write(f'{repo}/q"uote.md'),
           home.write(f"{repo}/back\\slash.md")]
    ok = home.write(f"{repo}/fine.md")
    home.transcript([(repo, p, "create") for p in odd] + [(repo, ok, "create")])
    r = home.sh("run", timeout=60)
    assert r.returncode == 0
    assert f"/users/{MEMBER[0]}/projects/{home.project}/fine.md" in server_files(member, f"/users/{MEMBER[0]}/projects/{home.project}")


# ---- commands -----------------------------------------------------------------------------

def test_off_on_status_unlink(home, member):
    assert home.sh("status").stdout.strip() == "Not linked."
    r = home.sh("off")
    assert r.returncode == 1 and "Not linked" in r.stderr
    assert home.link()[0] == 0
    assert "Automatic sync: on" in home.sh("status").stdout
    assert home.sh("off").returncode == 0 and home.conf()["enabled"] == "off"
    assert "Automatic sync: off" in home.sh("status").stdout
    home.sh("on")
    assert home.conf()["enabled"] == "on"
    assert open(os.path.join(home.dir, "sync.conf")).read().count("enabled=") == 1
    tid = home.token_id(member)
    r = home.sh("unlink")
    assert r.returncode == 0 and "Connect page" in r.stdout
    assert not os.path.exists(os.path.join(home.dir, "sync.conf")) and not os.path.exists(os.path.join(home.dir, "state", "sent.tsv"))
    assert home.sh("status").stdout.strip() == "Not linked."
    # unlink only forgets locally: the token still shows on the Connect page until turned off there.
    assert any(t["id"] == tid for t in member.get("/api/tokens").json()["tokens"])
    member.delete(f"/api/tokens/{tid}")


def test_conf_is_private(home):
    assert home.link()[0] == 0
    st = os.stat(os.path.join(home.dir, "sync.conf"))
    assert st.st_mode & 0o077 == 0


def test_link_denied_and_unknown_command(home):
    rc, out = home.link(decision="deny")
    assert rc == 1 and "declined" in out and not os.path.exists(os.path.join(home.dir, "sync.conf"))
    r = home.sh("frobnicate")
    assert r.returncode == 1 and "Unknown command" in r.stderr


def test_one_run_at_a_time(home):
    assert home.link()[0] == 0
    os.makedirs(os.path.join(home.dir, "lock"))
    r = home.sh("run")
    assert r.returncode == 1 and "Another sync is running" in r.stderr
    r = home.sh("hook-run")
    assert r.returncode == 0 and r.stdout == r.stderr == ""
    assert os.path.isdir(os.path.join(home.dir, "lock"))  # someone else's lock is left alone
    old = time.time() - 31 * 60
    os.utime(os.path.join(home.dir, "lock"), (old, old))
    r = home.sh("run")
    assert r.returncode == 0 and not os.path.isdir(os.path.join(home.dir, "lock"))


# ---- the hook ----------------------------------------------------------------------------

def hook_cmd():
    with open(f"{PLUGIN}/hooks/hooks.json") as f:
        h = json.load(f)["hooks"]
    cmds = {x["command"] for ev in h.values() for m in ev for x in m["hooks"]}
    assert len(cmds) == 1
    return cmds.pop().replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN)


def run_hook(home, timeout=10):
    t = time.time()
    r = subprocess.run(["bash", "-c", hook_cmd()], env=home.env(), capture_output=True, text=True, timeout=timeout,
                       cwd=home.root)
    return r, time.time() - t


def test_hook_shape():
    with open(f"{PLUGIN}/hooks/hooks.json") as f:
        h = json.load(f)["hooks"]
    assert set(h) == {"SessionStart", "Stop"}
    for ev in h.values():
        for m in ev:
            for x in m["hooks"]:
                assert x["type"] == "command" and x.get("async") is True and x.get("timeout", 0) >= 60


def test_hook_when_not_linked(home):
    f = home.write("p/a.md")
    home.transcript([(os.path.dirname(f), f, "create")])
    r, took = run_hook(home)
    assert r.returncode == 0 and r.stdout.strip() == "ok" and took < 2
    time.sleep(1)
    assert not os.path.exists(os.path.join(home.dir, "state", "plan.tsv"))
    assert not os.path.exists(os.path.join(home.dir, "sync.conf"))


@pytest.mark.xfail(strict=True, reason="FINDING S13: the hook creates ~/.artifact-buckets/state on every computer "
                                        "with the plugin, even when never linked")
def test_hook_when_not_linked_leaves_no_trace(home):
    run_hook(home)
    assert not os.path.exists(home.dir)


def test_hook_when_off(home, member):
    assert home.link()[0] == 0
    home.sh("off")
    repo = os.path.join(home.work, home.project)
    f = home.write(f"{repo}/a.md")
    home.transcript([(repo, f, "create")])
    r, took = run_hook(home)
    assert r.returncode == 0 and took < 2
    time.sleep(2)
    assert server_files(member, f"/users/{MEMBER[0]}/projects/{home.project}") == []


def test_hook_returns_at_once_and_copies_in_background(home, member):
    assert home.link()[0] == 0
    repo = os.path.join(home.work, home.project)
    files = [home.write(f"{repo}/f{i}.md", f"{i}\n") for i in range(25)]
    home.transcript([(repo, f, "create") for f in files])
    r, took = run_hook(home)
    assert r.returncode == 0 and took < 1.5, took
    # stdout of the hook is only "ok" (plus a harmless 'powershell: not found' on stderr).
    assert r.stdout.strip() == "ok"
    deadline = time.time() + 30
    while time.time() < deadline:
        if len(server_files(member, f"/users/{MEMBER[0]}/projects/{home.project}")) == 25 and \
                not os.path.isdir(os.path.join(home.dir, "lock")):
            break
        time.sleep(0.5)
    assert len(server_files(member, f"/users/{MEMBER[0]}/projects/{home.project}")) == 25
    time.sleep(0.5)
    assert not os.path.isdir(os.path.join(home.dir, "lock"))
    assert os.path.exists(os.path.join(home.dir, "state", "last-run"))


def test_hook_survives_parent_exit(home, member):
    """Closing Claude Code (killing the hook's process group) mustn't cut the copy short."""
    assert home.link()[0] == 0
    repo = os.path.join(home.work, home.project)
    files = [home.write(f"{repo}/g{i}.md", f"{i}\n") for i in range(15)]
    home.transcript([(repo, f, "create") for f in files])
    p = subprocess.Popen(["bash", "-c", hook_cmd()], env=home.env(), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, cwd=home.root)
    p.wait(timeout=5)
    try:
        os.killpg(p.pid, 9)
    except ProcessLookupError:
        pass
    deadline = time.time() + 30
    while time.time() < deadline and len(server_files(member, f"/users/{MEMBER[0]}/projects/{home.project}")) < 15:
        time.sleep(0.5)
    assert len(server_files(member, f"/users/{MEMBER[0]}/projects/{home.project}")) == 15
