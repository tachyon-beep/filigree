"""Tests for error handling fixes across CLI, MCP server, and dashboard.

Covers: filigree-8a7e6a, filigree-537425, filigree-d6c3f6,
        filigree-4c2fd9, filigree-9e7ed0
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient

import filigree.dashboard as dash_module
from filigree.cli import cli
from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, SUMMARY_FILENAME, FiligreeDB, write_config
from filigree.dashboard import create_app
from filigree.mcp_server import call_tool

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_in_project(tmp_path: Path, cli_runner: CliRunner) -> tuple[CliRunner, Path]:
    """Initialize a filigree project in tmp_path and return (runner, project_root)."""
    original_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    result = cli_runner.invoke(cli, ["init", "--prefix", "test"])
    assert result.exit_code == 0
    yield cli_runner, tmp_path
    os.chdir(original_cwd)


def _extract_id(create_output: str) -> str:
    """Extract issue ID from 'Created test-abc123: Title' output."""
    return create_output.split(":")[0].replace("Created ", "").strip()


def _parse(result: list[Any]) -> Any:
    """Extract text from MCP response and parse as JSON."""
    text = result[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


@pytest.fixture
def mcp_db(tmp_path: Path) -> FiligreeDB:
    """Set up a FiligreeDB and patch the MCP module globals."""
    filigree_dir = tmp_path / FILIGREE_DIR_NAME
    filigree_dir.mkdir()
    write_config(filigree_dir, {"prefix": "mcp", "version": 1})
    (filigree_dir / SUMMARY_FILENAME).write_text("# test\n")

    d = FiligreeDB(filigree_dir / DB_FILENAME, prefix="mcp")
    d.initialize()

    import filigree.mcp_server as mcp_mod

    original_db = mcp_mod.db
    original_dir = mcp_mod._filigree_dir
    mcp_mod.db = d
    mcp_mod._filigree_dir = filigree_dir

    yield d

    mcp_mod.db = original_db
    mcp_mod._filigree_dir = original_dir
    d.close()


@pytest.fixture
async def dashboard_client(tmp_path: Path) -> AsyncClient:
    """Create a test client with a fresh populated DB for dashboard tests."""
    d = FiligreeDB(tmp_path / "filigree.db", prefix="test", check_same_thread=False)
    d.initialize()
    # Create some test issues
    a = d.create_issue("Issue A", priority=1)
    b = d.create_issue("Issue B", priority=2)
    c = d.create_issue("Issue C", priority=3)
    d.close_issue(c.id, reason="done")
    d._test_ids = {"a": a.id, "b": b.id, "c": c.id}  # type: ignore[attr-defined]

    dash_module._db = d
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    dash_module._db = None
    d.close()


# ===========================================================================
# Bug 1: CLI reopen exit code (filigree-8a7e6a)
# ===========================================================================


class TestReopenExitCode:
    """CLI reopen must exit non-zero on error."""

    def test_reopen_nonexistent_exits_nonzero(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["reopen", "nonexistent-abc"])
        assert result.exit_code != 0, f"Expected non-zero exit code, got {result.exit_code}"
        assert "Not found" in result.output

    def test_reopen_nonexistent_json_exits_nonzero(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["reopen", "nonexistent-abc", "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output.splitlines()[0])
        assert "error" in data

    def test_reopen_open_issue_exits_nonzero(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Reopening an already-open issue should fail (ValueError path)."""
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Not closed"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["reopen", issue_id])
        assert result.exit_code != 0


# ===========================================================================
# Bug 2: create --json field error (filigree-537425)
# ===========================================================================


class TestCreateJsonFieldError:
    """create --json must emit JSON errors for bad field format."""

    def test_create_bad_field_json_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Bad field", "-f", "no_equals_sign", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "Invalid field format" in data["error"]

    def test_create_bad_field_text_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Without --json, error goes to stderr as text."""
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Bad field", "-f", "no_equals_sign"])
        assert result.exit_code == 1
        assert "Invalid field format" in result.output


# ===========================================================================
# Bug 3: MCP comment/label error handling (filigree-d6c3f6)
# ===========================================================================


class TestMCPCommentErrors:
    """MCP add_comment and get_comments on missing issues."""

    async def test_add_comment_missing_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "add_comment",
            {"issue_id": "nonexistent-xyz", "text": "Hello"},
        )
        data = _parse(result)
        assert data["code"] == "not_found"
        assert "nonexistent-xyz" in data["error"]

    async def test_get_comments_missing_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "get_comments",
            {"issue_id": "nonexistent-xyz"},
        )
        data = _parse(result)
        assert data["code"] == "not_found"
        assert "nonexistent-xyz" in data["error"]


class TestMCPLabelErrors:
    """MCP add_label and remove_label on missing issues."""

    async def test_add_label_missing_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "add_label",
            {"issue_id": "nonexistent-xyz", "label": "bug"},
        )
        data = _parse(result)
        assert data["code"] == "not_found"
        assert "nonexistent-xyz" in data["error"]

    async def test_remove_label_missing_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "remove_label",
            {"issue_id": "nonexistent-xyz", "label": "bug"},
        )
        data = _parse(result)
        assert data["code"] == "not_found"
        assert "nonexistent-xyz" in data["error"]

    async def test_add_label_still_works(self, mcp_db: FiligreeDB) -> None:
        """Verify normal add_label still works after the fix."""
        issue = mcp_db.create_issue("Labelable")
        result = await call_tool(
            "add_label",
            {"issue_id": issue.id, "label": "bug"},
        )
        data = _parse(result)
        assert data["status"] == "added"

    async def test_add_comment_still_works(self, mcp_db: FiligreeDB) -> None:
        """Verify normal add_comment still works after the fix."""
        issue = mcp_db.create_issue("Commentable")
        result = await call_tool(
            "add_comment",
            {"issue_id": issue.id, "text": "Hello"},
        )
        data = _parse(result)
        assert data["status"] == "ok"


# ===========================================================================
# Bug 4: Dashboard batch/close + JSON validation (filigree-4c2fd9)
# ===========================================================================


class TestDashboardBatchCloseKeyError:
    """POST /api/batch/close with nonexistent ID returns per-item error."""

    async def test_batch_close_nonexistent_id(self, dashboard_client: AsyncClient) -> None:
        resp = await dashboard_client.post(
            "/api/batch/close",
            json={"issue_ids": ["nonexistent-xyz"]},
        )
        # Returns 200 with per-item error collection (not fail-fast 404)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["closed"]) == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["id"] == "nonexistent-xyz"


class TestDashboardMalformedJSON:
    """Endpoints should return 400 for malformed JSON bodies."""

    async def test_update_malformed_json(self, dashboard_client: AsyncClient) -> None:
        resp = await dashboard_client.patch(
            "/api/issue/test-abc123",
            content=b"not valid json{{{",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_close_malformed_json(self, dashboard_client: AsyncClient) -> None:
        resp = await dashboard_client.post(
            "/api/issue/test-abc123/close",
            content=b"not valid json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_create_malformed_json(self, dashboard_client: AsyncClient) -> None:
        resp = await dashboard_client.post(
            "/api/issues",
            content=b"{broken",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_batch_close_malformed_json(self, dashboard_client: AsyncClient) -> None:
        resp = await dashboard_client.post(
            "/api/batch/close",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_batch_update_malformed_json(self, dashboard_client: AsyncClient) -> None:
        resp = await dashboard_client.post(
            "/api/batch/update",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()


# ===========================================================================
# Bug 5: Dashboard sync-in-async (filigree-9e7ed0)
# ===========================================================================


class TestDashboardHandlersAreAsync:
    """All endpoints must be async to avoid thread pool dispatch and shared-DB races.

    Supersedes the old sync-handler test. See TestDashboardConcurrency in test_dashboard.py
    for the full concurrency safety test (filigree-4b8e41).
    """

    def test_all_handlers_are_async(self) -> None:
        """All route handlers must be async def (not plain def)."""
        app = create_app()
        for route in app.routes:
            if not hasattr(route, "endpoint"):
                continue
            handler = route.endpoint  # type: ignore[union-attr]
            assert inspect.iscoroutinefunction(handler), (
                f"Handler {route.path} must be async def to avoid thread pool dispatch"  # type: ignore[union-attr]
            )
