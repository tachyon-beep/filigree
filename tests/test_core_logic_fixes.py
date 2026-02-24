"""Tests for core logic bug fixes (Phase 2).

Covers:
- claim_issue race condition (filigree-be24de)
- create_plan rollback on failure (filigree-4135c6)
- create_issue dep validation (filigree-1acc4b)
- closed dep filtering in get_issue (keel-326c2f)
- undo_last closed_at consistency (keel-3e899d)
- get_stats empty done_states guard (filigree-2e5af8)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from filigree.core import FiligreeDB


class TestClaimNextExhaustion:
    """Bug fix: filigree-2e5383 — claim_next logs when all candidates fail."""

    def test_claim_next_no_warning_when_no_candidates(self, db: FiligreeDB) -> None:
        """When no ready issues exist, claim_next returns None without warning."""
        issue = db.create_issue("Target")
        db.claim_issue(issue.id, assignee="agent1")

        result = db.claim_next("agent2")
        assert result is None

    def test_claim_next_logs_on_race_exhaustion(self, db: FiligreeDB) -> None:
        """When claim_issue raises ValueError for all candidates, warn about exhaustion."""
        db.create_issue("Target")

        # Simulate claim_issue always raising ValueError (race condition)
        with (
            patch.object(db, "claim_issue", side_effect=ValueError("race")),
            patch("filigree.db_issues.logger") as mock_logger,
        ):
            result = db.claim_next("agent2")

        assert result is None
        mock_logger.warning.assert_called_once()
        assert "failed to claim" in str(mock_logger.warning.call_args)


class TestClaimRaceCondition:
    """Bug fix: filigree-be24de — claim_issue race condition."""

    def test_claim_then_second_agent_raises(self, db: FiligreeDB) -> None:
        """Claiming an issue already assigned to another agent raises ValueError."""
        issue = db.create_issue("Race target")
        db.claim_issue(issue.id, assignee="agent1")
        with pytest.raises(ValueError, match="already assigned to 'agent1'"):
            db.claim_issue(issue.id, assignee="agent2")

    def test_claim_self_reclaim_succeeds(self, db: FiligreeDB) -> None:
        """Re-claiming an issue you already own should succeed (idempotent)."""
        issue = db.create_issue("Self claim")
        db.claim_issue(issue.id, assignee="agent1")
        # Second claim by same agent should succeed
        result = db.claim_issue(issue.id, assignee="agent1")
        assert result.assignee == "agent1"

    def test_claim_nonexistent_raises_keyerror(self, db: FiligreeDB) -> None:
        """Claiming a nonexistent issue raises KeyError."""
        with pytest.raises(KeyError):
            db.claim_issue("nonexistent-xyz", assignee="agent1")

    def test_claim_non_open_raises(self, db: FiligreeDB) -> None:
        """Claiming an issue not in an open-category state raises ValueError."""
        issue = db.create_issue("Close first")
        db.close_issue(issue.id)
        with pytest.raises(ValueError, match="expected open-category state"):
            db.claim_issue(issue.id, assignee="agent1")


class TestCreatePlanRollback:
    """Bug fix: filigree-4135c6 — create_plan no rollback."""

    def test_bad_dep_reference_rolls_back(self, db: FiligreeDB) -> None:
        """create_plan with a bad dep index should not leave orphan milestone/phases."""
        issues_before = len(db.list_issues())

        with pytest.raises((IndexError, ValueError)):
            db.create_plan(
                milestone={"title": "Orphan Test Milestone"},
                phases=[
                    {
                        "title": "Phase 1",
                        "steps": [
                            {"title": "Step A"},
                            {
                                "title": "Step B",
                                "deps": [99],  # Invalid: no step at index 99
                            },
                        ],
                    }
                ],
            )

        # No orphan issues should remain after rollback
        issues_after = len(db.list_issues())
        assert issues_after == issues_before, f"Expected {issues_before} issues after rollback, got {issues_after}"

    def test_successful_plan_commits(self, db: FiligreeDB) -> None:
        """A valid plan should commit successfully."""
        plan = db.create_plan(
            milestone={"title": "Good Milestone"},
            phases=[
                {
                    "title": "Phase 1",
                    "steps": [
                        {"title": "Step A"},
                        {"title": "Step B", "deps": [0]},
                    ],
                }
            ],
        )
        assert plan["milestone"]["title"] == "Good Milestone"
        assert len(plan["phases"]) == 1
        assert plan["phases"][0]["total"] == 2


class TestCreateIssuePartialWriteRollback:
    """Bug fix: filigree-340ce9 — create_issue leaves partial writes on dep failure."""

    def test_invalid_deps_no_orphan_issue(self, db: FiligreeDB) -> None:
        """create_issue with invalid deps must not leave an orphaned issue row."""
        issues_before = len(db.list_issues())

        with pytest.raises(ValueError, match="Invalid dependency IDs"):
            db.create_issue("Orphan candidate", deps=["nonexistent-dep-id"])

        # Force a commit to simulate MCP's long-lived connection
        db.conn.commit()

        issues_after = len(db.list_issues())
        assert issues_after == issues_before, (
            f"Expected {issues_before} issues, got {issues_after} — orphaned issue was committed after failed create_issue"
        )

    def test_invalid_deps_no_orphan_events(self, db: FiligreeDB) -> None:
        """create_issue with invalid deps must not leave orphaned events."""
        events_before = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        with pytest.raises(ValueError, match="Invalid dependency IDs"):
            db.create_issue("Event orphan candidate", deps=["ghost-id"])

        # Force commit
        db.conn.commit()

        events_after = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert events_after == events_before, (
            f"Expected {events_before} events, got {events_after} — orphaned 'created' event was committed after failed create_issue"
        )

    def test_invalid_deps_no_orphan_labels(self, db: FiligreeDB) -> None:
        """create_issue with labels + invalid deps must not leave orphaned labels."""
        labels_before = db.conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]

        with pytest.raises(ValueError, match="Invalid dependency IDs"):
            db.create_issue("Label orphan", labels=["defect", "urgent"], deps=["missing-id"])

        db.conn.commit()

        labels_after = db.conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
        assert labels_after == labels_before, (
            f"Expected {labels_before} labels, got {labels_after} — orphaned labels were committed after failed create_issue"
        )


class TestUpdateIssuePartialEventRollback:
    """Bug fix: filigree-1c0a33 — update_issue persists false events on validation failure."""

    def test_invalid_priority_no_orphan_title_event(self, db: FiligreeDB) -> None:
        """update_issue with valid title + invalid priority must not leave title_changed event."""
        issue = db.create_issue("Original title")
        events_before = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        with pytest.raises(ValueError, match="Priority must be between 0 and 4"):
            db.update_issue(issue.id, title="New title", priority=99)

        # Force commit to simulate MCP long-lived connection
        db.conn.commit()

        events_after = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert events_after == events_before, (
            f"Expected {events_before} events, got {events_after} — orphaned title_changed event was committed after failed update_issue"
        )

        # Title should remain unchanged
        refreshed = db.get_issue(issue.id)
        assert refreshed.title == "Original title"

    def test_circular_parent_no_orphan_events(self, db: FiligreeDB) -> None:
        """update_issue with valid title + circular parent must not leave orphaned events."""
        parent = db.create_issue("Parent")
        child = db.create_issue("Child", parent_id=parent.id)
        events_before = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        with pytest.raises(ValueError, match="circular parent chain"):
            db.update_issue(parent.id, title="Renamed parent", parent_id=child.id)

        db.conn.commit()

        events_after = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert events_after == events_before, (
            f"Expected {events_before} events, got {events_after} — orphaned events committed after failed update_issue"
        )


class TestInvalidDepValidation:
    """Bug fix: filigree-1acc4b — create_issue dep FK crash."""

    def test_nonexistent_dep_raises_valueerror(self, db: FiligreeDB) -> None:
        """Creating an issue with deps referencing nonexistent IDs raises ValueError."""
        with pytest.raises(ValueError, match="Invalid dependency IDs"):
            db.create_issue("Bad deps", deps=["nonexistent-id"])

    def test_nonexistent_dep_not_integrity_error(self, db: FiligreeDB) -> None:
        """The error should be ValueError, not sqlite3.IntegrityError."""
        import sqlite3

        with pytest.raises(ValueError, match="Invalid dependency IDs"):
            db.create_issue("Bad deps 2", deps=["ghost-abc123"])
        # Explicitly ensure it's not an IntegrityError
        try:
            db.create_issue("Bad deps 3", deps=["ghost-xyz789"])
        except ValueError:
            pass  # Expected
        except sqlite3.IntegrityError:
            pytest.fail("Should raise ValueError, not IntegrityError")

    def test_valid_dep_succeeds(self, db: FiligreeDB) -> None:
        """Creating an issue with valid dep IDs should work."""
        dep_issue = db.create_issue("Dep target")
        issue = db.create_issue("Has deps", deps=[dep_issue.id])
        assert dep_issue.id in issue.blocked_by


class TestClosedDepFiltering:
    """Bug fix: keel-326c2f — Dep persists after close."""

    def test_closed_blocker_not_in_blocked_by(self, db: FiligreeDB) -> None:
        """After closing B, get_issue(A) where A depends-on B should not show B in blocked_by."""
        a = db.create_issue("Issue A")
        b = db.create_issue("Issue B")
        db.add_dependency(a.id, b.id)

        # Before closing: B should be in A's blocked_by
        a_before = db.get_issue(a.id)
        assert b.id in a_before.blocked_by

        # Close B
        db.close_issue(b.id)

        # After closing: B should NOT be in A's blocked_by
        a_after = db.get_issue(a.id)
        assert b.id not in a_after.blocked_by

    def test_closed_blocker_still_in_blocks(self, db: FiligreeDB) -> None:
        """The blocks list on B should still show A (for audit trail)."""
        a = db.create_issue("Issue A")
        b = db.create_issue("Issue B")
        db.add_dependency(a.id, b.id)
        db.close_issue(b.id)

        b_after = db.get_issue(b.id)
        assert a.id in b_after.blocks

    def test_a_becomes_ready_after_blocker_closed(self, db: FiligreeDB) -> None:
        """After closing the only blocker, the blocked issue should become ready."""
        a = db.create_issue("Issue A")
        b = db.create_issue("Issue B")
        db.add_dependency(a.id, b.id)

        a_blocked = db.get_issue(a.id)
        assert not a_blocked.is_ready

        db.close_issue(b.id)
        a_ready = db.get_issue(a.id)
        assert a_ready.is_ready


class TestUndoCloseConsistency:
    """Bug fix: keel-3e899d — undo_last closed_at consistency."""

    def test_undo_close_clears_closed_at(self, db: FiligreeDB) -> None:
        """Closing an issue then undoing should clear closed_at."""
        issue = db.create_issue("Undo close test")
        db.close_issue(issue.id)

        # Verify closed_at is set
        closed = db.get_issue(issue.id)
        assert closed.closed_at is not None

        # Undo the close
        result = db.undo_last(issue.id)
        assert result["undone"] is True

        # closed_at should be cleared
        undone = db.get_issue(issue.id)
        assert undone.closed_at is None
        assert undone.status_category != "done"

    def test_undo_to_done_sets_closed_at(self, db: FiligreeDB) -> None:
        """Undoing from a non-done state back to a done state should set closed_at."""
        issue = db.create_issue("Undo to done test")

        # Close the issue (status -> closed, closed_at set)
        db.close_issue(issue.id)
        closed = db.get_issue(issue.id)
        assert closed.closed_at is not None

        # Reopen the issue (status -> open, closed_at cleared)
        db.reopen_issue(issue.id)
        reopened = db.get_issue(issue.id)
        assert reopened.closed_at is None

        # Now undo the reopen — this should restore the closed status
        # The most recent reversible event should be the status_changed from reopen
        result = db.undo_last(issue.id)
        assert result["undone"] is True

        # closed_at should be set because we're back in a done state
        restored = db.get_issue(issue.id)
        assert restored.status_category == "done"
        assert restored.closed_at is not None

    def test_undo_close_restores_correct_status(self, db: FiligreeDB) -> None:
        """Test that undoing a close restores the previous status and clears closed_at."""
        issue = db.create_issue("Undo close status test")

        # Move to in_progress first
        db.update_issue(issue.id, status="in_progress")

        # Close
        db.close_issue(issue.id)
        closed = db.get_issue(issue.id)
        assert closed.closed_at is not None
        assert closed.status_category == "done"

        # Undo close -> should restore to in_progress
        result = db.undo_last(issue.id)
        assert result["undone"] is True
        after_undo = db.get_issue(issue.id)
        assert after_undo.status == "in_progress"
        assert after_undo.closed_at is None
        assert after_undo.status_category != "done"


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
