"""Web dashboard for filigree — interactive project management UI.

Full-featured local web server: kanban board, dependency graph, metrics,
activity feed, workflow visualization. Supports issue management (create,
update, close, reopen, claim, dependency management), batch operations,
and real-time auto-refresh.

**Ethereal mode** (default): single-project.  A module-level ``_db`` is
set at startup and injected via ``Depends(_get_db)``.

**Server mode** (``--server-mode``): multi-project.  A ``ProjectStore``
reads ``server.json``, manages per-project ``FiligreeDB`` connections,
and resolves the active project via a ``ContextVar`` set by middleware.

Usage:
    filigree dashboard                    # Opens browser at localhost:8377
    filigree dashboard --port 9000        # Custom port
    filigree dashboard --no-browser       # Skip auto-open
    filigree dashboard --server-mode      # Multi-project server mode
"""

from __future__ import annotations

import json
import logging
import webbrowser
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request

from filigree import __version__
from filigree.core import (
    DB_FILENAME,
    FiligreeDB,
    find_filigree_root,
    read_config,
)

# Re-export so test imports continue to work.
from filigree.dashboard_routes.common import _safe_bounded_int as _safe_bounded_int

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8377

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state — set by main() or test fixtures
# ---------------------------------------------------------------------------

_db: FiligreeDB | None = None
_config: dict[str, Any] = {}

# Server mode: per-request project key set by middleware
_current_project_key: ContextVar[str] = ContextVar("project_key", default="")


class ProjectStore:
    """Manages multiple FiligreeDB connections for server mode.

    Reads ``server.json`` via :func:`read_server_config`, maps project
    prefixes to ``.filigree/`` paths, and lazily opens DB connections.
    """

    def __init__(self) -> None:
        self._projects: dict[str, dict[str, str]] = {}  # key -> {name, path}
        self._dbs: dict[str, FiligreeDB] = {}

    # -- public API --

    def load(self) -> None:
        """Read server.json and populate the project map.

        Skips directories that don't exist (logs warning).
        Raises ``ValueError`` on prefix collision.
        """
        from filigree.server import SERVER_CONFIG_FILE, read_server_config

        # Fail fast on corrupt JSON so reload() can retain current state.
        if SERVER_CONFIG_FILE.exists():
            try:
                raw = SERVER_CONFIG_FILE.read_text()
                parsed = json.loads(raw)
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Corrupt server config {SERVER_CONFIG_FILE}: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"Corrupt server config {SERVER_CONFIG_FILE}: expected JSON object")
            projects_node = parsed.get("projects", {})
            if not isinstance(projects_node, dict):
                raise ValueError(f"Corrupt server config {SERVER_CONFIG_FILE}: 'projects' must be an object")

        config = read_server_config()
        projects: dict[str, dict[str, str]] = {}
        for filigree_path_str, meta in config.projects.items():
            filigree_path = Path(filigree_path_str)
            if not filigree_path.is_dir():
                logger.warning("Skipping registered project (dir missing): %s", filigree_path)
                continue
            prefix = meta.get("prefix", "filigree")
            if prefix in projects:
                existing = projects[prefix]["path"]
                raise ValueError(f"Prefix collision: {prefix!r} claimed by both {existing} and {filigree_path_str}")
            proj_config = read_config(filigree_path)
            display_name = proj_config.get("name") or prefix
            projects[prefix] = {"name": display_name, "path": filigree_path_str}
        self._projects = projects

    def get_db(self, key: str) -> FiligreeDB:
        """Return (lazily opening) the DB for *key*. Raises ``KeyError``."""
        if key not in self._projects:
            raise KeyError(key)
        if key not in self._dbs:
            info = self._projects[key]
            filigree_path = Path(info["path"])
            db: FiligreeDB | None = None
            try:
                config = read_config(filigree_path)
                db = FiligreeDB(
                    filigree_path / DB_FILENAME,
                    prefix=config.get("prefix", key),
                    check_same_thread=False,
                )
                db.initialize()
                self._dbs[key] = db
            except Exception:
                logger.error("Failed to open project DB for key=%r path=%s", key, filigree_path, exc_info=True)
                if db is not None:
                    db.close()
                raise
        return self._dbs[key]

    def list_projects(self) -> list[dict[str, str]]:
        """Return ``[{key, name, path}]`` for the frontend."""
        return [{"key": k, **v} for k, v in self._projects.items()]

    def reload(self) -> dict[str, Any]:
        """Re-read server.json. On read failure, retains existing state."""
        old_projects = dict(self._projects)
        old_keys = set(old_projects)
        try:
            self.load()
        except Exception as exc:
            logger.error("Failed to reload server.json — retaining existing state", exc_info=True)
            return {"added": [], "removed": [], "error": str(exc)}
        new_keys = set(self._projects)
        removed = sorted(old_keys - new_keys)
        path_changed = sorted(key for key in (old_keys & new_keys) if old_projects[key].get("path") != self._projects[key].get("path"))

        # Close and evict stale DB handles for removed projects and projects
        # whose path changed under the same key.
        for key in [*removed, *path_changed]:
            db = self._dbs.pop(key, None)
            if db is None:
                continue
            try:
                db.close()
            except Exception:
                logger.warning("Error closing removed project DB for key=%r", key, exc_info=True)

        return {
            "added": sorted(new_keys - old_keys),
            "removed": removed,
            "error": "",
        }

    def close_all(self) -> None:
        """Close all open DB connections."""
        for key, db in self._dbs.items():
            try:
                db.close()
            except Exception:
                logger.warning("Error closing DB for project %s", key, exc_info=True)
        self._dbs.clear()

    @property
    def default_key(self) -> str:
        """First loaded project's key, or ``""`` if empty."""
        if not self._projects:
            return ""
        return next(iter(self._projects))


_project_store: ProjectStore | None = None


def _get_db() -> FiligreeDB:
    """Return the active database connection.

    In server mode (``_project_store`` set): resolves the project from
    the per-request ``_current_project_key`` ContextVar.  Falls back to
    ``default_key`` when the var is empty (un-prefixed ``/api/`` route).

    In ethereal mode: returns the module-level ``_db``.
    """
    from fastapi import HTTPException

    if _project_store is not None:
        key = _current_project_key.get() or _project_store.default_key
        if not key:
            raise HTTPException(status_code=503, detail="No projects registered")
        try:
            return _project_store.get_db(key)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown project: {key!r}") from None
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return _db


# ---------------------------------------------------------------------------
# Project-scoped router — all issue, workflow, and file endpoints
# ---------------------------------------------------------------------------


def _create_project_router() -> Any:
    """Build the APIRouter containing all project-scoped endpoints.

    Delegates to domain-specific sub-routers in ``dashboard_routes/``.
    """
    from fastapi import APIRouter

    from filigree.dashboard_routes import analytics, files, issues

    router = APIRouter()
    router.include_router(analytics.create_router())
    router.include_router(issues.create_router())
    router.include_router(files.create_router())
    return router


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(*, server_mode: bool = False) -> Any:
    """Create the FastAPI application with all dashboard endpoints.

    When *server_mode* is ``True`` the app serves multiple projects via
    ``_project_store`` and adds ``/api/p/{key}/…`` routing + management
    endpoints.  Otherwise (ethereal mode) it behaves as a single-project
    dashboard backed by the module-level ``_db``.
    """
    import contextlib
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    # --- MCP streamable-HTTP setup (optional) ---
    _mcp_handler: Any = None
    _mcp_lifespan_factory: Any = None
    try:
        from filigree.mcp_server import create_mcp_app

        if server_mode:
            # Closure reads ContextVar — no changes to mcp_server.py needed
            def _server_db_resolver() -> FiligreeDB | None:
                if _project_store is None:
                    return None
                key = _current_project_key.get() or _project_store.default_key
                if not key:
                    return None
                return _project_store.get_db(key)

            _mcp_handler, _mcp_lifespan_factory = create_mcp_app(db_resolver=_server_db_resolver)
        else:
            _mcp_handler, _mcp_lifespan_factory = create_mcp_app(db_resolver=lambda: _db)
    except ImportError:
        logger.debug("MCP streamable-HTTP not available (SDK not installed or import error)", exc_info=True)

    @contextlib.asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        if _mcp_lifespan_factory is not None:
            async with _mcp_lifespan_factory():
                yield
        else:
            yield

    app = FastAPI(title="Filigree Dashboard", docs_url=None, redoc_url=None, lifespan=_lifespan)

    router = _create_project_router()

    if server_mode:
        # Dual mount: /api/p/{key}/… for explicit project, /api/… for default
        app.include_router(router, prefix="/api/p/{project_key}")
        app.include_router(router, prefix="/api")

        # Middleware: extract project_key from path and set ContextVar
        from starlette.middleware.base import BaseHTTPMiddleware

        class ProjectMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Any:
                path = request.url.path
                # Match /api/p/{key}/… — extract the key segment
                if path.startswith("/api/p/"):
                    parts = path.split("/", 5)  # ['', 'api', 'p', key, ...]
                    if len(parts) >= 4:
                        token = _current_project_key.set(parts[3])
                        try:
                            return await call_next(request)
                        finally:
                            _current_project_key.reset(token)
                return await call_next(request)

        app.add_middleware(ProjectMiddleware)
    else:
        # Ethereal mode: single project at /api/
        app.include_router(router, prefix="/api")

    # Root-level endpoints (not project-scoped)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (STATIC_DIR / "dashboard.html").read_text()
        return HTMLResponse(html)

    @app.get("/api/health")
    async def api_health() -> JSONResponse:
        if server_mode and _project_store is not None:
            return JSONResponse(
                {
                    "status": "ok",
                    "mode": "server",
                    "projects": len(_project_store.list_projects()),
                    "version": __version__,
                }
            )
        return JSONResponse({"status": "ok", "mode": "ethereal", "version": __version__})

    @app.get("/api/projects")
    async def api_projects() -> JSONResponse:
        if server_mode and _project_store is not None:
            return JSONResponse(_project_store.list_projects())
        # Ethereal mode: single project with empty key so setProject("")
        # routes to /api (not /api/p/prefix/ which would 404).
        name = _config.get("name") or (_db.prefix if _db is not None else "")
        return JSONResponse([{"key": "", "name": name, "path": ""}])

    if server_mode:

        @app.post("/api/reload")
        async def api_reload() -> JSONResponse:
            if _project_store is None:
                return JSONResponse({"status": "error", "detail": "Not in server mode"}, status_code=500)
            diff = _project_store.reload()
            if diff.get("error"):
                from filigree.dashboard_routes.common import _error_response

                return _error_response(
                    f"Failed to reload project store: {diff['error']}",
                    "RELOAD_FAILED",
                    409,
                )
            logger.info("Project store reloaded: %s", diff)
            return JSONResponse({"status": "ok", **diff})

    # Serve static JS modules (ES modules for dashboard components)
    from starlette.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Mount MCP streamable-HTTP endpoint.
    if _mcp_handler is not None:
        from starlette.routing import Mount

        if server_mode:
            # Wrap MCP handler to extract ?project= query param
            from urllib.parse import parse_qs

            class _McpProjectWrapper:
                """ASGI wrapper that sets _current_project_key from ?project= query param."""

                def __init__(self, inner: Any) -> None:
                    self._inner = inner

                async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
                    if scope["type"] in ("http", "websocket"):
                        qs = scope.get("query_string", b"").decode()
                        params = parse_qs(qs)
                        project_vals = params.get("project", [])
                        if project_vals:
                            token = _current_project_key.set(project_vals[0])
                            try:
                                await self._inner(scope, receive, send)
                                return
                            finally:
                                _current_project_key.reset(token)
                    await self._inner(scope, receive, send)

            app.routes.append(Mount("/mcp", app=_McpProjectWrapper(_mcp_handler)))  # type: ignore[arg-type]
        else:
            app.routes.append(Mount("/mcp", app=_mcp_handler))

    return app


def main(port: int = DEFAULT_PORT, *, no_browser: bool = False, server_mode: bool = False) -> None:
    """Start the dashboard server.

    In server mode, reads ``server.json`` for multi-project routing.
    In ethereal mode (default), serves the single local project.
    """
    import threading

    import uvicorn

    global _db, _project_store

    if server_mode:
        _project_store = ProjectStore()
        _project_store.load()
        n = len(_project_store.list_projects())
        logger.info("Server mode: loaded %d project(s)", n)
    else:
        filigree_dir = find_filigree_root()
        config = read_config(filigree_dir)
        _config.update(config)
        _db = FiligreeDB(
            filigree_dir / DB_FILENAME,
            prefix=config.get("prefix", "filigree"),
            check_same_thread=False,
        )
        _db.initialize()

    app = create_app(server_mode=server_mode)

    if not no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    mode_label = "Server" if server_mode else "Dashboard"
    print(f"Filigree {mode_label}: http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
