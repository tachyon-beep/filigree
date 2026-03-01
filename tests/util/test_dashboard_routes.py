"""Unit tests for dashboard_routes helpers.

Covers:
- _semver_sort_key (releases.py)
- _parse_bool_value / _get_bool_param (common.py)
- _resolve_graph_runtime (common.py)
- Structural import tests for dashboard_routes/ and cli_commands/
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

from filigree.core import FiligreeDB
from tests._db_factory import make_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB for graph runtime tests."""
    d = make_db(tmp_path)
    yield d
    d.close()


# ---------------------------------------------------------------------------
# _semver_sort_key tests (filigree-51c9acf617)
# ---------------------------------------------------------------------------


class TestSemverSortKey:
    """Unit tests for _semver_sort_key in dashboard_routes/releases.py."""

    def test_three_component_semver(self) -> None:
        from filigree.dashboard_routes.releases import _semver_sort_key

        release = {"version": "v1.2.3", "title": "v1.2.3"}
        assert _semver_sort_key(release) == (1, 2, 3)

    def test_two_component_semver_defaults_patch_to_zero(self) -> None:
        from filigree.dashboard_routes.releases import _semver_sort_key

        release = {"version": "v1.2", "title": "v1.2"}
        assert _semver_sort_key(release) == (1, 2, 0)

    def test_semver_without_v_prefix(self) -> None:
        from filigree.dashboard_routes.releases import _semver_sort_key

        release = {"version": "2.0.1", "title": "2.0.1"}
        assert _semver_sort_key(release) == (2, 0, 1)

    def test_future_title_sorts_last(self) -> None:
        from filigree.dashboard_routes.releases import _FUTURE_KEY, _semver_sort_key

        release = {"version": "", "title": "future"}
        assert _semver_sort_key(release) == _FUTURE_KEY

    def test_future_case_insensitive(self) -> None:
        from filigree.dashboard_routes.releases import _FUTURE_KEY, _semver_sort_key

        assert _semver_sort_key({"version": "", "title": "FUTURE"}) == _FUTURE_KEY
        assert _semver_sort_key({"version": "", "title": "Future"}) == _FUTURE_KEY

    def test_future_with_whitespace(self) -> None:
        from filigree.dashboard_routes.releases import _FUTURE_KEY, _semver_sort_key

        assert _semver_sort_key({"version": "", "title": "  future  "}) == _FUTURE_KEY

    def test_non_semver_fallback_sorts_between_semver_and_future(self) -> None:
        from filigree.dashboard_routes.releases import _semver_sort_key

        semver_key = _semver_sort_key({"version": "v99.99.99", "title": "v99.99.99"})
        fallback_key = _semver_sort_key({"version": "", "title": "Some Random Release"})
        future_key = _semver_sort_key({"version": "", "title": "future"})

        assert semver_key < fallback_key < future_key

    def test_version_field_preferred_over_title(self) -> None:
        from filigree.dashboard_routes.releases import _semver_sort_key

        release = {"version": "v3.0.0", "title": "v1.0.0"}
        assert _semver_sort_key(release) == (3, 0, 0)

    def test_title_used_when_version_empty(self) -> None:
        from filigree.dashboard_routes.releases import _semver_sort_key

        release = {"version": "", "title": "v2.5.1"}
        assert _semver_sort_key(release) == (2, 5, 1)

    def test_sort_order_end_to_end(self) -> None:
        """Full sort order: semver ascending, then non-semver, then future."""
        from filigree.dashboard_routes.releases import _semver_sort_key

        releases = [
            {"version": "", "title": "future"},
            {"version": "v2.0.0", "title": "v2.0.0"},
            {"version": "", "title": "Backlog"},
            {"version": "v1.0.0", "title": "v1.0.0"},
            {"version": "v1.0", "title": "v1.0"},
        ]
        sorted_releases = sorted(releases, key=_semver_sort_key)
        titles = [r["title"] for r in sorted_releases]
        # v1.0 and v1.0.0 are equal keys (1,0,0) so stable sort preserves input order
        assert titles == ["v1.0.0", "v1.0", "v2.0.0", "Backlog", "future"]


# ---------------------------------------------------------------------------
# _parse_bool_value / _get_bool_param tests (filigree-4f2e6e099c)
# ---------------------------------------------------------------------------


class TestParseBoolValue:
    """Unit tests for _parse_bool_value in dashboard_routes/common.py."""

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "TRUE", "True", "YES", "ON"])
    def test_true_values(self, raw: str) -> None:
        from filigree.dashboard_routes.common import _parse_bool_value

        assert _parse_bool_value(raw, "test_param") is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "FALSE", "False", "NO", "OFF"])
    def test_false_values(self, raw: str) -> None:
        from filigree.dashboard_routes.common import _parse_bool_value

        assert _parse_bool_value(raw, "test_param") is False

    def test_whitespace_stripped(self) -> None:
        from filigree.dashboard_routes.common import _parse_bool_value

        assert _parse_bool_value("  true  ", "test_param") is True
        assert _parse_bool_value("  false  ", "test_param") is False

    def test_invalid_value_returns_error_response(self) -> None:
        from filigree.dashboard_routes.common import _parse_bool_value

        result = _parse_bool_value("maybe", "test_param")
        assert not isinstance(result, bool)
        # It's a JSONResponse — verify it has 400 status
        assert result.status_code == 400


class TestGetBoolParam:
    """Unit tests for _get_bool_param in dashboard_routes/common.py."""

    def test_returns_default_when_absent(self) -> None:
        from filigree.dashboard_routes.common import _get_bool_param

        assert _get_bool_param({}, "missing", default=True) is True
        assert _get_bool_param({}, "missing", default=False) is False

    def test_returns_parsed_value_when_present(self) -> None:
        from filigree.dashboard_routes.common import _get_bool_param

        assert _get_bool_param({"flag": "yes"}, "flag", default=False) is True
        assert _get_bool_param({"flag": "no"}, "flag", default=True) is False

    def test_invalid_value_returns_error(self) -> None:
        from filigree.dashboard_routes.common import _get_bool_param

        result = _get_bool_param({"flag": "banana"}, "flag", default=False)
        assert not isinstance(result, bool)
        assert result.status_code == 400


# ---------------------------------------------------------------------------
# _resolve_graph_runtime tests (filigree-fb93e0350a)
# ---------------------------------------------------------------------------


class TestResolveGraphRuntime:
    """Unit tests for _resolve_graph_runtime env+config precedence."""

    def test_env_var_overrides_config(self, graph_db: FiligreeDB, monkeypatch: pytest.MonkeyPatch) -> None:
        from filigree.dashboard_routes.common import _resolve_graph_runtime

        monkeypatch.setenv("FILIGREE_GRAPH_V2_ENABLED", "true")
        result = _resolve_graph_runtime(graph_db)
        assert result["v2_enabled"] is True

    def test_config_fallback_when_env_absent(self, graph_db: FiligreeDB, monkeypatch: pytest.MonkeyPatch) -> None:
        from filigree.dashboard_routes.common import _resolve_graph_runtime

        monkeypatch.delenv("FILIGREE_GRAPH_V2_ENABLED", raising=False)
        # Mock config to return graph_v2_enabled=True
        with patch(
            "filigree.dashboard_routes.common._read_graph_runtime_config",
            return_value={"graph_v2_enabled": True},
        ):
            result = _resolve_graph_runtime(graph_db)
        assert result["v2_enabled"] is True

    def test_unparseable_env_var_falls_back_to_false(self, graph_db: FiligreeDB, monkeypatch: pytest.MonkeyPatch) -> None:
        from filigree.dashboard_routes.common import _resolve_graph_runtime

        monkeypatch.setenv("FILIGREE_GRAPH_V2_ENABLED", "not-a-bool")
        result = _resolve_graph_runtime(graph_db)
        assert result["v2_enabled"] is False

    def test_env_mode_overrides_config_mode(self, graph_db: FiligreeDB, monkeypatch: pytest.MonkeyPatch) -> None:
        from filigree.dashboard_routes.common import _resolve_graph_runtime

        monkeypatch.setenv("FILIGREE_GRAPH_API_MODE", "v2")
        monkeypatch.delenv("FILIGREE_GRAPH_V2_ENABLED", raising=False)
        result = _resolve_graph_runtime(graph_db)
        assert result["configured_mode"] == "v2"
        assert result["compatibility_mode"] == "v2"

    def test_invalid_mode_falls_back_to_empty(self, graph_db: FiligreeDB, monkeypatch: pytest.MonkeyPatch) -> None:
        from filigree.dashboard_routes.common import _resolve_graph_runtime

        monkeypatch.setenv("FILIGREE_GRAPH_API_MODE", "v3-invalid")
        monkeypatch.delenv("FILIGREE_GRAPH_V2_ENABLED", raising=False)
        result = _resolve_graph_runtime(graph_db)
        assert result["configured_mode"] is None

    def test_default_no_env_no_config(self, graph_db: FiligreeDB, monkeypatch: pytest.MonkeyPatch) -> None:
        from filigree.dashboard_routes.common import _resolve_graph_runtime

        monkeypatch.delenv("FILIGREE_GRAPH_V2_ENABLED", raising=False)
        monkeypatch.delenv("FILIGREE_GRAPH_API_MODE", raising=False)
        result = _resolve_graph_runtime(graph_db)
        assert result["v2_enabled"] is False
        assert result["configured_mode"] is None
        assert result["compatibility_mode"] == "legacy"


# ---------------------------------------------------------------------------
# Structural import tests (filigree-878b1e0c40)
# ---------------------------------------------------------------------------


class TestDashboardRoutesStructure:
    """Structural tests for dashboard_routes/ subpackage."""

    def test_all_route_modules_importable(self) -> None:
        from filigree.dashboard_routes import analytics, common, files, issues, releases

        for mod in (analytics, common, files, issues, releases):
            assert mod is not None

    def test_router_modules_expose_create_router(self) -> None:
        from filigree.dashboard_routes import analytics, files, issues, releases

        for mod in (analytics, files, issues, releases):
            assert callable(getattr(mod, "create_router", None)), f"{mod.__name__} missing create_router()"


class TestCliCommandsStructure:
    """Structural tests for cli_commands/ subpackage."""

    def test_all_cli_modules_importable(self) -> None:
        from filigree.cli_commands import admin, issues, meta, planning, server, workflow

        for mod in (admin, issues, meta, planning, server, workflow):
            assert mod is not None

    def test_cli_modules_expose_register(self) -> None:
        from filigree.cli_commands import admin, issues, meta, planning, server, workflow

        for mod in (admin, issues, meta, planning, server, workflow):
            assert callable(getattr(mod, "register", None)), f"{mod.__name__} missing register()"
