"""Tests for hooks.py — session context and dashboard helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from filigree.core import FiligreeDB
from filigree.hooks import (
    READY_CAP,
    _build_context,
    _is_port_listening,
    generate_session_context,
)


class TestBuildContext:
    def test_empty_project(self, db: FiligreeDB) -> None:
        result = _build_context(db)
        assert "=== Filigree Project Snapshot ===" in result
        assert "STATS:" in result
        assert "0 ready" in result
        assert "0 blocked" in result

    def test_ready_issues_shown(self, db: FiligreeDB) -> None:
        db.create_issue("Fix the bug", priority=1)
        db.create_issue("Add feature", priority=2)
        result = _build_context(db)
        assert "READY TO WORK" in result
        assert "Fix the bug" in result
        assert "Add feature" in result

    def test_in_progress_shown(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Working on this", priority=1)
        db.update_issue(issue.id, status="in_progress")
        result = _build_context(db)
        assert "IN PROGRESS" in result
        assert "Working on this" in result

    def test_critical_path_shown(self, db: FiligreeDB) -> None:
        a = db.create_issue("Blocker task", priority=1)
        b = db.create_issue("Downstream task", priority=2)
        db.add_dependency(b.id, a.id)
        result = _build_context(db)
        assert "CRITICAL PATH" in result
        assert "Blocker task" in result
        assert "Downstream task" in result

    def test_truncation_at_cap(self, db: FiligreeDB) -> None:
        for i in range(READY_CAP + 5):
            db.create_issue(f"Issue {i}", priority=2)
        result = _build_context(db)
        assert "truncated" in result
        assert "filigree ready" in result

    def test_stats_line(self, populated_db: FiligreeDB) -> None:
        result = _build_context(populated_db)
        assert "STATS:" in result
        assert "ready" in result
        assert "blocked" in result


class TestGenerateSessionContext:
    def test_returns_none_without_filigree_dir(self, tmp_path: Path) -> None:
        with patch("filigree.hooks.find_filigree_root", side_effect=FileNotFoundError):
            assert generate_session_context() is None

    def test_returns_context_string(self, tmp_path: Path, db: FiligreeDB) -> None:
        """Smoke test that generate_session_context returns a string when a project exists."""
        # We mock find_filigree_root to return the db's directory
        db_dir = Path(db.db_path).parent
        with (
            patch("filigree.hooks.find_filigree_root", return_value=db_dir),
            patch("filigree.hooks.read_config", return_value={"prefix": "test"}),
        ):
            result = generate_session_context()
        assert result is not None
        assert "Filigree Project Snapshot" in result


class TestIsPortListening:
    def test_unused_port_returns_false(self) -> None:
        # Port 0 is never bound to a server; use a high random port
        assert _is_port_listening(49999) is False
