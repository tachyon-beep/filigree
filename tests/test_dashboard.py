"""Tests for the filigree web dashboard API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import filigree.dashboard as dash_module
from filigree.core import FiligreeDB
from filigree.dashboard import STATIC_DIR, create_app


@pytest.fixture
def dashboard_db(populated_db: FiligreeDB) -> FiligreeDB:
    """Use the populated_db fixture for dashboard tests.

    Enables check_same_thread=False so sync handlers run in FastAPI's threadpool.
    """
    populated_db._check_same_thread = False
    if populated_db._conn is not None:
        populated_db._conn.commit()
        populated_db._conn.close()
        populated_db._conn = None
    return populated_db


@pytest.fixture
async def client(dashboard_db: FiligreeDB, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """Create a test client backed by a single-project DB (ethereal mode)."""
    dash_module._db = dashboard_db
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
        err = resp.json()["error"]
        assert err["code"] == "ISSUE_NOT_FOUND"
        assert "nonexistent" in err["message"]

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
        err = resp.json()["error"]
        assert err["code"] == "INVALID_TYPE"
        # Error message must include the invalid value and valid types
        assert "nonexistent" in err["message"]
        assert "task" in err["message"]
        assert "bug" in err["message"]


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


class TestCommentAPI:
    """POST /api/issue/{issue_id}/comments — add a comment."""

    async def test_add_comment(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['b']}/comments",
            json={"text": "A new comment", "author": "alice"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["text"] == "A new comment"
        assert data["author"] == "alice"
        assert "id" in data
        assert "created_at" in data

    async def test_add_comment_default_author(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['a']}/comments",
            json={"text": "No author specified"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["text"] == "No author specified"
        assert data["author"] == ""

    async def test_add_comment_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issue/nonexistent/comments",
            json={"text": "orphan comment"},
        )
        assert resp.status_code == 404
        assert "error" in resp.json()

    async def test_add_comment_empty_text(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['b']}/comments",
            json={"text": ""},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_add_comment_whitespace_text(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['b']}/comments",
            json={"text": "   "},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()


class TestSearchAPI:
    """GET /api/search?q=... — server-side FTS5 search."""

    async def test_search_finds_issue(self, client: AsyncClient) -> None:
        resp = await client.get("/api/search", params={"q": "Issue A"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "total" in data
        assert data["total"] >= 1
        titles = [r["title"] for r in data["results"]]
        assert "Issue A" in titles

    async def test_search_empty_query(self, client: AsyncClient) -> None:
        resp = await client.get("/api/search", params={"q": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total"] == 0

    async def test_search_no_results(self, client: AsyncClient) -> None:
        resp = await client.get("/api/search", params={"q": "zzzznonexistentzzzz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["results"] == []

    async def test_search_with_limit(self, client: AsyncClient) -> None:
        resp = await client.get("/api/search", params={"q": "Issue", "limit": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) <= 1

    async def test_search_with_offset(self, client: AsyncClient) -> None:
        # Get total first
        resp_all = await client.get("/api/search", params={"q": "Issue"})
        total_all = resp_all.json()["total"]
        # Now with offset
        resp = await client.get("/api/search", params={"q": "Issue", "offset": 1})
        data = resp.json()
        assert data["total"] <= total_all


class TestMetricsAPI:
    """GET /api/metrics?days=30 — flow metrics."""

    async def test_metrics_default(self, client: AsyncClient) -> None:
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "period_days" in data
        assert "throughput" in data
        assert "avg_cycle_time_hours" in data
        assert "avg_lead_time_hours" in data
        assert "by_type" in data
        assert data["period_days"] == 30

    async def test_metrics_custom_days(self, client: AsyncClient) -> None:
        resp = await client.get("/api/metrics", params={"days": 7})
        assert resp.status_code == 200
        data = resp.json()
        assert data["period_days"] == 7

    async def test_metrics_throughput(self, client: AsyncClient) -> None:
        # populated_db has 1 closed issue (C), so throughput should be >= 1
        resp = await client.get("/api/metrics", params={"days": 365})
        data = resp.json()
        assert data["throughput"] >= 1


class TestCriticalPathAPI:
    """GET /api/critical-path — longest dependency chain."""

    async def test_critical_path(self, client: AsyncClient) -> None:
        resp = await client.get("/api/critical-path")
        assert resp.status_code == 200
        data = resp.json()
        assert "path" in data
        assert "length" in data
        assert isinstance(data["path"], list)
        assert data["length"] == len(data["path"])

    async def test_critical_path_has_dep_chain(self, client: AsyncClient) -> None:
        # A depends on B, both open, so the critical path should be >= 2
        resp = await client.get("/api/critical-path")
        data = resp.json()
        assert data["length"] >= 2

    async def test_critical_path_node_structure(self, client: AsyncClient) -> None:
        resp = await client.get("/api/critical-path")
        data = resp.json()
        if data["length"] > 0:
            node = data["path"][0]
            assert "id" in node
            assert "title" in node
            assert "priority" in node
            assert "type" in node


class TestActivityAPI:
    """GET /api/activity — recent events across all issues."""

    async def test_activity_default(self, client: AsyncClient) -> None:
        resp = await client.get("/api/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # populated_db creates events for epic, A, B, C (created, closed, dep_added, comment)
        assert len(data) >= 1

    async def test_activity_has_issue_title(self, client: AsyncClient) -> None:
        resp = await client.get("/api/activity")
        data = resp.json()
        assert len(data) >= 1
        event = data[0]
        assert "issue_id" in event
        assert "event_type" in event
        assert "issue_title" in event
        assert "created_at" in event

    async def test_activity_with_limit(self, client: AsyncClient) -> None:
        resp = await client.get("/api/activity", params={"limit": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 2

    async def test_activity_with_since(self, client: AsyncClient) -> None:
        # Use a very old timestamp to get all events
        resp = await client.get("/api/activity", params={"since": "2020-01-01T00:00:00"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    async def test_activity_since_returns_chronological(self, client: AsyncClient) -> None:
        resp = await client.get("/api/activity", params={"since": "2020-01-01T00:00:00"})
        data = resp.json()
        if len(data) >= 2:
            # Chronological: earliest first
            assert data[0]["created_at"] <= data[-1]["created_at"]

    async def test_activity_no_since_returns_newest_first(self, client: AsyncClient) -> None:
        resp = await client.get("/api/activity")
        data = resp.json()
        if len(data) >= 2:
            # Newest-first (no since param)
            assert data[0]["created_at"] >= data[-1]["created_at"]


class TestPlanAPI:
    """GET /api/plan/{milestone_id} — milestone plan tree."""

    async def test_plan_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/plan/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()

    async def test_plan_returns_tree(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        # Create a mini milestone -> phase -> step hierarchy
        milestone = dashboard_db.create_issue("Test Milestone", type="milestone")
        phase = dashboard_db.create_issue("Phase 1", type="phase", parent_id=milestone.id)
        dashboard_db.create_issue("Step 1", type="step", parent_id=phase.id)

        resp = await client.get(f"/api/plan/{milestone.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "milestone" in data
        assert "phases" in data
        assert "total_steps" in data
        assert "completed_steps" in data
        assert data["milestone"]["id"] == milestone.id
        assert len(data["phases"]) == 1
        assert data["total_steps"] == 1
        assert data["completed_steps"] == 0


class TestBatchAPI:
    """POST /api/batch/update and /api/batch/close — batch operations."""

    async def test_batch_update_priority(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/batch/update",
            json={"issue_ids": [ids["a"], ids["b"]], "priority": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "updated" in data
        assert "errors" in data
        assert len(data["updated"]) == 2
        assert all(i["priority"] == 0 for i in data["updated"])

    async def test_batch_update_with_errors(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/batch/update",
            json={"issue_ids": [ids["a"], "nonexistent"], "priority": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["updated"]) == 1
        assert len(data["errors"]) == 1
        assert data["errors"][0]["id"] == "nonexistent"

    async def test_batch_update_with_actor(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/batch/update",
            json={"issue_ids": [ids["b"]], "priority": 3, "actor": "batch-bot"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["updated"]) == 1

    async def test_batch_close(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/batch/close",
            json={"issue_ids": [ids["b"]], "reason": "batch done"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "closed" in data
        assert "errors" in data
        assert len(data["closed"]) == 1
        assert len(data["errors"]) == 0
        assert data["closed"][0]["id"] == ids["b"]

    async def test_batch_close_already_closed(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        # C is already closed — should report per-item error, not 409
        resp = await client.post(
            "/api/batch/close",
            json={"issue_ids": [ids["c"]]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["closed"]) == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["id"] == ids["c"]

    async def test_batch_close_with_actor(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/batch/close",
            json={"issue_ids": [ids["b"]], "actor": "closer-bot"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["closed"]) == 1


class TestTypesListAPI:
    """GET /api/types — list all registered issue types."""

    async def test_types_list(self, client: AsyncClient) -> None:
        resp = await client.get("/api/types")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        # Check structure of each type entry
        for t in data:
            assert "type" in t
            assert "display_name" in t
            assert "pack" in t
            assert "initial_state" in t

    async def test_types_includes_task(self, client: AsyncClient) -> None:
        resp = await client.get("/api/types")
        data = resp.json()
        type_names = [t["type"] for t in data]
        assert "task" in type_names

    async def test_types_includes_bug(self, client: AsyncClient) -> None:
        resp = await client.get("/api/types")
        data = resp.json()
        type_names = [t["type"] for t in data]
        assert "bug" in type_names


class TestCreateIssueAPI:
    """POST /api/issues — create a new issue."""

    async def test_create_basic_issue(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issues",
            json={"title": "New test issue"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "New test issue"
        assert "id" in data
        assert data["type"] == "task"
        assert data["priority"] == 2

    async def test_create_with_all_fields(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issues",
            json={
                "title": "Full issue",
                "type": "bug",
                "priority": 0,
                "description": "A bug report",
                "notes": "Some notes",
                "assignee": "alice",
                "labels": ["critical", "ui"],
                "actor": "api-user",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Full issue"
        assert data["type"] == "bug"
        assert data["priority"] == 0
        assert data["description"] == "A bug report"
        assert data["assignee"] == "alice"

    async def test_create_empty_title_returns_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issues",
            json={"title": ""},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_create_invalid_type_returns_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issues",
            json={"title": "Bad type", "type": "nonexistent_type"},
        )
        assert resp.status_code == 400
        err = resp.json()["error"]
        # Error from core.py includes valid types
        assert "nonexistent_type" in err["message"]
        assert "task" in err["message"]

    async def test_create_with_parent(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/issues",
            json={"title": "Child issue", "parent_id": ids["epic"]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["parent_id"] == ids["epic"]

    async def test_create_with_deps(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/issues",
            json={"title": "Dep issue", "deps": [ids["b"]]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert ids["b"] in data["blocked_by"]


class TestClaimAPI:
    async def test_claim_issue(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['a']}/claim",
            json={"assignee": "agent-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["assignee"] == "agent-1"

    async def test_release_claim(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        dashboard_db.claim_issue(ids["a"], assignee="agent-1")
        resp = await client.post(
            f"/api/issue/{ids['a']}/release",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["assignee"] == ""

    async def test_claim_next(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        resp = await client.post(
            "/api/claim-next",
            json={"assignee": "agent-2"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["assignee"] == "agent-2"

    async def test_claim_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issue/nonexistent/claim",
            json={"assignee": "x"},
        )
        assert resp.status_code == 404


class TestClaimEmptyAssigneeAPI:
    """Bug filigree-040ddb: dashboard claim endpoints must reject empty assignee."""

    async def test_claim_empty_assignee_returns_400(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(f"/api/issue/{ids['a']}/claim", json={"assignee": ""})
        assert resp.status_code == 400
        assert "assignee" in resp.json()["error"]["message"].lower()

    async def test_claim_missing_assignee_returns_400(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(f"/api/issue/{ids['a']}/claim", json={})
        assert resp.status_code == 400
        assert "assignee" in resp.json()["error"]["message"].lower()

    async def test_claim_next_empty_assignee_returns_400(self, client: AsyncClient) -> None:
        resp = await client.post("/api/claim-next", json={"assignee": ""})
        assert resp.status_code == 400
        assert "assignee" in resp.json()["error"]["message"].lower()

    async def test_claim_next_missing_assignee_returns_400(self, client: AsyncClient) -> None:
        resp = await client.post("/api/claim-next", json={})
        assert resp.status_code == 400
        assert "assignee" in resp.json()["error"]["message"].lower()


class TestDependencyManagementAPI:
    async def test_add_dependency(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['b']}/dependencies",
            json={"depends_on": ids["c"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] is True

    async def test_remove_dependency(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        dashboard_db.add_dependency(ids["a"], ids["b"])
        resp = await client.request(
            "DELETE",
            f"/api/issue/{ids['a']}/dependencies/{ids['b']}",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] is True

    async def test_add_dep_cycle_detection(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        dashboard_db.add_dependency(ids["a"], ids["b"])
        resp = await client.post(
            f"/api/issue/{ids['b']}/dependencies",
            json={"depends_on": ids["a"]},
        )
        assert resp.status_code == 409

    async def test_add_dep_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issue/nonexistent/dependencies",
            json={"depends_on": "also-nonexistent"},
        )
        assert resp.status_code == 404


class TestNonObjectBodyReturns400:
    """Non-dict JSON bodies (e.g. []) must return 400, not crash with 500."""

    async def test_update_issue_rejects_array_body(self, client: AsyncClient) -> None:
        resp = await client.patch("/api/issue/test-1", content="[]")
        assert resp.status_code == 400

    async def test_create_issue_rejects_array_body(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", content="[]")
        assert resp.status_code == 400

    async def test_close_issue_rejects_array_body(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issue/test-1/close", content="[]")
        assert resp.status_code == 400

    async def test_batch_update_rejects_array_body(self, client: AsyncClient) -> None:
        resp = await client.post("/api/batch/update", content="[]")
        assert resp.status_code == 400


class TestBatchAPIInputValidation:
    """Bug filigree-366a6d: null issue_ids crashes with 500 TypeError."""

    async def test_batch_update_null_issue_ids_returns_400(self, client: AsyncClient) -> None:
        """Sending issue_ids: null should return 400, not crash with 500."""
        resp = await client.post("/api/batch/update", json={"issue_ids": None, "priority": 1})
        assert resp.status_code == 400
        assert "issue_ids" in resp.json()["error"]["message"].lower()

    async def test_batch_close_null_issue_ids_returns_400(self, client: AsyncClient) -> None:
        """Sending issue_ids: null should return 400, not crash with 500."""
        resp = await client.post("/api/batch/close", json={"issue_ids": None})
        assert resp.status_code == 400
        assert "issue_ids" in resp.json()["error"]["message"].lower()

    async def test_batch_update_string_issue_ids_returns_400(self, client: AsyncClient) -> None:
        """Sending issue_ids as a string should return 400."""
        resp = await client.post("/api/batch/update", json={"issue_ids": "not-a-list", "priority": 1})
        assert resp.status_code == 400

    async def test_batch_close_string_issue_ids_returns_400(self, client: AsyncClient) -> None:
        """Sending issue_ids as a string should return 400."""
        resp = await client.post("/api/batch/close", json={"issue_ids": "not-a-list"})
        assert resp.status_code == 400

    async def test_batch_update_missing_issue_ids_returns_400(self, client: AsyncClient) -> None:
        """Omitting issue_ids entirely should return 400."""
        resp = await client.post("/api/batch/update", json={"priority": 1})
        assert resp.status_code == 400

    async def test_batch_close_missing_issue_ids_returns_400(self, client: AsyncClient) -> None:
        """Omitting issue_ids entirely should return 400."""
        resp = await client.post("/api/batch/close", json={"reason": "done"})
        assert resp.status_code == 400

    async def test_batch_update_non_string_ids_returns_400(self, client: AsyncClient) -> None:
        """Sending issue_ids with non-string elements (e.g. integers) should return 400."""
        resp = await client.post("/api/batch/update", json={"issue_ids": [123], "priority": 1})
        assert resp.status_code == 400
        assert "string" in resp.json()["error"]["message"].lower()

    async def test_batch_close_non_string_ids_returns_400(self, client: AsyncClient) -> None:
        """Sending issue_ids with non-string elements should return 400."""
        resp = await client.post("/api/batch/close", json={"issue_ids": [123]})
        assert resp.status_code == 400
        assert "string" in resp.json()["error"]["message"].lower()


class TestBatchClosePartialMutation:
    """Bug filigree-2cecbb: batch/close partially mutates then returns error."""

    async def test_batch_close_collects_per_item_errors(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        """batch/close with mix of valid and invalid IDs should return 200
        with succeeded and failed lists, not a single error response."""
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        # ids["a"] is open (closeable), "nonexistent" will fail
        resp = await client.post(
            "/api/batch/close",
            json={"issue_ids": [ids["a"], "nonexistent"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "closed" in data
        assert "errors" in data
        assert len(data["closed"]) == 1
        assert data["closed"][0]["id"] == ids["a"]
        assert len(data["errors"]) == 1
        assert data["errors"][0]["id"] == "nonexistent"

    async def test_batch_close_all_fail_returns_200_with_errors(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        """Even if all items fail, batch/close should return 200 with errors list."""
        resp = await client.post(
            "/api/batch/close",
            json={"issue_ids": ["bad-1", "bad-2"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["closed"]) == 0
        assert len(data["errors"]) == 2

    async def test_core_batch_close_returns_tuple(self, dashboard_db: FiligreeDB) -> None:
        """core.batch_close should return (results, errors) like batch_update."""
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        result = dashboard_db.batch_close([ids["a"], "nonexistent"], reason="test")
        # Should be a tuple of (closed_list, errors_list)
        assert isinstance(result, tuple)
        assert len(result) == 2
        closed, errors = result
        assert len(closed) == 1
        assert len(errors) == 1


class TestDashboardConcurrency:
    """Bug filigree-4b8e41: sync handlers run in thread pool, creating races on shared DB."""

    def test_all_route_handlers_are_async(self) -> None:
        """All handlers must be async to avoid thread pool dispatch and shared-DB races.

        FastAPI runs sync handlers in a thread pool (anyio.to_thread). With a single
        shared SQLite connection, this causes concurrent multi-thread access.
        Making all handlers async keeps them on the event loop thread — naturally serialized.
        """
        import asyncio

        app = create_app()
        sync_handlers: list[str] = []
        for route in app.routes:
            if hasattr(route, "endpoint") and not asyncio.iscoroutinefunction(route.endpoint):
                sync_handlers.append(f"{route.path} ({route.endpoint.__name__})")
        assert sync_handlers == [], f"Sync handlers run in thread pool, racing on shared DB: {sync_handlers}"

    async def test_concurrent_requests_no_errors(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        """Concurrent reads + writes must all succeed without SQLite threading errors."""
        import asyncio

        ids = dashboard_db._test_ids  # type: ignore[attr-defined]

        async def read_issues() -> int:
            resp = await client.get("/api/issues")
            return resp.status_code

        async def read_stats() -> int:
            resp = await client.get("/api/stats")
            return resp.status_code

        async def update_priority(p: int) -> int:
            resp = await client.patch(
                f"/api/issue/{ids['b']}",
                json={"priority": p % 5},
            )
            return resp.status_code

        # Mix 10 reads and 5 writes concurrently
        tasks: list[asyncio.Task[int]] = []
        for i in range(5):
            tasks.append(asyncio.ensure_future(read_issues()))
            tasks.append(asyncio.ensure_future(read_stats()))
            tasks.append(asyncio.ensure_future(update_priority(i)))

        results = await asyncio.gather(*tasks)
        assert all(r == 200 for r in results), f"Got status codes: {results}"


class TestEtherealDashboard:
    async def test_no_register_endpoint(self, client: AsyncClient) -> None:
        """Ethereal mode should not have /api/register."""
        resp = await client.post("/api/register", json={"path": "/foo"})
        assert resp.status_code == 404 or resp.status_code == 405

    async def test_no_projects_endpoint(self, client: AsyncClient) -> None:
        """Ethereal mode should not have /api/projects."""
        resp = await client.get("/api/projects")
        assert resp.status_code == 404

    async def test_no_reload_endpoint(self, client: AsyncClient) -> None:
        """Ethereal mode should not have /api/reload."""
        resp = await client.post("/api/reload")
        assert resp.status_code == 404 or resp.status_code == 405

    async def test_issues_at_root_api(self, client: AsyncClient) -> None:
        """Issues served at /api/issues (no project key prefix)."""
        resp = await client.get("/api/issues")
        assert resp.status_code == 200


class TestHealthAPI:
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestFilesSchemaAPI:
    """GET /api/files/_schema — API discovery for file/scan features."""

    async def test_schema_returns_valid_severities(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/_schema")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["valid_severities"]) == {"critical", "high", "medium", "low", "info"}

    async def test_schema_returns_valid_finding_statuses(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/_schema")
        data = resp.json()
        assert "unseen_in_latest" in data["valid_finding_statuses"]

    async def test_schema_returns_valid_association_types(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/_schema")
        data = resp.json()
        assert "bug_in" in data["valid_association_types"]
        assert "scan_finding" in data["valid_association_types"]

    async def test_schema_returns_valid_sort_fields(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/_schema")
        data = resp.json()
        assert set(data["valid_file_sort_fields"]) == {"updated_at", "first_seen", "path", "language"}
        assert set(data["valid_finding_sort_fields"]) == {"updated_at", "severity"}

    async def test_schema_returns_endpoints_catalog(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/_schema")
        data = resp.json()
        assert isinstance(data["endpoints"], list)
        assert len(data["endpoints"]) >= 1
        ep = data["endpoints"][0]
        assert "method" in ep
        assert "path" in ep
        assert "description" in ep

    async def test_schema_has_cache_control(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/_schema")
        assert resp.headers.get("cache-control") == "max-age=3600"


class TestScanRunsAPI:
    """GET /api/scan-runs — scan run history."""

    async def test_empty_table(self, client: AsyncClient) -> None:
        resp = await client.get("/api/scan-runs")
        assert resp.status_code == 200
        assert resp.json() == {"scan_runs": []}

    async def test_single_scan_run(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        dashboard_db.process_scan_results(
            scan_source="codex",
            scan_run_id="run-001",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        resp = await client.get("/api/scan-runs")
        assert resp.status_code == 200
        runs = resp.json()["scan_runs"]
        assert len(runs) == 1
        assert runs[0]["scan_run_id"] == "run-001"
        assert runs[0]["scan_source"] == "codex"
        assert runs[0]["total_findings"] == 1
        assert runs[0]["files_scanned"] == 1

    async def test_multiple_runs_ordered_by_recent(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        dashboard_db.process_scan_results(
            scan_source="codex",
            scan_run_id="run-old",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        dashboard_db.process_scan_results(
            scan_source="claude",
            scan_run_id="run-new",
            findings=[{"path": "b.py", "rule_id": "R2", "severity": "high", "message": "m"}],
        )
        resp = await client.get("/api/scan-runs")
        runs = resp.json()["scan_runs"]
        assert len(runs) == 2
        # Most recent first
        assert runs[0]["scan_run_id"] == "run-new"
        assert runs[1]["scan_run_id"] == "run-old"

    async def test_limit_param(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        for i in range(5):
            dashboard_db.process_scan_results(
                scan_source="ruff",
                scan_run_id=f"run-{i:03d}",
                findings=[{"path": f"f{i}.py", "rule_id": "R1", "severity": "low", "message": "m"}],
            )
        resp = await client.get("/api/scan-runs?limit=2")
        runs = resp.json()["scan_runs"]
        assert len(runs) == 2

    async def test_empty_run_id_excluded(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        dashboard_db.process_scan_results(
            scan_source="ruff",
            scan_run_id="",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        resp = await client.get("/api/scan-runs")
        assert resp.json() == {"scan_runs": []}

    async def test_no_cache_header(self, client: AsyncClient) -> None:
        resp = await client.get("/api/scan-runs")
        assert resp.headers.get("cache-control") == "no-cache"

    async def test_schema_includes_scan_runs_endpoint(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/_schema")
        data = resp.json()
        paths = [ep["path"] for ep in data["endpoints"]]
        assert "/api/scan-runs" in paths


class TestFilesScanSourceFilterAPI:
    """GET /api/files?scan_source=... — filter files by scan source."""

    async def test_scan_source_filters_files(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        dashboard_db.process_scan_results(
            scan_source="codex",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        dashboard_db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "b.py", "rule_id": "R2", "severity": "low", "message": "m"}],
        )
        resp = await client.get("/api/files?scan_source=codex")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["results"][0]["path"] == "a.py"

    async def test_no_scan_source_returns_all(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        dashboard_db.process_scan_results(
            scan_source="codex",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        dashboard_db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "b.py", "rule_id": "R2", "severity": "low", "message": "m"}],
        )
        resp = await client.get("/api/files")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2


class TestErrorMessagesIncludeValidOptions:
    """Error messages must include valid values to be self-documenting."""

    async def test_unknown_type_lists_valid_types(self, client: AsyncClient) -> None:
        resp = await client.get("/api/type/bogus_type")
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert err["code"] == "INVALID_TYPE"
        assert '"bogus_type"' in err["message"]
        # Must include at least some known types
        for expected in ("task", "bug", "feature"):
            assert expected in err["message"], f"Missing valid type '{expected}' in error"

    async def test_create_issue_unknown_type_lists_valid_types(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Bad", "type": "widgets"})
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert "widgets" in err["message"]
        assert "task" in err["message"]

    async def test_priority_error_includes_valid_range(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(f"/api/issue/{ids['a']}", json={"priority": "high"})
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "INVALID_PRIORITY"
        assert "0" in err["message"]
        assert "4" in err["message"]

    async def test_issue_not_found_includes_id(self, client: AsyncClient) -> None:
        resp = await client.get("/api/issue/nonexistent-id-xyz")
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert err["code"] == "ISSUE_NOT_FOUND"
        assert "nonexistent-id-xyz" in err["message"]


class TestEtherealTracerBullet:
    """End-to-end validation that init+dashboard+API works together."""

    async def test_single_project_lifecycle(self, dashboard_db: FiligreeDB) -> None:
        """Create an issue via DB, verify it appears in the API."""
        import filigree.dashboard as dash_module

        # Create an issue directly in the DB
        dashboard_db.create_issue(title="Tracer bullet test")

        # Wire up dashboard
        dash_module._db = dashboard_db
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Verify issues endpoint returns our issue
            resp = await client.get("/api/issues")
            assert resp.status_code == 200
            issues = resp.json()
            assert any(i["title"] == "Tracer bullet test" for i in issues)

            # Verify removed endpoints are gone
            assert (await client.get("/api/projects")).status_code == 404
            assert (await client.post("/api/register", json={})).status_code in (404, 405)
        dash_module._db = None
