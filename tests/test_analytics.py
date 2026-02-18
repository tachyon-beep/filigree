"""Tests for filigree.analytics — flow metrics."""

from __future__ import annotations

from unittest.mock import patch

from filigree.analytics import cycle_time, get_flow_metrics, lead_time
from filigree.core import FiligreeDB


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
        # Note: cycle_time() currently keys off literal "in_progress" → "closed" events,
        # so only types whose WIP state is "in_progress" produce cycle_time data.
        # Bug type uses "fixing" as WIP, so it won't appear in by_type (cycle_time=None).
        task = db.create_issue("Task", type="task")
        db.update_issue(task.id, status="in_progress")
        db.close_issue(task.id)
        epic = db.create_issue("Epic", type="epic")
        db.update_issue(epic.id, status="in_progress")
        db.close_issue(epic.id)
        data = get_flow_metrics(db)
        assert "task" in data["by_type"]
        assert "epic" in data["by_type"]

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
