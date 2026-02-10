# tests/test_migration_v6.py
"""Tests for v5 -> v6 schema migration (parent_id FK with ON DELETE SET NULL)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from filigree.core import FiligreeDB


def _create_v5_database(db_path: Path) -> sqlite3.Connection:
    """Create a v5-schema database with test data including orphaned parent_ids."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # FK enforcement OFF during v5 DB creation to simulate orphaned parent_ids
    conn.execute("PRAGMA foreign_keys=OFF")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS issues (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'open',
            priority    INTEGER NOT NULL DEFAULT 2,
            type        TEXT NOT NULL DEFAULT 'task',
            parent_id   TEXT REFERENCES issues(id),
            assignee    TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            closed_at   TEXT,
            description TEXT DEFAULT '',
            notes       TEXT DEFAULT '',
            fields      TEXT DEFAULT '{}',
            CHECK (priority BETWEEN 0 AND 4)
        );

        CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
        CREATE INDEX IF NOT EXISTS idx_issues_type ON issues(type);
        CREATE INDEX IF NOT EXISTS idx_issues_parent ON issues(parent_id);
        CREATE INDEX IF NOT EXISTS idx_issues_priority ON issues(priority);
        CREATE INDEX IF NOT EXISTS idx_issues_status_priority ON issues(status, priority, created_at);

        CREATE TABLE IF NOT EXISTS dependencies (
            issue_id       TEXT NOT NULL REFERENCES issues(id),
            depends_on_id  TEXT NOT NULL REFERENCES issues(id),
            type           TEXT NOT NULL DEFAULT 'blocks',
            created_at     TEXT NOT NULL,
            PRIMARY KEY (issue_id, depends_on_id)
        );

        CREATE INDEX IF NOT EXISTS idx_deps_depends_on ON dependencies(depends_on_id);
        CREATE INDEX IF NOT EXISTS idx_deps_issue_depends ON dependencies(issue_id, depends_on_id);

        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id   TEXT NOT NULL REFERENCES issues(id),
            event_type TEXT NOT NULL,
            actor      TEXT DEFAULT '',
            old_value  TEXT,
            new_value  TEXT,
            comment    TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_issue ON events(issue_id);
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
        CREATE INDEX IF NOT EXISTS idx_events_issue_time ON events(issue_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id   TEXT NOT NULL REFERENCES issues(id),
            author     TEXT DEFAULT '',
            text       TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_id, created_at);

        CREATE TABLE IF NOT EXISTS labels (
            issue_id TEXT NOT NULL REFERENCES issues(id),
            label    TEXT NOT NULL,
            PRIMARY KEY (issue_id, label)
        );

        CREATE TABLE IF NOT EXISTS type_templates (
            type          TEXT PRIMARY KEY,
            pack          TEXT NOT NULL DEFAULT 'core',
            definition    TEXT NOT NULL,
            is_builtin    BOOLEAN NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS packs (
            name          TEXT PRIMARY KEY,
            version       TEXT NOT NULL,
            definition    TEXT NOT NULL,
            is_builtin    BOOLEAN NOT NULL DEFAULT 0,
            enabled       BOOLEAN NOT NULL DEFAULT 1
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS issues_fts USING fts5(
            title, description, content='issues', content_rowid='rowid'
        );

        CREATE TRIGGER IF NOT EXISTS issues_fts_insert AFTER INSERT ON issues BEGIN
            INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
        END;
        CREATE TRIGGER IF NOT EXISTS issues_fts_update AFTER UPDATE OF title, description ON issues BEGIN
            INSERT INTO issues_fts(issues_fts, rowid, title, description)
                VALUES('delete', old.rowid, old.title, old.description);
            INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
        END;
        CREATE TRIGGER IF NOT EXISTS issues_fts_delete AFTER DELETE ON issues BEGIN
            INSERT INTO issues_fts(issues_fts, rowid, title, description)
                VALUES('delete', old.rowid, old.title, old.description);
        END;
    """)

    now = "2026-02-01T00:00:00+00:00"

    # Parent issue
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test-parent1", "Parent", "open", 2, "epic", now, now),
    )
    # Child with valid parent
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, parent_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("test-child1", "Valid child", "open", 2, "task", "test-parent1", now, now),
    )
    # Child with orphaned parent_id (references non-existent issue)
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, parent_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("test-orphan1", "Orphaned child", "open", 2, "task", "test-deleted999", now, now),
    )
    # Another standalone issue
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at, closed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("test-closed1", "Closed task", "closed", 3, "task", now, now, now),
    )

    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    return conn


class TestMigrationV6Fresh:
    """Test fresh database creation at v6."""

    def test_fresh_db_creates_v6_schema(self, tmp_path: Path) -> None:
        db = FiligreeDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()
        assert db.get_schema_version() == 6
        db.close()

    def test_fresh_db_has_on_delete_set_null(self, tmp_path: Path) -> None:
        """Fresh DB should have ON DELETE SET NULL on parent_id FK."""
        db = FiligreeDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()

        # Create parent and child
        parent = db.create_issue("Parent", type="epic")
        child = db.create_issue("Child", parent_id=parent.id)
        assert child.parent_id == parent.id

        # Delete parent — must also clean up events/deps that reference parent
        db.conn.execute("DELETE FROM events WHERE issue_id = ?", (parent.id,))
        db.conn.execute("DELETE FROM dependencies WHERE issue_id = ? OR depends_on_id = ?", (parent.id, parent.id))
        db.conn.execute("DELETE FROM issues WHERE id = ?", (parent.id,))
        db.conn.commit()

        updated_child = db.get_issue(child.id)
        assert updated_child.parent_id is None

        db.close()


class TestMigrationV5ToV6:
    """Test upgrade from v5 to v6."""

    def test_v5_to_v6_upgrade_succeeds(self, tmp_path: Path) -> None:
        db_path = tmp_path / "upgrade.db"
        v5_conn = _create_v5_database(db_path)
        v5_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()
        assert db.get_schema_version() == 6
        db.close()

    def test_v5_to_v6_preserves_all_issues(self, tmp_path: Path) -> None:
        db_path = tmp_path / "upgrade.db"
        v5_conn = _create_v5_database(db_path)
        v5_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        parent = db.get_issue("test-parent1")
        assert parent.title == "Parent"

        child = db.get_issue("test-child1")
        assert child.title == "Valid child"
        assert child.parent_id == "test-parent1"

        closed = db.get_issue("test-closed1")
        assert closed.status == "closed"

        db.close()

    def test_v5_to_v6_orphaned_parent_ids_nullified(self, tmp_path: Path) -> None:
        """Orphaned parent_ids should be set to NULL during migration."""
        db_path = tmp_path / "upgrade.db"
        v5_conn = _create_v5_database(db_path)
        v5_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        orphan = db.get_issue("test-orphan1")
        assert orphan.parent_id is None, "Orphaned parent_id should be NULL after migration"

        db.close()

    def test_v5_to_v6_fk_enforced_post_migration(self, tmp_path: Path) -> None:
        """After migration, invalid parent_id INSERT should fail."""
        db_path = tmp_path / "upgrade.db"
        v5_conn = _create_v5_database(db_path)
        v5_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        with pytest.raises(ValueError, match="does not reference"):
            db.create_issue("Bad child", parent_id="nonexistent-abc123")

        db.close()

    def test_v5_to_v6_on_delete_set_null(self, tmp_path: Path) -> None:
        """After migration, deleting parent sets child.parent_id to NULL."""
        db_path = tmp_path / "upgrade.db"
        v5_conn = _create_v5_database(db_path)
        v5_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        # Delete the parent — also clean up events/deps referencing it
        db.conn.execute("DELETE FROM events WHERE issue_id = 'test-parent1'")
        db.conn.execute("DELETE FROM dependencies WHERE issue_id = 'test-parent1' OR depends_on_id = 'test-parent1'")
        db.conn.execute("DELETE FROM issues WHERE id = 'test-parent1'")
        db.conn.commit()

        child = db.get_issue("test-child1")
        assert child.parent_id is None

        db.close()

    def test_v5_to_v6_fts_still_works(self, tmp_path: Path) -> None:
        """FTS triggers should still work after table recreation."""
        db_path = tmp_path / "upgrade.db"
        v5_conn = _create_v5_database(db_path)
        v5_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        # Create a new issue and search for it
        issue = db.create_issue("Searchable test issue", description="unique searchable content")
        results = db.search_issues("searchable")
        found_ids = {r.id for r in results}
        assert issue.id in found_ids

        db.close()


class TestMigrationV6Idempotency:
    """Test that v6 migration recovers from partial failure."""

    def test_v6_migration_recovers_from_leftover_temp_table(self, tmp_path: Path) -> None:
        """If issues_v6 exists from a prior partial run, migration should succeed."""
        db_path = tmp_path / "partial.db"
        v5_conn = _create_v5_database(db_path)

        # Simulate a partial v6 migration: create the temp table but don't complete the swap
        v5_conn.execute("""
            CREATE TABLE issues_v6 (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL
            )
        """)
        v5_conn.commit()
        v5_conn.close()

        # Migration should succeed despite the leftover table
        db = FiligreeDB(db_path, prefix="test")
        db.initialize()
        assert db.get_schema_version() == 6

        # Data should be intact
        parent = db.get_issue("test-parent1")
        assert parent.title == "Parent"
        child = db.get_issue("test-child1")
        assert child.parent_id == "test-parent1"

        db.close()


class TestMigrationV6Logging:
    """Test migration logging."""

    def test_migration_logs_at_info_level(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        db_path = tmp_path / "log.db"
        v5_conn = _create_v5_database(db_path)
        v5_conn.close()

        with caplog.at_level(logging.INFO, logger="filigree.core"):
            db = FiligreeDB(db_path, prefix="test")
            db.initialize()
            db.close()

        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("v5" in m and "v6" in m for m in info_messages)
