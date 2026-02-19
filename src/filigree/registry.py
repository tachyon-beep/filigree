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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filigree.core import read_config

logger = logging.getLogger(__name__)

REGISTRY_DIR = Path.home() / ".filigree"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"
REGISTRY_LOCK = REGISTRY_DIR / "registry.lock"
DEFAULT_TTL_HOURS = 6.0


@dataclass
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
                return {}
            return data
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt registry file, resetting")
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        """Write registry.json with flock for atomicity."""
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        lock_fd = None
        try:
            lock_fd = open(REGISTRY_LOCK, "w")  # noqa: SIM115
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            REGISTRY_FILE.write_text(json.dumps(data, indent=2) + "\n")
        finally:
            if lock_fd is not None:
                lock_fd.close()

    def register(self, filigree_dir: Path) -> ProjectEntry:
        """Register or touch a project. Returns its ProjectEntry."""
        filigree_dir = filigree_dir.resolve()
        path_str = str(filigree_dir)
        config = read_config(filigree_dir)
        prefix = config.get("prefix", "filigree")
        now = datetime.now(timezone.utc).isoformat()

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
        """Return projects seen within the TTL window."""
        data = self.read()
        cutoff = datetime.now(timezone.utc).timestamp() - (ttl_hours * 3600)
        result = []
        for entry_data in data.values():
            try:
                seen = datetime.fromisoformat(entry_data["last_seen"]).timestamp()
                if seen >= cutoff:
                    result.append(ProjectEntry(**entry_data))
            except (KeyError, ValueError):
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
