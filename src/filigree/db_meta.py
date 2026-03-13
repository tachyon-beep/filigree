"""MetaMixin — comments, labels, stats, bulk operations, and export/import.

All methods access ``self.conn``, ``self.get_issue()``, etc. via
Python's MRO when composed into ``FiligreeDB``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, ClassVar

from filigree.db_base import DBMixinProtocol, _now_iso
from filigree.db_files import VALID_FINDING_STATUSES, VALID_SEVERITIES
from filigree.types.planning import CommentRecord, StatsResult

logger = logging.getLogger(__name__)


class MetaMixin(DBMixinProtocol):
    """Comments, labels, stats, bulk operations, and export/import.

    Inherits ``DBMixinProtocol`` for type-safe access to shared attributes.
    Actual implementations provided by ``FiligreeDB`` at composition time via MRO.
    """

    # -- Comments ------------------------------------------------------------

    def add_comment(self, issue_id: str, text: str, *, author: str = "") -> int:
        if not text or not text.strip():
            msg = "Comment text cannot be empty"
            raise ValueError(msg)
        now = _now_iso()
        try:
            cursor = self.conn.execute(
                "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
                (issue_id, author, text, now),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
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
        return [CommentRecord(id=r["id"], author=r["author"], text=r["text"], created_at=r["created_at"]) for r in rows]

    # -- Labels --------------------------------------------------------------

    def add_label(self, issue_id: str, label: str) -> bool:
        normalized = self._validate_label_name(label)
        try:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO labels (issue_id, label) VALUES (?, ?)",
                (issue_id, normalized),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return cursor.rowcount > 0

    def remove_label(self, issue_id: str, label: str) -> bool:
        try:
            cursor = self.conn.execute(
                "DELETE FROM labels WHERE issue_id = ? AND label = ?",
                (issue_id, label),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
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

    def bulk_insert_issue(self, issue_data: dict[str, Any], *, validate: bool = True) -> bool:
        """Insert a pre-formed issue dict directly. For migration use only.

        Returns True if the row was inserted, False if skipped (duplicate).
        """
        if validate:
            self._validate_parent_id(issue_data.get("parent_id"))
        cursor = self.conn.execute(
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
        inserted = cursor.rowcount > 0
        if not inserted:
            logger.debug("bulk_insert_issue: skipped duplicate id=%s", issue_data.get("id"))
        return inserted

    def bulk_insert_dependency(self, issue_id: str, depends_on_id: str, dep_type: str = "blocks") -> bool:
        """Insert a dependency. Returns True if inserted, False if skipped (duplicate)."""
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, ?, ?)",
            (issue_id, depends_on_id, dep_type, _now_iso()),
        )
        inserted = cursor.rowcount > 0
        if not inserted:
            logger.debug("bulk_insert_dependency: skipped duplicate %s -> %s", issue_id, depends_on_id)
        return inserted

    def bulk_insert_event(self, event_data: dict[str, Any]) -> bool:
        """Insert an event. Returns True if inserted, False if skipped (duplicate)."""
        cursor = self.conn.execute(
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
        inserted = cursor.rowcount > 0
        if not inserted:
            logger.debug("bulk_insert_event: skipped duplicate for issue=%s", event_data.get("issue_id"))
        return inserted

    def bulk_commit(self) -> None:
        self.conn.commit()

    # -- Export / Import (JSONL) -----------------------------------------------

    @staticmethod
    def _issue_fields_json(fields: Any) -> str:
        if isinstance(fields, str):
            # Validate that string is actually valid JSON before passing through
            try:
                obj = json.loads(fields)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid JSON fields string, replacing with empty: %r", fields[:200] if fields else fields)
                return "{}"
            # Ensure the decoded value is a dict (not a bare string/list/number)
            if not isinstance(obj, dict):
                logger.warning("Fields JSON is not a dict, replacing with empty: %r", fields[:80])
                return "{}"
            return fields
        return json.dumps(fields or {})

    @classmethod
    def _is_future_release_record(cls, record: dict[str, Any]) -> bool:
        if record.get("type") != "release":
            return False
        try:
            fields = json.loads(cls._issue_fields_json(record.get("fields", "{}")))
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(fields, dict) and fields.get("version") == "Future"

    def _is_reconcilable_seeded_future(self, issue_id: str) -> bool:
        row = self.conn.execute(
            "SELECT id, title, assignee, description, notes, fields FROM issues WHERE id = ?",
            (issue_id,),
        ).fetchone()
        if row is None:
            return False
        if row["title"] != "Future" or row["assignee"] != "" or row["description"] != "" or row["notes"] != "":
            return False
        try:
            fields = json.loads(row["fields"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(fields, dict) or fields.get("version") != "Future":
            return False

        blockers = [
            ("SELECT 1 FROM issues WHERE parent_id = ? LIMIT 1", (issue_id,)),
            ("SELECT 1 FROM dependencies WHERE issue_id = ? OR depends_on_id = ? LIMIT 1", (issue_id, issue_id)),
            ("SELECT 1 FROM labels WHERE issue_id = ? LIMIT 1", (issue_id,)),
            ("SELECT 1 FROM comments WHERE issue_id = ? LIMIT 1", (issue_id,)),
            ("SELECT 1 FROM events WHERE issue_id = ? LIMIT 1", (issue_id,)),
            ("SELECT 1 FROM file_associations WHERE issue_id = ? LIMIT 1", (issue_id,)),
            ("SELECT 1 FROM scan_findings WHERE issue_id = ? LIMIT 1", (issue_id,)),
        ]
        return not any(self.conn.execute(query, params).fetchone() is not None for query, params in blockers)

    def _reconcile_future_release_import(self, issues: list[dict[str, Any]]) -> None:
        imported_future_ids = [record["id"] for record in issues if self._is_future_release_record(record)]
        if not imported_future_ids:
            return
        if len(imported_future_ids) > 1:
            msg = "Import file contains multiple Future release issues"
            raise ValueError(msg)

        imported_id = imported_future_ids[0]
        existing_ids = [
            row["id"]
            for row in self.conn.execute(
                "SELECT id FROM issues WHERE type = 'release' AND json_extract(fields, '$.version') = 'Future'"
            ).fetchall()
        ]
        conflicting_ids = [issue_id for issue_id in existing_ids if issue_id != imported_id]
        if not conflicting_ids:
            return
        if len(conflicting_ids) == 1 and self._is_reconcilable_seeded_future(conflicting_ids[0]):
            self.conn.execute("DELETE FROM issues WHERE id = ?", (conflicting_ids[0],))
            return

        msg = "Cannot import Future release: tracker already contains a different Future release"
        raise ValueError(msg)

    @staticmethod
    def _json_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value or {})

    def _resolve_imported_file_id(
        self,
        record: dict[str, Any],
        *,
        merge: bool,
        conflict: str,
        file_id_map: dict[str, str],
    ) -> int:
        src_id = record["id"]
        path = record["path"]
        cursor = self.conn.execute(
            f"INSERT {conflict} INTO file_records (id, path, language, file_type, first_seen, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                src_id,
                path,
                record.get("language", ""),
                record.get("file_type", ""),
                record.get("first_seen", _now_iso()),
                record.get("updated_at", _now_iso()),
                self._json_text(record.get("metadata", {})),
            ),
        )
        if cursor.rowcount > 0:
            file_id_map[src_id] = src_id
            return cursor.rowcount

        existing_by_id = self.conn.execute("SELECT id, path FROM file_records WHERE id = ?", (src_id,)).fetchone()
        existing_by_path = self.conn.execute("SELECT id, path FROM file_records WHERE path = ?", (path,)).fetchone()

        if existing_by_id is not None and existing_by_id["path"] != path:
            msg = f"Import conflict for file id {src_id}: existing path {existing_by_id['path']!r} != imported path {path!r}"
            raise ValueError(msg)

        if not merge:
            msg = f"Import conflict for file {path!r}"
            raise sqlite3.IntegrityError(msg)

        if existing_by_path is not None:
            file_id_map[src_id] = existing_by_path["id"]
            return 0

        if existing_by_id is not None:
            file_id_map[src_id] = existing_by_id["id"]
            return 0

        msg = f"Could not reconcile imported file record for path {path!r}"
        raise sqlite3.IntegrityError(msg)

    @staticmethod
    def _remap_file_id(source_file_id: str, file_id_map: dict[str, str]) -> str:
        try:
            return file_id_map[source_file_id]
        except KeyError as exc:
            msg = f"Import references unknown file_id {source_file_id!r}"
            raise ValueError(msg) from exc

    # Table export definitions: (record_type_tag, SQL query)
    _EXPORT_TABLES: ClassVar[list[tuple[str, str]]] = [
        ("issue", "SELECT * FROM issues ORDER BY created_at"),
        ("file_record", "SELECT * FROM file_records ORDER BY path"),
        ("scan_finding", "SELECT * FROM scan_findings ORDER BY first_seen, file_id, scan_source, rule_id"),
        ("dependency", "SELECT * FROM dependencies ORDER BY issue_id"),
        ("label", "SELECT * FROM labels ORDER BY issue_id"),
        ("comment", "SELECT * FROM comments ORDER BY created_at"),
        ("event", "SELECT * FROM events ORDER BY created_at"),
        ("file_association", "SELECT * FROM file_associations ORDER BY created_at, file_id, issue_id"),
        ("file_event", "SELECT * FROM file_events ORDER BY created_at, file_id"),
        ("observation", "SELECT * FROM observations ORDER BY created_at"),
        ("dismissed_observation", "SELECT * FROM dismissed_observations ORDER BY dismissed_at"),
    ]

    def export_jsonl(self, output_path: str | Path) -> int:
        """Export full project data to JSONL.

        Each line is a JSON object with a "_type" field indicating the record type.
        Returns the total number of records written.
        """
        count = 0
        with Path(output_path).open("w") as f:
            for type_tag, query in self._EXPORT_TABLES:
                for row in self.conn.execute(query).fetchall():
                    record = dict(row)
                    record["_type"] = type_tag
                    f.write(json.dumps(record, default=str) + "\n")
                    count += 1
        return count

    def import_jsonl(self, input_path: str | Path, *, merge: bool = False) -> dict[str, Any]:
        """Import full project data from a JSONL file.

        Args:
            input_path: Path to JSONL file
            merge: If True, skip existing records (OR IGNORE). If False, raise on conflict.

        Returns dict with ``count`` (records inserted) and ``skipped_types``
        (mapping of unknown _type values to their occurrence counts, empty if none).
        """
        count = 0
        skipped_types: dict[str, int] = {}
        conflict = "OR IGNORE" if merge else "OR ABORT"
        issues: list[dict[str, Any]] = []
        file_records: list[dict[str, Any]] = []
        scan_findings: list[dict[str, Any]] = []
        dependencies: list[dict[str, Any]] = []
        labels: list[dict[str, Any]] = []
        comments: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        file_associations: list[dict[str, Any]] = []
        file_events: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        dismissed_observations: list[dict[str, Any]] = []

        with Path(input_path).open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    skipped_types["<corrupt_json>"] = skipped_types.get("<corrupt_json>", 0) + 1
                    continue
                record_type = record.pop("_type", None)

                if record_type == "issue":
                    issues.append(record)
                elif record_type == "file_record":
                    file_records.append(record)
                elif record_type == "scan_finding":
                    scan_findings.append(record)
                elif record_type == "dependency":
                    dependencies.append(record)
                elif record_type == "label":
                    labels.append(record)
                elif record_type == "comment":
                    comments.append(record)
                elif record_type == "event":
                    events.append(record)
                elif record_type == "file_association":
                    file_associations.append(record)
                elif record_type == "file_event":
                    file_events.append(record)
                elif record_type == "observation":
                    observations.append(record)
                elif record_type == "dismissed_observation":
                    dismissed_observations.append(record)
                else:
                    skipped_types[record_type or "<missing>"] = skipped_types.get(record_type or "<missing>", 0) + 1

        inserted_issue_ids: set[str] = set()
        parent_map: dict[str, str] = {}
        file_id_map: dict[str, str] = {}
        _import_stage = "setup"
        try:
            self._reconcile_future_release_import(issues)
            _import_stage = "issue"

            for record in issues:
                parent_id = record.get("parent_id")
                fields = self._issue_fields_json(record.get("fields", "{}"))
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
                        None,
                        record.get("assignee", ""),
                        record.get("created_at", _now_iso()),
                        record.get("updated_at", _now_iso()),
                        record.get("closed_at"),
                        record.get("description", ""),
                        record.get("notes", ""),
                        fields,
                    ),
                )
                count += cursor.rowcount
                if cursor.rowcount > 0:
                    inserted_issue_ids.add(record["id"])
                    if parent_id:
                        parent_map[record["id"]] = parent_id

            for issue_id, parent_id in parent_map.items():
                if issue_id in inserted_issue_ids:
                    # Validate parent_id references an existing issue (in DB or just imported)
                    if parent_id not in inserted_issue_ids:
                        exists = self.conn.execute("SELECT 1 FROM issues WHERE id = ?", (parent_id,)).fetchone()
                        if not exists:
                            msg = f"import_jsonl: parent_id {parent_id!r} for issue {issue_id!r} references non-existent issue"
                            raise ValueError(msg)
                    self.conn.execute("UPDATE issues SET parent_id = ? WHERE id = ?", (parent_id, issue_id))

            _import_stage = "file_record"
            for record in file_records:
                count += self._resolve_imported_file_id(record, merge=merge, conflict=conflict, file_id_map=file_id_map)

            _import_stage = "scan_finding"
            for record in scan_findings:
                file_id = self._remap_file_id(record["file_id"], file_id_map)
                severity = record.get("severity", "info")
                finding_status = record.get("status", "open")
                rec_id = record.get("id", "?")
                if severity not in VALID_SEVERITIES:
                    msg = f"Invalid severity {severity!r} in scan_finding {rec_id}, expected one of {sorted(VALID_SEVERITIES)}"
                    raise ValueError(msg)
                if finding_status not in VALID_FINDING_STATUSES:
                    valid = sorted(VALID_FINDING_STATUSES)
                    msg = f"Invalid finding status {finding_status!r} in scan_finding {rec_id}, expected one of {valid}"
                    raise ValueError(msg)
                cursor = self.conn.execute(
                    f"INSERT {conflict} INTO scan_findings "
                    "(id, file_id, issue_id, scan_source, rule_id, severity, status, message, suggestion, scan_run_id, "
                    "line_start, line_end, seen_count, first_seen, updated_at, last_seen_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record["id"],
                        file_id,
                        record.get("issue_id"),
                        record.get("scan_source", ""),
                        record.get("rule_id", ""),
                        severity,
                        finding_status,
                        record.get("message", ""),
                        record.get("suggestion", ""),
                        record.get("scan_run_id", ""),
                        record.get("line_start"),
                        record.get("line_end"),
                        record.get("seen_count", 1),
                        record.get("first_seen", _now_iso()),
                        record.get("updated_at", _now_iso()),
                        record.get("last_seen_at"),
                        self._json_text(record.get("metadata", {})),
                    ),
                )
                count += cursor.rowcount

            _import_stage = "dependency"
            for record in dependencies:
                cursor = self.conn.execute(
                    f"INSERT {conflict} INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, ?, ?)",
                    (
                        record["issue_id"],
                        record["depends_on_id"],
                        record.get("type", "blocks"),
                        record.get("created_at", _now_iso()),
                    ),
                )
                count += cursor.rowcount

            _import_stage = "label"
            for record in labels:
                cursor = self.conn.execute(
                    f"INSERT {conflict} INTO labels (issue_id, label) VALUES (?, ?)",
                    (record["issue_id"], record["label"]),
                )
                count += cursor.rowcount

            _import_stage = "comment"
            for record in comments:
                if merge:
                    created = record.get("created_at", _now_iso())
                    cursor = self.conn.execute(
                        "INSERT INTO comments (issue_id, author, text, created_at) "
                        "SELECT ?, ?, ?, ? "
                        "WHERE NOT EXISTS ("
                        "  SELECT 1 FROM comments WHERE issue_id = ? AND author = ? AND text = ? AND created_at = ?"
                        ")",
                        (
                            record.get("issue_id", ""),
                            record.get("author", ""),
                            record.get("text", ""),
                            created,
                            record.get("issue_id", ""),
                            record.get("author", ""),
                            record.get("text", ""),
                            created,
                        ),
                    )
                else:
                    cursor = self.conn.execute(
                        "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
                        (
                            record.get("issue_id", ""),
                            record.get("author", ""),
                            record.get("text", ""),
                            record.get("created_at", _now_iso()),
                        ),
                    )
                count += cursor.rowcount

            _import_stage = "event"
            for record in events:
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
                count += cursor.rowcount

            _import_stage = "file_association"
            for record in file_associations:
                file_id = self._remap_file_id(record["file_id"], file_id_map)
                cursor = self.conn.execute(
                    f"INSERT {conflict} INTO file_associations (file_id, issue_id, assoc_type, created_at) VALUES (?, ?, ?, ?)",
                    (
                        file_id,
                        record["issue_id"],
                        record.get("assoc_type", "bug_in"),
                        record.get("created_at", _now_iso()),
                    ),
                )
                count += cursor.rowcount

            _import_stage = "file_event"
            for record in file_events:
                file_id = self._remap_file_id(record["file_id"], file_id_map)
                if merge:
                    created = record.get("created_at", _now_iso())
                    cursor = self.conn.execute(
                        "INSERT INTO file_events (file_id, event_type, field, old_value, new_value, created_at) "
                        "SELECT ?, ?, ?, ?, ?, ? "
                        "WHERE NOT EXISTS ("
                        "  SELECT 1 FROM file_events "
                        "  WHERE file_id = ? AND event_type = ? AND field = ? AND old_value = ? AND new_value = ? AND created_at = ?"
                        ")",
                        (
                            file_id,
                            record.get("event_type", "file_metadata_update"),
                            record.get("field", ""),
                            record.get("old_value", ""),
                            record.get("new_value", ""),
                            created,
                            file_id,
                            record.get("event_type", "file_metadata_update"),
                            record.get("field", ""),
                            record.get("old_value", ""),
                            record.get("new_value", ""),
                            created,
                        ),
                    )
                else:
                    cursor = self.conn.execute(
                        "INSERT INTO file_events (file_id, event_type, field, old_value, new_value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            file_id,
                            record.get("event_type", "file_metadata_update"),
                            record.get("field", ""),
                            record.get("old_value", ""),
                            record.get("new_value", ""),
                            record.get("created_at", _now_iso()),
                        ),
                    )
                count += cursor.rowcount

            _import_stage = "observation"
            for record in observations:
                obs_file_id: str | None = record.get("file_id")
                if obs_file_id and obs_file_id in file_id_map:
                    obs_file_id = file_id_map[obs_file_id]
                cursor = self.conn.execute(
                    f"INSERT {conflict} INTO observations "
                    "(id, summary, detail, file_id, file_path, line, source_issue_id, "
                    "priority, actor, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record["id"],
                        record["summary"],
                        record.get("detail", ""),
                        obs_file_id,
                        record.get("file_path", ""),
                        record.get("line"),
                        record.get("source_issue_id", ""),
                        record.get("priority", 3),
                        record.get("actor", ""),
                        record.get("created_at", _now_iso()),
                        record.get("expires_at", _now_iso()),
                    ),
                )
                count += cursor.rowcount

            _import_stage = "dismissed_observation"
            for record in dismissed_observations:
                cursor = self.conn.execute(
                    f"INSERT {conflict} INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        record["obs_id"],
                        record["summary"],
                        record.get("actor", ""),
                        record.get("reason", ""),
                        record.get("dismissed_at", _now_iso()),
                    ),
                )
                count += cursor.rowcount
        except KeyError as exc:
            self.conn.rollback()
            msg = f"Missing required field {exc} in {_import_stage} record"
            raise ValueError(msg) from exc
        except Exception:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

        if skipped_types:
            for rtype, rcount in skipped_types.items():
                logger.warning("import_jsonl: skipped %d record(s) with unknown type %r", rcount, rtype)

        return {"count": count, "skipped_types": dict(skipped_types)}
