"""Remote MCP server for Claude Code (Streamable HTTP transport, JSON responses, stateless).

Claude Code connects to {base}/mcp. Without a valid token the server answers 401 with
a pointer to the OAuth metadata, which makes Claude Code open the browser sign-in.
The tool list depends on who signed in: everyone gets file tools; admins also get
admin tools (not over personal tokens); auditors get audit_search.
Every tool calls the same functions as the web app, so permissions are identical.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import accounts, audit, config, directory, fs, paths, routing, storage
from .db import Audit, Folder, Group, Token, User, Volume, new_session, utcnow
from .errors import AppError, BadRequest, Conflict, Forbidden, NotFound
from .perms import Actor, at_least, role_for
from .security import resolve_token

router = APIRouter()
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "artifact-buckets", "title": "Artifact Buckets", "version": "0.1.0"}
INSTRUCTIONS = ("Artifact Buckets stores the artifacts people make with Claude Code. Use save_artifact to keep a "
                "finished file; pass project and git_remote so it is routed to the right folder. Use list_folder, "
                "search_artifacts and get_artifact to find earlier work. Deletes go to a 30-day trash.")

S = "string"
STR = {"type": S}


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


PRINCIPAL = {"group": {"type": S, "description": "Group name, e.g. platform-eng"},
             "user": {"type": S, "description": "Username, e.g. maya"}}
ROLE = {"type": S, "enum": ["viewer", "editor", "owner"]}

# name -> (audience, description, input schema, read_only)
TOOLS: dict[str, tuple[str, str, dict[str, Any], bool]] = {
    "save_artifact": ("user", "Save a file. Give either `path` (full path), or `name` plus optionally `folder`. "
                      "Without a folder the file is routed by project: team rules first, then /users/<you>/projects/<project>, "
                      "else your inbox. Saving the same path again creates a new version.",
                      _obj({"name": {**STR, "description": "File name, e.g. latency-dashboard.html"},
                            "folder": {**STR, "description": "Folder to save into, e.g. /shared/platform-eng/dashboards"},
                            "path": {**STR, "description": "Full path including the file name"},
                            "content": {**STR, "description": "Text content"},
                            "content_base64": {**STR, "description": "Binary content, base64 encoded"},
                            "project": {**STR, "description": "Project name (usually the repo folder name)"},
                            "git_remote": {**STR, "description": "git remote URL of the project, if any"},
                            "git_commit": STR, "session_id": STR,
                            "tags": {"type": "array", "items": STR},
                            "if_version": {"type": "integer", "minimum": 1,
                                           "description": "Only save if the file is still at this version (the one you opened); "
                                                          "otherwise someone changed it meanwhile and the save is refused"}}), False),
    "get_artifact": ("user", "Read a file's text and details. Binary files can be fetched with as_base64.",
                     _obj({"path": STR, "as_base64": {"type": "boolean", "description": "Return the bytes as content_base64 (files up to 10 MB)"}},
                          ["path"]), True),
    "search_artifacts": ("user", "Find files you can open by name, project, tag or folder.",
                         _obj({"query": STR, "project": STR, "tag": STR, "folder": STR,
                               "limit": {"type": "integer", "minimum": 1, "maximum": 200}}), True),
    "list_folder": ("user", "List a folder. Without a path, lists your home folder.", _obj({"path": STR}), True),
    "list_writable_folders": ("user", "Folders you can save into (your home and team folders).", _obj({}), True),
    "create_folder": ("user", "Create a folder (and any missing parents you can create).", _obj({"path": STR}, ["path"]), False),
    "rename": ("user", "Rename a file or folder in place.", _obj({"path": STR, "new_name": STR}, ["path", "new_name"]), False),
    "move": ("user", "Move a file or folder.", _obj({"from": STR, "to": STR}, ["from", "to"]), False),
    "delete": ("user", "Move a file or folder to the trash (restorable for 30 days). Large folders must be confirmed in the web app.",
               _obj({"path": STR}, ["path"]), False),
    "edit_file": ("user", "Replace one exact piece of text in a text file. Creates a new version.",
                  _obj({"path": STR, "old_text": STR, "new_text": STR}, ["path", "old_text", "new_text"]), False),
    "list_versions": ("user", "List a file's versions.", _obj({"path": STR}, ["path"]), True),
    "restore_version": ("user", "Make an earlier version current again (kept as a new version).",
                        _obj({"path": STR, "version": {"type": "integer"}}, ["path", "version"]), False),
    "list_trash": ("user", "Items you deleted that can still be restored.", _obj({}), True),
    "restore_from_trash": ("user", "Restore an item from the trash by its id.", _obj({"id": STR}, ["id"]), False),
    "share_folder": ("user", "Give a group or person a role on a folder you own.",
                     _obj({"path": STR, **PRINCIPAL, "role": ROLE}, ["path", "role"]), False),
    "unshare_folder": ("user", "Remove a group's or person's grant from a folder you own.",
                       _obj({"path": STR, **PRINCIPAL}, ["path"]), False),
    "who_has_access": ("user", "Show who can reach a folder and which grant gives them access.", _obj({"path": STR}, ["path"]), True),
    # admin
    "list_users": ("admin", "List people, optionally filtered by text.", _obj({"query": STR}), True),
    "suspend_user": ("admin", "Suspend or reactivate a person. Suspending signs them out everywhere.",
                     _obj({"username": STR, "suspend": {"type": "boolean"}}, ["username", "suspend"]), False),
    "set_quota": ("admin", "Set a quota in GB for a person (username) or a team folder (folder).",
                  _obj({"username": STR, "folder": STR, "gigabytes": {"type": "number", "exclusiveMinimum": 0}}, ["gigabytes"]), False),
    "list_devices": ("admin", "List signed-in Claude Code devices and tokens.", _obj({"username": STR}), True),
    "revoke_device": ("admin", "Revoke a device or token by id.", _obj({"token_id": {"type": "integer"}}, ["token_id"]), False),
    "map_ad_group": ("admin", "Start syncing an AD group whose name does not start with the prefix.", _obj({"ad_group": STR}, ["ad_group"]), False),
    "grant_folder": ("admin", "Give a group or person a role on any folder.",
                     _obj({"path": STR, **PRINCIPAL, "role": ROLE}, ["path", "role"]), False),
    "list_routing_rules": ("admin", "List routing rules.", _obj({}), True),
    "add_routing_rule": ("admin", "Route saves whose git remote or project matches a pattern to a folder. Target may use {project} and {user}.",
                         _obj({"match": {"type": S, "enum": ["git_remote", "project"]}, "pattern": STR, "target": STR,
                               "priority": {"type": "integer"}}, ["match", "pattern", "target"]), False),
    "sync_directory": ("admin", "Sync people and groups from Active Directory now.", _obj({}), False),
    "storage_status": ("admin", "Pool capacity and volumes.", _obj({}), True),
    "add_volume_to_pool": ("admin", "Add a pending volume to the storage pool.", _obj({"volume": STR}, ["volume"]), False),
    "drain_volume": ("admin", "Stop new files landing on a volume and move its files to the others.", _obj({"volume": STR}, ["volume"]), False),
    "audit_search": ("auditor", "Search the audit log.", _obj({"action": STR, "user": STR, "path": STR,
                                                               "limit": {"type": "integer", "minimum": 1, "maximum": 500}}), True),
}


def visible_tools(actor: Actor) -> list[str]:
    out = []
    personal = actor.token is not None and actor.token.kind == "personal"
    for name, (aud, *_rest) in TOOLS.items():
        if aud == "user":
            if actor.token is not None and actor.token.scope == "read" and not TOOLS[name][3]:
                continue
            out.append(name)
        elif aud == "admin" and actor.is_admin and not personal:
            out.append(name)
        elif aud == "auditor" and actor.is_auditor and not personal:
            out.append(name)
    return out


def _tool_def(name: str) -> dict[str, Any]:
    aud, desc, schema, ro = TOOLS[name]
    return {"name": name, "description": desc, "inputSchema": schema,
            "annotations": {"readOnlyHint": ro, "destructiveHint": name in ("delete", "suspend_user", "drain_volume", "revoke_device")}}


def link(path: str) -> str:
    """A link that opens the file in the web app's viewer (or the folder, for a folder)."""
    return f"{config.settings.base_url}/view?path={quote(path)}"


def _principal(args: dict[str, Any]) -> tuple[str, str]:
    if args.get("group"):
        return "group", args["group"]
    if args.get("user"):
        return "user", args["user"]
    raise BadRequest("Give either group or user")


def _volume(db: Session, name: str) -> Volume:
    storage.scan(db)
    v = db.scalar(select(Volume).where(Volume.name == name))
    if v is None:
        raise NotFound(f"No volume named {name}")
    return v


def call(db: Session, actor: Actor, name: str, a: dict[str, Any]) -> Any:
    if name not in visible_tools(actor):
        audit.log(db, actor, "access.denied", name, ok=False, reason="tool not available")
        db.commit()
        raise Forbidden(f"Tool {name} is not available to you")
    me = actor.user.username
    if name == "save_artifact":
        if a.get("content") is not None and a.get("content_base64") is not None:
            raise BadRequest("Give content or content_base64, not both")
        if a.get("content_base64") is not None:
            try:
                data = base64.b64decode(a["content_base64"], validate=True)
            except (binascii.Error, ValueError):
                raise BadRequest("content_base64 is not valid base64")
        elif a.get("content") is not None:
            data = a["content"].encode("utf-8")
        else:
            raise BadRequest("content or content_base64 is required")
        if a.get("path"):
            path = paths.normalize(a["path"])
            reason = "path"
        else:
            if not a.get("name") or "/" in a["name"]:
                raise BadRequest("Give a file name without slashes, or a full path")
            folder, reason = routing.resolve(db, actor, folder=a.get("folder"), project=a.get("project"), git_remote=a.get("git_remote"))
            path = paths.join(folder, a["name"])
        if a.get("if_version") is not None:
            cur = fs.file_row(db, path)
            if cur is None:
                raise Conflict(f"{path} no longer exists: it was moved, renamed or deleted after you opened it. "
                               "Search for it to find where it went, or save under a new name.")
            if cur.version != a["if_version"]:
                saver = db.get(User, cur.saved_by) if cur.saved_by else None
                raise Conflict(f"{path} changed since you opened it: it is now version {cur.version}"
                               f"{' (saved by ' + (saver.display_name or saver.username) + ')' if saver else ''}. "
                               "Open it again and reapply your change, or save under a new name.")
        meta = fs.SaveMeta(source="mcp", project=a.get("project"), git_remote=routing.normalize_remote(a.get("git_remote")),
                           git_commit=a.get("git_commit"), session_id=a.get("session_id"), tags=a.get("tags"))
        res = fs.save_file(db, actor, path, data, meta)
        return {**res, "routed_by": reason, "link": link(path)}
    if name == "get_artifact":
        if a.get("as_base64"):
            return fs.read_base64(db, actor, a["path"])
        return {**fs.read_text(db, actor, a["path"]), "link": link(paths.normalize(a["path"]))}
    if name == "search_artifacts":
        return {"results": fs.search(db, actor, a.get("query") or "", project=a.get("project"), tag=a.get("tag"),
                                     folder=a.get("folder"), limit=int(a.get("limit") or 50))}
    if name == "list_folder":
        return fs.list_folder(db, actor, a.get("path") or f"/users/{me}")
    if name == "list_writable_folders":
        cands = [f"/users/{me}", f"/users/{me}/inbox", f"/users/{me}/projects"]
        for g in db.scalars(select(Group).where(Group.source.in_(("ad", "local")))):
            cands.append(f"/shared/{g.name}")
        from .db import Grant
        for gr in db.scalars(select(Grant.folder_path).distinct()):
            cands.append(gr)
        seen, out = set(), []
        for c in cands:
            if c in seen:
                continue
            seen.add(c)
            try:
                r = role_for(db, actor, paths.normalize(c))
            except AppError:
                continue
            if at_least(r, "editor"):
                out.append({"path": c, "role": r})
        return {"folders": out}
    if name == "create_folder":
        return fs.make_folder(db, actor, a["path"])
    if name == "rename":
        p = paths.normalize(a["path"])
        if "/" in a["new_name"]:
            raise BadRequest("new_name is a name, not a path; use move to change folders")
        return fs.move(db, actor, p, paths.join(paths.parent(p), a["new_name"]))
    if name == "move":
        return fs.move(db, actor, a["from"], a["to"])
    if name == "delete":
        return fs.delete(db, actor, a["path"], confirm=False)
    if name == "edit_file":
        return fs.edit_file(db, actor, a["path"], a["old_text"], a["new_text"])
    if name == "list_versions":
        return {"versions": fs.versions(db, actor, a["path"])}
    if name == "restore_version":
        return fs.restore_version(db, actor, a["path"], int(a["version"]))
    if name == "list_trash":
        return {"items": fs.list_trash(db, actor)}
    if name == "restore_from_trash":
        return fs.restore_trash(db, actor, a["id"])
    if name in ("share_folder", "grant_folder"):
        kind, who = _principal(a)
        return fs.share(db, actor, a["path"], kind, who, a["role"])
    if name == "unshare_folder":
        kind, who = _principal(a)
        return fs.unshare(db, actor, a["path"], kind, who)
    if name == "who_has_access":
        return {"access": fs.who_has_access(db, actor, a["path"])}
    # ---- admin ----
    if name == "list_users":
        q = (a.get("query") or "").lower()
        users = [accounts.user_info(db, u) for u in db.scalars(select(User).order_by(User.username))]
        return {"users": [u for u in users if not q or q in (u["username"] + " " + (u["email"] or "") + " " + u["display_name"]).lower()][:200]}
    if name == "suspend_user":
        u = db.scalar(select(User).where(User.username == a["username"]))
        if not u:
            raise NotFound("No such person")
        accounts.update_user(db, actor, u.id, status="suspended" if a["suspend"] else "active")
        return accounts.user_info(db, u)
    if name == "set_quota":
        q = int(float(a["gigabytes"]) * 1024**3)
        if a.get("username"):
            u = db.scalar(select(User).where(User.username == a["username"]))
            if not u:
                raise NotFound("No such person")
            accounts.update_user(db, actor, u.id, quota_bytes=q)
            return {"username": u.username, "quota_bytes": q}
        if a.get("folder"):
            p = paths.normalize(a["folder"])
            if paths.quota_root(p) != p:
                raise BadRequest("Quotas apply to /users/<name> or /shared/<group>")
            fo = db.scalar(select(Folder).where(Folder.path == p)) or Folder(path=p)
            fo.quota_bytes = q
            db.add(fo)
            audit.log(db, actor, "folder.quota", p, quota_bytes=q)
            db.commit()
            return {"folder": p, "quota_bytes": q}
        raise BadRequest("Give username or folder")
    if name == "list_devices":
        q = select(Token).where(Token.kind.in_(("personal", "oauth_refresh")), Token.revoked.is_(False))
        if a.get("username"):
            u = db.scalar(select(User).where(User.username == a["username"]))
            q = q.where(Token.user_id == (u.id if u else -1))
        return {"devices": [token_info(db, t) for t in db.scalars(q.order_by(Token.created_at.desc()))]}
    if name == "revoke_device":
        t = db.get(Token, int(a["token_id"]))
        if not t:
            raise NotFound("No such token")
        revoke_family(db, t)
        audit.log(db, actor, "token.revoke", t.name, token_id=t.id)
        db.commit()
        return {"revoked": t.id}
    if name == "map_ad_group":
        directory.add_extra_group(db, actor, a["ad_group"])
        return {"mapped": a["ad_group"], "note": "Run sync_directory to bring it in"}
    if name == "list_routing_rules":
        from .db import RoutingRule
        return {"rules": [routing.rule_info(r) for r in db.scalars(select(RoutingRule).order_by(RoutingRule.priority))]}
    if name == "add_routing_rule":
        return routing.rule_info(routing.add_rule(db, actor, a["match"], a["pattern"], a["target"], int(a.get("priority") or 100)))
    if name == "sync_directory":
        return directory.sync(db, actor)
    if name == "storage_status":
        return storage.pool_info(db)
    if name == "add_volume_to_pool":
        return storage.add_to_pool(db, actor, _volume(db, a["volume"]).id)
    if name == "drain_volume":
        return storage.start_drain(db, actor, _volume(db, a["volume"]).id)
    if name == "audit_search":
        return {"events": audit_query(db, action=a.get("action"), user=a.get("user"), path=a.get("path"), limit=int(a.get("limit") or 100))}
    raise BadRequest(f"Unknown tool {name}")


def audit_query(db: Session, *, action: str | None = None, user: str | None = None, path: str | None = None,
                via: str | None = None, ok: bool | None = None, limit: int = 100,
                exclude: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    q = select(Audit).order_by(Audit.ts.desc(), Audit.id.desc())
    for pat in exclude:
        q = q.where(~Audit.action.like(pat.replace("*", "%")))
    if action:
        q = q.where(Audit.action.like(action.replace("*", "%")))
    if user:
        q = q.where(Audit.actor == user)
    if path:
        q = q.where(Audit.target.like(path.rstrip("/") + "%"))
    if via:
        q = q.where(Audit.via == via)
    if ok is not None:
        q = q.where(Audit.ok.is_(ok))
    rows = list(db.scalars(q.limit(max(1, min(limit, 50000)))))
    names = {u.id: (u.display_name or u.username) for u in db.scalars(select(User).where(User.id.in_({e.user_id for e in rows if e.user_id})))}
    return [{"id": e.id, "ts": e.ts.isoformat() + "Z", "actor": e.actor, "actor_name": names.get(e.user_id, e.actor),
             "action": e.action, "target": e.target, "via": e.via, "ok": e.ok, "detail": e.detail, "ip": e.ip} for e in rows]


def token_info(db: Session, t: Token) -> dict[str, Any]:
    u = db.get(User, t.user_id)
    kind = "device" if t.kind.startswith("oauth") else ("sync" if t.kind == "sync" else "personal")
    return {"id": t.id, "name": t.name, "kind": kind,
            "hint": t.hint, "user": u.username if u else None, "scope": t.scope, "folders": t.folders,
            "created_at": t.created_at.isoformat() + "Z",
            "last_used": t.last_used.isoformat() + "Z" if t.last_used else None,
            "expires_at": t.expires_at.isoformat() + "Z" if t.expires_at else None, "revoked": t.revoked}


def revoke_family(db: Session, t: Token) -> None:
    t.revoked = True
    if t.grant_id:
        for other in db.scalars(select(Token).where(Token.grant_id == t.grant_id)):
            other.revoked = True


# ---- transport -------------------------------------------------------------

def _rpc_error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _unauthorized(msg: str = "Sign in required") -> Response:
    meta = f"{config.settings.base_url}/.well-known/oauth-protected-resource"
    return JSONResponse({"error": "invalid_token", "error_description": msg}, status_code=401,
                        headers={"WWW-Authenticate": f'Bearer resource_metadata="{meta}", error="invalid_token"'})


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = urlparse(origin).hostname or ""
    return origin.rstrip("/") == config.settings.base_url or host in ("localhost", "127.0.0.1", "::1")


def authenticate(db: Session, request: Request) -> Actor | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    tok = resolve_token(db, auth[7:].strip())
    if tok is None:
        return None
    user = db.get(User, tok.user_id)
    if user is None or user.status != "active":
        return None
    tok.last_used = utcnow()
    accounts.touch(db, user)
    db.commit()
    ip = request.client.host if request.client else None
    return Actor(user=user, via="mcp", token=tok, ip=ip)


def handle_message(db: Session, actor: Actor, msg: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    method, id_, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if id_ is None:  # notification
        return None
    if not isinstance(params, dict):
        return _rpc_error(id_, -32602, "Invalid params")
    if method == "initialize":
        want = params.get("protocolVersion")
        ver = want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return {"jsonrpc": "2.0", "id": id_, "result": {"protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": [_tool_def(n) for n in visible_tools(actor)]}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            return _rpc_error(id_, -32602, "Invalid params")
        if name not in TOOLS:
            return _rpc_error(id_, -32602, f"Unknown tool: {name}")
        meta = params.get("_meta") or {}
        if isinstance(meta, dict) and meta.get("claudecode/sessionId"):
            actor.session_id = str(meta["claudecode/sessionId"])[:128]
        try:
            _validate(TOOLS[name][2], args)
            result = call(db, actor, name, args)
            text = json.dumps(result, default=str, indent=1)
            return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": text}],
                                                              "structuredContent": result if isinstance(result, dict) else {"result": result},
                                                              "isError": False}}
        except AppError as e:
            db.rollback()
            return _tool_error(id_, f"{e.code}: {e.message}")
        except NotADirectoryError:
            db.rollback()
            return _tool_error(id_, "conflict: Part of that path is a file, not a folder")
        except (OverflowError, ValueError) as e:
            db.rollback()
            return _tool_error(id_, f"bad_request: {e}")
    return _rpc_error(id_, -32601, f"Method not found: {method}")


def _tool_error(id_: Any, text: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": text}], "isError": True}}


MAX_INT = 2 ** 53


def _validate(schema: dict[str, Any], args: dict[str, Any]) -> None:
    props = schema.get("properties", {})
    for k in schema.get("required", []):
        if k not in args or args[k] is None:
            raise BadRequest(f"Missing argument: {k}")
    for k, v in args.items():
        if k not in props:
            raise BadRequest(f"Unknown argument: {k}")
        t = props[k].get("type")
        ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
              "number": isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": isinstance(v, bool),
              "array": isinstance(v, list), None: True}.get(t, True)
        if not ok:
            raise BadRequest(f"Argument {k} must be a {t}")
        if t in ("integer", "number"):
            if v != v or abs(v) > MAX_INT:  # NaN, inf, or beyond what any id or size here can be
                raise BadRequest(f"Argument {k} is out of range")
            if "minimum" in props[k] and v < props[k]["minimum"]:
                raise BadRequest(f"Argument {k} must be at least {props[k]['minimum']}")
            if "exclusiveMinimum" in props[k] and v <= props[k]["exclusiveMinimum"]:
                raise BadRequest(f"Argument {k} must be more than {props[k]['exclusiveMinimum']}")
            if "maximum" in props[k] and v > props[k]["maximum"]:
                raise BadRequest(f"Argument {k} must be at most {props[k]['maximum']}")
        if "enum" in props[k] and v not in props[k]["enum"]:
            raise BadRequest(f"Argument {k} must be one of {', '.join(props[k]['enum'])}")


@router.post("/mcp")
async def mcp_post(request: Request) -> Response:
    if not _origin_ok(request):
        return JSONResponse({"error": "origin not allowed"}, status_code=403)
    body = await request.body()
    if len(body) > config.settings.max_file_bytes * 4 // 3 + 65536:
        return JSONResponse(_rpc_error(None, -32600, "Request too large"), status_code=413)
    with new_session() as db:
        actor = authenticate(db, request)
        if actor is None:
            return _unauthorized()
        try:
            payload = json.loads(body)
        except ValueError:  # bad JSON, or bytes that are not UTF-8/16/32
            return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
        if isinstance(payload, list):
            out = [r for r in (handle_message(db, actor, m) for m in payload) if r is not None]
            return JSONResponse(out) if out else Response(status_code=202)
        res = handle_message(db, actor, payload)
        if res is None:
            return Response(status_code=202)
        return JSONResponse(res)


@router.get("/mcp")
@router.delete("/mcp")
async def mcp_other() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


def stats_today(db: Session, user_id: int | None = None) -> dict[str, int]:
    since = utcnow() - dt.timedelta(hours=24)
    base = select(func.count()).select_from(Audit).where(Audit.ts >= since, Audit.action == "file.save", Audit.via == "mcp")
    if user_id is not None:
        base = base.where(Audit.user_id == user_id)
    return {"saved": int(db.scalar(base.where(Audit.ok.is_(True))) or 0),
            "rejected": int(db.scalar(base.where(Audit.ok.is_(False))) or 0)}

