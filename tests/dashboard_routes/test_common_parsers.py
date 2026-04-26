"""Tests for query-param parsers in dashboard_routes.common."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from filigree.dashboard_routes.common import _parse_response_detail


def test_response_detail_default_slim() -> None:
    """Missing param returns 'slim' (the default)."""
    result = _parse_response_detail({})
    assert result == "slim"


def test_response_detail_explicit_slim() -> None:
    result = _parse_response_detail({"response_detail": "slim"})
    assert result == "slim"


def test_response_detail_explicit_full() -> None:
    result = _parse_response_detail({"response_detail": "full"})
    assert result == "full"


def test_response_detail_invalid_returns_400() -> None:
    """Unknown values return a 400 JSONResponse with VALIDATION code."""
    result = _parse_response_detail({"response_detail": "banana"})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


def test_response_detail_empty_string_returns_400() -> None:
    """Empty string is rejected — explicit empty is not the same as missing."""
    result = _parse_response_detail({"response_detail": ""})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
