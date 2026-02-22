"""Hook logic for Claude Code SessionStart hooks.

Separated from CLI for testability. These functions are called by the
``filigree session-context`` and ``filigree ensure-dashboard`` CLI
subcommands, which in turn are registered as Claude Code hooks by
``filigree install --hooks``.
"""

from __future__ import annotations

import fcntl
import logging
import socket
import subprocess
import time
from pathlib import Path

from filigree.core import (
    DB_FILENAME,
    FiligreeDB,
    find_filigree_command,
    find_filigree_root,
    get_mode,
    read_config,
)
from filigree.install import (
    FILIGREE_INSTRUCTIONS_MARKER,
    _instructions_hash,
    inject_instructions,
    install_skills,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session context
# ---------------------------------------------------------------------------

READY_CAP = 15


def _build_context(db: FiligreeDB, filigree_dir: Path | None = None) -> str:
    """Assemble the project snapshot string from a live DB handle."""
    lines: list[str] = []
    lines.append("=== Filigree Project Snapshot ===")
    lines.append("")

    # Dashboard URL (if running)
    if filigree_dir is not None:
        from filigree.ephemeral import is_pid_alive, read_pid_file, read_port_file

        port_file = filigree_dir / "ephemeral.port"
        pid_file = filigree_dir / "ephemeral.pid"
        port = read_port_file(port_file)
        pid_info = read_pid_file(pid_file)
        if port and pid_info and is_pid_alive(pid_info["pid"]) and _is_port_listening(port):
            lines.append(f"DASHBOARD: http://localhost:{port}")
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


def _extract_marker_hash(content: str) -> str | None:
    """Extract the hash from a filigree instructions marker comment.

    Looks for ``<!-- filigree:instructions:v{VER}:{HASH} -->`` and returns
    the HASH portion, or ``None`` if the marker is missing or uses the old
    format without a hash.
    """
    import re

    m = re.search(r"<!-- filigree:instructions:v[^:]+:([0-9a-f]+) -->", content)
    return m.group(1) if m else None


def _check_instructions_freshness(project_root: Path) -> list[str]:
    """Check whether CLAUDE.md/AGENTS.md instructions and skills are current.

    Compares the hash embedded in the marker comment against the hash of
    the currently installed instructions template.  Updates stale files
    in-place and returns a list of human-readable status messages.
    """
    messages: list[str] = []
    current_hash = _instructions_hash()

    # Check CLAUDE.md and AGENTS.md
    for filename in ("CLAUDE.md", "AGENTS.md"):
        md_path = project_root / filename
        if not md_path.exists():
            continue
        content = md_path.read_text()
        if FILIGREE_INSTRUCTIONS_MARKER not in content:
            continue
        embedded_hash = _extract_marker_hash(content)
        if embedded_hash == current_hash:
            continue
        # Stale or old-format marker — update
        inject_instructions(md_path)
        messages.append(f"Updated filigree instructions in {filename}")

    # Check skill pack
    skill_target = project_root / ".claude" / "skills" / "filigree-workflow" / "SKILL.md"
    if skill_target.exists():
        from filigree.install import _get_skills_source_dir

        source_skill = _get_skills_source_dir() / "filigree-workflow" / "SKILL.md"
        if source_skill.exists():
            import hashlib

            target_hash = hashlib.sha256(skill_target.read_bytes()).hexdigest()[:8]
            source_hash = hashlib.sha256(source_skill.read_bytes()).hexdigest()[:8]
            if target_hash != source_hash:
                install_skills(project_root)
                messages.append("Updated filigree skill pack")

    return messages


def generate_session_context() -> str | None:
    """Generate a project snapshot for Claude Code session context.

    Also checks whether filigree instructions in CLAUDE.md/AGENTS.md
    and the skill pack are up-to-date with the installed package version.

    Returns ``None`` when there is no filigree project (silent exit).
    """
    try:
        filigree_dir = find_filigree_root()
    except FileNotFoundError:
        return None

    project_root = filigree_dir.parent

    # Check instruction freshness (best-effort — don't let failures block context)
    freshness_messages: list[str] = []
    try:
        freshness_messages = _check_instructions_freshness(project_root)
    except Exception:
        logger.debug("Instructions freshness check failed", exc_info=True)

    config = read_config(filigree_dir)
    db = FiligreeDB(
        filigree_dir / DB_FILENAME,
        prefix=config.get("prefix", "filigree"),
    )
    try:
        db.initialize()
        context = _build_context(db, filigree_dir)
    finally:
        db.close()

    # Append freshness messages so they appear in hook output
    if freshness_messages:
        context += "\n\n" + "\n".join(freshness_messages)

    return context


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


def ensure_dashboard_running(port: int = 8377) -> str:
    """Ensure the filigree dashboard is running.

    In ethereal mode (default): spawns a single-project dashboard on a
    deterministic port, with PID/port files in .filigree/.
    In server mode: just verifies the daemon is reachable.
    """
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401

        import filigree.dashboard  # noqa: F401
    except ImportError:
        return 'Dashboard requires extra dependencies. Install with: pip install "filigree[dashboard]"'

    try:
        filigree_dir = find_filigree_root()
    except FileNotFoundError:
        return ""

    mode = get_mode(filigree_dir)

    if mode == "server":
        return _ensure_dashboard_server_mode(filigree_dir, port)
    return _ensure_dashboard_ethereal_mode(filigree_dir)


def _ensure_dashboard_ethereal_mode(filigree_dir: Path) -> str:
    """Ethereal mode: session-scoped dashboard on a deterministic port."""
    from filigree.ephemeral import (
        cleanup_stale_pid,
        find_available_port,
        is_pid_alive,
        read_pid_file,
        read_port_file,
        write_pid_file,
        write_port_file,
    )

    pid_file = filigree_dir / "ephemeral.pid"
    port_file = filigree_dir / "ephemeral.port"
    lock_file = filigree_dir / "ephemeral.lock"

    # Check if already running from a previous session
    pid_info = read_pid_file(pid_file)
    existing_port = read_port_file(port_file)
    if pid_info and existing_port and is_pid_alive(pid_info["pid"]) and _is_port_listening(existing_port):
        return f"Filigree dashboard running on http://localhost:{existing_port}"

    # Clean up stale state
    cleanup_stale_pid(pid_file)

    # Atomic start with lock
    lock_fd = None
    try:
        lock_fd = open(lock_file, "w")  # noqa: SIM115
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "Filigree dashboard: another session is starting it, skipping"

        # Re-check after acquiring lock
        pid_info = read_pid_file(pid_file)
        existing_port = read_port_file(port_file)
        if pid_info and existing_port and is_pid_alive(pid_info["pid"]) and _is_port_listening(existing_port):
            return f"Filigree dashboard running on http://localhost:{existing_port}"

        port = find_available_port(filigree_dir)
        filigree_cmd = find_filigree_command()

        log_file = filigree_dir / "ephemeral.log"
        with open(log_file, "w") as log_fd:
            proc = subprocess.Popen(
                [*filigree_cmd, "dashboard", "--no-browser", "--port", str(port)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_fd,
                start_new_session=True,
            )

        write_pid_file(pid_file, proc.pid, cmd="filigree")
        write_port_file(port_file, port)

        # Wait for startup
        for _ in range(10):
            time.sleep(0.3)
            exit_code = proc.poll()
            if exit_code is not None:
                pid_file.unlink(missing_ok=True)
                port_file.unlink(missing_ok=True)
                stderr_output = log_file.read_text().strip()
                detail = f": {stderr_output}" if stderr_output else ""
                return f"Dashboard process exited (pid {proc.pid}, code {exit_code}){detail}"
            if _is_port_listening(port):
                return f"Started Filigree dashboard on http://localhost:{port}"

        return f"Started Filigree dashboard on http://localhost:{port} (may still be initializing)"
    finally:
        if lock_fd is not None:
            lock_fd.close()


def _ensure_dashboard_server_mode(filigree_dir: Path, port: int) -> str:
    """Server mode: just verify the daemon is reachable."""
    if _is_port_listening(port):
        return f"Filigree server running on http://localhost:{port}"
    return f"Filigree server not running on port {port}. Start it with: filigree server start"
