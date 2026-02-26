"""Migration round-trip tests — beads → filigree."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from filigree.migrate import migrate_from_beads
from filigree.migrations import rebuild_table


class TestMigration:
    def test_basic_migration(self, beads_db: Path, db: FiligreeDB) -> None:
        count = migrate_from_beads(beads_db, db)
        # 4 non-deleted issues in fixture (bd-aaa111, bd-bbb222, bd-ccc333, bd-ddd444)
        assert count == 4

    def test_deleted_issue_excluded(self, beads_db: Path, db: FiligreeDB) -> None:
        migrate_from_beads(beads_db, db)
        with pytest.raises(KeyError):
            db.get_issue("bd-del999")

    def test_issue_fields_mapped(self, beads_db: Path, db: FiligreeDB) -> None:
        migrate_from_beads(beads_db, db)
        bug = db.get_issue("bd-ccc333")
        assert bug.title == "Closed bug"
        assert bug.status == "closed"
        assert bug.priority == 0
        assert bug.type == "bug"
        assert bug.assignee == "bob"
        assert bug.fields.get("design") == "fix the thing"

    def test_metadata_preserved(self, beads_db: Path, db: FiligreeDB) -> None:
        migrate_from_beads(beads_db, db)
        bug = db.get_issue("bd-ccc333")
        assert bug.fields.get("_beads_metadata") == {"source": "import"}

    def test_parent_id_from_parent_id_column(self, beads_db: Path, db: FiligreeDB) -> None:
        """Issues with parent_id set should preserve the hierarchy."""
        migrate_from_beads(beads_db, db)
        bug = db.get_issue("bd-ccc333")
        assert bug.parent_id == "bd-aaa111"

    def test_parent_id_from_parent_epic(self, beads_db: Path, db: FiligreeDB) -> None:
        """Issues with parent_epic (but no parent_id) should use parent_epic."""
        migrate_from_beads(beads_db, db)
        task = db.get_issue("bd-bbb222")
        assert task.parent_id == "bd-aaa111"

    def test_dependency_migration(self, beads_db: Path, db: FiligreeDB) -> None:
        migrate_from_beads(beads_db, db)
        task = db.get_issue("bd-bbb222")
        # bd-ccc333 is closed, so it should NOT appear in blocked_by (only open blockers shown)
        assert "bd-ccc333" not in task.blocked_by
        # But the dependency row still exists in the DB
        deps = db.conn.execute("SELECT depends_on_id FROM dependencies WHERE issue_id = ?", (task.id,)).fetchall()
        assert any(r["depends_on_id"] == "bd-ccc333" for r in deps)

    def test_dangling_dependency_filtered(self, beads_db: Path, db: FiligreeDB) -> None:
        """Dependency referencing deleted issue should be skipped."""
        migrate_from_beads(beads_db, db)
        task = db.get_issue("bd-bbb222")
        # bd-del999 was deleted, so that dep should be filtered
        assert "bd-del999" not in task.blocked_by

    def test_events_migrated(self, beads_db: Path, db: FiligreeDB) -> None:
        migrate_from_beads(beads_db, db)
        events = db.get_recent_events(limit=50)
        # Should have at least the one event from fixture
        assert any(e["issue_id"] == "bd-aaa111" and e["event_type"] == "created" for e in events)

    def test_labels_migrated(self, beads_db: Path, db: FiligreeDB) -> None:
        migrate_from_beads(beads_db, db)
        epic = db.get_issue("bd-aaa111")
        assert "important" in epic.labels
        task = db.get_issue("bd-bbb222")
        assert "backend" in task.labels

    def test_comments_migrated(self, beads_db: Path, db: FiligreeDB) -> None:
        migrate_from_beads(beads_db, db)
        comments = db.get_comments("bd-bbb222")
        assert len(comments) == 1
        assert comments[0]["text"] == "working on this"
        assert comments[0]["author"] == "alice"

    def test_unknown_status_normalized(self, beads_db: Path, db: FiligreeDB) -> None:
        migrate_from_beads(beads_db, db)
        weird = db.get_issue("bd-ddd444")
        assert weird.status == "open"  # "review" → "open"


class TestCommentDedupPreservesRepeats:
    """Bug filigree-581584: same-text comments at different times must both survive."""

    def test_repeated_comment_different_times_both_migrate(self, tmp_path: Path, db: FiligreeDB) -> None:
        db_path = tmp_path / "comments_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
                parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
                description TEXT DEFAULT '', notes TEXT DEFAULT '',
                metadata TEXT DEFAULT 'null',
                design TEXT DEFAULT '', acceptance_criteria TEXT DEFAULT '',
                estimated_minutes INTEGER DEFAULT 0, close_reason TEXT DEFAULT '',
                external_ref TEXT DEFAULT '', mol_type TEXT DEFAULT '',
                work_type TEXT DEFAULT '', quality_score TEXT DEFAULT '',
                source_system TEXT DEFAULT '', event_kind TEXT DEFAULT '',
                actor TEXT DEFAULT '', target TEXT DEFAULT '',
                payload TEXT DEFAULT '', source_repo TEXT DEFAULT '',
                await_type TEXT DEFAULT '', await_id TEXT DEFAULT '',
                role_type TEXT DEFAULT '', rig TEXT DEFAULT '',
                spec_id TEXT DEFAULT '', wisp_type TEXT DEFAULT '',
                sender TEXT DEFAULT ''
            );
            CREATE TABLE dependencies (
                issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
                PRIMARY KEY (issue_id, depends_on_id)
            );
            CREATE TABLE comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL, author TEXT DEFAULT '',
                text TEXT NOT NULL, created_at TEXT
            );
        """)
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("iss-1", "Test issue", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        # Two identical-text comments at different times
        conn.execute(
            "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
            ("iss-1", "alice", "LGTM", "2026-01-01T10:00:00"),
        )
        conn.execute(
            "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
            ("iss-1", "alice", "LGTM", "2026-01-01T14:00:00"),
        )
        conn.commit()
        conn.close()

        migrate_from_beads(db_path, db)
        comments = db.get_comments("iss-1")
        assert len(comments) == 2, f"Expected 2 comments, got {len(comments)}"


class TestMigrationPreservesZeroValues:
    """Bug filigree-edbce1: numeric zero values must survive migration."""

    def test_zero_estimated_minutes_preserved(self, tmp_path: Path, db: FiligreeDB) -> None:
        db_path = tmp_path / "zero_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
                parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
                description TEXT DEFAULT '', notes TEXT DEFAULT '',
                metadata TEXT DEFAULT 'null',
                design TEXT DEFAULT '', acceptance_criteria TEXT DEFAULT '',
                estimated_minutes INTEGER DEFAULT 0, close_reason TEXT DEFAULT '',
                external_ref TEXT DEFAULT '', mol_type TEXT DEFAULT '',
                work_type TEXT DEFAULT '', quality_score TEXT DEFAULT '',
                source_system TEXT DEFAULT '', event_kind TEXT DEFAULT '',
                actor TEXT DEFAULT '', target TEXT DEFAULT '',
                payload TEXT DEFAULT '', source_repo TEXT DEFAULT '',
                await_type TEXT DEFAULT '', await_id TEXT DEFAULT '',
                role_type TEXT DEFAULT '', rig TEXT DEFAULT '',
                spec_id TEXT DEFAULT '', wisp_type TEXT DEFAULT '',
                sender TEXT DEFAULT ''
            );
            CREATE TABLE dependencies (
                issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
                PRIMARY KEY (issue_id, depends_on_id)
            );
        """)
        conn.execute(
            "INSERT INTO issues (id, title, estimated_minutes, quality_score, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("iss-z", "Zero fields", 0, "0", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        migrate_from_beads(db_path, db)
        issue = db.get_issue("iss-z")
        assert issue.fields.get("estimated_minutes") == 0, "Zero estimated_minutes was dropped"
        assert issue.fields.get("quality_score") == "0", "Zero quality_score was dropped"


class TestMigrationRerunCount:
    def test_rerun_returns_zero_for_already_migrated(self, beads_db: Path, db: FiligreeDB) -> None:
        """Re-running migration should report 0 (not re-count existing rows)."""
        count1 = migrate_from_beads(beads_db, db)
        assert count1 == 4
        count2 = migrate_from_beads(beads_db, db)
        assert count2 == 0

    def test_rerun_does_not_overwrite_parent_id(self, beads_db: Path, db: FiligreeDB) -> None:
        """Re-running migration must not overwrite parent_id changes made after initial migration."""
        migrate_from_beads(beads_db, db)
        # bd-ccc333 was migrated with parent_id="bd-aaa111"
        assert db.get_issue("bd-ccc333").parent_id == "bd-aaa111"

        # User manually re-parents the issue after migration
        db.update_issue("bd-ccc333", parent_id="bd-bbb222")
        assert db.get_issue("bd-ccc333").parent_id == "bd-bbb222"

        # Re-run migration — parent_id must NOT revert to bd-aaa111
        count = migrate_from_beads(beads_db, db)
        assert count == 0
        assert db.get_issue("bd-ccc333").parent_id == "bd-bbb222"


class TestMigrationParentOrdering:
    def test_child_before_parent_does_not_cause_fk_error(self, tmp_path: Path, db: FiligreeDB) -> None:
        """Migration must handle child rows appearing before parent rows."""
        db_path = tmp_path / "unordered_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
                parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
                description TEXT DEFAULT '', notes TEXT DEFAULT '',
                metadata TEXT DEFAULT 'null',
                design TEXT DEFAULT '', acceptance_criteria TEXT DEFAULT '',
                estimated_minutes INTEGER DEFAULT 0, close_reason TEXT DEFAULT '',
                external_ref TEXT DEFAULT '', mol_type TEXT DEFAULT '',
                work_type TEXT DEFAULT '', quality_score TEXT DEFAULT '',
                source_system TEXT DEFAULT '', event_kind TEXT DEFAULT '',
                actor TEXT DEFAULT '', target TEXT DEFAULT '',
                payload TEXT DEFAULT '', source_repo TEXT DEFAULT '',
                await_type TEXT DEFAULT '', await_id TEXT DEFAULT '',
                role_type TEXT DEFAULT '', rig TEXT DEFAULT '',
                spec_id TEXT DEFAULT '', wisp_type TEXT DEFAULT '',
                sender TEXT DEFAULT ''
            );
            CREATE TABLE dependencies (
                issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
                PRIMARY KEY (issue_id, depends_on_id)
            );
        """)
        now = "2026-01-01T00:00:00+00:00"
        # Insert CHILD first, then parent — triggers FK error if not handled
        conn.execute(
            "INSERT INTO issues (id, title, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("bd-child", "Child task", "bd-parent", now, now),
        )
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("bd-parent", "Parent epic", now, now),
        )
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 2
        child = db.get_issue("bd-child")
        assert child.parent_id == "bd-parent"


class TestMigrationAtomicity:
    def test_failure_rolls_back_partial_writes(self, tmp_path: Path, db: FiligreeDB) -> None:
        """If migration fails partway, no partial data should be committed."""
        db_path = tmp_path / "bad_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
                parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
                description TEXT DEFAULT '', notes TEXT DEFAULT '',
                metadata TEXT DEFAULT 'null',
                design TEXT DEFAULT '', acceptance_criteria TEXT DEFAULT '',
                estimated_minutes INTEGER DEFAULT 0, close_reason TEXT DEFAULT '',
                external_ref TEXT DEFAULT '', mol_type TEXT DEFAULT '',
                work_type TEXT DEFAULT '', quality_score TEXT DEFAULT '',
                source_system TEXT DEFAULT '', event_kind TEXT DEFAULT '',
                actor TEXT DEFAULT '', target TEXT DEFAULT '',
                payload TEXT DEFAULT '', source_repo TEXT DEFAULT '',
                await_type TEXT DEFAULT '', await_id TEXT DEFAULT '',
                role_type TEXT DEFAULT '', rig TEXT DEFAULT '',
                spec_id TEXT DEFAULT '', wisp_type TEXT DEFAULT '',
                sender TEXT DEFAULT ''
            );
            CREATE TABLE dependencies (
                issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
                PRIMARY KEY (issue_id, depends_on_id)
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL,
                event_type TEXT, actor TEXT DEFAULT '',
                old_value TEXT, new_value TEXT,
                comment TEXT DEFAULT '', created_at TEXT
            );
        """)
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("bd-ok1", "Good issue", now, now),
        )
        # Insert a dependency referencing a non-existent issue to cause
        # an error when filigree tries to record a dep event
        conn.execute(
            "INSERT INTO dependencies (issue_id, depends_on_id) VALUES (?, ?)",
            ("bd-ok1", "bd-nonexistent"),
        )
        conn.commit()
        conn.close()

        # Migration will insert the issue, then move on to deps.
        # The dep references bd-nonexistent which is not in migrated_ids,
        # so it gets filtered. We need a different failure trigger.
        # Use a mock to force an error mid-migration instead.
        from unittest.mock import patch

        original_bulk_commit = db.bulk_commit

        def fail_on_commit() -> None:
            raise sqlite3.IntegrityError("simulated failure")

        with (
            patch.object(db, "bulk_commit", side_effect=fail_on_commit),
            pytest.raises(sqlite3.IntegrityError, match="simulated failure"),
        ):
            migrate_from_beads(db_path, db)

        # After failure + rollback, the issue should NOT be visible
        original_bulk_commit()  # commit anything that might have leaked
        issues = db.list_issues(limit=10000)
        assert not any(i.id == "bd-ok1" for i in issues)


class TestMigrationPriorityCoercion:
    """Bug filigree-fa9ddf: non-numeric priority must not crash migration."""

    _BEADS_SCHEMA = """
        CREATE TABLE issues (
            id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
            priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
            parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
            created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
            description TEXT DEFAULT '', notes TEXT DEFAULT '',
            metadata TEXT DEFAULT 'null',
            design TEXT DEFAULT '', acceptance_criteria TEXT DEFAULT '',
            estimated_minutes INTEGER DEFAULT 0, close_reason TEXT DEFAULT '',
            external_ref TEXT DEFAULT '', mol_type TEXT DEFAULT '',
            work_type TEXT DEFAULT '', quality_score TEXT DEFAULT '',
            source_system TEXT DEFAULT '', event_kind TEXT DEFAULT '',
            actor TEXT DEFAULT '', target TEXT DEFAULT '',
            payload TEXT DEFAULT '', source_repo TEXT DEFAULT '',
            await_type TEXT DEFAULT '', await_id TEXT DEFAULT '',
            role_type TEXT DEFAULT '', rig TEXT DEFAULT '',
            spec_id TEXT DEFAULT '', wisp_type TEXT DEFAULT '',
            sender TEXT DEFAULT ''
        );
        CREATE TABLE dependencies (
            issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
            PRIMARY KEY (issue_id, depends_on_id)
        );
    """

    def test_string_priority_falls_back_to_default(self, tmp_path: Path, db: FiligreeDB) -> None:
        db_path = tmp_path / "priority_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._BEADS_SCHEMA)
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO issues (id, title, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("iss-str", "String priority", "high", now, now),
        )
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 1
        issue = db.get_issue("iss-str")
        assert issue.priority == 2  # default fallback

    def test_float_string_priority_coerced(self, tmp_path: Path, db: FiligreeDB) -> None:
        db_path = tmp_path / "priority_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._BEADS_SCHEMA)
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO issues (id, title, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("iss-flt", "Float priority", "1.7", now, now),
        )
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 1
        issue = db.get_issue("iss-flt")
        assert issue.priority == 1  # truncated to int, clamped

    def test_out_of_range_priority_clamped(self, tmp_path: Path, db: FiligreeDB) -> None:
        db_path = tmp_path / "priority_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._BEADS_SCHEMA)
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO issues (id, title, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("iss-big", "Big priority", 99, now, now),
        )
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 1
        assert db.get_issue("iss-big").priority == 4  # clamped to max


class TestMigrationTimestampNormalization:
    """Bug filigree-40fe9c: missing timestamps must not be written as empty strings."""

    _BEADS_SCHEMA = """
        CREATE TABLE issues (
            id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
            priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
            parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
            created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
            description TEXT DEFAULT '', notes TEXT DEFAULT '',
            metadata TEXT DEFAULT 'null',
            design TEXT DEFAULT '', acceptance_criteria TEXT DEFAULT '',
            estimated_minutes INTEGER DEFAULT 0, close_reason TEXT DEFAULT '',
            external_ref TEXT DEFAULT '', mol_type TEXT DEFAULT '',
            work_type TEXT DEFAULT '', quality_score TEXT DEFAULT '',
            source_system TEXT DEFAULT '', event_kind TEXT DEFAULT '',
            actor TEXT DEFAULT '', target TEXT DEFAULT '',
            payload TEXT DEFAULT '', source_repo TEXT DEFAULT '',
            await_type TEXT DEFAULT '', await_id TEXT DEFAULT '',
            role_type TEXT DEFAULT '', rig TEXT DEFAULT '',
            spec_id TEXT DEFAULT '', wisp_type TEXT DEFAULT '',
            sender TEXT DEFAULT ''
        );
        CREATE TABLE dependencies (
            issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
            PRIMARY KEY (issue_id, depends_on_id)
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id TEXT NOT NULL, event_type TEXT, actor TEXT DEFAULT '',
            old_value TEXT, new_value TEXT,
            comment TEXT DEFAULT '', created_at TEXT
        );
        CREATE TABLE comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id TEXT NOT NULL, author TEXT DEFAULT '',
            text TEXT NOT NULL, created_at TEXT
        );
    """

    def test_null_created_at_gets_valid_timestamp(self, tmp_path: Path, db: FiligreeDB) -> None:
        db_path = tmp_path / "ts_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._BEADS_SCHEMA)
        # Insert with NULL timestamps (created_at and updated_at both NULL)
        conn.execute(
            "INSERT INTO issues (id, title) VALUES (?, ?)",
            ("iss-null", "Null timestamps"),
        )
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 1
        row = db.conn.execute("SELECT created_at, updated_at FROM issues WHERE id = ?", ("iss-null",)).fetchone()
        # Must NOT be empty string
        assert row["created_at"] != ""
        assert row["updated_at"] != ""
        # Must be valid ISO-8601 (parseable)
        from datetime import datetime

        datetime.fromisoformat(row["created_at"])
        datetime.fromisoformat(row["updated_at"])

    def test_empty_string_created_at_gets_valid_timestamp(self, tmp_path: Path, db: FiligreeDB) -> None:
        db_path = tmp_path / "ts_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._BEADS_SCHEMA)
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("iss-empty", "Empty timestamps", "", ""),
        )
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 1
        row = db.conn.execute("SELECT created_at, updated_at FROM issues WHERE id = ?", ("iss-empty",)).fetchone()
        assert row["created_at"] != ""
        assert row["updated_at"] != ""
        from datetime import datetime

        datetime.fromisoformat(row["created_at"])
        datetime.fromisoformat(row["updated_at"])

    def test_event_null_timestamp_gets_valid_fallback(self, tmp_path: Path, db: FiligreeDB) -> None:
        db_path = tmp_path / "ts_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._BEADS_SCHEMA)
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("iss-ev", "Has events", now, now),
        )
        # Event with NULL created_at
        conn.execute(
            "INSERT INTO events (issue_id, event_type, created_at) VALUES (?, ?, ?)",
            ("iss-ev", "created", None),
        )
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 1
        events = db.conn.execute("SELECT created_at FROM events WHERE issue_id = ?", ("iss-ev",)).fetchall()
        for evt in events:
            assert evt["created_at"] != ""
            from datetime import datetime

            datetime.fromisoformat(evt["created_at"])

    def test_comment_null_timestamp_gets_valid_fallback(self, tmp_path: Path, db: FiligreeDB) -> None:
        db_path = tmp_path / "ts_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._BEADS_SCHEMA)
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("iss-cmt", "Has comments", now, now),
        )
        conn.execute(
            "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
            ("iss-cmt", "alice", "no timestamp", None),
        )
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 1
        comments = db.get_comments("iss-cmt")
        assert len(comments) == 1
        assert comments[0]["created_at"] != ""
        from datetime import datetime

        datetime.fromisoformat(comments[0]["created_at"])

    def test_valid_timestamps_preserved(self, tmp_path: Path, db: FiligreeDB) -> None:
        """Existing valid timestamps must pass through unchanged."""
        db_path = tmp_path / "ts_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._BEADS_SCHEMA)
        ts = "2026-01-15T10:30:00+00:00"
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("iss-ok", "Good timestamps", ts, ts),
        )
        conn.commit()
        conn.close()

        migrate_from_beads(db_path, db)
        row = db.conn.execute("SELECT created_at, updated_at FROM issues WHERE id = ?", ("iss-ok",)).fetchone()
        assert row["created_at"] == ts
        assert row["updated_at"] == ts


class TestMigrationEdgeCases:
    def test_missing_events_table(self, tmp_path: Path, db: FiligreeDB) -> None:
        """Beads DB without events table should not crash."""
        db_path = tmp_path / "bare_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
                parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
                description TEXT DEFAULT '', notes TEXT DEFAULT '',
                metadata TEXT DEFAULT 'null',
                design TEXT DEFAULT '', acceptance_criteria TEXT DEFAULT '',
                estimated_minutes INTEGER DEFAULT 0, close_reason TEXT DEFAULT '',
                external_ref TEXT DEFAULT '', mol_type TEXT DEFAULT '',
                work_type TEXT DEFAULT '', quality_score TEXT DEFAULT '',
                source_system TEXT DEFAULT '', event_kind TEXT DEFAULT '',
                actor TEXT DEFAULT '', target TEXT DEFAULT '',
                payload TEXT DEFAULT '', source_repo TEXT DEFAULT '',
                await_type TEXT DEFAULT '', await_id TEXT DEFAULT '',
                role_type TEXT DEFAULT '', rig TEXT DEFAULT '',
                spec_id TEXT DEFAULT '', wisp_type TEXT DEFAULT '',
                sender TEXT DEFAULT ''
            );
            CREATE TABLE dependencies (
                issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
                PRIMARY KEY (issue_id, depends_on_id)
            );
        """)
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("bd-test01", "Test", now, now),
        )
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 1

    def test_empty_beads_db(self, tmp_path: Path, db: FiligreeDB) -> None:
        """Empty beads DB should return 0."""
        db_path = tmp_path / "empty_beads.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
                parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
                description TEXT DEFAULT '', notes TEXT DEFAULT '',
                metadata TEXT DEFAULT 'null',
                design TEXT DEFAULT '', acceptance_criteria TEXT DEFAULT '',
                estimated_minutes INTEGER DEFAULT 0, close_reason TEXT DEFAULT '',
                external_ref TEXT DEFAULT '', mol_type TEXT DEFAULT '',
                work_type TEXT DEFAULT '', quality_score TEXT DEFAULT '',
                source_system TEXT DEFAULT '', event_kind TEXT DEFAULT '',
                actor TEXT DEFAULT '', target TEXT DEFAULT '',
                payload TEXT DEFAULT '', source_repo TEXT DEFAULT '',
                await_type TEXT DEFAULT '', await_id TEXT DEFAULT '',
                role_type TEXT DEFAULT '', rig TEXT DEFAULT '',
                spec_id TEXT DEFAULT '', wisp_type TEXT DEFAULT '',
                sender TEXT DEFAULT ''
            );
            CREATE TABLE dependencies (
                issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
                PRIMARY KEY (issue_id, depends_on_id)
            );
        """)
        conn.commit()
        conn.close()

        count = migrate_from_beads(db_path, db)
        assert count == 0


# ===========================================================================
# rebuild_table edge cases (from test_minor_fixes.py)
# ===========================================================================


class TestRebuildTableNoSharedColumns:
    """rebuild_table should raise ValueError when schemas share zero columns."""

    def test_no_shared_columns_raises(self) -> None:
        """When old and new schemas have no columns in common, raise ValueError."""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE test_tbl (alpha TEXT, beta INTEGER)")
        conn.execute("INSERT INTO test_tbl VALUES ('hello', 42)")

        new_schema = "CREATE TABLE test_tbl (gamma TEXT, delta REAL)"

        with pytest.raises(ValueError, match="No shared columns"):
            rebuild_table(conn, "test_tbl", new_schema)

        conn.close()

    def test_shared_columns_succeeds(self) -> None:
        """When schemas share columns, rebuild_table should work normally."""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE test_tbl (id TEXT, name TEXT, old_col INTEGER)")
        conn.execute("INSERT INTO test_tbl VALUES ('1', 'test', 99)")

        new_schema = "CREATE TABLE test_tbl (id TEXT, name TEXT, new_col REAL DEFAULT 0.0)"
        rebuild_table(conn, "test_tbl", new_schema)

        rows = conn.execute("SELECT id, name FROM test_tbl").fetchall()
        assert len(rows) == 1
        assert rows[0] == ("1", "test")

        conn.close()


# ===========================================================================
# Migration robustness (from test_peripheral_fixes.py)
# Covers: idempotent comments, connection safety, targeted exceptions
# ===========================================================================


class TestMigrateIdempotency:
    def test_no_duplicate_comments_on_remigration(self, beads_db: Path, db: FiligreeDB) -> None:
        """Running migration twice must not create duplicate comments."""
        migrate_from_beads(beads_db, db)
        comments_first = db.get_comments("bd-bbb222")
        assert len(comments_first) == 1

        # Run migration again — comments should be deduped
        migrate_from_beads(beads_db, db)
        comments_second = db.get_comments("bd-bbb222")
        assert len(comments_second) == 1
        assert comments_second[0]["text"] == "working on this"

    def test_different_comments_not_suppressed(self, tmp_path: Path, db: FiligreeDB) -> None:
        """Dedup should only match on (issue_id, text, author), not suppress distinct comments."""
        db_path = tmp_path / "beads_multi_comment.db"
        conn = sqlite3.connect(str(db_path))
        now = "2026-01-15T10:00:00+00:00"
        conn.executescript("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
                parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
                description TEXT DEFAULT '', notes TEXT DEFAULT '',
                metadata TEXT DEFAULT 'null'
            );
            CREATE TABLE dependencies (
                issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
                PRIMARY KEY (issue_id, depends_on_id)
            );
            CREATE TABLE comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL, author TEXT DEFAULT '',
                text TEXT NOT NULL, created_at TEXT
            );
        """)
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("bd-x01", "Test issue", now, now),
        )
        conn.execute(
            "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
            ("bd-x01", "alice", "comment one", now),
        )
        conn.execute(
            "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
            ("bd-x01", "alice", "comment two", now),
        )
        conn.commit()
        conn.close()

        migrate_from_beads(db_path, db)
        comments = db.get_comments("bd-x01")
        assert len(comments) == 2
        texts = {c["text"] for c in comments}
        assert texts == {"comment one", "comment two"}


class TestMigrateConnectionSafety:
    def test_connection_closed_on_error(self, tmp_path: Path, db: FiligreeDB) -> None:
        """If an error occurs during migration, the beads connection must still be closed."""
        db_path = tmp_path / "bad_beads.db"
        conn = sqlite3.connect(str(db_path))
        # Create a DB that will cause an error during issue migration
        # (missing required columns like 'metadata' causes IndexError)
        conn.execute("CREATE TABLE issues (id TEXT PRIMARY KEY, deleted_at TEXT)")
        conn.execute("INSERT INTO issues (id) VALUES ('bd-broken')")
        conn.commit()
        conn.close()

        with pytest.raises((sqlite3.OperationalError, IndexError)):
            migrate_from_beads(db_path, db)

        # The connection should be closed — verify by opening the file again
        verify_conn = sqlite3.connect(str(db_path))
        verify_conn.execute("SELECT * FROM issues")
        verify_conn.close()


class TestMigrateTargetedExceptions:
    def test_missing_events_table_graceful(self, tmp_path: Path, db: FiligreeDB) -> None:
        """Missing events table should be handled gracefully."""
        db_path = tmp_path / "no_events_beads.db"
        conn = sqlite3.connect(str(db_path))
        now = "2026-01-01T00:00:00+00:00"
        conn.executescript("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
                parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
                description TEXT DEFAULT '', notes TEXT DEFAULT '',
                metadata TEXT DEFAULT 'null'
            );
            CREATE TABLE dependencies (
                issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
                PRIMARY KEY (issue_id, depends_on_id)
            );
        """)
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("bd-test01", "Test", now, now),
        )
        conn.commit()
        conn.close()

        # Should not raise — missing events/labels/comments tables are expected
        count = migrate_from_beads(db_path, db)
        assert count == 1

    def test_unexpected_sqlite_error_propagates(self, tmp_path: Path, db: FiligreeDB) -> None:
        """Non-'missing table' SQLite errors should NOT be silently suppressed."""
        db_path = tmp_path / "corrupt_beads.db"
        conn = sqlite3.connect(str(db_path))
        now = "2026-01-01T00:00:00+00:00"
        conn.executescript("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2, issue_type TEXT DEFAULT 'task',
                parent_id TEXT, parent_epic TEXT, assignee TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT, closed_at TEXT, deleted_at TEXT,
                description TEXT DEFAULT '', notes TEXT DEFAULT '',
                metadata TEXT DEFAULT 'null'
            );
            CREATE TABLE dependencies (
                issue_id TEXT, depends_on_id TEXT, type TEXT DEFAULT 'blocks',
                PRIMARY KEY (issue_id, depends_on_id)
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO issues (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("bd-test01", "Test", now, now),
        )
        conn.commit()
        conn.close()

        # The events table exists but lacks the columns we query, so this
        # should raise an OperationalError that is NOT "no such table"
        with pytest.raises(sqlite3.OperationalError):
            migrate_from_beads(db_path, db)
