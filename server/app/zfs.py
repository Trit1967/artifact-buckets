"""Protected storage: the app's side of the ZFS helper (deploy/zfs/bucketsd.py).

The helper runs as root on the host and does the ZFS work. The app only asks it for fixed operations over a
Unix socket, records each change in the audit log, and turns restore points into files people can use.
Words on screen avoid ZFS terms: restore points (snapshots), health check (scrub), disks.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
from typing import Any

from sqlalchemy.orm import Session

from . import audit, config, fs, paths
from .errors import BadRequest, NotFound, Unavailable
from .perms import Actor


def available() -> bool:
    p = config.settings.zfs_socket
    return bool(p) and os.path.exists(p)


def call(op: str, timeout: float = 30, **args: Any) -> Any:
    if not available():
        raise Unavailable("Protected storage isn't set up on this server")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(config.settings.zfs_socket)
        s.sendall((json.dumps({"op": op, "args": args}) + "\n").encode())
        line = s.makefile("r").readline()
    except OSError as e:
        raise Unavailable(f"The storage helper didn't answer ({e.__class__.__name__})") from e
    finally:
        s.close()
    if not line:
        raise Unavailable("The storage helper closed the connection")
    res = json.loads(line)
    if not res.get("ok"):
        raise BadRequest(res.get("error") or "The storage helper refused")
    return res.get("result")


def status() -> dict[str, Any]:
    st = call("status")
    disks = [d for v in st["vdevs"] for d in v["disks"]]
    bad = [d for d in disks if d["health"] != "ONLINE" or d["read"] or d["write"] or d["cksum"]]
    mirrored = all(v["type"] != "single" for v in st["vdevs"])
    st["summary"] = {
        "healthy": st["health"] == "ONLINE" and not bad,
        "disks": len(disks),
        "mirrored": mirrored,
        "file_backed": any(d["file"] for d in disks),
        "problems": [{"disk": d["id"], "name": d["name"], "health": d["health"],
                      "errors": d["read"] + d["write"] + d["cksum"]} for d in bad],
    }
    return st


def _change(db: Session, actor: Actor, action: str, target: str, op: str, **args: Any) -> Any:
    try:
        res = call(op, timeout=120, **args)
    except BadRequest as e:
        audit.log(db, actor, action, target, ok=False, reason=e.message)
        db.commit()
        raise
    audit.log(db, actor, action, target, **{k: v for k, v in args.items() if k not in ("host",)})
    db.commit()
    return res


def add_disks(db: Session, actor: Actor, disks: list[str], layout: str) -> Any:
    if layout not in ("mirror", "single"):
        raise BadRequest("Layout is mirror or single")
    return _change(db, actor, "storage.disk_add", ", ".join(disks), "add_disks", disks=disks, layout=layout)


def mirror_disk(db: Session, actor: Actor, existing: str, new: str) -> Any:
    return _change(db, actor, "storage.disk_mirror", new, "mirror_disk", existing=existing, new=new)


def replace_disk(db: Session, actor: Actor, old: str, new: str) -> Any:
    return _change(db, actor, "storage.disk_replace", f"{old} → {new}", "replace_disk", old=old, new=new)


def grow(db: Session, actor: Actor, disk: str, gigabytes: int) -> Any:
    return _change(db, actor, "storage.grow", disk, "grow", disk=disk, gigabytes=gigabytes)


def health_check(db: Session, actor: Actor) -> Any:
    return _change(db, actor, "storage.health_check", "", "scrub")


def set_keep(db: Session, actor: Actor, keep: dict[str, int]) -> Any:
    return _change(db, actor, "storage.schedule", "", "set_keep", **keep)


def set_backup(db: Session, actor: Actor, kind: str | None, host: str | None, dataset: str | None) -> Any:
    return _change(db, actor, "storage.backup_target", f"{host + ':' if host else ''}{dataset or 'off'}", "set_backup",
                   kind=kind, host=host, dataset=dataset)


def backup_now(db: Session, actor: Actor) -> Any:
    res = call("backup_now", timeout=3600)
    audit.log(db, actor, "storage.backup", res.get("dataset", ""), ok=not res.get("last_error"), error=res.get("last_error"))
    db.commit()
    return res


def make_point(db: Session, actor: Actor) -> Any:
    return _change(db, actor, "storage.restore_point", "", "snapshot_now")


def points() -> list[dict[str, Any]]:
    return call("snapshots")


def browse(point: str, path: str) -> list[dict[str, Any]]:
    path = paths.normalize(path)
    if len(path.strip("/").split("/")) < 1 or path == "/":
        return [{"name": "users", "folder": True, "size": None, "mtime": None},
                {"name": "shared", "folder": True, "size": None, "mtime": None}]
    return call("list_at", snapshot=point, path=path)


def restore(db: Session, actor: Actor, point: str, path: str) -> dict[str, Any]:
    """Bring a file or folder back as it was at a restore point.

    A file comes back as a new version of the current file (or recreated where it was, if it's gone). A folder
    comes back beside the current one, named "<name> (restored <date>)", so nothing current is overwritten."""
    path = paths.normalize(path)
    if len(path.strip("/").split("/")) < 3:
        raise BadRequest("Pick a file or a folder inside someone's home or a team folder")
    staged = call("stage", timeout=600, snapshot=point, path=path)
    src = os.path.join(paths.tree_root(), staged["staged"])
    t = point.split(".", 1)[-1]  # ab-daily.20261113-0100
    label = f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[9:11]}.{t[11:13]}" if len(t) == 13 else point
    meta = fs.SaveMeta(source="restore")
    try:
        if not staged["folder"]:
            with open(src, "rb") as fh:
                res = fs.save_file(db, actor, path, fh.read(), meta)
            target, count = path, 1
        else:
            target = f"{path} (restored {label})"
            if os.path.exists(paths.disk(target)):
                raise BadRequest(f"{paths.name(target)} is already there")
            count = 0
            for dp, dirs, files in os.walk(src):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                rel = os.path.relpath(dp, src)
                dest_dir = target if rel == "." else paths.join(target, *rel.split(os.sep))
                fs.ensure_dir(dest_dir, actor.user)
                for f in files:
                    if f.startswith("."):
                        continue
                    with open(os.path.join(dp, f), "rb") as fh:
                        fs.save_file(db, actor, paths.join(dest_dir, f), fh.read(), meta)
                    count += 1
            res = {"path": target}
    finally:
        shutil.rmtree(os.path.dirname(src), ignore_errors=True)
    audit.log(db, actor, "storage.restore", target, point=point, source=path, files=count)
    db.commit()
    return {**res, "restored_to": target, "files": count, "point": point}
