"""MCP server for the filigree issue tracker.

Primary interface for agents. Direct SQLite, no daemon.
Exposes filigree operations as MCP tools.

Usage:
    filigree-mcp                              # Auto-discover .filigree/ from cwd
    filigree-mcp --project /path/to/project   # Explicit project root
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    TextContent,
    Tool,
)

from filigree.core import (
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
    find_filigree_root,
    read_config,
)
from filigree.summary import generate_summary, write_summary

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Hard cap on list_issues / search_issues results to keep MCP response size
# within token limits.  Callers can pass no_limit=true to bypass.
_MAX_LIST_RESULTS = 50

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = Server("filigree")
db: FiligreeDB | None = None
_filigree_dir: Path | None = None
_logger: logging.Logger | None = None


def _get_db() -> FiligreeDB:
    if db is None:
        msg = "Database not initialized"
        raise RuntimeError(msg)
    return db


def _refresh_summary() -> None:
    """Regenerate context.md after mutations."""
    if _filigree_dir is not None:
        write_summary(_get_db(), _filigree_dir / SUMMARY_FILENAME)


def _safe_path(raw: str) -> Path:
    """Resolve a user-supplied path safely within the project root.

    Raises ValueError for paths that escape the project directory.
    """
    if Path(raw).is_absolute():
        msg = f"Absolute paths not allowed: {raw}"
        raise ValueError(msg)

    if _filigree_dir is None:
        msg = "Project directory not initialized"
        raise ValueError(msg)

    # Resolve relative to project root (parent of .filigree/)
    base = _filigree_dir.resolve().parent
    resolved = (base / raw).resolve()

    # Ensure resolved path is under the project root
    try:
        resolved.relative_to(base)
    except ValueError:
        msg = f"Path escapes project directory: {raw}"
        raise ValueError(msg) from None

    return resolved


def _text(content: Any) -> list[TextContent]:
    if isinstance(content, str):
        return [TextContent(type="text", text=content)]
    return [TextContent(type="text", text=json.dumps(content, indent=2, default=str))]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

CONTEXT_URI = "filigree://context"


@server.list_resources()  # type: ignore[untyped-decorator,no-untyped-call]
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri=CONTEXT_URI,  # type: ignore[arg-type]
            name="Project Pulse",
            description="Auto-generated project summary: vitals, ready work, blockers, recent activity",
            mimeType="text/markdown",
        ),
    ]


@server.read_resource()  # type: ignore[untyped-decorator,no-untyped-call]
async def read_context(uri: Any) -> str:
    if str(uri) == CONTEXT_URI:
        return generate_summary(_get_db())
    msg = f"Unknown resource: {uri}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_WORKFLOW_TEXT_STATIC = """\
# Filigree Workflow

You are working in a project that uses **filigree** for issue tracking.
Filigree data lives in `.filigree/` and is accessed via these MCP tools.

## Quick start
1. Read `filigree://context` resource for current project state (vitals, ready work, blockers)
2. Use `get_ready` to find unblocked tasks sorted by priority
3. Use `claim_issue` or `claim_next` to atomically claim a task (prevents double-work)
4. Use `get_valid_transitions` to see allowed state changes before updating
5. Work on the task, use `add_comment` to log progress
6. Use `close_issue` when done — response includes newly-unblocked items

## Key tools
- **get_issue / list_issues / search_issues** — read project state
- **create_issue / update_issue / close_issue** — mutate issues
- **claim_issue / claim_next** — atomic claim with optimistic locking
- **get_valid_transitions / validate_issue** — workflow-aware state management
- **list_types / get_type_info / explain_state** — discover type workflows
- **list_packs / get_workflow_guide** — workflow pack documentation
- **add_dependency / remove_dependency** — manage blockers
- **get_plan / create_plan** — milestone/phase/step hierarchies
- **batch_close / batch_update** — bulk operations (per-issue error handling)
- **get_changes** — events since a timestamp (session resumption)
- **get_template** — field schemas for issue types
- **get_stats / get_summary** — project analytics
- **get_metrics** — flow metrics (cycle time, lead time, throughput)
- **get_critical_path** — longest dependency chain among open issues
- **reload_templates** — refresh templates after editing .filigree/templates/

## Conventions
- Issue IDs: `{prefix}-{6hex}` (e.g., `myproj-a3f9b2`)
- Priorities: P0 (critical) through P4 (low)
- Each type has its own state machine — use `list_types` to discover
- Use `get_valid_transitions <id>` before status changes
"""


def _build_workflow_text() -> str:
    """Build dynamic workflow prompt from template registry if available."""
    if db is None:
        return _WORKFLOW_TEXT_STATIC

    try:
        tracker = _get_db()
        types_list = tracker.templates.list_types()
        if not types_list:
            return _WORKFLOW_TEXT_STATIC

        lines = [_WORKFLOW_TEXT_STATIC, "\n## Registered Types\n"]
        for tpl in sorted(types_list, key=lambda t: t.type):
            states = " → ".join(s.name for s in tpl.states)
            lines.append(f"- **{tpl.type}** ({tpl.display_name}): {states}")

        packs = tracker.templates.list_packs()
        if packs:
            lines.append("\n## Enabled Packs\n")
            for pack in sorted(packs, key=lambda p: p.pack):
                type_names = ", ".join(sorted(pack.types.keys()))
                lines.append(f"- **{pack.pack}** v{pack.version}: {type_names}")

        return "\n".join(lines) + "\n"
    except Exception:
        return _WORKFLOW_TEXT_STATIC


@server.list_prompts()  # type: ignore[untyped-decorator,no-untyped-call]
async def list_prompts() -> list[Prompt]:
    return [
        Prompt(
            name="filigree-workflow",
            description="Filigree workflow guide with current project context. Use at session start.",
            arguments=[
                PromptArgument(
                    name="include_context",
                    description="Include current project summary (default: true)",
                    required=False,
                ),
            ],
        ),
    ]


@server.get_prompt()  # type: ignore[untyped-decorator,no-untyped-call]
async def get_workflow_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    if name != "filigree-workflow":
        msg = f"Unknown prompt: {name}"
        raise ValueError(msg)
    messages: list[PromptMessage] = [
        PromptMessage(role="user", content=TextContent(type="text", text=_build_workflow_text())),
    ]
    include_ctx = (arguments or {}).get("include_context", "true").lower() != "false"
    if include_ctx:
        try:
            summary = generate_summary(_get_db())
            messages.append(
                PromptMessage(role="user", content=TextContent(type="text", text=summary)),
            )
        except RuntimeError:
            pass  # DB not initialized — skip context
    return GetPromptResult(description="Filigree workflow guide with project context", messages=messages)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


@server.list_tools()  # type: ignore[untyped-decorator,no-untyped-call]
async def list_tools() -> list[Tool]:
    return [
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
                        "description": "Max results (default 100)",
                    },
                    "offset": {"type": "integer", "default": 0, "minimum": 0, "description": "Skip first N results"},
                    "no_limit": {
                        "type": "boolean",
                        "default": False,
                        "description": "Bypass the default result cap. Use with caution on large projects.",
                    },
                },
            },
        ),
        Tool(
            name="create_issue",
            description="Create a new issue. Use get_template first to see available fields for the type.",
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
                    "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels"},
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
                        "description": "Max results (default 100)",
                    },
                    "offset": {"type": "integer", "default": 0, "minimum": 0, "description": "Skip first N results"},
                    "no_limit": {
                        "type": "boolean",
                        "default": False,
                        "description": "Bypass the default result cap. Use with caution on large projects.",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_template",
            description="Get the field schema for an issue type (shows what fields to populate)",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Issue type (bug, task, feature, epic, milestone, phase, step, requirement)",
                    },
                },
                "required": ["type"],
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
            name="get_critical_path",
            description="Longest dependency chain among open issues. Helps prioritize work that unblocks the most downstream items.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="release_claim",
            description="Release a claimed issue back to open. Reverses claim_issue.",
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
            name="get_workflow_states",
            description="Return workflow states by category (open/wip/done) from enabled templates.",
            inputSchema={"type": "object", "properties": {}},
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
        # -- Workflow template tools --
        Tool(
            name="list_types",
            description="List all registered issue types with their workflow info (states, pack, description).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_type_info",
            description="Get full workflow definition for an issue type: states, transitions, fields, enforcement rules.",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Issue type name (e.g. 'bug', 'task', 'feature')"},
                },
                "required": ["type"],
            },
        ),
        Tool(
            name="list_packs",
            description="List all enabled workflow packs with their types and metadata.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_valid_transitions",
            description="Get valid next states for an issue with readiness indicators. Shows which fields are needed before each transition.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="validate_issue",
            description="Validate an issue against its type template. Returns warnings for missing recommended fields. Call get_valid_transitions first to see allowed state changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="get_workflow_guide",
            description="Get the workflow guide for a pack: state diagram, overview, tips, common mistakes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pack": {"type": "string", "description": "Pack name (e.g. 'core', 'planning', 'engineering')"},
                },
                "required": ["pack"],
            },
        ),
        Tool(
            name="explain_state",
            description="Explain a state within a type's workflow: its category, inbound/outbound transitions, and fields required at this state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Issue type name"},
                    "state": {"type": "string", "description": "State name to explain"},
                },
                "required": ["type", "state"],
            },
        ),
        Tool(
            name="reload_templates",
            description="Reload workflow templates from disk. Use after editing .filigree/templates/ or .filigree/packs/ files.",
            inputSchema={"type": "object", "properties": {}},
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
    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


@server.call_tool()  # type: ignore[untyped-decorator]
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    tracker = _get_db()
    t0 = time.monotonic()

    try:
        result = await _dispatch(name, arguments, tracker)
    except Exception:
        if _logger:
            _logger.error("tool_error", extra={"tool": name, "args_data": arguments}, exc_info=True)
        raise
    else:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        if _logger:
            _logger.info("tool_call", extra={"tool": name, "args_data": arguments, "duration_ms": duration_ms})
        return result


async def _dispatch(name: str, arguments: dict[str, Any], tracker: FiligreeDB) -> list[TextContent]:
    match name:
        case "get_issue":
            try:
                issue = tracker.get_issue(arguments["id"])
                data = issue.to_dict()
                if arguments.get("include_transitions"):
                    transitions = tracker.get_valid_transitions(arguments["id"])
                    data["valid_transitions"] = [
                        {
                            "to": t.to,
                            "category": t.category,
                            "enforcement": t.enforcement,
                            "requires_fields": list(t.requires_fields),
                            "missing_fields": list(t.missing_fields),
                            "ready": t.ready,
                        }
                        for t in transitions
                    ]
                return _text(data)
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['id']}", "code": "not_found"})

        case "list_issues":
            status_filter = arguments.get("status")
            status_category = arguments.get("status_category")
            if status_category and not status_filter:
                # Expand category to matching states
                category_states = tracker._get_states_for_category(status_category)
                if category_states:
                    # Use the category name which list_issues already handles
                    status_filter = status_category

            no_limit = arguments.get("no_limit", False)
            requested_limit = arguments.get("limit", 100)
            offset = arguments.get("offset", 0)

            if no_limit:
                # Bypass cap — return everything the caller asked for
                issues = tracker.list_issues(
                    status=status_filter,
                    type=arguments.get("type"),
                    priority=arguments.get("priority"),
                    parent_id=arguments.get("parent_id"),
                    assignee=arguments.get("assignee"),
                    label=arguments.get("label"),
                    limit=requested_limit,
                    offset=offset,
                )
                return _text(
                    {
                        "issues": [i.to_dict() for i in issues],
                        "limit": requested_limit,
                        "offset": offset,
                        "has_more": False,
                    }
                )

            effective_limit = min(requested_limit, _MAX_LIST_RESULTS)
            # Overfetch by 1 to detect whether more results exist
            issues = tracker.list_issues(
                status=status_filter,
                type=arguments.get("type"),
                priority=arguments.get("priority"),
                parent_id=arguments.get("parent_id"),
                assignee=arguments.get("assignee"),
                label=arguments.get("label"),
                limit=effective_limit + 1,
                offset=offset,
            )
            has_more = len(issues) > effective_limit
            if has_more:
                issues = issues[:effective_limit]
            return _text(
                {
                    "issues": [i.to_dict() for i in issues],
                    "limit": effective_limit,
                    "offset": offset,
                    "has_more": has_more,
                }
            )

        case "create_issue":
            try:
                issue = tracker.create_issue(
                    arguments["title"],
                    type=arguments.get("type", "task"),
                    priority=arguments.get("priority", 2),
                    parent_id=arguments.get("parent_id"),
                    description=arguments.get("description", ""),
                    notes=arguments.get("notes", ""),
                    fields=arguments.get("fields"),
                    labels=arguments.get("labels"),
                    deps=arguments.get("deps"),
                    actor=arguments.get("actor", "mcp"),
                )
            except ValueError as e:
                return _text({"error": str(e), "code": "validation_error"})
            _refresh_summary()
            return _text(issue.to_dict())

        case "update_issue":
            try:
                before = tracker.get_issue(arguments["id"])
                issue = tracker.update_issue(
                    arguments["id"],
                    status=arguments.get("status"),
                    priority=arguments.get("priority"),
                    title=arguments.get("title"),
                    assignee=arguments.get("assignee"),
                    description=arguments.get("description"),
                    notes=arguments.get("notes"),
                    parent_id=arguments.get("parent_id"),
                    fields=arguments.get("fields"),
                    actor=arguments.get("actor", "mcp"),
                )
                _refresh_summary()
                result = issue.to_dict()
                # Compute changed fields for agent DX
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
                result["changed_fields"] = changed
                return _text(result)
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['id']}", "code": "not_found"})
            except ValueError as e:
                error_data: dict[str, Any] = {"error": str(e), "code": "invalid_transition"}
                try:
                    transitions = tracker.get_valid_transitions(arguments["id"])
                    error_data["valid_transitions"] = [
                        {"to": t.to, "category": t.category, "ready": t.ready} for t in transitions
                    ]
                    error_data["hint"] = "Use get_valid_transitions to see allowed state changes"
                except KeyError:
                    pass
                return _text(error_data)

        case "close_issue":
            try:
                ready_before = {i.id for i in tracker.get_ready()}
                issue = tracker.close_issue(
                    arguments["id"],
                    reason=arguments.get("reason", ""),
                    actor=arguments.get("actor", "mcp"),
                )
                _refresh_summary()
                ready_after = tracker.get_ready()
                newly_unblocked = [i for i in ready_after if i.id not in ready_before]
                result = issue.to_dict()
                if newly_unblocked:
                    result["newly_unblocked"] = [
                        {"id": i.id, "title": i.title, "priority": i.priority, "type": i.type} for i in newly_unblocked
                    ]
                return _text(result)
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['id']}", "code": "not_found"})
            except ValueError as e:
                error_data = {"error": str(e), "code": "invalid_transition"}
                try:
                    transitions = tracker.get_valid_transitions(arguments["id"])
                    error_data["valid_transitions"] = [
                        {"to": t.to, "category": t.category, "ready": t.ready} for t in transitions
                    ]
                    error_data["hint"] = "Use get_valid_transitions to see allowed state changes"
                except KeyError:
                    pass
                return _text(error_data)

        case "reopen_issue":
            try:
                issue = tracker.reopen_issue(
                    arguments["id"],
                    actor=arguments.get("actor", "mcp"),
                )
                _refresh_summary()
                return _text(issue.to_dict())
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['id']}", "code": "not_found"})
            except ValueError as e:
                return _text({"error": str(e), "code": "invalid"})

        case "add_dependency":
            try:
                added = tracker.add_dependency(
                    arguments["from_id"],
                    arguments["to_id"],
                    actor=arguments.get("actor", "mcp"),
                )
            except (ValueError, KeyError) as e:
                return _text({"error": str(e), "code": "invalid"})
            _refresh_summary()
            status = "added" if added else "already_exists"
            return _text({"status": status, "from_id": arguments["from_id"], "to_id": arguments["to_id"]})

        case "remove_dependency":
            removed = tracker.remove_dependency(
                arguments["from_id"],
                arguments["to_id"],
                actor=arguments.get("actor", "mcp"),
            )
            _refresh_summary()
            status = "removed" if removed else "not_found"
            return _text({"status": status, "from_id": arguments["from_id"], "to_id": arguments["to_id"]})

        case "get_ready":
            issues = tracker.get_ready()
            return _text([{"id": i.id, "title": i.title, "priority": i.priority, "type": i.type} for i in issues])

        case "get_blocked":
            issues = tracker.get_blocked()
            return _text(
                [
                    {"id": i.id, "title": i.title, "priority": i.priority, "type": i.type, "blocked_by": i.blocked_by}
                    for i in issues
                ]
            )

        case "get_plan":
            try:
                plan_data = tracker.get_plan(arguments["milestone_id"])
                # Add overall progress percentage
                total = plan_data.get("total_steps", 0)
                completed = plan_data.get("completed_steps", 0)
                plan_data["progress_pct"] = round(completed / total * 100, 1) if total > 0 else 0.0
                return _text(plan_data)
            except KeyError:
                return _text({"error": f"Milestone not found: {arguments['milestone_id']}", "code": "not_found"})

        case "add_comment":
            try:
                tracker.get_issue(arguments["issue_id"])
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})
            try:
                comment_id = tracker.add_comment(
                    arguments["issue_id"],
                    arguments["text"],
                    author=arguments.get("actor", "mcp"),
                )
            except ValueError as e:
                return _text({"error": str(e), "code": "validation_error"})
            return _text({"status": "ok", "comment_id": comment_id})

        case "get_comments":
            try:
                tracker.get_issue(arguments["issue_id"])
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})
            comments = tracker.get_comments(arguments["issue_id"])
            return _text(comments)

        case "search_issues":
            no_limit = arguments.get("no_limit", False)
            requested_limit = arguments.get("limit", 100)
            offset = arguments.get("offset", 0)

            def _slim(i: Any) -> dict[str, Any]:
                return {"id": i.id, "title": i.title, "status": i.status, "priority": i.priority, "type": i.type}

            if no_limit:
                issues = tracker.search_issues(
                    arguments["query"],
                    limit=requested_limit,
                    offset=offset,
                )
                return _text(
                    {
                        "issues": [_slim(i) for i in issues],
                        "limit": requested_limit,
                        "offset": offset,
                        "has_more": False,
                    }
                )

            effective_limit = min(requested_limit, _MAX_LIST_RESULTS)
            issues = tracker.search_issues(
                arguments["query"],
                limit=effective_limit + 1,
                offset=offset,
            )
            has_more = len(issues) > effective_limit
            if has_more:
                issues = issues[:effective_limit]
            return _text(
                {
                    "issues": [_slim(i) for i in issues],
                    "limit": effective_limit,
                    "offset": offset,
                    "has_more": has_more,
                }
            )

        case "get_template":
            tpl = tracker.get_template(arguments["type"])
            if tpl is None:
                return _text({"error": f"Unknown template: {arguments['type']}", "code": "not_found"})
            return _text(tpl)

        case "add_label":
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

        case "remove_label":
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

        case "claim_issue":
            try:
                issue = tracker.claim_issue(
                    arguments["id"],
                    assignee=arguments["assignee"],
                    actor=arguments.get("actor", arguments["assignee"]),
                )
                _refresh_summary()
                return _text(issue.to_dict())
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['id']}", "code": "not_found"})
            except ValueError as e:
                return _text({"error": str(e), "code": "conflict"})

        case "get_changes":
            events = tracker.get_events_since(
                arguments["since"],
                limit=arguments.get("limit", 100),
            )
            return _text(events)

        case "create_plan":
            try:
                plan = tracker.create_plan(
                    arguments["milestone"],
                    arguments["phases"],
                    actor=arguments.get("actor", "mcp"),
                )
                _refresh_summary()
                return _text(plan)
            except (KeyError, IndexError, ValueError) as e:
                return _text({"error": str(e), "code": "invalid"})

        case "batch_close":
            ready_before = {i.id for i in tracker.get_ready()}
            succeeded: list[str] = []
            failed: list[dict[str, Any]] = []
            warnings: list[str] = []
            for issue_id in arguments["ids"]:
                try:
                    issue = tracker.close_issue(
                        issue_id,
                        reason=arguments.get("reason", ""),
                        actor=arguments.get("actor", "mcp"),
                    )
                    succeeded.append(issue.id)
                except KeyError:
                    failed.append({"id": issue_id, "error": f"Issue not found: {issue_id}", "code": "not_found"})
                except ValueError as e:
                    fail_data: dict[str, Any] = {"id": issue_id, "error": str(e), "code": "invalid_transition"}
                    try:
                        transitions = tracker.get_valid_transitions(issue_id)
                        fail_data["valid_transitions"] = [{"to": t.to, "category": t.category} for t in transitions]
                    except KeyError:
                        pass
                    failed.append(fail_data)
            _refresh_summary()
            ready_after = tracker.get_ready()
            newly_unblocked = [i for i in ready_after if i.id not in ready_before]
            batch_result: dict[str, Any] = {
                "succeeded": succeeded,
                "failed": failed,
                "warnings": warnings,
                "count": len(succeeded),
            }
            if newly_unblocked:
                batch_result["newly_unblocked"] = [
                    {"id": i.id, "title": i.title, "priority": i.priority, "type": i.type} for i in newly_unblocked
                ]
            return _text(batch_result)

        case "batch_update":
            update_succeeded: list[str] = []
            update_failed: list[dict[str, Any]] = []
            update_warnings: list[str] = []
            for issue_id in arguments["ids"]:
                try:
                    issue = tracker.update_issue(
                        issue_id,
                        status=arguments.get("status"),
                        priority=arguments.get("priority"),
                        assignee=arguments.get("assignee"),
                        fields=arguments.get("fields"),
                        actor=arguments.get("actor", "mcp"),
                    )
                    update_succeeded.append(issue.id)
                except KeyError:
                    update_failed.append({"id": issue_id, "error": f"Issue not found: {issue_id}", "code": "not_found"})
                except ValueError as e:
                    ufail: dict[str, Any] = {"id": issue_id, "error": str(e), "code": "invalid_transition"}
                    try:
                        transitions = tracker.get_valid_transitions(issue_id)
                        ufail["valid_transitions"] = [{"to": t.to, "category": t.category} for t in transitions]
                    except KeyError:
                        pass
                    update_failed.append(ufail)
            _refresh_summary()
            return _text(
                {
                    "succeeded": update_succeeded,
                    "failed": update_failed,
                    "warnings": update_warnings,
                    "count": len(update_succeeded),
                }
            )

        case "get_summary":
            summary = generate_summary(tracker)
            return _text(summary)

        case "get_stats":
            return _text(tracker.get_stats())

        case "get_metrics":
            from filigree.analytics import get_flow_metrics

            return _text(get_flow_metrics(tracker, days=arguments.get("days", 30)))

        case "get_critical_path":
            path = tracker.get_critical_path()
            return _text({"path": path, "length": len(path)})

        case "release_claim":
            try:
                issue = tracker.release_claim(arguments["id"], actor=arguments.get("actor", "mcp"))
                _refresh_summary()
                return _text(issue.to_dict())
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['id']}", "code": "not_found"})
            except ValueError as e:
                return _text({"error": str(e), "code": "conflict"})

        case "export_jsonl":
            try:
                safe = _safe_path(arguments["output_path"])
                count = tracker.export_jsonl(safe)
                return _text({"status": "ok", "records": count, "path": str(safe)})
            except ValueError as e:
                return _text({"error": str(e), "code": "invalid_path"})

        case "import_jsonl":
            try:
                safe = _safe_path(arguments["input_path"])
                count = tracker.import_jsonl(safe, merge=arguments.get("merge", False))
                _refresh_summary()
                return _text({"status": "ok", "records": count, "path": str(safe)})
            except ValueError as e:
                return _text({"error": str(e), "code": "invalid_path"})
            except Exception as e:
                return _text({"error": str(e), "code": "invalid"})

        case "archive_closed":
            archived = tracker.archive_closed(
                days_old=arguments.get("days_old", 30),
                actor=arguments.get("actor", "mcp"),
            )
            _refresh_summary()
            return _text({"status": "ok", "archived_count": len(archived), "archived_ids": archived})

        case "compact_events":
            deleted = tracker.compact_events(keep_recent=arguments.get("keep_recent", 50))
            return _text({"status": "ok", "events_deleted": deleted})

        case "get_workflow_states":
            return _text(
                {
                    "states": {
                        "open": tracker._get_states_for_category("open"),
                        "wip": tracker._get_states_for_category("wip"),
                        "done": tracker._get_states_for_category("done"),
                    }
                }
            )

        case "undo_last":
            try:
                result = tracker.undo_last(arguments["id"], actor=arguments.get("actor", "mcp"))
                if result["undone"]:
                    _refresh_summary()
                return _text(result)
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['id']}", "code": "not_found"})

        case "get_issue_events":
            try:
                events = tracker.get_issue_events(
                    arguments["issue_id"],
                    limit=arguments.get("limit", 50),
                )
                return _text(events)
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})

        # -- Workflow template tools --

        case "list_types":
            types_list = []
            for tt in tracker.templates.list_types():
                types_list.append(
                    {
                        "type": tt.type,
                        "display_name": tt.display_name,
                        "description": tt.description,
                        "pack": tt.pack,
                        "states": [{"name": s.name, "category": s.category} for s in tt.states],
                        "initial_state": tt.initial_state,
                    }
                )
            return _text(sorted(types_list, key=lambda t: t["type"]))

        case "get_type_info":
            type_tpl = tracker.templates.get_type(arguments["type"])
            if type_tpl is None:
                return _text({"error": f"Unknown type: {arguments['type']}", "code": "not_found"})
            return _text(
                {
                    "type": type_tpl.type,
                    "display_name": type_tpl.display_name,
                    "description": type_tpl.description,
                    "pack": type_tpl.pack,
                    "states": [{"name": s.name, "category": s.category} for s in type_tpl.states],
                    "initial_state": type_tpl.initial_state,
                    "transitions": [
                        {
                            "from": td.from_state,
                            "to": td.to_state,
                            "enforcement": td.enforcement,
                            "requires_fields": list(td.requires_fields),
                        }
                        for td in type_tpl.transitions
                    ],
                    "fields_schema": [
                        {
                            "name": fd.name,
                            "type": fd.type,
                            "description": fd.description,
                            **({"options": list(fd.options)} if fd.options else {}),
                            **({"default": fd.default} if fd.default is not None else {}),
                            **({"required_at": list(fd.required_at)} if fd.required_at else {}),
                        }
                        for fd in type_tpl.fields_schema
                    ],
                }
            )

        case "list_packs":
            packs_list = []
            for pack in tracker.templates.list_packs():
                packs_list.append(
                    {
                        "pack": pack.pack,
                        "version": pack.version,
                        "display_name": pack.display_name,
                        "description": pack.description,
                        "types": sorted(pack.types.keys()),
                        "requires_packs": list(pack.requires_packs),
                    }
                )
            return _text(sorted(packs_list, key=lambda p: p["pack"]))

        case "get_valid_transitions":
            try:
                transitions = tracker.get_valid_transitions(arguments["issue_id"])
                issue = tracker.get_issue(arguments["issue_id"])
                tpl_data = tracker.get_template(issue.type)
                field_schemas = {f["name"]: f for f in (tpl_data or {}).get("fields_schema", [])}
                return _text(
                    [
                        {
                            "to": t.to,
                            "category": t.category,
                            "enforcement": t.enforcement,
                            "requires_fields": list(t.requires_fields),
                            "missing_fields": [
                                {
                                    "name": f,
                                    **{k: v for k, v in field_schemas.get(f, {}).items() if k != "name"},
                                }
                                for f in t.missing_fields
                            ],
                            "ready": t.ready,
                        }
                        for t in transitions
                    ]
                )
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})

        case "validate_issue":
            try:
                val_result = tracker.validate_issue(arguments["issue_id"])
                return _text(
                    {
                        "valid": val_result.valid,
                        "warnings": list(val_result.warnings),
                        "errors": list(val_result.errors),
                    }
                )
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})

        case "get_workflow_guide":
            wf_pack = tracker.templates.get_pack(arguments["pack"])
            if wf_pack is None:
                # Check if the user passed a type name instead of a pack name
                type_tpl = tracker.templates.get_type(arguments["pack"])
                if type_tpl is not None:
                    wf_pack = tracker.templates.get_pack(type_tpl.pack)
                    if wf_pack is not None:
                        if wf_pack.guide is None:
                            return _text(
                                {"pack": wf_pack.pack, "guide": None, "message": "No guide available for this pack"}
                            )
                        return _text(
                            {
                                "pack": wf_pack.pack,
                                "guide": wf_pack.guide,
                                "note": f"Resolved type '{arguments['pack']}' to pack '{wf_pack.pack}'",
                            }
                        )
                return _text(
                    {
                        "error": f"Unknown pack: '{arguments['pack']}'. Use list_packs to see available packs, or list_types to see types.",
                        "code": "not_found",
                    }
                )
            if wf_pack.guide is None:
                return _text({"pack": wf_pack.pack, "guide": None, "message": "No guide available for this pack"})
            return _text({"pack": wf_pack.pack, "guide": wf_pack.guide})

        case "explain_state":
            state_tpl = tracker.templates.get_type(arguments["type"])
            if state_tpl is None:
                return _text({"error": f"Unknown type: {arguments['type']}", "code": "not_found"})
            state_name = arguments["state"]
            state_def = None
            for s in state_tpl.states:
                if s.name == state_name:
                    state_def = s
                    break
            if state_def is None:
                return _text(
                    {"error": f"Unknown state '{state_name}' for type '{arguments['type']}'", "code": "not_found"}
                )
            inbound = [
                {"from": td.from_state, "enforcement": td.enforcement}
                for td in state_tpl.transitions
                if td.to_state == state_name
            ]
            outbound = [
                {"to": td.to_state, "enforcement": td.enforcement, "requires_fields": list(td.requires_fields)}
                for td in state_tpl.transitions
                if td.from_state == state_name
            ]
            required_fields = [fd.name for fd in state_tpl.fields_schema if state_name in fd.required_at]
            return _text(
                {
                    "state": state_name,
                    "category": state_def.category,
                    "type": arguments["type"],
                    "inbound_transitions": inbound,
                    "outbound_transitions": outbound,
                    "required_fields": required_fields,
                }
            )

        case "reload_templates":
            tracker.reload_templates()
            return _text({"status": "ok"})

        case "claim_next":
            claimed = tracker.claim_next(
                arguments["assignee"],
                type_filter=arguments.get("type"),
                priority_min=arguments.get("priority_min"),
                priority_max=arguments.get("priority_max"),
                actor=arguments.get("actor", arguments["assignee"]),
            )
            if claimed is None:
                return _text({"status": "empty", "reason": "No ready issues matching filters"})
            _refresh_summary()
            result = claimed.to_dict()
            parts = [f"P{claimed.priority}"]
            if claimed.type != "task":
                parts.append(f"type={claimed.type}")
            parts.append("ready issue (no blockers)")
            result["selection_reason"] = f"Highest-priority {', '.join(parts)}"
            return _text(result)

        case _:
            return _text({"error": f"Unknown tool: {name}", "code": "unknown_tool"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run(project_path: Path | None) -> None:
    global db, _filigree_dir, _logger

    if project_path:
        filigree_dir = project_path / FILIGREE_DIR_NAME
        if not filigree_dir.is_dir():
            print(f"Error: {filigree_dir} not found. Run 'filigree init' first.", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            filigree_dir = find_filigree_root()
        except FileNotFoundError:
            print(f"Error: No {FILIGREE_DIR_NAME}/ found. Run 'filigree init' first.", file=sys.stderr)
            sys.exit(1)

    _filigree_dir = filigree_dir
    config = read_config(filigree_dir)
    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=config.get("prefix", "filigree"))
    db.initialize()

    from filigree.logging import setup_logging

    _logger = setup_logging(filigree_dir)
    _logger.info("mcp_server_start", extra={"tool": "server", "args_data": {"project": str(filigree_dir.parent)}})

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    import asyncio

    parser = argparse.ArgumentParser(description="Filigree MCP server")
    parser.add_argument(
        "--project", type=Path, default=None, help="Project root (auto-discovers .filigree/ if omitted)"
    )
    args = parser.parse_args()

    asyncio.run(_run(args.project))


if __name__ == "__main__":
    main()
