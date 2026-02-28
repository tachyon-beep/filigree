"""Pure helpers and constants shared across MCP tool modules.

This module has NO dependency on ``mcp_server`` module globals, so it can
be imported freely without triggering circular-import issues.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, TypeVar, cast

from mcp.types import TextContent

from filigree.core import Issue

if TYPE_CHECKING:
    from filigree.core import FiligreeDB
from filigree.types.api import SlimIssue, TransitionError
from filigree.validation import sanitize_actor

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def _parse_args(arguments: dict[str, Any], cls: type[_T]) -> _T:
    """Cast MCP arguments to a typed dict for static analysis.

    Safety: MCP SDK validates argument presence/types against JSON Schema
    before handler invocation. Core validates authoritatively. This cast()
    provides mypy type narrowing only — no runtime validation.
    """
    return cast(_T, arguments)


# Hard cap on list_issues / search_issues results to keep MCP response size
# within token limits.  Callers can pass no_limit=true to bypass.
_MAX_LIST_RESULTS = 50


def _text(content: object) -> list[TextContent]:
    if isinstance(content, str):
        return [TextContent(type="text", text=content)]
    return [TextContent(type="text", text=json.dumps(content, indent=2, default=str))]


def _slim_issue(issue: Issue) -> SlimIssue:
    """Return a lightweight dict for search result listings."""
    return SlimIssue(
        id=issue.id,
        title=issue.title,
        status=issue.status,
        priority=issue.priority,
        type=issue.type,
    )


def _resolve_pagination(arguments: dict[str, Any]) -> tuple[int, int]:
    """Compute effective limit and offset for paginated MCP list/search tools.

    Handles the ``no_limit`` bypass and caps to ``_MAX_LIST_RESULTS``.
    The returned *effective_limit* is the user-visible page size; callers
    should overfetch by 1 (``limit=effective_limit + 1``) to detect ``has_more``.
    """
    no_limit = arguments.get("no_limit", False)
    requested_limit = arguments.get("limit", 100)
    offset = arguments.get("offset", 0)

    effective_limit = (requested_limit if "limit" in arguments else 10_000_000) if no_limit else min(requested_limit, _MAX_LIST_RESULTS)

    return effective_limit, offset


def _apply_has_more(items: list[Any], effective_limit: int) -> tuple[list[Any], bool]:
    """Trim an overfetched result list and return ``(trimmed, has_more)``."""
    has_more = len(items) > effective_limit
    if has_more:
        items = items[:effective_limit]
    return items, has_more


def _validate_str(value: Any, name: str) -> list[TextContent] | None:
    """Return a validation error if *value* is not ``None`` and not a ``str``."""
    if value is not None and not isinstance(value, str):
        return _text({"error": f"{name} must be a string", "code": "validation_error"})
    return None


def _validate_int_range(
    value: Any,
    name: str,
    min_val: int | None = None,
    max_val: int | None = None,
) -> list[TextContent] | None:
    """Return a validation error if *value* is not ``None`` and outside range.

    When *value* is ``None`` it is considered optional and passes.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return _text({"error": f"{name} must be an integer", "code": "validation_error"})
    if min_val is not None and value < min_val:
        return _text({"error": f"{name} must be >= {min_val}", "code": "validation_error"})
    if max_val is not None and value > max_val:
        return _text({"error": f"{name} must be <= {max_val}", "code": "validation_error"})
    return None


def _validate_actor(value: Any) -> tuple[str, list[TextContent] | None]:
    """Sanitize actor, returning (cleaned, None) or ("", error_response)."""
    cleaned, err = sanitize_actor(value)
    if err:
        return ("", _text({"error": err, "code": "validation_error"}))
    return (cleaned, None)


def _build_transition_error(
    tracker: FiligreeDB,
    issue_id: str,
    error: str,
    *,
    include_ready: bool = True,
) -> TransitionError:
    """Build a structured error dict with valid-transition hints."""
    data: TransitionError = {"error": error, "code": "invalid_transition"}
    try:
        transitions = tracker.get_valid_transitions(issue_id)
        if include_ready:
            data["valid_transitions"] = [{"to": t.to, "category": t.category, "ready": t.ready} for t in transitions]
        else:
            data["valid_transitions"] = [{"to": t.to, "category": t.category} for t in transitions]
        data["hint"] = "Use get_valid_transitions to see allowed state changes"
    except KeyError:
        logger.debug("Could not resolve transitions for %s", issue_id, exc_info=True)
    return data
