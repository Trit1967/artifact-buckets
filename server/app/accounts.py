"""People and groups. AD is the source of truth when connected; local accounts cover the bootstrap
admin and labs without a directory."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import acl, audit, fs, paths
from .db import File, Grant, Group, Membership, Token, User, WebSession, set_setting, utcnow
from .errors import BadRequest, Conflict, Forbidden, NotFound
from .perms import Actor
from .security import hash_password

USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
GROUPNAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def require_admin(actor: Actor) -> None:
    if actor.personal_token and actor.user.system_role == "admin":
        raise Forbidden("Admin changes need a signed-in session, not a personal token")
    if not actor.is_admin:
        raise Forbidden("Only admins can do this")


def require_auditor(actor: Actor) -> None:
    if not actor.is_auditor:
        raise Forbidden("Only admins and auditors can see this")


def provision_home(db: Session, user: User) -> None:
    """Create /users/<name> with inbox/ and projects/, owned by the person."""
    home = f"/users/{user.username}"
    for p in (home, home + "/inbox", home + "/projects"):
        fs.ensure_dir(p, user)
    if not db.scalar(select(Grant).where(Grant.folder_path == home, Grant.principal_type == "user", Grant.principal_id == user.id)):
        db.add(Grant(folder_path=home, principal_type="user", principal_id=user.id, role="owner"))
    db.flush()
    acl.on_change(db, home)


def provision_group_folder(db: Session, group: Group, role: str = "editor") -> str:
    root = f"/shared/{group.name}"
    fs.ensure_dir(root)
    fs.ensure_dir(root + "/projects")
    if not db.scalar(select(Grant).where(Grant.folder_path == root, Grant.principal_type == "group", Grant.principal_id == group.id)):
        db.add(Grant(folder_path=root, principal_type="group", principal_id=group.id, role=role))
    db.flush()
    acl.on_change(db, root)
    return root


def create_user(db: Session, actor: Actor | None, *, username: str, email: str | None = None, display_name: str = "",
                password: str | None = None, system_role: str = "member", source: str = "local",
                status: str = "active", **extra: Any) -> User:
    username = username.strip().lower()
    if not USERNAME.match(username):
        raise BadRequest("Usernames use lower-case letters, digits, dot, dash and underscore")
    if system_role not in ("member", "auditor", "admin"):
        raise BadRequest("Unknown system role")
    if db.scalar(select(User).where(User.username == username)):
        raise Conflict("That username is taken")
    if password is not None and len(password) < 12:
        raise BadRequest("Passwords need at least 12 characters")
    u = User(username=username, email=email, display_name=display_name or username,
             password_hash=hash_password(password) if password else None,
             system_role=system_role, source=source, status=status, **extra)
    db.add(u)
    db.flush()
    provision_home(db, u)
    audit.log(db, actor, "user.create", username, role=system_role, source=source)
    return u


def user_info(db: Session, u: User) -> dict[str, Any]:
    groups = list(db.scalars(select(Group.name).join(Membership, Membership.group_id == Group.id).where(Membership.user_id == u.id)))
    home = f"/users/{u.username}"
    return {"id": u.id, "username": u.username, "email": u.email, "display_name": u.display_name or u.username,
            "system_role": u.system_role, "status": u.status, "source": u.source, "groups": sorted(groups),
            "home": home, "used_bytes": fs.usage(db, home), "quota_bytes": fs.quota_for(db, home),
            "last_active": u.last_active.isoformat() + "Z" if u.last_active else None,
            "has_password": bool(u.password_hash), "uid_number": u.uid_number}


def update_user(db: Session, actor: Actor, user_id: int, **changes: Any) -> User:
    require_admin(actor)
    u = db.get(User, user_id)
    if u is None:
        raise NotFound("No such person")
    if "system_role" in changes and changes["system_role"] is not None:
        if changes["system_role"] not in ("member", "auditor", "admin"):
            raise BadRequest("Unknown system role")
        if u.id == actor.user.id and changes["system_role"] != "admin":
            raise BadRequest("You cannot remove your own admin role")
        u.system_role = changes["system_role"]
    if "status" in changes and changes["status"] is not None:
        if changes["status"] not in ("active", "suspended"):
            raise BadRequest("Status must be active or suspended")
        if u.id == actor.user.id and changes["status"] != "active":
            raise BadRequest("You cannot suspend yourself")
        changed = u.status != changes["status"]
        u.status = changes["status"]
        # A status set by an admin is the admin's decision; directory sync must not undo it.
        set_setting(db, f"suspended_by_sync:{u.id}", False)
        if u.status == "suspended":
            end_access(db, u)
        if changed:
            acl.on_user_change(db, u)
    if "quota_bytes" in changes and changes["quota_bytes"] is not None:
        q = int(changes["quota_bytes"])
        if q <= 0:
            raise BadRequest("Quota must be positive")
        u.quota_bytes = q
    if changes.get("password"):
        if len(changes["password"]) < 12:
            raise BadRequest("Passwords need at least 12 characters")
        u.password_hash = hash_password(changes["password"])
    if changes.get("display_name"):
        u.display_name = changes["display_name"][:256]
    audit.log(db, actor, "user.update", u.username, **{k: v for k, v in changes.items() if k != "password" and v is not None})
    db.commit()
    return u


def end_access(db: Session, u: User) -> None:
    """Sign a person out everywhere: web sessions and every token."""
    for s in db.scalars(select(WebSession).where(WebSession.user_id == u.id)):
        db.delete(s)
    for t in db.scalars(select(Token).where(Token.user_id == u.id, Token.revoked.is_(False))):
        t.revoked = True


def create_group(db: Session, actor: Actor | None, name: str, description: str = "", *, source: str = "local",
                 folder: bool = True, **extra: Any) -> Group:
    name = name.strip().lower()
    if not GROUPNAME.match(name):
        raise BadRequest("Group names use lower-case letters, digits, dash and underscore")
    if db.scalar(select(Group).where(Group.name == name)):
        raise Conflict("A group with that name exists")
    g = Group(name=name, description=description[:512], source=source, **extra)
    db.add(g)
    db.flush()
    if folder:
        provision_group_folder(db, g)
    audit.log(db, actor, "group.create", name, source=source)
    return g


def set_members(db: Session, group: Group, user_ids: set[int]) -> tuple[int, int]:
    current = set(db.scalars(select(Membership.user_id).where(Membership.group_id == group.id)))
    added, removed = user_ids - current, current - user_ids
    for uid in added:
        db.add(Membership(user_id=uid, group_id=group.id))
    for uid in removed:
        m = db.get(Membership, (uid, group.id))
        if m:
            db.delete(m)
    return len(added), len(removed)


def add_member(db: Session, actor: Actor, group_id: int, username: str) -> None:
    require_admin(actor)
    g = db.get(Group, group_id)
    u = db.scalar(select(User).where(User.username == username))
    if not g or not u:
        raise NotFound("No such group or person")
    if g.source == "ad":
        raise BadRequest("Members of AD groups are managed in Active Directory")
    if not db.get(Membership, (u.id, g.id)):
        db.add(Membership(user_id=u.id, group_id=g.id))
    audit.log(db, actor, "group.add_member", g.name, user=u.username)
    db.commit()
    acl.on_group_change(db, g)


def remove_member(db: Session, actor: Actor, group_id: int, username: str) -> None:
    require_admin(actor)
    g = db.get(Group, group_id)
    u = db.scalar(select(User).where(User.username == username))
    if not g or not u:
        raise NotFound("No such group or person")
    if g.source == "ad":
        raise BadRequest("Members of AD groups are managed in Active Directory")
    m = db.get(Membership, (u.id, g.id))
    if m:
        db.delete(m)
    audit.log(db, actor, "group.remove_member", g.name, user=u.username)
    db.commit()
    acl.on_group_change(db, g)


def group_info(db: Session, g: Group, with_members: bool = False) -> dict[str, Any]:
    n = db.scalar(select(func.count()).select_from(Membership).where(Membership.group_id == g.id))
    grants = [{"id": x.id, "path": x.folder_path, "role": x.role, "granted_at": x.created_at.isoformat() + "Z",
               "granted_by": (db.get(User, x.granted_by).username if x.granted_by and db.get(User, x.granted_by) else "system")}
              for x in db.scalars(select(Grant).where(Grant.principal_type == "group", Grant.principal_id == g.id).order_by(Grant.folder_path))]
    out = {"id": g.id, "name": g.name, "description": g.description, "source": g.source, "ad_name": g.ad_name,
           "members": n, "grants": grants, "folder": f"/shared/{g.name}"}
    if with_members:
        out["member_list"] = [{"username": u.username, "display_name": u.display_name or u.username, "email": u.email,
                               "added_at": m.added_at.isoformat() + "Z"}
                              for u, m in db.execute(select(User, Membership).join(Membership, Membership.user_id == User.id)
                                                     .where(Membership.group_id == g.id).order_by(User.username))]
    return out


def bootstrap(db: Session, username: str, password: str | None, email: str | None = None) -> User | None:
    """Create the first admin when the database is empty."""
    if db.scalar(select(func.count()).select_from(User)):
        return None
    for top in ("/users", "/shared"):
        fs.ensure_dir(top)
    if not password:
        return None
    u = create_user(db, None, username=username, password=password, system_role="admin", display_name="Administrator",
                    email=email)
    db.commit()
    return u


def touch(db: Session, u: User) -> None:
    now = utcnow()
    if not u.last_active or (now - u.last_active).total_seconds() > 60:
        u.last_active = now


def files_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(File).where(~File.path.like("/.trash/%"))) or 0)
