# tests/test_migration_v5.py
"""Tests for v4 -> v5 schema migration (workflow templates tables)."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from filigree.core import (
    CURRENT_SCHEMA_VERSION,
    FiligreeDB,
)


def _create_v4_database(db_path: Path) -> sqlite3.Connection:
    """Create a v4-schema database with the old templates table and some data.

    Returns the connection (caller must close).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Create the v4 schema (issues + old templates table + indexes)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS issues (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'open',
            priority    INTEGER NOT NULL DEFAULT 2,
            type        TEXT NOT NULL DEFAULT 'task',
            parent_id   TEXT,
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

        CREATE TABLE IF NOT EXISTS dependencies (
            issue_id       TEXT NOT NULL REFERENCES issues(id),
            depends_on_id  TEXT NOT NULL REFERENCES issues(id),
            type           TEXT NOT NULL DEFAULT 'blocks',
            created_at     TEXT NOT NULL,
            PRIMARY KEY (issue_id, depends_on_id)
        );

        CREATE INDEX IF NOT EXISTS idx_deps_depends_on ON dependencies(depends_on_id);

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

        CREATE TABLE IF NOT EXISTS comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id   TEXT NOT NULL REFERENCES issues(id),
            author     TEXT DEFAULT '',
            text       TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS labels (
            issue_id TEXT NOT NULL REFERENCES issues(id),
            label    TEXT NOT NULL,
            PRIMARY KEY (issue_id, label)
        );

        CREATE TABLE IF NOT EXISTS templates (
            type         TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            description  TEXT DEFAULT '',
            fields_schema TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS issues_fts USING fts5(
            title, description, content='issues', content_rowid='rowid'
        );

        CREATE INDEX IF NOT EXISTS idx_issues_status_priority ON issues(status, priority, created_at);
        CREATE INDEX IF NOT EXISTS idx_deps_issue_depends ON dependencies(issue_id, depends_on_id);
        CREATE INDEX IF NOT EXISTS idx_events_issue_time ON events(issue_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_id, created_at);
    """)

    # Seed old templates (mimics what _seed_templates did in v4)
    old_templates = [
        (
            "task",
            "Task",
            "General-purpose work item",
            json.dumps(
                [
                    {"name": "context", "type": "text"},
                    {"name": "done_definition", "type": "text"},
                ]
            ),
        ),
        (
            "bug",
            "Bug Report",
            "Defects and regressions",
            json.dumps(
                [
                    {"name": "severity", "type": "enum", "options": ["critical", "major", "minor"]},
                    {"name": "component", "type": "text"},
                ]
            ),
        ),
        (
            "epic",
            "Epic",
            "Large body of work",
            json.dumps(
                [
                    {"name": "scope_summary", "type": "text"},
                ]
            ),
        ),
    ]
    for t_type, t_name, t_desc, t_schema in old_templates:
        conn.execute(
            "INSERT OR IGNORE INTO templates (type, display_name, description, fields_schema) VALUES (?, ?, ?, ?)",
            (t_type, t_name, t_desc, t_schema),
        )

    # Insert some issues to verify they survive migration
    now = "2026-02-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test-aaa111", "Existing task", "open", 2, "task", now, now),
    )
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test-bbb222", "In-progress bug", "in_progress", 1, "bug", now, now),
    )
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at, closed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("test-ccc333", "Closed epic", "closed", 3, "epic", now, now, now),
    )

    # Set to v4
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    return conn


class TestMigrationV5Fresh:
    """Test fresh database creation at v5."""

    def test_fresh_db_creates_v5_schema(self, tmp_path: Path) -> None:
        """A brand-new FiligreeDB should create v5 schema directly."""
        db = FiligreeDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()

        assert db.get_schema_version() == 6

        # type_templates table should exist
        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='type_templates'").fetchone()
        assert row is not None

        # packs table should exist
        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='packs'").fetchone()
        assert row is not None

        db.close()

    def test_fresh_db_has_builtin_packs(self, tmp_path: Path) -> None:
        """Fresh DB should have all 9 built-in packs seeded."""
        db = FiligreeDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()

        packs = db.conn.execute("SELECT name, is_builtin, enabled FROM packs ORDER BY name").fetchall()
        pack_names = [p["name"] for p in packs]

        assert "core" in pack_names
        assert "planning" in pack_names
        assert "risk" in pack_names
        assert "spike" in pack_names
        assert len(pack_names) == 9

        for p in packs:
            assert p["is_builtin"] == 1

        enabled = {p["name"] for p in packs if p["enabled"]}
        assert "core" in enabled
        assert "planning" in enabled

        db.close()

    def test_fresh_db_has_builtin_type_templates(self, tmp_path: Path) -> None:
        """Fresh DB should have type templates from enabled packs."""
        db = FiligreeDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()

        templates = db.conn.execute("SELECT type, pack, is_builtin FROM type_templates ORDER BY type").fetchall()
        type_names = [t["type"] for t in templates]

        assert "task" in type_names
        assert "bug" in type_names
        assert "feature" in type_names
        assert "epic" in type_names
        assert "milestone" in type_names
        assert "phase" in type_names
        assert "step" in type_names

        for t in templates:
            assert t["is_builtin"] == 1

        db.close()

    def test_fresh_db_type_templates_has_data(self, tmp_path: Path) -> None:
        """Fresh v5 DB type_templates should have content."""
        db = FiligreeDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()

        assert db.conn.execute("SELECT COUNT(*) FROM type_templates").fetchone()[0] >= 9

        db.close()


class TestMigrationV4ToV5:
    """Test upgrade from v4 to v5."""

    def test_v4_to_v5_upgrade_succeeds(self, tmp_path: Path) -> None:
        """v4 database should upgrade to v5 cleanly."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        assert db.get_schema_version() == 6
        db.close()

    def test_v4_to_v5_creates_new_tables(self, tmp_path: Path) -> None:
        """Migration creates type_templates and packs tables."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='type_templates'").fetchone()
        assert row is not None

        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='packs'").fetchone()
        assert row is not None

        db.close()

    def test_v4_to_v5_backup_created(self, tmp_path: Path) -> None:
        """Migration creates _templates_v4_backup table (review B2)."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_templates_v4_backup'"
        ).fetchone()
        assert row is not None

        backup_count = db.conn.execute("SELECT COUNT(*) FROM _templates_v4_backup").fetchone()[0]
        assert backup_count == 3

        db.close()

    def test_v4_to_v5_old_templates_migrated(self, tmp_path: Path) -> None:
        """Old templates rows should appear in type_templates with enriched definitions."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        row = db.conn.execute("SELECT type, pack, definition FROM type_templates WHERE type = 'task'").fetchone()
        assert row is not None
        defn = json.loads(row["definition"])
        assert "states" in defn
        assert "initial_state" in defn

        db.close()

    def test_v4_to_v5_builtin_packs_seeded(self, tmp_path: Path) -> None:
        """Migration seeds all 9 built-in packs."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        pack_count = db.conn.execute("SELECT COUNT(*) FROM packs WHERE is_builtin = 1").fetchone()[0]
        assert pack_count == 9

        db.close()

    def test_v4_to_v5_issues_untouched(self, tmp_path: Path) -> None:
        """Existing issues must survive migration completely untouched."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        task = db.get_issue("test-aaa111")
        assert task.title == "Existing task"
        assert task.status == "open"
        assert task.type == "task"

        bug = db.get_issue("test-bbb222")
        assert bug.title == "In-progress bug"
        assert bug.status == "in_progress"

        epic = db.get_issue("test-ccc333")
        assert epic.title == "Closed epic"
        assert epic.status == "closed"

        db.close()

    def test_v4_to_v5_old_templates_table_dropped(self, tmp_path: Path) -> None:
        """Old templates table should be dropped after successful migration."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='templates'").fetchone()
        assert row is None  # Old table should be gone

        db.close()

    def test_v4_to_v5_post_migration_validation(self, tmp_path: Path) -> None:
        """Post-migration validation checks row counts."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        template_count = db.conn.execute("SELECT COUNT(*) FROM type_templates").fetchone()[0]
        assert template_count >= 9

        db.close()


class TestMigrationV5FailureRecovery:
    """Test migration failure behavior (review B2)."""

    def test_migration_failure_preserves_backup(self, tmp_path: Path) -> None:
        """If migration fails during seeding, backup table should survive."""
        db_path = tmp_path / "fail.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        with patch("filigree.core._seed_builtin_packs_v5", side_effect=RuntimeError("Simulated seeding failure")):
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            from filigree.core import _migrate_v5_workflow_templates

            with pytest.raises(RuntimeError, match="Simulated seeding failure"):
                _migrate_v5_workflow_templates(conn)

            backup_count = conn.execute("SELECT COUNT(*) FROM _templates_v4_backup").fetchone()[0]
            assert backup_count == 3

            conn.close()

    def test_migration_failure_does_not_drop_old_table(self, tmp_path: Path) -> None:
        """On failure, old templates table should NOT be dropped."""
        db_path = tmp_path / "fail2.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        with patch("filigree.core._seed_builtin_packs_v5", side_effect=RuntimeError("Boom")):
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            from filigree.core import _migrate_v5_workflow_templates

            with pytest.raises(RuntimeError):
                _migrate_v5_workflow_templates(conn)

            row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='templates'").fetchone()
            assert row is not None

            count = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
            assert count == 3

            conn.close()


class TestMigrationV5CustomStates:
    """Test migration preserves custom template states."""

    def test_custom_template_states_in_backup(self, tmp_path: Path) -> None:
        """v4 databases with custom template states should preserve them in backup."""
        db_path = tmp_path / "custom.db"
        v4_conn = _create_v4_database(db_path)

        custom_schema = json.dumps(
            [
                {"name": "review_notes", "type": "text"},
                {"name": "approved_by", "type": "text"},
            ]
        )
        v4_conn.execute(
            "INSERT INTO templates (type, display_name, description, fields_schema) VALUES (?, ?, ?, ?)",
            ("custom_review", "Custom Review", "A custom review type", custom_schema),
        )
        v4_conn.commit()
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        backup_row = db.conn.execute("SELECT * FROM _templates_v4_backup WHERE type = 'custom_review'").fetchone()
        assert backup_row is not None
        assert backup_row["display_name"] == "Custom Review"

        tpl_row = db.conn.execute("SELECT definition FROM type_templates WHERE type = 'custom_review'").fetchone()
        assert tpl_row is not None
        defn = json.loads(tpl_row["definition"])
        assert "states" in defn
        field_names = [f["name"] for f in defn.get("fields_schema", [])]
        assert "review_notes" in field_names
        assert "approved_by" in field_names

        db.close()

    def test_custom_template_assigned_to_custom_pack(self, tmp_path: Path) -> None:
        """Migrated custom templates should be assigned to 'custom' pack."""
        db_path = tmp_path / "custom2.db"
        v4_conn = _create_v4_database(db_path)

        custom_schema = json.dumps([{"name": "my_field", "type": "text"}])
        v4_conn.execute(
            "INSERT INTO templates (type, display_name, description, fields_schema) VALUES (?, ?, ?, ?)",
            ("my_type", "My Type", "Custom", custom_schema),
        )
        v4_conn.commit()
        v4_conn.close()

        db = FiligreeDB(db_path, prefix="test")
        db.initialize()

        row = db.conn.execute("SELECT pack, is_builtin FROM type_templates WHERE type = 'my_type'").fetchone()
        assert row is not None
        assert row["pack"] == "custom"
        assert row["is_builtin"] == 0

        db.close()


class TestMigrationV5Logging:
    """Test migration logging levels (review B4)."""

    def test_migration_logs_at_info_level(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Migration should log start and completion at INFO level."""
        db_path = tmp_path / "log.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        with caplog.at_level(logging.DEBUG, logger="filigree.core"):
            db = FiligreeDB(db_path, prefix="test")
            db.initialize()
            db.close()

        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("v4" in m and "v5" in m for m in info_messages), (
            f"Expected v4->v5 in INFO logs, got: {info_messages}"
        )

        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("backup" in m.lower() for m in debug_messages), (
            f"Expected 'backup' in DEBUG logs, got: {debug_messages}"
        )


class TestSchemaVersion:
    """Basic schema version checks."""

    def test_current_schema_version_is_6(self) -> None:
        assert CURRENT_SCHEMA_VERSION == 6
