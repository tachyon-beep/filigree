"""Web dashboard for filigree — interactive project management UI.

Full-featured local web server: kanban board, dependency graph, metrics,
activity feed, workflow visualization. Supports issue management (create,
update, close, reopen, claim, dependency management), batch operations,
and real-time auto-refresh.

Usage:
    filigree dashboard                    # Opens browser at localhost:8377
    filigree dashboard --port 9000        # Custom port
    filigree dashboard --no-browser       # Skip auto-open
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request

from filigree.core import DB_FILENAME, FiligreeDB, find_filigree_root, read_config

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8377

_db: FiligreeDB | None = None
_prefix: str = "filigree"


def _get_db() -> FiligreeDB:
    if _db is None:
        msg = "Database not initialized"
        raise RuntimeError(msg)
    return _db


def create_app() -> Any:
    """Create the FastAPI application with all dashboard endpoints."""
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    # Expose Request in module globals so PEP 563 deferred annotations resolve
    globals()["Request"] = Request

    app = FastAPI(title="Filigree Dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (STATIC_DIR / "dashboard.html").read_text()
        return HTMLResponse(html)

    @app.get("/api/issues")
    async def api_issues() -> JSONResponse:
        db = _get_db()
        issues = db.list_issues(limit=10000)
        return JSONResponse([i.to_dict() for i in issues])

    @app.get("/api/graph")
    async def api_graph() -> JSONResponse:
        """Graph data: nodes (issues) + edges (dependencies) for Cytoscape.js."""
        db = _get_db()
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

    @app.get("/api/stats")
    async def api_stats() -> JSONResponse:
        db = _get_db()
        stats = db.get_stats()
        stats["prefix"] = _prefix
        return JSONResponse(stats)

    @app.get("/api/issue/{issue_id}")
    async def api_issue_detail(issue_id: str) -> JSONResponse:
        """Full issue detail with dependency details, events, and comments."""
        db = _get_db()
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
                dep_details[did] = {"title": did, "status": "unknown", "status_category": "open", "priority": 2}
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

    @app.get("/api/dependencies")
    async def api_dependencies() -> JSONResponse:
        db = _get_db()
        deps = db.get_all_dependencies()
        return JSONResponse(deps)

    @app.get("/api/type/{type_name}")
    async def api_type_template(type_name: str) -> JSONResponse:
        """Workflow template for a given issue type (WFT-FR-065)."""
        db = _get_db()
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
                    {"from": t.from_state, "to": t.to_state, "enforcement": t.enforcement} for t in tpl.transitions
                ],
            }
        )

    @app.get("/api/issue/{issue_id}/transitions")
    async def api_issue_transitions(issue_id: str) -> JSONResponse:
        """Valid next states for an issue."""
        db = _get_db()
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

    @app.patch("/api/issue/{issue_id}")
    async def api_update_issue(issue_id: str, request: Request) -> JSONResponse:
        """Update issue fields (status, priority, assignee, etc.)."""
        db = _get_db()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        actor = body.pop("actor", "dashboard")
        try:
            issue = db.update_issue(
                issue_id,
                status=body.get("status"),
                priority=body.get("priority"),
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

    @app.post("/api/issue/{issue_id}/close")
    async def api_close_issue(issue_id: str, request: Request) -> JSONResponse:
        """Close an issue."""
        db = _get_db()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        actor = body.get("actor", "dashboard")
        reason = body.get("reason", "")
        try:
            issue = db.close_issue(issue_id, reason=reason, actor=actor)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @app.post("/api/issue/{issue_id}/reopen")
    async def api_reopen_issue(issue_id: str, request: Request) -> JSONResponse:
        """Reopen a closed issue."""
        db = _get_db()
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

    @app.post("/api/issue/{issue_id}/comments", status_code=201)
    async def api_add_comment(issue_id: str, request: Request) -> JSONResponse:
        """Add a comment to an issue."""
        db = _get_db()
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

    @app.get("/api/search")
    async def api_search(q: str = "", limit: int = 50, offset: int = 0) -> JSONResponse:
        """Full-text search across issues."""
        if not q.strip():
            return JSONResponse({"results": [], "total": 0})
        db = _get_db()
        issues = db.search_issues(q, limit=limit, offset=offset)
        return JSONResponse({"results": [i.to_dict() for i in issues], "total": len(issues)})

    @app.get("/api/metrics")
    async def api_metrics(days: int = 30) -> JSONResponse:
        """Flow metrics: cycle time, lead time, throughput."""
        from filigree.analytics import get_flow_metrics

        db = _get_db()
        metrics = get_flow_metrics(db, days=days)
        return JSONResponse(metrics)

    @app.get("/api/critical-path")
    async def api_critical_path() -> JSONResponse:
        """Longest dependency chain among open issues."""
        db = _get_db()
        path = db.get_critical_path()
        return JSONResponse({"path": path, "length": len(path)})

    @app.get("/api/activity")
    async def api_activity(limit: int = 50, since: str = "") -> JSONResponse:
        """Recent events across all issues."""
        db = _get_db()
        events = db.get_events_since(since, limit=limit) if since else db.get_recent_events(limit=limit)
        return JSONResponse(events)

    @app.get("/api/plan/{milestone_id}")
    async def api_plan(milestone_id: str) -> JSONResponse:
        """Milestone plan tree."""
        db = _get_db()
        try:
            plan = db.get_plan(milestone_id)
        except KeyError:
            return JSONResponse({"error": f"Not found: {milestone_id}"}, status_code=404)
        return JSONResponse(plan)

    @app.post("/api/batch/update")
    async def api_batch_update(request: Request) -> JSONResponse:
        """Batch update issues."""
        db = _get_db()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        issue_ids = body.get("issue_ids")
        if not isinstance(issue_ids, list):
            return JSONResponse({"error": "issue_ids must be a JSON array"}, status_code=400)
        actor = body.get("actor", "dashboard")
        updated, errors = db.batch_update(
            issue_ids,
            status=body.get("status"),
            priority=body.get("priority"),
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

    @app.post("/api/batch/close")
    async def api_batch_close(request: Request) -> JSONResponse:
        """Batch close issues."""
        db = _get_db()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        issue_ids = body.get("issue_ids")
        if not isinstance(issue_ids, list):
            return JSONResponse({"error": "issue_ids must be a JSON array"}, status_code=400)
        reason = body.get("reason", "")
        actor = body.get("actor", "dashboard")
        closed, errors = db.batch_close(issue_ids, reason=reason, actor=actor)
        return JSONResponse(
            {
                "closed": [i.to_dict() for i in closed],
                "errors": errors,
            }
        )

    @app.get("/api/types")
    async def api_types_list() -> JSONResponse:
        """List all registered issue types."""
        db = _get_db()
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

    @app.post("/api/issues", status_code=201)
    async def api_create_issue(request: Request) -> JSONResponse:
        """Create a new issue."""
        db = _get_db()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        title = body.get("title", "")
        try:
            issue = db.create_issue(
                title,
                type=body.get("type", "task"),
                priority=body.get("priority", 2),
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

    @app.post("/api/issue/{issue_id}/claim")
    async def api_claim_issue(issue_id: str, request: Request) -> JSONResponse:
        """Claim an issue."""
        db = _get_db()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        assignee = body.get("assignee", "")
        if not assignee or not assignee.strip():
            return JSONResponse({"error": "assignee is required and cannot be empty"}, status_code=400)
        actor = body.get("actor", "dashboard")
        try:
            issue = db.claim_issue(issue_id, assignee=assignee, actor=actor)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @app.post("/api/issue/{issue_id}/release")
    async def api_release_claim(issue_id: str, request: Request) -> JSONResponse:
        """Release a claimed issue."""
        db = _get_db()
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

    @app.post("/api/claim-next")
    async def api_claim_next(request: Request) -> JSONResponse:
        """Claim the highest-priority ready issue."""
        db = _get_db()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
        assignee = body.get("assignee", "")
        if not assignee or not assignee.strip():
            return JSONResponse({"error": "assignee is required and cannot be empty"}, status_code=400)
        actor = body.get("actor", "dashboard")
        try:
            issue = db.claim_next(assignee, actor=actor)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        if issue is None:
            return JSONResponse({"error": "No ready issues to claim"}, status_code=404)
        return JSONResponse(issue.to_dict())

    @app.post("/api/issue/{issue_id}/dependencies")
    async def api_add_dependency(issue_id: str, request: Request) -> JSONResponse:
        """Add a dependency: issue_id depends on depends_on."""
        db = _get_db()
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

    @app.delete("/api/issue/{issue_id}/dependencies/{dep_id}")
    async def api_remove_dependency(issue_id: str, dep_id: str) -> JSONResponse:
        """Remove a dependency."""
        db = _get_db()
        try:
            removed = db.remove_dependency(issue_id, dep_id)
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        return JSONResponse({"removed": removed})

    return app


def main(port: int = DEFAULT_PORT, *, no_browser: bool = False) -> None:
    """Start the dashboard server."""
    import threading

    import uvicorn

    global _db, _prefix

    filigree_dir = find_filigree_root()
    config = read_config(filigree_dir)
    _prefix = config.get("prefix", "filigree")
    _db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=_prefix, check_same_thread=False)
    _db.initialize()

    app = create_app()

    if not no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    print(f"Filigree Dashboard: http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
