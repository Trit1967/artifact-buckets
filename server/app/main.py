"""The Artifact Buckets web app: REST API, MCP endpoint, OAuth sign-in and the web UI.

Run with:  uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from . import accounts, api, audit, claude_dist, config, directory, fs, mcp, oauth, paths, storage
from .db import Volume, init_engine, new_session
from .errors import AppError

log = logging.getLogger("artifact_buckets")

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
       "font-src 'self' data:; script-src 'self'; connect-src 'self'; frame-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; "
       # The consent form posts to /oauth/approve, which redirects to Claude Code's loopback listener;
       # browsers apply form-action to that redirect too.
       "form-action 'self' http://127.0.0.1:* http://localhost:* https:")


def startup() -> None:
    config.settings.check()
    init_engine(config.settings.database_url)
    os.makedirs(paths.tree_root(), exist_ok=True)
    with new_session() as db:
        accounts.bootstrap(db, config.settings.bootstrap_admin, config.settings.bootstrap_password)
        storage.scan(db)
        # After a reboot the fstab line may list disks that were drained or never added; put back the app's list.
        try:
            storage.apply_pool(db)
        except OSError as e:
            print(f"Could not apply the storage pool's branch list: {e}", file=sys.stderr)


def index_tree(db, limit: int = 2000) -> int:
    """Pick up files that arrived without the app (dropped on the SMB share)."""
    root = paths.tree_root()
    n = 0
    for top in ("users", "shared"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, top)):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            rel = "/" + os.path.relpath(dirpath, root).replace(os.sep, "/")
            for f in filenames:
                if f.startswith("."):
                    continue
                p = rel.rstrip("/") + "/" + f
                try:
                    if fs.file_row(db, p) is None and fs.index_file(db, p) is not None:
                        n += 1
                except AppError:
                    continue
                if n >= limit:
                    db.commit()
                    return n
    db.commit()
    return n


def run_jobs_once() -> dict[str, Any]:
    """One pass of the housekeeping jobs. Each job is independent; one failing doesn't stop the rest."""
    out: dict[str, Any] = {}
    jobs = {
        "scan": lambda db: len(storage.scan(db)),
        "index": index_tree,
        "purge": lambda db: fs.purge_trash(db),
        "drain": lambda db: sum(storage.drain_step(db, v.id)
                                for v in list(db.scalars(select(Volume).where(Volume.status == "draining")))),
        "directory": lambda db: (directory.sync(db, None) if config.settings.ldap_enabled and directory.is_stale(db, 15)
                                 else None),
    }
    for name, job in jobs.items():
        with new_session() as db:
            try:
                out[name] = job(db)
            except Exception as e:  # noqa: BLE001 - background jobs must keep running
                db.rollback()
                log.warning("job %s failed: %s", name, e)
                out[name] = f"error: {e}"
    return out


async def _job_loop() -> None:
    while True:
        await asyncio.to_thread(run_jobs_once)
        await asyncio.sleep(30)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    startup()
    task = asyncio.create_task(_job_loop()) if config.settings.background_jobs else None
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title="Artifact Buckets", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    # Only used for the short Entra sign-in round trip (authlib keeps its state here).
    config.settings.dev_key()
    app.add_middleware(SessionMiddleware, secret_key=config.settings.secret_key or "", session_cookie="ab_oidc",
                       same_site="lax", https_only=config.settings.secure_cookies, max_age=600)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp: Response = await call_next(request)
        h = resp.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if config.settings.secure_cookies:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith(("/api/", "/oauth/", "/mcp")):
            h.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(AppError)
    async def app_error(request: Request, e: AppError):
        actor = getattr(request.state, "actor", None)
        if e.status == 403 and actor is not None:
            # Refused attempts (a member trying an admin page, a write without rights) go in the audit log.
            with new_session() as db:
                audit.log(db, actor, "access.denied", request.url.path, ok=False, method=request.method, reason=e.message)
                db.commit()
        return JSONResponse({"error": e.code, "message": e.message, **e.extra}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def bad_input(_request: Request, e: RequestValidationError):
        # Don't echo the input back (it may be NaN/Infinity, which JSON can't carry, or a secret).
        errs = [{"loc": [str(x) for x in err.get("loc", [])], "msg": err.get("msg", "")} for err in e.errors()]
        return JSONResponse({"error": "bad_request", "message": "Invalid input", "details": errs}, status_code=422)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(oauth.router)
    app.include_router(claude_dist.router)
    app.include_router(mcp.router)
    app.include_router(api.router)
    app.include_router(api.preview_router)

    static = os.path.realpath(config.settings.static_dir)

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "oauth/", "mcp", ".well-known/", "claude/")):
            return JSONResponse({"error": "not_found", "message": "Not found"}, status_code=404)
        if full_path:
            fp = os.path.realpath(os.path.join(static, full_path))
            if fp.startswith(static + os.sep) and os.path.isfile(fp):
                cache = "public, max-age=31536000, immutable" if "/assets/" in fp else "no-cache"
                return FileResponse(fp, headers={"Cache-Control": cache})
        index = os.path.join(static, "index.html")
        if os.path.isfile(index):
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"error": "not_built", "message": "The web UI has not been built"}, status_code=503)

    return app


app = create_app()
