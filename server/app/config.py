"""Runtime configuration, read once from environment variables.

Every setting has a safe default for local development. Production values are
set in docker-compose.yml and the .env file (see README).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v else default


@dataclass
class Settings:
    # Public base URL of the app, used for OAuth metadata and links.
    base_url: str = field(default_factory=lambda: os.environ.get("AB_BASE_URL", "http://localhost:8080").rstrip("/"))
    database_url: str = field(default_factory=lambda: os.environ.get("AB_DATABASE_URL", "sqlite:///./data/ab.db"))
    # The single folder tree every way in (web, MCP, SMB) reads and writes.
    tree_root: str = field(default_factory=lambda: os.environ.get("AB_TREE_ROOT", "./data/tree"))
    # Where storage volumes are mounted. Each sub-directory is one volume.
    volumes_root: str = field(default_factory=lambda: os.environ.get("AB_VOLUMES_ROOT", "./data/volumes"))
    # The same folder as seen by the host, where mergerfs runs (the app sees it through a bind mount).
    volumes_host_root: str = field(default_factory=lambda: os.environ.get("AB_VOLUMES_HOST_ROOT", ""))
    # "mergerfs" in the lab (tree_root is a mergerfs pool of the volumes); "direct" for dev.
    storage_mode: str = field(default_factory=lambda: os.environ.get("AB_STORAGE_MODE", "direct"))
    zfs_socket: str = field(default_factory=lambda: os.environ.get("AB_ZFS_SOCKET", ""))
    secret_key: str = field(default_factory=lambda: os.environ.get("AB_SECRET_KEY"))  # type: ignore[arg-type]
    secure_cookies: bool = field(default_factory=lambda: _bool("AB_SECURE_COOKIES", False))
    session_hours: int = field(default_factory=lambda: _int("AB_SESSION_HOURS", 12))
    max_file_bytes: int = field(default_factory=lambda: _int("AB_MAX_FILE_BYTES", 500 * 1024 * 1024))
    default_user_quota: int = field(default_factory=lambda: _int("AB_USER_QUOTA_BYTES", 50 * 1024**3))
    default_group_quota: int = field(default_factory=lambda: _int("AB_GROUP_QUOTA_BYTES", 500 * 1024**3))
    trash_days: int = field(default_factory=lambda: _int("AB_TRASH_DAYS", 30))
    mcp_delete_confirm_threshold: int = field(default_factory=lambda: _int("AB_MCP_DELETE_CONFIRM_FILES", 50))
    oauth_access_minutes: int = field(default_factory=lambda: _int("AB_OAUTH_ACCESS_MINUTES", 60))
    oauth_refresh_days: int = field(default_factory=lambda: _int("AB_OAUTH_REFRESH_DAYS", 30))
    # First admin, created on first start if no users exist.
    bootstrap_admin: str = field(default_factory=lambda: os.environ.get("AB_BOOTSTRAP_ADMIN", "admin"))
    bootstrap_password: str | None = field(default_factory=lambda: os.environ.get("AB_BOOTSTRAP_PASSWORD"))
    bootstrap_email: str | None = field(default_factory=lambda: os.environ.get("AB_BOOTSTRAP_EMAIL") or None)
    # Active Directory (LDAP) group and user sync.
    ldap_url: str | None = field(default_factory=lambda: os.environ.get("AB_LDAP_URL"))
    ldap_bind_dn: str | None = field(default_factory=lambda: os.environ.get("AB_LDAP_BIND_DN"))
    ldap_bind_password: str | None = field(default_factory=lambda: os.environ.get("AB_LDAP_BIND_PASSWORD"))
    ldap_base_dn: str | None = field(default_factory=lambda: os.environ.get("AB_LDAP_BASE_DN"))
    ldap_group_prefix: str = field(default_factory=lambda: os.environ.get("AB_LDAP_GROUP_PREFIX", "AB-"))
    ldap_admin_group: str = field(default_factory=lambda: os.environ.get("AB_LDAP_ADMIN_GROUP", "AB-Admins"))
    ldap_auditor_group: str = field(default_factory=lambda: os.environ.get("AB_LDAP_AUDITOR_GROUP", "AB-Auditors"))
    ad_domain: str | None = field(default_factory=lambda: os.environ.get("AB_AD_DOMAIN"))
    # Samba idmap "rid" backend: uid/gid = idmap_base + RID. Must match smb.conf.
    idmap_base: int = field(default_factory=lambda: _int("AB_IDMAP_BASE", 100000))
    smb_enabled: bool = field(default_factory=lambda: _bool("AB_SMB_ENABLED", False))
    smb_host: str = field(default_factory=lambda: os.environ.get("AB_SMB_HOST", "buckets.lab.local"))
    # Entra ID (OIDC) sign-in.
    oidc_issuer: str | None = field(default_factory=lambda: os.environ.get("AB_OIDC_ISSUER"))
    oidc_client_id: str | None = field(default_factory=lambda: os.environ.get("AB_OIDC_CLIENT_ID"))
    oidc_client_secret: str | None = field(default_factory=lambda: os.environ.get("AB_OIDC_CLIENT_SECRET"))
    static_dir: str = field(default_factory=lambda: os.environ.get("AB_STATIC_DIR", "../web/dist"))
    background_jobs: bool = field(default_factory=lambda: _bool("AB_BACKGROUND_JOBS", True))
    # The plugin marketplace people add. Empty (the default): this server hands out its own, with its
    # address built in, at <base_url>/claude/marketplace.json (see claude_dist.py).
    plugin_marketplace: str = field(default_factory=lambda: os.environ.get("AB_PLUGIN_MARKETPLACE", ""))
    # The plugin's files, served as a zip with this server's address written into its .mcp.json.
    plugin_dir: str = field(default_factory=lambda: os.environ.get("AB_PLUGIN_DIR", "../plugin-marketplace/artifact-buckets"))
    # Where the one-line installers are fetched from, when that differs from base_url (the lab serves
    # them over plain http so a computer can fetch them before it trusts the lab's own certificate).
    install_url: str = field(default_factory=lambda: os.environ.get("AB_INSTALL_URL", ""))
    # A private root certificate the installers should trust (lab only; empty with a real certificate).
    install_ca_file: str = field(default_factory=lambda: os.environ.get("AB_INSTALL_CA_FILE", ""))
    # Sign-in redirects Claude Code may use. Claude Code always uses a loopback address; other https hosts
    # (for example a web-based client) must be listed here, comma-separated, or registration is refused.
    oauth_redirect_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(
        h.strip().lower() for h in os.environ.get("AB_OAUTH_REDIRECT_HOSTS", "").split(",") if h.strip()))

    def dev_key(self) -> None:
        """Development only: with no AB_SECRET_KEY and no secure cookies, make a key fresh for this run,
        so nothing is ever signed with a known value. Sign-ins and preview links end when the app restarts."""
        if self.secret_key is None and not self.secure_cookies:
            import secrets
            import sys
            self.secret_key = secrets.token_hex(32)
            print("AB_SECRET_KEY is not set: using a temporary key for this run (development only)", file=sys.stderr)

    def check(self) -> None:
        """Refuse to start with settings that would make the app unsafe."""
        self.dev_key()
        if not self.secret_key or len(self.secret_key) < 16:
            raise SystemExit("AB_SECRET_KEY must be set to a long random value (openssl rand -hex 32)")

    @property
    def ldap_enabled(self) -> bool:
        return bool(self.ldap_url and self.ldap_base_dn)

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)


settings = Settings()


def reload_settings() -> Settings:
    """Re-read the environment (used by tests)."""
    global settings
    settings = Settings()
    return settings
