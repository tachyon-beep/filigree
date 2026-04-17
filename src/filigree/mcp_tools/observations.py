"""MCP tools for observation CRUD — agent scratchpad."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any

from mcp.types import TextContent, Tool

from filigree.mcp_tools.common import (
    _MAX_LIST_RESULTS,
    _apply_has_more,
    _parse_args,
    _resolve_pagination,
    _text,
    _validate_actor,
    _validate_int_range,
    _validate_str,
)
from filigree.types.api import ErrorResponse
from filigree.types.inputs import (
    BatchDismissObservationsArgs,
    DismissObservationArgs,
    ListObservationsArgs,
    ObserveArgs,
    PromoteObservationArgs,
)

logger = logging.getLogger(__name__)


def register() -> tuple[list[Tool], dict[str, Callable[..., Any]]]:
    """Return (tool_definitions, handler_map) for observation tools."""
    tools = [
        Tool(
            name="observe",
            description="Record an observation — something you noticed in passing. Fire-and-forget: observations are not issues. They expire after 14 days unless promoted or dismissed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Short description of the observation"},
                    "detail": {"type": "string", "description": "Longer explanation or context"},
                    "file_path": {"type": "string", "description": "File path (relative to project root)"},
                    "line": {"type": "integer", "description": "Line number in file (1-indexed, 0 accepted for unknown)"},
                    "source_issue_id": {"type": "string", "description": "Issue ID that prompted this observation"},
                    "priority": {
                        "type": "integer",
                        "description": "Priority 0-4 (0=critical)",
                        "default": 3,
                        "minimum": 0,
                        "maximum": 4,
                    },
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["summary"],
            },
        ),
        Tool(
            name="list_observations",
            description="List pending observations with optional filtering by file path or file ID. Automatically sweeps expired observations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": _MAX_LIST_RESULTS,
                        "minimum": 1,
                        "description": f"Max results (default {_MAX_LIST_RESULTS}, capped at {_MAX_LIST_RESULTS} unless no_limit=true)",
                    },
                    "offset": {"type": "integer", "default": 0, "description": "Skip first N results", "minimum": 0},
                    "no_limit": {
                        "type": "boolean",
                        "default": False,
                        "description": f"Bypass the default result cap of {_MAX_LIST_RESULTS}. Use with caution on large projects.",
                    },
                    "file_path": {"type": "string", "description": "Filter by substring in file path"},
                    "file_id": {"type": "string", "description": "Filter by exact file ID"},
                },
            },
        ),
        Tool(
            name="dismiss_observation",
            description="Dismiss a single observation (logs to audit trail).",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Observation ID"},
                    "reason": {"type": "string", "description": "Reason for dismissal"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="batch_dismiss_observations",
            description="Dismiss multiple observations in one call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Observation IDs to dismiss",
                    },
                    "reason": {"type": "string", "default": "", "description": "Reason for dismissal"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["ids"],
            },
        ),
        Tool(
            name="promote_observation",
            description="Promote an observation to a real issue. Deletes the observation, creates an issue with the from-observation label. Use type='bug' for defects, type='task' for improvements/cleanup.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Observation ID"},
                    "type": {
                        "type": "string",
                        "default": "task",
                        "description": "Issue type: 'bug' for defects, 'task' for improvements/cleanup, 'feature' for new capability, 'requirement' for formal requirements",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "Override priority (default: observation priority)",
                        "minimum": 0,
                        "maximum": 4,
                    },
                    "title": {"type": "string", "description": "Override title (default: observation summary)"},
                    "description": {"type": "string", "description": "Extra description to prepend"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["id"],
            },
        ),
    ]

    handlers: dict[str, Callable[..., Any]] = {
        "observe": _handle_observe,
        "list_observations": _handle_list_observations,
        "dismiss_observation": _handle_dismiss_observation,
        "batch_dismiss_observations": _handle_batch_dismiss_observations,
        "promote_observation": _handle_promote_observation,
    }

    return tools, handlers


async def _handle_observe(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, ObserveArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err

    # Validate raw argument types up front — otherwise a bool priority would
    # silently coerce to int (True==1), a dict detail would hit SQLite binding
    # errors, and a non-string file_path would crash in _normalize_scan_path.
    for err in (
        _validate_str(args.get("summary"), "summary"),
        _validate_str(args.get("detail"), "detail"),
        _validate_str(args.get("file_path"), "file_path"),
        _validate_str(args.get("source_issue_id"), "source_issue_id"),
        _validate_int_range(args.get("line"), "line", min_val=0),
        _validate_int_range(args.get("priority"), "priority", min_val=0, max_val=4),
    ):
        if err is not None:
            return err

    tracker = _get_db()
    try:
        obs = tracker.create_observation(
            args.get("summary", ""),
            detail=args.get("detail", ""),
            file_path=args.get("file_path", ""),
            line=args.get("line"),
            source_issue_id=args.get("source_issue_id", ""),
            priority=args.get("priority", 3),
            actor=actor,
        )
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="validation_error"))
    except sqlite3.Error as e:
        return _text(ErrorResponse(error=f"Database error: {e}", code="database_error"))
    _refresh_summary()
    return _text(obs)


async def _handle_list_observations(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, ListObservationsArgs)
    effective_limit, offset, pag_err = _resolve_pagination(arguments)
    if pag_err is not None:
        return pag_err
    tracker = _get_db()
    try:
        observations = tracker.list_observations(
            limit=effective_limit + 1,
            offset=offset,
            file_path=args.get("file_path", ""),
            file_id=args.get("file_id", ""),
        )
    except sqlite3.Error as e:
        return _text(ErrorResponse(error=f"Database error: {e}", code="database_error"))
    observations, has_more = _apply_has_more(observations, effective_limit)
    stats: dict[str, object]
    try:
        stats = dict(tracker.observation_stats(sweep=False))
    except sqlite3.Error as e:
        logger.warning("observation_stats failed, returning degraded response", exc_info=True)
        stats = {
            "count": None,
            "page_count": len(observations),
            "stale_count": None,
            "oldest_hours": None,
            "expiring_soon_count": None,
            "stats_error": f"observation stats temporarily unavailable ({type(e).__name__})",
        }
    return _text({"observations": observations, "stats": stats, "has_more": has_more})


async def _handle_dismiss_observation(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, DismissObservationArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err

    tracker = _get_db()
    try:
        tracker.dismiss_observation(
            args["id"],
            actor=actor,
            reason=args.get("reason", ""),
        )
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="not_found"))
    except sqlite3.Error as e:
        return _text(ErrorResponse(error=f"Database error: {e}", code="database_error"))
    _refresh_summary()
    return _text({"status": "dismissed", "id": args["id"]})


async def _handle_batch_dismiss_observations(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, BatchDismissObservationsArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err

    # Validate ids is a list of strings up front — a bare string would
    # otherwise be iterated char-by-char and produce bogus per-character
    # not_found results (see filigree-45580755aa).
    raw_ids = args.get("ids", [])
    if not isinstance(raw_ids, list):
        return _text(ErrorResponse(error="'ids' must be an array of strings", code="validation_error"))
    if not all(isinstance(x, str) for x in raw_ids):
        return _text(ErrorResponse(error="'ids' must contain only string values", code="validation_error"))

    tracker = _get_db()
    try:
        result = tracker.batch_dismiss_observations(
            raw_ids,
            actor=actor,
            reason=args.get("reason", ""),
        )
    except sqlite3.Error as e:
        return _text(ErrorResponse(error=f"Database error: {e}", code="database_error"))
    _refresh_summary()
    resp: dict[str, object] = {"dismissed": result["dismissed"], "ok": True}
    if result["not_found"]:
        resp["not_found"] = result["not_found"]
    return _text(resp)


async def _handle_promote_observation(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    args = _parse_args(arguments, PromoteObservationArgs)
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err

    priority = args.get("priority")
    if priority is not None:
        priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
        if priority_err:
            return priority_err

    tracker = _get_db()
    try:
        result = tracker.promote_observation(
            args["id"],
            issue_type=args.get("type", "task"),
            priority=priority,
            title=args.get("title"),
            extra_description=args.get("description", ""),
            actor=actor,
        )
    except ValueError as e:
        msg = str(e)
        code = "not_found" if "not found" in msg.lower() else "validation_error"
        return _text(ErrorResponse(error=msg, code=code))
    except sqlite3.Error as e:
        logger.error("promote_observation database error", exc_info=True)
        return _text(ErrorResponse(error=f"Database error: {e}", code="database_error"))
    _refresh_summary()
    resp: dict[str, object] = {"issue": result["issue"].to_dict()}
    if result.get("warnings"):
        resp["warnings"] = result["warnings"]
    return _text(resp)
