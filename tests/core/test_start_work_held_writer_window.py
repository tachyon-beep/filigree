"""§2.2 benchmark — held-writer window for start_work / start_next_work.

Pins the design's intent: candidate discovery and template lookups run
lock-free, only the claim+update composite acquires a writer lock. Uses
``sqlite3.Connection.set_trace_callback`` to observe every SQL statement
the engine executes and time the span from ``BEGIN IMMEDIATE`` to the
next ``COMMIT`` / ``ROLLBACK``.

The test gates on a generous wall-clock ceiling so it doesn't flake on
slow CI; under the §2.2 design the ceiling is comfortably met. A
regression that puts the candidate iteration or template lookup back
inside the lock would noticeably blow it out.
"""

from __future__ import annotations

import time

import pytest

from filigree.core import FiligreeDB


class _LockWindowTracker:
    """Records wall-clock duration of every BEGIN IMMEDIATE → COMMIT/ROLLBACK span."""

    def __init__(self) -> None:
        self.durations: list[float] = []
        self._start: float | None = None

    def __call__(self, sql: str) -> None:
        head = sql.lstrip().upper()
        if head.startswith("BEGIN IMMEDIATE"):
            self._start = time.perf_counter()
        elif head.startswith(("COMMIT", "ROLLBACK")) and self._start is not None:
            self.durations.append(time.perf_counter() - self._start)
            self._start = None


@pytest.mark.parametrize("with_explicit_target", [True, False])
def test_start_work_held_writer_window_excludes_template_lookup(
    db: FiligreeDB,
    with_explicit_target: bool,
) -> None:
    """``start_work`` holds the writer lock only across the claim+update
    composite, not the template lookup that resolves ``target_status``."""
    issue = db.create_issue("contended", priority=1)
    target = "in_progress" if with_explicit_target else None

    tracker = _LockWindowTracker()
    db.conn.set_trace_callback(tracker)
    try:
        db.start_work(issue.id, assignee="alice", target_status=target)
    finally:
        db.conn.set_trace_callback(None)

    # Exactly one writer-lock window opened during start_work
    # (the _start_work_locked critical section).
    assert len(tracker.durations) == 1, f"expected 1 BEGIN/COMMIT pair, got {tracker.durations}"
    # Held window must be small — under §2.2 it covers UPDATE + UPDATE +
    # event INSERTs and no template work. 250ms is comfortably above
    # measured durations on slow CI but well under what a regression
    # putting the template lookup inside the lock would produce on a
    # template with many states.
    assert tracker.durations[0] < 0.25, f"writer lock held too long: {tracker.durations[0]:.3f}s"


def test_start_next_work_iteration_runs_outside_writer_lock(db: FiligreeDB) -> None:
    """``start_next_work`` iterates ``get_ready()`` candidates outside any
    writer lock; only the per-candidate claim+update enters BEGIN IMMEDIATE."""
    issues = [db.create_issue(f"ready-{i}", priority=2) for i in range(3)]

    tracker = _LockWindowTracker()
    db.conn.set_trace_callback(tracker)
    try:
        result = db.start_next_work(assignee="alice")
    finally:
        db.conn.set_trace_callback(None)

    assert result is not None
    assert result.id in {i.id for i in issues}
    # One writer-lock window per successful start (the first candidate).
    assert len(tracker.durations) == 1
    # And it covers only the claim+update writes.
    assert tracker.durations[0] < 0.25
