"""MCP server for the filigree issue tracker.

Primary interface for agents. Direct SQLite in stdio mode (no daemon).
Also mountable as streamable-HTTP handler inside the dashboard daemon for server mode.
Exposes filigree operations as MCP tools.

Usage:
    filigree-mcp                              # Auto-discover .filigree/ from cwd
    filigree-mcp --project /path/to/project   # Explicit project root
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
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
from filigree.mcp_tools.common import (  # noqa: F401  — re-exported for backward compat
    _MAX_LIST_RESULTS,
    _text,
)
from filigree.summary import generate_summary, write_summary

# ---------------------------------------------------------------------------
# Module globals (state accessors depend on these)
# ---------------------------------------------------------------------------

server = Server("filigree")
db: FiligreeDB | None = None
_filigree_dir: Path | None = None
_logger: logging.Logger | None = None
_request_db: ContextVar[FiligreeDB | None] = ContextVar("filigree_request_db", default=None)
_request_filigree_dir: ContextVar[Path | None] = ContextVar("filigree_request_dir", default=None)

# Per-(project, scanner, file) cooldown to prevent unbounded process spawning.
# Maps (project_scope, scanner_name, file_path) -> timestamp of last trigger.
_scan_cooldowns: dict[tuple[str, str, str], float] = {}
_SCAN_COOLDOWN_SECONDS = 30


# ---------------------------------------------------------------------------
# State accessors (used by domain modules via deferred import)
# ---------------------------------------------------------------------------


def _get_db() -> FiligreeDB:
    active_db = _request_db.get() or db
    if active_db is None:
        msg = "Database not initialized"
        raise RuntimeError(msg)
    return active_db


def _get_filigree_dir() -> Path | None:
    return _request_filigree_dir.get() or _filigree_dir


def _refresh_summary() -> None:
    """Regenerate context.md after mutations (best-effort, never fatal)."""
    filigree_dir = _get_filigree_dir()
    if filigree_dir is not None:
        try:
            write_summary(_get_db(), filigree_dir / SUMMARY_FILENAME)
        except OSError:
            (_logger or logging.getLogger(__name__)).warning("Failed to write context.md", exc_info=True)
        except Exception:
            (_logger or logging.getLogger(__name__)).error(
                "Unexpected error refreshing context.md — database may be inconsistent", exc_info=True
            )


def _safe_path(raw: str) -> Path:
    """Resolve a user-supplied path safely within the project root.

    Raises ValueError for paths that escape the project directory.
    """
    if Path(raw).is_absolute():
        msg = f"Absolute paths not allowed: {raw}"
        raise ValueError(msg)

    filigree_dir = _get_filigree_dir()
    if filigree_dir is None:
        msg = "Project directory not initialized"
        raise ValueError(msg)

    # Resolve relative to project root (parent of .filigree/)
    base = filigree_dir.resolve().parent
    resolved = (base / raw).resolve()

    # Ensure resolved path is under the project root
    try:
        resolved.relative_to(base)
    except ValueError:
        msg = f"Path escapes project directory: {raw}"
        raise ValueError(msg) from None

    return resolved


# ---------------------------------------------------------------------------
# Tool aggregation from domain modules
# ---------------------------------------------------------------------------

from filigree.mcp_tools import (  # noqa: E402, I001  — must come after globals
    files as _files_mod,
    issues as _issues_mod,
    meta as _meta_mod,
    planning as _planning_mod,
    workflow as _workflow_mod,
)

_all_tools: list[Tool] = []
_all_handlers: dict[str, Callable[..., Any]] = {}

for _mod in (_issues_mod, _planning_mod, _files_mod, _workflow_mod, _meta_mod):
    _tools, _handlers = _mod.register()
    _all_tools.extend(_tools)
    _all_handlers.update(_handlers)


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
- Issue IDs: `{prefix}-{10hex}` (e.g., `myproj-a3f9b2e1c0`)
- Priorities: P0 (critical) through P4 (low)
- Each type has its own state machine — use `list_types` to discover
- Use `get_valid_transitions <id>` before status changes
"""


def _build_workflow_text() -> str:
    """Build dynamic workflow prompt from template registry if available."""
    if (_request_db.get() or db) is None:
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
    except sqlite3.Error:
        logging.getLogger(__name__).error(
            "Database error building workflow text — database may need repair",
            exc_info=True,
        )
        return (
            _WORKFLOW_TEXT_STATIC + "\n\n> **WARNING:** Database error prevented loading "
            "workflow types. Run `filigree doctor` to diagnose.\n"
        )
    except Exception:
        logging.getLogger(__name__).error(
            "Failed to build dynamic workflow text; falling back to static",
            exc_info=True,
        )
        return (
            _WORKFLOW_TEXT_STATIC + "\n\n> **Note:** Dynamic workflow info unavailable. "
            "Custom types, states, and packs may not be reflected above. "
            "Use `list_types` and `list_packs` for current workflow details.\n"
        )


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
        except RuntimeError as exc:
            if "not initialized" not in str(exc):
                logging.getLogger(__name__).error("Unexpected RuntimeError building prompt context", exc_info=True)
    return GetPromptResult(description="Filigree workflow guide with project context", messages=messages)


# ---------------------------------------------------------------------------
# Tool definitions & dispatch
# ---------------------------------------------------------------------------


@server.list_tools()  # type: ignore[untyped-decorator,no-untyped-call]
async def list_tools() -> list[Tool]:
    return _all_tools


@server.call_tool()  # type: ignore[untyped-decorator]
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    tracker = _get_db()
    t0 = time.monotonic()

    try:
        handler = _all_handlers.get(name)
        if handler is None:
            from filigree.mcp_tools.common import _text as _common_text

            return _common_text({"error": f"Unknown tool: {name}", "code": "unknown_tool"})
        result: list[TextContent] = await handler(arguments)
    except Exception:
        if _logger:
            _logger.error("tool_error", extra={"tool": name, "args_data": arguments}, exc_info=True)
        raise
    else:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        if _logger:
            _logger.info("tool_call", extra={"tool": name, "args_data": arguments, "duration_ms": duration_ms})
        return result
    finally:
        # Safety net: roll back any uncommitted transaction left by a failed
        # mutation.  Successful mutations commit explicitly; only partial
        # failures leave dirty state that would be flushed by the next commit.
        if tracker.conn.in_transaction:
            tracker.conn.rollback()


# ---------------------------------------------------------------------------
# HTTP transport factory (for server-mode dashboard)
# ---------------------------------------------------------------------------


def create_mcp_app(
    db_resolver: Callable[[], FiligreeDB | None] | None = None,
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Create an ASGI app + lifespan hook for MCP streamable-HTTP.

    Returns ``(asgi_app, lifespan_context_manager)`` where:

    * **asgi_app** is an ASGI callable to mount at ``/mcp`` in the
      dashboard.
    * **lifespan_context_manager** is an async-context-manager that
      must be entered during the parent application's lifespan so the
      underlying ``StreamableHTTPSessionManager`` task-group is
      running before the first request arrives.

    ``db_resolver`` — optional callable returning the active
    :class:`FiligreeDB`. When provided, each request gets an isolated
    request-local DB + project directory context.
    """
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=False,
        stateless=True,
    )

    async def _handle_mcp(scope: Any, receive: Any, send: Any) -> None:
        db_token: Any = None
        dir_token: Any = None
        if db_resolver is not None:
            from starlette.responses import JSONResponse

            try:
                resolved = db_resolver()
            except KeyError as exc:
                project_key = str(exc.args[0]) if exc.args else ""
                resp = JSONResponse(
                    {
                        "error": "Unknown project",
                        "code": "project_not_found",
                        "project": project_key,
                    },
                    status_code=404,
                )
                await resp(scope, receive, send)
                return

            if resolved is None:
                resp = JSONResponse(
                    {
                        "error": "Unable to resolve project database",
                        "code": "project_unavailable",
                    },
                    status_code=503,
                )
                await resp(scope, receive, send)
                return
            db_token = _request_db.set(resolved)
            dir_token = _request_filigree_dir.set(resolved.db_path.parent)
        try:
            await session_manager.handle_request(scope, receive, send)
        except RuntimeError:
            # Session manager not started (e.g. lifespan not triggered in
            # test or ethereal mode).  Return 503 so the route is visible
            # but clearly not ready.
            from starlette.responses import JSONResponse

            resp = JSONResponse(
                {"error": "MCP session manager not initialized"},
                status_code=503,
            )
            await resp(scope, receive, send)
        finally:
            if dir_token is not None:
                _request_filigree_dir.reset(dir_token)
            if db_token is not None:
                _request_db.reset(db_token)

    return _handle_mcp, session_manager.run


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
    parser.add_argument("--project", type=Path, default=None, help="Project root (auto-discovers .filigree/ if omitted)")
    args = parser.parse_args()

    asyncio.run(_run(args.project))


if __name__ == "__main__":
    main()
