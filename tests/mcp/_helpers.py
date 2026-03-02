"""Shared test helpers for MCP tests.

Extracted from conftest.py so test modules can import directly
(``from tests.mcp._helpers import _parse``) instead of reaching
into conftest, which pytest discourages.
"""

from __future__ import annotations

import json
from typing import Any


def _parse(result: list[Any]) -> Any:
    """Extract text content from MCP response and parse as JSON if possible."""
    text = result[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
