"""Server mode configuration and daemon management.

Handles the persistent multi-project daemon for server installation mode.
Config lives at ~/.config/filigree/server.json.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
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


def read_server_config() -> ServerConfig:
    """Read server.json. Returns defaults if missing."""
    if not SERVER_CONFIG_FILE.exists():
        return ServerConfig()
    try:
        data = json.loads(SERVER_CONFIG_FILE.read_text())
        return ServerConfig(
            port=data.get("port", DEFAULT_PORT),
            projects=data.get("projects", {}),
        )
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt server config %s: %s", SERVER_CONFIG_FILE, exc)
        return ServerConfig()


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
                    f"Prefix collision: {prefix!r} already registered by {existing_path}. "
                    "Choose a unique prefix in .filigree/config.json."
                )

        config.projects[project_key] = {"prefix": prefix}
        write_server_config(config)


def unregister_project(filigree_dir: Path) -> None:
    """Remove a project from server.json."""
    filigree_dir = filigree_dir.resolve()
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


def start_daemon(port: int | None = None) -> DaemonResult:
    """Start the filigree server daemon."""
    from filigree.core import find_filigree_command

    # Check if already running
    info = read_pid_file(SERVER_PID_FILE)
    if info and is_pid_alive(info["pid"]):
        return DaemonResult(False, f"Daemon already running (pid {info['pid']})")

    config = read_server_config()
    daemon_port = port or config.port
    # Persist the effective daemon port so status/hooks/install agree.
    if config.port != daemon_port:
        config.port = daemon_port
        write_server_config(config)

    SERVER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    filigree_cmd = find_filigree_command()
    log_file = SERVER_CONFIG_DIR / "server.log"

    with open(log_file, "w") as log_fd:
        proc = subprocess.Popen(
            [*filigree_cmd, "dashboard", "--no-browser", "--server-mode", "--port", str(daemon_port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            start_new_session=True,
        )

    write_pid_file(SERVER_PID_FILE, proc.pid, cmd="filigree")

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
    except (PermissionError, ProcessLookupError):
        pass

    SERVER_PID_FILE.unlink(missing_ok=True)
    return DaemonResult(True, f"Force-killed filigree daemon (pid {pid})")


def daemon_status() -> DaemonStatus:
    """Check daemon status."""
    info = read_pid_file(SERVER_PID_FILE)
    if info is None or not is_pid_alive(info["pid"]):
        return DaemonStatus(running=False)

    config = read_server_config()
    return DaemonStatus(
        running=True,
        pid=info["pid"],
        port=config.port,
        project_count=len(config.projects),
    )
