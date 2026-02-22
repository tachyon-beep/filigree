"""Server mode configuration and daemon management.

Handles the persistent multi-project daemon for server installation mode.
Config lives at ~/.config/filigree/server.json.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from filigree.core import read_config, write_atomic

logger = logging.getLogger(__name__)

SERVER_CONFIG_DIR = Path.home() / ".config" / "filigree"
SERVER_CONFIG_FILE = SERVER_CONFIG_DIR / "server.json"
SERVER_PID_FILE = SERVER_CONFIG_DIR / "server.pid"

DEFAULT_PORT = 8377


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
    """Register a project in server.json."""
    filigree_dir = filigree_dir.resolve()
    config = read_server_config()
    project_config = read_config(filigree_dir)
    config.projects[str(filigree_dir)] = {
        "prefix": project_config.get("prefix", "filigree"),
    }
    write_server_config(config)


def unregister_project(filigree_dir: Path) -> None:
    """Remove a project from server.json."""
    filigree_dir = filigree_dir.resolve()
    config = read_server_config()
    config.projects.pop(str(filigree_dir), None)
    write_server_config(config)
