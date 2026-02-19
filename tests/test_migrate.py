"""Migration round-trip tests — beads → filigree."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from filigree.migrate import migrate_from_beads


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
            "INSERT INTO issues (id, title, estimated_minutes, quality_score, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
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
