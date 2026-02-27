"""Release management route handlers."""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request

from filigree.core import FiligreeDB
from filigree.dashboard_routes.common import _error_response, _get_bool_param

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_router() -> Any:
    """Build the APIRouter for release endpoints.

    NOTE: All handlers are intentionally async despite doing synchronous
    SQLite I/O. This serializes DB access on the event loop thread,
    avoiding concurrent multi-thread access to the shared DB connection.
    """
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse

    from filigree.dashboard import _get_db

    router = APIRouter()

    @router.get("/releases")
    async def api_releases(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """List releases with progress rollups."""
        include_released = _get_bool_param(request.query_params, "include_released", False)
        if not isinstance(include_released, bool):
            return include_released  # propagate the 400 error response

        try:
            releases = db.get_releases_summary(include_released=include_released)
        except Exception:
            logger.exception("Failed to load releases summary")
            return _error_response("Internal error loading releases", "RELEASES_LOAD_ERROR", 500)

        # Sort is a UI concern — applied here, not in the DB layer
        # Unblocked first (actionability), then priority ASC, then created_at ASC
        releases.sort(key=lambda r: (len(r["blocked_by"]) > 0, r["priority"], r["created_at"]))

        return JSONResponse({"releases": releases})

    @router.get("/release/{release_id}/tree")
    async def api_release_tree(release_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Release hierarchy tree with progress rollups."""
        try:
            tree = db.get_release_tree(release_id)
        except KeyError:
            return _error_response(f"Release not found: {release_id}", "RELEASE_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "NOT_A_RELEASE", 404)
        except Exception:
            logger.exception("Failed to load release tree for %s", release_id)
            return _error_response("Internal error loading release tree", "TREE_LOAD_ERROR", 500)
        return JSONResponse(tree)

    return router
