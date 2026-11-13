"""The single permission check used by the web app, the API, the MCP server and the SMB ACL writer.

A grant gives a user or group one role on a folder and everything below it.
The highest role from any matching grant wins. There are no deny rules.
Admins are Owner everywhere. Auditors get no file access from their system role.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import paths
from .db import Grant, Membership, Token, User

RANK = {"viewer": 1, "editor": 2, "owner": 3}
ROLES = tuple(RANK)


@dataclass
class Actor:
    """Who is acting, and through which door."""
    user: User
    via: str = "web"  # web | mcp | api | smb | system
    token: Token | None = None
    ip: str | None = None
    session_id: str | None = None  # Claude Code session, when known
    _gids: list[int] | None = field(default=None, repr=False)

    @property
    def personal_token(self) -> bool:
        # Personal and sync tokens never carry admin or auditor powers.
        return self.token is not None and self.token.kind in ("personal", "sync")

    @property
    def is_admin(self) -> bool:
        """Admin powers need a signed-in session (web or Claude Code), never a personal token."""
        return self.user.system_role == "admin" and self.user.status == "active" and not self.personal_token

    @property
    def is_auditor(self) -> bool:
        return (self.user.system_role in ("auditor", "admin") and self.user.status == "active"
                and not self.personal_token)

    def group_ids(self, db: Session) -> list[int]:
        if self._gids is None:
            self._gids = list(db.scalars(select(Membership.group_id).where(Membership.user_id == self.user.id)))
        return self._gids


def _max(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if RANK[a] >= RANK[b] else b


def _cap(role: str | None, cap: str) -> str | None:
    if role is None:
        return None
    return role if RANK[role] <= RANK[cap] else cap


def _principal_filter(actor: Actor, db: Session):
    gids = actor.group_ids(db)
    conds = [(Grant.principal_type == "user") & (Grant.principal_id == actor.user.id)]
    if gids:
        conds.append((Grant.principal_type == "group") & (Grant.principal_id.in_(gids)))
    return or_(*conds)


def base_role(db: Session, actor: Actor, path: str) -> str | None:
    """Role from system role and grants, ignoring token limits."""
    if actor.user.status != "active":
        return None
    if actor.is_admin:
        return "owner"
    rows = db.scalars(select(Grant.role).where(Grant.folder_path.in_(paths.ancestors(path)),
                                               _principal_filter(actor, db)))
    role = None
    for r in rows:
        role = _max(role, r)
    return role


def role_for(db: Session, actor: Actor, path: str) -> str | None:
    """Effective role, including the limits of the token used (read-only scope, folder list)."""
    role = base_role(db, actor, path)
    tok = actor.token
    if tok is not None and role is not None:
        if tok.folders and not any(paths.is_under(path, f) for f in tok.folders):
            return None
        if tok.scope == "read":
            role = _cap(role, "viewer")
    return role


def can_traverse(db: Session, actor: Actor, path: str) -> bool:
    """True when the person may see this folder in a listing: they can read it,
    or it leads to a folder they were granted (access-based enumeration)."""
    if role_for(db, actor, path) is not None:
        return True
    if actor.user.status != "active":
        return False
    prefix = "/" if path == "/" else path.rstrip("/") + "/"
    q = select(Grant.folder_path).where(Grant.folder_path.like(prefix.replace("%", r"\%").replace("_", r"\_") + "%", escape="\\"),
                                        _principal_filter(actor, db))
    for fp in db.scalars(q):
        tok = actor.token
        if tok is not None and tok.folders and not any(paths.is_under(fp, f) or paths.is_under(f, path) for f in tok.folders):
            continue
        return True
    return False


def at_least(role: str | None, needed: str) -> bool:
    return role is not None and RANK[role] >= RANK[needed]
