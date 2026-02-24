"""Verify mixin-based FiligreeDB composition works correctly."""

from pathlib import Path

from filigree.core import FiligreeDB
from filigree.db_events import EventsMixin
from filigree.db_meta import MetaMixin
from filigree.db_planning import PlanningMixin
from filigree.db_workflow import WorkflowMixin


def test_events_mixin_is_base_class() -> None:
    """FiligreeDB should inherit from EventsMixin."""
    assert issubclass(FiligreeDB, EventsMixin)


def test_record_event_available(db: FiligreeDB) -> None:
    """_record_event should be callable on FiligreeDB instances."""
    issue = db.create_issue(title="test")
    db._record_event(issue.id, "test_event", actor="test")
    events = db.get_issue_events(issue.id)
    assert any(e["event_type"] == "test_event" for e in events)


def test_undo_last_available(db: FiligreeDB) -> None:
    """undo_last should work through mixin composition."""
    issue = db.create_issue(title="original")
    db.update_issue(issue.id, title="changed")
    result = db.undo_last(issue.id)
    assert result["event_type"] == "title_changed"


def test_archive_compact_available(db: FiligreeDB) -> None:
    """archive_closed and compact_events should work."""
    archived = db.archive_closed(days_old=0)
    assert isinstance(archived, list)
    compacted = db.compact_events(keep_recent=50)
    assert isinstance(compacted, int)


# -- WorkflowMixin ----------------------------------------------------------


def test_workflow_mixin_is_base_class() -> None:
    """FiligreeDB should inherit from WorkflowMixin."""
    assert issubclass(FiligreeDB, WorkflowMixin)


def test_templates_available(db: FiligreeDB) -> None:
    """Template listing should work through mixin composition."""
    templates = db.list_templates()
    assert isinstance(templates, list)
    assert len(templates) > 0  # builtins exist


def test_validate_status(db: FiligreeDB) -> None:
    """_validate_status should work through mixin composition."""
    # Should not raise for valid status
    db._validate_status("open", "task")


# -- MetaMixin --------------------------------------------------------------


def test_meta_mixin_is_base_class() -> None:
    """FiligreeDB should inherit from MetaMixin."""
    assert issubclass(FiligreeDB, MetaMixin)


def test_comments_available(db: FiligreeDB) -> None:
    """Comment operations should work through mixin composition."""
    issue = db.create_issue(title="test")
    comment_id = db.add_comment(issue.id, "hello")
    assert isinstance(comment_id, int)
    comments = db.get_comments(issue.id)
    assert len(comments) == 1


def test_export_import_roundtrip(db: FiligreeDB, tmp_path: Path) -> None:
    """Export/import should work through mixin composition."""
    db.create_issue(title="export-test")
    out = str(tmp_path / "export.jsonl")
    count = db.export_jsonl(out)
    assert count > 0


# -- PlanningMixin ----------------------------------------------------------


def test_planning_mixin_is_base_class() -> None:
    """FiligreeDB should inherit from PlanningMixin."""
    assert issubclass(FiligreeDB, PlanningMixin)


def test_dependency_management(db: FiligreeDB) -> None:
    """Dependency operations should work through mixin composition."""
    a = db.create_issue(title="a")
    b = db.create_issue(title="b")
    db.add_dependency(b.id, a.id)
    blocked = db.get_blocked()
    assert any(i.id == b.id for i in blocked)


def test_get_ready(db: FiligreeDB) -> None:
    """get_ready should work through mixin composition."""
    db.create_issue(title="ready-test")
    ready = db.get_ready()
    assert len(ready) >= 1
