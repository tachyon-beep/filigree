"""Tests for peripheral module robustness fixes.

Covers:
- migrate.py: idempotent comments, connection safety, targeted exceptions
- install.py: TOML backslash escaping, proper presence check, malformed .mcp.json
- __init__.py: PackageNotFoundError handling
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from filigree.core import FiligreeDB
from filigree.install import install_claude_code_mcp, install_codex_mcp
from filigree.migrate import migrate_from_beads

# ---------------------------------------------------------------------------
# Bug 1: migrate — idempotent comments (no duplicates on re-migration)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Bug 1: migrate — connection safety (closed on error)
# ---------------------------------------------------------------------------


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
        # (if not closed on Windows, this could fail due to file locking;
        # the try/finally pattern ensures cleanup)
        verify_conn = sqlite3.connect(str(db_path))
        verify_conn.execute("SELECT * FROM issues")
        verify_conn.close()


# ---------------------------------------------------------------------------
# Bug 1: migrate — targeted exception handling (missing table)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Bug 2a: install — TOML backslash path escaping
# ---------------------------------------------------------------------------


class TestCodexTomlBackslash:
    def test_backslash_paths_escaped(self, tmp_path: Path) -> None:
        """Windows-style backslash paths must be escaped in TOML output."""
        with (
            patch("filigree.install_support.integrations.shutil.which", return_value=None),
            patch(
                "filigree.install_support.integrations._find_filigree_mcp_command",
                return_value="C:\\Program Files\\filigree\\filigree-mcp.exe",
            ),
        ):
            ok, _msg = install_codex_mcp(tmp_path)
        assert ok
        content = (tmp_path / ".codex" / "config.toml").read_text()
        # The raw TOML should have escaped backslashes
        assert "C:\\\\Program Files\\\\filigree\\\\filigree-mcp.exe" in content

    def test_unix_paths_unchanged(self, tmp_path: Path) -> None:
        """Unix paths (no backslashes) should be passed through unchanged."""
        with (
            patch("filigree.install_support.integrations.shutil.which", return_value=None),
            patch(
                "filigree.install_support.integrations._find_filigree_mcp_command",
                return_value="/usr/local/bin/filigree-mcp",
            ),
        ):
            ok, _msg = install_codex_mcp(tmp_path)
        assert ok
        content = (tmp_path / ".codex" / "config.toml").read_text()
        assert "/usr/local/bin/filigree-mcp" in content


# ---------------------------------------------------------------------------
# Bug 2b: install — TOML presence check does not false-match
# ---------------------------------------------------------------------------


class TestCodexTomlPresenceCheck:
    def test_filigree_extra_does_not_match(self, tmp_path: Path) -> None:
        """A TOML section [mcp_servers.filigree-extra] should NOT be mistaken for filigree."""
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        config = codex_dir / "config.toml"
        config.write_text('[mcp_servers.filigree-extra]\ncommand = "other"\n')

        with patch("filigree.install_support.integrations.shutil.which", return_value=None):
            ok, msg = install_codex_mcp(tmp_path)
        assert ok
        # Should have written a new filigree section (not returned "Already configured")
        assert "Already configured" not in msg
        content = config.read_text()
        assert "[mcp_servers.filigree]" in content

    def test_exact_filigree_detected(self, tmp_path: Path) -> None:
        """An existing [mcp_servers.filigree] section should be detected correctly."""
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        config = codex_dir / "config.toml"
        config.write_text('[mcp_servers.filigree]\ncommand = "filigree-mcp"\n')

        with patch("filigree.install_support.integrations.shutil.which", return_value=None):
            ok, msg = install_codex_mcp(tmp_path)
        assert ok
        assert "Already configured" in msg


# ---------------------------------------------------------------------------
# Bug 3: __init__.py — PackageNotFoundError
# ---------------------------------------------------------------------------


class TestPackageNotFoundError:
    def test_import_works_without_package_metadata(self) -> None:
        """Importing filigree should work even when package metadata is unavailable."""
        from importlib.metadata import PackageNotFoundError

        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("filigree"),
        ):
            # Re-import the module to trigger the version lookup
            import importlib

            import filigree

            importlib.reload(filigree)
            assert filigree.__version__ == "0.0.0-dev"

    def test_version_set_when_installed(self) -> None:
        """When package is installed, __version__ should be set from metadata."""
        import filigree

        # In our test environment, filigree should be installed
        assert filigree.__version__ is not None
        assert isinstance(filigree.__version__, str)


# ---------------------------------------------------------------------------
# Bug 4: install — malformed .mcp.json recovery
# ---------------------------------------------------------------------------


class TestMalformedMcpJson:
    def test_malformed_json_recovered(self, tmp_path: Path) -> None:
        """If .mcp.json contains invalid JSON, install should recover gracefully."""
        mcp_json = tmp_path / ".mcp.json"
        mcp_json.write_text("{this is not valid json!!!")

        with patch("filigree.install_support.integrations.shutil.which", return_value=None):
            ok, _msg = install_claude_code_mcp(tmp_path)

        assert ok
        # The output file should now be valid JSON with filigree configured
        data = json.loads(mcp_json.read_text())
        assert "filigree" in data["mcpServers"]

    def test_malformed_json_backup_created(self, tmp_path: Path) -> None:
        """The corrupt .mcp.json should be backed up before overwriting."""
        mcp_json = tmp_path / ".mcp.json"
        corrupt_content = "{this is not valid json!!!"
        mcp_json.write_text(corrupt_content)

        with patch("filigree.install_support.integrations.shutil.which", return_value=None):
            install_claude_code_mcp(tmp_path)

        backup = tmp_path / ".mcp.json.bak"
        assert backup.exists()
        assert backup.read_text() == corrupt_content

    def test_valid_json_preserved(self, tmp_path: Path) -> None:
        """Valid .mcp.json with existing entries should be preserved."""
        mcp_json = tmp_path / ".mcp.json"
        existing = {"mcpServers": {"other_tool": {"type": "stdio", "command": "other"}}}
        mcp_json.write_text(json.dumps(existing))

        with patch("filigree.install_support.integrations.shutil.which", return_value=None):
            ok, _msg = install_claude_code_mcp(tmp_path)

        assert ok
        data = json.loads(mcp_json.read_text())
        assert "other_tool" in data["mcpServers"]
        assert "filigree" in data["mcpServers"]
        # No backup should be created for valid JSON
        assert not (tmp_path / ".mcp.json.bak").exists()

    def test_empty_json_file(self, tmp_path: Path) -> None:
        """An empty .mcp.json file should be handled gracefully."""
        mcp_json = tmp_path / ".mcp.json"
        mcp_json.write_text("")

        with patch("filigree.install_support.integrations.shutil.which", return_value=None):
            ok, _msg = install_claude_code_mcp(tmp_path)

        assert ok
        data = json.loads(mcp_json.read_text())
        assert "filigree" in data["mcpServers"]
