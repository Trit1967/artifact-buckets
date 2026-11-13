"""Writes the app's grants onto the folder tree as POSIX ACLs, which Samba shows to Windows as
NTFS permissions. With Samba's idmap 'rid' backend an AD account's uid/gid is
AB_IDMAP_BASE + RID, so the app can name AD users and groups without asking Samba.

Mapping: Viewer -> read (r-x on folders, r-- on files); Editor and Owner -> modify (rwx / rw-).
Only AD users and groups (which have id numbers) appear on the share; local accounts reach
files through the web app and Claude Code only.

The app is the only writer: Samba runs with 'nt acl support = no', so nobody changes
permissions from Explorer, and reconcile() puts back anything changed on the server.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, paths
from .db import Grant, Group, User

PERM_DIR = {"viewer": "r-x", "editor": "rwx", "owner": "rwx"}
RANK = {"viewer": 1, "editor": 2, "owner": 3}


def enabled() -> bool:
    return config.settings.smb_enabled and shutil.which("setfacl") is not None


def sid_rid(sid: str | None) -> int | None:
    if not sid:
        return None
    try:
        return int(sid.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def id_from_sid(sid: str | None) -> int | None:
    rid = sid_rid(sid)
    return None if rid is None else config.settings.idmap_base + rid


def desired(db: Session, path: str) -> dict[tuple[str, int], str]:
    """{('u'|'g', id): role} for a folder, from grants on it and its parents, plus admins."""
    out: dict[tuple[str, int], str] = {}

    def put(k: tuple[str, int], role: str) -> None:
        if k not in out or RANK[role] > RANK[out[k]]:
            out[k] = role

    for g in db.scalars(select(Grant).where(Grant.folder_path.in_(paths.ancestors(path)))):
        if g.principal_type == "user":
            u = db.get(User, g.principal_id)
            if u and u.uid_number and u.status == "active":
                put(("u", u.uid_number), g.role)
        else:
            grp = db.get(Group, g.principal_id)
            if grp and grp.gid_number:
                put(("g", grp.gid_number), g.role)
    admins = db.scalar(select(Group).where(Group.ad_name == config.settings.ldap_admin_group))
    if admins and admins.gid_number:
        put(("g", admins.gid_number), "owner")
    return out


def _spec(entries: dict[tuple[str, int], str], is_dir: bool) -> str:
    base = ["u::rwx" if is_dir else "u::rw-", "g::---", "o::---", "m::rwx" if is_dir else "m::rw-"]
    for (kind, num), role in sorted(entries.items()):
        p = PERM_DIR[role] if is_dir else PERM_DIR[role].replace("x", "-")
        base.append(f"{kind}:{num}:{p}")
    return ",".join(base)


def _setfacl(target: str, spec: str, default: bool = False) -> None:
    args = ["setfacl", "-d", "--set", spec, target] if default else ["setfacl", "--set", spec, target]
    subprocess.run(args, check=True, capture_output=True)


def apply_dir(db: Session, path: str, recursive: bool = True) -> int:
    """Write ACLs on a folder (and, by default, everything inside it). Returns items touched."""
    if not enabled():
        return 0
    root = paths.disk(path)
    if not os.path.isdir(root):
        return 0
    n = 0
    cache: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel = os.path.relpath(dirpath, paths.tree_root())
        logical = "/" + rel.replace(os.sep, "/") if rel != "." else "/"
        ent = cache.setdefault(logical, desired(db, logical))
        spec = _spec(ent, True)
        _setfacl(dirpath, spec)
        _setfacl(dirpath, spec, default=True)
        n += 1
        fspec = _spec(ent, False)
        for f in filenames:
            if not f.startswith("."):
                _setfacl(os.path.join(dirpath, f), fspec)
                n += 1
        if not recursive:
            break
    return n


def read_acl(target: str) -> dict[tuple[str, int], str]:
    out = subprocess.run(["getfacl", "--omit-header", "--numeric", "--absolute-names", target],
                         check=True, capture_output=True, text=True).stdout
    got: dict[tuple[str, int], str] = {}
    for line in out.splitlines():
        line = line.split("#")[0].strip()
        parts = line.split(":")
        if len(parts) == 3 and parts[0] in ("user", "group") and parts[1]:
            got[(parts[0][0], int(parts[1]))] = parts[2]
    return got


def drift(db: Session, limit: int = 200, max_items: int = 20000) -> list[dict[str, Any]]:
    """Folders and files whose ACL on disk no longer matches the grants."""
    if not enabled():
        return []
    out: list[dict[str, Any]] = []
    seen = 0
    root = paths.tree_root()
    for top in ("users", "shared"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, top)):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            rel = os.path.relpath(dirpath, root)
            logical = "/" + rel.replace(os.sep, "/")
            want_role = desired(db, logical)
            want_dir = {k: PERM_DIR[r] for k, r in want_role.items()}
            want_file = {k: v.replace("x", "-") for k, v in want_dir.items()}
            items = [(logical, dirpath, want_dir)] + [
                (logical.rstrip("/") + "/" + f, os.path.join(dirpath, f), want_file)
                for f in filenames if not f.startswith(".")]
            for lp, disk, want in items:
                seen += 1
                if os.path.islink(disk):
                    continue
                have = read_acl(disk)
                if want != have:
                    out.append({"path": lp, "expected": {f"{k[0]}:{k[1]}": v for k, v in want.items()},
                                "found": {f"{k[0]}:{k[1]}": v for k, v in have.items()}})
                    if len(out) >= limit:
                        return out
                if seen >= max_items:
                    return out
    return out


# Hooks called by the rest of the app ---------------------------------------

def on_change(db: Session, path: str) -> None:
    if not enabled():
        return
    if os.path.isfile(paths.disk(path)):
        on_new_file(db, path)
    else:
        apply_dir(db, path)


def on_user_change(db: Session, user: User) -> None:
    """A person was suspended or reactivated: rewrite the folders their own grants touch.
    (Group grants follow AD membership, and suspended people are dropped from AD groups' reach
    by the next directory sync; their direct entries go now.)"""
    if not enabled() or not user.uid_number:
        return
    db.flush()
    done: list[str] = []
    for fp in sorted({g.folder_path for g in db.scalars(select(Grant).where(
            Grant.principal_type == "user", Grant.principal_id == user.id))}):
        if any(paths.is_under(fp, d) for d in done):
            continue
        apply_dir(db, fp)
        done.append(fp)


def on_new_dir(path: str) -> None:
    """New folders inherit the parent's default ACL automatically; nothing to do."""
    return None


def on_new_file(db: Session, path: str) -> None:
    """Files the app writes are created in a staging folder and renamed into place, so they don't
    inherit the folder's default ACL. Set it explicitly from the grants on the folder."""
    if not enabled():
        return
    fp = paths.disk(path)
    if os.path.isfile(fp):
        _setfacl(fp, _spec(desired(db, paths.parent(path)), False))


def on_group_change(db: Session, group: Group) -> None:
    """Membership lives in AD, so the share follows it without rewriting ACLs."""
    return None


def reconcile_all(db: Session) -> int:
    if not enabled():
        return 0
    return apply_dir(db, "/users") + apply_dir(db, "/shared")
