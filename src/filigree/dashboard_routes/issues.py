"""Issue, workflow, and dependency route handlers."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from filigree.core import FiligreeDB
from filigree.dashboard_routes.common import (
    _error_response,
    _parse_json_body,
)

# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_router() -> Any:
    """Build the APIRouter for issue, workflow, and dependency endpoints.

    NOTE: All handlers are intentionally async despite doing synchronous
    SQLite I/O. This serializes DB access on the event loop thread,
    avoiding concurrent multi-thread access to the shared DB connection.
    """
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse

    from filigree.dashboard import _get_db

    router = APIRouter()

    @router.get("/issues")
    async def api_issues(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        issues = db.list_issues(limit=10000)
        return JSONResponse([i.to_dict() for i in issues])

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
        """Workflow template for a given issue type."""
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

    return router
