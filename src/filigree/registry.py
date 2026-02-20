"""Ephemeral multi-project registry.

Tracks recently-active filigree projects in ~/.filigree/registry.json.
Each MCP server and dashboard command registers its project on startup.
The dashboard uses this to show a project switcher with TTL-based filtering.

The registry is advisory — deleting it loses only the dropdown list.
Projects re-register on next MCP/dashboard interaction.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filigree.core import DB_FILENAME, FiligreeDB, read_config

logger = logging.getLogger(__name__)

REGISTRY_DIR = Path.home() / ".filigree"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"
REGISTRY_LOCK = REGISTRY_DIR / "registry.lock"
DEFAULT_TTL_HOURS = 6.0


@dataclass(frozen=True)
class ProjectEntry:
    path: str
    name: str
    key: str
    prefix: str
    last_seen: str


class Registry:
    """Read/write the ephemeral project registry with file locking."""

    def read(self) -> dict[str, dict[str, Any]]:
        """Read registry.json. Returns empty dict if missing/corrupt."""
        if not REGISTRY_FILE.exists():
            return {}
        try:
            data = json.loads(REGISTRY_FILE.read_text())
            if not isinstance(data, dict):
                logger.warning("Registry file %s contains non-dict JSON, ignoring", REGISTRY_FILE)
                return {}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt registry file %s, returning empty: %s", REGISTRY_FILE, exc)
            return {}

    def register(self, filigree_dir: Path) -> ProjectEntry:
        """Register or touch a project. Returns its ProjectEntry."""
        filigree_dir = filigree_dir.resolve()
        path_str = str(filigree_dir)
        config = read_config(filigree_dir)
        prefix = config.get("prefix", "filigree")
        now = datetime.now(UTC).isoformat()

        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        lock_fd = None
        try:
            lock_fd = open(REGISTRY_LOCK, "w")  # noqa: SIM115
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            data = self.read()

            if path_str in data:
                data[path_str]["last_seen"] = now
                entry = ProjectEntry(**data[path_str])
            else:
                key = self._derive_key(prefix, path_str, data)
                entry = ProjectEntry(
                    path=path_str,
                    name=prefix,
                    key=key,
                    prefix=prefix,
                    last_seen=now,
                )
                data[path_str] = asdict(entry)

            REGISTRY_FILE.write_text(json.dumps(data, indent=2) + "\n")
        finally:
            if lock_fd is not None:
                lock_fd.close()

        return entry

    def active_projects(self, ttl_hours: float = DEFAULT_TTL_HOURS) -> list[ProjectEntry]:
        """Return projects seen within the TTL window whose directories still exist."""
        data = self.read()
        cutoff = datetime.now(UTC).timestamp() - (ttl_hours * 3600)
        result = []
        for entry_data in data.values():
            try:
                seen = datetime.fromisoformat(entry_data["last_seen"]).timestamp()
                if seen >= cutoff:
                    # Skip entries whose .filigree/ directory no longer exists
                    if not Path(entry_data["path"]).is_dir():
                        logger.debug("Skipping stale registry entry (dir gone): %s", entry_data.get("path"))
                        continue
                    result.append(ProjectEntry(**entry_data))
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("Skipping malformed registry entry: %s", exc)
                continue
        result.sort(key=lambda e: e.last_seen, reverse=True)
        return result

    @staticmethod
    def _derive_key(prefix: str, path_str: str, existing: dict[str, Any]) -> str:
        """Derive a unique URL-safe project key from the prefix."""
        base = "".join(c if c.isalnum() or c == "-" else "" for c in prefix.lower())
        if not base:
            base = "project"

        existing_keys = {e.get("key") for e in existing.values()}
        if base not in existing_keys:
            return base

        suffix = hashlib.sha256(path_str.encode()).hexdigest()[:6]
        return f"{base}-{suffix}"


class ProjectManager:
    """Manages DB connections for multiple registered projects."""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._connections: dict[str, FiligreeDB] = {}
        self._paths: dict[str, Path] = {}  # key -> .filigree/ path

    def register(self, filigree_dir: Path) -> ProjectEntry:
        """Register a project and cache its path for later DB opening."""
        entry = self._registry.register(filigree_dir)
        self._paths[entry.key] = Path(entry.path)
        return entry

    def get_db(self, key: str) -> FiligreeDB | None:
        """Get or lazily open a DB connection for a project key."""
        if key in self._connections:
            return self._connections[key]

        path = self._paths.get(key)
        if path is None:
            # Check registry for projects registered by other processes
            data = self._registry.read()
            for entry_data in data.values():
                if entry_data.get("key") == key:
                    path = Path(entry_data["path"])
                    self._paths[key] = path
                    break

        if path is None:
            return None

        # Guard against stale registry entries pointing to deleted directories
        if not path.is_dir() or not (path / DB_FILENAME).exists():
            self._paths.pop(key, None)
            return None

        try:
            config = read_config(path)
            db = FiligreeDB(
                path / DB_FILENAME,
                prefix=config.get("prefix", "filigree"),
                check_same_thread=False,
            )
            db.initialize()
        except Exception:
            logger.warning("Failed to open DB for project key=%s path=%s", key, path, exc_info=True)
            self._paths.pop(key, None)
            return None

        self._connections[key] = db
        return db

    def get_active_projects(self, ttl_hours: float = DEFAULT_TTL_HOURS) -> list[ProjectEntry]:
        """Return recently-active projects from the registry."""
        return self._registry.active_projects(ttl_hours=ttl_hours)

    def close_all(self) -> None:
        """Close all open DB connections."""
        for db in self._connections.values():
            db.close()
        self._connections.clear()
        self._paths.clear()
