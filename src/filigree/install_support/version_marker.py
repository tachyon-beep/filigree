"""Schema-version markers and shared mismatch guidance text."""

from __future__ import annotations

from pathlib import Path

MARKER_NAME = "INSTALL_VERSION"


def format_schema_mismatch_guidance(installed: int, database: int) -> str:
    return (
        f"Database schema v{database} is newer than this filigree (v{installed}).\n"
        f"Downgrade is not supported.\n\n"
        f"To fix: upgrade filigree (`uv tool upgrade filigree` or your installer's "
        f"equivalent), or use a project that was created with this version.\n"
        f"Run `filigree doctor` for details."
    )


def read_install_version(filigree_dir: Path) -> int | None:
    """Return the recorded INSTALL_VERSION integer, or None if absent/invalid."""
    marker = filigree_dir / MARKER_NAME
    if not marker.exists():
        return None
    try:
        return int(marker.read_text().strip())
    except (OSError, ValueError):
        return None


def write_install_version(filigree_dir: Path, version: int) -> None:
    """Write the INSTALL_VERSION marker for the given project directory."""
    (filigree_dir / MARKER_NAME).write_text(f"{version}\n")
