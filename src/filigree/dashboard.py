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
import os
import sqlite3
import webbrowser
from collections import deque
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi.responses import JSONResponse
    from starlette.requests import Request

from filigree.core import (
    DB_FILENAME,
    VALID_ASSOC_TYPES,
    VALID_FINDING_STATUSES,
    VALID_SEVERITIES,
    FiligreeDB,
    find_filigree_root,
    read_config,
)

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8377

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state — set by main() or test fixtures
# ---------------------------------------------------------------------------

_db: FiligreeDB | None = None

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
            projects[prefix] = {"name": prefix, "path": filigree_path_str}
        self._projects = projects

    def get_db(self, key: str) -> FiligreeDB:
        """Return (lazily opening) the DB for *key*. Raises ``KeyError``."""
        if key not in self._projects:
            raise KeyError(key)
        if key not in self._dbs:
            info = self._projects[key]
            filigree_path = Path(info["path"])
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
                logger.debug("Error closing removed project DB for key=%r", key, exc_info=True)

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


def _error_response(
    message: str,
    code: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Return a structured error response and log the error."""
    from fastapi.responses import JSONResponse

    logger.warning("API error [%s] %s: %s", status_code, code, message)
    return JSONResponse(
        {"error": {"message": message, "code": code, "details": details or {}}},
        status_code=status_code,
    )


async def _parse_json_body(request: Request) -> dict[str, Any] | JSONResponse:
    """Parse and validate a JSON object body, returning 400 on failure."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
    if not isinstance(body, dict):
        return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
    return body


def _safe_int(value: str, name: str, *, min_value: int | None = None) -> int | JSONResponse:
    """Parse a query-param string to int, returning a 400 error response on failure.

    When *min_value* is set, values below that floor are rejected with 400.
    """
    try:
        result = int(value)
    except (ValueError, TypeError):
        return _error_response(
            f'Invalid value for {name}: "{value}". Must be an integer.',
            "VALIDATION_ERROR",
            400,
        )
    if min_value is not None and result < min_value:
        return _error_response(
            f"Invalid value for {name}: {result}. Must be >= {min_value}.",
            "VALIDATION_ERROR",
            400,
        )
    return result


_GRAPH_MODE_VALUES = frozenset({"legacy", "v2"})
_GRAPH_STATUS_CATEGORIES = frozenset({"open", "wip", "done"})
_BOOL_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _parse_bool_value(raw: str, name: str) -> bool | JSONResponse:
    value = raw.strip().lower()
    if value in _BOOL_TRUE_VALUES:
        return True
    if value in _BOOL_FALSE_VALUES:
        return False
    return _error_response(
        f'Invalid value for {name}: "{raw}". Must be one of true/false, 1/0, yes/no, on/off.',
        "GRAPH_INVALID_PARAM",
        400,
        {"param": name, "value": raw},
    )


def _get_bool_param(params: Mapping[str, str], name: str, default: bool) -> bool | JSONResponse:
    """Extract a boolean query param, returning *default* when absent."""
    raw = params.get(name)
    if raw is None:
        return default
    return _parse_bool_value(raw, name)


def _read_graph_runtime_config(db: FiligreeDB) -> dict[str, Any]:
    """Read graph runtime settings from project config, if available.

    Note: read_config() already handles JSONDecodeError/OSError internally
    and returns defaults, so no outer try/except is needed here.
    """
    return read_config(db.db_path.parent)


def _resolve_graph_runtime(db: FiligreeDB) -> dict[str, Any]:
    """Resolve graph feature controls from env + project config."""
    config = _read_graph_runtime_config(db)

    enabled_raw = os.getenv("FILIGREE_GRAPH_V2_ENABLED")
    enabled: bool
    if enabled_raw is not None:
        enabled_value = _parse_bool_value(enabled_raw, "FILIGREE_GRAPH_V2_ENABLED")
        enabled = bool(enabled_value) if isinstance(enabled_value, bool) else False
    else:
        enabled = bool(config.get("graph_v2_enabled", False))

    configured_mode_raw = os.getenv("FILIGREE_GRAPH_API_MODE") or str(config.get("graph_api_mode", "")).strip()
    configured_mode = configured_mode_raw.lower() if configured_mode_raw else ""
    if configured_mode not in _GRAPH_MODE_VALUES:
        configured_mode = ""

    compatibility_mode = configured_mode or ("v2" if enabled else "legacy")
    return {
        "v2_enabled": enabled,
        "configured_mode": configured_mode or None,
        "compatibility_mode": compatibility_mode,
    }


def _parse_csv_param(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _safe_bounded_int(raw: str, *, name: str, min_value: int, max_value: int) -> int | JSONResponse:
    value = _safe_int(raw, name)
    if not isinstance(value, int):
        return value  # pass through _safe_int's error response
    if value < min_value or value > max_value:
        return _error_response(
            f'Invalid value for {name}: "{raw}". Must be between {min_value} and {max_value}.',
            "GRAPH_INVALID_PARAM",
            400,
            {"param": name, "value": raw},
        )
    return value


def _coerce_graph_mode(raw: str | None, db: FiligreeDB) -> str | JSONResponse:
    runtime = _resolve_graph_runtime(db)
    if raw is None:
        return str(runtime["compatibility_mode"])
    mode = raw.strip().lower()
    if mode not in _GRAPH_MODE_VALUES:
        return _error_response(
            f'Invalid value for mode: "{raw}". Must be one of: legacy, v2.',
            "GRAPH_INVALID_PARAM",
            400,
            {"param": "mode", "value": raw},
        )
    return mode


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
# Project-scoped router — all 32 issue/workflow endpoints
# ---------------------------------------------------------------------------


def _create_project_router() -> Any:
    """Build the APIRouter containing all project-scoped endpoints."""
    from fastapi import APIRouter, Depends, Request
    from fastapi.responses import JSONResponse

    # Expose Request in module globals so PEP 563 deferred annotations resolve
    globals()["Request"] = Request

    router = APIRouter()

    # NOTE: All handlers are intentionally async despite doing synchronous
    # SQLite I/O. This serializes DB access on the event loop thread,
    # avoiding concurrent multi-thread access to the shared DB connection.
    # Using plain `def` would cause FastAPI to dispatch handlers to a thread
    # pool, where parallel threads would race on the single SQLite connection.

    @router.get("/issues")
    async def api_issues(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        issues = db.list_issues(limit=10000)
        return JSONResponse([i.to_dict() for i in issues])

    @router.get("/config")
    async def api_config(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Dashboard runtime config exposed to frontend consumers."""
        runtime = _resolve_graph_runtime(db)
        return JSONResponse(
            {
                "graph_v2_enabled": runtime["v2_enabled"],
                "graph_api_mode": runtime["compatibility_mode"],
                "graph_mode_configured": runtime["configured_mode"],
            }
        )

    @router.get("/graph")
    async def api_graph(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Graph data API with legacy and v2 compatibility modes."""
        mode = _coerce_graph_mode(request.query_params.get("mode"), db)
        if isinstance(mode, JSONResponse):
            return mode

        issues = db.list_issues(limit=10000)
        deps = db.get_all_dependencies()

        # Legacy behavior remains the default compatibility path.
        if mode == "legacy":
            nodes = [
                {
                    "id": i.id,
                    "title": i.title,
                    "status": i.status,
                    "status_category": i.status_category,
                    "priority": i.priority,
                    "type": i.type,
                }
                for i in issues
            ]
            edges = [{"source": d["to"], "target": d["from"]} for d in deps]
            return JSONResponse({"nodes": nodes, "edges": edges})

        # Graph v2 query model
        started = perf_counter()
        params = request.query_params
        issue_map = {i.id: i for i in issues}

        include_done = _get_bool_param(params, "include_done", True)
        if isinstance(include_done, JSONResponse):
            return include_done
        blocked_only = _get_bool_param(params, "blocked_only", False)
        if isinstance(blocked_only, JSONResponse):
            return blocked_only
        ready_only = _get_bool_param(params, "ready_only", False)
        if isinstance(ready_only, JSONResponse):
            return ready_only
        critical_path_only = _get_bool_param(params, "critical_path_only", False)
        if isinstance(critical_path_only, JSONResponse):
            return critical_path_only

        if ready_only and blocked_only:
            return _error_response(
                "ready_only and blocked_only cannot both be true.",
                "GRAPH_INVALID_PARAM",
                422,
                {"param": "ready_only,blocked_only"},
            )

        node_limit = 600
        node_limit_raw = params.get("node_limit")
        if node_limit_raw is not None:
            node_limit_value = _safe_bounded_int(node_limit_raw, name="node_limit", min_value=50, max_value=2000)
            if isinstance(node_limit_value, JSONResponse):
                return node_limit_value
            node_limit = node_limit_value

        edge_limit = 2000
        edge_limit_raw = params.get("edge_limit")
        if edge_limit_raw is not None:
            edge_limit_value = _safe_bounded_int(edge_limit_raw, name="edge_limit", min_value=50, max_value=5000)
            if isinstance(edge_limit_value, JSONResponse):
                return edge_limit_value
            edge_limit = edge_limit_value

        scope_root = params.get("scope_root") or None
        if scope_root and scope_root not in issue_map:
            return _error_response(
                f"Unknown scope_root issue id: {scope_root}",
                "GRAPH_INVALID_PARAM",
                404,
                {"param": "scope_root", "value": scope_root},
            )

        scope_radius = 2 if scope_root else 0
        scope_radius_raw = params.get("scope_radius")
        if scope_radius_raw is not None:
            scope_radius_value = _safe_bounded_int(scope_radius_raw, name="scope_radius", min_value=0, max_value=6)
            if isinstance(scope_radius_value, JSONResponse):
                return scope_radius_value
            scope_radius = scope_radius_value
            if not scope_root:
                return _error_response(
                    "scope_radius requires scope_root.",
                    "GRAPH_INVALID_PARAM",
                    422,
                    {"param": "scope_radius", "value": scope_radius_raw},
                )

        type_filter_raw = params.get("types")
        type_filter = set(_parse_csv_param(type_filter_raw)) if type_filter_raw else set()
        if type_filter:
            known_types = {i.type for i in issues}
            unknown_types = sorted(type_filter - known_types)
            if unknown_types:
                return _error_response(
                    f"Unknown types: {', '.join(unknown_types)}",
                    "GRAPH_INVALID_PARAM",
                    400,
                    {"param": "types", "value": type_filter_raw},
                )

        status_filter_raw = params.get("status_categories")
        status_filter = set(_parse_csv_param(status_filter_raw)) if status_filter_raw else set()
        if status_filter:
            unknown_cats = sorted(status_filter - _GRAPH_STATUS_CATEGORIES)
            if unknown_cats:
                return _error_response(
                    f"Unknown status_categories: {', '.join(unknown_cats)}",
                    "GRAPH_INVALID_PARAM",
                    400,
                    {"param": "status_categories", "value": status_filter_raw},
                )

        assignee_filter = params.get("assignee")

        window_days: int | None = None
        window_days_raw = params.get("window_days")
        if window_days_raw is not None:
            window_days_value = _safe_bounded_int(window_days_raw, name="window_days", min_value=0, max_value=3650)
            if isinstance(window_days_value, JSONResponse):
                return window_days_value
            window_days = window_days_value
        window_cutoff = datetime.now(UTC) - timedelta(days=window_days) if window_days and window_days > 0 else None

        critical_path_ids: set[str] = set()
        if critical_path_only:
            critical_path_ids = {node["id"] for node in db.get_critical_path()}

        # Scope neighborhood (undirected BFS around scope_root)
        scoped_ids: set[str] | None = None
        if scope_root:
            neighbors: dict[str, set[str]] = {}
            for dep in deps:
                blocker = dep["to"]
                blocked = dep["from"]
                neighbors.setdefault(blocker, set()).add(blocked)
                neighbors.setdefault(blocked, set()).add(blocker)

            scoped_ids = {scope_root}
            queue: deque[tuple[str, int]] = deque([(scope_root, 0)])
            while queue:
                current, dist = queue.popleft()
                if dist >= scope_radius:
                    continue
                for nxt in neighbors.get(current, set()):
                    if nxt in scoped_ids:
                        continue
                    scoped_ids.add(nxt)
                    queue.append((nxt, dist + 1))

        def _open_blocker_count(issue_id: str) -> int:
            issue = issue_map[issue_id]
            total = 0
            for blocker_id in issue.blocked_by:
                blocker = issue_map.get(blocker_id)
                if blocker and blocker.status_category != "done":
                    total += 1
            return total

        def _open_blocks_count(issue_id: str) -> int:
            issue = issue_map[issue_id]
            total = 0
            for blocked_id in issue.blocks:
                blocked_issue = issue_map.get(blocked_id)
                if blocked_issue and blocked_issue.status_category != "done":
                    total += 1
            return total

        def _issue_updated_at(issue: Any) -> datetime | None:
            raw = issue.updated_at or issue.created_at
            if not raw or not isinstance(raw, str):
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)

        filtered_nodes: list[dict[str, Any]] = []
        for issue in issues:
            if scoped_ids is not None and issue.id not in scoped_ids:
                continue
            if not include_done and issue.status_category == "done":
                continue
            if type_filter and issue.type not in type_filter:
                continue
            if status_filter and issue.status_category not in status_filter:
                continue
            if assignee_filter is not None and issue.assignee != assignee_filter:
                continue
            if window_cutoff is not None:
                issue_updated_at = _issue_updated_at(issue)
                if issue_updated_at is None or issue_updated_at < window_cutoff:
                    continue

            blocker_count = _open_blocker_count(issue.id)
            blocks_count = _open_blocks_count(issue.id)
            if blocked_only and blocker_count == 0:
                continue
            if ready_only and not issue.is_ready:
                continue
            if critical_path_only and issue.id not in critical_path_ids:
                continue

            filtered_nodes.append(
                {
                    "id": issue.id,
                    "title": issue.title,
                    "status": issue.status,
                    "status_category": issue.status_category,
                    "priority": issue.priority,
                    "type": issue.type,
                    "assignee": issue.assignee,
                    "is_ready": issue.is_ready,
                    "blocked_by_open_count": blocker_count,
                    "blocks_open_count": blocks_count,
                }
            )

        total_nodes_before_limit = len(filtered_nodes)
        truncated = False
        if len(filtered_nodes) > node_limit:
            filtered_nodes = filtered_nodes[:node_limit]
            truncated = True

        visible_ids = {node["id"] for node in filtered_nodes}
        filtered_edges = [
            {
                "id": f"{dep['to']}->{dep['from']}",
                "source": dep["to"],
                "target": dep["from"],
                "kind": dep["type"],
                "is_critical_path": dep["to"] in critical_path_ids and dep["from"] in critical_path_ids,
            }
            for dep in deps
            if dep["to"] in visible_ids and dep["from"] in visible_ids
        ]

        total_edges_before_limit = len(filtered_edges)
        if len(filtered_edges) > edge_limit:
            filtered_edges = filtered_edges[:edge_limit]
            truncated = True

        query_ms = int((perf_counter() - started) * 1000)
        runtime = _resolve_graph_runtime(db)
        return JSONResponse(
            {
                "mode": "v2",
                "compatibility_mode": runtime["compatibility_mode"],
                "query": {
                    "scope_root": scope_root,
                    "scope_radius": scope_radius if scope_root else None,
                    "include_done": include_done,
                    "types": sorted(type_filter) if type_filter else [],
                    "status_categories": sorted(status_filter) if status_filter else [],
                    "assignee": assignee_filter,
                    "blocked_only": blocked_only,
                    "ready_only": ready_only,
                    "critical_path_only": critical_path_only,
                    "window_days": window_days,
                },
                "limits": {
                    "node_limit": node_limit,
                    "edge_limit": edge_limit,
                    "truncated": truncated,
                },
                "telemetry": {
                    "query_ms": query_ms,
                    "total_nodes_before_limit": total_nodes_before_limit,
                    "total_edges_before_limit": total_edges_before_limit,
                },
                "nodes": filtered_nodes,
                "edges": filtered_edges,
            }
        )

    @router.get("/stats")
    async def api_stats(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        stats = db.get_stats()
        stats["prefix"] = db.prefix
        return JSONResponse(stats)

    @router.get("/issue/{issue_id}")
    async def api_issue_detail(issue_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Full issue detail with dependency details, events, and comments."""
        try:
            issue = db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)

        data = issue.to_dict()

        # Resolve dep details for blocks and blocked_by
        dep_ids = set(issue.blocks + issue.blocked_by)
        dep_details: dict[str, dict[str, Any]] = {}
        for did in dep_ids:
            try:
                dep = db.get_issue(did)
                dep_details[did] = {
                    "title": dep.title,
                    "status": dep.status,
                    "status_category": dep.status_category,
                    "priority": dep.priority,
                }
            except KeyError:
                dep_details[did] = {
                    "title": did,
                    "status": "unknown",
                    "status_category": "open",
                    "priority": 2,
                }
        data["dep_details"] = dep_details

        # Events
        events = db.conn.execute(
            "SELECT event_type, actor, old_value, new_value, created_at FROM events WHERE issue_id = ? ORDER BY created_at DESC LIMIT 20",
            (issue_id,),
        ).fetchall()
        data["events"] = [dict(e) for e in events]

        # Comments
        data["comments"] = db.get_comments(issue_id)

        return JSONResponse(data)

    @router.get("/dependencies")
    async def api_dependencies(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        deps = db.get_all_dependencies()
        return JSONResponse(deps)

    @router.get("/type/{type_name}")
    async def api_type_template(type_name: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Workflow template for a given issue type (WFT-FR-065)."""
        tpl = db.templates.get_type(type_name)
        if tpl is None:
            valid_types = [t.type for t in db.templates.list_types()]
            return _error_response(
                f'Unknown type "{type_name}". Valid types: {", ".join(valid_types)}',
                "INVALID_TYPE",
                404,
            )
        return JSONResponse(
            {
                "type": tpl.type,
                "display_name": tpl.display_name,
                "states": [{"name": s.name, "category": s.category} for s in tpl.states],
                "initial_state": tpl.initial_state,
                "transitions": [{"from": t.from_state, "to": t.to_state, "enforcement": t.enforcement} for t in tpl.transitions],
            }
        )

    @router.get("/issue/{issue_id}/transitions")
    async def api_issue_transitions(issue_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Valid next states for an issue."""
        try:
            transitions = db.get_valid_transitions(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        return JSONResponse(
            [
                {
                    "to": t.to,
                    "category": t.category,
                    "enforcement": t.enforcement,
                    "ready": t.ready,
                    "missing_fields": list(t.missing_fields),
                    "requires_fields": list(t.requires_fields),
                }
                for t in transitions
            ]
        )

    @router.get("/issue/{issue_id}/files")
    async def api_issue_files(issue_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Files associated with an issue."""
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        files = db.get_issue_files(issue_id)
        return JSONResponse(files)

    @router.get("/issue/{issue_id}/findings")
    async def api_issue_findings(issue_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Scan findings related to an issue."""
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        findings = db.get_issue_findings(issue_id)
        return JSONResponse([f.to_dict() for f in findings])

    @router.patch("/issue/{issue_id}")
    async def api_update_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Update issue fields (status, priority, assignee, etc.)."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        actor = body.pop("actor", "dashboard")
        priority = body.get("priority")
        if priority is not None and not isinstance(priority, int):
            return _error_response("priority must be an integer between 0 and 4", "INVALID_PRIORITY", 400)
        try:
            issue = db.update_issue(
                issue_id,
                status=body.get("status"),
                priority=priority,
                assignee=body.get("assignee"),
                title=body.get("title"),
                description=body.get("description"),
                notes=body.get("notes"),
                fields=body.get("fields"),
                actor=actor,
            )
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "TRANSITION_ERROR", 409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/close")
    async def api_close_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Close an issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        actor = body.get("actor", "dashboard")
        reason = body.get("reason", "")
        fields = body.get("fields")
        try:
            issue = db.close_issue(issue_id, reason=reason, actor=actor, fields=fields)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        except TypeError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        except ValueError as e:
            return _error_response(str(e), "TRANSITION_ERROR", 409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/reopen")
    async def api_reopen_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Reopen a closed issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        actor = body.get("actor", "dashboard")
        try:
            issue = db.reopen_issue(issue_id, actor=actor)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "TRANSITION_ERROR", 409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/comments", status_code=201)
    async def api_add_comment(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Add a comment to an issue."""
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        text = body.get("text", "")
        author = body.get("author", "")
        try:
            comment_id = db.add_comment(issue_id, text, author=author)
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        # Fetch the comment from DB to get the real created_at timestamp
        comments = db.get_comments(issue_id)
        created_at = ""
        for c in comments:
            if c["id"] == comment_id:
                created_at = c["created_at"]
                break
        return JSONResponse(
            {"id": comment_id, "author": author, "text": text, "created_at": created_at},
            status_code=201,
        )

    @router.get("/search")
    async def api_search(q: str = "", limit: int = 50, offset: int = 0, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Full-text search across issues."""
        if not q.strip():
            return JSONResponse({"results": [], "total": 0})
        issues = db.search_issues(q, limit=limit, offset=offset)
        return JSONResponse({"results": [i.to_dict() for i in issues], "total": len(issues)})

    @router.get("/metrics")
    async def api_metrics(days: int = 30, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Flow metrics: cycle time, lead time, throughput."""
        from filigree.analytics import get_flow_metrics

        metrics = get_flow_metrics(db, days=days)
        return JSONResponse(metrics)

    @router.get("/critical-path")
    async def api_critical_path(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Longest dependency chain among open issues."""
        path = db.get_critical_path()
        return JSONResponse({"path": path, "length": len(path)})

    @router.get("/activity")
    async def api_activity(limit: int = 50, since: str = "", db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Recent events across all issues."""
        events = db.get_events_since(since, limit=limit) if since else db.get_recent_events(limit=limit)
        return JSONResponse(events)

    @router.get("/plan/{milestone_id}")
    async def api_plan(milestone_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Milestone plan tree."""
        try:
            plan = db.get_plan(milestone_id)
        except KeyError:
            return _error_response(f"Issue not found: {milestone_id}", "ISSUE_NOT_FOUND", 404)
        return JSONResponse(plan)

    @router.post("/batch/update")
    async def api_batch_update(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Batch update issues."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        issue_ids = body.get("issue_ids")
        if not isinstance(issue_ids, list):
            return _error_response("issue_ids must be a JSON array", "VALIDATION_ERROR", 400)
        if not all(isinstance(i, str) for i in issue_ids):
            return _error_response("All issue_ids must be strings", "VALIDATION_ERROR", 400)
        actor = body.get("actor", "dashboard")
        priority = body.get("priority")
        if priority is not None and not isinstance(priority, int):
            return _error_response("priority must be an integer between 0 and 4", "INVALID_PRIORITY", 400)
        updated, errors = db.batch_update(
            issue_ids,
            status=body.get("status"),
            priority=priority,
            assignee=body.get("assignee"),
            fields=body.get("fields"),
            actor=actor,
        )
        return JSONResponse(
            {
                "updated": [i.to_dict() for i in updated],
                "errors": errors,
            }
        )

    @router.post("/batch/close")
    async def api_batch_close(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Batch close issues."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        issue_ids = body.get("issue_ids")
        if not isinstance(issue_ids, list):
            return _error_response("issue_ids must be a JSON array", "VALIDATION_ERROR", 400)
        if not all(isinstance(i, str) for i in issue_ids):
            return _error_response("All issue_ids must be strings", "VALIDATION_ERROR", 400)
        reason = body.get("reason", "")
        actor = body.get("actor", "dashboard")
        closed, errors = db.batch_close(issue_ids, reason=reason, actor=actor)
        return JSONResponse(
            {
                "closed": [i.to_dict() for i in closed],
                "errors": errors,
            }
        )

    @router.get("/types")
    async def api_types_list(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """List all registered issue types."""
        types = db.templates.list_types()
        return JSONResponse(
            [
                {
                    "type": t.type,
                    "display_name": t.display_name,
                    "pack": t.pack,
                    "initial_state": t.initial_state,
                }
                for t in types
            ]
        )

    @router.post("/issues", status_code=201)
    async def api_create_issue(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Create a new issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        title = body.get("title", "")
        priority = body.get("priority", 2)
        if not isinstance(priority, int):
            return _error_response("priority must be an integer between 0 and 4", "INVALID_PRIORITY", 400)
        try:
            issue = db.create_issue(
                title,
                type=body.get("type", "task"),
                priority=priority,
                parent_id=body.get("parent_id"),
                assignee=body.get("assignee", ""),
                description=body.get("description", ""),
                notes=body.get("notes", ""),
                labels=body.get("labels"),
                deps=body.get("deps"),
                actor=body.get("actor", ""),
            )
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse(issue.to_dict(), status_code=201)

    @router.post("/issue/{issue_id}/claim")
    async def api_claim_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Claim an issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        assignee = body.get("assignee", "")
        if not assignee or not assignee.strip():
            return _error_response("assignee is required and cannot be empty", "VALIDATION_ERROR", 400)
        actor = body.get("actor", "dashboard")
        try:
            issue = db.claim_issue(issue_id, assignee=assignee, actor=actor)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "CLAIM_CONFLICT", 409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/release")
    async def api_release_claim(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Release a claimed issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        actor = body.get("actor", "dashboard")
        try:
            issue = db.release_claim(issue_id, actor=actor)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "CLAIM_CONFLICT", 409)
        return JSONResponse(issue.to_dict())

    @router.post("/claim-next")
    async def api_claim_next(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Claim the highest-priority ready issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        assignee = body.get("assignee", "")
        if not assignee or not assignee.strip():
            return _error_response("assignee is required and cannot be empty", "VALIDATION_ERROR", 400)
        actor = body.get("actor", "dashboard")
        try:
            issue = db.claim_next(assignee, actor=actor)
        except ValueError as e:
            return _error_response(str(e), "CLAIM_CONFLICT", 409)
        if issue is None:
            return _error_response("No ready issues to claim", "ISSUE_NOT_FOUND", 404)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/dependencies")
    async def api_add_dependency(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Add a dependency: issue_id depends on depends_on."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        depends_on = body.get("depends_on", "")
        actor = body.get("actor", "dashboard")
        try:
            added = db.add_dependency(issue_id, depends_on, actor=actor)
        except KeyError as e:
            return _error_response(str(e), "ISSUE_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "DEPENDENCY_ERROR", 409)
        return JSONResponse({"added": added})

    @router.delete("/issue/{issue_id}/dependencies/{dep_id}")
    async def api_remove_dependency(issue_id: str, dep_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Remove a dependency."""
        try:
            removed = db.remove_dependency(issue_id, dep_id, actor="dashboard")
        except KeyError as e:
            return _error_response(str(e), "ISSUE_NOT_FOUND", 404)
        return JSONResponse({"removed": removed})

    @router.get("/files")
    async def api_list_files(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """List tracked file records with optional filtering and pagination."""
        params = request.query_params
        limit = _safe_int(params.get("limit", "100"), "limit", min_value=1)
        if isinstance(limit, JSONResponse):
            return limit
        offset = _safe_int(params.get("offset", "0"), "offset", min_value=0)
        if isinstance(offset, JSONResponse):
            return offset
        min_findings = _safe_int(params.get("min_findings", "0"), "min_findings", min_value=0)
        if isinstance(min_findings, JSONResponse):
            return min_findings
        result = db.list_files_paginated(
            limit=limit,
            offset=offset,
            language=params.get("language"),
            path_prefix=params.get("path_prefix"),
            min_findings=min_findings if min_findings > 0 else None,
            has_severity=params.get("has_severity"),
            scan_source=params.get("scan_source"),
            sort=params.get("sort", "updated_at"),
            direction=params.get("direction"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-cache"})

    @router.get("/files/hotspots")
    async def api_file_hotspots(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Files ranked by weighted finding severity score."""
        params = request.query_params
        limit = _safe_int(params.get("limit", "10"), "limit", min_value=1)
        if isinstance(limit, JSONResponse):
            return limit
        result = db.get_file_hotspots(limit=limit)
        return JSONResponse(result)

    @router.get("/files/stats")
    async def api_file_stats(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Global findings severity stats across all files."""
        return JSONResponse(db.get_global_findings_stats())

    @router.get("/files/_schema")
    async def api_files_schema() -> JSONResponse:
        """API discovery: valid enum values and endpoint catalog for file/scan features."""
        schema = {
            "valid_severities": sorted(VALID_SEVERITIES),
            "valid_finding_statuses": sorted(VALID_FINDING_STATUSES),
            "valid_association_types": sorted(VALID_ASSOC_TYPES),
            "valid_file_sort_fields": ["first_seen", "language", "path", "updated_at"],
            "valid_finding_sort_fields": ["severity", "updated_at"],
            "endpoints": [
                {
                    "method": "POST",
                    "path": "/api/v1/scan-results",
                    "description": "Ingest scan results",
                    "status": "live",
                    "request_body": {
                        "scan_source": "string (required)",
                        "findings": "array (required)",
                        "scan_run_id": "string (optional)",
                        "mark_unseen": "boolean (optional)",
                    },
                },
                {"method": "GET", "path": "/api/files", "description": "List tracked files", "status": "live"},
                {
                    "method": "GET",
                    "path": "/api/files/{file_id}",
                    "description": "Get file details",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/{file_id}/findings",
                    "description": "Findings for a specific file",
                    "status": "live",
                },
                {
                    "method": "PATCH",
                    "path": "/api/files/{file_id}/findings/{finding_id}",
                    "description": "Update finding status/linkage",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/{file_id}/timeline",
                    "description": "Merged event timeline for a file",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/hotspots",
                    "description": "Files ranked by weighted finding severity",
                    "status": "live",
                },
                {
                    "method": "POST",
                    "path": "/api/files/{file_id}/associations",
                    "description": "Link a file to an issue",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/stats",
                    "description": "Global findings severity stats",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/scan-runs",
                    "description": "Scan run history (grouped by scan_run_id)",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/_schema",
                    "description": "API discovery (this endpoint)",
                    "status": "live",
                },
            ],
        }
        return JSONResponse(schema, headers={"Cache-Control": "max-age=3600"})

    @router.get("/files/{file_id}")
    async def api_get_file(file_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Get file record with associations, recent findings, and summary."""
        try:
            data = db.get_file_detail(file_id)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        return JSONResponse(data, headers={"Cache-Control": "no-cache"})

    @router.get("/files/{file_id}/findings")
    async def api_get_file_findings(file_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Get scan findings for a file with pagination."""
        try:
            db.get_file(file_id)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        params = request.query_params
        limit = _safe_int(params.get("limit", "100"), "limit", min_value=1)
        if isinstance(limit, JSONResponse):
            return limit
        offset = _safe_int(params.get("offset", "0"), "offset", min_value=0)
        if isinstance(offset, JSONResponse):
            return offset
        try:
            result = db.get_findings_paginated(
                file_id,
                severity=params.get("severity"),
                status=params.get("status"),
                sort=params.get("sort", "updated_at"),
                limit=limit,
                offset=offset,
            )
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse(result, headers={"Cache-Control": "max-age=30"})

    @router.patch("/files/{file_id}/findings/{finding_id}")
    async def api_update_file_finding(
        file_id: str,
        finding_id: str,
        request: Request,
        db: FiligreeDB = Depends(_get_db),
    ) -> JSONResponse:
        """Update finding status and/or linked issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        status = body.get("status")
        issue_id = body.get("issue_id")
        if status is None and issue_id is None:
            return _error_response("At least one of status or issue_id is required", "VALIDATION_ERROR", 400)
        if status is not None and not isinstance(status, str):
            return _error_response("status must be a string", "VALIDATION_ERROR", 400)
        if issue_id is not None and not isinstance(issue_id, str):
            return _error_response("issue_id must be a string", "VALIDATION_ERROR", 400)
        try:
            finding = db.update_finding(
                file_id,
                finding_id,
                status=status,
                issue_id=issue_id,
            )
        except KeyError:
            return _error_response(f"Finding not found: {finding_id}", "FINDING_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse(finding.to_dict())

    @router.get("/files/{file_id}/timeline")
    async def api_get_file_timeline(file_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Get merged timeline of events for a file."""
        params = request.query_params
        limit = _safe_int(params.get("limit", "50"), "limit", min_value=1)
        if isinstance(limit, JSONResponse):
            return limit
        offset = _safe_int(params.get("offset", "0"), "offset", min_value=0)
        if isinstance(offset, JSONResponse):
            return offset
        event_type = params.get("event_type")
        try:
            result = db.get_file_timeline(file_id, limit=limit, offset=offset, event_type=event_type)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        return JSONResponse(result)

    @router.post("/files/{file_id}/associations")
    async def api_add_file_association(file_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Link a file to an issue."""
        try:
            db.get_file(file_id)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        issue_id = body.get("issue_id", "")
        assoc_type = body.get("assoc_type", "")
        if not issue_id or not assoc_type:
            return _error_response("issue_id and assoc_type are required", "VALIDATION_ERROR", 400)
        try:
            db.add_file_association(file_id, issue_id, assoc_type)
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse({"status": "created"}, status_code=201)

    @router.post("/v1/scan-results")
    async def api_scan_results(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Ingest scan results."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        scan_source = body.get("scan_source", "")
        if not isinstance(scan_source, str) or not scan_source:
            return _error_response("scan_source is required and must be a string", "VALIDATION_ERROR", 400)
        findings = body.get("findings", [])
        if "create_issues" in body:
            return _error_response(
                "create_issues is not supported on scan ingest; create tickets via UI or MCP",
                "VALIDATION_ERROR",
                400,
            )
        mark_unseen = body.get("mark_unseen", False)
        if not isinstance(mark_unseen, bool):
            return _error_response("mark_unseen must be a boolean", "VALIDATION_ERROR", 400)
        status_code = 202 if not findings else 200
        try:
            result = db.process_scan_results(
                scan_source=scan_source,
                findings=findings,
                scan_run_id=body.get("scan_run_id", ""),
                mark_unseen=mark_unseen,
            )
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse(result, status_code=status_code)

    @router.get("/scan-runs")
    async def api_scan_runs(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Get scan run history from scan_findings grouped by scan_run_id."""
        params = request.query_params
        limit = _safe_int(params.get("limit", "10"), "limit", min_value=1)
        if isinstance(limit, JSONResponse):
            return limit
        try:
            runs = db.get_scan_runs(limit=limit)
        except sqlite3.Error:
            logger.exception("Failed to query scan runs")
            return _error_response("Failed to query scan runs", "INTERNAL_ERROR", 500)
        return JSONResponse({"scan_runs": runs}, headers={"Cache-Control": "no-cache"})

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

    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    # Expose Request in module globals so PEP 563 deferred annotations resolve
    globals()["Request"] = Request

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
        pass  # MCP SDK not installed

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
                }
            )
        return JSONResponse({"status": "ok", "mode": "ethereal"})

    @app.get("/api/projects")
    async def api_projects() -> JSONResponse:
        if server_mode and _project_store is not None:
            return JSONResponse(_project_store.list_projects())
        # Ethereal mode: single project with empty key so setProject("")
        # routes to /api (not /api/p/prefix/ which would 404).
        name = _db.prefix if _db is not None else ""
        return JSONResponse([{"key": "", "name": name, "path": ""}])

    if server_mode:

        @app.post("/api/reload")
        async def api_reload() -> JSONResponse:
            if _project_store is None:
                return JSONResponse({"status": "error", "detail": "Not in server mode"}, status_code=500)
            diff = _project_store.reload()
            if diff.get("error"):
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
