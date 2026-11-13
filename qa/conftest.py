"""QA fixtures. Reuses the developer's fixtures (env, app, admin, make_user, make_group, login,
oauth_sign_in, MCP) by loading server/tests/conftest.py under another module name, and adds a fake AD."""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "server")
sys.path.insert(0, SERVER)

_spec = importlib.util.spec_from_file_location("ab_dev_conftest", os.path.join(SERVER, "tests", "conftest.py"))
dev = importlib.util.module_from_spec(_spec)
sys.modules["ab_dev_conftest"] = dev
_spec.loader.exec_module(dev)

def _qlogin(app, username, password):
    """Like the developer's login() but server errors come back as 500 responses instead of raising."""
    from fastapi.testclient import TestClient
    c = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return dev.Web(c, r.json()["csrf"])


dev.login = _qlogin

# Re-export fixtures and helpers.
env, app, admin, make_user, make_group = dev.env, dev.app, dev.admin, dev.make_user, dev.make_group
oauth_sign_in, MCP, Web, pkce = dev.oauth_sign_in, dev.MCP, dev.Web, dev.pkce
ADMIN_PW, PW = dev.ADMIN_PW, dev.PW
login = _qlogin

from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server  # noqa: E402

DOM = "S-1-5-21-1111-2222-3333"
BASE = "DC=lab,DC=local"


def fake_ad(users, groups, extra_entries=()):
    """users: {sam: (rid, disabled)}, groups: {cn: (rid, [member sam or 'group:<cn>'...])}"""
    conn = Connection(Server("fake-dc", get_info=OFFLINE_AD_2012_R2), user="CN=svc,DC=lab,DC=local", password="x",
                      client_strategy=MOCK_SYNC)
    conn.strategy.add_entry("CN=svc,DC=lab,DC=local", {"userPassword": "x", "sAMAccountName": "svc"})
    for sam, (rid, disabled) in users.items():
        conn.strategy.add_entry(f"CN={sam},OU=People,{BASE}", {
            "objectClass": ["top", "person", "user"], "sAMAccountName": sam, "mail": f"{sam.lower()}@lab.local",
            "displayName": sam.title(), "objectSid": f"{DOM}-{rid}", "userAccountControl": 514 if disabled else 512})
    for cn, (rid, members) in groups.items():
        mdns = []
        for m in members:
            if m.startswith("group:"):
                mdns.append(f"CN={m[6:]},OU=Groups,{BASE}")
            else:
                mdns.append(f"CN={m},OU=People,{BASE}")
        conn.strategy.add_entry(f"CN={cn},OU=Groups,{BASE}", {
            "objectClass": ["top", "group"], "cn": cn, "description": f"{cn} group", "objectSid": f"{DOM}-{rid}",
            "member": mdns})
    for dn, attrs in extra_entries:
        conn.strategy.add_entry(dn, attrs)
    conn.bind()
    return conn


def run_sync(conn):
    from app import db as dbm, directory
    with dbm.new_session() as s:
        return directory.sync(s, None, conn=conn)


@pytest.fixture()
def ad_env(env, monkeypatch):
    monkeypatch.setenv("AB_LDAP_URL", "ldaps://dc.lab.local")
    monkeypatch.setenv("AB_LDAP_BASE_DN", BASE)
    from app import config
    config.reload_settings()


@pytest.fixture()
def raw(app):
    """An unauthenticated client that does not follow redirects and does not raise server errors."""
    from fastapi.testclient import TestClient
    return TestClient(app, follow_redirects=False, raise_server_exceptions=False)


def bearer(app, token):
    from fastapi.testclient import TestClient
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def save(web, path, content="x"):
    r = web.post("/api/fs/save-text", json={"path": path, "content": content})
    assert r.status_code == 200, r.text
    return r.json()


def uid_of(admin, username):
    return next(u["id"] for u in admin.get("/api/users").json()["users"] if u["username"] == username)


def user_row(admin, username):
    return next(u for u in admin.get("/api/users").json()["users"] if u["username"] == username)


def no_raise(web):
    """Make a Web client return 500 responses instead of raising."""
    web.c.raise_server_exceptions = False
    return web


def audit_events(admin, **params):
    r = admin.get("/api/audit", params={"limit": 1000, **params})
    assert r.status_code == 200, r.text
    return r.json()["events"]
