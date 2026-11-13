"""File and folder operations. The web API, the MCP tools and the indexer all go through here,
so every door gets the same permission checks, quotas, versions, trash and audit trail."""
from __future__ import annotations

import datetime as dt
import hashlib
import mimetypes
import os
import shutil
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import acl, audit, config, paths
from .db import File, FileVersion, Folder, Grant, Group, Membership, TrashItem, User, get_setting, utcnow
from .errors import BadRequest, Conflict, Forbidden, NeedsConfirmation, NotFound, Paused, QuotaExceeded, TooLarge
from .perms import RANK, ROLES, Actor, at_least, base_role, can_traverse, role_for


@dataclass
class SaveMeta:
    source: str = "web"
    project: str | None = None
    git_remote: str | None = None
    git_commit: str | None = None
    session_id: str | None = None
    tags: list[str] | None = None


def _like_prefix(path: str) -> str:
    p = path.rstrip("/") + "/"
    return p.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def _need(db: Session, actor: Actor, path: str, needed: str, what: str) -> str:
    role = role_for(db, actor, path)
    if not at_least(role, needed):
        audit.log(db, actor, "denied", path, ok=False, needed=needed, op=what)
        db.commit()
        if role is None and not can_traverse(db, actor, path):
            raise NotFound("No such file or folder, or you do not have access")
        raise Forbidden(f"You need {needed} access to {what} here")
    return role  # type: ignore[return-value]


def _sha256_file(fp: str) -> str:
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _chown_for(user: User | None, fp: str) -> None:
    """On the lab host the app runs as root so files belong to the person (seen as owner over SMB)."""
    if user is not None and user.uid_number and hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            os.chown(fp, user.uid_number, -1)
        except OSError:
            pass


def file_row(db: Session, path: str) -> File | None:
    return db.scalar(select(File).where(File.path == path))


def file_info(f: File, db: Session | None = None, role: str | None = None) -> dict[str, Any]:
    by = db.get(User, f.saved_by) if (db is not None and f.saved_by) else None
    return {
        "type": "file", "path": f.path, "name": paths.name(f.path), "size": f.size, "sha256": f.sha256,
        "mime": f.mime, "version": f.version, "source": f.source, "project": f.project,
        "git_remote": f.git_remote, "git_commit": f.git_commit, "session_id": f.session_id,
        "tags": f.tags or [], "saved_by": (by.display_name or by.username) if by else None,
        "updated_at": f.updated_at.isoformat() + "Z", "created_at": f.created_at.isoformat() + "Z",
        **({"role": role} if role else {}),
    }


# ---- quotas -------------------------------------------------------------

def quota_for(db: Session, root: str) -> int | None:
    parts = root.strip("/").split("/")
    folder = db.scalar(select(Folder).where(Folder.path == root))
    if folder and folder.quota_bytes:
        return folder.quota_bytes
    if parts[0] == "users":
        u = db.scalar(select(User).where(User.username == parts[1]))
        if u and u.quota_bytes:
            return u.quota_bytes
        return int(get_setting(db, "default_user_quota", config.settings.default_user_quota))
    if parts[0] == "shared":
        return int(get_setting(db, "default_group_quota", config.settings.default_group_quota))
    return None


def usage(db: Session, root: str) -> int:
    """Bytes used under a folder: current files plus their kept older versions. (Trash does not count.)"""
    like = File.path.like(_like_prefix(root), escape="\\")
    cur = db.scalar(select(func.coalesce(func.sum(File.size), 0)).where(like)) or 0
    old = db.scalar(select(func.coalesce(func.sum(FileVersion.size), 0)).join(File, FileVersion.file_id == File.id)
                    .where(like)) or 0
    return int(cur) + int(old)


def _plan_quota(db: Session, path: str, new_size: int, existing: File | None) -> tuple[list[FileVersion], bool]:
    """Check a save against the quota. Older versions count; when a save would go over, the oldest versions of
    this same file are given up first to make room. Returns (versions to drop, drop the replaced copy too)."""
    root = paths.quota_root(path)
    limit = quota_for(db, root) if root else None
    if limit is None:
        return [], False
    over = usage(db, root) + new_size - limit
    if over <= 0:
        return [], False
    if existing is not None:
        drops: list[FileVersion] = []
        for v in db.scalars(select(FileVersion).where(FileVersion.file_id == existing.id).order_by(FileVersion.version)):
            drops.append(v)
            over -= v.size
            if over <= 0:
                return drops, False
        if over - existing.size <= 0:
            return drops, True
    raise QuotaExceeded(f"Saving this would go over the quota for {root}", limit=limit)


def _check_quota(db: Session, path: str, new_size: int, old_size: int) -> None:
    root = paths.quota_root(path)
    if not root:
        return
    limit = quota_for(db, root)
    if limit is None:
        return
    if usage(db, root) - old_size + new_size > limit:
        raise QuotaExceeded(f"Saving this would go over the quota for {root}", limit=limit)


# ---- folders ------------------------------------------------------------

def ensure_dir(path: str, owner: User | None = None) -> None:
    """Create a folder and its parents on disk (no permission check: callers check)."""
    d = paths.disk(path)
    if os.path.isfile(d):
        raise Conflict(f"{path} is a file")
    if not os.path.isdir(d):
        try:
            os.makedirs(d, exist_ok=True)
        except (NotADirectoryError, FileExistsError):
            raise Conflict("Part of that path is a file, not a folder") from None
        _chown_for(owner, d)
        acl.on_new_dir(path)


def make_folder(db: Session, actor: Actor, path: str) -> dict[str, Any]:
    path = paths.normalize(path)
    if path == "/" or len(path.strip("/").split("/")) < 2:
        raise Forbidden("Top-level folders are managed by admins")
    _need(db, actor, paths.parent(path), "editor", "create folders")
    d = paths.disk(path)
    if os.path.exists(d):
        raise Conflict("A file or folder with that name already exists")
    if not os.path.isdir(paths.disk(paths.parent(path))):
        ensure_dir(paths.parent(path), actor.user)
    ensure_dir(path, actor.user)
    audit.log(db, actor, "folder.create", path)
    db.commit()
    return {"type": "folder", "path": path, "name": paths.name(path)}


def list_folder(db: Session, actor: Actor, path: str) -> dict[str, Any]:
    path = paths.normalize(path)
    if not can_traverse(db, actor, path):
        raise NotFound("No such folder, or you do not have access")
    role = role_for(db, actor, path)
    d = paths.disk(path)
    if path in ("/", "/users", "/shared"):
        os.makedirs(d, exist_ok=True)
    if not os.path.isdir(d):
        raise NotFound("No such folder")
    folders, files = [], []
    rows = {f.path: f for f in db.scalars(select(File).where(File.path.like(_like_prefix(path), escape="\\")))}
    with os.scandir(d) as it:
        for e in sorted(it, key=lambda e: e.name.lower()):
            if e.name.startswith("."):
                continue
            child = (path.rstrip("/") + "/" + e.name)
            if e.is_dir(follow_symlinks=False):
                if can_traverse(db, actor, child):
                    folders.append({"type": "folder", "path": child, "name": e.name,
                                    "role": role_for(db, actor, child)})
            elif e.is_file(follow_symlinks=False) and role is not None:
                f = rows.get(child) or index_file(db, child, source="smb")
                if f is not None:
                    files.append(file_info(f, db))
    db.commit()
    return {"path": path, "role": role, "folders": folders, "files": files}


# ---- files --------------------------------------------------------------

def index_file(db: Session, path: str, source: str = "smb") -> File | None:
    """Record a file that appeared on disk without the app (saved over SMB)."""
    d = paths.disk(path)
    try:
        st = os.stat(d)
    except FileNotFoundError:
        return None
    f = File(path=path, size=st.st_size, sha256=_sha256_file(d), mime=mimetypes.guess_type(path)[0] or "application/octet-stream",
             source=source, mtime_ns=st.st_mtime_ns,
             updated_at=dt.datetime.fromtimestamp(st.st_mtime, dt.UTC).replace(tzinfo=None))
    db.add(f)
    db.flush()
    return f


def save_file(db: Session, actor: Actor, path: str, data: bytes, meta: SaveMeta | None = None) -> dict[str, Any]:
    meta = meta or SaveMeta(source=actor.via)
    path = paths.normalize(path)
    if len(path.strip("/").split("/")) < 3:
        raise Forbidden("Files go inside a person's or a group's folder")
    if meta.source == "mcp" and get_setting(db, "ingest_paused", False):
        audit.log(db, actor, "file.save", path, ok=False, reason="ingest paused")
        db.commit()
        raise Paused("Saving from Claude Code is paused by an admin")
    folder = paths.parent(path)
    try:
        _need(db, actor, folder, "editor", "save files")
    except (Forbidden, NotFound):
        if meta.source == "mcp":
            audit.log(db, actor, "file.save", path, ok=False, reason="no write access", size=len(data))
            db.commit()
        raise
    limit = config.settings.max_file_bytes
    if len(data) > limit:
        audit.log(db, actor, "file.save", path, ok=False, reason="too large", size=len(data))
        db.commit()
        raise TooLarge(f"File is larger than the {limit // (1024 * 1024)} MB limit")
    d = paths.disk(path)
    if os.path.isdir(d):
        raise Conflict("A folder with that name already exists")
    existing = file_row(db, path)
    if existing is None and os.path.isfile(d):
        existing = index_file(db, path)
    try:
        drops, drop_replaced = _plan_quota(db, path, len(data), existing)
        dropped = sorted([v.version for v in drops] + ([existing.version] if existing is not None and drop_replaced else []))
    except QuotaExceeded:
        audit.log(db, actor, "file.save", path, ok=False, reason="quota", size=len(data))
        db.commit()
        raise
    ensure_dir(folder, actor.user)
    tmp = os.path.join(paths.system_dir("tmp"), uuid.uuid4().hex)
    with open(tmp, "wb") as fh:
        fh.write(data)
    digest = hashlib.sha256(data).hexdigest()
    now = utcnow()
    if existing is not None:
        if existing.sha256 == digest:
            os.unlink(tmp)
            existing.updated_at = now
            audit.log(db, actor, "file.save", path, unchanged=True, version=existing.version)
            db.commit()
            return {**file_info(existing, db), "unchanged": True}
        vdir = os.path.join(paths.system_dir("versions"), str(existing.id))
        os.makedirs(vdir, exist_ok=True)
        for v in drops:
            _drop_version(existing, v, db)
        if drop_replaced:
            if os.path.isfile(d):
                os.unlink(d)
        else:
            stored = f"{existing.version}-{uuid.uuid4().hex[:8]}"
            if os.path.isfile(d):
                shutil.move(d, os.path.join(vdir, stored))
            db.add(FileVersion(file_id=existing.id, version=existing.version, size=existing.size, sha256=existing.sha256,
                               stored_name=stored, saved_by=existing.saved_by, source=existing.source,
                               created_at=existing.updated_at))
        f = existing
        f.version += 1
    else:
        f = File(path=path, created_at=now)
        db.add(f)
    shutil.move(tmp, d)
    _chown_for(actor.user, d)
    acl.on_new_file(db, path)
    st = os.stat(d)
    f.size, f.sha256, f.mtime_ns, f.updated_at = len(data), digest, st.st_mtime_ns, now
    f.mime = mimetypes.guess_type(path)[0] or ("text/markdown" if path.endswith(".md") else "application/octet-stream")
    f.saved_by, f.source = actor.user.id, meta.source
    f.project, f.git_remote, f.git_commit = meta.project, meta.git_remote, meta.git_commit
    f.session_id = meta.session_id or actor.session_id
    if meta.tags is not None:
        f.tags = sorted({t.strip().lower()[:40] for t in meta.tags if t and t.strip()})[:20]
    db.flush()
    _prune_versions(db, f)
    extra = {"dropped_versions": dropped} if dropped else {}
    audit.log(db, actor, "file.save", path, version=f.version, size=f.size, project=meta.project, source=meta.source, **extra)
    db.commit()
    info = file_info(f, db)
    if dropped:
        info["dropped_versions"] = dropped
        info["note"] = f"To stay within the quota, older version(s) {', '.join(map(str, dropped))} were removed from history."
    return info


def _prune_versions(db: Session, f: File) -> None:
    keep = None
    for anc in reversed(paths.ancestors(paths.parent(f.path))):
        fo = db.scalar(select(Folder).where(Folder.path == anc))
        if fo and fo.keep_versions:
            keep = fo.keep_versions
            break
    if not keep:
        return
    old = list(db.scalars(select(FileVersion).where(FileVersion.file_id == f.id).order_by(FileVersion.version.desc())))
    for v in old[max(keep - 1, 0):]:
        _drop_version(f, v, db)


def _drop_version(f: File, v: FileVersion, db: Session) -> None:
    try:
        os.unlink(os.path.join(paths.system_dir("versions"), str(f.id), v.stored_name))
    except FileNotFoundError:
        pass
    db.delete(v)


def open_file(db: Session, actor: Actor, path: str) -> tuple[str, File]:
    path = paths.normalize(path)
    _need(db, actor, paths.parent(path), "viewer", "open files")
    d = paths.disk(path)
    if not os.path.isfile(d):
        raise NotFound("No such file")
    f = file_row(db, path) or index_file(db, path)
    assert f is not None
    return d, f


def read_text(db: Session, actor: Actor, path: str, max_bytes: int = 2 * 1024 * 1024) -> dict[str, Any]:
    d, f = open_file(db, actor, path)
    with open(d, "rb") as fh:
        raw = fh.read(max_bytes + 1)
    audit.log(db, actor, "file.read", f.path)
    db.commit()
    info = file_info(f, db, role_for(db, actor, f.path))
    try:
        info["text"] = raw[:max_bytes].decode("utf-8")
        info["truncated"] = len(raw) > max_bytes
        if info["truncated"]:
            info["note"] = (f"Only the first {max_bytes // (1024 * 1024)} MB is shown. Don't save this text back: "
                            "it would replace the whole file with its first part.")
    except UnicodeDecodeError:
        info["text"] = None
        info["binary"] = True
    return info


def read_base64(db: Session, actor: Actor, path: str, max_bytes: int = 10 * 1024 * 1024) -> dict[str, Any]:
    import base64
    d, f = open_file(db, actor, path)
    if f.size > max_bytes:
        raise TooLarge(f"{f.path} is {f.size // (1024 * 1024)} MB; download it from the web app instead")
    with open(d, "rb") as fh:
        raw = fh.read()
    audit.log(db, actor, "file.read", f.path)
    db.commit()
    return {**file_info(f, db, role_for(db, actor, f.path)), "content_base64": base64.b64encode(raw).decode()}


def edit_file(db: Session, actor: Actor, path: str, old: str, new: str, meta: SaveMeta | None = None) -> dict[str, Any]:
    path = paths.normalize(path)
    d, f = open_file(db, actor, path)
    _need(db, actor, paths.parent(path), "editor", "edit files")
    with open(d, "rb") as fh:
        try:
            text = fh.read().decode("utf-8")
        except UnicodeDecodeError:
            raise BadRequest("Only text files can be edited")
    n = text.count(old) if old else 0
    if n != 1:
        raise BadRequest(f"The text to replace must appear exactly once (found {n} times)")
    return save_file(db, actor, path, text.replace(old, new, 1).encode("utf-8"),
                     meta or SaveMeta(source=actor.via, project=f.project, git_remote=f.git_remote,
                                      git_commit=f.git_commit, tags=f.tags))


def versions(db: Session, actor: Actor, path: str) -> list[dict[str, Any]]:
    _, f = open_file(db, actor, path)

    def who(uid: int | None) -> str | None:
        u = db.get(User, uid) if uid else None
        return (u.display_name or u.username) if u else None

    out = [{"version": f.version, "size": f.size, "sha256": f.sha256, "current": True,
            "saved_at": f.updated_at.isoformat() + "Z", "source": f.source, "saved_by": who(f.saved_by)}]
    for v in db.scalars(select(FileVersion).where(FileVersion.file_id == f.id).order_by(FileVersion.version.desc())):
        out.append({"version": v.version, "size": v.size, "sha256": v.sha256, "current": False,
                    "saved_at": v.created_at.isoformat() + "Z", "source": v.source, "saved_by": who(v.saved_by)})
    return out


def restore_version(db: Session, actor: Actor, path: str, version: int) -> dict[str, Any]:
    _, f = open_file(db, actor, path)
    _need(db, actor, paths.parent(f.path), "editor", "restore versions")
    v = db.scalar(select(FileVersion).where(FileVersion.file_id == f.id, FileVersion.version == version))
    if v is None:
        raise NotFound("No such version")
    with open(os.path.join(paths.system_dir("versions"), str(f.id), v.stored_name), "rb") as fh:
        data = fh.read()
    res = save_file(db, actor, f.path, data, SaveMeta(source=actor.via, project=f.project, git_remote=f.git_remote,
                                                       git_commit=f.git_commit, tags=f.tags))
    audit.log(db, actor, "file.restore_version", f.path, restored=version)
    db.commit()
    return res


# ---- move, rename, delete, trash ---------------------------------------------

def _rewrite_prefix(db: Session, src: str, dst: str) -> None:
    for model, col in ((File, File.path), (Grant, Grant.folder_path), (Folder, Folder.path)):
        for row in db.scalars(select(model).where((col == src) | col.like(_like_prefix(src), escape="\\"))):
            attr = "folder_path" if model is Grant else "path"
            old = getattr(row, attr)
            setattr(row, attr, dst + old[len(src):])


def _grants_inside(db: Session, path: str) -> list[Grant]:
    return list(db.scalars(select(Grant).where((Grant.folder_path == path) | Grant.folder_path.like(_like_prefix(path), escape="\\"))))


def move(db: Session, actor: Actor, src: str, dst: str) -> dict[str, Any]:
    src, dst = paths.normalize(src), paths.normalize(dst)
    if src == dst:
        raise BadRequest("Source and destination are the same")
    if paths.is_under(dst, src):
        raise BadRequest("Cannot move a folder into itself")
    s, t = paths.disk(src), paths.disk(dst)
    # Permission first, so a missing path and a hidden one answer the same.
    _need(db, actor, paths.parent(src), "editor", "move items from here")
    if not os.path.exists(s):
        raise NotFound("No such file or folder")
    if paths.protected(src):
        raise Forbidden("Home and team folders cannot be moved or renamed")
    if len(dst.strip("/").split("/")) < 3:
        raise Forbidden("Items go inside a person's or a group's folder")
    _need(db, actor, paths.parent(dst), "editor", "move items here")
    is_dir = os.path.isdir(s)
    if is_dir and _grants_inside(db, src) and not at_least(role_for(db, actor, src), "owner"):
        raise Forbidden("This folder has its own sharing; only an Owner can move it")
    if os.path.exists(t):
        raise Conflict("Something with that name already exists at the destination")
    if paths.quota_root(src) != paths.quota_root(dst):
        size = usage(db, src) if is_dir else (file_row(db, src).size if file_row(db, src) else os.path.getsize(s))
        _check_quota(db, dst, size, 0)
    ensure_dir(paths.parent(dst), actor.user)
    try:
        os.rename(s, t)
    except OSError:
        shutil.move(s, t)
    _rewrite_prefix(db, src, dst)
    audit.log(db, actor, "item.move", src, to=dst)
    db.commit()
    acl.on_change(db, dst)
    return {"from": src, "to": dst, "type": "folder" if is_dir else "file"}


def delete(db: Session, actor: Actor, path: str, confirm: bool = False, preview: bool = False) -> dict[str, Any]:
    """Move a file or folder to trash. preview=True only checks it may be deleted and says how much it holds."""
    path = paths.normalize(path)
    d = paths.disk(path)
    _need(db, actor, paths.parent(path), "editor", "delete items here")
    if not os.path.exists(d):
        raise NotFound("No such file or folder")
    if paths.protected(path):
        raise Forbidden("Home and team folders cannot be deleted here")
    is_dir = os.path.isdir(d)
    count, size = 1, 0
    if is_dir:
        count = sum(len(fs) for _, _, fs in os.walk(d))
        size = usage(db, path)
        if _grants_inside(db, path) and not at_least(role_for(db, actor, path), "owner"):
            raise Forbidden("This folder has its own sharing; only an Owner can delete it")
        if actor.via == "mcp" and count > config.settings.mcp_delete_confirm_threshold and not confirm:
            raise NeedsConfirmation(f"This folder holds {count} files. Confirm the delete in the web app.", files=count)
    else:
        f = file_row(db, path)
        size = f.size if f else os.path.getsize(d)
    if preview:
        return {"path": path, "folder": is_dir, "files": count, "bytes": size}
    key = uuid.uuid4().hex
    tdir = os.path.join(paths.system_dir("trash"), key)
    os.makedirs(tdir)
    shutil.move(d, os.path.join(tdir, paths.name(path)))
    tpath = f"/.trash/{key}/{paths.name(path)}"
    saved = [{"folder_path": g.folder_path[len(path):], "principal_type": g.principal_type,
              "principal_id": g.principal_id, "role": g.role} for g in _grants_inside(db, path)]
    for g in _grants_inside(db, path):
        db.delete(g)
    for row in db.scalars(select(File).where((File.path == path) | File.path.like(_like_prefix(path), escape="\\"))):
        row.path = tpath + row.path[len(path):]
    db.add(TrashItem(original_path=path, trash_key=key, is_dir=is_dir, file_count=count, size=size,
                     deleted_by=actor.user.id, grants=saved))
    audit.log(db, actor, "item.delete", path, files=count, trash=key)
    db.commit()
    return {"deleted": path, "trash_id": key, "files": count, "restore_until": (utcnow() + dt.timedelta(days=config.settings.trash_days)).isoformat() + "Z"}


def list_trash(db: Session, actor: Actor) -> list[dict[str, Any]]:
    q = select(TrashItem).order_by(TrashItem.deleted_at.desc())
    if not actor.is_admin:
        q = q.where(TrashItem.deleted_by == actor.user.id)
    out = []
    for t in db.scalars(q):
        u = db.get(User, t.deleted_by) if t.deleted_by else None
        out.append({"id": t.trash_key, "path": t.original_path, "is_dir": t.is_dir, "files": t.file_count,
                    "size": t.size, "deleted_by": u.username if u else None, "deleted_by_name": (u.display_name or u.username) if u else None, "deleted_at": t.deleted_at.isoformat() + "Z"})
    return out


def restore_trash(db: Session, actor: Actor, key: str) -> dict[str, Any]:
    t = db.scalar(select(TrashItem).where(TrashItem.trash_key == key))
    if t is None:
        raise NotFound("Nothing in the trash with that id")
    if not actor.is_admin and t.deleted_by != actor.user.id:
        raise NotFound("Nothing in the trash with that id")
    _need(db, actor, paths.parent(t.original_path), "editor", "restore items here")
    target = paths.disk(t.original_path)
    if os.path.exists(target):
        raise Conflict("Something already exists at the original location")
    _check_quota(db, t.original_path, usage(db, f"/.trash/{key}"), 0)
    ensure_dir(paths.parent(t.original_path), actor.user)
    shutil.move(os.path.join(paths.system_dir("trash"), key, paths.name(t.original_path)), target)
    shutil.rmtree(os.path.join(paths.system_dir("trash"), key), ignore_errors=True)
    tpath = f"/.trash/{key}/{paths.name(t.original_path)}"
    for row in db.scalars(select(File).where((File.path == tpath) | File.path.like(_like_prefix(tpath), escape="\\"))):
        row.path = t.original_path + row.path[len(tpath):]
    for g in t.grants or []:
        db.add(Grant(folder_path=t.original_path + g["folder_path"], principal_type=g["principal_type"],
                     principal_id=g["principal_id"], role=g["role"], granted_by=actor.user.id))
    db.delete(t)
    audit.log(db, actor, "item.restore", t.original_path)
    db.commit()
    acl.on_change(db, t.original_path)
    return {"restored": t.original_path}


def purge_trash(db: Session, older_than_days: int | None = None) -> int:
    days = config.settings.trash_days if older_than_days is None else older_than_days
    cutoff = utcnow() - dt.timedelta(days=days)
    n = 0
    for t in db.scalars(select(TrashItem).where(TrashItem.deleted_at <= cutoff)):
        shutil.rmtree(os.path.join(paths.system_dir("trash"), t.trash_key), ignore_errors=True)
        tpath = f"/.trash/{t.trash_key}"
        for row in db.scalars(select(File).where(File.path.like(_like_prefix(tpath), escape="\\"))):
            shutil.rmtree(os.path.join(paths.system_dir("versions"), str(row.id)), ignore_errors=True)
            db.delete(row)
        db.delete(t)
        n += 1
    if n:
        audit.log(db, None, "trash.purge", "", items=n)
    db.commit()
    return n


# ---- search ---------------------------------------------------------------

def search(db: Session, actor: Actor, query: str = "", *, project: str | None = None, tag: str | None = None,
           folder: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    q = select(File).where(~File.path.like("/.trash/%")).order_by(File.updated_at.desc())
    if query:
        like = "%" + query.lower().replace("%", r"\%").replace("_", r"\_") + "%"
        q = q.where(func.lower(File.path).like(like, escape="\\"))
    if project:
        q = q.where(File.project == project)
    if folder:
        q = q.where(File.path.like(_like_prefix(paths.normalize(folder)), escape="\\"))
    out: list[dict[str, Any]] = []
    cache: dict[str, str | None] = {}
    for f in db.scalars(q.limit(2000)):
        if tag and tag.lower() not in (f.tags or []):
            continue
        par = paths.parent(f.path)
        if par not in cache:
            cache[par] = role_for(db, actor, par)
        if cache[par] is None:
            continue
        out.append(file_info(f, db))
        if len(out) >= limit:
            break
    return out


# ---- sharing ----------------------------------------------------------------

def _principal(db: Session, kind: str, name: str) -> tuple[str, int, str]:
    if kind == "group":
        g = db.scalar(select(Group).where(Group.name == name))
        if not g:
            raise NotFound(f"No group named {name}")
        return "group", g.id, g.name
    u = db.scalar(select(User).where(User.username == name))
    if not u:
        raise NotFound(f"No person named {name}")
    return "user", u.id, u.username


def principal_label(db: Session, ptype: str, pid: int) -> dict[str, Any]:
    if ptype == "group":
        g = db.get(Group, pid)
        n = db.scalar(select(func.count()).select_from(Membership).where(Membership.group_id == pid)) if g else 0
        return {"type": "group", "id": pid, "name": g.name if g else f"group {pid}", "members": n}
    u = db.get(User, pid)
    return {"type": "user", "id": pid, "name": u.username if u else f"user {pid}",
            "display_name": (u.display_name or u.username) if u else None}


def share(db: Session, actor: Actor, path: str, kind: str, name: str, role: str) -> dict[str, Any]:
    path = paths.normalize(path)
    if role not in ROLES:
        raise BadRequest("Role must be viewer, editor or owner")
    _need(db, actor, path, "owner", "share this folder")
    if not os.path.isdir(paths.disk(path)):
        raise NotFound("Sharing works on folders")
    ptype, pid, pname = _principal(db, kind, name)
    parts = path.strip("/").split("/")
    if parts[0] == "users" and len(parts) == 2 and ptype == "user" and pname == parts[1] and role != "owner":
        raise Forbidden("A person always owns their home folder")
    g = db.scalar(select(Grant).where(Grant.folder_path == path, Grant.principal_type == ptype, Grant.principal_id == pid))
    if g is None:
        g = Grant(folder_path=path, principal_type=ptype, principal_id=pid, role=role, granted_by=actor.user.id)
        db.add(g)
    else:
        g.role, g.granted_by, g.created_at = role, actor.user.id, utcnow()
    audit.log(db, actor, "grant.set", path, principal=f"{ptype}:{pname}", role=role)
    db.commit()
    acl.on_change(db, path)
    return {"id": g.id, "path": path, "principal": principal_label(db, ptype, pid), "role": role}


def unshare(db: Session, actor: Actor, path: str, kind: str, name: str) -> dict[str, Any]:
    path = paths.normalize(path)
    _need(db, actor, path, "owner", "change sharing")
    ptype, pid, pname = _principal(db, kind, name)
    g = db.scalar(select(Grant).where(Grant.folder_path == path, Grant.principal_type == ptype, Grant.principal_id == pid))
    if g is None:
        raise NotFound("That grant does not exist on this folder")
    remove_grant(db, actor, g)
    return {"removed": True, "path": path, "principal": f"{ptype}:{pname}"}


def remove_grant(db: Session, actor: Actor, g: Grant) -> None:
    if not at_least(role_for(db, actor, g.folder_path), "owner"):
        raise Forbidden("Only an Owner can change sharing")
    # Never let someone lock everybody out of a home folder.
    parts = g.folder_path.strip("/").split("/")
    if parts[0] == "users" and len(parts) == 2 and g.principal_type == "user":
        u = db.get(User, g.principal_id)
        if u and u.username == parts[1]:
            raise Forbidden("A person always owns their home folder")
    path = g.folder_path
    label = principal_label(db, g.principal_type, g.principal_id)["name"]
    audit.log(db, actor, "grant.remove", path, principal=f"{g.principal_type}:{label}", role=g.role)
    db.delete(g)
    db.commit()
    acl.on_change(db, path)


def who_has_access(db: Session, actor: Actor, path: str) -> list[dict[str, Any]]:
    path = paths.normalize(path)
    _need(db, actor, path, "viewer", "see who has access")
    out = []
    for g in db.scalars(select(Grant).where(Grant.folder_path.in_(paths.ancestors(path)))):
        out.append({"grant_id": g.id, "principal": principal_label(db, g.principal_type, g.principal_id),
                    "role": g.role, "via": g.folder_path, "inherited": g.folder_path != path,
                    "granted_at": g.created_at.isoformat() + "Z"})
    admins = db.scalar(select(func.count()).select_from(User).where(User.system_role == "admin", User.status == "active"))
    out.append({"grant_id": None, "principal": {"type": "role", "name": "admins", "members": admins},
                "role": "owner", "via": "system role", "inherited": True})
    out.sort(key=lambda x: -RANK[x["role"]])
    return out


def explain_access(db: Session, user: User, path: str) -> dict[str, Any]:
    """Can this person reach the path, with what role, and which grant gives it."""
    who = Actor(user=user, via="system")
    role = base_role(db, who, path)
    why: list[dict[str, Any]] = []
    if user.status != "active":
        reason = "suspended"
    elif who.is_admin:
        reason = "admin"
    else:
        gids = set(who.group_ids(db))
        for g in db.scalars(select(Grant).where(Grant.folder_path.in_(paths.ancestors(path)))):
            if (g.principal_type == "user" and g.principal_id == user.id) or \
               (g.principal_type == "group" and g.principal_id in gids):
                why.append({"grant_id": g.id, "principal": principal_label(db, g.principal_type, g.principal_id),
                            "role": g.role, "via": g.folder_path})
        why.sort(key=lambda x: -RANK[x["role"]])
        reason = "grant" if why else "no grant"
    return {"username": user.username, "status": user.status, "role": role, "can_read": role is not None,
            "can_write": at_least(role, "editor"), "reason": reason, "grants": why}


def my_role(db: Session, actor: Actor, path: str) -> str | None:
    return base_role(db, actor, paths.normalize(path))
