"""Append-only audit log. Every sign-in, grant change, save, read, move and delete is written here."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .db import Audit
from .perms import Actor


def log(db: Session, actor: Actor | None, action: str, target: str = "", *, ok: bool = True,
        via: str | None = None, ip: str | None = None, **detail: Any) -> None:
    if actor is not None:
        if actor.token is not None:
            detail.setdefault("token", f"{actor.token.name} (…{actor.token.hint})")
        if actor.session_id:
            detail.setdefault("session", actor.session_id)
    db.add(Audit(user_id=actor.user.id if actor else None,
                 actor=(actor.user.username if actor else "system"),
                 action=action, target=target[:1024], via=via or (actor.via if actor else "system"),
                 ok=ok, detail=detail, ip=(actor.ip if actor else None) or ip))
