"""Verify mixin-based FiligreeDB composition works correctly."""

import tempfile
from pathlib import Path

from filigree.core import FiligreeDB
from filigree.db_events import EventsMixin
from filigree.db_workflow import WorkflowMixin


def _make_db() -> FiligreeDB:
    tmp = tempfile.mkdtemp()
    db = FiligreeDB(Path(tmp) / "test.db", prefix="test")
    db.initialize()
    return db


def test_events_mixin_is_base_class() -> None:
    """FiligreeDB should inherit from EventsMixin."""
    assert issubclass(FiligreeDB, EventsMixin)


def test_record_event_available() -> None:
    """_record_event should be callable on FiligreeDB instances."""
    db = _make_db()
    issue = db.create_issue(title="test")
    db._record_event(issue.id, "test_event", actor="test")
    events = db.get_issue_events(issue.id)
    assert any(e["event_type"] == "test_event" for e in events)
    db.close()


def test_undo_last_available() -> None:
    """undo_last should work through mixin composition."""
    db = _make_db()
    issue = db.create_issue(title="original")
    db.update_issue(issue.id, title="changed")
    result = db.undo_last(issue.id)
    assert result["event_type"] == "title_changed"
    db.close()


def test_archive_compact_available() -> None:
    """archive_closed and compact_events should work."""
    db = _make_db()
    archived = db.archive_closed(days_old=0)
    assert isinstance(archived, list)
    compacted = db.compact_events(keep_recent=50)
    assert isinstance(compacted, int)
    db.close()


# -- WorkflowMixin ----------------------------------------------------------


def test_workflow_mixin_is_base_class() -> None:
    """FiligreeDB should inherit from WorkflowMixin."""
    assert issubclass(FiligreeDB, WorkflowMixin)


def test_templates_available() -> None:
    """Template listing should work through mixin composition."""
    db = _make_db()
    templates = db.list_templates()
    assert isinstance(templates, list)
    assert len(templates) > 0  # builtins exist
    db.close()


def test_validate_status() -> None:
    """_validate_status should work through mixin composition."""
    db = _make_db()
    # Should not raise for valid status
    db._validate_status("open", "task")
    db.close()
