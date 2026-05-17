"""MCP tools for entity_associations (ADR-029, Clarion B.7 / WP9-A).

Three tools binding Filigree issues to Clarion entities:

- ``add_entity_association`` — attach (or refresh) a Clarion entity to
  an issue, snapshotting the current content hash.
- ``remove_entity_association`` — remove the binding by composite key.
- ``list_entity_associations`` — enumerate bindings for an issue;
  returns raw rows (drift comparison is the caller's job per
  ADR-029 §"Decision 3").

The Clarion entity ID is opaque to Filigree — these tools do not parse
or validate the grammar (federation enrich-only rule, ``loom.md`` §5).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mcp.types import TextContent, Tool

from filigree.mcp_tools.common import (
    _parse_args,
    _text,
    _validate_actor,
)
from filigree.types.api import ErrorCode, ErrorResponse
from filigree.types.inputs import (
    AddEntityAssociationArgs,
    ListEntityAssociationsArgs,
    RemoveEntityAssociationArgs,
)

_logger = logging.getLogger(__name__)


def _require_nonempty_str(value: Any, name: str) -> list[TextContent] | None:
    """Return a validation error if *value* is not a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        return _text(ErrorResponse(error=f"{name} is required", code=ErrorCode.VALIDATION))
    return None


def register() -> tuple[list[Tool], dict[str, Callable[..., Any]]]:
    """Return (tool_definitions, handler_map) for the entity-association tools."""
    tools = [
        Tool(
            name="add_entity_association",
            description=(
                "Attach a Clarion entity to a Filigree issue (ADR-029). "
                "Idempotent on (issue_id, entity_id): re-attaching refreshes "
                "content_hash and timestamp while preserving the original actor. "
                "The entity_id is opaque to Filigree — its grammar is Clarion's "
                "contract (ADR-003)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Filigree issue ID"},
                    "entity_id": {
                        "type": "string",
                        "description": "Clarion entity ID (opaque string; not parsed)",
                    },
                    "content_hash": {
                        "type": "string",
                        "description": (
                            "Clarion's current entities.content_hash for the entity. "
                            "Stored verbatim; used by the consumer (Clarion's issues_for) "
                            "to compute drift at query time."
                        ),
                    },
                    "actor": {
                        "type": "string",
                        "description": "Actor identity recorded as attached_by on first attach",
                    },
                },
                "required": ["issue_id", "entity_id", "content_hash"],
            },
        ),
        Tool(
            name="remove_entity_association",
            description=("Remove the binding identified by (issue_id, entity_id). Idempotent — returns removed=false if no row existed."),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Filigree issue ID"},
                    "entity_id": {
                        "type": "string",
                        "description": "Clarion entity ID (opaque string)",
                    },
                },
                "required": ["issue_id", "entity_id"],
            },
        ),
        Tool(
            name="list_entity_associations",
            description=(
                "Return all Clarion entity bindings attached to an issue. "
                "Returns raw rows — drift detection is the caller's job per "
                'ADR-029 §"Decision 3" (Clarion\'s issues_for compares '
                "content_hash_at_attach against the live hash)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Filigree issue ID"},
                },
                "required": ["issue_id"],
            },
        ),
    ]

    handlers: dict[str, Callable[..., Any]] = {
        "add_entity_association": _handle_add_entity_association,
        "remove_entity_association": _handle_remove_entity_association,
        "list_entity_associations": _handle_list_entity_associations,
    }

    return tools, handlers


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_add_entity_association(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.core import WrongProjectError
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, AddEntityAssociationArgs)
    tracker = _get_db()
    issue_id = args.get("issue_id", "")
    entity_id = args.get("entity_id", "")
    content_hash = args.get("content_hash", "")
    actor, actor_err = _validate_actor(args.get("actor", "mcp"))
    if actor_err:
        return actor_err

    for err in (
        _require_nonempty_str(issue_id, "issue_id"),
        _require_nonempty_str(entity_id, "entity_id"),
        _require_nonempty_str(content_hash, "content_hash"),
    ):
        if err is not None:
            return err

    try:
        row = tracker.add_entity_association(issue_id, entity_id, content_hash, actor=actor)
    except WrongProjectError as exc:
        return _text(ErrorResponse(error=str(exc), code=ErrorCode.VALIDATION))
    except ValueError as exc:
        # Distinguish "issue not found" from generic validation so the
        # caller can react. The data-layer message starts with that phrase.
        code = ErrorCode.NOT_FOUND if "Issue not found" in str(exc) else ErrorCode.VALIDATION
        return _text(ErrorResponse(error=str(exc), code=code))
    return _text(dict(row))


async def _handle_remove_entity_association(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.core import WrongProjectError
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, RemoveEntityAssociationArgs)
    tracker = _get_db()
    issue_id = args.get("issue_id", "")
    entity_id = args.get("entity_id", "")

    for err in (
        _require_nonempty_str(issue_id, "issue_id"),
        _require_nonempty_str(entity_id, "entity_id"),
    ):
        if err is not None:
            return err

    try:
        removed = tracker.remove_entity_association(issue_id, entity_id)
    except WrongProjectError as exc:
        return _text(ErrorResponse(error=str(exc), code=ErrorCode.VALIDATION))
    except ValueError as exc:
        return _text(ErrorResponse(error=str(exc), code=ErrorCode.VALIDATION))
    return _text({"removed": removed})


async def _handle_list_entity_associations(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.core import WrongProjectError
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, ListEntityAssociationsArgs)
    tracker = _get_db()
    issue_id = args.get("issue_id", "")

    err = _require_nonempty_str(issue_id, "issue_id")
    if err is not None:
        return err

    try:
        rows = tracker.list_entity_associations(issue_id)
    except WrongProjectError as exc:
        return _text(ErrorResponse(error=str(exc), code=ErrorCode.VALIDATION))
    return _text({"associations": [dict(row) for row in rows]})
