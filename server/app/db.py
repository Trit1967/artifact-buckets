"""Database models. PostgreSQL in production, SQLite for development and tests."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from . import config


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(256), index=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    password_hash: Mapped[str | None] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(16), default="local")  # local | ad
    ad_dn: Mapped[str | None] = mapped_column(String(512))
    ad_sid: Mapped[str | None] = mapped_column(String(128), index=True)
    uid_number: Mapped[int | None] = mapped_column(Integer)
    system_role: Mapped[str] = mapped_column(String(16), default="member")  # member | auditor | admin
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | invited | suspended
    quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    last_active: Mapped[dt.datetime | None] = mapped_column(DateTime)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(16), default="local")  # local | ad
    ad_name: Mapped[str | None] = mapped_column(String(256), unique=True)
    ad_dn: Mapped[str | None] = mapped_column(String(512))
    ad_sid: Mapped[str | None] = mapped_column(String(128))
    gid_number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Folder(Base):
    """Folders that carry settings (quota, retention). Plain folders exist only on disk."""
    __tablename__ = "folders"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    keep_versions: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Grant(Base):
    __tablename__ = "grants"
    __table_args__ = (UniqueConstraint("folder_path", "principal_type", "principal_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    folder_path: Mapped[str] = mapped_column(String(1024), index=True)
    principal_type: Mapped[str] = mapped_column(String(8))  # user | group
    principal_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(8))  # viewer | editor | owner
    granted_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class File(Base):
    __tablename__ = "files"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    mime: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    version: Mapped[int] = mapped_column(Integer, default=1)
    saved_by: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="web")  # web | mcp | smb | api
    project: Mapped[str | None] = mapped_column(String(256))
    git_remote: Mapped[str | None] = mapped_column(String(512))
    git_commit: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(128))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class FileVersion(Base):
    __tablename__ = "file_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    stored_name: Mapped[str] = mapped_column(String(256))
    saved_by: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="web")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class TrashItem(Base):
    __tablename__ = "trash"
    id: Mapped[int] = mapped_column(primary_key=True)
    original_path: Mapped[str] = mapped_column(String(1024))
    trash_key: Mapped[str] = mapped_column(String(64), unique=True)
    is_dir: Mapped[bool] = mapped_column(Boolean, default=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    deleted_by: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    # Grants that lived inside the deleted folder, restored with it.
    grants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class Token(Base):
    """Personal access tokens and OAuth tokens issued to Claude Code."""
    __tablename__ = "tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))  # personal | oauth_access | oauth_refresh
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hint: Mapped[str] = mapped_column(String(16), default="")
    scope: Mapped[str] = mapped_column(String(16), default="write")  # read | write
    folders: Mapped[list[str] | None] = mapped_column(JSON)
    client_id: Mapped[str | None] = mapped_column(String(64))
    grant_id: Mapped[str | None] = mapped_column(String(64), index=True)  # links an OAuth sign-in's tokens
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    last_used: Mapped[dt.datetime | None] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class OAuthClient(Base):
    __tablename__ = "oauth_clients"
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="MCP client")
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class OAuthCode(Base):
    __tablename__ = "oauth_codes"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(Integer)
    redirect_uri: Mapped[str] = mapped_column(String(1024))
    code_challenge: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class WebSession(Base):
    __tablename__ = "web_sessions"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class RoutingRule(Base):
    __tablename__ = "routing_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    match: Mapped[str] = mapped_column(String(16))  # git_remote | project
    pattern: Mapped[str] = mapped_column(String(512))
    target: Mapped[str] = mapped_column(String(1024))  # may contain {project} and {user}
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Volume(Base):
    __tablename__ = "volumes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active | draining | drained | missing
    label: Mapped[str] = mapped_column(String(256), default="")
    drain_moved: Mapped[int] = mapped_column(Integer, default=0)
    drain_total: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(1024), default="")
    via: Mapped[str] = mapped_column(String(16), default="web")  # web | mcp | smb | api | system
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


_engine = None
_Session: sessionmaker[Session] | None = None


def init_engine(url: str | None = None):
    global _engine, _Session
    url = url or config.settings.database_url
    kwargs: dict[str, Any] = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _fk(dbapi_conn, _):  # noqa: ANN001
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            # Paths are case-sensitive (Linux allows /p/a next to /p/A); LIKE must be too, as on PostgreSQL.
            dbapi_conn.execute("PRAGMA case_sensitive_like=ON")
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def new_session() -> Session:
    if _Session is None:
        init_engine()
    assert _Session is not None
    return _Session()


def get_setting(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    return default if row is None else row.value


def set_setting(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
