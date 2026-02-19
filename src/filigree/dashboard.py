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

import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request

from filigree.core import FiligreeDB, find_filigree_root
from filigree.registry import ProjectManager, Registry

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8377

# ---------------------------------------------------------------------------
# Module-level state — set by main() or test fixtures
# ---------------------------------------------------------------------------

_project_manager: ProjectManager | None = None
_default_project_key: str = ""


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

        raise HTTPException(status_code=404, detail=f"Unknown project: {key}")
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
    async def api_issue_detail(
        issue_id: str, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Full issue detail with dependency details, events, and comments."""
        try:
            issue = db.get_issue(issue_id)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)

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
            "SELECT event_type, actor, old_value, new_value, created_at "
            "FROM events WHERE issue_id = ? ORDER BY created_at DESC LIMIT 20",
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
    async def api_type_template(
        type_name: str, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Workflow template for a given issue type (WFT-FR-065)."""
        tpl = db.templates.get_type(type_name)
        if tpl is None:
            return JSONResponse({"error": f"Unknown type: {type_name}"}, status_code=404)
        return JSONResponse(
            {
                "type": tpl.type,
                "display_name": tpl.display_name,
                "states": [{"name": s.name, "category": s.category} for s in tpl.states],
                "initial_state": tpl.initial_state,
                "transitions": [
                    {"from": t.from_state, "to": t.to_state, "enforcement": t.enforcement}
                    for t in tpl.transitions
                ],
            }
        )

    @router.get("/issue/{issue_id}/transitions")
    async def api_issue_transitions(
        issue_id: str, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Valid next states for an issue."""
        try:
            transitions = db.get_valid_transitions(issue_id)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
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

    @router.patch("/issue/{issue_id}")
    async def api_update_issue(
        issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Update issue fields (status, priority, assignee, etc.)."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        actor = body.pop("actor", "dashboard")
        priority = body.get("priority")
        if priority is not None and not isinstance(priority, int):
            return JSONResponse({"error": "priority must be an integer"}, status_code=400)
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
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/close")
    async def api_close_issue(
        issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Close an issue."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        actor = body.get("actor", "dashboard")
        reason = body.get("reason", "")
        fields = body.get("fields")
        try:
            issue = db.close_issue(issue_id, reason=reason, actor=actor, fields=fields)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except TypeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/reopen")
    async def api_reopen_issue(
        issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Reopen a closed issue."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        actor = body.get("actor", "dashboard")
        try:
            issue = db.reopen_issue(issue_id, actor=actor)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/comments", status_code=201)
    async def api_add_comment(
        issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Add a comment to an issue."""
        try:
            db.get_issue(issue_id)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        text = body.get("text", "")
        author = body.get("author", "")
        try:
            comment_id = db.add_comment(issue_id, text, author=author)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
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
    async def api_search(
        q: str = "", limit: int = 50, offset: int = 0, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Full-text search across issues."""
        if not q.strip():
            return JSONResponse({"results": [], "total": 0})
        issues = db.search_issues(q, limit=limit, offset=offset)
        return JSONResponse({"results": [i.to_dict() for i in issues], "total": len(issues)})

    @router.get("/metrics")
    async def api_metrics(
        days: int = 30, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
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
    async def api_activity(
        limit: int = 50, since: str = "", db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Recent events across all issues."""
        events = db.get_events_since(since, limit=limit) if since else db.get_recent_events(limit=limit)
        return JSONResponse(events)

    @router.get("/plan/{milestone_id}")
    async def api_plan(
        milestone_id: str, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Milestone plan tree."""
        try:
            plan = db.get_plan(milestone_id)
        except KeyError:
            return JSONResponse({"error": f"Not found: {milestone_id}"}, status_code=404)
        return JSONResponse(plan)

    @router.post("/batch/update")
    async def api_batch_update(
        request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Batch update issues."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        issue_ids = body.get("issue_ids")
        if not isinstance(issue_ids, list):
            return JSONResponse({"error": "issue_ids must be a JSON array"}, status_code=400)
        if not all(isinstance(i, str) for i in issue_ids):
            return JSONResponse({"error": "All issue_ids must be strings"}, status_code=400)
        actor = body.get("actor", "dashboard")
        priority = body.get("priority")
        if priority is not None and not isinstance(priority, int):
            return JSONResponse({"error": "priority must be an integer"}, status_code=400)
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
    async def api_batch_close(
        request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Batch close issues."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        issue_ids = body.get("issue_ids")
        if not isinstance(issue_ids, list):
            return JSONResponse({"error": "issue_ids must be a JSON array"}, status_code=400)
        if not all(isinstance(i, str) for i in issue_ids):
            return JSONResponse({"error": "All issue_ids must be strings"}, status_code=400)
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
    async def api_create_issue(
        request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Create a new issue."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        title = body.get("title", "")
        priority = body.get("priority", 2)
        if not isinstance(priority, int):
            return JSONResponse({"error": "priority must be an integer"}, status_code=400)
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
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(issue.to_dict(), status_code=201)

    @router.post("/issue/{issue_id}/claim")
    async def api_claim_issue(
        issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Claim an issue."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        assignee = body.get("assignee", "")
        if not assignee or not assignee.strip():
            return JSONResponse(
                {"error": "assignee is required and cannot be empty"}, status_code=400
            )
        actor = body.get("actor", "dashboard")
        try:
            issue = db.claim_issue(issue_id, assignee=assignee, actor=actor)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/release")
    async def api_release_claim(
        issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Release a claimed issue."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        actor = body.get("actor", "dashboard")
        try:
            issue = db.release_claim(issue_id, actor=actor)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @router.post("/claim-next")
    async def api_claim_next(
        request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Claim the highest-priority ready issue."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        assignee = body.get("assignee", "")
        if not assignee or not assignee.strip():
            return JSONResponse(
                {"error": "assignee is required and cannot be empty"}, status_code=400
            )
        actor = body.get("actor", "dashboard")
        try:
            issue = db.claim_next(assignee, actor=actor)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        if issue is None:
            return JSONResponse({"error": "No ready issues to claim"}, status_code=404)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/dependencies")
    async def api_add_dependency(
        issue_id: str, request: Request, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Add a dependency: issue_id depends on depends_on."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        depends_on = body.get("depends_on", "")
        actor = body.get("actor", "dashboard")
        try:
            added = db.add_dependency(issue_id, depends_on, actor=actor)
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse({"added": added})

    @router.delete("/issue/{issue_id}/dependencies/{dep_id}")
    async def api_remove_dependency(
        issue_id: str, dep_id: str, db: FiligreeDB = Depends(_get_project_db)
    ) -> JSONResponse:
        """Remove a dependency."""
        try:
            removed = db.remove_dependency(issue_id, dep_id, actor="dashboard")
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        return JSONResponse({"removed": removed})

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
            return JSONResponse({"error": "Project manager not initialized"}, status_code=500)
        body = await request.json()
        path = body.get("path")
        if not path or not Path(path).is_dir():
            return JSONResponse({"error": "Invalid path"}, status_code=400)
        entry = _project_manager.register(Path(path))
        return JSONResponse(asdict(entry))

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
