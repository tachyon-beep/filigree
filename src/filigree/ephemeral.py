"""Ephemeral (session-scoped) dashboard lifecycle.

Handles deterministic port selection, PID tracking, and stale process cleanup
for the ethereal installation mode.
"""

from __future__ import annotations

import hashlib
import json as _json
import logging
import os
import socket
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PORT_BASE = 8400
PORT_RANGE = 1000
PORT_RETRIES = 5


def compute_port(filigree_dir: Path) -> int:
    """Deterministic port from project path: 8400 + hash(path) % 1000."""
    h = hashlib.sha256(str(filigree_dir.resolve()).encode()).hexdigest()
    return PORT_BASE + (int(h, 16) % PORT_RANGE)


def _is_port_free(port: int) -> bool:
    """Check whether a port is available for binding."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def find_available_port(filigree_dir: Path) -> int:
    """Find an available port, starting with the deterministic one.

    Tries the deterministic port first, then up to PORT_RETRIES sequential
    ports, then falls back to OS-assigned (port 0).

    Note: There is a small TOCTOU race between checking port availability
    and the subprocess binding to it. This is acceptable because: (1) the
    deterministic port makes collisions rare, (2) uvicorn will fail-fast
    with a clear "address in use" error, and (3) the caller can retry.
    """
    base = compute_port(filigree_dir)
    for offset in range(PORT_RETRIES):
        candidate = base + offset
        if candidate >= 65536:
            break
        if _is_port_free(candidate):
            return candidate

    # Fallback: OS-assigned
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    sock.close()
    return port


# ---------------------------------------------------------------------------
# PID lifecycle
# ---------------------------------------------------------------------------


def write_pid_file(pid_file: Path, pid: int, *, cmd: str = "filigree") -> None:
    """Write PID + process identity to file (JSON format, atomic)."""
    from filigree.core import write_atomic

    content = _json.dumps({"pid": pid, "cmd": cmd})
    write_atomic(pid_file, content)


def read_pid_file(pid_file: Path) -> dict[str, Any] | None:
    """Read PID info from file. Returns None if missing or corrupt.

    Supports both JSON format (new) and plain integer (legacy).
    """
    if not pid_file.exists():
        return None
    try:
        text = pid_file.read_text().strip()
        # Try JSON first (new format)
        try:
            data = _json.loads(text)
            if isinstance(data, dict) and "pid" in data:
                return {"pid": int(data["pid"]), "cmd": data.get("cmd", "unknown")}
        except (_json.JSONDecodeError, TypeError):
            pass
        # Fall back to plain integer (legacy format)
        return {"pid": int(text), "cmd": "unknown"}
    except (ValueError, OSError):
        return None


def is_pid_alive(pid: int) -> bool:
    """Check if a process is running (via kill signal 0)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def verify_pid_ownership(pid_file: Path, *, expected_cmd: str = "filigree") -> bool:
    """Verify PID file refers to a live process with expected identity."""
    info = read_pid_file(pid_file)
    if info is None:
        return False
    if not is_pid_alive(info["pid"]):
        return False
    return bool(info["cmd"] == expected_cmd)


def cleanup_stale_pid(pid_file: Path) -> bool:
    """Remove PID file if the process is dead. Returns True if cleaned."""
    info = read_pid_file(pid_file)
    if info is None:
        return False
    if not is_pid_alive(info["pid"]):
        pid_file.unlink(missing_ok=True)
        logger.info("Cleaned stale PID file %s (pid %d)", pid_file, info["pid"])
        return True
    return False


# ---------------------------------------------------------------------------
# Port file helpers
# ---------------------------------------------------------------------------


def write_port_file(port_file: Path, port: int) -> None:
    """Write the active dashboard port to file (atomic)."""
    from filigree.core import write_atomic

    write_atomic(port_file, str(port))


def read_port_file(port_file: Path) -> int | None:
    """Read the dashboard port from file. Returns None if missing/corrupt."""
    if not port_file.exists():
        return None
    try:
        return int(port_file.read_text().strip())
    except (ValueError, OSError):
        return None
