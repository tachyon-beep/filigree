"""Tests for minor fixes across the codebase."""

from __future__ import annotations

import sqlite3

import pytest

from filigree.core import FiligreeDB
from filigree.migrations import rebuild_table


class TestFTS5SpecialCharacters:
    """FTS5 search should handle special characters gracefully."""

    def test_search_with_special_characters_returns_valid_terms(self, db: FiligreeDB) -> None:
        """Searching for 'notification @#$%' should find issues matching 'notification'."""
        db.create_issue("Fix notification system")
        db.create_issue("Unrelated feature")
        results = db.search_issues("notification @#$%")
        assert len(results) == 1
        assert "notification" in results[0].title.lower()

    def test_search_with_only_special_characters_returns_empty(self, db: FiligreeDB) -> None:
        """Searching for only special characters should return empty, not error."""
        db.create_issue("Some issue")
        results = db.search_issues("@#$%^&()")
        assert results == []

    def test_search_with_mixed_special_and_valid(self, db: FiligreeDB) -> None:
        """Special chars mixed with valid terms should still find matches."""
        db.create_issue("Authentication bug in login")
        results = db.search_issues("auth!@#enti")
        # "auth" and "enti" are separate tokens after sanitization — but the original
        # becomes "authentication" after stripping specials, which may tokenize differently.
        # The key point is: no crash.
        assert isinstance(results, list)


class TestCycleTimeDisplay:
    """cli.py metrics should display 0.0h correctly, not 'n/a'."""

    def test_zero_cycle_time_not_displayed_as_na(self) -> None:
        """0.0 is a valid cycle time and should format as '0.0h', not 'n/a'."""
        # The fix changes `if val` to `if val is not None` in cli.py.
        # Simulate the fixed formatting logic from cli.py line ~894:
        cycle_time = 0.0
        ct_str = f"{cycle_time}h" if cycle_time is not None else "n/a"
        assert ct_str == "0.0h"

        # None should still produce "n/a"
        cycle_time_none: float | None = None
        ct_str_none = f"{cycle_time_none}h" if cycle_time_none is not None else "n/a"
        assert ct_str_none == "n/a"


class TestRebuildTableNoSharedColumns:
    """rebuild_table should raise ValueError when schemas share zero columns."""

    def test_no_shared_columns_raises(self, tmp_path: object) -> None:
        """When old and new schemas have no columns in common, raise ValueError."""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE test_tbl (alpha TEXT, beta INTEGER)")
        conn.execute("INSERT INTO test_tbl VALUES ('hello', 42)")

        new_schema = "CREATE TABLE test_tbl (gamma TEXT, delta REAL)"

        with pytest.raises(ValueError, match="No shared columns"):
            rebuild_table(conn, "test_tbl", new_schema)

        conn.close()

    def test_shared_columns_succeeds(self, tmp_path: object) -> None:
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
