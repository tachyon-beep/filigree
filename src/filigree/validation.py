"""Shared validation functions for all entry points.

Pure functions — no MCP, FastAPI, or Click dependencies.
"""

from __future__ import annotations

import unicodedata
from typing import Any

_MAX_ACTOR_LENGTH = 128


def sanitize_actor(value: Any) -> tuple[str, str | None]:
    """Validate and clean an actor name.

    Returns (cleaned_actor, None) on success or ("", error_message) on failure.
    Strips whitespace, then checks: non-empty, max length, no control/format chars.
    """
    if not isinstance(value, str):
        return ("", "actor must be a string")
    # Check for control/format chars before stripping — reject "\nbad" rather
    # than silently absorbing the newline via strip().
    for ch in value:
        cat = unicodedata.category(ch)
        if cat.startswith("C"):  # Cc (control) and Cf (format)
            return ("", f"actor must not contain control characters (found U+{ord(ch):04X})")
    cleaned = value.strip()
    if not cleaned:
        return ("", "actor must not be empty")
    if len(cleaned) > _MAX_ACTOR_LENGTH:
        return ("", f"actor must be at most {_MAX_ACTOR_LENGTH} characters")
    return (cleaned, None)
