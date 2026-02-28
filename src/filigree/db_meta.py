"""MetaMixin — comments, labels, stats, bulk operations, and export/import.

Extracted from core.py as part of the module architecture split.
All methods access ``self.conn``, ``self.get_issue()``, etc. via
Python's MRO when composed into ``FiligreeDB``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from filigree.db_base import DBMixinProtocol, StatusCategory, _now_iso
from filigree.types.planning import CommentRecord, StatsResult


class MetaMixin(DBMixinProtocol):
    """Comments, labels, stats, bulk operations, and export/import.

    Inherits ``DBMixinProtocol`` for type-safe access to shared attributes.
    Actual implementations provided by ``FiligreeDB`` at composition time via MRO.
    """

    if TYPE_CHECKING:

        def _validate_label_name(self, label: str) -> str: ...
        def _validate_parent_id(self, parent_id: str | None) -> None: ...
        def _resolve_status_category(self, issue_type: str, status: str) -> StatusCategory: ...
        def _resolve_open_done_states(self) -> tuple[list[str], list[str], str, str]: ...

    # -- Comments ------------------------------------------------------------

    def add_comment(self, issue_id: str, text: str, *, author: str = "") -> int:
        if not text or not text.strip():
            msg = "Comment text cannot be empty"
            raise ValueError(msg)
        now = _now_iso()
        cursor = self.conn.execute(
            "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
            (issue_id, author, text, now),
        )
        self.conn.commit()
        rowid = cursor.lastrowid
        if rowid is None:  # pragma: no cover — INSERT always sets lastrowid
            msg = "INSERT did not produce a lastrowid"
            raise RuntimeError(msg)
        return rowid

    def get_comments(self, issue_id: str) -> list[CommentRecord]:
        rows = self.conn.execute(
            "SELECT id, author, text, created_at FROM comments WHERE issue_id = ? ORDER BY created_at",
            (issue_id,),
        ).fetchall()
        return cast(list[CommentRecord], [dict(r) for r in rows])

    # -- Labels --------------------------------------------------------------

    def add_label(self, issue_id: str, label: str) -> bool:
        normalized = self._validate_label_name(label)
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO labels (issue_id, label) VALUES (?, ?)",
            (issue_id, normalized),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def remove_label(self, issue_id: str, label: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM labels WHERE issue_id = ? AND label = ?",
            (issue_id, label),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # -- Stats ---------------------------------------------------------------

    def get_stats(self) -> StatsResult:
        by_status = {}
        for row in self.conn.execute("SELECT status, COUNT(*) as cnt FROM issues GROUP BY status").fetchall():
            by_status[row["status"]] = row["cnt"]

        by_type = {}
        for row in self.conn.execute("SELECT type, COUNT(*) as cnt FROM issues GROUP BY type").fetchall():
            by_type[row["type"]] = row["cnt"]

        open_states, done_states, open_ph, done_ph = self._resolve_open_done_states()
        if not open_states:
            ready_count = 0
            blocked_count = 0
        else:
            ready_count = self.conn.execute(
                f"SELECT COUNT(*) as cnt FROM issues i "
                f"WHERE i.status IN ({open_ph}) "
                f"AND NOT EXISTS ("
                f"  SELECT 1 FROM dependencies d "
                f"  JOIN issues blocker ON d.depends_on_id = blocker.id "
                f"  WHERE d.issue_id = i.id AND blocker.status NOT IN ({done_ph})"
                f")",
                [*open_states, *done_states],
            ).fetchone()["cnt"]
            blocked_count = self.conn.execute(
                f"SELECT COUNT(DISTINCT i.id) as cnt FROM issues i "
                f"JOIN dependencies d ON d.issue_id = i.id "
                f"JOIN issues blocker ON d.depends_on_id = blocker.id "
                f"WHERE i.status IN ({open_ph}) AND blocker.status NOT IN ({done_ph})",
                [*open_states, *done_states],
            ).fetchone()["cnt"]

        dep_count = self.conn.execute("SELECT COUNT(*) as cnt FROM dependencies").fetchone()["cnt"]

        # Category-level counts (open/wip/done) via template-aware resolution
        by_category: dict[str, int] = {"open": 0, "wip": 0, "done": 0}
        for row in self.conn.execute("SELECT type, status, COUNT(*) as cnt FROM issues GROUP BY type, status").fetchall():
            cat = self._resolve_status_category(row["type"], row["status"])
            by_category[cat] = by_category.get(cat, 0) + row["cnt"]

        return {
            "by_status": by_status,
            "by_category": by_category,
            "by_type": by_type,
            "ready_count": ready_count,
            "blocked_count": blocked_count,
            "total_dependencies": dep_count,
        }

    # -- Bulk import (for migration) -----------------------------------------

    def bulk_insert_issue(self, issue_data: dict[str, Any], *, validate: bool = True) -> None:
        """Insert a pre-formed issue dict directly. For migration use only."""
        if validate:
            self._validate_parent_id(issue_data.get("parent_id"))
        self.conn.execute(
            "INSERT OR IGNORE INTO issues "
            "(id, title, status, priority, type, parent_id, assignee, "
            "created_at, updated_at, closed_at, description, notes, fields) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                issue_data["id"],
                issue_data["title"],
                issue_data.get("status", "open"),
                issue_data.get("priority", 2),
                issue_data.get("type", "task"),
                issue_data.get("parent_id"),
                issue_data.get("assignee", ""),
                issue_data.get("created_at", _now_iso()),
                issue_data.get("updated_at", _now_iso()),
                issue_data.get("closed_at"),
                issue_data.get("description", ""),
                issue_data.get("notes", ""),
                json.dumps(issue_data.get("fields", {})),
            ),
        )

    def bulk_insert_dependency(self, issue_id: str, depends_on_id: str, dep_type: str = "blocks") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, ?, ?)",
            (issue_id, depends_on_id, dep_type, _now_iso()),
        )

    def bulk_insert_event(self, event_data: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, actor, old_value, new_value, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_data["issue_id"],
                event_data["event_type"],
                event_data.get("actor", ""),
                event_data.get("old_value"),
                event_data.get("new_value"),
                event_data.get("comment", ""),
                event_data.get("created_at", _now_iso()),
            ),
        )

    def bulk_commit(self) -> None:
        self.conn.commit()

    # -- Export / Import (JSONL) -----------------------------------------------

    def export_jsonl(self, output_path: str | Path) -> int:
        """Export all issues, dependencies, labels, comments, and events to JSONL.

        Each line is a JSON object with a "type" field indicating the record type.
        Returns the total number of records written.
        """
        count = 0
        with Path(output_path).open("w") as f:
            # Issues
            for row in self.conn.execute("SELECT * FROM issues ORDER BY created_at").fetchall():
                record = dict(row)
                record["_type"] = "issue"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            # Dependencies
            for row in self.conn.execute("SELECT * FROM dependencies ORDER BY issue_id").fetchall():
                record = dict(row)
                record["_type"] = "dependency"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            # Labels
            for row in self.conn.execute("SELECT * FROM labels ORDER BY issue_id").fetchall():
                record = dict(row)
                record["_type"] = "label"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            # Comments
            for row in self.conn.execute("SELECT * FROM comments ORDER BY created_at").fetchall():
                record = dict(row)
                record["_type"] = "comment"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            # Events
            for row in self.conn.execute("SELECT * FROM events ORDER BY created_at").fetchall():
                record = dict(row)
                record["_type"] = "event"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

        return count

    def import_jsonl(self, input_path: str | Path, *, merge: bool = False) -> int:
        """Import issues from JSONL file.

        Args:
            input_path: Path to JSONL file
            merge: If True, skip existing records (OR IGNORE). If False, raise on conflict.

        Returns the number of records imported.
        """
        count = 0
        conflict = "OR IGNORE" if merge else "OR ABORT"

        with Path(input_path).open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record_type = record.pop("_type", None)

                if record_type == "issue":
                    cursor = self.conn.execute(
                        f"INSERT {conflict} INTO issues "
                        "(id, title, status, priority, type, parent_id, assignee, "
                        "created_at, updated_at, closed_at, description, notes, fields) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            record["id"],
                            record["title"],
                            record.get("status", "open"),
                            record.get("priority", 2),
                            record.get("type", "task"),
                            record.get("parent_id"),
                            record.get("assignee", ""),
                            record.get("created_at", _now_iso()),
                            record.get("updated_at", _now_iso()),
                            record.get("closed_at"),
                            record.get("description", ""),
                            record.get("notes", ""),
                            record.get("fields", "{}"),
                        ),
                    )
                elif record_type == "dependency":
                    cursor = self.conn.execute(
                        f"INSERT {conflict} INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, ?, ?)",
                        (
                            record["issue_id"],
                            record["depends_on_id"],
                            record.get("type", "blocks"),
                            record.get("created_at", _now_iso()),
                        ),
                    )
                elif record_type == "label":
                    cursor = self.conn.execute(
                        f"INSERT {conflict} INTO labels (issue_id, label) VALUES (?, ?)",
                        (record["issue_id"], record["label"]),
                    )
                elif record_type == "comment":
                    cursor = self.conn.execute(
                        f"INSERT {conflict} INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
                        (
                            record.get("issue_id", ""),
                            record.get("author", ""),
                            record.get("text", ""),
                            record.get("created_at", _now_iso()),
                        ),
                    )
                elif record_type == "event":
                    cursor = self.conn.execute(
                        f"INSERT {conflict} INTO events "
                        "(issue_id, event_type, actor, old_value, new_value, comment, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            record.get("issue_id", ""),
                            record.get("event_type", ""),
                            record.get("actor", ""),
                            record.get("old_value"),
                            record.get("new_value"),
                            record.get("comment", ""),
                            record.get("created_at", _now_iso()),
                        ),
                    )
                else:
                    continue  # Unknown record type — skip

                count += cursor.rowcount

        self.conn.commit()
        return count
