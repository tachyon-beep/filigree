"""Issue, workflow, and dependency route handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from starlette.requests import Request

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

from filigree.core import FiligreeDB
from filigree.dashboard_routes.common import (
    _error_response,
    _parse_json_body,
    _validate_actor,
    _validate_priority_field,
)
from filigree.models import Issue
from filigree.types.api import (
    DepDetail,
    EnrichedIssueDetail,
    ErrorCode,
    IssueDetailEvent,
    classify_value_error,
    errorcode_to_http_status,
)
from filigree.types.core import ISOTimestamp
from filigree.types.planning import CommentRecord

logger = logging.getLogger(__name__)

# Page size used when streaming every issue into the dashboard preload.
# Exposed at module scope so tests can shrink it to exercise pagination.
_ISSUES_LIST_PAGE_SIZE = 1000
_MISSING = object()


def _fetch_all_issues(db: FiligreeDB) -> list[Issue]:
    """Return every issue in the DB by paginating list_issues.

    The dashboard preload previously called ``list_issues(limit=10000)``,
    which silently truncated large projects. Pagination removes that hidden
    ceiling while preserving the response shape.
    """
    all_issues: list[Issue] = []
    offset = 0
    while True:
        page = db.list_issues(limit=_ISSUES_LIST_PAGE_SIZE, offset=offset)
        all_issues.extend(page)
        if len(page) < _ISSUES_LIST_PAGE_SIZE:
            break
        offset += _ISSUES_LIST_PAGE_SIZE
    return all_issues


def _validate_body_string_field(
    body: dict[str, object],
    field: str,
    *,
    allow_null: bool = False,
    default: str | None = None,
) -> str | None | JSONResponse:
    """Validate a JSON body field expected to be a string.

    Missing fields fall back to ``default`` so callers can preserve existing
    create/patch semantics. Present fields must be strings, except when
    ``allow_null`` is set, in which case explicit ``null`` is accepted.
    """
    value = body.get(field, _MISSING)
    if value is _MISSING:
        return default
    if value is None:
        if allow_null:
            return None
        return _error_response(f"{field} must be a string", ErrorCode.VALIDATION, 400)
    if not isinstance(value, str):
        suffix = " or null" if allow_null else ""
        return _error_response(f"{field} must be a string{suffix}", ErrorCode.VALIDATION, 400)
    return value


def _parse_batch_update_body(body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    """Validate the batch-update request body.

    Shared by classic ``POST /api/batch/update`` and loom
    ``POST /api/loom/batch/update``; both generations accept the same
    request shape (``issue_ids``, ``status``, ``priority``, ``assignee``,
    ``fields``, ``actor``). Returns the kwargs dict for
    ``db.batch_update`` on success, or a 400 ``JSONResponse`` on error.
    """
    issue_ids = body.get("issue_ids")
    if not isinstance(issue_ids, list):
        return _error_response("issue_ids must be a JSON array", ErrorCode.VALIDATION, 400)
    if not all(isinstance(i, str) for i in issue_ids):
        return _error_response("All issue_ids must be strings", ErrorCode.VALIDATION, 400)
    actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
    if actor_err:
        return actor_err
    priority = _validate_priority_field(body)
    if not isinstance(priority, int) and priority is not None:
        return priority  # JSONResponse error
    return {
        "issue_ids": issue_ids,
        "status": body.get("status"),
        "priority": priority,
        "assignee": body.get("assignee"),
        "fields": body.get("fields"),
        "actor": actor,
    }


def _parse_batch_close_body(body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    """Validate the batch-close request body.

    Shared by classic ``POST /api/batch/close`` and loom
    ``POST /api/loom/batch/close``. Returns kwargs for ``db.batch_close``
    on success, or a 400 ``JSONResponse`` on error.
    """
    issue_ids = body.get("issue_ids")
    if not isinstance(issue_ids, list):
        return _error_response("issue_ids must be a JSON array", ErrorCode.VALIDATION, 400)
    if not all(isinstance(i, str) for i in issue_ids):
        return _error_response("All issue_ids must be strings", ErrorCode.VALIDATION, 400)
    reason = body.get("reason", "")
    actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
    if actor_err:
        return actor_err
    return {"issue_ids": issue_ids, "reason": reason, "actor": actor}


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_classic_router() -> APIRouter:
    """Build the classic-generation APIRouter for issue, workflow, and
    dependency endpoints.

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
        issues = _fetch_all_issues(db)
        return JSONResponse([i.to_dict() for i in issues])

    @router.get("/ready")
    async def api_ready(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Issues with no open blockers, sorted by priority."""
        issues = db.get_ready()
        return JSONResponse([i.to_dict() for i in issues])

    @router.get("/issue/{issue_id}")
    async def api_issue_detail(issue_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Full issue detail with dependency details, events, and comments."""
        try:
            issue = db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)

        # Resolve dep details for blocks and blocked_by in a single query
        dep_ids = list(set(issue.blocks + issue.blocked_by))
        dep_details: dict[str, DepDetail] = {}
        if dep_ids:
            placeholders = ",".join("?" * len(dep_ids))
            rows = db.conn.execute(
                f"SELECT id, title, status, type, priority FROM issues WHERE id IN ({placeholders})",
                dep_ids,
            ).fetchall()
            found = {r["id"]: r for r in rows}
            for did in dep_ids:
                if did in found:
                    r = found[did]
                    dep_details[did] = DepDetail(
                        title=r["title"],
                        status=r["status"],
                        status_category=db._resolve_status_category(r["type"], r["status"]),
                        priority=r["priority"],
                    )
                else:
                    logger.warning("Dangling dependency reference %s in issue %s", did, issue_id)
                    dep_details[did] = DepDetail(
                        title=f"[Deleted: {did}]",
                        status="deleted",
                        status_category="done",
                        priority=4,
                    )

        # Events — NOTE: SQL column list must stay in sync with IssueDetailEvent fields.
        # IssueDetailEvent is a slim 5-column projection — NOT full EventRecord.
        events = db.conn.execute(
            "SELECT event_type, actor, old_value, new_value, created_at FROM events WHERE issue_id = ? ORDER BY created_at DESC LIMIT 20",
            (issue_id,),
        ).fetchall()
        event_list: list[IssueDetailEvent] = [
            IssueDetailEvent(
                event_type=e["event_type"],
                actor=e["actor"],
                old_value=e["old_value"],
                new_value=e["new_value"],
                created_at=e["created_at"],
            )
            for e in events
        ]

        result = EnrichedIssueDetail(
            **issue.to_dict(),
            dep_details=dep_details,
            events=event_list,
            comments=db.get_comments(issue_id),
        )
        return JSONResponse(result)

    @router.get("/dependencies")
    async def api_dependencies(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        deps = db.get_all_dependencies()
        return JSONResponse(deps)

    @router.get("/type/{type_name}")
    async def api_type_template(type_name: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Workflow template for a given issue type."""
        tpl = db.templates.get_type(type_name)
        if tpl is None:
            # Unknown type_name is input validation (rejected-enum-value),
            # not resource-lookup, so VALIDATION + 400 matches the pattern
            # used everywhere else in this file for rejected enum values.
            valid_types = [t.type for t in db.templates.list_types()]
            return _error_response(
                f'Unknown type "{type_name}". Valid types: {", ".join(valid_types)}',
                ErrorCode.VALIDATION,
                400,
                {"param": "type_name", "value": type_name, "valid_types": valid_types},
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
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
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
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        files = db.get_issue_files(issue_id)
        return JSONResponse(files)

    @router.get("/issue/{issue_id}/findings")
    async def api_issue_findings(issue_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Scan findings related to an issue."""
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        findings = db.get_issue_findings(issue_id)
        return JSONResponse([f.to_dict() for f in findings])

    @router.patch("/issue/{issue_id}")
    async def api_update_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Update issue fields (status, priority, assignee, etc.)."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
        if actor_err:
            return actor_err
        body.pop("actor", None)
        priority = _validate_priority_field(body)
        if isinstance(priority, JSONResponse):
            return priority
        title = _validate_body_string_field(body, "title", default=None)
        if isinstance(title, JSONResponse):
            return title
        description = _validate_body_string_field(body, "description", default=None)
        if isinstance(description, JSONResponse):
            return description
        notes = _validate_body_string_field(body, "notes", default=None)
        if isinstance(notes, JSONResponse):
            return notes
        parent_id = _validate_body_string_field(body, "parent_id", allow_null=True, default=None)
        if isinstance(parent_id, JSONResponse):
            return parent_id
        try:
            issue = db.update_issue(
                issue_id,
                status=body.get("status"),
                priority=priority,
                assignee=body.get("assignee"),
                title=title,
                description=description,
                notes=notes,
                parent_id=parent_id,
                fields=body.get("fields"),
                actor=actor,
            )
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        except TypeError as e:
            return _error_response(str(e), ErrorCode.VALIDATION, 400)
        except ValueError as e:
            code = classify_value_error(str(e))
            return _error_response(str(e), code, errorcode_to_http_status(code))
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/close")
    async def api_close_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Close an issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
        if actor_err:
            return actor_err
        reason = body.get("reason", "")
        fields = body.get("fields")
        try:
            issue = db.close_issue(issue_id, reason=reason, actor=actor, fields=fields)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        except TypeError as e:
            return _error_response(str(e), ErrorCode.VALIDATION, 400)
        except ValueError as e:
            code = classify_value_error(str(e))
            return _error_response(str(e), code, errorcode_to_http_status(code))
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/reopen")
    async def api_reopen_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Reopen a closed issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
        if actor_err:
            return actor_err
        try:
            issue = db.reopen_issue(issue_id, actor=actor)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        except ValueError as e:
            code = classify_value_error(str(e))
            return _error_response(str(e), code, errorcode_to_http_status(code))
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/comments", status_code=201)
    async def api_add_comment(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Add a comment to an issue."""
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        text = body.get("text", "")
        if not isinstance(text, str):
            return _error_response("text must be a string", ErrorCode.VALIDATION, 400)
        author, author_err = _validate_actor(body.get("author", "dashboard"))
        if author_err:
            return author_err
        try:
            comment_id = db.add_comment(issue_id, text, author=author)
        except ValueError as e:
            return _error_response(str(e), ErrorCode.VALIDATION, 400)
        # Fetch just the single comment to get the real created_at timestamp
        row = db.conn.execute("SELECT created_at FROM comments WHERE id = ?", (comment_id,)).fetchone()
        if row is None:
            logger = logging.getLogger(__name__)
            logger.error("Comment %d not found immediately after INSERT for issue %s", comment_id, issue_id)
            return _error_response("Internal error: comment created but not retrievable", ErrorCode.INTERNAL, 500)
        created_at = ISOTimestamp(row["created_at"])
        return JSONResponse(
            CommentRecord(id=comment_id, author=author, text=text, created_at=created_at),
            status_code=201,
        )

    @router.get("/search")
    async def api_search(q: str = "", limit: int = 50, offset: int = 0, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Full-text search across issues."""
        limit = min(max(limit, 1), 1000)
        offset = max(offset, 0)
        if not q.strip():
            return JSONResponse({"results": [], "total": 0})
        total = db.count_search_results(q)
        page = db.search_issues(q, limit=limit, offset=offset)
        return JSONResponse({"results": [i.to_dict() for i in page], "total": total})

    @router.get("/plan/{milestone_id}")
    async def api_plan(milestone_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Milestone plan tree."""
        try:
            plan = db.get_plan(milestone_id)
        except KeyError:
            return _error_response(f"Issue not found: {milestone_id}", ErrorCode.NOT_FOUND, 404)
        return JSONResponse(plan)

    @router.post("/batch/update")
    async def api_batch_update(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Batch update issues."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        parsed = _parse_batch_update_body(body)
        if isinstance(parsed, JSONResponse):
            return parsed
        issue_ids = parsed.pop("issue_ids")
        try:
            updated, errors = db.batch_update(issue_ids, **parsed)
        except TypeError as e:
            return _error_response(str(e), ErrorCode.VALIDATION, 400)
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
        parsed = _parse_batch_close_body(body)
        if isinstance(parsed, JSONResponse):
            return parsed
        issue_ids = parsed.pop("issue_ids")
        closed, errors = db.batch_close(issue_ids, **parsed)
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
        if not isinstance(title, str):
            return _error_response("title must be a string", ErrorCode.VALIDATION, 400)
        actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
        if actor_err:
            return actor_err
        priority = _validate_priority_field(body, default=2)
        if isinstance(priority, JSONResponse):
            return priority
        if priority is None:
            priority = 2
        parent_id = _validate_body_string_field(body, "parent_id", allow_null=True, default=None)
        if isinstance(parent_id, JSONResponse):
            return parent_id
        description = _validate_body_string_field(body, "description", default="")
        if isinstance(description, JSONResponse):
            return description
        if description is None:
            description = ""
        notes = _validate_body_string_field(body, "notes", default="")
        if isinstance(notes, JSONResponse):
            return notes
        if notes is None:
            notes = ""
        try:
            issue = db.create_issue(
                title,
                type=body.get("type", "task"),
                priority=priority,
                parent_id=parent_id,
                assignee=body.get("assignee", ""),
                description=description,
                notes=notes,
                fields=body.get("fields"),
                labels=body.get("labels"),
                deps=body.get("deps"),
                actor=actor,
            )
        except TypeError as e:
            return _error_response(str(e), ErrorCode.VALIDATION, 400)
        except ValueError as e:
            return _error_response(str(e), ErrorCode.VALIDATION, 400)
        return JSONResponse(issue.to_dict(), status_code=201)

    @router.post("/issue/{issue_id}/claim")
    async def api_claim_issue(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Claim an issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        assignee = body.get("assignee", "")
        if not isinstance(assignee, str):
            return _error_response("assignee must be a string", ErrorCode.VALIDATION, 400)
        if not assignee or not assignee.strip():
            return _error_response("assignee is required and cannot be empty", ErrorCode.VALIDATION, 400)
        actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
        if actor_err:
            return actor_err
        try:
            issue = db.claim_issue(issue_id, assignee=assignee, actor=actor)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        except ValueError as e:
            return _error_response(str(e), ErrorCode.CONFLICT, 409)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/release")
    async def api_release_claim(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Release a claimed issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
        if actor_err:
            return actor_err
        try:
            issue = db.release_claim(issue_id, actor=actor)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        except ValueError as e:
            return _error_response(str(e), ErrorCode.CONFLICT, 409)
        return JSONResponse(issue.to_dict())

    @router.post("/claim-next")
    async def api_claim_next(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Claim the highest-priority ready issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        assignee = body.get("assignee", "")
        if not isinstance(assignee, str):
            return _error_response("assignee must be a string", ErrorCode.VALIDATION, 400)
        if not assignee or not assignee.strip():
            return _error_response("assignee is required and cannot be empty", ErrorCode.VALIDATION, 400)
        actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
        if actor_err:
            return actor_err
        try:
            issue = db.claim_next(assignee, actor=actor)
        except ValueError as e:
            return _error_response(str(e), ErrorCode.CONFLICT, 409)
        if issue is None:
            return _error_response("No ready issues to claim", ErrorCode.NOT_FOUND, 404)
        return JSONResponse(issue.to_dict())

    @router.post("/issue/{issue_id}/dependencies")
    async def api_add_dependency(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Add a dependency: issue_id depends on depends_on."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        depends_on = body.get("depends_on", "")
        if not depends_on or not isinstance(depends_on, str) or not depends_on.strip():
            return _error_response("depends_on is required and must be a non-empty string", ErrorCode.VALIDATION, 400)
        actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
        if actor_err:
            return actor_err
        try:
            added = db.add_dependency(issue_id, depends_on, actor=actor)
        except KeyError as e:
            return _error_response(str(e), ErrorCode.NOT_FOUND, 404)
        except ValueError as e:
            return _error_response(str(e), ErrorCode.CONFLICT, 409)
        return JSONResponse({"added": added})

    @router.delete("/issue/{issue_id}/dependencies/{dep_id}")
    async def api_remove_dependency(
        issue_id: str, dep_id: str, actor: str = "dashboard", db: FiligreeDB = Depends(_get_db)
    ) -> JSONResponse:
        """Remove a dependency."""
        clean_actor, actor_err = _validate_actor(actor)
        if actor_err:
            return actor_err
        try:
            removed = db.remove_dependency(issue_id, dep_id, actor=clean_actor)
        except KeyError as e:
            return _error_response(str(e), ErrorCode.NOT_FOUND, 404)
        return JSONResponse({"removed": removed})

    return router


def create_loom_router() -> APIRouter:
    """Build the loom-generation APIRouter for issue, workflow, and
    dependency endpoints.

    Phase C2 mounts the batch endpoints (``/batch/update``,
    ``/batch/close``); subsequent C tasks add the rest of the loom
    issue surface. See ADR-002 for the generation framing and
    ``tests/fixtures/contracts/loom/`` for the response-shape pins.
    """
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse

    from filigree.dashboard import _get_db
    from filigree.generations.loom.adapters import slim_issue_to_loom

    router = APIRouter()

    @router.post("/batch/update")
    async def api_loom_batch_update(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Batch update issues — loom envelope (BatchResponse[SlimIssueLoom])."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        parsed = _parse_batch_update_body(body)
        if isinstance(parsed, JSONResponse):
            return parsed
        issue_ids = parsed.pop("issue_ids")
        try:
            updated, errors = db.batch_update(issue_ids, **parsed)
        except TypeError as e:
            return _error_response(str(e), ErrorCode.VALIDATION, 400)
        return JSONResponse(
            {
                "succeeded": [slim_issue_to_loom(i) for i in updated],
                "failed": errors,
            }
        )

    @router.post("/batch/close")
    async def api_loom_batch_close(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Batch close issues — loom envelope (BatchCloseResponseLoom).

        Includes ``newly_unblocked`` (omitted when empty) computed the
        same way MCP ``batch_close`` does it: diff ``get_ready()``
        before vs. after. Classic ``/api/batch/close`` does not surface
        ``newly_unblocked`` and stays unchanged.
        """
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        parsed = _parse_batch_close_body(body)
        if isinstance(parsed, JSONResponse):
            return parsed
        issue_ids = parsed.pop("issue_ids")
        ready_before = {i.id for i in db.get_ready()}
        closed, errors = db.batch_close(issue_ids, **parsed)
        ready_after = db.get_ready()
        newly_unblocked = [i for i in ready_after if i.id not in ready_before]
        response: dict[str, Any] = {
            "succeeded": [slim_issue_to_loom(i) for i in closed],
            "failed": errors,
        }
        if newly_unblocked:
            response["newly_unblocked"] = [slim_issue_to_loom(i) for i in newly_unblocked]
        return JSONResponse(response)

    return router
