"""Tests for v1.0 features: archival, compaction, performance, schema migration."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.cli import cli
from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, FiligreeDB, write_config


class TestInvalidStatusRejected:
    """Status validation via templates rejects invalid states."""

    def test_invalid_status_rejected(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        with pytest.raises(ValueError, match="Invalid status"):
            db.update_issue(issue.id, status="nonexistent_state")


# ---------------------------------------------------------------------------
# Schema migration v3: CHECK constraint removed
# ---------------------------------------------------------------------------


class TestMigrationV3:
    def test_custom_status_after_migration(self, db: FiligreeDB) -> None:
        """After v3 migration, custom status values should be accepted by SQLite."""
        # The migration removes the CHECK constraint.
        # Directly insert a row with a custom status to verify.
        db.conn.execute(
            "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at) "
            "VALUES ('test-custom1', 'Custom status', 'review', 2, 'task', '2026-01-01', '2026-01-01')",
        )
        db.conn.commit()
        issue = db.get_issue("test-custom1")
        assert issue.status == "review"

    def test_schema_version_is_current(self, db: FiligreeDB) -> None:
        from filigree.core import CURRENT_SCHEMA_VERSION

        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Archival
# ---------------------------------------------------------------------------


class TestArchival:
    def test_archive_old_closed(self, db: FiligreeDB) -> None:
        issue = db.create_issue("To archive")
        db.close_issue(issue.id)
        # Manually backdate closed_at
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        db.conn.execute("UPDATE issues SET closed_at = ? WHERE id = ?", (old_date, issue.id))
        db.conn.commit()

        archived = db.archive_closed(days_old=30)
        assert issue.id in archived
        refreshed = db.get_issue(issue.id)
        assert refreshed.status == "archived"

    def test_archive_skips_recent(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Recent close")
        db.close_issue(issue.id)
        # closed_at is now — should NOT be archived
        archived = db.archive_closed(days_old=30)
        assert issue.id not in archived

    def test_archive_skips_open(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Still open")
        archived = db.archive_closed(days_old=0)
        assert issue.id not in archived

    def test_archive_records_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Archive event")
        db.close_issue(issue.id)
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        db.conn.execute("UPDATE issues SET closed_at = ? WHERE id = ?", (old_date, issue.id))
        db.conn.commit()

        db.archive_closed(days_old=30, actor="janitor")
        events = db.get_recent_events(limit=10)
        archived_events = [e for e in events if e["event_type"] == "archived"]
        assert len(archived_events) == 1
        assert archived_events[0]["actor"] == "janitor"


# ---------------------------------------------------------------------------
# Event compaction
# ---------------------------------------------------------------------------


class TestCompaction:
    def test_compact_archived_events(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Compact me")
        # Directly insert many events
        for i in range(60):
            db.conn.execute(
                "INSERT INTO events (issue_id, event_type, actor, created_at) VALUES (?, ?, ?, ?)",
                (issue.id, "test_event", "tester", f"2026-01-01T00:{i:02d}:00+00:00"),
            )
        db.conn.commit()
        db.close_issue(issue.id)
        # Backdate and archive
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        db.conn.execute("UPDATE issues SET closed_at = ? WHERE id = ?", (old_date, issue.id))
        db.conn.commit()
        db.archive_closed(days_old=30)

        # Count events before
        before = db.conn.execute("SELECT COUNT(*) as cnt FROM events WHERE issue_id = ?", (issue.id,)).fetchone()["cnt"]
        assert before > 50

        # Compact
        deleted = db.compact_events(keep_recent=10)
        assert deleted > 0

        # Count events after
        after = db.conn.execute("SELECT COUNT(*) as cnt FROM events WHERE issue_id = ?", (issue.id,)).fetchone()["cnt"]
        assert after == 10

    def test_compact_skips_non_archived(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Not archived")
        for i in range(20):
            db.update_issue(issue.id, notes=f"note {i}")
        deleted = db.compact_events(keep_recent=5)
        assert deleted == 0

    def test_vacuum(self, db: FiligreeDB) -> None:
        # Just ensure it doesn't error
        db.vacuum()

    def test_analyze(self, db: FiligreeDB) -> None:
        db.analyze()


# ---------------------------------------------------------------------------
# Performance indexes (v4 migration)
# ---------------------------------------------------------------------------


class TestPerformanceIndexes:
    def test_composite_index_exists(self, db: FiligreeDB) -> None:
        indexes = db.conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_issues_status_priority'").fetchall()
        assert len(indexes) == 1

    def test_deps_covering_index_exists(self, db: FiligreeDB) -> None:
        indexes = db.conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_deps_issue_depends'").fetchall()
        assert len(indexes) == 1

    def test_events_time_index_exists(self, db: FiligreeDB) -> None:
        indexes = db.conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_events_issue_time'").fetchall()
        assert len(indexes) == 1


# ---------------------------------------------------------------------------
# MCP: new v1.0 tools
# ---------------------------------------------------------------------------


class TestMCPV10:
    @pytest.fixture(autouse=True)
    def _setup_mcp(self, tmp_path: Path) -> None:
        import filigree.mcp_server as mcp_mod
        from filigree.core import SUMMARY_FILENAME

        filigree_dir = tmp_path / FILIGREE_DIR_NAME
        filigree_dir.mkdir()
        write_config(filigree_dir, {"prefix": "mcp", "version": 1})
        (filigree_dir / SUMMARY_FILENAME).write_text("# test\n")
        d = FiligreeDB(filigree_dir / DB_FILENAME, prefix="mcp")
        d.initialize()
        mcp_mod.db = d
        mcp_mod._filigree_dir = filigree_dir
        self.db = d

    def _parse(self, result: list) -> dict | str:
        text = result[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    async def test_archive_closed_via_mcp(self) -> None:
        from filigree.mcp_server import call_tool

        issue = self.db.create_issue("Archive via MCP")
        self.db.close_issue(issue.id)
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        self.db.conn.execute("UPDATE issues SET closed_at = ? WHERE id = ?", (old_date, issue.id))
        self.db.conn.commit()

        result = await call_tool("archive_closed", {"days_old": 30})
        data = self._parse(result)
        assert data["status"] == "ok"
        assert data["archived_count"] == 1

    async def test_compact_events_via_mcp(self) -> None:
        from filigree.mcp_server import call_tool

        result = await call_tool("compact_events", {})
        data = self._parse(result)
        assert data["status"] == "ok"
        assert data["events_deleted"] == 0


# ---------------------------------------------------------------------------
# CLI: archive, compact
# ---------------------------------------------------------------------------


class TestCLIArchive:
    def test_archive_via_cli(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            cli_runner.invoke(cli, ["init", "--prefix", "test"])
            cli_runner.invoke(cli, ["create", "Archive CLI test"])
            result = cli_runner.invoke(cli, ["archive", "--days", "0"])
            assert result.exit_code == 0
            # No issues old enough to archive
            assert "No issues" in result.output or "Archived" in result.output
        finally:
            os.chdir(original)
