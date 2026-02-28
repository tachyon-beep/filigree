"""Release management route handlers."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import APIRouter

from starlette.requests import Request

from filigree.core import FiligreeDB
from filigree.dashboard_routes.common import _error_response, _get_bool_param

logger = logging.getLogger(__name__)

_SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")
_FUTURE_KEY = (float("inf"), 0, 0)


def _semver_sort_key(release: dict[str, Any]) -> tuple[float, int, int]:
    """Extract a (major, minor, patch) sort key from a release.

    Checks the ``version`` field first, then falls back to the title.
    Releases whose title matches "future" (case-insensitive) sort last.
    """
    version = release.get("version") or ""
    title = release.get("title", "")

    if title.strip().lower() == "future":
        return _FUTURE_KEY

    text = version or title
    m = _SEMVER_RE.search(text)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))

    # Fallback: after semver releases, before future
    return (float("inf") - 1, 0, 0)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_router() -> APIRouter:
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
        # Primary: semantic version ascending; "future" always last
        releases.sort(key=_semver_sort_key)

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
