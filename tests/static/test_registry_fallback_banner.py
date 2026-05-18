from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_dashboard_shell_has_registry_fallback_banner_slot() -> None:
    html = _read("src/filigree/static/dashboard.html")

    assert 'id="registryFallbackBanner"' in html
    assert 'role="status"' in html
    assert "Clarion registry fallback active" in html


def test_dashboard_renders_registry_fallback_banner_from_file_schema() -> None:
    app_js = _read("src/filigree/static/js/app.js")

    assert "fetchFileSchema" in app_js
    assert "registryFallbackBanner" in app_js
    assert "schema?.config_flags?.allow_local_fallback" in app_js
    assert 'banner.classList.remove("hidden")' in app_js
    assert 'banner.classList.add("hidden")' in app_js
