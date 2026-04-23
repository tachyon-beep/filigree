"""Release management route handlers."""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

    from filigree.types.planning import ReleaseSummaryItem

from starlette.requests import Request

from filigree.core import FiligreeDB
from filigree.dashboard_routes.common import _error_response, _get_bool_param
from filigree.db_planning import NotAReleaseError
from filigree.types.api import ErrorCode

logger = logging.getLogger(__name__)

_SEMVER_STRICT_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_SEMVER_LOOSE_RE = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")

# Tagged sort keys: a leading "kind" discriminator guarantees that no valid
# semver tuple can collide with a sentinel, regardless of version magnitude.
# 0 = semver, 1 = non-semver, 2 = Future — so the ordering contract
# (semver < non-semver < Future) holds without relying on numeric headroom.
_SemverSortKey = tuple[int, int, int, int]
_NON_SEMVER_KEY: _SemverSortKey = (1, 0, 0, 0)
_FUTURE_KEY: _SemverSortKey = (2, 0, 0, 0)


def _semver_sort_key(release: ReleaseSummaryItem) -> _SemverSortKey:
    """Extract a tagged sort key ``(kind, major, minor, patch)`` from a release.

    Priority order for "Future" detection:
      1. ``version == "Future"`` (exact match on version field)
      2. Title matches "future" (case-insensitive, backward compat)

    For semver parsing, checks version field first (strict 3-part),
    then falls back to title (loose matching).
    Non-semver releases sort after all semver releases but before "future".

    Non-string ``version``/``title`` values (possible via ``import_jsonl``,
    which stores ``fields`` verbatim) are treated as absent rather than being
    passed through to ``re.match``.
    """
    version_raw = release.get("version")
    version = version_raw if isinstance(version_raw, str) else ""
    title_raw = release.get("title", "")
    title = title_raw if isinstance(title_raw, str) else ""

    # Check version field for exact "Future" first
    if version == "Future":
        return _FUTURE_KEY

    # Backward compat: title-based Future detection
    if not version and title.strip().lower() == "future":
        return _FUTURE_KEY

    # Try strict semver on version field
    if version:
        m = _SEMVER_STRICT_RE.match(version)
        if m:
            return (0, int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # Fallback: loose semver on version or title
    text = version or title
    m = _SEMVER_LOOSE_RE.search(text)
    if m:
        return (0, int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))

    # Fallback: after semver releases, before future
    return _NON_SEMVER_KEY


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_classic_router() -> APIRouter:
    """Build the classic-generation APIRouter for release endpoints.

    NOTE: All handlers are intentionally async despite doing synchronous
    SQLite I/O. This serializes DB access on the event loop thread,
    avoiding concurrent multi-thread access to the shared DB connection.

    Classic routes live at their existing unprefixed paths. See ADR-002
    for the generation naming and lifecycle rules.
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
            # Sort is a UI concern — applied here, not in the DB layer.
            # Kept inside the try block so a corrupt release row (e.g.
            # non-string version from import_jsonl) surfaces as a structured
            # error instead of an uncaught exception.
            releases.sort(key=_semver_sort_key)
        except sqlite3.Error:
            logger.exception("Database error loading releases summary")
            return _error_response("Database error loading releases", ErrorCode.IO, 500, exc_info=False)
        except Exception:
            logger.exception("BUG: Unexpected error loading releases summary")
            return _error_response("Internal error loading releases", ErrorCode.INTERNAL, 500, exc_info=False)

        return JSONResponse({"releases": releases})

    @router.get("/release/{release_id}/tree")
    async def api_release_tree(release_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Release hierarchy tree with progress rollups."""
        try:
            tree = db.get_release_tree(release_id)
        except KeyError:
            return _error_response(f"Release not found: {release_id}", ErrorCode.NOT_FOUND, 404)
        except NotAReleaseError as e:
            # Asking for a /release/<id>/tree on an id that exists but is not
            # a release is still a "not a release of that id" — matching the
            # 404 status with NOT_FOUND keeps the envelope internally
            # consistent (status and code agree).
            return _error_response(str(e), ErrorCode.NOT_FOUND, 404)
        except sqlite3.Error:
            logger.exception("Database error loading release tree for %s", release_id)
            return _error_response("Database error loading release tree", ErrorCode.IO, 500, exc_info=False)
        except Exception:
            # Includes bare ValueError from corrupt imported data (e.g. Issue.__post_init__)
            # — that is data corruption, not a release-type mismatch.
            logger.exception("BUG: Unexpected error loading release tree for %s", release_id)
            return _error_response("Internal error loading release tree", ErrorCode.INTERNAL, 500, exc_info=False)
        return JSONResponse(tree)

    return router


def create_loom_router() -> APIRouter:
    """Build the loom-generation APIRouter for release endpoints.

    Empty in Phase B of the 2.0 federation work package; Phase C fills
    loom release endpoints as they are implemented. See ADR-002 for the
    generation framing and docs/federation/contracts.md for the stability
    guarantee.
    """
    from fastapi import APIRouter

    return APIRouter()
