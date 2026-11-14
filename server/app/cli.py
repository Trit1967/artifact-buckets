"""Server-side admin commands, run inside the app container:

  python -m app.cli reset-password [username]

For a forgotten admin password. Sets a new random password, signs that person out everywhere and prints it.
"""
from __future__ import annotations

import secrets
import sys

from sqlalchemy import delete, select

from . import audit, config
from .db import User, WebSession, init_engine, new_session
from .perms import Actor
from .security import hash_password


def reset_password(username: str) -> str:
    init_engine(config.settings.database_url)
    with new_session() as db:
        u = db.scalar(select(User).where(User.username == username.strip().lower()))
        if u is None or u.source != "local":
            raise SystemExit(f"No local account named '{username}'.")
        pw = secrets.token_urlsafe(15)
        u.password_hash = hash_password(pw)
        if u.status != "active":
            u.status = "active"
        db.execute(delete(WebSession).where(WebSession.user_id == u.id))
        audit.log(db, Actor(user=u, via="system"), "user.password", u.username, reason="reset from the server")
        db.commit()
        return pw


def main(argv: list[str]) -> None:
    if len(argv) >= 1 and argv[0] == "reset-password":
        name = argv[1] if len(argv) > 1 else config.settings.bootstrap_admin
        print(reset_password(name))
        return
    raise SystemExit("usage: python -m app.cli reset-password [username]")


if __name__ == "__main__":
    main(sys.argv[1:])
