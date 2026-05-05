"""Shared path utilities for the filigree project tree."""

from __future__ import annotations

from pathlib import Path


def safe_path(raw: str, project_root: Path) -> Path:
    """Resolve a user-supplied path safely within the project root.

    Raises ValueError for absolute paths or paths that escape ``project_root``.
    """
    if Path(raw).is_absolute():
        msg = f"Absolute paths not allowed: {raw}"
        raise ValueError(msg)
    base = project_root.resolve()
    resolved = (base / raw).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        msg = f"Path escapes project directory: {raw}"
        raise ValueError(msg) from None
    return resolved
