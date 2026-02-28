"""Shared helpers and constants for dashboard route modules."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi.responses import JSONResponse
    from starlette.requests import Request

from filigree.core import FiligreeDB, read_config
from filigree.validation import sanitize_actor as _sanitize_actor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GRAPH_MODE_VALUES = frozenset({"legacy", "v2"})
_GRAPH_STATUS_CATEGORIES = frozenset({"open", "wip", "done"})
_BOOL_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(
    message: str,
    code: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Return a structured error response and log the error."""
    from fastapi.responses import JSONResponse

    logger.warning("API error [%s] %s: %s", status_code, code, message)
    return JSONResponse(
        {"error": {"message": message, "code": code, "details": details or {}}},
        status_code=status_code,
    )


async def _parse_json_body(request: Request) -> dict[str, Any] | JSONResponse:
    """Parse and validate a JSON object body, returning 400 on failure."""
    import json

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return _error_response("Invalid JSON body", "VALIDATION_ERROR", 400)
    if not isinstance(body, dict):
        return _error_response("Request body must be a JSON object", "VALIDATION_ERROR", 400)
    return body


def _parse_pagination(
    params: Mapping[str, str],
    default_limit: int = 100,
) -> tuple[int, int] | JSONResponse:
    """Extract ``limit`` and ``offset`` from query params with validation.

    Returns ``(limit, offset)`` on success or a 400 ``JSONResponse`` on error.
    """
    limit = _safe_int(params.get("limit", str(default_limit)), "limit", min_value=1)
    if not isinstance(limit, int):
        return limit
    offset = _safe_int(params.get("offset", "0"), "offset", min_value=0)
    if not isinstance(offset, int):
        return offset
    return limit, offset


def _safe_int(value: str, name: str, *, min_value: int | None = None) -> int | JSONResponse:
    """Parse a query-param string to int, returning a 400 error response on failure.

    When *min_value* is set, values below that floor are rejected with 400.
    """
    try:
        result = int(value)
    except (ValueError, TypeError):
        return _error_response(
            f'Invalid value for {name}: "{value}". Must be an integer.',
            "VALIDATION_ERROR",
            400,
        )
    if min_value is not None and result < min_value:
        return _error_response(
            f"Invalid value for {name}: {result}. Must be >= {min_value}.",
            "VALIDATION_ERROR",
            400,
        )
    return result


def _parse_bool_value(raw: str, name: str) -> bool | JSONResponse:
    value = raw.strip().lower()
    if value in _BOOL_TRUE_VALUES:
        return True
    if value in _BOOL_FALSE_VALUES:
        return False
    return _error_response(
        f'Invalid value for {name}: "{raw}". Must be one of true/false, 1/0, yes/no, on/off.',
        "GRAPH_INVALID_PARAM",
        400,
        {"param": name, "value": raw},
    )


def _get_bool_param(params: Mapping[str, str], name: str, default: bool) -> bool | JSONResponse:
    """Extract a boolean query param, returning *default* when absent."""
    raw = params.get(name)
    if raw is None:
        return default
    return _parse_bool_value(raw, name)


def _read_graph_runtime_config(db: FiligreeDB) -> dict[str, Any]:
    """Read graph runtime settings from project config, if available.

    Note: read_config() already handles JSONDecodeError/OSError internally
    and returns defaults, so no outer try/except is needed here.
    """
    return dict(read_config(db.db_path.parent))


def _resolve_graph_runtime(db: FiligreeDB) -> dict[str, Any]:
    """Resolve graph feature controls from env + project config."""
    config = _read_graph_runtime_config(db)

    enabled_raw = os.getenv("FILIGREE_GRAPH_V2_ENABLED")
    enabled: bool
    if enabled_raw is not None:
        enabled_value = _parse_bool_value(enabled_raw, "FILIGREE_GRAPH_V2_ENABLED")
        if not isinstance(enabled_value, bool):
            logger.warning("Unparseable FILIGREE_GRAPH_V2_ENABLED=%r, falling back to False", enabled_raw)
        enabled = bool(enabled_value) if isinstance(enabled_value, bool) else False
    else:
        enabled = bool(config.get("graph_v2_enabled", False))

    configured_mode_raw = os.getenv("FILIGREE_GRAPH_API_MODE") or str(config.get("graph_api_mode", "")).strip()
    configured_mode = configured_mode_raw.lower() if configured_mode_raw else ""
    if configured_mode not in _GRAPH_MODE_VALUES:
        configured_mode = ""

    compatibility_mode = configured_mode or ("v2" if enabled else "legacy")
    return {
        "v2_enabled": enabled,
        "configured_mode": configured_mode or None,
        "compatibility_mode": compatibility_mode,
    }


def _parse_csv_param(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _safe_bounded_int(raw: str, *, name: str, min_value: int, max_value: int) -> int | JSONResponse:
    value = _safe_int(raw, name)
    if not isinstance(value, int):
        return value  # pass through _safe_int's error response
    if value < min_value or value > max_value:
        return _error_response(
            f'Invalid value for {name}: "{raw}". Must be between {min_value} and {max_value}.',
            "GRAPH_INVALID_PARAM",
            400,
            {"param": name, "value": raw},
        )
    return value


def _coerce_graph_mode(raw: str | None, db: FiligreeDB) -> str | JSONResponse:
    runtime = _resolve_graph_runtime(db)
    if raw is None:
        return str(runtime["compatibility_mode"])
    mode = raw.strip().lower()
    if mode not in _GRAPH_MODE_VALUES:
        return _error_response(
            f'Invalid value for mode: "{raw}". Must be one of: legacy, v2.',
            "GRAPH_INVALID_PARAM",
            400,
            {"param": "mode", "value": raw},
        )
    return mode


def _validate_priority(value: Any, *, required: bool = False) -> int | None | JSONResponse:
    """Validate a priority value from JSON body.

    Returns the validated int, None (if optional and absent), or a JSONResponse error.
    """
    if value is None:
        if required:
            return _error_response("priority is required", "VALIDATION_ERROR", 400)
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        return _error_response("priority must be an integer between 0 and 4", "INVALID_PRIORITY", 400)
    if not (0 <= value <= 4):
        return _error_response(f"priority must be between 0 and 4, got {value}", "INVALID_PRIORITY", 400)
    return value


def _validate_actor(value: Any) -> tuple[str, JSONResponse | None]:
    """Validate an actor name from JSON body.

    Returns (cleaned_actor, None) on success or ("", JSONResponse) on error.
    """
    cleaned, err = _sanitize_actor(value)
    if err:
        return ("", _error_response(err, "VALIDATION_ERROR", 400))
    return (cleaned, None)
