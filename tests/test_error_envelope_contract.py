"""Cross-surface contract for the 2.0 flat error envelope (review finding #12).

test_error_format.py pins the shape only for the dashboard surface.
This module parameterises the same structural contract across all three
emit surfaces — dashboard (FastAPI), MCP (in-process tool handler), and
CLI (--json flag) — so a regression on any surface lights up red.

Shape invariants (from ErrorResponse TypedDict, types/api.py):
- Exactly 2 or 3 top-level keys: {error, code, details?}.
- ``error`` is a non-empty string (never a nested dict — old shape).
- ``code`` is one of the 11 ErrorCode member values (uppercase).
- No legacy keys: no ``message``, no ``message_detail``, no lowercase code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

import filigree.dashboard as dash
from filigree.cli import cli
from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, SUMMARY_FILENAME, FiligreeDB, write_config
from filigree.dashboard import create_app
from filigree.mcp_tools.issues import _handle_get_issue
from filigree.types.api import ErrorCode

_VALID_CODES: frozenset[str] = frozenset(e.value for e in ErrorCode)


def _assert_flat_envelope(payload: dict, *, surface: str) -> None:
    """Assert ``payload`` conforms to the 2.0 flat error envelope.

    Shared helper so every surface asserts the same invariants.
    """
    assert isinstance(payload, dict), f"[{surface}] payload is not a dict: {payload!r}"

    # Required keys
    assert "error" in payload, f"[{surface}] missing 'error' key: {payload!r}"
    assert "code" in payload, f"[{surface}] missing 'code' key: {payload!r}"

    # error is a string, not a nested dict (old shape)
    assert isinstance(payload["error"], str), (
        f"[{surface}] 'error' is not a string — old nested shape detected: {payload!r}"
    )
    assert payload["error"], f"[{surface}] 'error' is empty: {payload!r}"

    # code is a valid ErrorCode member value
    code = payload["code"]
    assert isinstance(code, str), f"[{surface}] 'code' is not a string: {code!r}"
    assert code in _VALID_CODES, f"[{surface}] 'code' = {code!r} is not in ErrorCode"

    # No legacy fields at the top level
    assert "message" not in payload, f"[{surface}] legacy 'message' key present: {payload!r}"
    assert "message_detail" not in payload, (
        f"[{surface}] legacy 'message_detail' key present: {payload!r}"
    )

    # Allowed keys: error, code, optional details. Anything else is either
    # a per-surface carve-out (batch results with succeeded/failed) or a
    # violation.
    allowed_top_level = {"error", "code", "details"}
    extra = set(payload.keys()) - allowed_top_level
    # Allow transition-error hints (valid_transitions, hint) for now since
    # TransitionError carries them as top-level optional fields. Anything
    # else is a violation.
    extra -= {"valid_transitions", "hint"}
    assert not extra, (
        f"[{surface}] unexpected top-level keys {extra!r} — extras should be in 'details': "
        f"{payload!r}"
    )


# ---------------------------------------------------------------------------
# Dashboard surface
# ---------------------------------------------------------------------------


@pytest.fixture
def dashboard_client(filigree_project: Path) -> TestClient:
    filigree_dir = filigree_project / FILIGREE_DIR_NAME
    db = FiligreeDB.from_filigree_dir(filigree_dir, check_same_thread=False)

    original_db = dash._db
    original_store = dash._project_store
    dash._db = db
    dash._project_store = None
    app = create_app(server_mode=False)
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    dash._db = original_db
    dash._project_store = original_store
    db.close()


class TestDashboardSurface:
    def test_404_issue_not_found(self, dashboard_client: TestClient) -> None:
        resp = dashboard_client.get("/api/issue/definitely-does-not-exist")
        assert resp.status_code == 404
        _assert_flat_envelope(resp.json(), surface="dashboard")

    def test_400_invalid_json_body(self, dashboard_client: TestClient) -> None:
        resp = dashboard_client.post(
            "/api/issues",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        _assert_flat_envelope(resp.json(), surface="dashboard")

    def test_400_unknown_type_template(self, dashboard_client: TestClient) -> None:
        resp = dashboard_client.get("/api/type/definitely-not-a-real-type")
        assert resp.status_code == 400
        _assert_flat_envelope(resp.json(), surface="dashboard")


# ---------------------------------------------------------------------------
# MCP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def envelope_mcp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FiligreeDB:
    """Minimal MCP project for envelope testing.

    Inlined rather than depending on tests/mcp/conftest.py's mcp_db fixture
    because this test module lives at tests/ root so the dashboard and CLI
    sub-tests can share the _assert_flat_envelope helper without import
    gymnastics.
    """
    filigree_dir = tmp_path / FILIGREE_DIR_NAME
    filigree_dir.mkdir()
    write_config(filigree_dir, {"prefix": "env", "version": 1})
    (filigree_dir / SUMMARY_FILENAME).write_text("# envelope-contract\n")
    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix="env")
    db.initialize()

    import filigree.mcp_server as mcp_mod

    monkeypatch.setattr(mcp_mod, "db", db)
    monkeypatch.setattr(mcp_mod, "_filigree_dir", filigree_dir)
    return db


class TestMCPSurface:
    async def test_get_issue_not_found(self, envelope_mcp_db: FiligreeDB) -> None:
        """MCP NOT_FOUND emits the same envelope as dashboard + CLI."""
        import json as json_mod

        result = await _handle_get_issue({"id": "env-ffffffffff"})
        # _text returns list[TextContent]; .text is the JSON-serialised payload.
        payload = json_mod.loads(result[0].text)
        _assert_flat_envelope(payload, surface="mcp")
        assert payload["code"] == ErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCLISurface:
    def test_show_missing_id_json(self, initialized_project: Path) -> None:
        """`filigree show <missing-id> --json` emits the 2.0 envelope."""
        import os

        original = os.getcwd()
        os.chdir(initialized_project)
        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["show", "test-ffffffffff", "--json"])
            assert result.exit_code != 0
            payload = json.loads(result.output)
            _assert_flat_envelope(payload, surface="cli")
            assert payload["code"] == ErrorCode.NOT_FOUND
        finally:
            os.chdir(original)
