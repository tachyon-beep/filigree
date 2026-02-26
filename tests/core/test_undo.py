# tests/test_undo.py
"""Tests for the undo_last mechanism."""

from __future__ import annotations

import pytest

from filigree.core import FiligreeDB


class TestUndoStatus:
    """Undo status_changed events."""

    def test_undo_status_change(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        db.update_issue(issue.id, status="in_progress", actor="t")
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "status_changed"
        assert result["issue"]["status"] == "open"

    def test_undo_close_clears_closed_at(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        db.close_issue(issue.id, actor="t")
        closed = db.get_issue(issue.id)
        assert closed.closed_at is not None

        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["issue"]["closed_at"] is None


class TestUndoTitle:
    def test_undo_title_change(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Original title")
        db.update_issue(issue.id, title="Changed title", actor="t")
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["issue"]["title"] == "Original title"


class TestUndoPriority:
    def test_undo_priority_change(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", priority=2)
        db.update_issue(issue.id, priority=0, actor="t")
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["issue"]["priority"] == 2


class TestUndoAssignee:
    def test_undo_assignee_change(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        db.update_issue(issue.id, assignee="alice", actor="t")
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["issue"]["assignee"] == ""


class TestUndoClaim:
    def test_undo_claim(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        db.claim_issue(issue.id, assignee="alice", actor="t")
        claimed = db.get_issue(issue.id)
        assert claimed.status == "open"  # claim does not change status
        assert claimed.assignee == "alice"

        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "claimed"
        assert result["issue"]["status"] == "open"  # status still unchanged
        assert result["issue"]["assignee"] == ""

    def test_undo_claim_clears_assignee_only(self, db: FiligreeDB) -> None:
        """Undo claim just clears assignee — status was never changed by claim."""
        bug = db.create_issue("Bug", type="bug")
        assert bug.status == "triage"
        # Move to confirmed (still open-category, but not initial)
        db.update_issue(bug.id, status="confirmed", actor="t")
        confirmed = db.get_issue(bug.id)
        assert confirmed.status == "confirmed"

        db.claim_issue(bug.id, assignee="alice", actor="t")
        claimed = db.get_issue(bug.id)
        assert claimed.status == "confirmed"  # claim does not change status

        result = db.undo_last(bug.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "claimed"
        # Status stays at "confirmed" — claim never changed it
        assert result["issue"]["status"] == "confirmed"
        assert result["issue"]["assignee"] == ""

    def test_undo_claim_restores_prior_assignee(self, db: FiligreeDB) -> None:
        """Bug filigree-a8e7cf: undo claim must restore prior assignee, not blank."""
        issue = db.create_issue("Test")
        # Alice claims first
        db.claim_issue(issue.id, assignee="alice", actor="t")
        assert db.get_issue(issue.id).assignee == "alice"

        # Release and let bob claim (alice's claim is released, bob claims fresh)
        db.release_claim(issue.id, actor="t")
        db.claim_issue(issue.id, assignee="bob", actor="t")
        assert db.get_issue(issue.id).assignee == "bob"

        # Undo bob's claim — should restore to "" (what was there before bob claimed)
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "claimed"
        assert result["issue"]["assignee"] == ""


class TestUndoDependency:
    def test_undo_dependency_added(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        db.add_dependency(a.id, b.id, actor="t")

        # a is blocked by b
        a_before = db.get_issue(a.id)
        assert b.id in a_before.blocked_by

        result = db.undo_last(a.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "dependency_added"

        a_after = db.get_issue(a.id)
        assert b.id not in a_after.blocked_by

    def test_undo_dependency_removed(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        db.add_dependency(a.id, b.id, actor="t")
        db.remove_dependency(a.id, b.id, actor="t")

        a_before = db.get_issue(a.id)
        assert b.id not in a_before.blocked_by

        result = db.undo_last(a.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "dependency_removed"

        a_after = db.get_issue(a.id)
        assert b.id in a_after.blocked_by


class TestUndoDescription:
    def test_undo_description_change(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", description="original")
        db.update_issue(issue.id, description="changed", actor="t")
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "description_changed"
        assert result["issue"]["description"] == "original"


class TestUndoNotes:
    def test_undo_notes_change(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", notes="original")
        db.update_issue(issue.id, notes="changed", actor="t")
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "notes_changed"
        assert result["issue"]["notes"] == "original"


class TestUndoEdgeCases:
    def test_undo_created_only_fails(self, db: FiligreeDB) -> None:
        """Issue with only a 'created' event has no reversible events."""
        issue = db.create_issue("Test")
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is False
        assert "No reversible events" in result["reason"]

    def test_undo_skips_transition_warning(self, db: FiligreeDB) -> None:
        """transition_warning events should be skipped when finding last event."""
        issue = db.create_issue("Test", type="bug")
        # Move through triage -> confirmed without severity (generates warning)
        db.update_issue(issue.id, status="confirmed", actor="t")

        # The most recent non-skip event should be status_changed, not transition_warning
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "status_changed"

    def test_double_undo_returns_already_undone(self, db: FiligreeDB) -> None:
        """Cannot undo the same reversible event twice."""
        issue = db.create_issue("Test")
        db.update_issue(issue.id, status="in_progress", actor="t")
        db.undo_last(issue.id, actor="t")
        # The reversible event (status_changed) already has a newer 'undone' covering it
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is False
        assert "already undone" in result["reason"]

    def test_undo_reaches_past_non_reversible_events(self, db: FiligreeDB) -> None:
        """Undo should skip non-reversible events and find earlier reversible ones."""
        issue = db.create_issue("Test")
        db.update_issue(issue.id, status="in_progress", actor="t")
        # Record a non-reversible event (simulate by inserting directly)
        db.conn.execute(
            "INSERT INTO events (issue_id, event_type, actor, created_at) VALUES (?, ?, ?, ?)",
            (issue.id, "released", "system", "2099-01-01T00:00:00+00:00"),
        )
        db.conn.commit()
        # Undo should skip 'released' and find 'status_changed'
        result = db.undo_last(issue.id, actor="t")
        assert result["undone"] is True
        assert result["event_type"] == "status_changed"
        assert result["issue"]["status"] == "open"

    def test_undo_nonexistent_issue_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.undo_last("nonexistent-abc123")

    def test_undone_event_recorded(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        db.update_issue(issue.id, status="in_progress", actor="t")
        db.undo_last(issue.id, actor="undoer")

        events = db.get_issue_events(issue.id)
        undone_events = [e for e in events if e["event_type"] == "undone"]
        assert len(undone_events) == 1
        assert undone_events[0]["actor"] == "undoer"
        assert undone_events[0]["old_value"] == "status_changed"


class TestGetIssueEvents:
    def test_returns_events_newest_first(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        db.update_issue(issue.id, title="Changed")
        events = db.get_issue_events(issue.id)
        assert len(events) >= 2
        assert events[0]["event_type"] == "title_changed"
        assert events[1]["event_type"] == "created"

    def test_respects_limit(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        db.update_issue(issue.id, title="T2")
        db.update_issue(issue.id, title="T3")
        events = db.get_issue_events(issue.id, limit=1)
        assert len(events) == 1

    def test_raises_on_nonexistent(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_issue_events("nonexistent-abc123")


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
