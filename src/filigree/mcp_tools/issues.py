"""MCP tools for issue CRUD, search, claim, and batch operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.types import TextContent, Tool

from filigree.mcp_tools.common import (
    _MAX_LIST_RESULTS,
    _apply_has_more,
    _build_transition_error,
    _resolve_pagination,
    _slim_issue,
    _text,
    _validate_actor,
    _validate_int_range,
)
from filigree.types.api import (
    BatchCloseResponse,
    BatchUpdateResponse,
    ClaimNextResponse,
    ErrorResponse,
    IssueListResponse,
    IssueWithChangedFields,
    IssueWithTransitions,
    SearchResponse,
)


def register() -> tuple[list[Tool], dict[str, Callable[..., Any]]]:
    """Return (tool_definitions, handler_map) for issue-domain tools."""
    tools = [
        Tool(
            name="get_issue",
            description="Get full details of an issue including deps, labels, children, ready status. Set include_transitions=true for valid next states.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Issue ID"},
                    "include_transitions": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include valid_transitions in response (saves a separate call)",
                    },
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="list_issues",
            description="List issues with optional filters. Use status_category for template-aware filtering.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by exact status name (use get_valid_transitions for allowed values)",
                    },
                    "status_category": {
                        "type": "string",
                        "enum": ["open", "wip", "done"],
                        "description": "Filter by status category (expands to all matching states)",
                    },
                    "type": {
                        "type": "string",
                        "description": "Filter by type (use list_types for available types)",
                    },
                    "priority": {"type": "integer", "minimum": 0, "maximum": 4, "description": "Filter by priority"},
                    "parent_id": {"type": "string", "description": "Filter by parent issue ID"},
                    "assignee": {"type": "string", "description": "Filter by assignee"},
                    "label": {"type": "string", "description": "Filter by label"},
                    "limit": {
                        "type": "integer",
                        "default": 100,
                        "minimum": 1,
                        "description": f"Max results (default 100, capped at {_MAX_LIST_RESULTS} unless no_limit=true)",
                    },
                    "offset": {"type": "integer", "default": 0, "minimum": 0, "description": "Skip first N results"},
                    "no_limit": {
                        "type": "boolean",
                        "default": False,
                        "description": f"Bypass the default result cap of {_MAX_LIST_RESULTS}. Use with caution on large projects.",
                    },
                },
            },
        ),
        Tool(
            name="create_issue",
            description=(
                "Create a new issue. You can set labels at creation time via labels=[...]. "
                "Use get_template first to see available fields for the type."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Issue title"},
                    "type": {"type": "string", "default": "task", "description": "Issue type"},
                    "priority": {
                        "type": "integer",
                        "default": 2,
                        "minimum": 0,
                        "maximum": 4,
                        "description": "Priority 0-4 (0=critical)",
                    },
                    "parent_id": {"type": "string", "description": "Parent issue ID (for hierarchy)"},
                    "description": {"type": "string", "description": "Issue description"},
                    "notes": {"type": "string", "description": "Additional notes"},
                    "fields": {"type": "object", "description": "Custom fields (from template schema)"},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Labels to attach during creation (avoids a follow-up add_label call)",
                    },
                    "deps": {"type": "array", "items": {"type": "string"}, "description": "Issue IDs this depends on"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["title"],
            },
        ),
        Tool(
            name="update_issue",
            description="Update an issue's status, priority, title, or custom fields. Use get_valid_transitions to see allowed status changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Issue ID"},
                    "status": {
                        "type": "string",
                        "description": "New status (use get_valid_transitions for allowed values)",
                    },
                    "priority": {"type": "integer", "minimum": 0, "maximum": 4, "description": "New priority"},
                    "title": {"type": "string", "description": "New title"},
                    "assignee": {"type": "string", "description": "New assignee"},
                    "description": {"type": "string", "description": "New description"},
                    "notes": {"type": "string", "description": "New notes"},
                    "parent_id": {"type": "string", "description": "New parent issue ID (empty string to clear)"},
                    "fields": {"type": "object", "description": "Fields to merge into existing fields"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="close_issue",
            description="Close an issue with optional reason",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Issue ID"},
                    "reason": {"type": "string", "description": "Close reason"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                    "fields": {"type": "object", "description": "Custom fields to set (e.g. root_cause for incidents)"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="reopen_issue",
            description="Reopen a closed issue, returning it to its type's initial state. Clears closed_at.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Issue ID"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="search_issues",
            description="Search issues by title and description",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {
                        "type": "integer",
                        "default": 100,
                        "minimum": 1,
                        "description": f"Max results (default 100, capped at {_MAX_LIST_RESULTS} unless no_limit=true)",
                    },
                    "offset": {"type": "integer", "default": 0, "minimum": 0, "description": "Skip first N results"},
                    "no_limit": {
                        "type": "boolean",
                        "default": False,
                        "description": f"Bypass the default result cap of {_MAX_LIST_RESULTS}. Use with caution on large projects.",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="claim_issue",
            description=(
                "Atomically claim an open issue by setting assignee (optimistic locking). "
                "Does NOT change status — use update_issue to advance through workflow after claiming."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Issue ID to claim"},
                    "assignee": {"type": "string", "description": "Who is claiming (agent name)"},
                    "actor": {
                        "type": "string",
                        "description": "Agent/user identity for audit trail (defaults to assignee)",
                    },
                },
                "required": ["id", "assignee"],
            },
        ),
        Tool(
            name="release_claim",
            description="Release a claimed issue by clearing its assignee. Does NOT change status. Only succeeds if the issue has an assignee.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Issue ID to release"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="claim_next",
            description="Claim the highest-priority ready issue by setting assignee. Does NOT change status — use update_issue to advance through workflow after claiming.",
            inputSchema={
                "type": "object",
                "properties": {
                    "assignee": {"type": "string", "description": "Who is claiming (agent name)"},
                    "type": {"type": "string", "description": "Filter by issue type"},
                    "priority_min": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 4,
                        "description": "Minimum priority (0=critical)",
                    },
                    "priority_max": {"type": "integer", "minimum": 0, "maximum": 4, "description": "Maximum priority"},
                    "actor": {
                        "type": "string",
                        "description": "Agent/user identity for audit trail (defaults to assignee)",
                    },
                },
                "required": ["assignee"],
            },
        ),
        Tool(
            name="batch_close",
            description="Close multiple issues in one call. Returns list of closed issues.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Issue IDs to close",
                    },
                    "reason": {"type": "string", "default": "", "description": "Close reason"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["ids"],
            },
        ),
        Tool(
            name="batch_update",
            description="Update multiple issues with the same changes in one call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Issue IDs to update",
                    },
                    "status": {
                        "type": "string",
                        "description": "New status (use get_valid_transitions for allowed values)",
                    },
                    "priority": {"type": "integer", "minimum": 0, "maximum": 4, "description": "New priority"},
                    "assignee": {"type": "string", "description": "New assignee"},
                    "fields": {"type": "object", "description": "Fields to merge"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["ids"],
            },
        ),
    ]

    handlers: dict[str, Callable[..., Any]] = {
        "get_issue": _handle_get_issue,
        "list_issues": _handle_list_issues,
        "create_issue": _handle_create_issue,
        "update_issue": _handle_update_issue,
        "close_issue": _handle_close_issue,
        "reopen_issue": _handle_reopen_issue,
        "search_issues": _handle_search_issues,
        "claim_issue": _handle_claim_issue,
        "release_claim": _handle_release_claim,
        "claim_next": _handle_claim_next,
        "batch_close": _handle_batch_close,
        "batch_update": _handle_batch_update,
    }

    return tools, handlers


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_get_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    try:
        issue = tracker.get_issue(arguments["id"])
        if arguments.get("include_transitions"):
            transitions = tracker.get_valid_transitions(arguments["id"])
            result = IssueWithTransitions(
                **issue.to_dict(),
                valid_transitions=[
                    {
                        "to": t.to,
                        "category": t.category,
                        "enforcement": t.enforcement,
                        "requires_fields": list(t.requires_fields),
                        "missing_fields": list(t.missing_fields),
                        "ready": t.ready,
                    }
                    for t in transitions
                ],
            )
            return _text(result)
        return _text(issue.to_dict())
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {arguments['id']}", code="not_found"))


async def _handle_list_issues(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    priority = arguments.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
    tracker = _get_db()
    status_filter = arguments.get("status")
    status_category = arguments.get("status_category")
    if status_category and not status_filter:
        category_states = tracker._get_states_for_category(status_category)
        if category_states:
            status_filter = status_category
        else:
            return _text({"issues": [], "limit": arguments.get("limit", 100), "offset": arguments.get("offset", 0), "has_more": False})

    effective_limit, offset = _resolve_pagination(arguments)

    issues = tracker.list_issues(
        status=status_filter,
        type=arguments.get("type"),
        priority=priority,
        parent_id=arguments.get("parent_id"),
        assignee=arguments.get("assignee"),
        label=arguments.get("label"),
        limit=effective_limit + 1,
        offset=offset,
    )
    issues, has_more = _apply_has_more(issues, effective_limit)
    return _text(
        IssueListResponse(
            issues=[i.to_dict() for i in issues],
            limit=effective_limit,
            offset=offset,
            has_more=has_more,
        )
    )


async def _handle_create_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    priority = arguments.get("priority", 2)
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
    tracker = _get_db()
    try:
        issue = tracker.create_issue(
            arguments["title"],
            type=arguments.get("type", "task"),
            priority=priority,
            parent_id=arguments.get("parent_id"),
            description=arguments.get("description", ""),
            notes=arguments.get("notes", ""),
            fields=arguments.get("fields"),
            labels=arguments.get("labels"),
            deps=arguments.get("deps"),
            actor=actor,
        )
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="validation_error"))
    _refresh_summary()
    return _text(issue.to_dict())


async def _handle_update_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    priority = arguments.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
    tracker = _get_db()
    try:
        before = tracker.get_issue(arguments["id"])
        issue = tracker.update_issue(
            arguments["id"],
            status=arguments.get("status"),
            priority=priority,
            title=arguments.get("title"),
            assignee=arguments.get("assignee"),
            description=arguments.get("description"),
            notes=arguments.get("notes"),
            parent_id=arguments.get("parent_id"),
            fields=arguments.get("fields"),
            actor=actor,
        )
        _refresh_summary()
        changed: list[str] = []
        if issue.status != before.status:
            changed.append("status")
        if issue.priority != before.priority:
            changed.append("priority")
        if issue.title != before.title:
            changed.append("title")
        if issue.assignee != before.assignee:
            changed.append("assignee")
        if issue.description != before.description:
            changed.append("description")
        if issue.notes != before.notes:
            changed.append("notes")
        if issue.parent_id != before.parent_id:
            changed.append("parent_id")
        if issue.fields != before.fields:
            changed.append("fields")
        result = IssueWithChangedFields(**issue.to_dict(), changed_fields=changed)
        return _text(result)
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {arguments['id']}", code="not_found"))
    except ValueError as e:
        return _text(_build_transition_error(tracker, arguments["id"], str(e)))


async def _handle_close_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        ready_before = {i.id for i in tracker.get_ready()}
        issue = tracker.close_issue(
            arguments["id"],
            reason=arguments.get("reason", ""),
            actor=actor,
            fields=arguments.get("fields"),
        )
        _refresh_summary()
        ready_after = tracker.get_ready()
        newly_unblocked = [i for i in ready_after if i.id not in ready_before]
        result_dict = issue.to_dict()
        if newly_unblocked:
            result_dict["newly_unblocked"] = [_slim_issue(i) for i in newly_unblocked]  # type: ignore[typeddict-unknown-key]
        return _text(result_dict)
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {arguments['id']}", code="not_found"))
    except ValueError as e:
        return _text(_build_transition_error(tracker, arguments["id"], str(e)))


async def _handle_reopen_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        issue = tracker.reopen_issue(
            arguments["id"],
            actor=actor,
        )
        _refresh_summary()
        return _text(issue.to_dict())
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {arguments['id']}", code="not_found"))
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="invalid"))


async def _handle_search_issues(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    effective_limit, offset = _resolve_pagination(arguments)

    issues = tracker.search_issues(
        arguments["query"],
        limit=effective_limit + 1,
        offset=offset,
    )
    issues, has_more = _apply_has_more(issues, effective_limit)
    return _text(
        SearchResponse(
            issues=[_slim_issue(i) for i in issues],
            limit=effective_limit,
            offset=offset,
            has_more=has_more,
        )
    )


async def _handle_claim_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", arguments["assignee"]))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        issue = tracker.claim_issue(
            arguments["id"],
            assignee=arguments["assignee"],
            actor=actor,
        )
        _refresh_summary()
        return _text(issue.to_dict())
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {arguments['id']}", code="not_found"))
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="conflict"))


async def _handle_release_claim(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        issue = tracker.release_claim(arguments["id"], actor=actor)
        _refresh_summary()
        return _text(issue.to_dict())
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {arguments['id']}", code="not_found"))
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="conflict"))


async def _handle_claim_next(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", arguments["assignee"]))
    if actor_err:
        return actor_err
    priority_min = arguments.get("priority_min")
    pmin_err = _validate_int_range(priority_min, "priority_min", min_val=0, max_val=4)
    if pmin_err:
        return pmin_err
    priority_max = arguments.get("priority_max")
    pmax_err = _validate_int_range(priority_max, "priority_max", min_val=0, max_val=4)
    if pmax_err:
        return pmax_err
    tracker = _get_db()
    claimed = tracker.claim_next(
        arguments["assignee"],
        type_filter=arguments.get("type"),
        priority_min=priority_min,
        priority_max=priority_max,
        actor=actor,
    )
    if claimed is None:
        return _text({"status": "empty", "reason": "No ready issues matching filters"})
    _refresh_summary()
    parts = [f"P{claimed.priority}"]
    if claimed.type != "task":
        parts.append(f"type={claimed.type}")
    parts.append("ready issue (no blockers)")
    result = ClaimNextResponse(
        **claimed.to_dict(),
        selection_reason=f"Highest-priority {', '.join(parts)}",
    )
    return _text(result)


async def _handle_batch_close(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    ids = arguments["ids"]
    if not all(isinstance(i, str) for i in ids):
        return _text(ErrorResponse(error="All issue IDs must be strings", code="validation_error"))
    ready_before = {i.id for i in tracker.get_ready()}
    closed, failed = tracker.batch_close(
        ids,
        reason=arguments.get("reason", ""),
        actor=actor,
    )
    _refresh_summary()
    ready_after = tracker.get_ready()
    newly_unblocked = [i for i in ready_after if i.id not in ready_before]
    batch_result = BatchCloseResponse(
        succeeded=[i.id for i in closed],
        failed=failed,
        count=len(closed),
    )
    if newly_unblocked:
        batch_result["newly_unblocked"] = [_slim_issue(i) for i in newly_unblocked]
    return _text(batch_result)


async def _handle_batch_update(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    priority = arguments.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
    tracker = _get_db()
    u_ids = arguments["ids"]
    if not all(isinstance(i, str) for i in u_ids):
        return _text(ErrorResponse(error="All issue IDs must be strings", code="validation_error"))
    u_fields = arguments.get("fields")
    if u_fields is not None and not isinstance(u_fields, dict):
        return _text(ErrorResponse(error="fields must be a JSON object", code="validation_error"))
    updated, update_failed = tracker.batch_update(
        u_ids,
        status=arguments.get("status"),
        priority=priority,
        assignee=arguments.get("assignee"),
        fields=u_fields,
        actor=actor,
    )
    _refresh_summary()
    return _text(
        BatchUpdateResponse(
            succeeded=[i.id for i in updated],
            failed=update_failed,
            count=len(updated),
        )
    )
