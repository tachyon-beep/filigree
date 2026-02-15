"""Tests for the filigree web dashboard API."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

# We need to mock the _db module-level variable in dashboard
import filigree.dashboard as dash_module
from filigree.core import FiligreeDB
from filigree.dashboard import STATIC_DIR, create_app


@pytest.fixture
def dashboard_db(populated_db: FiligreeDB) -> FiligreeDB:
    """Use the populated_db fixture for dashboard tests."""
    return populated_db


@pytest.fixture
async def client(dashboard_db: FiligreeDB) -> AsyncClient:
    """Create a test client for the dashboard FastAPI app."""
    # Patch module-level globals
    dash_module._db = dashboard_db
    dash_module._prefix = "test"
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._db = None


class TestDashboardIndex:
    async def test_serves_html(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Filigree" in resp.text

    async def test_html_file_exists(self) -> None:
        assert (STATIC_DIR / "dashboard.html").exists()


class TestIssuesAPI:
    async def test_list_all_issues(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        resp = await client.get("/api/issues")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 4  # epic + A + B + C

    async def test_issue_structure(self, client: AsyncClient) -> None:
        resp = await client.get("/api/issues")
        data = resp.json()
        issue = data[0]
        # Check expected fields
        for field in ["id", "title", "status", "priority", "type", "blocks", "blocked_by", "is_ready"]:
            assert field in issue


class TestGraphAPI:
    async def test_graph_structure(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)

    async def test_graph_nodes_have_required_fields(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph")
        data = resp.json()
        for node in data["nodes"]:
            assert "id" in node
            assert "title" in node
            assert "status" in node
            assert "priority" in node
            assert "type" in node

    async def test_graph_edges_from_dependencies(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph")
        data = resp.json()
        # populated_db has A depends on B, so there should be an edge
        assert len(data["edges"]) >= 1


class TestStatsAPI:
    async def test_stats_structure(self, client: AsyncClient) -> None:
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "by_status" in data
        assert "by_type" in data
        assert "ready_count" in data
        assert "blocked_count" in data
        assert "total_dependencies" in data
        assert "prefix" in data

    async def test_stats_prefix(self, client: AsyncClient) -> None:
        resp = await client.get("/api/stats")
        data = resp.json()
        assert data["prefix"] == "test"

    async def test_stats_counts(self, client: AsyncClient) -> None:
        resp = await client.get("/api/stats")
        data = resp.json()
        assert data["by_status"]["closed"] == 1
        assert data["total_dependencies"] >= 1


class TestIssueDetailAPI:
    async def test_issue_detail(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.get(f"/api/issue/{ids['a']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == ids["a"]
        assert data["title"] == "Issue A"

    async def test_issue_detail_includes_deps(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.get(f"/api/issue/{ids['a']}")
        data = resp.json()
        assert "dep_details" in data
        assert "events" in data
        assert "comments" in data

    async def test_issue_detail_blocked_by_details(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.get(f"/api/issue/{ids['a']}")
        data = resp.json()
        # A is blocked by B
        assert ids["b"] in data["blocked_by"]
        assert ids["b"] in data["dep_details"]
        dep = data["dep_details"][ids["b"]]
        assert dep["title"] == "Issue B"
        assert dep["status"] == "open"
        assert "status_category" in dep

    async def test_issue_detail_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/issue/nonexistent")
        assert resp.status_code == 404

    async def test_issue_with_comments(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.get(f"/api/issue/{ids['b']}")
        data = resp.json()
        assert len(data["comments"]) == 1
        assert data["comments"][0]["text"] == "Test comment"
        assert data["comments"][0]["author"] == "tester"


class TestDependenciesAPI:
    async def test_dependencies_list(self, client: AsyncClient) -> None:
        resp = await client.get("/api/dependencies")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        dep = data[0]
        assert "from" in dep
        assert "to" in dep
        assert "type" in dep


class TestTypeTemplateAPI:
    """WFT-FR-065: /api/type/{type_name} endpoint."""

    async def test_type_template_endpoint(self, client: AsyncClient) -> None:
        resp = await client.get("/api/type/bug")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "bug"
        assert data["display_name"] == "Bug Report"
        assert len(data["states"]) >= 4
        assert len(data["transitions"]) >= 4
        assert data["initial_state"] == "triage"
        # Each state has name + category
        for state in data["states"]:
            assert "name" in state
            assert "category" in state

    async def test_type_template_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/type/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()


class TestWorkflowAwareAPI:
    """Phase 4: API responses include category-level data."""

    async def test_stats_includes_by_category(self, client: AsyncClient) -> None:
        resp = await client.get("/api/stats")
        data = resp.json()
        assert "by_category" in data
        by_cat = data["by_category"]
        assert "open" in by_cat
        assert "wip" in by_cat
        assert "done" in by_cat

    async def test_graph_nodes_include_status_category(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph")
        data = resp.json()
        for node in data["nodes"]:
            assert "status_category" in node

    async def test_issues_include_status_category(self, client: AsyncClient) -> None:
        resp = await client.get("/api/issues")
        data = resp.json()
        for issue in data:
            assert "status_category" in issue


class TestTransitionsAPI:
    """GET /api/issue/{issue_id}/transitions — valid next states."""

    async def test_transitions_for_open_issue(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.get(f"/api/issue/{ids['b']}/transitions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        # Each transition has required fields
        for t in data:
            assert "to" in t
            assert "category" in t
            assert "ready" in t

    async def test_transitions_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/issue/nonexistent/transitions")
        assert resp.status_code == 404
        assert "error" in resp.json()


class TestUpdateAPI:
    """PATCH /api/issue/{issue_id} — update issue fields."""

    async def test_update_priority(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(
            f"/api/issue/{ids['b']}",
            json={"priority": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["priority"] == 0

    async def test_update_assignee(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(
            f"/api/issue/{ids['b']}",
            json={"assignee": "alice"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["assignee"] == "alice"

    async def test_update_status(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(
            f"/api/issue/{ids['b']}",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "in_progress"

    async def test_update_title(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(
            f"/api/issue/{ids['b']}",
            json={"title": "Renamed Issue B"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Renamed Issue B"

    async def test_update_not_found(self, client: AsyncClient) -> None:
        resp = await client.patch(
            "/api/issue/nonexistent",
            json={"priority": 1},
        )
        assert resp.status_code == 404
        assert "error" in resp.json()

    async def test_update_invalid_transition(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        # Trying to transition to an invalid state should 409
        resp = await client.patch(
            f"/api/issue/{ids['b']}",
            json={"status": "totally_bogus_state"},
        )
        assert resp.status_code == 409
        assert "error" in resp.json()

    async def test_update_actor_defaults_to_dashboard(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(
            f"/api/issue/{ids['b']}",
            json={"priority": 3},
        )
        assert resp.status_code == 200
        # Verify actor was recorded (check events)
        detail_resp = await client.get(f"/api/issue/{ids['b']}")
        events = detail_resp.json()["events"]
        # Most recent event should have actor "dashboard"
        assert any(e.get("actor") == "dashboard" for e in events)

    async def test_update_custom_actor(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(
            f"/api/issue/{ids['b']}",
            json={"priority": 1, "actor": "bot-1"},
        )
        assert resp.status_code == 200
        detail_resp = await client.get(f"/api/issue/{ids['b']}")
        events = detail_resp.json()["events"]
        assert any(e.get("actor") == "bot-1" for e in events)


class TestCloseReopenAPI:
    """POST /api/issue/{issue_id}/close and /reopen."""

    async def test_close_issue(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['b']}/close",
            json={"reason": "completed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status_category"] == "done"

    async def test_close_already_closed(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        # C is already closed
        resp = await client.post(
            f"/api/issue/{ids['c']}/close",
            json={},
        )
        assert resp.status_code == 409
        assert "error" in resp.json()

    async def test_close_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issue/nonexistent/close",
            json={},
        )
        assert resp.status_code == 404
        assert "error" in resp.json()

    async def test_reopen_closed_issue(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        # C is closed — reopen it
        resp = await client.post(
            f"/api/issue/{ids['c']}/reopen",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status_category"] == "open"

    async def test_reopen_not_closed(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        # B is open — can't reopen
        resp = await client.post(
            f"/api/issue/{ids['b']}/reopen",
            json={},
        )
        assert resp.status_code == 409
        assert "error" in resp.json()

    async def test_reopen_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issue/nonexistent/reopen",
            json={},
        )
        assert resp.status_code == 404
        assert "error" in resp.json()

    async def test_close_with_actor(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['b']}/close",
            json={"actor": "bot-2"},
        )
        assert resp.status_code == 200

    async def test_reopen_with_actor(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        # C is closed, reopen with actor
        resp = await client.post(
            f"/api/issue/{ids['c']}/reopen",
            json={"actor": "bot-3"},
        )
        assert resp.status_code == 200


class TestDashboardGetDb:
    """Cover _get_db when _db is None (lines 29-30)."""

    def test_get_db_raises_when_none(self) -> None:
        import filigree.dashboard as dm

        original = dm._db
        dm._db = None
        try:
            with pytest.raises(RuntimeError, match="Database not initialized"):
                dm._get_db()
        finally:
            dm._db = original
