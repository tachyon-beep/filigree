"""Tests for core CRUD operations — create, get, update, close, reopen, claim, export, import, archival."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from filigree.core import FiligreeDB


class TestCreateAndGet:
    def test_create_issue(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix the widget")
        assert issue.title == "Fix the widget"
        assert issue.status == "open"
        assert issue.id.startswith("test-")

    def test_get_issue(self, db: FiligreeDB) -> None:
        created = db.create_issue("Something")
        fetched = db.get_issue(created.id)
        assert fetched.title == "Something"
        assert fetched.id == created.id

    def test_get_missing_issue_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_issue("nonexistent-abc123")


class TestUpdateAndClose:
    def test_update_status(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Do the thing")
        updated = db.update_issue(issue.id, status="in_progress")
        assert updated.status == "in_progress"

    def test_close_issue(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Close me")
        closed = db.close_issue(issue.id, reason="done")
        assert closed.status == "closed"
        assert closed.closed_at is not None

    def test_close_already_closed_raises(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Close me twice")
        db.close_issue(issue.id)
        with pytest.raises(ValueError, match="already closed"):
            db.close_issue(issue.id)


class TestLabelsAndComments:
    def test_labels_on_create(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Labeled", labels=["defect", "urgent"])
        assert set(issue.labels) == {"defect", "urgent"}

    def test_add_remove_label(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Label test")
        db.add_label(issue.id, "backend")
        refreshed = db.get_issue(issue.id)
        assert "backend" in refreshed.labels
        db.remove_label(issue.id, "backend")
        refreshed = db.get_issue(issue.id)
        assert "backend" not in refreshed.labels

    def test_create_rejects_reserved_type_label(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="reserved as an issue type"):
            db.create_issue("Bad labels", labels=["bug", "urgent"])

    def test_add_label_rejects_reserved_type_label_case_insensitive(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Label test")
        with pytest.raises(ValueError, match="reserved as an issue type"):
            db.add_label(issue.id, "BuG")

    def test_add_comment(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Commentable")
        db.add_comment(issue.id, "This is a note", author="tester")
        comments = db.get_comments(issue.id)
        assert len(comments) == 1
        assert comments[0]["text"] == "This is a note"


class TestGenerateId:
    """Verify _generate_unique_id uses O(1) EXISTS check, not full-table scan."""

    def test_generate_id_returns_prefixed_id(self, db: FiligreeDB) -> None:
        issue_id = db._generate_unique_id("issues")
        assert issue_id.startswith("test-")
        assert len(issue_id) == len("test-") + 10

    def test_generate_id_avoids_collisions(self, db: FiligreeDB) -> None:
        ids = {db._generate_unique_id("issues") for _ in range(50)}
        assert len(ids) == 50

    def test_generate_id_uses_exists_check(self, db: FiligreeDB) -> None:
        """Verify the implementation queries by specific ID, not all IDs."""
        import inspect

        source = inspect.getsource(db._generate_unique_id)
        assert "SELECT 1 FROM {table} WHERE id = ?" in source
        assert "SELECT id FROM issues" not in source


class TestDescriptionNotesAuditTrail:
    """Verify description and notes changes produce audit events."""

    def test_description_changed_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test issue", description="old desc")
        db.update_issue(issue.id, description="new desc", actor="tester")

        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'description_changed'",
            (issue.id,),
        ).fetchall()
        assert len(events) == 1
        assert events[0]["old_value"] == "old desc"
        assert events[0]["new_value"] == "new desc"
        assert events[0]["actor"] == "tester"

    def test_notes_changed_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test issue", notes="old notes")
        db.update_issue(issue.id, notes="new notes", actor="tester")

        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'notes_changed'",
            (issue.id,),
        ).fetchall()
        assert len(events) == 1
        assert events[0]["old_value"] == "old notes"
        assert events[0]["new_value"] == "new notes"

    def test_no_event_when_description_unchanged(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test issue", description="same")
        db.update_issue(issue.id, description="same")

        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'description_changed'",
            (issue.id,),
        ).fetchall()
        assert len(events) == 0

    def test_no_event_when_notes_unchanged(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test issue", notes="same")
        db.update_issue(issue.id, notes="same")

        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'notes_changed'",
            (issue.id,),
        ).fetchall()
        assert len(events) == 0


class TestUpdateIssuePaths:
    def test_update_title(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Old title")
        updated = db.update_issue(issue.id, title="New title")
        assert updated.title == "New title"

    def test_update_priority(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Priority test", priority=2)
        updated = db.update_issue(issue.id, priority=0)
        assert updated.priority == 0

    def test_update_assignee(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Assign test")
        updated = db.update_issue(issue.id, assignee="alice")
        assert updated.assignee == "alice"

    def test_update_description(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Desc test")
        updated = db.update_issue(issue.id, description="new desc")
        assert updated.description == "new desc"

    def test_update_notes(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Notes test")
        updated = db.update_issue(issue.id, notes="new notes")
        assert updated.notes == "new notes"

    def test_update_fields_merge(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fields test", fields={"a": "1", "b": "2"})
        updated = db.update_issue(issue.id, fields={"b": "updated", "c": "3"})
        assert updated.fields == {"a": "1", "b": "updated", "c": "3"}

    def test_update_no_changes(self, db: FiligreeDB) -> None:
        """Update with no actual changes should not error."""
        issue = db.create_issue("No change")
        updated = db.update_issue(issue.id)
        assert updated.title == "No change"

    def test_update_nonexistent_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.update_issue("nonexistent-abc123", title="nope")


class TestIssueToDictRoundTrip:
    def test_to_dict_has_all_fields(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Roundtrip", labels=["a"], fields={"x": "1"})
        d = issue.to_dict()
        expected_keys = {
            "id",
            "title",
            "status",
            "status_category",
            "priority",
            "type",
            "parent_id",
            "assignee",
            "created_at",
            "updated_at",
            "closed_at",
            "description",
            "notes",
            "fields",
            "labels",
            "blocks",
            "blocked_by",
            "is_ready",
            "children",
        }
        assert set(d.keys()) == expected_keys
        assert d["labels"] == ["a"]
        assert d["fields"] == {"x": "1"}


class TestChildren:
    def test_children_populated(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent", type="epic")
        child1 = db.create_issue("Child 1", parent_id=parent.id)
        child2 = db.create_issue("Child 2", parent_id=parent.id)
        refreshed = db.get_issue(parent.id)
        assert set(refreshed.children) == {child1.id, child2.id}


class TestCreateWithOptions:
    def test_create_with_all_options(self, db: FiligreeDB) -> None:
        issue = db.create_issue(
            "Full issue",
            type="bug",
            priority=0,
            assignee="alice",
            description="A description",
            notes="Some notes",
            fields={"severity": "critical"},
            labels=["urgent", "backend"],
            deps=None,
        )
        assert issue.type == "bug"
        assert issue.priority == 0
        assert issue.assignee == "alice"
        assert issue.description == "A description"
        assert issue.notes == "Some notes"
        assert issue.fields["severity"] == "critical"
        assert set(issue.labels) == {"urgent", "backend"}

    def test_create_with_deps(self, db: FiligreeDB) -> None:
        blocker = db.create_issue("Blocker")
        issue = db.create_issue("Blocked", deps=[blocker.id])
        assert blocker.id in issue.blocked_by


class TestClaimIssue:
    def test_claim_success(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Claimable")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        assert claimed.status == "open"  # status unchanged
        assert claimed.assignee == "agent-1"

    def test_claim_step_uses_template_states(self, db: FiligreeDB) -> None:
        """Step type uses template open-category states (pending), not legacy 'open'."""
        step = db.create_issue("Step", type="step")
        assert step.status == "pending"  # step template initial state
        claimed = db.claim_issue(step.id, assignee="agent-1")
        assert claimed.status == "pending"  # status unchanged
        assert claimed.assignee == "agent-1"

    def test_claim_already_claimed(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Claimable")
        db.claim_issue(issue.id, assignee="agent-1")
        with pytest.raises(ValueError, match="already assigned to"):
            db.claim_issue(issue.id, assignee="agent-2")

    def test_claim_closed_issue(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Will close")
        db.close_issue(issue.id)
        with pytest.raises(ValueError, match="status is 'closed'"):
            db.claim_issue(issue.id, assignee="agent-1")

    def test_claim_not_found(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError, match="not found"):
            db.claim_issue("nonexistent-abc123", assignee="agent-1")

    def test_claim_records_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Claimable")
        db.claim_issue(issue.id, assignee="agent-1", actor="agent-1")
        events = db.get_recent_events(limit=5)
        claim_event = next(e for e in events if e["event_type"] == "claimed")
        assert claim_event["issue_id"] == issue.id
        assert claim_event["new_value"] == "agent-1"
        assert claim_event["actor"] == "agent-1"


class TestClaimEmptyAssignee:
    """Bug filigree-040ddb: claim_issue/claim_next must reject empty assignee."""

    @pytest.mark.parametrize("assignee", ["", "   "], ids=["empty", "whitespace"])
    def test_claim_issue_rejects_blank_assignee(self, db: FiligreeDB, assignee: str) -> None:
        issue = db.create_issue("Claimable")
        with pytest.raises(ValueError, match="Assignee cannot be empty"):
            db.claim_issue(issue.id, assignee=assignee)

    @pytest.mark.parametrize("assignee", ["", "   "], ids=["empty", "whitespace"])
    def test_claim_next_rejects_blank_assignee(self, db: FiligreeDB, assignee: str) -> None:
        db.create_issue("Ready")
        with pytest.raises(ValueError, match="Assignee cannot be empty"):
            db.claim_next(assignee)


class TestReparenting:
    def test_update_parent_id(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child")
        updated = db.update_issue(child.id, parent_id=parent.id)
        assert updated.parent_id == parent.id

    def test_update_parent_id_records_event(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child")
        db.update_issue(child.id, parent_id=parent.id, actor="tester")
        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'parent_changed'",
            (child.id,),
        ).fetchall()
        assert len(events) == 1
        assert events[0]["new_value"] == parent.id

    def test_update_parent_id_invalid_parent_raises(self, db: FiligreeDB) -> None:
        child = db.create_issue("Child")
        with pytest.raises(ValueError, match="does not reference"):
            db.update_issue(child.id, parent_id="nonexistent-123456")

    def test_update_parent_id_self_reference_raises(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Issue")
        with pytest.raises(ValueError, match="cannot be its own parent"):
            db.update_issue(issue.id, parent_id=issue.id)

    def test_update_parent_id_cycle_raises(self, db: FiligreeDB) -> None:
        grandparent = db.create_issue("Grandparent")
        parent = db.create_issue("Parent", parent_id=grandparent.id)
        child = db.create_issue("Child", parent_id=parent.id)
        with pytest.raises(ValueError, match="circular"):
            db.update_issue(grandparent.id, parent_id=child.id)

    def test_clear_parent_id(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child", parent_id=parent.id)
        updated = db.update_issue(child.id, parent_id="")
        assert updated.parent_id is None

    def test_update_parent_id_no_change_no_event(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child", parent_id=parent.id)
        db.update_issue(child.id, parent_id=parent.id)
        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'parent_changed'",
            (child.id,),
        ).fetchall()
        assert len(events) == 0


class TestClaimNextExhaustion:
    """Bug fix: filigree-2e5383 — claim_next logs when all candidates fail."""

    def test_claim_next_no_warning_when_no_candidates(self, db: FiligreeDB) -> None:
        """When no ready issues exist, claim_next returns None without warning."""
        # Claim all pre-existing ready issues (e.g. the Future release singleton)
        for existing in db.get_ready():
            db.claim_issue(existing.id, assignee="agent1")
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


# ---------------------------------------------------------------------------
# Export / Import (JSONL)
# ---------------------------------------------------------------------------


class TestExportJsonl:
    def test_export_populated(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "export.jsonl"
        count = populated_db.export_jsonl(out)
        assert count > 0
        lines = out.read_text().strip().split("\n")
        assert len(lines) == count

    def test_export_record_types(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "export.jsonl"
        populated_db.export_jsonl(out)
        types_seen = set()
        for line in out.read_text().strip().split("\n"):
            record = json.loads(line)
            types_seen.add(record["_type"])
        assert "issue" in types_seen
        assert "dependency" in types_seen
        assert "label" in types_seen
        assert "comment" in types_seen
        assert "event" in types_seen

    def test_export_issue_fields(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "export.jsonl"
        populated_db.export_jsonl(out)
        issues = []
        for line in out.read_text().strip().split("\n"):
            record = json.loads(line)
            if record["_type"] == "issue":
                issues.append(record)
        assert len(issues) == 5  # Future release + epic + A + B + C
        assert any(i["title"] == "Issue A" for i in issues)

    def test_export_empty_db(self, db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "export.jsonl"
        count = db.export_jsonl(out)
        # DB has the auto-seeded Future release singleton + its created event
        assert count >= 1
        lines = [line for line in out.read_text().strip().split("\n") if line]
        issues = [json.loads(line) for line in lines if json.loads(line).get("_type") == "issue"]
        assert len(issues) == 1
        assert issues[0]["title"] == "Future"


class TestImportJsonl:
    def test_import_roundtrip(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        """Export from populated, import into fresh — counts should match."""
        out = tmp_path / "roundtrip.jsonl"
        export_count = populated_db.export_jsonl(out)

        fresh = FiligreeDB(tmp_path / "fresh.db", prefix="test")
        fresh.initialize()
        import_count = fresh.import_jsonl(out)
        assert import_count == export_count
        fresh.close()

    def test_import_issues_arrive(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "data.jsonl"
        populated_db.export_jsonl(out)

        fresh = FiligreeDB(tmp_path / "fresh2.db", prefix="test")
        fresh.initialize()
        fresh.import_jsonl(out)
        issues = fresh.list_issues(limit=100)
        # fresh DB auto-seeds 1 Future + imports 5 (Future + epic + A + B + C) = 6
        assert len(issues) == 6
        titles = {i.title for i in issues}
        assert "Issue A" in titles
        assert "Epic E" in titles
        fresh.close()

    def test_import_dependencies_arrive(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "data.jsonl"
        populated_db.export_jsonl(out)

        fresh = FiligreeDB(tmp_path / "fresh3.db", prefix="test")
        fresh.initialize()
        fresh.import_jsonl(out)
        deps = fresh.get_all_dependencies()
        assert len(deps) >= 1
        fresh.close()

    def test_import_merge_skips_existing(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "merge.jsonl"
        populated_db.export_jsonl(out)
        # Import twice with merge — second import should not fail
        count2 = populated_db.import_jsonl(out, merge=True)
        # All records skipped since they already exist (issues by PK, deps by PK, labels by PK)
        # Events don't have PK constraint so they get duplicated
        assert count2 >= 0

    def test_import_without_merge_fails_on_conflict(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "conflict.jsonl"
        populated_db.export_jsonl(out)
        with pytest.raises(sqlite3.IntegrityError):
            populated_db.import_jsonl(out, merge=False)

    def test_import_event_uses_conflict_variable(self, db: FiligreeDB, tmp_path: Path) -> None:
        """Bug filigree-769ea4: event branch must respect merge flag, not hardcode OR IGNORE."""
        # Create an issue first so the event FK is valid
        issue = db.create_issue("Event test")

        # Write a JSONL file with one event
        jsonl = tmp_path / "events.jsonl"
        event_line = json.dumps(
            {
                "_type": "event",
                "issue_id": issue.id,
                "event_type": "status_change",
                "actor": "alice",
                "old_value": "open",
                "new_value": "closed",
                "comment": "",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
        jsonl.write_text(event_line + "\n")

        # First import succeeds
        count1 = db.import_jsonl(jsonl)
        assert count1 == 1

        # Second import with merge=False should ABORT on the duplicate event
        with pytest.raises(sqlite3.IntegrityError):
            db.import_jsonl(jsonl, merge=False)

    def test_import_merge_event_count_accurate(self, db: FiligreeDB, tmp_path: Path) -> None:
        """Bug filigree-769ea4: merge=True must not count skipped events."""
        issue = db.create_issue("Count test")

        jsonl = tmp_path / "events.jsonl"
        event_line = json.dumps(
            {
                "_type": "event",
                "issue_id": issue.id,
                "event_type": "status_change",
                "actor": "alice",
                "old_value": "open",
                "new_value": "closed",
                "comment": "",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
        jsonl.write_text(event_line + "\n")

        # Import once
        db.import_jsonl(jsonl, merge=True)

        # Import again with merge — duplicate event should be skipped, count=0
        count2 = db.import_jsonl(jsonl, merge=True)
        assert count2 == 0, f"Expected 0 (duplicate skipped), got {count2}"

    def test_import_skips_unknown_types(self, db: FiligreeDB, tmp_path: Path) -> None:
        jsonl = tmp_path / "unknown.jsonl"
        jsonl.write_text('{"_type": "alien", "data": "hello"}\n')
        count = db.import_jsonl(jsonl)
        assert count == 0

    def test_import_skips_blank_lines(self, db: FiligreeDB, tmp_path: Path) -> None:
        jsonl = tmp_path / "blanks.jsonl"
        jsonl.write_text('\n\n{"_type": "issue", "id": "test-aaa111", "title": "Blank test"}\n\n')
        count = db.import_jsonl(jsonl)
        assert count == 1


# ---------------------------------------------------------------------------
# Archival & Compaction
# ---------------------------------------------------------------------------


class TestArchival:
    def test_archive_old_closed(self, db: FiligreeDB) -> None:
        issue = db.create_issue("To archive")
        db.close_issue(issue.id)
        # Manually backdate closed_at
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        db.conn.execute("UPDATE issues SET closed_at = ? WHERE id = ?", (old_date, issue.id))
        db.conn.commit()

        archived = db.archive_closed(days_old=30)
        assert issue.id in archived
        refreshed = db.get_issue(issue.id)
        assert refreshed.status == "archived"

    def test_archive_skips_recent(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Recent close")
        db.close_issue(issue.id)
        # closed_at is now — should NOT be archived
        archived = db.archive_closed(days_old=30)
        assert issue.id not in archived

    def test_archive_skips_open(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Still open")
        archived = db.archive_closed(days_old=0)
        assert issue.id not in archived

    def test_archive_records_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Archive event")
        db.close_issue(issue.id)
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        db.conn.execute("UPDATE issues SET closed_at = ? WHERE id = ?", (old_date, issue.id))
        db.conn.commit()

        db.archive_closed(days_old=30, actor="janitor")
        events = db.get_recent_events(limit=10)
        archived_events = [e for e in events if e["event_type"] == "archived"]
        assert len(archived_events) == 1
        assert archived_events[0]["actor"] == "janitor"


class TestCompaction:
    def test_compact_archived_events(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Compact me")
        # Directly insert many events
        for i in range(60):
            db.conn.execute(
                "INSERT INTO events (issue_id, event_type, actor, created_at) VALUES (?, ?, ?, ?)",
                (issue.id, "test_event", "tester", f"2026-01-01T00:{i:02d}:00+00:00"),
            )
        db.conn.commit()
        db.close_issue(issue.id)
        # Backdate and archive
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        db.conn.execute("UPDATE issues SET closed_at = ? WHERE id = ?", (old_date, issue.id))
        db.conn.commit()
        db.archive_closed(days_old=30)

        # Count events before
        before = db.conn.execute("SELECT COUNT(*) as cnt FROM events WHERE issue_id = ?", (issue.id,)).fetchone()["cnt"]
        assert before > 50

        # Compact
        deleted = db.compact_events(keep_recent=10)
        assert deleted > 0

        # Count events after
        after = db.conn.execute("SELECT COUNT(*) as cnt FROM events WHERE issue_id = ?", (issue.id,)).fetchone()["cnt"]
        assert after == 10

    def test_compact_skips_non_archived(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Not archived")
        for i in range(20):
            db.update_issue(issue.id, notes=f"note {i}")
        deleted = db.compact_events(keep_recent=5)
        assert deleted == 0

    def test_vacuum(self, db: FiligreeDB) -> None:
        # vacuum() returns None; verify it completes without error
        result = db.vacuum()
        assert result is None

    def test_analyze(self, db: FiligreeDB) -> None:
        result = db.analyze()
        assert result is None
