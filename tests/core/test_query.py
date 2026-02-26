"""Tests for core query operations — list, search, filter, stats, events."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from filigree.core import FiligreeDB


class TestListAndSearch:
    def test_list_all(self, db: FiligreeDB) -> None:
        db.create_issue("A")
        db.create_issue("B")
        assert len(db.list_issues()) == 2

    def test_list_filter_status(self, db: FiligreeDB) -> None:
        a = db.create_issue("Open one")
        b = db.create_issue("Close one")
        db.close_issue(b.id)
        open_issues = db.list_issues(status="open")
        assert len(open_issues) == 1
        assert open_issues[0].id == a.id

    def test_search(self, db: FiligreeDB) -> None:
        db.create_issue("Fix authentication bug")
        db.create_issue("Add new feature")
        results = db.search_issues("auth")
        assert len(results) == 1
        assert "auth" in results[0].title.lower()


class TestSearchFTSFallback:
    """Bug filigree-35ef38: FTS fallback must only catch missing-table errors."""

    def test_non_fts_operational_error_propagates(self, db: FiligreeDB) -> None:
        """OperationalError unrelated to missing FTS5 must NOT be silently caught."""
        import sqlite3
        from unittest.mock import patch

        db.create_issue("Searchable item")

        original_execute = db.conn.execute

        class _SpyConn:
            """Wraps conn to intercept FTS queries."""

            def __getattr__(self, name):
                return getattr(db._conn, name)

            def execute(self, sql, params=()):
                if "issues_fts" in sql and "MATCH" in sql:
                    raise sqlite3.OperationalError("database disk image is malformed")
                return original_execute(sql, params)

        with patch.object(db, "_conn", _SpyConn()), pytest.raises(sqlite3.OperationalError, match="malformed"):
            db.search_issues("Searchable")


class TestStats:
    def test_stats_counts(self, db: FiligreeDB) -> None:
        db.create_issue("A")
        b = db.create_issue("B")
        db.close_issue(b.id)
        stats = db.get_stats()
        assert stats["by_status"]["open"] == 1
        assert stats["by_status"]["closed"] == 1

    def test_ready_count_matches_get_ready_for_template_open_states(self, db: FiligreeDB) -> None:
        bug = db.create_issue("Template-open bug", type="bug")
        ready_ids = {i.id for i in db.get_ready()}
        stats = db.get_stats()

        assert bug.id in ready_ids
        assert stats["ready_count"] == len(ready_ids)

    def test_done_category_blocker_does_not_count_as_blocked(self, db: FiligreeDB) -> None:
        blocker = db.create_issue("Blocker", type="bug")
        blocked = db.create_issue("Blocked", type="bug")
        db.add_dependency(blocked.id, blocker.id)
        db.close_issue(blocker.id, status="wont_fix")

        ready_ids = {i.id for i in db.get_ready()}
        stats = db.get_stats()

        assert blocked.id in ready_ids
        assert stats["blocked_count"] == 0
        assert stats["ready_count"] == len(ready_ids)


class TestGetStatsByCategory:
    """Verify get_stats() includes category-level counts (WFT-FR-060)."""

    def test_get_stats_by_category(self, db: FiligreeDB) -> None:
        """by_category sums issues across open/wip/done."""
        db.create_issue("A")  # open → open category
        b = db.create_issue("B")
        db.update_issue(b.id, status="in_progress")  # wip category
        c = db.create_issue("C")
        db.close_issue(c.id)  # done category

        stats = db.get_stats()
        by_cat = stats["by_category"]
        assert by_cat["open"] == 1
        assert by_cat["wip"] == 1
        assert by_cat["done"] == 1

    def test_get_stats_by_category_custom_states(self, db: FiligreeDB) -> None:
        """Bug in 'triage' counts as open; bug in 'fixing' counts as wip."""
        db.create_issue("Bug in triage", type="bug")  # initial_state=triage → open category
        bug2 = db.create_issue("Bug being fixed", type="bug")
        # Move through workflow: triage → confirmed → fixing
        db.update_issue(bug2.id, status="confirmed")
        db.update_issue(bug2.id, status="fixing")

        stats = db.get_stats()
        by_cat = stats["by_category"]
        assert by_cat["open"] >= 1  # triage bug
        assert by_cat["wip"] >= 1  # fixing bug

    def test_get_stats_backward_compat(self, db: FiligreeDB) -> None:
        """by_status is still present alongside by_category."""
        db.create_issue("X")
        stats = db.get_stats()
        assert "by_status" in stats
        assert "by_category" in stats
        assert "ready_count" in stats


class TestEvents:
    def test_events_recorded_on_create(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Event test")
        events = db.get_recent_events(limit=5)
        assert any(e["issue_id"] == issue.id and e["event_type"] == "created" for e in events)

    def test_events_recorded_on_status_change(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Event test")
        db.update_issue(issue.id, status="in_progress")
        events = db.get_recent_events(limit=5)
        assert any(e["issue_id"] == issue.id and e["event_type"] == "status_changed" for e in events)


class TestGetEventsSince:
    def test_basic(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Event source")
        # Get creation event timestamp
        events = db.get_recent_events(limit=1)
        ts = events[0]["created_at"]
        # Make a change after creation
        db.update_issue(issue.id, status="in_progress")
        since_events = db.get_events_since(ts)
        assert len(since_events) >= 1
        assert any(e["event_type"] == "status_changed" for e in since_events)

    def test_empty_when_no_events(self, db: FiligreeDB) -> None:
        result = db.get_events_since("2099-01-01T00:00:00+00:00")
        assert result == []

    def test_respects_limit(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.create_issue(f"Issue {i}")
        result = db.get_events_since("2000-01-01T00:00:00+00:00", limit=2)
        assert len(result) == 2

    def test_chronological_order(self, db: FiligreeDB) -> None:
        db.create_issue("First")
        db.create_issue("Second")
        result = db.get_events_since("2000-01-01T00:00:00+00:00")
        assert len(result) >= 2
        # Events should be in ascending order
        for i in range(len(result) - 1):
            assert result[i]["created_at"] <= result[i + 1]["created_at"]


class TestActorTracking:
    def test_actor_in_update_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Track me")
        db.update_issue(issue.id, status="in_progress", actor="agent-alpha")
        events = db.get_recent_events(limit=5)
        status_event = next(e for e in events if e["event_type"] == "status_changed")
        assert status_event["actor"] == "agent-alpha"

    def test_actor_in_close_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Close me")
        db.close_issue(issue.id, actor="agent-beta")
        events = db.get_recent_events(limit=5)
        close_event = next(e for e in events if e["event_type"] == "status_changed" and e["new_value"] == "closed")
        assert close_event["actor"] == "agent-beta"


class TestGetStatsEmptyDoneStates:
    """Bug fix: filigree-2e5af8 — get_stats empty done_states."""

    def test_get_stats_with_normal_templates(self, db: FiligreeDB) -> None:
        """get_stats() should work normally with templates loaded."""
        db.create_issue("Issue A")
        b = db.create_issue("Issue B")
        db.close_issue(b.id)

        stats = db.get_stats()
        assert "by_status" in stats
        assert "by_category" in stats
        assert "ready_count" in stats
        assert "blocked_count" in stats
        assert stats["by_category"]["done"] >= 1

    def test_get_stats_empty_done_states_no_crash(self, db: FiligreeDB) -> None:
        """get_stats() should not crash when done_states is empty."""
        db.create_issue("Issue A")

        # Simulate empty done_states by mocking _get_states_for_category
        original_method = db._get_states_for_category

        def mock_get_states(category: str) -> list[str]:
            if category == "done":
                return []
            return original_method(category)

        with patch.object(db, "_get_states_for_category", side_effect=mock_get_states):
            # This should not raise an error
            stats = db.get_stats()
            assert "ready_count" in stats
            assert "blocked_count" in stats
            assert isinstance(stats["ready_count"], int)
            assert isinstance(stats["blocked_count"], int)

    def test_get_stats_empty_done_states_with_deps(self, db: FiligreeDB) -> None:
        """get_stats() with empty done_states should count all deps as blockers."""
        a = db.create_issue("Issue A")
        b = db.create_issue("Issue B")
        db.add_dependency(a.id, b.id)

        original_method = db._get_states_for_category

        def mock_get_states(category: str) -> list[str]:
            if category == "done":
                return []
            return original_method(category)

        with patch.object(db, "_get_states_for_category", side_effect=mock_get_states):
            stats = db.get_stats()
            # With no done states, B blocks A, so A is blocked and B is ready
            assert stats["blocked_count"] >= 1
