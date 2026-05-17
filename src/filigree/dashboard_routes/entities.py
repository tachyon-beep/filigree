"""Entity-association HTTP routes (ADR-029, Clarion B.7 / WP9-A).

Mirrors the three MCP tools on the HTTP surface so cross-product
callers (notably Clarion's ``issues_for`` MCP tool, which runs on the
Clarion side and reaches into Filigree via HTTP) can read and write
the binding without going through MCP.

Routes:

- ``GET    /api/issue/{issue_id}/entity-associations`` — list rows
- ``POST   /api/issue/{issue_id}/entity-associations`` — attach (body)
- ``DELETE /api/issue/{issue_id}/entity-associations?entity_id=…`` — remove

The ``entity_id`` contains colons (``py:func:foo``); to keep it out of
URL path parameters it travels in request bodies (POST) and query
strings (DELETE), URL-encoded by the client.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

from starlette.requests import Request

from filigree.core import FiligreeDB, WrongProjectError
from filigree.dashboard_routes.common import _error_response, _parse_json_body
from filigree.types.api import ErrorCode

logger = logging.getLogger(__name__)


def create_classic_router() -> APIRouter:
    """Build the APIRouter for the entity_associations endpoints.

    All handlers are async despite doing synchronous SQLite I/O so DB
    access stays serialised on the event loop thread (matching the
    rest of ``dashboard_routes``).
    """
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse

    from filigree.dashboard import _get_db

    router = APIRouter()

    @router.get("/issue/{issue_id}/entity-associations")
    async def api_list_entity_associations(issue_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Return all entity_associations for *issue_id*.

        Returns raw rows; drift detection is the caller's job per
        ADR-029 §"Decision 3".
        """
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        try:
            rows = db.list_entity_associations(issue_id)
        except WrongProjectError as exc:
            return _error_response(str(exc), ErrorCode.VALIDATION, 400)
        return JSONResponse({"associations": [dict(row) for row in rows]})

    @router.post("/issue/{issue_id}/entity-associations", status_code=201)
    async def api_add_entity_association(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Attach a Clarion entity to *issue_id*. Idempotent on the composite
        key — re-attach refreshes ``content_hash_at_attach`` and ``attached_at``
        while preserving the original ``attached_by``.

        Body: ``{"entity_id": str, "content_hash": str, "actor": str?}``.
        """
        try:
            db.get_issue(issue_id)
        except KeyError:
            return _error_response(f"Issue not found: {issue_id}", ErrorCode.NOT_FOUND, 404)
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        entity_id = body.get("entity_id", "")
        content_hash = body.get("content_hash", "")
        actor = body.get("actor", "")
        if not isinstance(entity_id, str) or not entity_id.strip():
            return _error_response("entity_id is required", ErrorCode.VALIDATION, 400)
        if not isinstance(content_hash, str) or not content_hash.strip():
            return _error_response("content_hash is required", ErrorCode.VALIDATION, 400)
        if not isinstance(actor, str):
            return _error_response("actor must be a string", ErrorCode.VALIDATION, 400)
        try:
            row = db.add_entity_association(issue_id, entity_id, content_hash, actor=actor)
        except WrongProjectError as exc:
            return _error_response(str(exc), ErrorCode.VALIDATION, 400)
        except ValueError as exc:
            # The data layer already validated existence above, but a
            # ValueError can still surface for empty strings if a future
            # refactor weakens the route-side validation.
            code = ErrorCode.NOT_FOUND if "Issue not found" in str(exc) else ErrorCode.VALIDATION
            status = 404 if code == ErrorCode.NOT_FOUND else 400
            return _error_response(str(exc), code, status)
        return JSONResponse(dict(row), status_code=201)

    @router.delete("/issue/{issue_id}/entity-associations")
    async def api_remove_entity_association(issue_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Remove the binding identified by ``(issue_id, entity_id)``.

        The entity_id comes through as a query parameter (URL-encoded)
        because it contains colons that would foul a path parameter.
        Idempotent — returns ``{"removed": false}`` if no row existed.
        """
        entity_id = request.query_params.get("entity_id", "")
        if not isinstance(entity_id, str) or not entity_id.strip():
            return _error_response("entity_id query parameter is required", ErrorCode.VALIDATION, 400)
        try:
            removed = db.remove_entity_association(issue_id, entity_id)
        except WrongProjectError as exc:
            return _error_response(str(exc), ErrorCode.VALIDATION, 400)
        except ValueError as exc:
            return _error_response(str(exc), ErrorCode.VALIDATION, 400)
        return JSONResponse({"removed": removed})

    return router
