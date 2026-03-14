"""Tests for core batch operations — batch close, update, label, comment."""

from __future__ import annotations

import pytest

from filigree.core import FiligreeDB


class TestBatchOperations:
    def test_batch_close(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        results, errors = db.batch_close([a.id, b.id], reason="done")
        assert len(results) == 2
        assert len(errors) == 0
        assert all(r.status == "closed" for r in results)

    def test_batch_update_status(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        results, errors = db.batch_update([a.id, b.id], status="in_progress")
        assert len(results) == 2
        assert len(errors) == 0
        assert all(r.status == "in_progress" for r in results)

    def test_batch_update_priority(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        results, errors = db.batch_update([a.id, b.id], priority=0)
        assert all(r.priority == 0 for r in results)
        assert len(errors) == 0

    def test_batch_update_not_found(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        results, errors = db.batch_update([a.id, "nonexistent-xyz"], priority=0)
        assert len(results) == 1
        assert len(errors) == 1
        assert errors[0]["id"] == "nonexistent-xyz"

    def test_batch_close_not_found(self, db: FiligreeDB) -> None:
        results, errors = db.batch_close(["nonexistent-xyz"])
        assert len(results) == 0
        assert len(errors) == 1
        assert errors[0]["id"] == "nonexistent-xyz"

    def test_batch_add_label(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        labeled, errors = db.batch_add_label([a.id, b.id], label="security")
        assert len(labeled) == 2
        assert len(errors) == 0
        assert all(row["status"] == "added" for row in labeled)

    def test_batch_add_label_not_found(self, db: FiligreeDB) -> None:
        labeled, errors = db.batch_add_label(["nonexistent-xyz"], label="security")
        assert labeled == []
        assert len(errors) == 1
        assert errors[0]["code"] == "not_found"

    def test_batch_add_label_validation_error(self, db: FiligreeDB) -> None:
        issue = db.create_issue("A")
        labeled, errors = db.batch_add_label([issue.id], label="bug")
        assert labeled == []
        assert len(errors) == 1
        assert errors[0]["code"] == "validation_error"

    def test_batch_add_comment(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        commented, errors = db.batch_add_comment([a.id, b.id], text="triage complete", author="agent-1")
        assert len(commented) == 2
        assert len(errors) == 0
        assert all(isinstance(row["comment_id"], int) for row in commented)

    def test_batch_add_comment_not_found(self, db: FiligreeDB) -> None:
        commented, errors = db.batch_add_comment(["nonexistent-xyz"], text="triage complete")
        assert commented == []
        assert len(errors) == 1
        assert errors[0]["code"] == "not_found"

    def test_batch_add_comment_validation_error(self, db: FiligreeDB) -> None:
        issue = db.create_issue("A")
        commented, errors = db.batch_add_comment([issue.id], text="   ")
        assert commented == []
        assert len(errors) == 1
        assert errors[0]["code"] == "validation_error"


class TestBatchInputValidation:
    """Bug filigree-c45430: batch_close/batch_update must validate issue_ids type."""

    def test_batch_close_string_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_close("not-a-list")  # type: ignore[arg-type]

    def test_batch_close_list_of_ints_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_close([1, 2, 3])  # type: ignore[list-item]

    def test_batch_update_string_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_update("not-a-list", status="closed")  # type: ignore[arg-type]

    def test_batch_update_list_of_ints_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_update([1, 2, 3], status="closed")  # type: ignore[list-item]

    def test_batch_add_label_string_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_add_label("not-a-list", label="security")  # type: ignore[arg-type]

    def test_batch_add_comment_string_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_add_comment("not-a-list", text="note")  # type: ignore[arg-type]

    def test_batch_close_valid_list_passes(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Closeable")
        closed, errors = db.batch_close([issue.id])
        assert len(closed) == 1
        assert len(errors) == 0

    def test_batch_update_valid_list_passes(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Updateable")
        updated, errors = db.batch_update([issue.id], priority=0)
        assert len(updated) == 1
        assert len(errors) == 0

    def test_batch_add_label_valid_list_passes(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Labelable")
        labeled, errors = db.batch_add_label([issue.id], label="security")
        assert len(labeled) == 1
        assert len(errors) == 0

    def test_batch_add_comment_valid_list_passes(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Commentable")
        commented, errors = db.batch_add_comment([issue.id], text="done")
        assert len(commented) == 1
        assert len(errors) == 0


class TestBatchTransitionEnrichmentRace:
    """batch close/update should handle issue deletion between action and transition lookup."""

    def test_batch_close_already_closed_includes_valid_transitions(self, db: FiligreeDB) -> None:
        """A ValueError during close should enrich with valid_transitions."""
        issue = db.create_issue("Test")
        db.close_issue(issue.id)
        # Closing again triggers ValueError; enrichment should add valid_transitions
        _results, errors = db.batch_close([issue.id])
        assert len(errors) == 1
        assert errors[0]["code"] == "invalid_transition"

    def test_batch_close_deleted_issue_after_valueerror(self, db: FiligreeDB) -> None:
        """If issue is deleted between ValueError and get_valid_transitions, no crash.

        Simulates a TOCTOU race: close_issue raises ValueError (already closed),
        then get_valid_transitions raises KeyError (concurrent deletion).
        """
        from unittest.mock import patch

        issue = db.create_issue("Test")
        db.close_issue(issue.id)
        # Mock get_valid_transitions to raise KeyError, simulating concurrent deletion
        with patch.object(db, "get_valid_transitions", side_effect=KeyError(issue.id)):
            _results, errors = db.batch_close([issue.id])
        assert len(errors) == 1
        assert errors[0]["code"] == "invalid_transition"
        # Should NOT have valid_transitions key since the lookup failed
        assert "valid_transitions" not in errors[0]
