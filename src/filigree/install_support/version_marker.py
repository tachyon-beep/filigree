"""Schema-version markers and shared mismatch guidance text."""

from __future__ import annotations


def format_schema_mismatch_guidance(installed: int, database: int) -> str:
    return (
        f"Database schema v{database} is newer than this filigree (v{installed}).\n"
        f"Downgrade is not supported.\n\n"
        f"To fix: upgrade filigree (`uv tool upgrade filigree` or your installer's "
        f"equivalent), or use a project that was created with this version.\n"
        f"Run `filigree doctor` for details."
    )
