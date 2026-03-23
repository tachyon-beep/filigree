"""EventsMixin — event recording, undo, archive, and compaction.

Extracted from core.py as part of the module architecture split.
All methods access ``self.conn``, ``self.get_issue()``, etc. via
Python's MRO when composed into ``FiligreeDB``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from filigree.db_base import DBMixinProtocol, _now_iso
from filigree.types.events import EventRecord, EventRecordWithTitle, EventType, UndoResult

# ---------------------------------------------------------------------------
# Undo constants (moved from core.py — only used by undo_last)
# ---------------------------------------------------------------------------

_REVERSIBLE_EVENTS = frozenset(
    {
        "status_changed",
        "title_changed",
        "priority_changed",
        "assignee_changed",
        "claimed",
        "dependency_added",
        "dependency_removed",
        "description_changed",
        "notes_changed",
        "fields_changed",
    }
)


class EventsMixin(DBMixinProtocol):
    """Event recording, undo, archive, and compaction methods.

    Inherits ``DBMixinProtocol`` for type-safe access to shared attributes
    (``self.conn``, ``self.get_issue()``, etc.). Actual implementations
    provided by ``FiligreeDB`` at composition time via MRO.
    """

    # -- Build helpers (replace cast() at SQL boundary) -----------------------

    @staticmethod
    def _build_event_record(row: sqlite3.Row) -> EventRecord:
        """Build an EventRecord from a database row with explicit key mapping."""
        return EventRecord(
            id=row["id"],
            issue_id=row["issue_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            old_value=row["old_value"],
            new_value=row["new_value"],
            comment=row["comment"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _build_event_record_with_title(row: sqlite3.Row) -> EventRecordWithTitle:
        """Build an EventRecordWithTitle from a joined database row."""
        return EventRecordWithTitle(
            id=row["id"],
            issue_id=row["issue_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            old_value=row["old_value"],
            new_value=row["new_value"],
            comment=row["comment"],
            created_at=row["created_at"],
            issue_title=row["issue_title"],
        )

    # -- Events (private) ----------------------------------------------------

    def _record_event(
        self,
        issue_id: str,
        event_type: EventType,
        *,
        actor: str = "",
        old_value: str | None = None,
        new_value: str | None = None,
        comment: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, actor, old_value, new_value, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (issue_id, event_type, actor, old_value, new_value, comment, _now_iso()),
        )

    def get_recent_events(self, limit: int = 20) -> list[EventRecordWithTitle]:
        rows = self.conn.execute(
            "SELECT e.*, i.title as issue_title FROM events e "
            "JOIN issues i ON e.issue_id = i.id "
            "ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._build_event_record_with_title(r) for r in rows]

    def get_events_since(self, since: str, *, limit: int = 100) -> list[EventRecordWithTitle]:
        """Get events since a given ISO timestamp, ordered chronologically."""
        rows = self.conn.execute(
            "SELECT e.*, i.title as issue_title FROM events e "
            "JOIN issues i ON e.issue_id = i.id "
            "WHERE e.created_at > ? "
            "ORDER BY e.created_at ASC, e.id ASC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [self._build_event_record_with_title(r) for r in rows]

    def get_issue_events(self, issue_id: str, *, limit: int = 50) -> list[EventRecord]:
        """Get events for a specific issue, newest first."""
        self.get_issue(issue_id)  # raises KeyError if not found
        rows = self.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (issue_id, limit),
        ).fetchall()
        return [self._build_event_record(r) for r in rows]

    def undo_last(self, issue_id: str, *, actor: str = "") -> UndoResult:
        """Undo the most recent reversible event for an issue.

        Returns dict with 'undone' bool and details. Only reverses the single
        most recent reversible event — 'undone' events are not themselves
        undoable, preventing undo chains.
        """
        current = self.get_issue(issue_id)
        now = _now_iso()

        # Find the most recent reversible event directly (skips non-reversible events
        # like 'created', 'released', 'archived' so undo can reach earlier reversible ones)
        rev_ph = ",".join("?" * len(_REVERSIBLE_EVENTS))
        row = self.conn.execute(
            f"SELECT * FROM events WHERE issue_id = ? AND event_type IN ({rev_ph}) ORDER BY created_at DESC, id DESC LIMIT 1",
            (issue_id, *_REVERSIBLE_EVENTS),
        ).fetchone()

        if row is None:
            return {"undone": False, "reason": "No reversible events to undo"}

        event_type = row["event_type"]
        event_id = row["id"]

        # Check if this event was already undone (an 'undone' event references it)
        already_undone = self.conn.execute(
            "SELECT 1 FROM events WHERE issue_id = ? AND event_type = 'undone' AND new_value = ?",
            (issue_id, str(event_id)),
        ).fetchone()
        if already_undone:
            return {"undone": False, "reason": "Most recent reversible event already undone"}

        # Apply reverse action — wrapped in try/except for rollback safety
        try:
            match event_type:
                case "status_changed":
                    old_status = row["old_value"]
                    if old_status is None:
                        return {"undone": False, "reason": "Cannot undo: event has no old_value"}
                    # Direct SQL update — bypasses transition validation for undo
                    self.conn.execute(
                        "UPDATE issues SET status = ?, updated_at = ? WHERE id = ?",
                        (old_status, now, issue_id),
                    )
                    # Maintain closed_at consistency with the restored status
                    old_cat = self._resolve_status_category(current.type, old_status)
                    if old_cat == "done":
                        # Restoring to a done state — set closed_at
                        self.conn.execute(
                            "UPDATE issues SET closed_at = ? WHERE id = ?",
                            (now, issue_id),
                        )
                    else:
                        # Restoring to a non-done state — clear closed_at
                        self.conn.execute(
                            "UPDATE issues SET closed_at = NULL WHERE id = ?",
                            (issue_id,),
                        )

                case "title_changed":
                    if row["old_value"] is None:
                        return {"undone": False, "reason": "Cannot undo: event has no old_value"}
                    self.conn.execute(
                        "UPDATE issues SET title = ?, updated_at = ? WHERE id = ?",
                        (row["old_value"], now, issue_id),
                    )

                case "priority_changed":
                    if row["old_value"] is None:
                        return {"undone": False, "reason": "Cannot undo: event has no old_value"}
                    try:
                        old_priority = int(row["old_value"])
                    except (ValueError, TypeError):
                        return {"undone": False, "reason": f"Cannot undo: old_value {row['old_value']!r} is not a valid priority"}
                    self.conn.execute(
                        "UPDATE issues SET priority = ?, updated_at = ? WHERE id = ?",
                        (old_priority, now, issue_id),
                    )

                case "assignee_changed":
                    self.conn.execute(
                        "UPDATE issues SET assignee = ?, updated_at = ? WHERE id = ?",
                        (row["old_value"] or "", now, issue_id),
                    )

                case "claimed":
                    # Restore: revert to the assignee before the claim (usually '' but
                    # preserves prior assignee if the claim re-assigned from another agent)
                    self.conn.execute(
                        "UPDATE issues SET assignee = ?, updated_at = ? WHERE id = ?",
                        (row["old_value"] if row["old_value"] is not None else "", now, issue_id),
                    )

                case "dependency_added":
                    # Event: issue_id=from_id, new_value="type:depends_on_id"
                    if row["new_value"] is None:
                        return {"undone": False, "reason": "Cannot undo: event has no new_value"}
                    dep_target = row["new_value"].split(":", 1)[-1] if ":" in row["new_value"] else row["new_value"]
                    self.conn.execute(
                        "DELETE FROM dependencies WHERE issue_id = ? AND depends_on_id = ?",
                        (issue_id, dep_target),
                    )

                case "dependency_removed":
                    # Event: issue_id=from_id, old_value="dep_type:depends_on_id" or legacy "depends_on_id"
                    if row["old_value"] is None:
                        return {"undone": False, "reason": "Cannot undo: event has no old_value"}
                    old_val = row["old_value"]
                    if ":" in old_val:
                        dep_type, dep_target = old_val.split(":", 1)
                    else:
                        dep_type, dep_target = "blocks", old_val
                    # Check for cycles before re-inserting (inline DFS
                    # to avoid cross-mixin call that mypy can't resolve)
                    adj: dict[str, list[str]] = {}
                    for dep_row in self.conn.execute("SELECT issue_id, depends_on_id FROM dependencies").fetchall():
                        adj.setdefault(dep_row["issue_id"], []).append(dep_row["depends_on_id"])
                    visited: set[str] = set()
                    queue = [dep_target]
                    would_cycle = False
                    while queue:
                        cur = queue.pop()
                        if cur == issue_id:
                            would_cycle = True
                            break
                        if cur in visited:
                            continue
                        visited.add(cur)
                        queue.extend(adj.get(cur, ()))
                    if would_cycle:
                        return {"undone": False, "reason": "Cannot undo: restoring dependency would create a cycle"}
                    self.conn.execute(
                        "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, ?, ?)",
                        (issue_id, dep_target, dep_type, now),
                    )

                case "description_changed":
                    self.conn.execute(
                        "UPDATE issues SET description = ?, updated_at = ? WHERE id = ?",
                        (row["old_value"] or "", now, issue_id),
                    )

                case "notes_changed":
                    self.conn.execute(
                        "UPDATE issues SET notes = ?, updated_at = ? WHERE id = ?",
                        (row["old_value"] or "", now, issue_id),
                    )

                case "fields_changed":
                    old_fields = row["old_value"] or "{}"
                    try:
                        json.loads(old_fields)
                    except (json.JSONDecodeError, TypeError):
                        return {"undone": False, "reason": "Cannot undo: stored fields JSON is corrupt"}
                    self.conn.execute(
                        "UPDATE issues SET fields = ?, updated_at = ? WHERE id = ?",
                        (old_fields, now, issue_id),
                    )

            # Record the undo event
            self._record_event(
                issue_id,
                "undone",
                actor=actor,
                old_value=event_type,
                new_value=str(event_id),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return {
            "undone": True,
            "event_type": event_type,
            "event_id": event_id,
            "issue": self.get_issue(issue_id).to_dict(),
        }

    # -- Archival / Compaction ------------------------------------------------

    def archive_closed(self, *, days_old: int = 30, actor: str = "") -> list[str]:
        """Archive done-category issues older than `days_old` days.

        Sets their status to 'archived' (preserving closed_at).
        Returns list of archived issue IDs.
        """
        from datetime import timedelta

        cutoff_dt = datetime.now(UTC) - timedelta(days=days_old)
        cutoff = cutoff_dt.isoformat()

        # Requires WorkflowMixin._get_states_for_category via self
        done_states = self._get_states_for_category("done") or ["closed"]
        done_ph = ",".join("?" * len(done_states))
        rows = self.conn.execute(
            f"SELECT id FROM issues WHERE status IN ({done_ph}) AND closed_at < ? AND closed_at IS NOT NULL",
            [*done_states, cutoff],
        ).fetchall()

        archived_ids = [r["id"] for r in rows]
        if not archived_ids:
            return []

        now = _now_iso()
        try:
            for issue_id in archived_ids:
                self.conn.execute(
                    "UPDATE issues SET status = 'archived', updated_at = ? WHERE id = ?",
                    (now, issue_id),
                )
                self._record_event(issue_id, "archived", actor=actor)

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return archived_ids

    def compact_events(self, *, keep_recent: int = 50, actor: str = "") -> int:
        """Remove old events for archived issues, keeping only the most recent ones.

        Returns the number of events deleted.
        """
        archived = self.conn.execute("SELECT id FROM issues WHERE status = 'archived'").fetchall()
        if not archived:
            return 0

        total_deleted = 0
        try:
            for row in archived:
                issue_id = row["id"]
                event_count = self.conn.execute("SELECT COUNT(*) as cnt FROM events WHERE issue_id = ?", (issue_id,)).fetchone()["cnt"]

                if event_count <= keep_recent:
                    continue

                cursor = self.conn.execute(
                    "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE issue_id = ? ORDER BY created_at ASC, id ASC LIMIT ?)",
                    (issue_id, event_count - keep_recent),
                )
                total_deleted += cursor.rowcount

            if total_deleted > 0:
                self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return total_deleted

    def vacuum(self) -> None:
        """Run VACUUM to reclaim space after compaction."""
        self.conn.execute("VACUUM")

    def analyze(self) -> None:
        """Update query planner statistics."""
        self.conn.execute("ANALYZE")
