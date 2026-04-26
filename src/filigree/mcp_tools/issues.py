"""MCP tools for issue CRUD, search, claim, and batch operations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mcp.types import TextContent, Tool

from filigree.mcp_tools.common import (
    _MAX_LIST_RESULTS,
    _apply_has_more,
    _build_transition_error,
    _list_response,
    _parse_args,
    _resolve_pagination,
    _slim_issue,
    _text,
    _validate_actor,
    _validate_int_range,
)
from filigree.types.api import (
    BatchResponse,
    ClaimNextEmptyResponse,
    ClaimNextResponse,
    ErrorCode,
    ErrorResponse,
    IssueWithChangedFields,
    IssueWithTransitions,
    IssueWithUnblocked,
    SlimIssue,
    TransitionDetail,
    classify_value_error,
)
from filigree.types.core import IssueDict
from filigree.types.inputs import (
    BatchCloseArgs,
    BatchUpdateArgs,
    ClaimIssueArgs,
    ClaimNextArgs,
    CloseIssueArgs,
    CreateIssueArgs,
    GetIssueArgs,
    ListIssuesArgs,
    ReleaseClaimArgs,
    ReopenIssueArgs,
    SearchIssuesArgs,
    UpdateIssueArgs,
)

logger = logging.getLogger(__name__)

_UPDATE_TRACKED_FIELDS = ("status", "priority", "title", "assignee", "description", "notes", "parent_id", "fields")


def register() -> tuple[list[Tool], dict[str, Callable[..., Any]]]:
    """Return (tool_definitions, handler_map) for issue-domain tools."""
    tools = [
        Tool(
            name="get_issue",
            description="Get full details of an issue including deps, labels, children, ready status. Set include_transitions=true for valid next states.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                    "include_transitions": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include valid_transitions in response (saves a separate call)",
                    },
                    "include_files": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include file associations in response (default true)",
                    },
                },
                "required": ["issue_id"],
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
                    "parent_issue_id": {"type": "string", "description": "Filter by parent issue ID"},
                    "assignee": {"type": "string", "description": "Filter by assignee"},
                    "label": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "Filter by label(s). Multiple labels use AND logic. Supports virtual labels (age:fresh, has:findings).",
                    },
                    "label_prefix": {
                        "type": "string",
                        "description": "Filter by label namespace prefix (must include trailing colon, e.g. 'cluster:')",
                    },
                    "not_label": {
                        "type": "string",
                        "description": "Exclude issues with this label. Supports exact match, prefix (trailing colon), and virtual labels.",
                    },
                    "limit": {
                        "type": "integer",
                        "default": _MAX_LIST_RESULTS,
                        "minimum": 1,
                        "description": f"Max results (default {_MAX_LIST_RESULTS}, capped at {_MAX_LIST_RESULTS} unless no_limit=true)",
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
                    "parent_issue_id": {"type": "string", "description": "Parent issue ID (for hierarchy)"},
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
                    "issue_id": {"type": "string", "description": "Issue ID"},
                    "status": {
                        "type": "string",
                        "description": "New status (use get_valid_transitions for allowed values)",
                    },
                    "priority": {"type": "integer", "minimum": 0, "maximum": 4, "description": "New priority"},
                    "title": {"type": "string", "description": "New title"},
                    "assignee": {"type": "string", "description": "New assignee"},
                    "description": {"type": "string", "description": "New description"},
                    "notes": {"type": "string", "description": "New notes"},
                    "parent_issue_id": {
                        "type": "string",
                        "description": "New parent issue ID (empty string to clear)",
                    },
                    "fields": {"type": "object", "description": "Fields to merge into existing fields"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="close_issue",
            description="Close an issue with optional reason",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                    "reason": {"type": "string", "description": "Close reason"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                    "fields": {"type": "object", "description": "Custom fields to set (e.g. root_cause for incidents)"},
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="reopen_issue",
            description="Reopen a closed issue, returning it to its type's initial state. Clears closed_at.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["issue_id"],
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
                        "default": _MAX_LIST_RESULTS,
                        "minimum": 1,
                        "description": f"Max results (default {_MAX_LIST_RESULTS}, capped at {_MAX_LIST_RESULTS} unless no_limit=true)",
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
                    "issue_id": {"type": "string", "description": "Issue ID to claim"},
                    "assignee": {"type": "string", "minLength": 1, "description": "Who is claiming (agent name)"},
                    "actor": {
                        "type": "string",
                        "description": "Agent/user identity for audit trail (defaults to assignee)",
                    },
                },
                "required": ["issue_id", "assignee"],
            },
        ),
        Tool(
            name="release_claim",
            description="Release a claimed issue by clearing its assignee. Does NOT change status. Only succeeds if the issue has an assignee.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID to release"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="claim_next",
            description="Claim the highest-priority ready issue by setting assignee. Does NOT change status — use update_issue to advance through workflow after claiming.",
            inputSchema={
                "type": "object",
                "properties": {
                    "assignee": {"type": "string", "minLength": 1, "description": "Who is claiming (agent name)"},
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
            description="Close multiple issues in one call. Returns BatchResponse[SlimIssue] (succeeded/failed) plus newly_unblocked when applicable.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Issue IDs to close",
                    },
                    "reason": {"type": "string", "default": "", "description": "Close reason"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["issue_ids"],
            },
        ),
        Tool(
            name="batch_update",
            description="Update multiple issues with the same changes in one call. Returns BatchResponse[SlimIssue] (succeeded/failed).",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_ids": {
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
                "required": ["issue_ids"],
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

    args = _parse_args(arguments, GetIssueArgs)
    tracker = _get_db()
    try:
        issue = tracker.get_issue(args["issue_id"])
        issue_dict = issue.to_dict()

        # Fail-fast to match dashboard and get_issue_files MCP tool; see
        # filigree-c6c7842661 for why swallowing sqlite3.Error is wrong.
        file_assocs: list[Any] = []
        if args.get("include_files", True):
            file_assocs = tracker.get_issue_files(args["issue_id"])

        if args.get("include_transitions"):
            transitions = tracker.get_valid_transitions(args["issue_id"])
            result = IssueWithTransitions(
                **issue_dict,
                valid_transitions=[
                    TransitionDetail(
                        to=t.to,
                        category=t.category,
                        enforcement=t.enforcement or "",
                        requires_fields=list(t.requires_fields),
                        missing_fields=list(t.missing_fields),
                        ready=t.ready,
                    )
                    for t in transitions
                ],
            )
            out: dict[str, Any] = dict(result)
            if args.get("include_files", True):
                out["files"] = file_assocs
            return _text(out)
        out = dict(issue_dict)
        if args.get("include_files", True):
            out["files"] = file_assocs
        return _text(out)
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {args['issue_id']}", code=ErrorCode.NOT_FOUND))


async def _handle_list_issues(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, ListIssuesArgs)
    priority = args.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
    tracker = _get_db()
    status_filter = args.get("status")
    status_category = args.get("status_category")
    if status_category and not status_filter:
        # Pass the category name directly — list_issues() handles category
        # expansion internally (open/wip/done + aliases like in_progress/closed).
        status_filter = status_category

    effective_limit, offset, pag_err = _resolve_pagination(arguments)
    if pag_err is not None:
        return pag_err

    try:
        issues = tracker.list_issues(
            status=status_filter,
            type=args.get("type"),
            priority=priority,
            parent_id=args.get("parent_issue_id"),
            assignee=args.get("assignee"),
            label=args.get("label"),
            label_prefix=args.get("label_prefix"),
            not_label=args.get("not_label"),
            limit=effective_limit + 1,
            offset=offset,
        )
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code=ErrorCode.VALIDATION))
    issues, has_more = _apply_has_more(issues, effective_limit)
    items = [i.to_dict() for i in issues]
    next_offset = offset + len(items) if has_more else None
    return _text(_list_response(items, has_more=has_more, next_offset=next_offset))


async def _handle_create_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, CreateIssueArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err
    priority = args.get("priority", 2)
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
    tracker = _get_db()
    try:
        issue = tracker.create_issue(
            args["title"],
            type=args.get("type", "task"),
            priority=priority,
            parent_id=args.get("parent_issue_id"),
            description=args.get("description", ""),
            notes=args.get("notes", ""),
            fields=args.get("fields"),
            labels=args.get("labels"),
            deps=args.get("deps"),
            actor=actor,
        )
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code=ErrorCode.VALIDATION))
    _refresh_summary()
    return _text(issue.to_dict())


async def _handle_update_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, UpdateIssueArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err
    priority = args.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
    tracker = _get_db()
    try:
        before = tracker.get_issue(args["issue_id"])
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {args['issue_id']}", code=ErrorCode.NOT_FOUND))
    try:
        issue = tracker.update_issue(
            args["issue_id"],
            status=args.get("status"),
            priority=priority,
            title=args.get("title"),
            assignee=args.get("assignee"),
            description=args.get("description"),
            notes=args.get("notes"),
            parent_id=args.get("parent_issue_id"),
            fields=args.get("fields"),
            actor=actor,
        )
        _refresh_summary()
        changed = [attr for attr in _UPDATE_TRACKED_FIELDS if getattr(issue, attr) != getattr(before, attr)]
        result = IssueWithChangedFields(**issue.to_dict(), changed_fields=changed)
        return _text(result)
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {args['issue_id']}", code=ErrorCode.NOT_FOUND))
    except ValueError as e:
        msg = str(e)
        if classify_value_error(msg) == ErrorCode.INVALID_TRANSITION:
            return _text(_build_transition_error(tracker, args["issue_id"], msg))
        return _text(ErrorResponse(error=msg, code=ErrorCode.VALIDATION))


async def _handle_close_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, CloseIssueArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        ready_before = {i.id for i in tracker.get_ready()}
        issue = tracker.close_issue(
            args["issue_id"],
            reason=args.get("reason", ""),
            actor=actor,
            fields=args.get("fields"),
        )
        _refresh_summary()
        ready_after = tracker.get_ready()
        newly_unblocked = [i for i in ready_after if i.id not in ready_before]
        if newly_unblocked:
            result: IssueWithUnblocked | IssueDict = IssueWithUnblocked(
                **issue.to_dict(), newly_unblocked=[_slim_issue(i) for i in newly_unblocked]
            )
        else:
            result = issue.to_dict()
        return _text(result)
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {args['issue_id']}", code=ErrorCode.NOT_FOUND))
    except ValueError as e:
        return _text(_build_transition_error(tracker, args["issue_id"], str(e)))


async def _handle_reopen_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, ReopenIssueArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        issue = tracker.reopen_issue(
            args["issue_id"],
            actor=actor,
        )
        _refresh_summary()
        return _text(issue.to_dict())
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {args['issue_id']}", code=ErrorCode.NOT_FOUND))
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code=ErrorCode.INVALID_TRANSITION))


async def _handle_search_issues(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, SearchIssuesArgs)
    tracker = _get_db()
    effective_limit, offset, pag_err = _resolve_pagination(arguments)
    if pag_err is not None:
        return pag_err

    issues = tracker.search_issues(
        args["query"],
        limit=effective_limit + 1,
        offset=offset,
    )
    issues, has_more = _apply_has_more(issues, effective_limit)
    items = [_slim_issue(i) for i in issues]
    next_offset = offset + len(items) if has_more else None
    return _text(_list_response(items, has_more=has_more, next_offset=next_offset))


async def _handle_claim_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, ClaimIssueArgs)
    assignee = args.get("assignee")
    if not isinstance(assignee, str) or not assignee.strip():
        return _text(ErrorResponse(error="assignee must be a non-empty string", code=ErrorCode.VALIDATION))
    actor, actor_err = _validate_actor(args.get("actor", assignee))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        issue = tracker.claim_issue(
            args["issue_id"],
            assignee=assignee,
            actor=actor,
        )
        _refresh_summary()
        return _text(issue.to_dict())
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {args['issue_id']}", code=ErrorCode.NOT_FOUND))
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code=ErrorCode.CONFLICT))


async def _handle_release_claim(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, ReleaseClaimArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        issue = tracker.release_claim(args["issue_id"], actor=actor)
        _refresh_summary()
        return _text(issue.to_dict())
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {args['issue_id']}", code=ErrorCode.NOT_FOUND))
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code=ErrorCode.CONFLICT))


async def _handle_claim_next(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, ClaimNextArgs)
    assignee = args.get("assignee")
    if not isinstance(assignee, str) or not assignee.strip():
        return _text(ErrorResponse(error="assignee must be a non-empty string", code=ErrorCode.VALIDATION))
    actor, actor_err = _validate_actor(args.get("actor", assignee))
    if actor_err:
        return actor_err
    priority_min = args.get("priority_min")
    pmin_err = _validate_int_range(priority_min, "priority_min", min_val=0, max_val=4)
    if pmin_err:
        return pmin_err
    priority_max = args.get("priority_max")
    pmax_err = _validate_int_range(priority_max, "priority_max", min_val=0, max_val=4)
    if pmax_err:
        return pmax_err
    tracker = _get_db()
    try:
        claimed = tracker.claim_next(
            assignee,
            type_filter=args.get("type"),
            priority_min=priority_min,
            priority_max=priority_max,
            actor=actor,
        )
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code=ErrorCode.VALIDATION))
    if claimed is None:
        return _text(ClaimNextEmptyResponse(status="empty", reason="No ready issues matching filters"))
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

    args = _parse_args(arguments, BatchCloseArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    issue_ids = args["issue_ids"]
    if not all(isinstance(i, str) for i in issue_ids):
        return _text(ErrorResponse(error="All issue IDs must be strings", code=ErrorCode.VALIDATION))
    ready_before = {i.id for i in tracker.get_ready()}
    closed, failed = tracker.batch_close(
        issue_ids,
        reason=args.get("reason", ""),
        actor=actor,
    )
    _refresh_summary()
    ready_after = tracker.get_ready()
    newly_unblocked = [i for i in ready_after if i.id not in ready_before]
    batch_result: BatchResponse[SlimIssue] = BatchResponse(
        succeeded=[_slim_issue(i) for i in closed],
        failed=failed,
    )
    if newly_unblocked:
        batch_result["newly_unblocked"] = [_slim_issue(i) for i in newly_unblocked]
    return _text(batch_result)


async def _handle_batch_update(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, BatchUpdateArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err
    priority = args.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
    tracker = _get_db()
    issue_ids = args["issue_ids"]
    if not all(isinstance(i, str) for i in issue_ids):
        return _text(ErrorResponse(error="All issue IDs must be strings", code=ErrorCode.VALIDATION))
    u_fields = args.get("fields")
    if u_fields is not None and not isinstance(u_fields, dict):
        return _text(ErrorResponse(error="fields must be a JSON object", code=ErrorCode.VALIDATION))
    updated, update_failed = tracker.batch_update(
        issue_ids,
        status=args.get("status"),
        priority=priority,
        assignee=args.get("assignee"),
        fields=u_fields,
        actor=actor,
    )
    _refresh_summary()
    result: BatchResponse[SlimIssue] = BatchResponse(
        succeeded=[_slim_issue(i) for i in updated],
        failed=update_failed,
    )
    return _text(result)
