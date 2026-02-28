"""TypedDicts for db_events.py return types."""

from __future__ import annotations

from typing import Literal, TypeAlias, TypedDict

from filigree.types.core import ISOTimestamp, IssueDict


class EventRecord(TypedDict):
    """Row from the events table (SELECT * FROM events).

    Returned by ``get_issue_events()``.  The ``get_recent_events()`` and
    ``get_events_since()`` queries join on ``issues`` and add ``issue_title``,
    so they return ``EventRecordWithTitle`` instead.
    """

    id: int
    issue_id: str
    event_type: str
    actor: str
    old_value: str | None
    new_value: str | None
    comment: str
    created_at: ISOTimestamp


class EventRecordWithTitle(EventRecord):
    """EventRecord with the joined issue_title column.

    Returned by ``get_recent_events()`` and ``get_events_since()``.
    """

    issue_title: str


class UndoSuccess(TypedDict):
    """Successful undo result from ``undo_last()``."""

    undone: Literal[True]
    event_type: str
    event_id: int
    issue: IssueDict


class UndoFailure(TypedDict):
    """Failed undo result from ``undo_last()``."""

    undone: Literal[False]
    reason: str


UndoResult: TypeAlias = UndoSuccess | UndoFailure
