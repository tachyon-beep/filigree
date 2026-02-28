"""MCP tools for dependencies, ready/blocked queries, plans, and critical path."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.types import TextContent, Tool

from filigree.mcp_tools.common import _text, _validate_actor
from filigree.types.api import PlanResponse


def register() -> tuple[list[Tool], dict[str, Callable[..., Any]]]:
    """Return (tool_definitions, handler_map) for planning-domain tools."""
    tools = [
        Tool(
            name="add_dependency",
            description="Add dependency: from_id depends on to_id (to_id blocks from_id)",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_id": {"type": "string", "description": "Issue that is blocked"},
                    "to_id": {"type": "string", "description": "Issue that blocks"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["from_id", "to_id"],
            },
        ),
        Tool(
            name="remove_dependency",
            description="Remove a dependency between two issues",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_id": {"type": "string", "description": "Issue that was blocked"},
                    "to_id": {"type": "string", "description": "Issue that was blocking"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["from_id", "to_id"],
            },
        ),
        Tool(
            name="get_ready",
            description="Get all issues that are ready to work on (open, no blockers), sorted by priority",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_blocked",
            description="Get all blocked issues with their blocker lists",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_plan",
            description="Get milestone plan tree showing phases, steps, and progress",
            inputSchema={
                "type": "object",
                "properties": {
                    "milestone_id": {"type": "string", "description": "Milestone issue ID"},
                },
                "required": ["milestone_id"],
            },
        ),
        Tool(
            name="create_plan",
            description=(
                "Create a full milestone->phase->step hierarchy in one call. "
                "Returns the plan tree. Step deps use indices: integer for same-phase, "
                "'phase_idx.step_idx' for cross-phase."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "milestone": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "priority": {"type": "integer", "default": 2},
                            "description": {"type": "string", "default": ""},
                        },
                        "required": ["title"],
                    },
                    "phases": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "priority": {"type": "integer", "default": 2},
                                "description": {"type": "string", "default": ""},
                                "steps": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "title": {"type": "string"},
                                            "priority": {"type": "integer", "default": 2},
                                            "description": {"type": "string", "default": ""},
                                            "deps": {
                                                "type": "array",
                                                "items": {},
                                                "description": "Step indices (int for same-phase, 'p.s' for cross-phase)",
                                            },
                                        },
                                        "required": ["title"],
                                    },
                                },
                            },
                            "required": ["title"],
                        },
                    },
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["milestone", "phases"],
            },
        ),
        Tool(
            name="get_critical_path",
            description="Longest dependency chain among open issues. Helps prioritize work that unblocks the most downstream items.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]

    handlers: dict[str, Callable[..., Any]] = {
        "add_dependency": _handle_add_dependency,
        "remove_dependency": _handle_remove_dependency,
        "get_ready": _handle_get_ready,
        "get_blocked": _handle_get_blocked,
        "get_plan": _handle_get_plan,
        "create_plan": _handle_create_plan,
        "get_critical_path": _handle_get_critical_path,
    }

    return tools, handlers


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_add_dependency(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        added = tracker.add_dependency(
            arguments["from_id"],
            arguments["to_id"],
            actor=actor,
        )
    except (ValueError, KeyError) as e:
        return _text({"error": str(e), "code": "invalid"})
    _refresh_summary()
    status = "added" if added else "already_exists"
    return _text({"status": status, "from_id": arguments["from_id"], "to_id": arguments["to_id"]})


async def _handle_remove_dependency(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    removed = tracker.remove_dependency(
        arguments["from_id"],
        arguments["to_id"],
        actor=actor,
    )
    _refresh_summary()
    status = "removed" if removed else "not_found"
    return _text({"status": status, "from_id": arguments["from_id"], "to_id": arguments["to_id"]})


async def _handle_get_ready(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    issues = tracker.get_ready()
    return _text([{"id": i.id, "title": i.title, "priority": i.priority, "type": i.type} for i in issues])


async def _handle_get_blocked(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    issues = tracker.get_blocked()
    return _text([{"id": i.id, "title": i.title, "priority": i.priority, "type": i.type, "blocked_by": i.blocked_by} for i in issues])


async def _handle_get_plan(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    try:
        plan_tree = tracker.get_plan(arguments["milestone_id"])
        total = plan_tree["total_steps"]
        completed = plan_tree["completed_steps"]
        result = PlanResponse(
            milestone=plan_tree["milestone"],
            phases=plan_tree["phases"],
            total_steps=total,
            completed_steps=completed,
            progress_pct=round(completed / total * 100, 1) if total > 0 else 0.0,
        )
        return _text(result)
    except KeyError:
        return _text({"error": f"Milestone not found: {arguments['milestone_id']}", "code": "not_found"})


async def _handle_create_plan(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _refresh_summary

    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
    tracker = _get_db()
    try:
        plan = tracker.create_plan(
            arguments["milestone"],
            arguments["phases"],
            actor=actor,
        )
        _refresh_summary()
        return _text(plan)
    except (KeyError, IndexError, ValueError) as e:
        return _text({"error": str(e), "code": "invalid"})


async def _handle_get_critical_path(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    path = tracker.get_critical_path()
    return _text({"path": path, "length": len(path)})
