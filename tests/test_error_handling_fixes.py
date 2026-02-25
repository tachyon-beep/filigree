"""Dashboard error handling regression tests.

Covers: filigree-4c2fd9 (batch/close + JSON validation),
        filigree-9e7ed0 (sync-in-async handlers)

CLI tests moved to tests/cli/test_error_handling.py
MCP tests moved to tests/mcp/test_error_handling.py
"""

from __future__ import annotations

import inspect

import pytest
from httpx import ASGITransport, AsyncClient

import filigree.dashboard as dash_module
from filigree.core import FiligreeDB
from filigree.dashboard import create_app


@pytest.fixture
async def dashboard_client(tmp_path) -> AsyncClient:
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
