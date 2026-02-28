"""MCP tools for comments, labels, changes, stats, export/import, and maintenance."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any

from mcp.types import TextContent, Tool

from filigree.mcp_tools.common import _text, _validate_actor


def register() -> tuple[list[Tool], dict[str, Callable[..., Any]]]:
    """Return (tool_definitions, handler_map) for meta-domain tools."""
    tools = [
        Tool(
            name="add_comment",
            description="Add a comment to an issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                    "text": {"type": "string", "description": "Comment text"},
                    "actor": {"type": "string", "description": "Agent/user identity (used as comment author)"},
                },
                "required": ["issue_id", "text"],
            },
        ),
        Tool(
            name="get_comments",
            description="Get all comments on an issue (for agent-to-agent context handoff)",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="add_label",
            description="Add a label to an issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                    "label": {"type": "string", "description": "Label to add"},
                },
                "required": ["issue_id", "label"],
            },
        ),
        Tool(
            name="remove_label",
            description="Remove a label from an issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                    "label": {"type": "string", "description": "Label to remove"},
                },
                "required": ["issue_id", "label"],
            },
        ),
        Tool(
            name="batch_add_label",
            description="Add the same label to multiple issues in one call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Issue IDs to update",
                    },
                    "label": {"type": "string", "description": "Label to add"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["ids", "label"],
            },
        ),
        Tool(
            name="batch_add_comment",
            description="Add the same comment to multiple issues in one call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Issue IDs to update",
                    },
                    "text": {"type": "string", "description": "Comment text"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["ids", "text"],
            },
        ),
        Tool(
            name="get_changes",
            description="Get events since a timestamp (for session resumption). Returns chronological event list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "ISO timestamp to get events after"},
                    "limit": {
                        "type": "integer",
                        "default": 100,
                        "minimum": 1,
                        "description": "Max events (default 100)",
                    },
                },
                "required": ["since"],
            },
        ),
        Tool(
            name="get_summary",
            description="Get the pre-computed project summary (same as context.md)",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_stats",
            description="Get project statistics: counts by status, type, ready/blocked",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_metrics",
            description="Flow metrics: cycle time, lead time, throughput. Useful for retrospectives and velocity tracking.",
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 30, "minimum": 1, "description": "Lookback window in days"},
                },
            },
        ),
        Tool(
            name="export_jsonl",
            description="Export all project data (issues, deps, labels, comments, events) to a JSONL file for backup or migration.",
            inputSchema={
                "type": "object",
                "properties": {
                    "output_path": {"type": "string", "description": "File path to write JSONL output"},
                },
                "required": ["output_path"],
            },
        ),
        Tool(
            name="import_jsonl",
            description="Import project data from a JSONL file. Use merge=true to skip existing records.",
            inputSchema={
                "type": "object",
                "properties": {
                    "input_path": {"type": "string", "description": "File path to read JSONL from"},
                    "merge": {
                        "type": "boolean",
                        "default": False,
                        "description": "Skip existing records instead of failing",
                    },
                },
                "required": ["input_path"],
            },
        ),
        Tool(
            name="archive_closed",
            description="Archive old closed issues (>N days). Reduces active issue count for better performance.",
            inputSchema={
                "type": "object",
                "properties": {
                    "days_old": {
                        "type": "integer",
                        "default": 30,
                        "description": "Archive issues closed more than N days ago",
                    },
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
            },
        ),
        Tool(
            name="compact_events",
            description="Remove old events for archived issues. Run after archive_closed to reclaim space.",
            inputSchema={
                "type": "object",
                "properties": {
                    "keep_recent": {
                        "type": "integer",
                        "default": 50,
                        "description": "Keep N most recent events per archived issue",
                    },
                },
            },
        ),
        Tool(
            name="undo_last",
            description="Undo the most recent reversible action on an issue. Covers status, title, priority, assignee, description, notes, claims, and dependency changes.",
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
            name="get_issue_events",
            description="Get events for a specific issue, newest first. Useful for reviewing history before undo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                    "limit": {"type": "integer", "default": 50, "minimum": 1, "description": "Max events (default 50)"},
                },
                "required": ["issue_id"],
            },
        ),
    ]

    handlers: dict[str, Callable[..., Any]] = {
        "add_comment": _handle_add_comment,
        "get_comments": _handle_get_comments,
        "add_label": _handle_add_label,
        "remove_label": _handle_remove_label,
        "batch_add_label": _handle_batch_add_label,
        "batch_add_comment": _handle_batch_add_comment,
        "get_changes": _handle_get_changes,
        "get_summary": _handle_get_summary,
        "get_stats": _handle_get_stats,
        "get_metrics": _handle_get_metrics,
        "export_jsonl": _handle_export_jsonl,
        "import_jsonl": _handle_import_jsonl,
        "archive_closed": _handle_archive_closed,
        "compact_events": _handle_compact_events,
        "undo_last": _handle_undo_last,
        "get_issue_events": _handle_get_issue_events,
    }

    return tools, handlers


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_add_comment(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        tracker.get_issue(arguments["issue_id"])
    except KeyError:
        return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})
    try:
        comment_id = tracker.add_comment(
            arguments["issue_id"],
            arguments["text"],
            author=actor,
        )
    except ValueError as e:
        return _text({"error": str(e), "code": "validation_error"})
    return _text({"status": "ok", "comment_id": comment_id})


async def _handle_get_comments(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    try:
        tracker.get_issue(arguments["issue_id"])
    except KeyError:
        return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})
    comments = tracker.get_comments(arguments["issue_id"])
    return _text(comments)


async def _handle_add_label(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    tracker = _get_db()
    try:
        tracker.get_issue(arguments["issue_id"])
    except KeyError:
        return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})
    try:
        added = tracker.add_label(arguments["issue_id"], arguments["label"])
    except ValueError as e:
        return _text({"error": str(e), "code": "validation_error"})
    _refresh_summary()
    status = "added" if added else "already_exists"
    return _text({"status": status, "issue_id": arguments["issue_id"], "label": arguments["label"]})


async def _handle_remove_label(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    tracker = _get_db()
    try:
        tracker.get_issue(arguments["issue_id"])
    except KeyError:
        return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})
    try:
        removed = tracker.remove_label(arguments["issue_id"], arguments["label"])
    except ValueError as e:
        return _text({"error": str(e), "code": "validation_error"})
    _refresh_summary()
    status = "removed" if removed else "not_found"
    return _text({"status": status, "issue_id": arguments["issue_id"], "label": arguments["label"]})


async def _handle_batch_add_label(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    tracker = _get_db()
    label_ids = arguments["ids"]
    if not all(isinstance(i, str) for i in label_ids):
        return _text({"error": "All issue IDs must be strings", "code": "validation_error"})
    if not isinstance(arguments["label"], str):
        return _text({"error": "label must be a string", "code": "validation_error"})
    label_succeeded, label_failed = tracker.batch_add_label(label_ids, label=arguments["label"])
    _refresh_summary()
    return _text(
        {
            "succeeded": [row["id"] for row in label_succeeded],
            "results": label_succeeded,
            "failed": label_failed,
            "count": len(label_succeeded),
        }
    )


async def _handle_batch_add_comment(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    comment_ids = arguments["ids"]
    if not all(isinstance(i, str) for i in comment_ids):
        return _text({"error": "All issue IDs must be strings", "code": "validation_error"})
    if not isinstance(arguments["text"], str):
        return _text({"error": "text must be a string", "code": "validation_error"})
    comment_succeeded, comment_failed = tracker.batch_add_comment(
        comment_ids,
        text=arguments["text"],
        author=actor,
    )
    _refresh_summary()
    return _text(
        {
            "succeeded": [str(row["id"]) for row in comment_succeeded],
            "results": comment_succeeded,
            "failed": comment_failed,
            "count": len(comment_succeeded),
        }
    )


async def _handle_get_changes(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    events = tracker.get_events_since(
        arguments["since"],
        limit=arguments.get("limit", 100),
    )
    return _text(events)


async def _handle_get_summary(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db
    from filigree.summary import generate_summary

    tracker = _get_db()
    summary = generate_summary(tracker)
    return _text(summary)


async def _handle_get_stats(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    return _text(tracker.get_stats())


async def _handle_get_metrics(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.analytics import get_flow_metrics
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    return _text(get_flow_metrics(tracker, days=arguments.get("days", 30)))


async def _handle_export_jsonl(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _safe_path

    tracker = _get_db()
    try:
        safe = _safe_path(arguments["output_path"])
        count = tracker.export_jsonl(safe)
        return _text({"status": "ok", "records": count, "path": str(safe)})
    except ValueError as e:
        return _text({"error": str(e), "code": "invalid_path"})
    except OSError as e:
        return _text({"error": str(e), "code": "io_error"})


async def _handle_import_jsonl(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary, _safe_path

    tracker = _get_db()
    try:
        safe = _safe_path(arguments["input_path"])
    except ValueError as e:
        return _text({"error": str(e), "code": "invalid_path"})
    try:
        count = tracker.import_jsonl(safe, merge=arguments.get("merge", False))
        _refresh_summary()
        return _text({"status": "ok", "records": count, "path": str(safe)})
    except (ValueError, OSError, sqlite3.Error) as e:
        logging.getLogger(__name__).warning("import_jsonl failed: %s", e, exc_info=True)
        return _text({"error": str(e), "code": "import_error"})


async def _handle_archive_closed(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    archived = tracker.archive_closed(
        days_old=arguments.get("days_old", 30),
        actor=actor,
    )
    _refresh_summary()
    return _text({"status": "ok", "archived_count": len(archived), "archived_ids": archived})


async def _handle_compact_events(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    deleted = tracker.compact_events(keep_recent=arguments.get("keep_recent", 50))
    return _text({"status": "ok", "events_deleted": deleted})


async def _handle_undo_last(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        result = tracker.undo_last(arguments["id"], actor=actor)
        if result["undone"]:
            _refresh_summary()
        return _text(result)
    except KeyError:
        return _text({"error": f"Issue not found: {arguments['id']}", "code": "not_found"})


async def _handle_get_issue_events(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    try:
        events = tracker.get_issue_events(
            arguments["issue_id"],
            limit=arguments.get("limit", 50),
        )
        return _text(events)
    except KeyError:
        return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})
