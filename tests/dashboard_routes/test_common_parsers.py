"""Tests for query-param parsers in dashboard_routes.common."""

from __future__ import annotations

import pytest
from fastapi.responses import JSONResponse

from filigree.dashboard_routes import common as common_module
from filigree.dashboard_routes.common import _coerce_graph_mode, _parse_response_detail


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


class TestCoerceGraphModeAvoidsConfigIO:
    """filigree-1eaf84f2c3: explicit `?mode=` requests must not call
    `_resolve_graph_runtime` (which reads project config from disk). The
    documented precedence puts request mode ahead of compatibility/feature-flag
    defaults, so the runtime resolution is wasted work on the override path.
    """

    def test_explicit_mode_skips_runtime_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[object] = []

        def tracked(db: object) -> dict[str, object]:
            calls.append(db)
            return {"v2_enabled": False, "configured_mode": None, "compatibility_mode": "legacy"}

        monkeypatch.setattr(common_module, "_resolve_graph_runtime", tracked)
        sentinel_db: object = object()
        result = _coerce_graph_mode("v2", sentinel_db)  # type: ignore[arg-type]
        assert result == "v2"
        assert calls == [], "explicit mode override must not trigger _resolve_graph_runtime"

    def test_invalid_explicit_mode_skips_runtime_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Validation of the bad raw value must also be cheap — no disk I/O."""
        calls: list[object] = []

        def tracked(db: object) -> dict[str, object]:
            calls.append(db)
            return {"v2_enabled": False, "configured_mode": None, "compatibility_mode": "legacy"}

        monkeypatch.setattr(common_module, "_resolve_graph_runtime", tracked)
        sentinel_db: object = object()
        result = _coerce_graph_mode("nope", sentinel_db)  # type: ignore[arg-type]
        assert isinstance(result, JSONResponse)
        assert result.status_code == 400
        assert calls == []

    def test_missing_mode_falls_back_to_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no `?mode=` is supplied, the runtime resolution IS the source."""
        calls: list[object] = []

        def tracked(db: object) -> dict[str, object]:
            calls.append(db)
            return {"v2_enabled": True, "configured_mode": "v2", "compatibility_mode": "v2"}

        monkeypatch.setattr(common_module, "_resolve_graph_runtime", tracked)
        sentinel_db: object = object()
        result = _coerce_graph_mode(None, sentinel_db)  # type: ignore[arg-type]
        assert result == "v2"
        assert calls == [sentinel_db]
