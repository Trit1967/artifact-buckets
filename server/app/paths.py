"""Logical paths (what people see, e.g. /shared/design/flows) and their place on disk."""
from __future__ import annotations

import os
import re

from . import config
from .errors import BadRequest

TOP_LEVEL = ("users", "shared")
# Characters Windows (and so the SMB share) cannot store in a name.
_BAD = re.compile(r'[\\<>:"|?*\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]')
# Names Windows can't open over the share (CON, NUL, COM1.txt and so on).
_RESERVED = re.compile(r'^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$', re.I)
# (The last ranges are zero-width and direction marks: "\u202elmth.exe" would display as "exe.html".)
MAX_PATH = 1024
MAX_NAME = 255


def normalize(path: str | None) -> str:
    """Return a clean absolute logical path or raise BadRequest.

    Rules: starts at '/', no empty, '.' or '..' parts, no names starting with '.'
    (reserved for .versions/.trash), no characters Windows cannot store, and the
    first part must be 'users' or 'shared'.
    """
    if path is None:
        raise BadRequest("Path is required")
    p = path
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > MAX_PATH:
        raise BadRequest("Path is too long")
    parts = [x for x in p.split("/")]
    clean: list[str] = []
    for i, part in enumerate(parts):
        if part == "":
            if i == 0 or i == len(parts) - 1:
                continue
            raise BadRequest("Path has an empty part")
        if part in (".", ".."):
            raise BadRequest("Path may not contain '.' or '..'")
        if part.startswith("."):
            raise BadRequest("Names may not start with a dot")
        if part != part.strip() or part.endswith("."):
            raise BadRequest("Names may not start or end with spaces, or end with a dot")
        if _BAD.search(part):
            raise BadRequest(f"Name '{part}' contains a character that is not allowed")
        if _RESERVED.match(part):
            raise BadRequest(f"'{part}' is a name Windows reserves, so it couldn't be opened from the share")
        if len(part.encode()) > MAX_NAME:
            raise BadRequest("Name is too long")
        clean.append(part)
    if not clean:
        return "/"
    if clean[0] not in TOP_LEVEL:
        raise BadRequest("Paths must start with /users or /shared")
    return "/" + "/".join(clean)


def parent(path: str) -> str:
    if path == "/":
        return "/"
    head = path.rsplit("/", 1)[0]
    return head or "/"


def name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def join(base: str, *names: str) -> str:
    out = base.rstrip("/")
    for n in names:
        out += "/" + n
    return normalize(out)


def ancestors(path: str) -> list[str]:
    """'/a/b/c' -> ['/', '/a', '/a/b', '/a/b/c']."""
    out = ["/"]
    if path == "/":
        return out
    acc = ""
    for part in path.strip("/").split("/"):
        acc += "/" + part
        out.append(acc)
    return out


def is_under(path: str, root: str) -> bool:
    if root == "/":
        return True
    return path == root or path.startswith(root.rstrip("/") + "/")


def tree_root() -> str:
    return os.path.realpath(config.settings.tree_root)


def disk(path: str) -> str:
    """Absolute disk location of a logical path, refusing anything that escapes the tree."""
    root = tree_root()
    full = os.path.join(root, path.lstrip("/"))
    real = os.path.realpath(full)
    if real != root and not real.startswith(root + os.sep):
        raise BadRequest("Path leaves the storage tree")
    if real != os.path.normpath(full):
        # A symlink somewhere on the way (made over SMB, or unpacked from an archive). The app never
        # makes links, and following one could reach another person's folder, so refuse.
        raise BadRequest("Path goes through a link, which is not allowed")
    return full


def system_dir(kind: str) -> str:
    """Hidden folders at the tree root: .versions, .trash, .tmp."""
    d = os.path.join(tree_root(), "." + kind)
    os.makedirs(d, exist_ok=True)
    return d


def protected(path: str) -> bool:
    """Structural folders nobody but an admin may move or delete."""
    parts = path.strip("/").split("/") if path != "/" else []
    if len(parts) <= 2:
        return True
    # Each home's inbox and projects folders, and each team's projects folder, are where saves are routed.
    return len(parts) == 3 and (
        (parts[0] == "users" and parts[2] in ("inbox", "projects")) or (parts[0] == "shared" and parts[2] == "projects"))


def quota_root(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) >= 2:
        return "/" + "/".join(parts[:2])
    return None
