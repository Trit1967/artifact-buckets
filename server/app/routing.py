"""Where a Claude Code save lands when nobody names a folder.

Order: 1) the folder the person named, 2) the first routing rule that matches the
project's git remote or name and that the person can write to, 3) the person's
/users/<name>/projects/<project>, 4) /users/<name>/inbox.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, paths
from .db import RoutingRule
from .errors import BadRequest, Forbidden, NotFound
from .perms import Actor, at_least, role_for

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_project(name: str | None) -> str | None:
    """Project names become lower-case folder names, so Windows (case-insensitive) sees one folder."""
    if not name:
        return None
    s = _SAFE.sub("-", name.strip().lower()).strip(".-")[:80]
    return s or None


def normalize_remote(remote: str | None) -> str | None:
    """git@git.lab.local:platform/latency-svc.git -> git.lab.local/platform/latency-svc"""
    if not remote:
        return None
    r = remote.strip().rstrip("/")
    had_scheme = re.match(r"^[a-z][a-z0-9+.-]*://", r, re.I) is not None
    r = re.sub(r"^[a-z][a-z0-9+.-]*://", "", r, flags=re.I)
    r = re.sub(r"^[^@/]+@", "", r)
    host, _, rest = r.partition("/")
    if had_scheme:
        host = re.sub(r":\d+$", "", host)          # ssh://host:22/a/b, https://host:8443/a/b
    elif ":" in host:                              # scp form host:a/b
        host, _, first = host.partition(":")
        rest = first + ("/" + rest if rest else "")
    r = (host + "/" + rest).rstrip("/")
    r = re.sub(r"\.git$", "", r).rstrip("/")
    return r.lower()


def resolve(db: Session, actor: Actor, *, folder: str | None, project: str | None, git_remote: str | None) -> tuple[str, str]:
    """Return (folder, reason)."""
    user = actor.user.username
    proj = safe_project(project)
    if folder:
        f = paths.normalize(folder)
        return f, "named"
    remote = normalize_remote(git_remote)
    for rule in db.scalars(select(RoutingRule).order_by(RoutingRule.priority, RoutingRule.id)):
        subject = remote if rule.match == "git_remote" else (project or "")
        if not subject or not fnmatch.fnmatch(subject.lower(), rule.pattern.lower()):
            continue
        if "{project}" in rule.target and not proj:
            continue
        try:
            target = paths.normalize(rule.target.replace("{project}", proj or "").replace("{user}", user))
        except BadRequest:
            continue
        if at_least(role_for(db, actor, target), "editor"):
            return target, f"rule {rule.id}"
    if proj:
        return f"/users/{user}/projects/{proj}", "project"
    return f"/users/{user}/inbox", "inbox"


def rule_info(r: RoutingRule) -> dict[str, Any]:
    return {"id": r.id, "priority": r.priority, "match": r.match, "pattern": r.pattern, "target": r.target}


def _check_team_owner(db: Session, actor: Actor, target: str, probe: str) -> None:
    """Non-admins may only route into a team folder (/shared/<team>/...) they own. Never into a home,
    and never with {user}, so nobody can pull other people's saves into a folder they control."""
    parts = probe.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "shared" or "{user}" in target:
        raise Forbidden("Only admins can route into personal folders; Owners can route into their team folder")
    if not at_least(role_for(db, actor, f"/shared/{parts[1]}"), "owner"):
        raise Forbidden("Only admins, or Owners of the target team folder, can manage routing rules")


def can_manage(db: Session, actor: Actor, target: str) -> bool:
    if actor.is_admin:
        return True
    try:
        _check_team_owner(db, actor, target, paths.normalize(target.replace("{project}", "x").replace("{user}", "x")))
        return True
    except (Forbidden, BadRequest):
        return False


def owned_team_folders(db: Session, actor: Actor) -> list[str]:
    from .db import Group
    out = []
    for g in db.scalars(select(Group)):
        p = f"/shared/{g.name}"
        if at_least(role_for(db, actor, p), "owner"):
            out.append(p)
    return out


def add_rule(db: Session, actor: Actor, match: str, pattern: str, target: str, priority: int = 100) -> RoutingRule:
    if match not in ("git_remote", "project"):
        raise BadRequest("match must be git_remote or project")
    if not pattern.strip():
        raise BadRequest("pattern is required")
    probe = paths.normalize(target.replace("{project}", "x").replace("{user}", "x"))
    if not actor.is_admin:
        _check_team_owner(db, actor, target, probe)
    r = RoutingRule(match=match, pattern=pattern.strip(), target=target.strip(), priority=priority, created_by=actor.user.id)
    db.add(r)
    audit.log(db, actor, "routing.add", target, match=match, pattern=pattern)
    db.commit()
    return r


def delete_rule(db: Session, actor: Actor, rule_id: int) -> None:
    r = db.get(RoutingRule, rule_id)
    if r is None:
        raise NotFound("No such rule")
    probe = paths.normalize(r.target.replace("{project}", "x").replace("{user}", "x"))
    if not actor.is_admin:
        _check_team_owner(db, actor, r.target, probe)
    audit.log(db, actor, "routing.delete", r.target, pattern=r.pattern)
    db.delete(r)
    db.commit()
