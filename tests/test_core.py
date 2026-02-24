"""Tests for filigree.core — FiligreeDB operations."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from filigree.core import FiligreeDB, find_filigree_command, get_mode, write_atomic


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


class TestDependencies:
    def test_add_dependency(self, db: FiligreeDB) -> None:
        a = db.create_issue("Blocked task")
        b = db.create_issue("Blocker task")
        db.add_dependency(a.id, b.id)
        refreshed = db.get_issue(a.id)
        assert b.id in refreshed.blocked_by

    def test_self_dependency_rejected(self, db: FiligreeDB) -> None:
        a = db.create_issue("Self")
        with pytest.raises(ValueError, match="self-dependency"):
            db.add_dependency(a.id, a.id)

    def test_cycle_detection(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        db.add_dependency(a.id, b.id)  # A depends on B
        with pytest.raises(ValueError, match="cycle"):
            db.add_dependency(b.id, a.id)  # B depends on A would cycle

    def test_ready_excludes_blocked(self, db: FiligreeDB) -> None:
        a = db.create_issue("Blocked")
        b = db.create_issue("Blocker")
        db.add_dependency(a.id, b.id)
        ready = db.get_ready()
        ready_ids = [i.id for i in ready]
        assert a.id not in ready_ids
        assert b.id in ready_ids

    def test_closing_blocker_unblocks(self, db: FiligreeDB) -> None:
        a = db.create_issue("Blocked")
        b = db.create_issue("Blocker")
        db.add_dependency(a.id, b.id)
        db.close_issue(b.id)
        ready = db.get_ready()
        ready_ids = [i.id for i in ready]
        assert a.id in ready_ids


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


class TestGenerateId:
    """Verify _generate_unique_id uses O(1) EXISTS check, not full-table scan."""

    def test_generate_id_returns_prefixed_id(self, db: FiligreeDB) -> None:
        issue_id = db._generate_unique_id("issues")
        assert issue_id.startswith("test-")
        assert len(issue_id) == len("test-") + 6

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


class TestGetMode:
    def test_default_mode_is_ethereal(self, tmp_path: Path) -> None:
        """Projects without a mode field default to ethereal."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == "ethereal"

    def test_explicit_ethereal(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "ethereal"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == "ethereal"

    def test_explicit_server(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "server"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == "server"

    def test_missing_config_defaults_to_ethereal(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        assert get_mode(filigree_dir) == "ethereal"

    def test_unknown_mode_falls_back_to_ethereal(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Unknown mode values fall back to ethereal with a warning."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "bogus"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        with caplog.at_level(logging.WARNING, logger="filigree.core"):
            result = get_mode(filigree_dir)
        assert result == "ethereal"
        assert "bogus" in caplog.text


class TestFindFiligreeCommand:
    def test_returns_list(self) -> None:
        """Command is always a list of strings."""
        result = find_filigree_command()
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)

    def test_at_least_one_element(self) -> None:
        result = find_filigree_command()
        assert len(result) >= 1


class TestWriteAtomic:
    def test_writes_content(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        write_atomic(target, "hello")
        assert target.read_text() == "hello"

    def test_no_tmp_file_left(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        write_atomic(target, "hello")
        assert not (tmp_path / "test.txt.tmp").exists()

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        """Overwriting an existing file works correctly."""
        target = tmp_path / "test.txt"
        target.write_text("original")
        write_atomic(target, "updated")
        assert target.read_text() == "updated"
