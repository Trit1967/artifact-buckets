"""Auto-sync: files Claude Code makes on a person's computer land in their folders by themselves.

A small script that ships with the Claude Code plugin (plugin/sync/sync.ps1 and sync.sh) reads Claude Code's
own history on the computer, finds the files Claude created or edited that aren't kept in git, and uploads
them. It runs once to bring in existing work, then after every Claude Code reply.

The computer is linked once, like signing in to a TV app: the script asks for a code, the browser opens
/link?code=…, the person (already signed in) presses Allow, and the script receives a sync token.

A sync token can only add files under the person's own projects/ and inbox/ folders, through PUT
/api/sync/file. It can't read, list, delete, share or do anything an admin can, and it shows on the
Connect page as a computer the person (or an admin) can turn off.
"""
from __future__ import annotations

import base64
import datetime as dt
import re
import secrets
import unicodedata
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import audit, config, fs, paths
from .api import current, get_db
from .db import Base, File, OAuthCode, Token, User, utcnow
from .errors import BadRequest, Forbidden, NotFound, Unauthorized
from .perms import Actor
from .security import SESSION_COOKIE, issue_token, resolve_token, sha256_hex

router = APIRouter(prefix="/api/sync")

LINK_MINUTES = 10
TOKEN_DAYS = 365
# Code people compare between the terminal and the browser: no 0/O/1/I.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class SyncLink(Base):
    """A pending "link this computer" request (device authorization)."""
    __tablename__ = "sync_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    poll_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    computer: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | denied | done
    user_id: Mapped[int | None] = mapped_column(Integer)
    ip: Mapped[str | None] = mapped_column(String(64))  # where the computer asked from
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class LinkPlan(Base):
    """What a link request covers. The computer sends the folders it would copy (names and counts only, no
    file contents); the person picks which to keep and whether to sync at all. Approving also signs in
    Claude Code on that computer: the next sign-in from the same browser within ten minutes needs no
    second click. Its own table, so existing databases pick it up without a migration."""
    __tablename__ = "sync_link_plans"
    link_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folders: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    keep: Mapped[list[str] | None] = mapped_column(JSON)
    hidden: Mapped[list[str] | None] = mapped_column(JSON)  # folders the page couldn't show: never copied
    sync: Mapped[bool] = mapped_column(Boolean, default=True)
    session_hash: Mapped[str | None] = mapped_column(String(64), index=True)  # the browser session that approved
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    oauth_used: Mapped[bool] = mapped_column(Boolean, default=False)
    client_id: Mapped[str | None] = mapped_column(String(64))  # the Claude Code sign-in it approved
    token_id: Mapped[int | None] = mapped_column(Integer, index=True)  # the sync token it issued


class Welcomed(Base):
    """People who have finished (or skipped) the first-run welcome. Its own table, so existing databases
    pick it up at startup without a migration."""
    __tablename__ = "welcomed"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


def welcomed(db: Session, user_id: int) -> bool:
    return db.get(Welcomed, user_id) is not None


def _code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _clean_computer(name: str) -> str:
    # Drop control, format (bidi, zero-width, soft hyphen...) and line/paragraph separator characters.
    name = "".join(ch for ch in unicodedata.normalize("NFKC", name or "")
                   if unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn") and ch != "\u180e")
    name = re.sub(r"\s+", " ", name).strip()
    return (name or "This computer")[:64]


def _link_row(db: Session, code: str) -> SyncLink:
    row = db.scalar(select(SyncLink).where(SyncLink.user_code == code.strip().upper()))
    if row is None or row.created_at < utcnow() - dt.timedelta(minutes=LINK_MINUTES):
        raise NotFound("This code has expired. Start again on the computer.")
    return row


# ---- the computer's side ------------------------------------------------------------

class FolderInfo(BaseModel):
    folder: str = Field(max_length=1024)
    files: int = 0
    bytes: int = 0
    source: str = Field("", max_length=2048)   # the folder on the computer it comes from, to recognise it


class StartBody(BaseModel):
    computer: str = Field("", max_length=4096)   # trimmed to 64 below
    folders: list[FolderInfo] = Field([], max_length=2000)


_FOLDER = re.compile(r"inbox|projects/[^/\\\x00-\x1f]{1,255}")
MAX_SHOWN = 200


def _printable(text: str, limit: int) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text or "")
                   if unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"))[:limit]


def _clean_folders(items: list[FolderInfo]) -> tuple[list[dict[str, Any]], list[str]]:
    """The folders to show, and the ones that can't be shown (odd names, or past the first 200). Those are
    never copied: the person can only agree to what they saw."""
    out: list[dict[str, Any]] = []
    hidden: list[str] = []
    seen: set[str] = set()
    for it in items:
        f = it.folder
        if f in seen:
            continue
        seen.add(f)
        ok = _FOLDER.fullmatch(f) and f not in ("projects/.", "projects/..")
        if not ok or len(out) >= MAX_SHOWN:
            if f.startswith(("projects/", "inbox")):
                hidden.append(f)
            continue
        label = "Loose files" if f == "inbox" else (_printable(f[len("projects/"):], 255) or "(unnamed)")
        out.append({"folder": f, "label": label, "files": max(0, min(int(it.files), 10_000_000)),
                    "bytes": max(0, min(int(it.bytes), 1 << 50)), "source": _printable(it.source, 400)})
    return out, hidden


@router.post("/link/start")
def link_start(body: StartBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    # Old requests are only useful for ten minutes.
    for old in db.scalars(select(SyncLink).where(SyncLink.created_at < utcnow() - dt.timedelta(hours=1))):
        p = db.get(LinkPlan, old.id)
        if p is not None:
            db.delete(p)
        db.delete(old)
    db.flush()
    poll = secrets.token_urlsafe(32)
    code = _code()
    while db.scalar(select(SyncLink).where(SyncLink.user_code == code)) is not None:
        code = _code()
    row = SyncLink(user_code=code, poll_hash=sha256_hex(poll), computer=_clean_computer(body.computer),
                   ip=request.client.host if request.client else None)
    db.add(row)
    db.flush()
    stale = db.get(LinkPlan, row.id)   # SQLite can hand out a deleted row's id again
    if stale is not None:
        db.delete(stale)
        db.flush()
    shown, hidden = _clean_folders(body.folders)
    db.add(LinkPlan(link_id=row.id, folders=shown, hidden=hidden))
    db.commit()
    base = config.settings.base_url
    return {"code": code, "poll": poll, "link": f"{base}/link?code={code}", "interval": 2, "expires_in": LINK_MINUTES * 60}


class PollBody(BaseModel):
    poll: str


@router.post("/link/poll")
def link_poll(body: PollBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.scalar(select(SyncLink).where(SyncLink.poll_hash == sha256_hex(body.poll)))
    if row is None or row.created_at < utcnow() - dt.timedelta(minutes=LINK_MINUTES):
        return {"status": "expired"}
    if row.status != "approved":
        return {"status": "done" if row.status == "done" else row.status}
    u = db.get(User, row.user_id)
    if u is None or u.status != "active":
        return {"status": "denied"}
    # Hand the token out once, even when two polls arrive together.
    won = db.execute(update(SyncLink).where(SyncLink.id == row.id, SyncLink.status == "approved").values(status="done"))
    if won.rowcount != 1:
        db.rollback()
        return {"status": "done"}
    plan = db.get(LinkPlan, row.id)
    home = f"/users/{u.username}"
    syncing = plan is None or plan.sync
    name = f"Sync on {row.computer}"
    # Linking the same computer again replaces its earlier link, and keeps its Claude Code sign-in attached.
    client_id = plan.client_id if plan is not None else None
    for old in db.scalars(select(Token).where(Token.user_id == u.id, Token.kind == "sync", Token.name == name,
                                              Token.revoked.is_(False))):
        old.revoked = True
        client_id = client_id or old.client_id
    # Connected without syncing still gets a computer key, so removing Artifact Buckets from the computer can
    # end its sign-in here; it just can't add files (scope "read").
    raw, tok = issue_token(db, u, name=name, kind="sync", scope="write" if syncing else "read",
                           folders=[f"{home}/projects", f"{home}/inbox"] if syncing else [],
                           ttl=dt.timedelta(days=TOKEN_DAYS))
    tok.client_id = client_id
    if plan is not None:
        plan.token_id = tok.id
    audit.log(db, None, "sync.link" if syncing else "sync.connect", row.computer, via="api", user=u.username)
    db.commit()
    out = {"token": raw, "user": u.username, "home": home, "server": config.settings.base_url}
    if not syncing:
        return {"status": "connected", **out}
    skip: list[str] = list(plan.hidden or []) if plan is not None else []
    if plan is not None and plan.keep is not None:
        skip += [f["folder"] for f in plan.folders if f["folder"] not in plan.keep]
    # One base64 line per folder, so names with commas, brackets or quotes survive the shell script.
    return {"status": "approved", **out, "max_file_bytes": config.settings.max_file_bytes,
            "skip": skip, "skip_b64": ",".join(base64.b64encode(f.encode()).decode() for f in skip)}


@router.post("/unlink")
def unlink(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Called by the computer when Artifact Buckets is removed from it: ends its sync link and the Claude Code
    sign-in that was approved with it. Files already copied stay."""
    actor = _sync_actor(request, db)
    tok = actor.token
    ended = 0
    if tok is not None and tok.client_id:
        for t in db.scalars(select(Token).where(Token.user_id == actor.user.id, Token.client_id == tok.client_id,
                                                Token.kind.in_(("oauth_access", "oauth_refresh")),
                                                Token.revoked.is_(False))):
            t.revoked = True
            ended += 1
    if tok is not None:
        tok.revoked = True
    audit.log(db, actor, "sync.unlink", tok.name.removeprefix("Sync on ") if tok else "")
    db.commit()
    return {"ok": True, "signed_out": ended > 0}


def _sync_actor(request: Request, db: Session) -> Actor:
    auth = request.headers.get("authorization", "")
    tok = resolve_token(db, auth[7:].strip(), kinds=("sync",)) if auth.lower().startswith("bearer ") else None
    if tok is None:
        raise Unauthorized("This computer isn't linked, or its link was turned off")
    u = db.get(User, tok.user_id)
    if u is None or u.status != "active":
        raise Unauthorized("Account is not active")
    tok.last_used = utcnow()
    return Actor(user=u, via="sync", token=tok, ip=request.client.host if request.client else None)


def _b64(value: str | None, what: str) -> str:
    if not value:
        raise BadRequest(f"{what} is required")
    try:
        pad = "=" * (-len(value) % 4)
        return base64.b64decode(value + pad, altchars=b"-_" if ("-" in value or "_" in value) else None,
                                validate=False).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise BadRequest(f"{what} isn't valid base64 text") from e


def destination(username: str, where: str, project: str, rel: str) -> str:
    """Where a synced file goes. Raises BadRequest for anything outside projects/ and inbox/."""
    rel = rel.replace("\\", "/").strip("/")
    if not rel:
        raise BadRequest("File path is empty")
    if where == "inbox":
        dest = f"/users/{username}/inbox/{rel}"
    elif where == "projects":
        project = project.strip().strip("/")
        if not project or "/" in project or "\\" in project:
            raise BadRequest("Project name must be one folder name")
        dest = f"/users/{username}/projects/{project}/{rel}"
    else:
        raise BadRequest("where must be projects or inbox")
    dest = paths.normalize(dest)  # refuses .., dot-names, bad characters
    home = f"/users/{username}"
    if not (paths.is_under(dest, f"{home}/projects") or paths.is_under(dest, f"{home}/inbox")):
        raise Forbidden("Sync only adds files to your projects and inbox")
    return dest


def _mine(f: File | None, uid: int) -> bool:
    return f is None or (f.source == "sync" and f.saved_by == uid)


def _own_place(db: Session, uid: int, dest: str, computer: str) -> str:
    """Sync only adds files: it may update a file it put there itself, but never replaces one the person
    (or anyone) made another way. That file stays as it is and the synced copy goes beside it."""
    if _mine(fs.file_row(db, dest), uid):
        return dest
    folder, name = dest.rsplit("/", 1)
    stem, dot, ext = name.rpartition(".")
    if not stem:
        stem, dot, ext = name, "", ""
    tag = re.sub(r"[^\w .-]", "", computer).strip() or "this computer"
    for n in range(1, 100):
        cand = f"{folder}/{stem} (from {tag}{'' if n == 1 else f' {n}'}){dot}{ext}"
        if _mine(fs.file_row(db, cand), uid):
            return cand
    raise BadRequest("Too many copies of this file already")


@router.put("/file")
async def put_file(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    actor = _sync_actor(request, db)
    if actor.token is None or actor.token.scope != "write":
        raise Forbidden("This computer is connected without syncing")
    h = request.headers
    where = h.get("x-sync-where", "projects")
    project = _b64(h.get("x-sync-project"), "Project") if where == "projects" else ""
    rel = _b64(h.get("x-sync-path"), "File path")
    dest = destination(actor.user.username, where, project, rel)
    data = await request.body()
    computer = actor.token.name.removeprefix("Sync on ") if actor.token else ""
    dest = _own_place(db, actor.user.id, dest, computer)
    session_id = h.get("x-sync-session") or None
    if session_id and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", session_id):
        session_id = None
    info = fs.save_file(db, actor, dest, data, fs.SaveMeta(source="sync", project=project or None, session_id=session_id))
    if not info.get("unchanged"):
        f = fs.file_row(db, dest)
        if f is not None and computer:
            f.tags = sorted(set(f.tags or []) | {"synced"})[:20]
            db.commit()
    return {"path": dest, "version": info.get("version"), "unchanged": bool(info.get("unchanged")),
            "note": info.get("note")}


# ---- the person's side (web) --------------------------------------------------------

def _web_actor(request: Request, db: Session) -> Actor:
    return current(request, db)  # the web session check, with CSRF on writes


@router.get("/link/{code}")
def link_info(code: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    actor = _web_actor(request, db)
    if actor.via != "web":
        raise Forbidden("Approve this in the web app")
    row = _link_row(db, code)
    here = request.client.host if request.client else None
    plan = db.get(LinkPlan, row.id)
    return {"code": row.user_code, "computer": row.computer, "status": row.status,
            "same_network": bool(row.ip and here and row.ip == here), "from_ip": row.ip,
            "age_seconds": int((utcnow() - row.created_at).total_seconds()),
            "folders": plan.folders if plan else [], "hidden": len(plan.hidden or []) if plan else 0,
            "home": f"/users/{actor.user.username}"}


class DecideBody(BaseModel):
    keep: list[str] | None = None   # folders to copy; None = all of them


@router.post("/link/{code}/{decision}")
def link_decide(code: str, decision: str, request: Request, body: DecideBody | None = None,
                db: Session = Depends(get_db)) -> dict[str, Any]:
    """allow: connect Claude Code and sync (the folders in keep); connect: connect Claude Code only; deny."""
    actor = _web_actor(request, db)
    if actor.via != "web":
        raise Forbidden("Approve this in the web app")
    row = _link_row(db, code)
    if row.status != "pending":
        raise BadRequest("This code was already used")
    if decision not in ("allow", "connect", "deny"):
        raise NotFound("Not found")
    row.status = "denied" if decision == "deny" else "approved"
    row.user_id = actor.user.id
    plan = db.get(LinkPlan, row.id)
    if plan is None:
        plan = LinkPlan(link_id=row.id, folders=[])
        db.add(plan)
    if decision != "deny":
        plan.sync = decision == "allow"
        if body is not None and body.keep is not None:
            known = {f["folder"] for f in plan.folders}
            plan.keep = [k for k in body.keep if k in known]
        plan.session_hash = sha256_hex(request.cookies.get(SESSION_COOKIE) or "")
        plan.approved_at = utcnow()
        # Claude Code on this computer is already signed in (connecting again to change syncing): there's no
        # sign-in to wave through, so don't open a window for one.
        if _signed_in(db, actor.user.id, row.computer):
            plan.oauth_used = True
    audit.log(db, actor, {"allow": "sync.approve", "connect": "sync.connect_only", "deny": "sync.deny"}[decision],
              row.computer)
    db.commit()
    return {"status": row.status, "computer": row.computer, "sync": decision == "allow"}


def _signed_in(db: Session, user_id: int, computer: str) -> bool:
    prev = db.scalars(select(Token.client_id).where(Token.user_id == user_id, Token.kind == "sync",
                                                    Token.name == f"Sync on {computer}", Token.revoked.is_(False),
                                                    Token.client_id.is_not(None))).all()
    if not prev:
        return False
    if db.scalar(select(Token.id).where(Token.user_id == user_id, Token.client_id.in_(prev),
                                        Token.kind == "oauth_refresh", Token.revoked.is_(False))) is not None:
        return True
    # Or the sign-in is still being finished (a code was handed out and not yet swapped).
    return db.scalar(select(OAuthCode.code_hash).where(OAuthCode.user_id == user_id, OAuthCode.client_id.in_(prev),
                                                       OAuthCode.used.is_(False), OAuthCode.expires_at > utcnow())) is not None


def recent_plan(db: Session, user_id: int, session_cookie: str | None) -> LinkPlan | None:
    """The computer this browser session approved in the last ten minutes, if any."""
    if not session_cookie:
        return None
    since = utcnow() - dt.timedelta(minutes=LINK_MINUTES)
    for plan in db.scalars(select(LinkPlan).where(LinkPlan.session_hash == sha256_hex(session_cookie),
                                                  LinkPlan.approved_at >= since).order_by(LinkPlan.approved_at.desc())):
        row = db.get(SyncLink, plan.link_id)
        if row is not None and row.user_id == user_id and row.status in ("approved", "done"):
            return plan
    return None


def preapproved(db: Session, user_id: int, session_cookie: str | None, client_created: dt.datetime) \
        -> tuple[LinkPlan, SyncLink] | None:
    """A computer this browser session approved in the last ten minutes whose Claude Code sign-in hasn't
    happened yet, for a sign-in client registered after that approval (Claude Code registers when it starts
    signing in). Claims it, once: the OAuth authorize step uses it to skip the second click."""
    if not session_cookie:
        return None
    since = utcnow() - dt.timedelta(minutes=LINK_MINUTES)
    for plan in db.scalars(select(LinkPlan).where(LinkPlan.session_hash == sha256_hex(session_cookie),
                                                  LinkPlan.oauth_used.is_(False), LinkPlan.approved_at >= since)
                           .order_by(LinkPlan.approved_at.desc())):
        row = db.get(SyncLink, plan.link_id)
        if row is None or row.user_id != user_id or row.status not in ("approved", "done"):
            continue
        if plan.approved_at is None or client_created < plan.approved_at - dt.timedelta(seconds=5):
            continue
        won = db.execute(update(LinkPlan).where(LinkPlan.link_id == plan.link_id, LinkPlan.oauth_used.is_(False))
                         .values(oauth_used=True))
        if won.rowcount != 1:
            continue
        db.refresh(plan)
        return plan, row
    return None


def attach_client(db: Session, plan: LinkPlan, client_id: str) -> None:
    """Remember which Claude Code sign-in came with this computer, so removing it from the computer ends both."""
    plan.client_id = client_id
    if plan.token_id:
        tok = db.get(Token, plan.token_id)
        if tok is not None:
            tok.client_id = client_id


@router.post("/welcome/done")
def welcome_done(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    actor = _web_actor(request, db)
    if not welcomed(db, actor.user.id):
        db.add(Welcomed(user_id=actor.user.id))
        db.commit()
    return {"ok": True}


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """What the welcome flow and the Connect page show: linked computers and what has arrived."""
    actor = _web_actor(request, db)
    uid = actor.user.id
    computers = [{"id": t.id, "name": t.name.removeprefix("Sync on "),
                  "linked_at": t.created_at.isoformat() + "Z",
                  "last_sync": t.last_used.isoformat() + "Z" if t.last_used else None}
                 for t in db.scalars(select(Token).where(Token.user_id == uid, Token.kind == "sync", Token.scope == "write",
                                                         Token.revoked.is_(False)).order_by(Token.created_at.desc()))]
    q = select(File).where(File.saved_by == uid, File.source == "sync")
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    size = int(db.scalar(select(func.coalesce(func.sum(File.size), 0)).where(File.saved_by == uid, File.source == "sync")) or 0)
    home = f"/users/{actor.user.username}"
    groups: dict[str, dict[str, Any]] = {}
    for p, sz in db.execute(select(File.path, File.size).where(File.saved_by == uid, File.source == "sync")):
        rest = p[len(home) + 1:] if p.startswith(home + "/") else p
        parts = rest.split("/")
        key = f"{home}/projects/{parts[1]}" if parts[0] == "projects" and len(parts) > 2 else f"{home}/inbox"
        g = groups.setdefault(key, {"folder": key, "files": 0, "bytes": 0})
        g["files"] += 1
        g["bytes"] += sz or 0
    recent = [{"path": f.path, "size": f.size, "at": f.updated_at.isoformat() + "Z", "version": f.version}
              for f in db.scalars(q.order_by(File.updated_at.desc()).limit(12))]
    return {"computers": computers, "files": total, "bytes": size,
            "folders": sorted(groups.values(), key=lambda g: -g["files"]), "recent": recent}
