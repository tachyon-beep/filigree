"""Analytics, graph, and metrics route handlers."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

from starlette.requests import Request

from filigree.core import FiligreeDB, Issue
from filigree.dashboard_routes.common import (
    _GRAPH_STATUS_CATEGORIES,
    _coerce_graph_mode,
    _error_response,
    _get_bool_param,
    _parse_csv_param,
    _resolve_graph_runtime,
    _safe_bounded_int,
)
from filigree.types.api import StatsWithPrefix

# ---------------------------------------------------------------------------
# Graph v2 helpers
# ---------------------------------------------------------------------------


class _GraphV2Params:
    """Validated parameters for a graph v2 query."""

    __slots__ = (
        "assignee_filter",
        "blocked_only",
        "critical_path_only",
        "edge_limit",
        "include_done",
        "node_limit",
        "ready_only",
        "scope_radius",
        "scope_root",
        "status_filter",
        "type_filter",
        "window_cutoff",
        "window_days",
    )

    def __init__(self) -> None:
        self.include_done: bool = True
        self.blocked_only: bool = False
        self.ready_only: bool = False
        self.critical_path_only: bool = False
        self.node_limit: int = 600
        self.edge_limit: int = 2000
        self.scope_root: str | None = None
        self.scope_radius: int = 0
        self.type_filter: set[str] = set()
        self.status_filter: set[str] = set()
        self.assignee_filter: str | None = None
        self.window_days: int | None = None
        self.window_cutoff: datetime | None = None


def _parse_graph_v2_params(
    params: Mapping[str, str],
    issues: list[Issue],
    issue_map: dict[str, Issue],
) -> _GraphV2Params | JSONResponse:
    """Parse and validate all graph v2 query parameters.

    Returns a ``_GraphV2Params`` on success or a ``JSONResponse`` on the
    first validation failure.
    """
    gp = _GraphV2Params()

    include_done = _get_bool_param(params, "include_done", True)
    if not isinstance(include_done, bool):
        return include_done
    gp.include_done = include_done

    blocked_only = _get_bool_param(params, "blocked_only", False)
    if not isinstance(blocked_only, bool):
        return blocked_only
    gp.blocked_only = blocked_only

    ready_only = _get_bool_param(params, "ready_only", False)
    if not isinstance(ready_only, bool):
        return ready_only
    gp.ready_only = ready_only

    critical_path_only = _get_bool_param(params, "critical_path_only", False)
    if not isinstance(critical_path_only, bool):
        return critical_path_only
    gp.critical_path_only = critical_path_only

    if gp.ready_only and gp.blocked_only:
        return _error_response(
            "ready_only and blocked_only cannot both be true.",
            "GRAPH_INVALID_PARAM",
            422,
            {"param": "ready_only,blocked_only"},
        )

    # Limits
    node_limit_raw = params.get("node_limit")
    if node_limit_raw is not None:
        node_limit_value = _safe_bounded_int(node_limit_raw, name="node_limit", min_value=50, max_value=2000)
        if not isinstance(node_limit_value, int):
            return node_limit_value
        gp.node_limit = node_limit_value

    edge_limit_raw = params.get("edge_limit")
    if edge_limit_raw is not None:
        edge_limit_value = _safe_bounded_int(edge_limit_raw, name="edge_limit", min_value=50, max_value=5000)
        if not isinstance(edge_limit_value, int):
            return edge_limit_value
        gp.edge_limit = edge_limit_value

    # Scope
    gp.scope_root = params.get("scope_root") or None
    if gp.scope_root and gp.scope_root not in issue_map:
        return _error_response(
            f"Unknown scope_root issue id: {gp.scope_root}",
            "GRAPH_INVALID_PARAM",
            404,
            {"param": "scope_root", "value": gp.scope_root},
        )

    gp.scope_radius = 2 if gp.scope_root else 0
    scope_radius_raw = params.get("scope_radius")
    if scope_radius_raw is not None:
        scope_radius_value = _safe_bounded_int(scope_radius_raw, name="scope_radius", min_value=0, max_value=6)
        if not isinstance(scope_radius_value, int):
            return scope_radius_value
        gp.scope_radius = scope_radius_value
        if not gp.scope_root:
            return _error_response(
                "scope_radius requires scope_root.",
                "GRAPH_INVALID_PARAM",
                422,
                {"param": "scope_radius", "value": scope_radius_raw},
            )

    # Type filter
    type_filter_raw = params.get("types")
    gp.type_filter = set(_parse_csv_param(type_filter_raw)) if type_filter_raw else set()
    if gp.type_filter:
        known_types = {i.type for i in issues}
        unknown_types = sorted(gp.type_filter - known_types)
        if unknown_types:
            return _error_response(
                f"Unknown types: {', '.join(unknown_types)}",
                "GRAPH_INVALID_PARAM",
                400,
                {"param": "types", "value": type_filter_raw},
            )

    # Status category filter
    status_filter_raw = params.get("status_categories")
    gp.status_filter = set(_parse_csv_param(status_filter_raw)) if status_filter_raw else set()
    if gp.status_filter:
        unknown_cats = sorted(gp.status_filter - _GRAPH_STATUS_CATEGORIES)
        if unknown_cats:
            return _error_response(
                f"Unknown status_categories: {', '.join(unknown_cats)}",
                "GRAPH_INVALID_PARAM",
                400,
                {"param": "status_categories", "value": status_filter_raw},
            )

    gp.assignee_filter = params.get("assignee")

    # Time window
    window_days_raw = params.get("window_days")
    if window_days_raw is not None:
        window_days_value = _safe_bounded_int(window_days_raw, name="window_days", min_value=0, max_value=3650)
        if not isinstance(window_days_value, int):
            return window_days_value
        gp.window_days = window_days_value
    gp.window_cutoff = datetime.now(UTC) - timedelta(days=gp.window_days) if gp.window_days and gp.window_days > 0 else None

    return gp


def _issue_updated_at_utc(issue: Issue) -> datetime | None:
    """Parse an issue's updated_at (or created_at) into a UTC datetime."""
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


def _filter_graph_nodes(
    issues: list[Issue],
    issue_map: dict[str, Issue],
    gp: _GraphV2Params,
    scoped_ids: set[str] | None,
    critical_path_ids: set[str],
) -> list[dict[str, Any]]:
    """Apply all graph v2 filters and return node dicts."""

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

    filtered: list[dict[str, Any]] = []
    for issue in issues:
        if scoped_ids is not None and issue.id not in scoped_ids:
            continue
        if not gp.include_done and issue.status_category == "done":
            continue
        if gp.type_filter and issue.type not in gp.type_filter:
            continue
        if gp.status_filter and issue.status_category not in gp.status_filter:
            continue
        if gp.assignee_filter is not None and issue.assignee != gp.assignee_filter:
            continue
        if gp.window_cutoff is not None:
            ts = _issue_updated_at_utc(issue)
            if ts is None or ts < gp.window_cutoff:
                continue

        blocker_count = _open_blocker_count(issue.id)
        blocks_count = _open_blocks_count(issue.id)
        if gp.blocked_only and blocker_count == 0:
            continue
        if gp.ready_only and not issue.is_ready:
            continue
        if gp.critical_path_only and issue.id not in critical_path_ids:
            continue

        filtered.append(
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
    return filtered


def _filter_graph_edges(
    deps: list[dict[str, Any]],
    visible_ids: set[str],
    critical_path_ids: set[str],
) -> list[dict[str, Any]]:
    """Build edge dicts for visible nodes."""
    return [
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


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_router() -> APIRouter:
    """Build the APIRouter for analytics, graph, and metrics endpoints.

    NOTE: All handlers are intentionally async despite doing synchronous
    SQLite I/O. This serializes DB access on the event loop thread,
    avoiding concurrent multi-thread access to the shared DB connection.
    """
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse

    from filigree.dashboard import _get_db

    router = APIRouter()

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
        issue_map = {i.id: i for i in issues}

        gp = _parse_graph_v2_params(request.query_params, issues, issue_map)
        if isinstance(gp, JSONResponse):
            return gp

        critical_path_ids: set[str] = set()
        if gp.critical_path_only:
            critical_path_ids = {node["id"] for node in db.get_critical_path()}

        # Scope neighborhood (undirected BFS around scope_root)
        scoped_ids: set[str] | None = None
        if gp.scope_root:
            neighbors: dict[str, set[str]] = {}
            for dep in deps:
                neighbors.setdefault(dep["to"], set()).add(dep["from"])
                neighbors.setdefault(dep["from"], set()).add(dep["to"])

            scoped_ids = {gp.scope_root}
            queue: deque[tuple[str, int]] = deque([(gp.scope_root, 0)])
            while queue:
                current, dist = queue.popleft()
                if dist >= gp.scope_radius:
                    continue
                for nxt in neighbors.get(current, set()):
                    if nxt not in scoped_ids:
                        scoped_ids.add(nxt)
                        queue.append((nxt, dist + 1))

        filtered_nodes = _filter_graph_nodes(issues, issue_map, gp, scoped_ids, critical_path_ids)

        total_nodes_before_limit = len(filtered_nodes)
        truncated = False
        if len(filtered_nodes) > gp.node_limit:
            filtered_nodes = filtered_nodes[: gp.node_limit]
            truncated = True

        visible_ids = {node["id"] for node in filtered_nodes}
        filtered_edges = _filter_graph_edges([dict(d) for d in deps], visible_ids, critical_path_ids)

        total_edges_before_limit = len(filtered_edges)
        if len(filtered_edges) > gp.edge_limit:
            filtered_edges = filtered_edges[: gp.edge_limit]
            truncated = True

        query_ms = int((perf_counter() - started) * 1000)
        runtime = _resolve_graph_runtime(db)
        return JSONResponse(
            {
                "mode": "v2",
                "compatibility_mode": runtime["compatibility_mode"],
                "query": {
                    "scope_root": gp.scope_root,
                    "scope_radius": gp.scope_radius if gp.scope_root else None,
                    "include_done": gp.include_done,
                    "types": sorted(gp.type_filter) if gp.type_filter else [],
                    "status_categories": sorted(gp.status_filter) if gp.status_filter else [],
                    "assignee": gp.assignee_filter,
                    "blocked_only": gp.blocked_only,
                    "ready_only": gp.ready_only,
                    "critical_path_only": gp.critical_path_only,
                    "window_days": gp.window_days,
                },
                "limits": {
                    "node_limit": gp.node_limit,
                    "edge_limit": gp.edge_limit,
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
        result = StatsWithPrefix(**db.get_stats(), prefix=db.prefix)
        return JSONResponse(result)

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

    return router
