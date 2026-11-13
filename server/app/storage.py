"""The storage pool.

Each directory under AB_VOLUMES_ROOT is one volume (a disk, an NFS share, an iSCSI LUN
mounted by the lab host). In the lab, AB_TREE_ROOT is a mergerfs pool of the active
volumes; this module changes mergerfs's branch list at runtime through its control file,
so adding or draining a volume needs no restart and moves no data up front.

States: pending (found, not used) -> active -> draining (no new files, files moved
off in the background) -> drained (safe to unmount).
"""
from __future__ import annotations

import os
import shutil
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, config, paths
from .db import Volume, get_setting, set_setting, utcnow
from .errors import BadRequest, Conflict, NotFound
from .perms import Actor

MARKER = ".artifact-buckets-volume"
POLICIES = {"mfs": "Most free space", "ff": "Fill in order", "epmfs": "Keep folders on their volume"}


def _root() -> str:
    r = os.path.realpath(config.settings.volumes_root)
    os.makedirs(r, exist_ok=True)
    return r


def _usage(path: str) -> tuple[int, int]:
    try:
        du = shutil.disk_usage(path)
        return du.total, du.used
    except OSError:
        return 0, 0


def scan(db: Session) -> list[Volume]:
    """Find mounted volumes. New ones become 'pending'; vanished ones 'missing'."""
    root = _root()
    seen = set()
    for e in sorted(os.scandir(root), key=lambda e: e.name):
        if not e.is_dir() or e.name.startswith("."):
            continue
        seen.add(e.name)
        v = db.scalar(select(Volume).where(Volume.name == e.name))
        if v is None:
            status = "active" if os.path.exists(os.path.join(e.path, MARKER)) else "pending"
            db.add(Volume(name=e.name, path=e.path, status=status, added_at=utcnow() if status == "active" else None))
        elif v.status == "missing":
            v.status = "active" if os.path.exists(os.path.join(e.path, MARKER)) else "pending"
    for v in db.scalars(select(Volume)):
        if v.name not in seen and v.status != "drained":
            v.status = "missing"
    db.commit()
    return list(db.scalars(select(Volume).order_by(Volume.name)))


def _checks(path: str) -> dict[str, bool]:
    probe = os.path.join(path, ".ab-probe")
    ok_write = False
    try:
        with open(probe, "w") as f:
            f.write("ok")
        ok_write = True
        os.unlink(probe)
    except OSError:
        pass
    return {"mounted": os.path.isdir(path), "readable": os.access(path, os.R_OK), "writable": ok_write}


def info(db: Session, v: Volume) -> dict[str, Any]:
    total, used = _usage(v.path)
    files = 0
    if v.status in ("active", "draining") and config.settings.storage_mode == "mergerfs":
        for _, _, fs_ in os.walk(v.path):
            files += len(fs_)
    return {"id": v.id, "name": v.name, "path": host_path(v.path), "status": v.status, "label": v.label,
            "total_bytes": total, "used_bytes": used, "files": files,
            "drain": {"moved": v.drain_moved, "total": v.drain_total} if v.status in ("draining", "drained") else None,
            "checks": _checks(v.path) if v.status == "pending" else None,
            "added_at": v.added_at.isoformat() + "Z" if v.added_at else None}


def pool_info(db: Session) -> dict[str, Any]:
    vols = scan(db)
    active = [v for v in vols if v.status in ("active", "draining")]
    total = used = 0
    if config.settings.storage_mode == "mergerfs":
        for v in active:
            t, u = _usage(v.path)
            total += t
            used += u
    else:
        total, used = _usage(paths.tree_root())
    from . import zfs  # protected storage: the pool's own numbers, not the host disk's
    if zfs.available():
        try:
            st = zfs.call("status", timeout=10)
            total, used = st["size"], st["alloc"]
        except Exception:
            pass
    return {"mode": config.settings.storage_mode, "total_bytes": total, "used_bytes": used,
            "policy": get_setting(db, "placement_policy", "mfs"), "policies": POLICIES,
            "fstab": fstab_line(db) if config.settings.storage_mode == "mergerfs" else None,
            "volumes": [info(db, v) for v in vols]}


# ---- mergerfs control ------------------------------------------------------

def host_path(container_path: str) -> str:
    """mergerfs runs on the host, so it needs the host's path for a volume, not the container's."""
    host_root = config.settings.volumes_host_root
    if not host_root:
        return container_path
    rel = os.path.relpath(container_path, os.path.realpath(config.settings.volumes_root))
    return os.path.join(host_root, rel)


def _branches(db: Session) -> str:
    parts = []
    for v in db.scalars(select(Volume).where(Volume.status.in_(("active", "draining"))).order_by(Volume.name)):
        parts.append(f"{host_path(v.path)}={'RW' if v.status == 'active' else 'NC'}")
    return ":".join(parts)


def fstab_line(db: Session) -> str:
    """The pool line for /etc/fstab with today's branches, so the pool comes back the same after a reboot."""
    br = _branches(db) or "/srv/buckets/volumes/disk1=RW"
    policy = get_setting(db, "placement_policy", "mfs")
    return (f"{br} /srv/buckets/pool fuse.mergerfs allow_other,cache.files=off,category.create={policy},"
            "func.getattr=newest,dropcacheonclose=true,xattr=passthrough,posix_acl=true,fsname=buckets 0 0")


def apply_pool(db: Session) -> str:
    """Tell mergerfs the current branch list and create policy. Returns the branch string."""
    br = _branches(db)
    if config.settings.storage_mode == "mergerfs" and br:
        ctl = os.path.join(paths.tree_root(), ".mergerfs")
        os.setxattr(ctl, "user.mergerfs.branches", br.encode())
        os.setxattr(ctl, "user.mergerfs.category.create", str(get_setting(db, "placement_policy", "mfs")).encode())
    return br


def _get(db: Session, vid: int) -> Volume:
    v = db.get(Volume, vid)
    if v is None:
        raise NotFound("No such volume")
    return v


def add_to_pool(db: Session, actor: Actor, vid: int) -> dict[str, Any]:
    v = _get(db, vid)
    if v.status not in ("pending", "drained"):
        raise Conflict(f"Volume is {v.status}")
    c = _checks(v.path)
    if not all(c.values()):
        raise BadRequest("The volume must be mounted, readable and writable", checks=c)
    with open(os.path.join(v.path, MARKER), "w") as f:
        f.write(v.name)
    v.status, v.added_at, v.drain_moved, v.drain_total = "active", utcnow(), 0, 0
    apply_pool(db)
    audit.log(db, actor, "volume.add", v.name)
    db.commit()
    return info(db, v)


def start_drain(db: Session, actor: Actor, vid: int) -> dict[str, Any]:
    v = _get(db, vid)
    if v.status != "active":
        raise Conflict(f"Volume is {v.status}")
    others = db.scalars(select(Volume).where(Volume.status == "active", Volume.id != v.id)).all()
    if not others:
        raise Conflict("Add another volume before draining the last one")
    v.status, v.drain_moved = "draining", 0
    v.drain_total = sum(len([n for n in f if n != MARKER]) for _, _, f in os.walk(v.path))
    apply_pool(db)
    audit.log(db, actor, "volume.drain", v.name)
    db.commit()
    return info(db, v)


def stop_drain(db: Session, actor: Actor, vid: int) -> dict[str, Any]:
    v = _get(db, vid)
    if v.status not in ("draining", "drained"):
        raise Conflict(f"Volume is {v.status}")
    v.status = "active"
    apply_pool(db)
    audit.log(db, actor, "volume.undrain", v.name)
    db.commit()
    return info(db, v)


def _copy(src: str, dst: str) -> None:
    """Copy keeping contents, times, mode, ACLs (copy2 carries xattrs) and the owner."""
    shutil.copy2(src, dst)
    st = os.stat(src)
    try:
        os.chown(dst, st.st_uid, st.st_gid)
    except PermissionError:
        pass


def _make_parents(src_root: str, dst_root: str, rel_dir: str) -> None:
    """Create the folders on the target with the same owner and ACLs as on the draining disk."""
    if rel_dir in ("", "."):
        return
    acc = ""
    for part in rel_dir.split(os.sep):
        acc = os.path.join(acc, part)
        d = os.path.join(dst_root, acc)
        if not os.path.isdir(d):
            os.mkdir(d)
            s = os.path.join(src_root, acc)
            shutil.copystat(s, d)
            st = os.stat(s)
            try:
                os.chown(d, st.st_uid, st.st_gid)
            except PermissionError:
                pass


def drain_step(db: Session, vid: int, max_files: int = 500) -> int:
    """Move up to max_files off a draining volume onto the active volume with the most free space.
    Works on the branch directories directly, so mergerfs sees each file in its new place."""
    v = _get(db, vid)
    if v.status != "draining":
        return 0
    targets = list(db.scalars(select(Volume).where(Volume.status == "active")))
    if not targets:
        return 0
    moved = 0
    for dirpath, _, files in os.walk(v.path):
        for name in files:
            if name == MARKER:
                continue
            src = os.path.join(dirpath, name)
            rel = os.path.relpath(src, v.path)
            dest_vol = max(targets, key=lambda t: shutil.disk_usage(t.path).free)
            dst = os.path.join(dest_vol.path, rel)
            _make_parents(v.path, dest_vol.path, os.path.dirname(rel))
            if os.path.lexists(dst):
                # The same path on two disks: the pool shows the newer one, so keep that and park the other
                # in a hidden folder an admin can look at, instead of overwriting anything.
                keep_src = os.stat(src).st_mtime_ns > os.stat(dst).st_mtime_ns
                parked = os.path.join(dest_vol.path, ".drain-conflicts", rel + "." + str(int(utcnow().timestamp())))
                os.makedirs(os.path.dirname(parked), exist_ok=True)
                if keep_src:
                    os.replace(dst, parked)
                    _copy(src, dst)
                else:
                    _copy(src, parked)
                audit.log(db, None, "volume.drain_conflict", "/" + rel.replace(os.sep, "/"),
                          ok=False, kept="moved copy" if keep_src else "existing copy", parked=parked)
            else:
                _copy(src, dst)
            os.unlink(src)
            moved += 1
            if moved >= max_files:
                v.drain_moved += moved
                db.commit()
                return moved
    v.drain_moved += moved
    remaining = sum(len([f for f in fs_ if f != MARKER]) for _, _, fs_ in os.walk(v.path))
    if remaining == 0:
        v.status = "drained"
        apply_pool(db)
        audit.log(db, None, "volume.drained", v.name)
    db.commit()
    return moved


def set_policy(db: Session, actor: Actor, policy: str) -> None:
    if policy not in POLICIES:
        raise BadRequest("Unknown placement policy")
    set_setting(db, "placement_policy", policy)
    apply_pool(db)
    audit.log(db, actor, "storage.policy", policy)
    db.commit()


def set_label(db: Session, actor: Actor, vid: int, label: str) -> None:
    v = _get(db, vid)
    v.label = label[:256]
    db.commit()
