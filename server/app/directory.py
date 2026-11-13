"""Active Directory sync over LDAP.

Reads the AD groups whose name starts with the prefix (default "AB-") plus any extra
groups an admin chose, and the people in them. People and memberships are copied in;
nothing is ever written to AD. Disabled AD accounts, and people no longer in any synced
group, are suspended here and lose web, Claude Code and SMB access.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import accounts, acl, audit, config
from .db import Group, User, get_setting, set_setting, utcnow
from .errors import BadRequest
from .perms import Actor

UF_ACCOUNTDISABLE = 0x2


def connect():
    from ldap3 import ALL, Connection, Server
    s = config.settings
    if not s.ldap_enabled:
        raise BadRequest("Active Directory is not configured (set AB_LDAP_URL and AB_LDAP_BASE_DN)")
    server = Server(s.ldap_url, get_info=ALL, connect_timeout=5)
    return Connection(server, user=s.ldap_bind_dn, password=s.ldap_bind_password, auto_bind=True,
                      raise_exceptions=False, receive_timeout=15)


def _one(entry: Any, attr: str) -> Any:
    v = entry.get(attr) if isinstance(entry, dict) else None
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _attrs(conn) -> list[dict[str, Any]]:
    return [dict(e["attributes"]) | {"dn": e["dn"]} for e in conn.response if e.get("type") == "searchResEntry"]


def app_group_name(ad_name: str) -> str:
    p = config.settings.ldap_group_prefix
    base = ad_name[len(p):] if p and ad_name.lower().startswith(p.lower()) else ad_name
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in base.lower()).strip("-")[:64] or "group"


def list_ad_groups(conn) -> list[dict[str, Any]]:
    conn.search(config.settings.ldap_base_dn, "(objectClass=group)",
                attributes=["cn", "description", "objectSid", "member"])
    out = []
    for a in _attrs(conn):
        members = a.get("member") or []
        out.append({"dn": a["dn"], "cn": _one(a, "cn"), "description": _one(a, "description") or "",
                    "sid": str(_one(a, "objectSid") or ""), "members": len(members)})
    return sorted(out, key=lambda g: g["cn"].lower())


def _is_computer(entry: dict[str, Any], sam: str) -> bool:
    classes = [str(c).lower() for c in (entry.get("objectClass") or [])]
    return sam.endswith("$") or "computer" in classes


def _sync_flag(db: Session, u: User) -> bool:
    return bool(get_setting(db, f"suspended_by_sync:{u.id}", False))


def _set_sync_flag(db: Session, u: User, on: bool) -> None:
    set_setting(db, f"suspended_by_sync:{u.id}", on)


def _person(db: Session, actor: Actor | None, ue: dict[str, Any], stats: dict[str, Any]) -> User | None:
    """Find or create the app account for one AD person. AD people are matched by SID only:
    a username alone never hands an existing account (local, or a departed person's) to someone else."""
    sam = (_one(ue, "sAMAccountName") or "").lower()
    usid = str(_one(ue, "objectSid") or "")
    if not sam or not usid:
        stats["warnings"].append(f"{ue['dn']}: no account name or SID, skipped")
        return None
    if _is_computer(ue, sam):
        return None
    disabled = bool(int(_one(ue, "userAccountControl") or 0) & UF_ACCOUNTDISABLE)
    u = db.scalar(select(User).where(User.ad_sid == usid))
    if u is None:
        name = sam
        clash = db.scalar(select(User).where(User.username == name))
        if clash is not None:
            # Same name, different person (a local account, or someone who left). Keep them apart.
            stats["warnings"].append(f"{sam}: the name is taken by another account; created as a separate account")
            n = 2
            while db.scalar(select(User).where(User.username == f"{sam}-{n}")):
                n += 1
            name = f"{sam}-{n}"
        u = accounts.create_user(db, actor, username=name, email=_one(ue, "mail"),
                                 display_name=_one(ue, "displayName") or sam, source="ad",
                                 status="suspended" if disabled else "active",
                                 ad_dn=ue["dn"], ad_sid=usid, uid_number=acl.id_from_sid(usid))
        stats["created"] += 1
        if disabled:
            _set_sync_flag(db, u, True)
        return u
    u.ad_dn, u.uid_number = ue["dn"], acl.id_from_sid(usid)
    u.email = _one(ue, "mail") or u.email
    u.display_name = _one(ue, "displayName") or u.display_name
    if u.username != sam:
        stats["warnings"].append(f"{u.username} is now {sam} in AD; the app keeps the name {u.username}")
    if disabled:
        _deactivate(db, u, stats)
    else:
        _reactivate(db, u, stats)
    return u


def _deactivate(db: Session, u: User, stats: dict[str, Any]) -> None:
    """Sync only marks a suspension as its own when it made the change itself."""
    if u.status == "active":
        u.status = "suspended"
        _set_sync_flag(db, u, True)
        accounts.end_access(db, u)
        stats["suspended"] += 1


def _reactivate(db: Session, u: User, stats: dict[str, Any]) -> None:
    if u.status == "suspended" and _sync_flag(db, u):
        u.status = "active"
        _set_sync_flag(db, u, False)
        stats["reactivated"] += 1


def sync(db: Session, actor: Actor | None, conn=None) -> dict[str, Any]:
    s = config.settings
    conn = conn or connect()
    extra = set(get_setting(db, "ad_extra_groups", []) or [])
    prefix = s.ldap_group_prefix
    conn.search(s.ldap_base_dn, "(objectClass=group)", attributes=["cn", "description", "objectSid", "member"])
    ad_groups = [a for a in _attrs(conn)
                 if (_one(a, "cn") or "").lower().startswith(prefix.lower()) or _one(a, "cn") in extra]
    stats: dict[str, Any] = {"groups": 0, "users": 0, "created": 0, "suspended": 0, "reactivated": 0,
                             "skipped": 0, "warnings": []}
    seen_users: dict[int, User] = {}
    seen_groups: set[int] = set()
    group_members: dict[int, set[int]] = {}
    role_of: dict[int, str] = {}
    for a in ad_groups:
        cn = _one(a, "cn")
        sid = str(_one(a, "objectSid") or "")
        special = cn in (s.ldap_admin_group, s.ldap_auditor_group)
        g = (db.scalar(select(Group).where(Group.ad_sid == sid, Group.source == "ad")) if sid else None) \
            or db.scalar(select(Group).where(Group.ad_name == cn))
        if g is None:
            name = app_group_name(cn) if not special else cn.lower()
            if db.scalar(select(Group).where(Group.name == name)):
                name = (name + "-ad")[:64]
            g = accounts.create_group(db, actor, name, _one(a, "description") or "", source="ad",
                                      folder=not special, ad_name=cn, ad_dn=a["dn"], ad_sid=sid,
                                      gid_number=acl.id_from_sid(sid))
        else:
            if g.ad_name != cn:
                stats["warnings"].append(f"AD group {g.ad_name} is now {cn}; app group {g.name} follows it")
            g.ad_name, g.ad_dn, g.ad_sid, g.gid_number = cn, a["dn"], sid, acl.id_from_sid(sid)
            if _one(a, "description"):
                g.description = _one(a, "description")
        seen_groups.add(g.id)
        stats["groups"] += 1
        member_ids: set[int] = set()
        for mdn in a.get("member") or []:
            try:
                with db.begin_nested():
                    conn.search(mdn, "(objectClass=user)", search_scope="BASE",
                                attributes=["sAMAccountName", "mail", "displayName", "objectSid",
                                            "userAccountControl", "objectClass"])
                    ents = _attrs(conn)
                    if not ents:
                        continue
                    u = _person(db, actor, ents[0], stats)
            except Exception as e:  # noqa: BLE001 - one odd account must not stop the sync
                stats["skipped"] += 1
                stats["warnings"].append(f"{mdn}: skipped ({e})")
                continue
            if u is None:
                stats["skipped"] += 1
                continue
            seen_users[u.id] = u
            member_ids.add(u.id)
            if cn == s.ldap_admin_group:
                role_of[u.id] = "admin"
            elif cn == s.ldap_auditor_group and role_of.get(u.id) != "admin":
                role_of[u.id] = "auditor"
        group_members[g.id] = member_ids
    for gid, members in group_members.items():
        accounts.set_members(db, db.get(Group, gid), members)
    # AD groups that disappeared from AD (or lost the prefix) keep no members.
    for g in db.scalars(select(Group).where(Group.source == "ad")):
        if g.id not in seen_groups and accounts.set_members(db, g, set()) != (0, 0):
            stats["warnings"].append(f"AD group {g.ad_name} was not found; its members were removed")
    for u in seen_users.values():
        if u.source == "ad":
            u.system_role = role_of.get(u.id, "member")
    stats["users"] = len(seen_users)
    # AD people who are no longer in any synced group lose access.
    for u in db.scalars(select(User).where(User.source == "ad", User.status == "active")):
        if u.id not in seen_users:
            _deactivate(db, u, stats)
    stats["warnings"] = stats["warnings"][:100]
    set_setting(db, "ad_last_sync", {"at": utcnow().isoformat() + "Z", **stats})
    audit.log(db, actor, "directory.sync", s.ldap_base_dn or "", **{k: v for k, v in stats.items() if k != "warnings"},
              warnings=len(stats["warnings"]))
    db.commit()
    acl.reconcile_all(db)
    return stats


def status(db: Session) -> dict[str, Any]:
    s = config.settings
    return {"ad": {"configured": s.ldap_enabled, "url": s.ldap_url, "base_dn": s.ldap_base_dn, "domain": s.ad_domain,
                   "group_prefix": s.ldap_group_prefix, "admin_group": s.ldap_admin_group,
                   "auditor_group": s.ldap_auditor_group, "last_sync": get_setting(db, "ad_last_sync"),
                   "extra_groups": get_setting(db, "ad_extra_groups", [])},
            "entra": {"configured": s.oidc_enabled, "issuer": s.oidc_issuer, "client_id": s.oidc_client_id},
            "smb": {"enabled": s.smb_enabled, "host": s.smb_host, "idmap_base": s.idmap_base}}


def add_extra_group(db: Session, actor: Actor, ad_name: str) -> None:
    extra = list(get_setting(db, "ad_extra_groups", []) or [])
    if ad_name not in extra:
        extra.append(ad_name)
    set_setting(db, "ad_extra_groups", extra)
    audit.log(db, actor, "directory.map_group", ad_name)
    db.commit()


def is_stale(db: Session, minutes: int = 5) -> bool:
    last = get_setting(db, "ad_last_sync")
    if not last:
        return True
    at = dt.datetime.fromisoformat(last["at"].rstrip("Z"))
    return (utcnow() - at).total_seconds() > minutes * 60
