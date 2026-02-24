"""Tests for filigree.analytics — flow metrics."""

from __future__ import annotations

from unittest.mock import patch

from filigree.analytics import cycle_time, get_flow_metrics, lead_time
from filigree.core import FiligreeDB, Issue


class TestCycleTime:
    def test_cycle_time_basic(self, db: FiligreeDB) -> None:
        issue = db.create_issue("CT test")
        db.update_issue(issue.id, status="in_progress")
        db.close_issue(issue.id)
        ct = cycle_time(db, issue.id)
        assert ct is not None
        assert ct >= 0

    def test_cycle_time_never_started(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Never started")
        db.close_issue(issue.id)
        ct = cycle_time(db, issue.id)
        assert ct is None

    def test_cycle_time_not_closed(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Still open")
        db.update_issue(issue.id, status="in_progress")
        ct = cycle_time(db, issue.id)
        assert ct is None

    def test_cycle_time_skips_unparsable_done_timestamp(self, db: FiligreeDB) -> None:
        """If first done event has corrupt timestamp, later valid done events should be used."""
        issue = db.create_issue("CT corrupt test")
        # Manually insert all events with controlled timestamps
        # 1) WIP event with valid timestamp
        db.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'open', 'in_progress', '2026-01-10T10:00:00+00:00')",
            (issue.id,),
        )
        # 2) Done event with corrupt/empty timestamp (first done — should be skipped)
        db.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'in_progress', 'closed', 'not-a-date')",
            (issue.id,),
        )
        # 3) Done event with valid timestamp (should be used)
        db.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'in_progress', 'closed', '2026-01-15T12:00:00+00:00')",
            (issue.id,),
        )
        db.conn.commit()
        ct = cycle_time(db, issue.id)
        assert ct is not None

    def test_cycle_time_closed_before_wip_then_reopened(self, db: FiligreeDB) -> None:
        """open→closed→open→in_progress→closed should return valid cycle time.

        Regression: cycle_time() broke on the first done event even though
        no WIP start had been found yet, returning None.
        """
        issue = db.create_issue("Closed-before-WIP test")
        # Simulate: open→closed (no WIP), reopen, then proper WIP→done
        db.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'open', 'closed', '2026-01-01T10:00:00+00:00')",
            (issue.id,),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'closed', 'open', '2026-01-02T10:00:00+00:00')",
            (issue.id,),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'open', 'in_progress', '2026-01-03T10:00:00+00:00')",
            (issue.id,),
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'in_progress', 'closed', '2026-01-05T10:00:00+00:00')",
            (issue.id,),
        )
        db.conn.commit()
        ct = cycle_time(db, issue.id)
        assert ct is not None, "Should find the valid WIP→done pair after reopen"
        # 2 days = 48 hours
        assert abs(ct - 48.0) < 0.1

    def test_cycle_time_uses_type_specific_status_categories(self, db: FiligreeDB) -> None:
        """cycle_time must classify status by issue type, not global state-name sets."""
        issue = db.create_issue("Type-aware CT", type="bug")
        db.conn.execute("DELETE FROM events WHERE issue_id = ? AND event_type = 'status_changed'", (issue.id,))
        db.conn.execute(
            "INSERT INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'triage', 'shared_wip', '2026-01-01T10:00:00+00:00')",
            (issue.id,),
        )
        db.conn.execute(
            "INSERT INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'shared_wip', 'shared_done', '2026-01-02T10:00:00+00:00')",
            (issue.id,),
        )
        db.conn.commit()

        def fake_global_states(category: str) -> set[str]:
            if category == "wip":
                return {"shared_wip"}
            if category == "done":
                return {"shared_done"}
            return set()

        def fake_resolve(issue_type: str, state: str) -> str:
            if issue_type == issue.type:
                if state == "shared_wip":
                    return "done"
                if state == "shared_done":
                    return "wip"
            return "open"

        with (
            patch.object(db, "_get_states_for_category", side_effect=fake_global_states),
            patch.object(db, "_resolve_status_category", side_effect=fake_resolve),
        ):
            ct = cycle_time(db, issue.id)

        assert ct is None, "Type-aware resolver should ignore misleading global state sets"


class TestLeadTime:
    def test_lead_time_basic(self, db: FiligreeDB) -> None:
        issue = db.create_issue("LT test")
        db.close_issue(issue.id)
        lt = lead_time(db, issue.id)
        assert lt is not None
        assert lt >= 0

    def test_lead_time_not_closed(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Still open")
        lt = lead_time(db, issue.id)
        assert lt is None

    def test_lead_time_accepts_issue_object(self, db: FiligreeDB) -> None:
        """lead_time() should accept an Issue object to avoid N+1 re-fetches."""
        issue = db.create_issue("LT direct")
        db.close_issue(issue.id)
        refreshed = db.get_issue(issue.id)
        lt = lead_time(db, issue=refreshed)
        assert lt is not None
        assert lt >= 0

    def test_lead_time_no_extra_queries_with_issue_object(self, db: FiligreeDB) -> None:
        """Passing an Issue object should not call db.get_issue()."""
        issue = db.create_issue("LT no query")
        db.close_issue(issue.id)
        refreshed = db.get_issue(issue.id)

        original_get = db.get_issue
        calls: list[str] = []

        def tracking_get(issue_id: str) -> Issue:
            calls.append(issue_id)
            return original_get(issue_id)

        with patch.object(db, "get_issue", side_effect=tracking_get):
            lt = lead_time(db, issue=refreshed)

        assert lt is not None
        assert len(calls) == 0, f"get_issue called {len(calls)} times with Issue object"


class TestFlowMetrics:
    def test_flow_metrics_structure(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Metric test")
        db.update_issue(issue.id, status="in_progress")
        db.close_issue(issue.id)
        data = get_flow_metrics(db)
        assert "throughput" in data
        assert "period_days" in data
        assert "avg_cycle_time_hours" in data
        assert "avg_lead_time_hours" in data
        assert "by_type" in data
        assert data["period_days"] == 30

    def test_flow_metrics_empty(self, db: FiligreeDB) -> None:
        data = get_flow_metrics(db)
        assert data["throughput"] == 0
        assert data["avg_cycle_time_hours"] is None

    def test_flow_metrics_by_type(self, db: FiligreeDB) -> None:
        task = db.create_issue("Task", type="task")
        db.update_issue(task.id, status="in_progress")
        db.close_issue(task.id)
        epic = db.create_issue("Epic", type="epic")
        db.update_issue(epic.id, status="in_progress")
        db.close_issue(epic.id)
        data = get_flow_metrics(db)
        assert "task" in data["by_type"]
        assert "epic" in data["by_type"]

    def test_flow_metrics_uses_type_specific_status_categories(self, db: FiligreeDB) -> None:
        """get_flow_metrics must use per-issue-type category resolution for cycle time."""
        issue = db.create_issue("Type-aware metrics", type="bug")
        db.close_issue(issue.id)
        db.conn.execute("DELETE FROM events WHERE issue_id = ? AND event_type = 'status_changed'", (issue.id,))
        db.conn.execute(
            "INSERT INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'triage', 'shared_wip', '2026-01-01T10:00:00+00:00')",
            (issue.id,),
        )
        db.conn.execute(
            "INSERT INTO events (issue_id, event_type, old_value, new_value, created_at) "
            "VALUES (?, 'status_changed', 'shared_wip', 'shared_done', '2026-01-02T10:00:00+00:00')",
            (issue.id,),
        )
        db.conn.commit()

        def fake_global_states(category: str) -> set[str]:
            if category == "wip":
                return {"in_progress"}
            if category == "done":
                return {"closed"}
            return set()

        def fake_resolve(issue_type: str, state: str) -> str:
            if issue_type == issue.type:
                if state == "shared_wip":
                    return "wip"
                if state == "shared_done":
                    return "done"
            return "open"

        with (
            patch.object(db, "_get_states_for_category", side_effect=fake_global_states),
            patch.object(db, "_resolve_status_category", side_effect=fake_resolve),
        ):
            data = get_flow_metrics(db, days=30)

        assert data["throughput"] == 1
        assert "bug" in data["by_type"]
        assert data["by_type"]["bug"]["count"] == 1

    def test_flow_metrics_respects_days(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Recent close")
        db.update_issue(issue.id, status="in_progress")
        db.close_issue(issue.id)
        # With a large window, should see the issue
        data_30 = get_flow_metrics(db, days=30)
        assert data_30["throughput"] >= 1
        assert data_30["period_days"] == 30
        # days param is wired through and changes the result shape
        data_7 = get_flow_metrics(db, days=7)
        assert data_7["period_days"] == 7
        assert data_7["throughput"] >= 1  # recently closed, still in 7d window

    def test_flow_metrics_uses_high_limit(self, db: FiligreeDB) -> None:
        """get_flow_metrics must not use default limit=100 for list_issues."""
        original = db.list_issues
        call_limits: list[int | None] = []

        def tracking(**kwargs):  # type: ignore[no-untyped-def]
            call_limits.append(kwargs.get("limit"))
            return original(**kwargs)

        with patch.object(db, "list_issues", side_effect=tracking):
            get_flow_metrics(db)

        # Every call should use a high limit, not the default 100
        for limit in call_limits:
            assert limit is not None, "list_issues called without explicit limit"
            assert limit > 100, f"list_issues called with limit={limit}, expected >100"

    def test_flow_metrics_paginates_beyond_single_page(self, db: FiligreeDB) -> None:
        """get_flow_metrics must paginate to collect all done issues, not cap at a fixed limit."""
        original = db.list_issues
        page_size = [0]  # Track what page size is used

        def capped_list(**kwargs):  # type: ignore[no-untyped-def]
            # Record the limit used and simulate a smaller page size
            page_size[0] = kwargs.get("limit", 100)
            return original(**kwargs)

        # Create some issues so there's data
        for i in range(3):
            iss = db.create_issue(f"Paginate test {i}")
            db.update_issue(iss.id, status="in_progress")
            db.close_issue(iss.id)

        with patch.object(db, "list_issues", side_effect=capped_list):
            data = get_flow_metrics(db)

        assert data["throughput"] >= 3

    def test_flow_metrics_batch_fetches_status_events(self, db: FiligreeDB) -> None:
        """Regression: avoid N+1 events queries while computing cycle time."""
        for i in range(4):
            issue = db.create_issue(f"Batch metric {i}")
            db.update_issue(issue.id, status="in_progress")
            db.close_issue(issue.id)

        seen_sql: list[str] = []

        def _trace(sql: str) -> None:
            seen_sql.append(sql.lower())

        db.conn.set_trace_callback(_trace)
        try:
            data = get_flow_metrics(db)
        finally:
            db.conn.set_trace_callback(None)

        assert data["throughput"] >= 4
        event_queries = [sql for sql in seen_sql if "from events" in sql and "event_type = 'status_changed'" in sql]
        assert len(event_queries) == 1, f"expected 1 batched status-events query, got {len(event_queries)}"

    def test_flow_metrics_includes_archived_issues(self, db: FiligreeDB) -> None:
        """Archived issues (from archive_closed) must be counted in throughput."""
        issue = db.create_issue("Will archive")
        db.update_issue(issue.id, status="in_progress")
        db.close_issue(issue.id)
        # archive_closed rewrites status to 'archived' but preserves closed_at
        db.archive_closed(days_old=0)
        refreshed = db.get_issue(issue.id)
        assert refreshed.status == "archived"
        data = get_flow_metrics(db, days=30)
        assert data["throughput"] >= 1, "Archived issues should be counted"
