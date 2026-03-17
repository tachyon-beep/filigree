"""MCP tools for scanner lifecycle — list, trigger, batch trigger, status, preview."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

from filigree.mcp_tools.common import _parse_args, _text, _validate_int_range
from filigree.scanners import list_scanners as _list_scanners
from filigree.scanners import load_scanner, validate_scanner_command
from filigree.types.api import ErrorResponse
from filigree.types.inputs import (
    GetScanStatusArgs,
    PreviewScanArgs,
    TriggerScanArgs,
    TriggerScanBatchArgs,
)

_LOCALHOST_HOSTS = frozenset(("localhost", "127.0.0.1", "::1", ""))

_logger = logging.getLogger(__name__)


def register(
    *,
    include_legacy: bool = False,
) -> tuple[list[Tool], dict[str, Callable[..., Any]]]:
    """Return (tool_definitions, handler_map) for scanner-domain tools.

    When *include_legacy* is True, all 5 scanner tools are returned
    (adds ``list_scanners`` and single-file ``trigger_scan``).  When
    False, only the 3 batch/status/preview tools are returned.
    """
    new_tools = [
        Tool(
            name="trigger_scan_batch",
            description=(
                "Trigger a scanner on multiple files in one call. "
                "Registers all files, spawns one scanner process per file, "
                "and returns a shared scan_run_id for correlation. Rate-limited per scanner+file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "scanner": {"type": "string", "description": "Scanner name (from list_scanners)"},
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File paths to scan (relative to project root)",
                    },
                    "api_url": {
                        "type": "string",
                        "default": "http://localhost:8377",
                        "description": "Dashboard URL where scanner POSTs results",
                    },
                },
                "required": ["scanner", "file_paths"],
            },
        ),
        Tool(
            name="get_scan_status",
            description="Get the status of a scan run by ID, including live PID check and log tail.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scan_run_id": {"type": "string", "description": "Scan run ID"},
                    "log_lines": {
                        "type": "integer",
                        "default": 50,
                        "minimum": 1,
                        "maximum": 500,
                        "description": "Number of log lines to tail",
                    },
                },
                "required": ["scan_run_id"],
            },
        ),
        Tool(
            name="preview_scan",
            description="Preview the command that would be executed for a scan, without spawning a process.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scanner": {"type": "string", "description": "Scanner name"},
                    "file_path": {"type": "string", "description": "File path (relative to project root)"},
                },
                "required": ["scanner", "file_path"],
            },
        ),
    ]

    legacy_tools = [
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
                "and returns immediately with a scan_run_id for correlation. Check scan status with get_scan_status "
                "or file findings later for results. "
                "Note: results are POSTed to the dashboard API — ensure the dashboard is running at the target api_url. "
                "Rate-limited (30s cooldown per scanner+file, DB-persisted)."
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

    tools = list(new_tools)
    handlers: dict[str, Callable[..., Any]] = {
        "trigger_scan_batch": _handle_trigger_scan_batch,
        "get_scan_status": _handle_get_scan_status,
        "preview_scan": _handle_preview_scan,
    }

    if include_legacy:
        tools.extend(legacy_tools)
        handlers["list_scanners"] = _handle_list_scanners
        handlers["trigger_scan"] = _handle_trigger_scan

    return tools, handlers


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _validate_localhost_url(api_url: str) -> list[TextContent] | None:
    """Return an error response if *api_url* is not localhost, else ``None``."""
    from urllib.parse import urlparse

    host = urlparse(api_url).hostname or ""
    if host not in _LOCALHOST_HOSTS:
        return _text(
            ErrorResponse(
                error=f"Non-localhost api_url not allowed: {host!r}. Scanner results would be sent to an external host.",
                code="invalid_api_url",
            )
        )
    return None


def _load_scanner_or_error(filigree_dir: Path, scanner_name: str) -> tuple[Any | None, list[TextContent] | None]:
    """Load scanner config or return an error response."""
    scanners_dir = filigree_dir / "scanners"
    cfg = load_scanner(scanners_dir, scanner_name)
    if cfg is None:
        available = [s.name for s in _list_scanners(scanners_dir)]
        return None, _text(
            {
                "error": f"Scanner {scanner_name!r} not found",
                "code": "scanner_not_found",
                "available_scanners": available,
            }
        )
    return cfg, None


def _spawn_scan(
    *,
    cfg: Any,
    canonical_path: str,
    api_url: str,
    project_root: Path,
    scan_run_id: str,
    filigree_dir: Path,
    log_suffix: str = "",
) -> dict[str, Any] | list[TextContent]:
    """Build command, validate, and spawn scanner process.

    Returns ``{'proc': Popen, 'scan_log_path': Path, 'cmd': list[str],
    'log_warning'?: str}`` on success, or a ``list[TextContent]`` error
    response.

    *log_suffix* disambiguates log files when multiple processes share
    a scan_run_id (batch mode).
    """
    try:
        cmd = cfg.build_command(
            file_path=canonical_path,
            api_url=api_url,
            project_root=str(project_root),
            scan_run_id=scan_run_id,
        )
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="invalid_command"))

    cmd_err = validate_scanner_command(cmd, project_root=project_root)
    if cmd_err is not None:
        return _text(ErrorResponse(error=cmd_err, code="command_not_found"))

    scan_log_dir = filigree_dir / "scans"
    scan_log_dir.mkdir(parents=True, exist_ok=True)
    log_name = f"{scan_run_id}{log_suffix}.log"
    scan_log_path = scan_log_dir / log_name
    log_warning: str | None = None
    try:
        scan_log_fd = open(scan_log_path, "w")  # noqa: SIM115
    except OSError as log_err:
        scan_log_fd = None
        log_warning = f"Scan log could not be created at {scan_log_path}: {log_err}. Scanner stderr will be discarded."
        _logger.warning("Cannot open scan log %s: %s", scan_log_path, log_err)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=scan_log_fd if scan_log_fd is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError, TypeError) as e:
        return _text(
            {
                "error": f"Failed to spawn scanner process: {e}",
                "code": "spawn_failed",
                "scanner": cfg.name,
            }
        )
    finally:
        if scan_log_fd is not None:
            scan_log_fd.close()

    result: dict[str, Any] = {
        "proc": proc,
        "scan_log_path": scan_log_path,
        "cmd": cmd,
    }
    if log_warning:
        result["log_warning"] = log_warning
    return result


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_list_scanners(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_filigree_dir

    filigree_dir = _get_filigree_dir()
    scanners_dir = filigree_dir / "scanners" if filigree_dir else None
    if scanners_dir is None:
        return _text(ErrorResponse(error="Project directory not initialized", code="not_initialized"))
    load_errors: list[str] = []
    scanners = _list_scanners(scanners_dir, errors=load_errors)
    result_data: dict[str, Any] = {"scanners": [s.to_dict() for s in scanners]}
    if load_errors:
        result_data["errors"] = load_errors
    if not scanners:
        result_data["hint"] = "No scanners registered. Add TOML files to .filigree/scanners/"
    return _text(result_data)


async def _handle_trigger_scan(arguments: dict[str, Any]) -> list[TextContent]:
    from datetime import UTC, datetime

    from filigree.mcp_server import _get_db, _get_filigree_dir, _safe_path

    filigree_dir = _get_filigree_dir()
    if filigree_dir is None:
        return _text(ErrorResponse(error="Project directory not initialized", code="not_initialized"))

    args = _parse_args(arguments, TriggerScanArgs)
    tracker = _get_db()
    scanner_name = args["scanner"]
    file_path = args["file_path"]
    api_url = args.get("api_url", "http://localhost:8377")

    url_err = _validate_localhost_url(api_url)
    if url_err is not None:
        return url_err

    try:
        target = _safe_path(file_path)
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="invalid_path"))

    cfg, err = _load_scanner_or_error(filigree_dir, scanner_name)
    if err is not None:
        return err
    assert cfg is not None  # noqa: S101  -- narrowing after error-check

    if not target.is_file():
        return _text(ErrorResponse(error=f"File not found: {file_path}", code="file_not_found"))

    file_type_warning = ""
    if cfg.file_types:
        ext = Path(file_path).suffix.lstrip(".")
        if ext and ext not in cfg.file_types:
            file_type_warning = f"Warning: file extension {ext!r} not in scanner's declared file_types {cfg.file_types}. Proceeding anyway."

    canonical_path = str(target.relative_to(filigree_dir.resolve().parent))

    # DB-persisted cooldown check
    blocking_run = tracker.check_scan_cooldown(scanner_name, canonical_path)
    if blocking_run is not None:
        return _text(
            {
                "error": f"Scanner {scanner_name!r} was already triggered for {file_path!r} recently.",
                "code": "rate_limited",
                "blocking_run_id": blocking_run["id"],
            }
        )

    file_record = tracker.register_file(canonical_path)
    project_root = filigree_dir.parent
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    scan_run_id = f"{scanner_name}-{ts}-{secrets.token_hex(3)}"

    spawn_result = _spawn_scan(
        cfg=cfg,
        canonical_path=canonical_path,
        api_url=api_url,
        project_root=project_root,
        scan_run_id=scan_run_id,
        filigree_dir=filigree_dir,
    )
    if isinstance(spawn_result, list):
        return spawn_result

    proc = spawn_result["proc"]
    scan_log_path = spawn_result["scan_log_path"]
    log_rel = str(scan_log_path.relative_to(filigree_dir.parent))

    # Create DB-tracked scan run.  If the DB call fails the spawned
    # process would be orphaned, so kill it on any error.
    try:
        tracker.create_scan_run(
            scan_run_id=scan_run_id,
            scanner_name=scanner_name,
            scan_source=scanner_name,
            file_paths=[canonical_path],
            file_ids=[file_record.id],
            pid=proc.pid,
            api_url=api_url,
            log_path=log_rel,
        )
        tracker.update_scan_run_status(scan_run_id, "running")
    except Exception as exc:
        with contextlib.suppress(OSError):
            proc.kill()
        _logger.error(
            "Failed to record scan run %s (pid %d killed): %s",
            scan_run_id,
            proc.pid,
            exc,
        )
        return _text(
            ErrorResponse(
                error=f"Scan process spawned but DB tracking failed: {exc}. Process (pid={proc.pid}) terminated.",
                code="db_error",
            )
        )

    await asyncio.sleep(0.2)
    exit_code = proc.poll()
    if exit_code is not None and exit_code != 0:
        tracker.update_scan_run_status(
            scan_run_id,
            "failed",
            exit_code=exit_code,
            error_message=f"Scanner exited immediately with code {exit_code}",
        )
        log_hint = ""
        if scan_log_path.exists() and scan_log_path.stat().st_size > 0:
            log_hint = f" Check log: {log_rel}"
        elif spawn_result.get("log_warning"):
            log_hint = f" Note: {spawn_result['log_warning']}"
        return _text(
            {
                "error": f"Scanner process exited immediately with code {exit_code}.{log_hint}",
                "code": "spawn_failed",
                "scanner": scanner_name,
                "file_id": file_record.id,
                "scan_run_id": scan_run_id,
                "exit_code": exit_code,
                "log_path": log_rel,
            }
        )

    _logger.info(
        "Spawned scanner %s for %s (pid=%d, run_id=%s)",
        scanner_name,
        file_path,
        proc.pid,
        scan_run_id,
    )

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
            f"Poll findings via file_id={file_record.id!r} or status via get_scan_status. "
            f"Scanner log: {log_rel}"
        ),
    }
    warnings: list[str] = []
    if file_type_warning:
        warnings.append(file_type_warning)
    if spawn_result.get("log_warning"):
        warnings.append(spawn_result["log_warning"])
    if warnings:
        scan_result["warnings"] = warnings
    return _text(scan_result)


async def _handle_trigger_scan_batch(arguments: dict[str, Any]) -> list[TextContent]:
    from datetime import UTC, datetime

    from filigree.mcp_server import _get_db, _get_filigree_dir, _safe_path

    filigree_dir = _get_filigree_dir()
    if filigree_dir is None:
        return _text(ErrorResponse(error="Project directory not initialized", code="not_initialized"))

    args = _parse_args(arguments, TriggerScanBatchArgs)
    tracker = _get_db()
    scanner_name = args["scanner"]
    file_paths = args.get("file_paths", [])
    api_url = args.get("api_url", "http://localhost:8377")

    if not isinstance(file_paths, list) or not file_paths:
        return _text(ErrorResponse(error="file_paths must be a non-empty list", code="validation_error"))

    url_err = _validate_localhost_url(api_url)
    if url_err is not None:
        return url_err

    cfg, err = _load_scanner_or_error(filigree_dir, scanner_name)
    if err is not None:
        return err
    assert cfg is not None  # noqa: S101  -- narrowing after error-check

    # Validate and resolve all paths
    canonical_paths: list[str] = []
    file_ids: list[str] = []
    skipped: list[dict[str, str]] = []
    for fp in file_paths:
        try:
            target = _safe_path(fp)
        except ValueError as e:
            skipped.append({"file_path": fp, "reason": str(e)})
            continue
        if not target.is_file():
            skipped.append({"file_path": fp, "reason": "File not found"})
            continue
        cp = str(target.relative_to(filigree_dir.resolve().parent))
        # Check cooldown
        blocking = tracker.check_scan_cooldown(scanner_name, cp)
        if blocking is not None:
            skipped.append({"file_path": fp, "reason": "rate_limited"})
            continue
        file_record = tracker.register_file(cp)
        canonical_paths.append(cp)
        file_ids.append(file_record.id)

    if not canonical_paths:
        return _text(
            {
                "error": "No files eligible for scanning",
                "code": "no_eligible_files",
                "skipped": skipped,
            }
        )

    project_root = filigree_dir.parent
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    scan_run_id = f"{scanner_name}-batch-{ts}-{secrets.token_hex(3)}"

    # Spawn one scanner process per file — build_command only accepts a single path.
    # Each process gets a unique log file via log_suffix to avoid clobbering.
    spawned: list[dict[str, Any]] = []
    spawned_paths: list[str] = []
    spawned_file_ids: list[str] = []
    spawn_errors: list[dict[str, str]] = []
    for i, cp in enumerate(canonical_paths):
        spawn_result = _spawn_scan(
            cfg=cfg,
            canonical_path=cp,
            api_url=api_url,
            project_root=project_root,
            scan_run_id=scan_run_id,
            filigree_dir=filigree_dir,
            log_suffix=f"-{i}",
        )
        if isinstance(spawn_result, list):
            # Extract error detail from the TextContent response
            reason = "spawn_failed"
            try:
                detail = json.loads(spawn_result[0].text)
                reason = detail.get("error", reason)
            except (ValueError, IndexError, AttributeError, TypeError):
                pass
            spawn_errors.append({"file_path": cp, "reason": reason})
            continue
        spawned.append(spawn_result)
        spawned_paths.append(cp)
        spawned_file_ids.append(file_ids[i])

    if not spawned:
        return _text(
            {
                "error": "All scanner processes failed to spawn",
                "code": "spawn_failed",
                "spawn_errors": spawn_errors,
                "skipped": skipped,
            }
        )

    # Limitation: only one PID/log_path can be stored per scan_run_id.
    # The last spawned process is used; get_scan_status only monitors this PID.
    # Individual per-file logs are available via log_suffix (-0, -1, ...).
    last = spawned[-1]
    scan_log_path = last["scan_log_path"]
    log_rel = str(scan_log_path.relative_to(filigree_dir.parent))

    # Record the batch scan run.  If DB tracking fails, kill all
    # spawned processes so they don't run orphaned.
    try:
        tracker.create_scan_run(
            scan_run_id=scan_run_id,
            scanner_name=scanner_name,
            scan_source=scanner_name,
            file_paths=spawned_paths,
            file_ids=spawned_file_ids,
            pid=last["proc"].pid,
            api_url=api_url,
            log_path=log_rel,
        )
        tracker.update_scan_run_status(scan_run_id, "running")
    except Exception as exc:
        for s in spawned:
            with contextlib.suppress(OSError):
                s["proc"].kill()
        _logger.error(
            "Failed to record batch scan run %s (%d processes killed): %s",
            scan_run_id,
            len(spawned),
            exc,
        )
        return _text(
            ErrorResponse(
                error=f"Batch scan spawned {len(spawned)} processes but DB tracking failed: {exc}. All processes terminated.",
                code="db_error",
            )
        )

    # Quick check: did any process exit immediately with error?
    await asyncio.sleep(0.2)
    immediate_failures = 0
    for s in spawned:
        ec = s["proc"].poll()
        if ec is not None and ec != 0:
            immediate_failures += 1

    if immediate_failures == len(spawned):
        last_exit = last["proc"].poll()
        tracker.update_scan_run_status(
            scan_run_id,
            "failed",
            exit_code=last_exit,
            error_message=f"All {len(spawned)} scanner processes exited immediately",
        )
        return _text(
            {
                "error": f"All {len(spawned)} scanner processes exited immediately.",
                "code": "spawn_failed",
                "scan_run_id": scan_run_id,
                "log_path": log_rel,
            }
        )

    result: dict[str, Any] = {
        "status": "triggered",
        "scanner": scanner_name,
        "file_count": len(spawned_paths),
        "processes_spawned": len(spawned),
        "scan_run_id": scan_run_id,
        "log_path": log_rel,
    }
    if spawn_errors:
        result["spawn_errors"] = spawn_errors
    if skipped:
        result["skipped"] = skipped
    if immediate_failures:
        result["immediate_failures"] = immediate_failures
    log_warnings = [s["log_warning"] for s in spawned if s.get("log_warning")]
    if log_warnings:
        result["warnings"] = log_warnings
    return _text(result)


async def _handle_get_scan_status(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, GetScanStatusArgs)
    scan_run_id = args.get("scan_run_id", "")
    if not isinstance(scan_run_id, str) or not scan_run_id.strip():
        return _text(ErrorResponse(error="scan_run_id is required", code="validation_error"))
    log_lines = args.get("log_lines", 50)

    err_resp = _validate_int_range(log_lines, "log_lines", min_val=1, max_val=500)
    if err_resp is not None:
        return err_resp

    tracker = _get_db()
    try:
        status = tracker.get_scan_status(scan_run_id, log_lines=log_lines)
    except KeyError:
        return _text(ErrorResponse(error=f"Scan run not found: {scan_run_id}", code="not_found"))
    return _text(status)


async def _handle_preview_scan(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_filigree_dir, _safe_path

    filigree_dir = _get_filigree_dir()
    if filigree_dir is None:
        return _text(ErrorResponse(error="Project directory not initialized", code="not_initialized"))

    args = _parse_args(arguments, PreviewScanArgs)
    scanner_name = args["scanner"]
    file_path = args["file_path"]

    try:
        target = _safe_path(file_path)
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="invalid_path"))

    cfg, err = _load_scanner_or_error(filigree_dir, scanner_name)
    if err is not None:
        return err
    assert cfg is not None  # noqa: S101  -- narrowing after error-check

    canonical_path = str(target.relative_to(filigree_dir.resolve().parent))
    project_root = filigree_dir.parent
    try:
        cmd = cfg.build_command(
            file_path=canonical_path,
            api_url="http://localhost:8377",
            project_root=str(project_root),
            scan_run_id="preview-dry-run",
        )
    except ValueError as e:
        return _text(ErrorResponse(error=str(e), code="invalid_command"))

    cmd_err = validate_scanner_command(cmd, project_root=project_root)

    return _text(
        {
            "scanner": scanner_name,
            "file_path": file_path,
            "command": cmd,
            "command_string": " ".join(cmd),
            "valid": cmd_err is None,
            "validation_error": cmd_err,
        }
    )
