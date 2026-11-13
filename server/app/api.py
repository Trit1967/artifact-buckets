"""REST API used by the web app (cookie session + CSRF header) and by scripts (Bearer token)."""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File as UploadFileField, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import accounts, acl, audit, config, directory, fs, mcp, paths, routing, storage, zfs
from .db import Audit, File, Folder, Grant, Group, Membership, RoutingRule, Token, User, get_setting, new_session, set_setting, utcnow
from .errors import BadRequest, Forbidden, NotFound, Unauthorized
from .oauth import set_session_cookie
from .perms import Actor, role_for
from .security import SESSION_COOKIE, consteq, create_web_session, end_web_session, issue_token, load_web_session, \
    resolve_token, sign, unsign, verify_password

router = APIRouter(prefix="/api")


def get_db():
    db = new_session()
    try:
        yield db
    finally:
        db.close()


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def current(request: Request, db: Session = Depends(get_db)) -> Actor:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tok = resolve_token(db, auth[7:].strip())
        if tok is None:
            raise Unauthorized("Invalid or expired token")
        u = db.get(User, tok.user_id)
        if u is None or u.status != "active":
            raise Unauthorized("Account is not active")
        tok.last_used = utcnow()
        accounts.touch(db, u)
        db.commit()
        request.state.actor = Actor(user=u, via="api", token=tok, ip=_ip(request))
        return request.state.actor
    sess = load_web_session(db, request.cookies.get(SESSION_COOKIE))
    if sess is None:
        raise Unauthorized("Sign in required")
    u = db.get(User, sess.user_id)
    if u is None or u.status != "active":
        raise Unauthorized("Account is not active")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not consteq(request.headers.get("x-csrf-token", ""), sess.csrf):
            raise Forbidden("Missing or wrong CSRF token")
    accounts.touch(db, u)
    db.commit()
    request.state.actor = Actor(user=u, via="web", ip=_ip(request))
    return request.state.actor


# ---- auth -------------------------------------------------------------------

class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)) -> Response:
    u = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    ok = u is not None and u.status == "active" and verify_password(body.password, u.password_hash)
    if not ok:
        audit.log(db, Actor(user=u, via="web", ip=_ip(request)) if u else None, "signin.password", body.username[:64], ok=False,
                  via="web", ip=_ip(request), reason="unknown account" if u is None else "wrong password or not active")
        db.commit()
        raise Unauthorized("Wrong username or password")
    raw, csrf = create_web_session(db, u)
    accounts.touch(db, u)
    audit.log(db, Actor(user=u, via="web", ip=_ip(request)), "signin.password", u.username)
    db.commit()
    resp = JSONResponse({"user": accounts.user_info(db, u), "csrf": csrf})
    set_session_cookie(resp, raw)
    return resp


@router.post("/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    sess = load_web_session(db, request.cookies.get(SESSION_COOKIE))
    if sess is not None and not consteq(request.headers.get("x-csrf-token", ""), sess.csrf):
        raise Forbidden("Missing or wrong CSRF token")
    end_web_session(db, request.cookies.get(SESSION_COOKIE))
    db.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/auth/me")
def me(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    sess = load_web_session(db, request.cookies.get(SESSION_COOKIE))
    s = config.settings
    info = {"oidc": s.oidc_enabled, "smb": s.smb_enabled, "smb_host": s.smb_host, "base_url": s.base_url,
            "marketplace": s.plugin_marketplace, "trash_days": s.trash_days, "max_file_bytes": s.max_file_bytes,
            "storage_mode": s.storage_mode, "protected_storage": zfs.available()}
    if sess is None:
        return {"user": None, **info}
    u = db.get(User, sess.user_id)
    if u is None or u.status != "active":
        return {"user": None, **info}
    return {"user": accounts.user_info(db, u), "csrf": sess.csrf, **info}


# ---- files ----------------------------------------------------------------------

@router.get("/fs/list")
def fs_list(path: str = "/", actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return fs.list_folder(db, actor, path)


@router.get("/fs/meta")
def fs_meta(path: str, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    _, f = fs.open_file(db, actor, path)
    info = fs.file_info(f, db, role=role_for(db, actor, paths.parent(f.path)))
    info["preview_url"] = "/preview/" + sign({"p": f.path, "u": actor.user.id}, "preview") + "/" + quote(paths.name(f.path))
    return info


@router.get("/fs/download")
def fs_download(path: str, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    d, f = fs.open_file(db, actor, path)
    audit.log(db, actor, "file.download", f.path)
    db.commit()
    return FileResponse(d, filename=paths.name(f.path), media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


class Meta(BaseModel):
    project: str | None = None
    tags: list[str] | None = None


@router.post("/fs/upload")
async def fs_upload(folder: str = Form(...), file: UploadFile = UploadFileField(...), tags: str = Form(""),
                    actor: Actor = Depends(current), db: Session = Depends(get_db)):
    data = await file.read(config.settings.max_file_bytes + 1)
    name = os.path.basename(file.filename or "upload")
    return fs.save_file(db, actor, paths.join(paths.normalize(folder), name), data,
                        fs.SaveMeta(source="web", tags=[t for t in tags.split(",") if t.strip()] or None))


class TextSave(BaseModel):
    path: str
    content: str
    tags: list[str] | None = None
    project: str | None = None


@router.post("/fs/save-text")
def fs_save_text(body: TextSave, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return fs.save_file(db, actor, body.path, body.content.encode("utf-8"),
                        fs.SaveMeta(source=actor.via, tags=body.tags, project=body.project))


class PathBody(BaseModel):
    path: str


class MoveBody(BaseModel):
    src: str
    dst: str


class DeleteBody(BaseModel):
    path: str
    confirm: bool = False
    preview: bool = False


@router.post("/fs/mkdir")
def fs_mkdir(body: PathBody, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return fs.make_folder(db, actor, body.path)


@router.post("/fs/move")
def fs_move(body: MoveBody, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return fs.move(db, actor, body.src, body.dst)


@router.post("/fs/delete")
def fs_delete(body: DeleteBody, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return fs.delete(db, actor, body.path, confirm=body.confirm, preview=body.preview)


@router.get("/fs/versions")
def fs_versions(path: str, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return {"versions": fs.versions(db, actor, path)}


BIG = 2 ** 53


class RestoreVersion(BaseModel):
    path: str
    version: int = Field(ge=1, le=BIG)


@router.post("/fs/restore-version")
def fs_restore_version(body: RestoreVersion, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return fs.restore_version(db, actor, body.path, body.version)


@router.get("/fs/search")
def fs_search(q: str = "", project: str | None = None, tag: str | None = None, folder: str | None = None,
              actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return {"results": fs.search(db, actor, q, project=project, tag=tag, folder=folder)}


@router.get("/fs/access")
def fs_access(path: str, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return {"path": paths.normalize(path), "my_role": role_for(db, actor, paths.normalize(path)),
            "access": fs.who_has_access(db, actor, path)}


class ShareBody(BaseModel):
    path: str
    kind: str  # group | user
    name: str
    role: str


@router.post("/fs/share")
def fs_share(body: ShareBody, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    if body.kind not in ("group", "user"):
        raise BadRequest("kind must be group or user")
    return fs.share(db, actor, body.path, body.kind, body.name, body.role)


@router.delete("/grants/{grant_id}")
def grant_delete(grant_id: int, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    g = db.get(Grant, grant_id)
    if g is None:
        raise NotFound("No such grant")
    fs.remove_grant(db, actor, g)
    return {"ok": True}


@router.get("/trash")
def trash_list(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return {"items": fs.list_trash(db, actor)}


class TrashRestore(BaseModel):
    id: str


@router.post("/trash/restore")
def trash_restore(body: TrashRestore, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return fs.restore_trash(db, actor, body.id)


# ---- people and groups ---------------------------------------------------------------

@router.get("/users")
def users_list(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_auditor(actor)
    return {"users": [accounts.user_info(db, u) for u in db.scalars(select(User).order_by(User.display_name, User.username))]}


class NewUser(BaseModel):
    username: str
    email: str | None = None
    display_name: str = ""
    password: str
    system_role: str = "member"
    groups: list[str] = []


@router.post("/users")
def users_create(body: NewUser, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    u = accounts.create_user(db, actor, username=body.username, email=body.email, display_name=body.display_name,
                             password=body.password, system_role=body.system_role)
    for gname in body.groups:
        g = db.scalar(select(Group).where(Group.name == gname))
        if g is None or g.source == "ad":
            raise BadRequest(f"{gname} is not a local group")
        db.add(Membership(user_id=u.id, group_id=g.id))
    db.commit()
    return accounts.user_info(db, u)


class UserPatch(BaseModel):
    system_role: str | None = None
    status: str | None = None
    quota_bytes: int | None = Field(default=None, gt=0, le=BIG)
    password: str | None = None
    display_name: str | None = None


@router.patch("/users/{user_id}")
def users_patch(user_id: int, body: UserPatch, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    u = accounts.update_user(db, actor, user_id, **body.model_dump())
    return accounts.user_info(db, u)


@router.get("/groups")
def groups_list(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    if not actor.is_auditor:
        mine = set(actor.group_ids(db))
        return {"groups": [accounts.group_info(db, g) for g in db.scalars(select(Group).order_by(Group.name)) if g.id in mine]}
    return {"groups": [accounts.group_info(db, g) for g in db.scalars(select(Group).order_by(Group.name))]}


@router.get("/groups/{gid}")
def groups_get(gid: int, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    g = db.get(Group, gid)
    if g is None or (not actor.is_auditor and gid not in actor.group_ids(db)):
        raise NotFound("No such group")
    return accounts.group_info(db, g, with_members=True)


class NewGroup(BaseModel):
    name: str
    description: str = ""


@router.post("/groups")
def groups_create(body: NewGroup, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    g = accounts.create_group(db, actor, body.name, body.description)
    db.commit()
    return accounts.group_info(db, g)


class MemberBody(BaseModel):
    username: str


@router.post("/groups/{gid}/members")
def groups_add_member(gid: int, body: MemberBody, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.add_member(db, actor, gid, body.username)
    return accounts.group_info(db, db.get(Group, gid), with_members=True)


@router.delete("/groups/{gid}/members/{username}")
def groups_remove_member(gid: int, username: str, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.remove_member(db, actor, gid, username)
    return accounts.group_info(db, db.get(Group, gid), with_members=True)


@router.get("/access-check")
def access_check(path: str, user: str | None = None, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    """Who can reach a folder (every grant on it or above), and, with ?user=, whether that person can and why."""
    accounts.require_auditor(actor)
    p = paths.normalize(path)
    sys_actor = Actor(user=actor.user, via=actor.via)
    if not actor.is_admin:
        sys_actor = Actor(user=User(id=-1, username="auditor", system_role="admin", status="active"), via="web")
    out: dict[str, Any] = {"path": p, "access": fs.who_has_access(db, sys_actor, p)}
    if user:
        u = db.scalar(select(User).where(User.username == user.strip().lower()))
        if u is None:
            raise NotFound("No such person")
        out["person"] = fs.explain_access(db, u, p)
    return out


# ---- tokens and devices ---------------------------------------------------------------

@router.get("/tokens")
def tokens_list(all: bool = False, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    q = select(Token).where(Token.kind.in_(("personal", "oauth_refresh")), Token.revoked.is_(False))
    if all:
        accounts.require_admin(actor)
    else:
        q = q.where(Token.user_id == actor.user.id)
    rows = [mcp.token_info(db, t) for t in db.scalars(q.order_by(Token.created_at.desc()))]
    now = utcnow()
    for r in rows:
        if r["expires_at"]:
            left = (dt.datetime.fromisoformat(r["expires_at"].rstrip("Z")) - now).total_seconds()
            r["expires_in_days"] = max(0, -int(-left // 86400))  # round up: a 3-day token reads "3 days"
    return {"tokens": rows}


class NewToken(BaseModel):
    name: str
    scope: str = "write"
    folders: list[str] | None = None
    days: int = 30


@router.post("/tokens")
def tokens_create(body: NewToken, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    if actor.via != "web":
        raise Forbidden("Create tokens from the web app")
    if body.scope not in ("read", "write"):
        raise BadRequest("scope must be read or write")
    if not 1 <= body.days <= 365:
        raise BadRequest("Tokens last 1 to 365 days")
    folders = [paths.normalize(f) for f in (body.folders or [])] or None
    raw, tok = issue_token(db, actor.user, name=body.name or "Personal token", kind="personal", scope=body.scope,
                           folders=folders, ttl=dt.timedelta(days=body.days))
    audit.log(db, actor, "token.create", tok.name, scope=body.scope, folders=folders)
    db.commit()
    return {"token": raw, **mcp.token_info(db, tok)}


@router.delete("/tokens/{tid}")
def tokens_revoke(tid: int, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    t = db.get(Token, tid)
    if t is None or (t.user_id != actor.user.id and not actor.is_admin):
        raise NotFound("No such token")
    mcp.revoke_family(db, t)
    audit.log(db, actor, "token.revoke", t.name, token_id=t.id)
    db.commit()
    return {"ok": True}


# ---- routing and ingest -------------------------------------------------------------

@router.get("/routing")
def routing_list(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    """Admins and auditors see every rule; others see rules that point somewhere they can reach,
    with a flag for the ones they may change."""
    out = []
    for r in db.scalars(select(RoutingRule).order_by(RoutingRule.priority, RoutingRule.id)):
        info = routing.rule_info(r)
        info["can_edit"] = routing.can_manage(db, actor, r.target)
        root = paths.quota_root(paths.normalize(r.target.replace("{project}", "x").replace("{user}", actor.user.username)))
        if actor.is_auditor or info["can_edit"] or (root and role_for(db, actor, root) is not None):
            out.append(info)
    return {"rules": out, "can_add": actor.is_admin or routing.owned_team_folders(db, actor) != []}


class NewRule(BaseModel):
    match: str
    pattern: str
    target: str
    priority: int = Field(default=100, ge=-1000000, le=1000000)


@router.post("/routing")
def routing_add(body: NewRule, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return routing.rule_info(routing.add_rule(db, actor, body.match, body.pattern, body.target, body.priority))


@router.delete("/routing/{rid}")
def routing_delete(rid: int, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    routing.delete_rule(db, actor, rid)
    return {"ok": True}


class RoutePreview(BaseModel):
    project: str | None = None
    git_remote: str | None = None


@router.post("/routing/preview")
def routing_preview(body: RoutePreview, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    folder, reason = routing.resolve(db, actor, folder=None, project=body.project, git_remote=body.git_remote)
    return {"folder": folder, "reason": reason}


@router.get("/ingest")
def ingest(actor: Actor = Depends(current), db: Session = Depends(get_db), status: str = "all", limit: int = 50):
    q = select(Audit).where(Audit.action == "file.save", Audit.via == "mcp").order_by(Audit.id.desc())
    if not actor.is_auditor:
        q = q.where(Audit.user_id == actor.user.id)
    if status == "saved":
        q = q.where(Audit.ok.is_(True))
    elif status == "rejected":
        q = q.where(Audit.ok.is_(False))
    feed = [{"id": e.id, "ts": e.ts.isoformat() + "Z", "user": e.actor, "path": e.target, "ok": e.ok,
             "detail": e.detail} for e in db.scalars(q.limit(min(limit, 200)))]
    return {"paused": bool(get_setting(db, "ingest_paused", False)), "today": mcp.stats_today(db, None if actor.is_auditor else actor.user.id), "feed": feed,
            "max_file_bytes": config.settings.max_file_bytes}


class IngestSettings(BaseModel):
    paused: bool


@router.put("/ingest/settings")
def ingest_settings(body: IngestSettings, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    set_setting(db, "ingest_paused", body.paused)
    audit.log(db, actor, "ingest.pause" if body.paused else "ingest.resume", "")
    db.commit()
    return {"paused": body.paused}


# ---- protected storage (ZFS helper) ------------------------------------------------------

@router.get("/storage/protected")
def protected_get(actor: Actor = Depends(current)):
    accounts.require_auditor(actor)
    return zfs.status()


@router.get("/storage/protected/points")
def protected_points(actor: Actor = Depends(current)):
    accounts.require_auditor(actor)
    return {"points": zfs.points()}


@router.get("/storage/protected/browse")
def protected_browse(point: str, path: str = "/", actor: Actor = Depends(current)):
    accounts.require_admin(actor)
    return {"point": point, "path": paths.normalize(path), "items": zfs.browse(point, path)}


class RestoreBody(BaseModel):
    point: str = Field(max_length=64)
    path: str = Field(max_length=1024)


@router.post("/storage/protected/restore")
def protected_restore(body: RestoreBody, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    return zfs.restore(db, actor, body.point, body.path)


class DisksBody(BaseModel):
    disks: list[str] = Field(default_factory=list, max_length=2)
    layout: str = "mirror"
    existing: str | None = None
    old: str | None = None
    new: str | None = None
    disk: str | None = None
    gigabytes: int | None = Field(default=None, ge=1, le=1024)


@router.post("/storage/protected/disks/{action}")
def protected_disks(action: str, body: DisksBody, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    if action == "add":
        return zfs.add_disks(db, actor, body.disks, body.layout)
    if action == "mirror" and body.existing and body.new:
        return zfs.mirror_disk(db, actor, body.existing, body.new)
    if action == "replace" and body.old and body.new:
        return zfs.replace_disk(db, actor, body.old, body.new)
    if action == "grow" and body.disk and body.gigabytes:
        return zfs.grow(db, actor, body.disk, body.gigabytes)
    raise BadRequest("Unknown disk action, or something missing")


class KeepBody(BaseModel):
    hourly: int = Field(ge=0, le=400)
    daily: int = Field(ge=0, le=400)
    weekly: int = Field(ge=0, le=400)
    monthly: int = Field(ge=0, le=400)


@router.put("/storage/protected/schedule")
def protected_schedule(body: KeepBody, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    return zfs.set_keep(db, actor, body.model_dump())


class BackupBody(BaseModel):
    kind: str | None = None
    host: str | None = Field(default=None, max_length=200)
    dataset: str | None = Field(default=None, max_length=200)


@router.put("/storage/protected/backup")
def protected_backup_set(body: BackupBody, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    return {"backup": zfs.set_backup(db, actor, body.kind, body.host, body.dataset)}


@router.post("/storage/protected/{action}")
def protected_action(action: str, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    if action == "point":
        return zfs.make_point(db, actor)
    if action == "health-check":
        return zfs.health_check(db, actor)
    if action == "backup":
        return zfs.backup_now(db, actor)
    raise NotFound("Unknown action")


# ---- storage ----------------------------------------------------------------------------

@router.get("/storage")
def storage_get(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_auditor(actor)
    return storage.pool_info(db)


@router.post("/storage/{vid}/{action}")
def storage_action(vid: int, action: str, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    if action == "add":
        return storage.add_to_pool(db, actor, vid)
    if action == "drain":
        return storage.start_drain(db, actor, vid)
    if action == "undrain":
        return storage.stop_drain(db, actor, vid)
    raise NotFound("Unknown action")


class Policy(BaseModel):
    policy: str


@router.put("/storage/policy")
def storage_policy(body: Policy, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    storage.set_policy(db, actor, body.policy)
    return storage.pool_info(db)


class Quotas(BaseModel):
    user_gb: float | None = Field(default=None, allow_inf_nan=False, le=1e9)
    group_gb: float | None = Field(default=None, allow_inf_nan=False, le=1e9)


@router.put("/storage/quotas")
def storage_quotas(body: Quotas, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    if body.user_gb is not None:
        if body.user_gb <= 0:
            raise BadRequest("Quota must be positive")
        set_setting(db, "default_user_quota", int(body.user_gb * 1024**3))
    if body.group_gb is not None:
        if body.group_gb <= 0:
            raise BadRequest("Quota must be positive")
        set_setting(db, "default_group_quota", int(body.group_gb * 1024**3))
    audit.log(db, actor, "storage.quotas", "", **body.model_dump())
    db.commit()
    return quotas_get(actor, db)


@router.get("/storage/quotas")
def quotas_get(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    return {"user_bytes": int(get_setting(db, "default_user_quota", config.settings.default_user_quota)),
            "group_bytes": int(get_setting(db, "default_group_quota", config.settings.default_group_quota)),
            "max_file_bytes": config.settings.max_file_bytes}


# ---- directory and SMB ------------------------------------------------------------------

@router.get("/directory")
def directory_get(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_auditor(actor)
    out = directory.status(db)
    out["groups"] = [accounts.group_info(db, g) | {"ad_sid": g.ad_sid} for g in db.scalars(select(Group).order_by(Group.name))]
    return out


@router.get("/directory/ad-groups")
def directory_ad_groups(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    conn = directory.connect()
    mapped = {g.ad_name for g in db.scalars(select(Group).where(Group.ad_name.is_not(None)))}
    return {"groups": [g | {"mapped": g["cn"] in mapped} for g in directory.list_ad_groups(conn)]}


@router.post("/directory/sync")
def directory_sync(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    return directory.sync(db, actor)


class MapGroup(BaseModel):
    ad_group: str


@router.post("/directory/map")
def directory_map(body: MapGroup, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    directory.add_extra_group(db, actor, body.ad_group)
    return {"ok": True}


@router.get("/smb")
def smb_get(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_auditor(actor)
    s = config.settings
    return {"enabled": s.smb_enabled, "acl_tools": acl.enabled(), "host": s.smb_host,
            "shares": [{"name": "home", "unc": f"\\\\{s.smb_host}\\home", "path": "/users/<username>", "drive": "H:"},
                       {"name": "shared", "unc": f"\\\\{s.smb_host}\\shared", "path": "/shared", "drive": "S:"}],
            "drift": acl.drift(db, limit=50),
            "folders_with_grants": int(db.scalar(select(func.count(func.distinct(Grant.folder_path)))) or 0),
            "sessions": smb_sessions()}


def smb_sessions() -> list[dict[str, Any]]:
    """Live SMB sessions, written by the Samba container's status job to a shared file."""
    fp = os.path.join(paths.tree_root(), ".smbstatus.json")
    try:
        import json
        with open(fp) as f:
            return json.load(f).get("sessions", [])[:200]
    except (OSError, ValueError):
        return []


@router.post("/smb/reconcile")
def smb_reconcile(actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_admin(actor)
    n = acl.reconcile_all(db)
    audit.log(db, actor, "smb.reconcile", "", items=n)
    db.commit()
    return {"items": n, "drift": acl.drift(db, limit=50)}


# ---- overview and audit -------------------------------------------------------------------

@router.get("/overview")
def overview(actor: Actor = Depends(current), db: Session = Depends(get_db), days: int = Query(14, ge=1, le=90)):
    accounts.require_auditor(actor)
    now = utcnow()
    start = (now - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    saves = db.execute(select(Audit.ts, Audit.via).where(Audit.action == "file.save", Audit.ok.is_(True), Audit.ts >= start)).all()
    series = []
    for i in range(days):
        d0 = start + dt.timedelta(days=i)
        d1 = d0 + dt.timedelta(days=1)
        series.append({"date": d0.date().isoformat(),
                       "mcp": sum(1 for t, v in saves if d0 <= t < d1 and v == "mcp"),
                       "other": sum(1 for t, v in saves if d0 <= t < d1 and v != "mcp")})
    pool = storage.pool_info(db)
    attention = []
    for v in pool["volumes"]:
        if v["status"] == "pending":
            attention.append({"kind": "volume", "text": f"{v['name']} is waiting to join the pool", "link": "/storage"})
        if v["status"] == "missing":
            attention.append({"kind": "volume", "text": f"{v['name']} is no longer mounted", "link": "/storage"})
    if zfs.available():
        try:
            st = zfs.status()
            for prob in st["summary"]["problems"]:
                attention.append({"kind": "disk", "text": f"Disk {prob['name']} needs attention ({prob['health'].lower()})", "link": "/storage"})
            if st["cap_pct"] >= 80:
                attention.append({"kind": "disk", "text": f"Storage is {st['cap_pct']}% full", "link": "/storage"})
            b = st.get("backup")
            if b and b.get("last_error"):
                attention.append({"kind": "backup", "text": "The last backup failed", "link": "/storage"})
            pool["total_bytes"], pool["used_bytes"] = st["size"], st["alloc"]
        except Exception:
            attention.append({"kind": "disk", "text": "The storage helper isn't answering", "link": "/storage"})
    soon = db.scalar(select(func.count()).select_from(Token).where(Token.kind == "personal", Token.revoked.is_(False),
                                                                   Token.expires_at <= now + dt.timedelta(days=7), Token.expires_at > now))
    if soon:
        attention.append({"kind": "tokens", "text": f"{soon} personal token(s) expire this week", "link": "/connect"})
    if config.settings.smb_enabled:
        drift_n = len(acl.drift(db, limit=20))
        if drift_n:
            attention.append({"kind": "smb", "text": f"{drift_n} folder(s) have share permissions out of step", "link": "/smb"})
    if config.settings.ldap_enabled and directory.is_stale(db, 30):
        attention.append({"kind": "directory", "text": "Directory has not synced in the last 30 minutes", "link": "/directory"})
    return {"people": db.scalar(select(func.count()).select_from(User).where(User.status == "active")),
            "invited": db.scalar(select(func.count()).select_from(User).where(User.status == "invited")),
            "files": accounts.files_count(db),
            "folders_with_grants": int(db.scalar(select(func.count(func.distinct(Grant.folder_path)))) or 0),
            "series": series, "pool": {k: pool[k] for k in ("total_bytes", "used_bytes", "mode")},
            "volumes": [{k: v[k] for k in ("name", "status", "total_bytes", "used_bytes", "label")} for v in pool["volumes"]],
            "activity": mcp.audit_query(db, limit=8, exclude=("signin.*", "oauth.token*", "file.preview", "access.denied", "denied")),
            "attention": attention}


@router.get("/audit")
def audit_list(action: str | None = None, user: str | None = None, path: str | None = None, via: str | None = None,
               limit: int = 100, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    accounts.require_auditor(actor)
    return {"events": mcp.audit_query(db, action=action, user=user, path=path, via=via, limit=max(1, min(limit, 1000)))}


def _cell(v: Any) -> str:
    """A CSV cell that spreadsheets show as text: anything starting like a formula gets a leading quote."""
    t = "" if v is None else (v if isinstance(v, str) else json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v))
    return "'" + t if t[:1] in ("=", "+", "-", "@", "\t", "\r", "|") else t


@router.get("/audit/export.csv")
def audit_export(action: str | None = None, user: str | None = None, path: str | None = None, via: str | None = None,
                 actor: Actor = Depends(current), db: Session = Depends(get_db)):
    """The whole filtered log (up to 50,000 rows), not just what's on screen."""
    import csv as _csv
    import io
    accounts.require_auditor(actor)
    rows = mcp.audit_query(db, action=action or None, user=user or None, path=path or None, via=via or None, limit=50000)
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["time", "person", "name", "action", "target", "via", "ok", "ip", "detail"])
    for e in rows:
        w.writerow([_cell(e[k]) for k in ("ts", "actor", "actor_name", "action", "target", "via", "ok", "ip", "detail")])
    audit.log(db, actor, "audit.export", "", rows=len(rows))
    db.commit()
    name = f"audit-{utcnow().date().isoformat()}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---- folder settings ----------------------------------------------------------------------

class FolderSettings(BaseModel):
    path: str
    quota_gb: float | None = Field(default=None, allow_inf_nan=False)
    keep_versions: int | None = Field(default=None, le=100000)


@router.put("/folders/settings")
def folder_settings(body: FolderSettings, actor: Actor = Depends(current), db: Session = Depends(get_db)):
    p = paths.normalize(body.path)
    if body.quota_gb is not None:
        accounts.require_admin(actor)
    elif role_for(db, actor, p) != "owner":
        raise Forbidden("Only an Owner can change folder settings")
    fo = db.scalar(select(Folder).where(Folder.path == p)) or Folder(path=p, created_by=actor.user.id)
    if body.quota_gb is not None:
        if paths.quota_root(p) != p:
            raise BadRequest("Quotas are set on a person's home or a team folder, not on folders inside them")
        if not 0 < body.quota_gb <= 1e9 or int(body.quota_gb * 1024**3) < 1:
            raise BadRequest("Quota must be a positive number of GB")
        fo.quota_bytes = int(body.quota_gb * 1024**3)
    if body.keep_versions is not None:
        if body.keep_versions < 1:
            raise BadRequest("Keep at least one version")
        fo.keep_versions = body.keep_versions
    db.add(fo)
    audit.log(db, actor, "folder.settings", p, **body.model_dump(exclude={"path"}))
    db.commit()
    return {"path": p, "quota_bytes": fo.quota_bytes, "keep_versions": fo.keep_versions}


# ---- preview (sandboxed) -------------------------------------------------------------------

preview_router = APIRouter()


@preview_router.get("/preview/{token}/{name}")
def preview(token: str, name: str, request: Request):
    data = unsign(token, "preview", max_age=600)
    if not data:
        return Response("Preview link expired", status_code=403)
    with new_session() as db:
        # The link only works inside the signed-in browser session it was made for, so a copied link
        # (in chat, in history) is useless to anyone else and stops working at sign-out.
        sess = load_web_session(db, request.cookies.get(SESSION_COOKIE))
        if sess is None or sess.user_id != data["u"]:
            return Response("Sign in to Artifact Buckets to view this file", status_code=403)
        u = db.get(User, data["u"])
        if u is None or u.status != "active":
            return Response("Not allowed", status_code=403)
        who = Actor(user=u, via="web")
        d, f = fs.open_file(db, who, data["p"])
        audit.log(db, who, "file.preview", f.path, size=f.size)
        db.commit()
        mime = f.mime if f.mime.startswith(("text/", "image/")) or f.mime in ("application/json", "application/pdf") else "text/plain"
        if f.mime == "image/svg+xml":
            mime = "image/svg+xml"
    headers = {"Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com; "
                                          "style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com data:; img-src data: blob:; connect-src 'none'; form-action 'none'; frame-ancestors 'self'",
               "X-Frame-Options": "SAMEORIGIN",
               "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "private, max-age=60",
               "Cross-Origin-Resource-Policy": "same-origin"}
    return FileResponse(d, media_type=mime, headers=headers)
