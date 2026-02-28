"""MCP tools for file tracking, associations, scanners, and scan triggering."""

from __future__ import annotations

import asyncio
import secrets
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

from filigree.core import VALID_ASSOC_TYPES, VALID_SEVERITIES
from filigree.mcp_tools.common import _parse_args, _text, _validate_int_range, _validate_str
from filigree.types.inputs import (
    AddFileAssociationArgs,
    GetFileArgs,
    GetFileTimelineArgs,
    GetIssueFilesArgs,
    ListFilesArgs,
    RegisterFileArgs,
    TriggerScanArgs,
)
from filigree.scanners import list_scanners as _list_scanners
from filigree.scanners import load_scanner, validate_scanner_command


def register() -> tuple[list[Tool], dict[str, Callable[..., Any]]]:
    """Return (tool_definitions, handler_map) for file-domain tools."""
    tools = [
        Tool(
            name="list_files",
            description="List tracked files with filtering, sorting, and pagination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 10000},
                    "offset": {"type": "integer", "default": 0, "minimum": 0},
                    "language": {"type": "string", "description": "Filter by language"},
                    "path_prefix": {"type": "string", "description": "Filter by substring in file path"},
                    "min_findings": {"type": "integer", "minimum": 0, "description": "Minimum open findings count"},
                    "has_severity": {
                        "type": "string",
                        "enum": sorted(VALID_SEVERITIES),
                        "description": "Require at least one open finding at this severity",
                    },
                    "scan_source": {"type": "string", "description": "Filter files by finding source"},
                    "sort": {
                        "type": "string",
                        "enum": ["updated_at", "first_seen", "path", "language"],
                        "default": "updated_at",
                    },
                    "direction": {"type": "string", "enum": ["asc", "desc", "ASC", "DESC"]},
                },
            },
        ),
        Tool(
            name="get_file",
            description="Get file details, linked issues, recent findings, and summary by file ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "File ID"},
                },
                "required": ["file_id"],
            },
        ),
        Tool(
            name="get_file_timeline",
            description="Get merged timeline events for a file (finding, association, metadata updates).",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "File ID"},
                    "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 10000},
                    "offset": {"type": "integer", "default": 0, "minimum": 0},
                    "event_type": {
                        "type": "string",
                        "enum": ["finding", "association", "file_metadata_update"],
                        "description": "Optional event type filter",
                    },
                },
                "required": ["file_id"],
            },
        ),
        Tool(
            name="get_issue_files",
            description="List files associated with an issue.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="add_file_association",
            description="Create a file<->issue association. Idempotent for duplicate tuples.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "File ID"},
                    "issue_id": {"type": "string", "description": "Issue ID"},
                    "assoc_type": {
                        "type": "string",
                        "enum": sorted(VALID_ASSOC_TYPES),
                        "description": "Association type",
                    },
                },
                "required": ["file_id", "issue_id", "assoc_type"],
            },
        ),
        Tool(
            name="register_file",
            description="Register or fetch a file record by project-relative path without running a scanner.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (relative to project root)"},
                    "language": {"type": "string", "description": "Optional language hint"},
                    "file_type": {"type": "string", "description": "Optional file type tag"},
                    "metadata": {"type": "object", "description": "Optional metadata map"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="list_scanners",
            description="List registered scanners from .filigree/scanners/*.toml. Returns available scanner names, descriptions, and supported file types.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="trigger_scan",
            description=(
                "Trigger an async bug scan on a file. Registers the file, spawns a detached scanner process, "
                "and returns immediately with a scan_run_id for correlation. Check file findings later for results. "
                "Note: results are POSTed to the dashboard API — ensure the dashboard is running at the target api_url. "
                "Repeated triggers for the same scanner+file are rate-limited (30s cooldown)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "scanner": {"type": "string", "description": "Scanner name (from list_scanners)"},
                    "file_path": {"type": "string", "description": "File path to scan (relative to project root)"},
                    "api_url": {
                        "type": "string",
                        "default": "http://localhost:8377",
                        "description": "Dashboard URL where scanner POSTs results (localhost only by default)",
                    },
                },
                "required": ["scanner", "file_path"],
            },
        ),
    ]

    handlers: dict[str, Callable[..., Any]] = {
        "list_files": _handle_list_files,
        "get_file": _handle_get_file,
        "get_file_timeline": _handle_get_file_timeline,
        "get_issue_files": _handle_get_issue_files,
        "add_file_association": _handle_add_file_association,
        "register_file": _handle_register_file,
        "list_scanners": _handle_list_scanners,
        "trigger_scan": _handle_trigger_scan,
    }

    return tools, handlers


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_list_files(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, ListFilesArgs)
    tracker = _get_db()
    limit = args.get("limit", 100)
    offset = args.get("offset", 0)
    min_findings = args.get("min_findings")
    has_severity = args.get("has_severity")
    language = args.get("language")
    path_prefix = args.get("path_prefix")
    scan_source = args.get("scan_source")
    sort = args.get("sort", "updated_at")
    direction = args.get("direction")
    valid_sorts = {"updated_at", "first_seen", "path", "language"}

    for err in (
        _validate_int_range(limit, "limit", min_val=1, max_val=10000),
        _validate_int_range(offset, "offset", min_val=0),
        _validate_int_range(min_findings, "min_findings", min_val=0),
        _validate_str(language, "language"),
        _validate_str(path_prefix, "path_prefix"),
        _validate_str(scan_source, "scan_source"),
    ):
        if err is not None:
            return err
    if has_severity is not None and (not isinstance(has_severity, str) or has_severity not in VALID_SEVERITIES):
        return _text({"error": f"has_severity must be one of {sorted(VALID_SEVERITIES)}", "code": "validation_error"})
    if not isinstance(sort, str) or sort not in valid_sorts:
        return _text({"error": f"sort must be one of {sorted(valid_sorts)}", "code": "validation_error"})
    if direction is not None and (not isinstance(direction, str) or direction.upper() not in {"ASC", "DESC"}):
        return _text({"error": "direction must be 'asc' or 'desc'", "code": "validation_error"})

    files_result = tracker.list_files_paginated(
        limit=limit,
        offset=offset,
        language=language,
        path_prefix=path_prefix,
        min_findings=min_findings,
        has_severity=has_severity,
        scan_source=scan_source,
        sort=sort,
        direction=direction,
    )
    return _text(files_result)


async def _handle_get_file(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, GetFileArgs)
    tracker = _get_db()
    file_id = args.get("file_id", "")
    if not isinstance(file_id, str) or not file_id.strip():
        return _text({"error": "file_id is required", "code": "validation_error"})
    try:
        data = tracker.get_file_detail(file_id)
    except KeyError:
        return _text({"error": f"File not found: {file_id}", "code": "not_found"})
    return _text(data)


async def _handle_get_file_timeline(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, GetFileTimelineArgs)
    tracker = _get_db()
    file_id = args.get("file_id", "")
    limit = args.get("limit", 50)
    offset = args.get("offset", 0)
    event_type = args.get("event_type")
    valid_event_types = {"finding", "association", "file_metadata_update"}

    if not isinstance(file_id, str) or not file_id.strip():
        return _text({"error": "file_id is required", "code": "validation_error"})
    if not isinstance(limit, int) or limit < 1 or limit > 10000:
        return _text({"error": "limit must be an integer in [1, 10000]", "code": "validation_error"})
    if not isinstance(offset, int) or offset < 0:
        return _text({"error": "offset must be a non-negative integer", "code": "validation_error"})
    if event_type is not None and (not isinstance(event_type, str) or event_type not in valid_event_types):
        return _text(
            {
                "error": f"event_type must be one of {sorted(valid_event_types)}",
                "code": "validation_error",
            }
        )

    try:
        timeline_result = tracker.get_file_timeline(file_id, limit=limit, offset=offset, event_type=event_type)
    except KeyError:
        return _text({"error": f"File not found: {file_id}", "code": "not_found"})
    return _text(timeline_result)


async def _handle_get_issue_files(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, GetIssueFilesArgs)
    tracker = _get_db()
    issue_id = args.get("issue_id", "")
    if not isinstance(issue_id, str) or not issue_id.strip():
        return _text({"error": "issue_id is required", "code": "validation_error"})
    try:
        tracker.get_issue(issue_id)
    except KeyError:
        return _text({"error": f"Issue not found: {issue_id}", "code": "not_found"})
    return _text(tracker.get_issue_files(issue_id))


async def _handle_add_file_association(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, AddFileAssociationArgs)
    tracker = _get_db()
    file_id = args.get("file_id", "")
    issue_id = args.get("issue_id", "")
    assoc_type = args.get("assoc_type", "")

    if not isinstance(file_id, str) or not file_id.strip():
        return _text({"error": "file_id is required", "code": "validation_error"})
    if not isinstance(issue_id, str) or not issue_id.strip():
        return _text({"error": "issue_id is required", "code": "validation_error"})
    if not isinstance(assoc_type, str) or not assoc_type.strip():
        return _text({"error": "assoc_type is required", "code": "validation_error"})

    try:
        tracker.get_file(file_id)
    except KeyError:
        return _text({"error": f"File not found: {file_id}", "code": "not_found"})

    try:
        tracker.get_issue(issue_id)
    except KeyError:
        return _text({"error": f"Issue not found: {issue_id}", "code": "not_found"})

    try:
        tracker.add_file_association(file_id, issue_id, assoc_type)
    except ValueError as e:
        return _text({"error": str(e), "code": "validation_error"})
    return _text({"status": "created"})


async def _handle_register_file(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db, _get_filigree_dir, _safe_path

    args = _parse_args(arguments, RegisterFileArgs)
    tracker = _get_db()
    raw_path = args.get("path", "")
    language = args.get("language", "")
    file_type = args.get("file_type", "")
    metadata = args.get("metadata")

    if not isinstance(raw_path, str) or not raw_path.strip():
        return _text({"error": "path is required", "code": "validation_error"})
    if language is not None and not isinstance(language, str):
        return _text({"error": "language must be a string", "code": "validation_error"})
    if file_type is not None and not isinstance(file_type, str):
        return _text({"error": "file_type must be a string", "code": "validation_error"})
    if metadata is not None and not isinstance(metadata, dict):
        return _text({"error": "metadata must be an object", "code": "validation_error"})

    try:
        target = _safe_path(raw_path)
    except ValueError as e:
        return _text({"error": str(e), "code": "invalid_path"})

    filigree_dir = _get_filigree_dir()
    if filigree_dir is None:
        return _text({"error": "Project directory not initialized", "code": "not_initialized"})

    canonical_path = str(target.relative_to(filigree_dir.resolve().parent))
    file_record = tracker.register_file(
        canonical_path,
        language=language or "",
        file_type=file_type or "",
        metadata=metadata,
    )
    return _text(file_record.to_dict())


async def _handle_list_scanners(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_filigree_dir

    filigree_dir = _get_filigree_dir()
    scanners_dir = filigree_dir / "scanners" if filigree_dir else None
    if scanners_dir is None:
        return _text({"scanners": [], "hint": "Project directory not initialized"})
    scanners = _list_scanners(scanners_dir)
    result_data: dict[str, Any] = {"scanners": [s.to_dict() for s in scanners]}
    if not scanners:
        result_data["hint"] = "No scanners registered. Add TOML files to .filigree/scanners/"
    return _text(result_data)


async def _handle_trigger_scan(arguments: dict[str, Any]) -> list[TextContent]:
    from datetime import UTC, datetime
    from urllib.parse import urlparse

    from filigree.mcp_server import (
        _SCAN_COOLDOWN_SECONDS,
        _get_db,
        _get_filigree_dir,
        _logger,
        _safe_path,
        _scan_cooldowns,
    )

    filigree_dir = _get_filigree_dir()
    if filigree_dir is None:
        return _text({"error": "Project directory not initialized", "code": "not_initialized"})

    args = _parse_args(arguments, TriggerScanArgs)
    tracker = _get_db()
    scanner_name = args["scanner"]
    file_path = args["file_path"]
    api_url = args.get("api_url", "http://localhost:8377")

    parsed_url = urlparse(api_url)
    url_host = parsed_url.hostname or ""
    if url_host not in ("localhost", "127.0.0.1", "::1", ""):
        return _text(
            {
                "error": f"Non-localhost api_url not allowed: {url_host!r}. Scanner results would be sent to an external host.",
                "code": "invalid_api_url",
            }
        )

    try:
        target = _safe_path(file_path)
    except ValueError as e:
        return _text({"error": str(e), "code": "invalid_path"})

    scanners_dir = filigree_dir / "scanners"
    cfg = load_scanner(scanners_dir, scanner_name)
    if cfg is None:
        available = [s.name for s in _list_scanners(scanners_dir)]
        return _text(
            {
                "error": f"Scanner {scanner_name!r} not found",
                "code": "scanner_not_found",
                "available_scanners": available,
            }
        )

    if not target.is_file():
        return _text(
            {
                "error": f"File not found: {file_path}",
                "code": "file_not_found",
            }
        )

    file_type_warning = ""
    if cfg.file_types:
        ext = Path(file_path).suffix.lstrip(".")
        if ext and ext not in cfg.file_types:
            file_type_warning = f"Warning: file extension {ext!r} not in scanner's declared file_types {cfg.file_types}. Proceeding anyway."

    canonical_path = str(target.relative_to(filigree_dir.resolve().parent))
    project_scope = str(tracker.db_path.parent.resolve())
    cooldown_key = (project_scope, scanner_name, canonical_path)
    now_mono = time.monotonic()
    stale = [k for k, v in _scan_cooldowns.items() if now_mono - v >= _SCAN_COOLDOWN_SECONDS]
    for k in stale:
        del _scan_cooldowns[k]
    last_trigger = _scan_cooldowns.get(cooldown_key, 0.0)
    if now_mono - last_trigger < _SCAN_COOLDOWN_SECONDS:
        remaining = _SCAN_COOLDOWN_SECONDS - (now_mono - last_trigger)
        return _text(
            {
                "error": f"Scanner {scanner_name!r} was already triggered for {file_path!r} recently. Wait {remaining:.0f}s.",
                "code": "rate_limited",
                "retry_after_seconds": round(remaining),
            }
        )

    # Reserve cooldown BEFORE any await points to prevent concurrent
    # calls from bypassing rate limiting (filigree-5bee22).
    _scan_cooldowns[cooldown_key] = now_mono

    project_root = filigree_dir.parent
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    scan_run_id = f"{scanner_name}-{ts}-{secrets.token_hex(3)}"
    try:
        cmd = cfg.build_command(
            file_path=canonical_path,
            api_url=api_url,
            project_root=str(project_root),
            scan_run_id=scan_run_id,
        )
    except ValueError as e:
        del _scan_cooldowns[cooldown_key]
        return _text({"error": str(e), "code": "invalid_command"})

    cmd_err = validate_scanner_command(cmd, project_root=project_root)
    if cmd_err is not None:
        del _scan_cooldowns[cooldown_key]
        return _text({"error": cmd_err, "code": "command_not_found"})

    file_record = tracker.register_file(canonical_path)

    scan_log_dir = filigree_dir / "scans"
    scan_log_dir.mkdir(parents=True, exist_ok=True)
    scan_log_path = scan_log_dir / f"{scan_run_id}.log"
    try:
        scan_log_fd = open(scan_log_path, "w")  # noqa: SIM115
    except OSError:
        scan_log_fd = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=scan_log_fd if scan_log_fd is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        del _scan_cooldowns[cooldown_key]
        return _text(
            {
                "error": f"Failed to spawn scanner process: {e}",
                "code": "spawn_failed",
                "scanner": scanner_name,
                "file_id": file_record.id,
            }
        )
    finally:
        if scan_log_fd is not None:
            scan_log_fd.close()

    await asyncio.sleep(0.2)
    exit_code = proc.poll()
    if exit_code is not None and exit_code != 0:
        log_hint = ""
        if scan_log_path.exists():
            log_hint = f" Check log: {scan_log_path.relative_to(filigree_dir.parent)}"
        return _text(
            {
                "error": f"Scanner process exited immediately with code {exit_code}.{log_hint}",
                "code": "spawn_failed",
                "scanner": scanner_name,
                "file_id": file_record.id,
                "exit_code": exit_code,
                "log_path": str(scan_log_path.relative_to(filigree_dir.parent)),
            }
        )

    if _logger:
        _logger.info(
            "Spawned scanner %s for %s (pid=%d, run_id=%s)",
            scanner_name,
            file_path,
            proc.pid,
            scan_run_id,
        )

    log_rel = str(scan_log_path.relative_to(filigree_dir.parent))
    scan_result: dict[str, Any] = {
        "status": "triggered",
        "scanner": scanner_name,
        "file_path": file_path,
        "file_id": file_record.id,
        "scan_run_id": scan_run_id,
        "pid": proc.pid,
        "log_path": log_rel,
        "message": (
            f"Scan triggered with run_id={scan_run_id!r}. "
            f"Results will be POSTed to {api_url}. "
            f"Poll findings via file_id={file_record.id!r}. "
            f"Scanner log: {log_rel}"
        ),
    }
    if file_type_warning:
        scan_result["warning"] = file_type_warning
    return _text(scan_result)
