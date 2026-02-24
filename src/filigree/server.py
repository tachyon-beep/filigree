"""Server mode configuration and daemon management.

Handles the persistent multi-project daemon for server installation mode.
Config lives at $HOME/.config/filigree/server.json (see SERVER_CONFIG_DIR).
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from filigree.core import read_config, write_atomic
from filigree.ephemeral import is_pid_alive, read_pid_file, verify_pid_ownership, write_pid_file

logger = logging.getLogger(__name__)

SERVER_CONFIG_DIR = Path.home() / ".config" / "filigree"
SERVER_CONFIG_FILE = SERVER_CONFIG_DIR / "server.json"
SERVER_PID_FILE = SERVER_CONFIG_DIR / "server.pid"

DEFAULT_PORT = 8377
SUPPORTED_SCHEMA_VERSION = 1  # Max schema version this filigree version can handle


@dataclass
class ServerConfig:
    port: int = DEFAULT_PORT
    projects: dict[str, dict[str, str]] = field(default_factory=dict)


def _backup_corrupt_config() -> None:
    """Back up a corrupt server.json before callers overwrite it with defaults."""
    backup_path = SERVER_CONFIG_FILE.parent / (SERVER_CONFIG_FILE.name + ".bak")
    try:
        shutil.copy2(SERVER_CONFIG_FILE, backup_path)
    except OSError:
        logger.debug("Could not back up corrupt config to %s", backup_path, exc_info=True)


def read_server_config() -> ServerConfig:
    """Read server.json. Returns defaults if missing or invalid."""
    if not SERVER_CONFIG_FILE.exists():
        return ServerConfig()
    try:
        data = json.loads(SERVER_CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        _backup_corrupt_config()
        logger.warning("Corrupt server config %s: %s — backed up to .bak", SERVER_CONFIG_FILE, exc)
        return ServerConfig()

    if not isinstance(data, dict):
        _backup_corrupt_config()
        logger.warning("Server config %s is not a JSON object; backed up to .bak, using defaults", SERVER_CONFIG_FILE)
        return ServerConfig()

    # Coerce port
    raw_port = data.get("port", DEFAULT_PORT)
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        logger.warning("Invalid port value %r in server config; using default %d", raw_port, DEFAULT_PORT)
        port = DEFAULT_PORT
    if not (1 <= port <= 65535):
        logger.warning("Port %d out of range (1-65535) in server config; using default %d", port, DEFAULT_PORT)
        port = DEFAULT_PORT

    # Coerce projects — must be dict of dicts
    raw_projects = data.get("projects", {})
    if not isinstance(raw_projects, dict):
        raw_projects = {}
    projects = {str(k): v for k, v in raw_projects.items() if isinstance(v, dict)}

    return ServerConfig(port=port, projects=projects)


def write_server_config(config: ServerConfig) -> None:
    """Write server.json atomically."""
    SERVER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        {"port": config.port, "projects": config.projects},
        indent=2,
    )
    write_atomic(SERVER_CONFIG_FILE, content + "\n")


def register_project(filigree_dir: Path) -> None:
    """Register a project in server.json.

    Uses ``fcntl.flock`` around the read-modify-write to prevent
    concurrent sessions from losing each other's registrations.
    Raises ``ValueError`` if another registered project already uses
    the same prefix.
    """
    filigree_dir = filigree_dir.resolve()
    project_config = read_config(filigree_dir)

    # Version enforcement
    schema_version = project_config.get("version", 1)
    if schema_version > SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"Project schema version {schema_version} is newer than supported "
            f"version {SUPPORTED_SCHEMA_VERSION}. Upgrade filigree to manage this project."
        )

    SERVER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = SERVER_CONFIG_DIR / "server.lock"
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        config = read_server_config()
        project_key = str(filigree_dir)
        prefix = str(project_config.get("prefix", "filigree"))

        for existing_path, meta in config.projects.items():
            if existing_path == project_key:
                continue  # idempotent re-register
            existing_prefix = str(meta.get("prefix", "filigree")) if isinstance(meta, dict) else "filigree"
            if existing_prefix == prefix:
                raise ValueError(
                    f"Prefix collision: {prefix!r} already registered by {existing_path}. Choose a unique prefix in .filigree/config.json."
                )

        config.projects[project_key] = {"prefix": prefix}
        write_server_config(config)


def unregister_project(filigree_dir: Path) -> None:
    """Remove a project from server.json."""
    filigree_dir = filigree_dir.resolve()
    SERVER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = SERVER_CONFIG_DIR / "server.lock"
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        config = read_server_config()
        config.projects.pop(str(filigree_dir), None)
        write_server_config(config)


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------


@dataclass
class DaemonResult:
    success: bool
    message: str


@dataclass
class DaemonStatus:
    running: bool
    pid: int | None = None
    port: int | None = None
    project_count: int = 0

    def __post_init__(self) -> None:
        if self.running and (self.pid is None or self.port is None):
            raise ValueError("DaemonStatus with running=True requires both pid and port")


def start_daemon(port: int | None = None) -> DaemonResult:
    """Start the filigree server daemon."""
    from filigree.core import find_filigree_command

    SERVER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = SERVER_CONFIG_DIR / "server.lock"
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Check if already running — verify PID is actually a filigree process
        info = read_pid_file(SERVER_PID_FILE)
        if info and is_pid_alive(info["pid"]):
            if verify_pid_ownership(SERVER_PID_FILE, expected_cmd="filigree"):
                return DaemonResult(False, f"Daemon already running (pid {info['pid']})")
            # Stale PID from a reused process — clean up and proceed
            logger.warning("Stale PID file (pid %d is not filigree); cleaning up", info["pid"])
            SERVER_PID_FILE.unlink(missing_ok=True)

        config = read_server_config()
        daemon_port = port or config.port
        # Persist the effective daemon port so status/hooks/install agree.
        if config.port != daemon_port:
            config.port = daemon_port
            write_server_config(config)

        filigree_cmd = find_filigree_command()
        log_file = SERVER_CONFIG_DIR / "server.log"

        try:
            with open(log_file, "w") as log_fd:
                proc = subprocess.Popen(
                    [*filigree_cmd, "dashboard", "--no-browser", "--server-mode", "--port", str(daemon_port)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log_fd,
                    start_new_session=True,
                )
        except OSError as exc:
            return DaemonResult(False, f"Failed to start daemon: {exc}")

        write_pid_file(SERVER_PID_FILE, proc.pid, cmd="filigree")
        # Lock released here — critical section is complete

    # Post-startup health check runs outside the lock to avoid blocking
    # concurrent register_project() calls
    time.sleep(0.5)
    exit_code = proc.poll()
    if exit_code is not None:
        SERVER_PID_FILE.unlink(missing_ok=True)
        stderr = log_file.read_text().strip()
        return DaemonResult(False, f"Daemon exited immediately (code {exit_code}): {stderr}")

    return DaemonResult(True, f"Started filigree daemon (pid {proc.pid}) on port {daemon_port}")


def stop_daemon() -> DaemonResult:
    """Stop the filigree server daemon."""
    info = read_pid_file(SERVER_PID_FILE)
    if info is None:
        return DaemonResult(False, "No PID file found — daemon may not be running")

    pid = info["pid"]
    if not is_pid_alive(pid):
        SERVER_PID_FILE.unlink(missing_ok=True)
        return DaemonResult(True, f"Daemon (pid {pid}) was not running; cleaned up PID file")

    if not verify_pid_ownership(SERVER_PID_FILE, expected_cmd="filigree"):
        return DaemonResult(False, f"PID {pid} is not a filigree process — refusing to kill")

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        SERVER_PID_FILE.unlink(missing_ok=True)
        return DaemonResult(True, f"Daemon (pid {pid}) exited before SIGTERM; cleaned up PID file")
    except PermissionError:
        return DaemonResult(False, f"Permission denied sending SIGTERM to pid {pid}")

    # Wait for process to exit
    for _ in range(50):
        time.sleep(0.1)
        if not is_pid_alive(pid):
            SERVER_PID_FILE.unlink(missing_ok=True)
            return DaemonResult(True, f"Stopped filigree daemon (pid {pid})")

    # Escalate to SIGKILL
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
    except ProcessLookupError:
        # Process died between last check and SIGKILL — success
        SERVER_PID_FILE.unlink(missing_ok=True)
        return DaemonResult(True, f"Force-killed filigree daemon (pid {pid})")
    except PermissionError:
        return DaemonResult(False, f"Permission denied sending SIGKILL to pid {pid}")

    # Verify the process is actually dead
    if is_pid_alive(pid):
        # Still clean up PID file even when SIGKILL fails, to prevent a permanently stuck state
        SERVER_PID_FILE.unlink(missing_ok=True)
        logger.warning("Failed to kill daemon (pid %d) even with SIGKILL", pid)
        return DaemonResult(False, f"Failed to kill daemon (pid {pid}) even with SIGKILL")

    SERVER_PID_FILE.unlink(missing_ok=True)
    return DaemonResult(True, f"Force-killed filigree daemon (pid {pid})")


def daemon_status() -> DaemonStatus:
    """Check daemon status."""
    info = read_pid_file(SERVER_PID_FILE)
    if info is None or not is_pid_alive(info["pid"]):
        return DaemonStatus(running=False)

    # Verify PID belongs to filigree before reporting as running
    if not verify_pid_ownership(SERVER_PID_FILE, expected_cmd="filigree"):
        return DaemonStatus(running=False)

    config = read_server_config()
    return DaemonStatus(
        running=True,
        pid=info["pid"],
        port=config.port,
        project_count=len(config.projects),
    )


def claim_current_process_as_daemon(*, port: int | None = None) -> bool:
    """Track the current process as the server daemon.

    Returns ``True`` when the current process owns (or successfully claimed)
    ``server.pid``. Returns ``False`` if a different live process is already
    tracked.

    Uses ``fcntl.flock`` to serialise with ``start_daemon()`` and
    ``register_project()``, preventing two callers from simultaneously
    reading an empty PID file and both writing their own PID.
    """
    current_pid = os.getpid()
    SERVER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = SERVER_CONFIG_DIR / "server.lock"
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        info = read_pid_file(SERVER_PID_FILE)
        if info is not None:
            tracked_pid = info["pid"]
            if tracked_pid == current_pid:
                if port is not None:
                    config = read_server_config()
                    if config.port != port:
                        config.port = port
                        write_server_config(config)
                return True
            if is_pid_alive(tracked_pid):
                if verify_pid_ownership(SERVER_PID_FILE, expected_cmd="filigree"):
                    return False
                # Stale PID from a reused process — clean up and proceed
                logger.warning("Stale PID file (pid %d is not filigree); cleaning up", tracked_pid)
            SERVER_PID_FILE.unlink(missing_ok=True)

        write_pid_file(SERVER_PID_FILE, current_pid, cmd="filigree")
        if port is not None:
            config = read_server_config()
            if config.port != port:
                config.port = port
                write_server_config(config)
        return True


def release_daemon_pid_if_owned(pid: int) -> None:
    """Remove ``server.pid`` only if it currently points at *pid*."""
    info = read_pid_file(SERVER_PID_FILE)
    if info is not None and info["pid"] == pid:
        SERVER_PID_FILE.unlink(missing_ok=True)
