"""Passwords, web sessions, API tokens and small crypto helpers."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import Token, User, WebSession, utcnow

_ph = PasswordHasher()
SESSION_COOKIE = "ab_session"
# Checked against when the account doesn't exist, so a wrong name and a wrong password take equally long.
_DUMMY_HASH = _ph.hash("not-a-real-password")


def burn_password_check(pw: str) -> None:
    try:
        _ph.verify(_DUMMY_HASH, pw)
    except (VerificationError, InvalidHashError):
        pass


class LoginThrottle:
    """Slows password guessing: a few wrong tries per (address, name), more per address, then a short wait.

    Kept in memory (resets on restart) and keyed by address too, so someone can't lock a colleague out
    by guessing their name."""
    WINDOW = 15 * 60
    PER_NAME = 8
    PER_ADDRESS = 40

    def __init__(self) -> None:
        self._fails: dict[tuple, list[float]] = {}

    def _recent(self, key: tuple, now: float) -> list[float]:
        hits = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]
        if hits:
            self._fails[key] = hits
        else:
            self._fails.pop(key, None)
        return hits

    def wait(self, ip: str | None, name: str, now: float | None = None) -> int:
        """Seconds the caller must wait before trying again (0 when allowed)."""
        import time
        now = time.monotonic() if now is None else now
        waits = []
        for key, limit in ((("n", ip, name), self.PER_NAME), (("a", ip), self.PER_ADDRESS)):
            hits = self._recent(key, now)
            if len(hits) >= limit:
                waits.append(int(self.WINDOW - (now - hits[0])) + 1)
        return max(waits, default=0)

    def failed(self, ip: str | None, name: str, now: float | None = None) -> None:
        import time
        now = time.monotonic() if now is None else now
        for key in (("n", ip, name), ("a", ip)):
            self._fails.setdefault(key, []).append(now)
        if len(self._fails) > 20000:  # a flood of made-up names must not grow memory without end
            for k in list(self._fails):
                self._recent(k, now)

    def ok(self, ip: str | None, name: str) -> None:
        self._fails.pop(("n", ip, name), None)


login_throttle = LoginThrottle()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_secret(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ---- web sessions -------------------------------------------------------

def create_web_session(db: Session, user: User) -> tuple[str, str]:
    raw = new_secret()
    csrf = secrets.token_urlsafe(24)
    db.add(WebSession(id_hash=sha256_hex(raw), user_id=user.id, csrf=csrf,
                      expires_at=utcnow() + dt.timedelta(hours=config.settings.session_hours)))
    return raw, csrf


def load_web_session(db: Session, raw: str | None) -> WebSession | None:
    if not raw:
        return None
    s = db.get(WebSession, sha256_hex(raw))
    if s is None or s.expires_at < utcnow():
        return None
    return s


def end_web_session(db: Session, raw: str | None) -> None:
    if raw:
        s = db.get(WebSession, sha256_hex(raw))
        if s:
            db.delete(s)


# ---- API / OAuth tokens -------------------------------------------------

def issue_token(db: Session, user: User, *, name: str, kind: str, scope: str = "write",
                folders: list[str] | None = None, ttl: dt.timedelta | None = None,
                client_id: str | None = None, grant_id: str | None = None) -> tuple[str, Token]:
    prefix = {"personal": "ab_pat_", "oauth_access": "ab_at_", "oauth_refresh": "ab_rt_", "sync": "ab_sync_"}[kind]
    raw = new_secret(prefix)
    tok = Token(user_id=user.id, name=name[:128], kind=kind, token_hash=sha256_hex(raw), hint=raw[-4:],
                scope=scope, folders=folders or None, client_id=client_id, grant_id=grant_id,
                expires_at=(utcnow() + ttl) if ttl else None)
    db.add(tok)
    db.flush()
    return raw, tok


def resolve_token(db: Session, raw: str, kinds: tuple[str, ...] = ("personal", "oauth_access")) -> Token | None:
    tok = db.scalar(select(Token).where(Token.token_hash == sha256_hex(raw)))
    if tok is None or tok.revoked or tok.kind not in kinds:
        return None
    if tok.expires_at and tok.expires_at < utcnow():
        return None
    return tok


# ---- signed short-lived URLs (previews) -----------------------------------

def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.settings.secret_key, salt=salt)


def sign(data: dict, salt: str) -> str:
    return _serializer(salt).dumps(data)


def unsign(token: str, salt: str, max_age: int) -> dict | None:
    try:
        return _serializer(salt).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
