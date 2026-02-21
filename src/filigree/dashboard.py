"""Web dashboard for filigree — interactive project management UI.

Full-featured local web server: kanban board, dependency graph, metrics,
activity feed, workflow visualization. Supports issue management (create,
update, close, reopen, claim, dependency management), batch operations,
and real-time auto-refresh.

Multi-project support: all project-scoped endpoints live on an APIRouter
mounted at both ``/api/p/{project_key}/`` (explicit project) and ``/api/``
(default project, backward compatible).  Root-level endpoints like
``/api/health``, ``/api/projects``, and ``/api/register`` are not scoped.

Usage:
    filigree dashboard                    # Opens browser at localhost:8377
    filigree dashboard --port 9000        # Custom port
    filigree dashboard --no-browser       # Skip auto-open
"""

from __future__ import annotations

import logging
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi.responses import JSONResponse
    from starlette.requests import Request

from filigree.core import (
    VALID_ASSOC_TYPES,
    VALID_FINDING_STATUSES,
    VALID_SEVERITIES,
    FiligreeDB,
    find_filigree_root,
)
from filigree.registry import ProjectManager, Registry

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8377

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state — set by main() or test fixtures
# ---------------------------------------------------------------------------

_project_manager: ProjectManager | None = None
_default_project_key: str = ""


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


def _safe_int(value: str, name: str, default: int) -> int | JSONResponse:
    """Parse a query-param string to int, returning a 400 error response on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return _error_response(
            f'Invalid value for {name}: "{value}". Must be an integer.',
            "VALIDATION_ERROR",
            400,
        )


def _get_project_db(project_key: str = "") -> FiligreeDB:
    """Resolve *project_key* to a DB connection via the ProjectManager.

    When the router is mounted at ``/api/p/{project_key}/``, FastAPI injects
    the path parameter.  When mounted at ``/api/``, the default ``""`` falls
    through to ``_default_project_key``.
    """
    if _project_manager is None:
        msg = "Project manager not initialized"
        raise RuntimeError(msg)
    key = project_key if project_key else _default_project_key
    db = _project_manager.get_db(key)
    if db is None:
        from fastapi import HTTPException

        projects = _project_manager.get_active_projects()
        available = [p.key for p in projects]
        hint = f" Available projects: {', '.join(available)}" if available else ""
        raise HTTPException(status_code=404, detail=f"Unknown project: {key}.{hint}")
    return db


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
    async def api_issues(db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        issues = db.list_issues(limit=10000)
        return JSONResponse([i.to_dict() for i in issues])

    @router.get("/graph")
    async def api_graph(db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Graph data: nodes (issues) + edges (dependencies) for Cytoscape.js."""
        issues = db.list_issues(limit=10000)
        deps = db.get_all_dependencies()
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

    @router.get("/stats")
    async def api_stats(db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        stats = db.get_stats()
        stats["prefix"] = db.prefix
        return JSONResponse(stats)

    @router.get("/issue/{issue_id}")
    async def api_issue_detail(issue_id: str, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
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
    async def api_dependencies(db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        deps = db.get_all_dependencies()
        return JSONResponse(deps)

    @router.get("/type/{type_name}")
    async def api_type_template(type_name: str, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
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
    async def api_issue_transitions(issue_id: str, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
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
    async def api_issue_files(issue_id: str, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Files associated with an issue."""
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        files = db.get_issue_files(issue_id)
        return JSONResponse(files)

    @router.get("/issue/{issue_id}/findings")
    async def api_issue_findings(issue_id: str, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Scan findings related to an issue."""
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        findings = db.get_issue_findings(issue_id)
        return JSONResponse([f.to_dict() for f in findings])

    @router.patch("/issue/{issue_id}")
    async def api_update_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Update issue fields (status, priority, assignee, etc.)."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_close_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Close an issue."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_reopen_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Reopen a closed issue."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
        actor = body.get("actor", "dashboard")
        try:
            issue = db.reopen_issue(issue_id, actor=actor)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "TRANSITION_ERROR", 409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/comments", status_code=201)
    async def api_add_comment(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Add a comment to an issue."""
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_search(q: str = "", limit: int = 50, offset: int = 0, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Full-text search across issues."""
        if not q.strip():
            return JSONResponse({"results": [], "total": 0})
        issues = db.search_issues(q, limit=limit, offset=offset)
        return JSONResponse({"results": [i.to_dict() for i in issues], "total": len(issues)})

    @router.get("/metrics")
    async def api_metrics(days: int = 30, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Flow metrics: cycle time, lead time, throughput."""
        from filigree.analytics import get_flow_metrics

        metrics = get_flow_metrics(db, days=days)
        return JSONResponse(metrics)

    @router.get("/critical-path")
    async def api_critical_path(db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Longest dependency chain among open issues."""
        path = db.get_critical_path()
        return JSONResponse({"path": path, "length": len(path)})

    @router.get("/activity")
    async def api_activity(limit: int = 50, since: str = "", db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Recent events across all issues."""
        events = db.get_events_since(since, limit=limit) if since else db.get_recent_events(limit=limit)
        return JSONResponse(events)

    @router.get("/plan/{milestone_id}")
    async def api_plan(milestone_id: str, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Milestone plan tree."""
        try:
            plan = db.get_plan(milestone_id)
        except KeyError:
            return _error_response(f"Issue not found: {milestone_id}", "ISSUE_NOT_FOUND", 404)
        return JSONResponse(plan)

    @router.post("/batch/update")
    async def api_batch_update(request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Batch update issues."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_batch_close(request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Batch close issues."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_types_list(db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
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
    async def api_create_issue(request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Create a new issue."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_claim_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Claim an issue."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_release_claim(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Release a claimed issue."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
        actor = body.get("actor", "dashboard")
        try:
            issue = db.release_claim(issue_id, actor=actor)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", "ISSUE_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "CLAIM_CONFLICT", 409)
        return JSONResponse(issue.to_dict())

    @router.post("/claim-next")
    async def api_claim_next(request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Claim the highest-priority ready issue."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_add_dependency(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Add a dependency: issue_id depends on depends_on."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_remove_dependency(issue_id: str, dep_id: str, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Remove a dependency."""
        try:
            removed = db.remove_dependency(issue_id, dep_id, actor="dashboard")
        except KeyError as e:
            return _error_response(str(e), "ISSUE_NOT_FOUND", 404)
        return JSONResponse({"removed": removed})

    @router.get("/files")
    async def api_list_files(request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """List tracked file records with optional filtering and pagination."""
        params = request.query_params
        limit = _safe_int(params.get("limit", "100"), "limit", 100)
        if isinstance(limit, JSONResponse):
            return limit
        offset = _safe_int(params.get("offset", "0"), "offset", 0)
        if isinstance(offset, JSONResponse):
            return offset
        min_findings = _safe_int(params.get("min_findings", "0"), "min_findings", 0)
        if isinstance(min_findings, JSONResponse):
            return min_findings
        result = db.list_files_paginated(
            limit=limit,
            offset=offset,
            language=params.get("language"),
            path_prefix=params.get("path_prefix"),
            min_findings=min_findings if min_findings > 0 else None,
            has_severity=params.get("has_severity"),
            sort=params.get("sort", "updated_at"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-cache"})

    @router.get("/files/hotspots")
    async def api_file_hotspots(request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Files ranked by weighted finding severity score."""
        params = request.query_params
        limit = _safe_int(params.get("limit", "10"), "limit", 10)
        if isinstance(limit, JSONResponse):
            return limit
        result = db.get_file_hotspots(limit=limit)
        return JSONResponse(result)

    @router.get("/files/stats")
    async def api_file_stats(db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
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
                    "path": "/api/files/_schema",
                    "description": "API discovery (this endpoint)",
                    "status": "live",
                },
            ],
        }
        return JSONResponse(schema, headers={"Cache-Control": "max-age=3600"})

    @router.get("/files/{file_id}")
    async def api_get_file(file_id: str, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Get file record with associations, recent findings, and summary."""
        try:
            data = db.get_file_detail(file_id)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        return JSONResponse(data, headers={"Cache-Control": "no-cache"})

    @router.get("/files/{file_id}/findings")
    async def api_get_file_findings(file_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Get scan findings for a file with pagination."""
        try:
            db.get_file(file_id)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        params = request.query_params
        limit = _safe_int(params.get("limit", "100"), "limit", 100)
        if isinstance(limit, JSONResponse):
            return limit
        offset = _safe_int(params.get("offset", "0"), "offset", 0)
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

    @router.get("/files/{file_id}/timeline")
    async def api_get_file_timeline(file_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Get merged timeline of events for a file."""
        params = request.query_params
        limit = _safe_int(params.get("limit", "50"), "limit", 50)
        if isinstance(limit, JSONResponse):
            return limit
        offset = _safe_int(params.get("offset", "0"), "offset", 0)
        if isinstance(offset, JSONResponse):
            return offset
        event_type = params.get("event_type")
        try:
            result = db.get_file_timeline(file_id, limit=limit, offset=offset, event_type=event_type)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        return JSONResponse(result)

    @router.post("/files/{file_id}/associations")
    async def api_add_file_association(file_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Link a file to an issue."""
        try:
            db.get_file(file_id)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
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
    async def api_scan_results(request: Request, db: FiligreeDB = Depends(_get_project_db)) -> JSONResponse:
        """Ingest scan results."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
        scan_source = body.get("scan_source", "")
        if not scan_source:
            return _error_response("scan_source is required", "VALIDATION_ERROR", 400)
        findings = body.get("findings", [])
        status_code = 202 if not findings else 200
        try:
            result = db.process_scan_results(
                scan_source=scan_source,
                findings=findings,
                scan_run_id=body.get("scan_run_id", ""),
                mark_unseen=bool(body.get("mark_unseen", False)),
            )
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse(result, status_code=status_code)

    return router


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> Any:
    """Create the FastAPI application with all dashboard endpoints."""
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    # Expose Request in module globals so PEP 563 deferred annotations resolve
    globals()["Request"] = Request

    app = FastAPI(title="Filigree Dashboard", docs_url=None, redoc_url=None)

    from fastapi.exceptions import HTTPException as FastAPIHTTPException

    @app.exception_handler(FastAPIHTTPException)
    async def http_exception_handler(request: Request, exc: FastAPIHTTPException) -> JSONResponse:
        code = "PROJECT_NOT_FOUND" if exc.status_code == 404 else "INTERNAL_ERROR"
        return _error_response(str(exc.detail), code, exc.status_code)

    router = _create_project_router()

    # Scoped: /api/p/{project_key}/issues, etc.
    app.include_router(router, prefix="/api/p/{project_key}")
    # Backward compat: /api/issues (uses default project)
    app.include_router(router, prefix="/api")

    # Root-level endpoints (not project-scoped)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (STATIC_DIR / "dashboard.html").read_text()
        return HTMLResponse(html)

    @app.get("/api/health")
    async def api_health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/projects")
    async def api_projects(ttl: float = 6.0) -> JSONResponse:
        if _project_manager is None:
            return JSONResponse([])
        projects = _project_manager.get_active_projects(ttl_hours=ttl)
        return JSONResponse([asdict(p) for p in projects])

    @app.post("/api/register")
    async def api_register(request: Request) -> JSONResponse:
        if _project_manager is None:
            return _error_response("Project manager not initialized", "INTERNAL_ERROR", 500)
        try:
            body = await request.json()
        except Exception:
            return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
        if not isinstance(body, dict):
            return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
        path = body.get("path")
        if not path or not isinstance(path, str):
            return _error_response("path is required and must be a non-empty string", "VALIDATION_ERROR", 400)
        # Canonicalize to prevent path traversal
        p = Path(path).resolve()
        if not p.is_dir():
            return _error_response(f"Directory not found: {path}", "VALIDATION_ERROR", 400)
        # Resolve: accept either .filigree/ dir or its parent project root
        if p.name != ".filigree":
            candidate = p / ".filigree"
            if candidate.is_dir():
                p = candidate
            else:
                return _error_response(
                    "Path must be a .filigree/ directory or a project root containing one",
                    "VALIDATION_ERROR",
                    400,
                )
        entry = _project_manager.register(p)
        return JSONResponse(asdict(entry))

    @app.post("/api/reload")
    async def api_reload() -> JSONResponse:
        if _project_manager is None:
            return _error_response("Project manager not initialized", "INTERNAL_ERROR", 500)
        _project_manager.close_all()
        projects = _project_manager.get_active_projects()
        errors: list[str] = []
        for proj in projects:
            try:
                _project_manager.register(Path(proj.path))
            except Exception:
                logger.warning("Failed to re-register project %s", proj.key, exc_info=True)
                errors.append(proj.key)
        return JSONResponse(
            {
                "ok": len(errors) == 0,
                "projects": len(projects) - len(errors),
                "errors": errors,
            }
        )

    # Serve static JS modules (ES modules for dashboard components)
    from starlette.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


def main(port: int = DEFAULT_PORT, *, no_browser: bool = False) -> None:
    """Start the dashboard server."""
    import threading

    import uvicorn

    global _project_manager, _default_project_key

    registry = Registry()
    _project_manager = ProjectManager(registry)

    filigree_dir = find_filigree_root()
    entry = _project_manager.register(filigree_dir)
    _default_project_key = entry.key

    app = create_app()

    if not no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    print(f"Filigree Dashboard: http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
