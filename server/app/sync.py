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
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import audit, config, fs, paths
from .api import current, get_db
from .db import Base, File, Token, User, utcnow
from .errors import BadRequest, Forbidden, NotFound, Unauthorized
from .perms import Actor
from .security import issue_token, resolve_token, sha256_hex

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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


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
    name = re.sub(r"[\x00-\x1f\x7f​-‏‪-‮⁠-⁩﻿]", "", name or "").strip()
    return (name or "This computer")[:64]


def _link_row(db: Session, code: str) -> SyncLink:
    row = db.scalar(select(SyncLink).where(SyncLink.user_code == code.strip().upper()))
    if row is None or row.created_at < utcnow() - dt.timedelta(minutes=LINK_MINUTES):
        raise NotFound("This code has expired. Start again on the computer.")
    return row


# ---- the computer's side ------------------------------------------------------------

class StartBody(BaseModel):
    computer: str = ""


@router.post("/link/start")
def link_start(body: StartBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    # Old requests are only useful for ten minutes.
    for old in db.scalars(select(SyncLink).where(SyncLink.created_at < utcnow() - dt.timedelta(hours=1))):
        db.delete(old)
    poll = secrets.token_urlsafe(32)
    code = _code()
    while db.scalar(select(SyncLink).where(SyncLink.user_code == code)) is not None:
        code = _code()
    db.add(SyncLink(user_code=code, poll_hash=sha256_hex(poll), computer=_clean_computer(body.computer)))
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
    home = f"/users/{u.username}"
    raw, tok = issue_token(db, u, name=f"Sync on {row.computer}", kind="sync", scope="write",
                           folders=[f"{home}/projects", f"{home}/inbox"], ttl=dt.timedelta(days=TOKEN_DAYS))
    row.status = "done"
    audit.log(db, None, "sync.link", row.computer, via="api", user=u.username)
    db.commit()
    return {"status": "approved", "token": raw, "user": u.username, "home": home, "server": config.settings.base_url,
            "max_file_bytes": config.settings.max_file_bytes}


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


@router.put("/file")
async def put_file(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    actor = _sync_actor(request, db)
    h = request.headers
    where = h.get("x-sync-where", "projects")
    project = _b64(h.get("x-sync-project"), "Project") if where == "projects" else ""
    rel = _b64(h.get("x-sync-path"), "File path")
    dest = destination(actor.user.username, where, project, rel)
    data = await request.body()
    computer = actor.token.name.removeprefix("Sync on ") if actor.token else ""
    info = fs.save_file(db, actor, dest, data, fs.SaveMeta(source="sync", project=project or None,
                                                          session_id=(h.get("x-sync-session") or None)))
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
    _web_actor(request, db)
    row = _link_row(db, code)
    return {"code": row.user_code, "computer": row.computer, "status": row.status}


@router.post("/link/{code}/{decision}")
def link_decide(code: str, decision: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    actor = _web_actor(request, db)
    if actor.via != "web":
        raise Forbidden("Approve this in the web app")
    row = _link_row(db, code)
    if row.status != "pending":
        raise BadRequest("This code was already used")
    if decision not in ("allow", "deny"):
        raise NotFound("Not found")
    row.status = "approved" if decision == "allow" else "denied"
    row.user_id = actor.user.id
    audit.log(db, actor, "sync.approve" if decision == "allow" else "sync.deny", row.computer)
    db.commit()
    return {"status": row.status, "computer": row.computer}


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
                 for t in db.scalars(select(Token).where(Token.user_id == uid, Token.kind == "sync",
                                                         Token.revoked.is_(False)).order_by(Token.created_at.desc()))]
    q = select(File).where(File.saved_by == uid, File.source == "sync")
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    size = db.scalar(select(func.coalesce(func.sum(File.size), 0)).where(File.saved_by == uid, File.source == "sync")) or 0
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
