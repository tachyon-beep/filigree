"""Hook logic for Claude Code SessionStart hooks.

Separated from CLI for testability. These functions are called by the
``filigree session-context`` and ``filigree ensure-dashboard`` CLI
subcommands, which in turn are registered as Claude Code hooks by
``filigree install --hooks``.
"""

from __future__ import annotations

import fcntl
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from filigree.core import (
    DB_FILENAME,
    FiligreeDB,
    find_filigree_root,
    read_config,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session context
# ---------------------------------------------------------------------------

READY_CAP = 15


def _build_context(db: FiligreeDB) -> str:
    """Assemble the project snapshot string from a live DB handle."""
    lines: list[str] = []
    lines.append("=== Filigree Project Snapshot ===")
    lines.append("")

    # In-progress work
    in_progress = db.list_issues(status="in_progress")
    if in_progress:
        lines.append("IN PROGRESS (resume these):")
        for issue in in_progress:
            lines.append(f'P{issue.priority} {issue.id} [{issue.type}] "{issue.title}"')
        lines.append("")

    # Ready to work
    ready = db.get_ready()
    if ready:
        shown = ready[:READY_CAP]
        lines.append(f"READY TO WORK ({len(ready)} tasks with no blockers):")
        for issue in shown:
            lines.append(f'P{issue.priority} {issue.id} [{issue.type}] "{issue.title}"')
        if len(ready) > READY_CAP:
            lines.append("  ... (truncated, run 'filigree ready' for full list)")
        lines.append("")

    # Critical path
    crit = db.get_critical_path()
    if crit:
        lines.append("CRITICAL PATH (unblocks the most downstream work):")
        lines.append(f"Critical path ({len(crit)} issues):")
        for i, item in enumerate(crit):
            prefix = "  -> " if i > 0 else "  "
            lines.append(f'{prefix}P{item["priority"]} {item["id"]} [{item["type"]}] "{item["title"]}"')
        lines.append("")

    # Stats
    stats = db.get_stats()
    ready_count = stats.get("ready_count", 0)
    blocked_count = stats.get("blocked_count", 0)
    lines.append(f"STATS: {ready_count} ready, {blocked_count} blocked")

    return "\n".join(lines)


def generate_session_context() -> str | None:
    """Generate a project snapshot for Claude Code session context.

    Returns ``None`` when there is no filigree project (silent exit).
    """
    try:
        filigree_dir = find_filigree_root()
    except FileNotFoundError:
        return None

    config = read_config(filigree_dir)
    db = FiligreeDB(
        filigree_dir / DB_FILENAME,
        prefix=config.get("prefix", "filigree"),
    )
    try:
        db.initialize()
        return _build_context(db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Dashboard management
# ---------------------------------------------------------------------------


def _is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    """Check whether *port* is accepting connections on *host*."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _try_register_with_server(port: int) -> None:
    """Best-effort POST to register this project with a running dashboard."""
    try:
        import json
        import urllib.request

        filigree_dir = find_filigree_root()
        data = json.dumps({"path": str(filigree_dir)}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/register",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=2)  # noqa: S310
    except Exception:
        pass  # Best-effort


def ensure_dashboard_running(port: int = 8377) -> str:
    """Ensure the filigree dashboard is running.

    Uses ``fcntl.flock`` for atomic check-and-start so concurrent
    sessions don't race.  Returns a human-readable status message.
    """
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401

        import filigree.dashboard  # noqa: F401
    except ImportError:
        return 'Dashboard requires extra dependencies. Install with: pip install "filigree[dashboard]"'

    try:
        find_filigree_root()
    except FileNotFoundError:
        return ""

    # Register current project with the global registry (best-effort)
    try:
        from filigree.registry import Registry

        filigree_dir = find_filigree_root()
        Registry().register(filigree_dir)
    except Exception:
        pass  # Never fatal — registry is advisory

    tmpdir = os.environ.get("TMPDIR", "/tmp")  # noqa: S108
    lockfile = os.path.join(tmpdir, "filigree-dashboard.lock")
    pidfile = os.path.join(tmpdir, "filigree-dashboard.pid")
    logfile = os.path.join(tmpdir, "filigree-dashboard.log")

    lock_fd = None
    try:
        lock_fd = open(lockfile, "w")  # noqa: SIM115
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "Filigree dashboard: another session is starting it, skipping"

        if _is_port_listening(port):
            _try_register_with_server(port)
            return f"Filigree dashboard already running on http://localhost:{port}"

        # Start the dashboard in a detached process
        filigree_bin = str(Path(sys.executable).parent / "filigree")
        # Prefer the entry-point on PATH
        import shutil

        filigree_cmd = shutil.which("filigree") or filigree_bin

        # Capture stderr to a log file for diagnostics on failure
        with open(logfile, "w") as log_fd:
            proc = subprocess.Popen(
                [filigree_cmd, "dashboard", "--no-browser", "--port", str(port)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_fd,
                start_new_session=True,
            )
        # log_fd closed here; child process retains its own fd copy

        # Brief check: did the process exit immediately?
        time.sleep(0.5)
        exit_code = proc.poll()
        if exit_code is not None:
            stderr_output = Path(logfile).read_text().strip()
            detail = f": {stderr_output}" if stderr_output else ""
            return f"Dashboard process exited immediately (pid {proc.pid}, code {exit_code}){detail}"

        Path(pidfile).write_text(str(proc.pid))
        return f"Started Filigree dashboard (pid {proc.pid}) on http://localhost:{port}"
    finally:
        if lock_fd is not None:
            lock_fd.close()
