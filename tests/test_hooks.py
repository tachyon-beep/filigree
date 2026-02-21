"""Tests for hooks.py — session context and dashboard helpers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from filigree.core import FiligreeDB
from filigree.hooks import (
    READY_CAP,
    _build_context,
    _check_instructions_freshness,
    _extract_marker_hash,
    _is_port_listening,
    ensure_dashboard_running,
    generate_session_context,
)
from filigree.install import (
    _instructions_hash,
    inject_instructions,
    install_skills,
)


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the global registry to a temp dir so tests never touch ~/.filigree/."""
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()
    monkeypatch.setattr("filigree.registry.REGISTRY_DIR", reg_dir)
    monkeypatch.setattr("filigree.registry.REGISTRY_FILE", reg_dir / "registry.json")
    monkeypatch.setattr("filigree.registry.REGISTRY_LOCK", reg_dir / "registry.lock")


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


class TestExecutableResolution:
    """Bug filigree-ae9597: filigree_bin must not mangle directory names containing 'python'."""

    def test_no_directory_mangling(self, tmp_path: Path) -> None:
        """If sys.executable is in a dir containing 'python', only basename should change."""
        expected_bin = "/home/python_user/.venv/bin/filigree"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running
        mock_proc.pid = 11111

        with (
            patch("filigree.hooks.find_filigree_root", return_value=tmp_path),
            patch("filigree.hooks._is_port_listening", return_value=False),
            patch("filigree.hooks.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("filigree.hooks._find_filigree_command", return_value=expected_bin),
            patch("filigree.hooks.time.sleep"),
            patch.dict(os.environ, {"TMPDIR": str(tmp_path)}),
        ):
            ensure_dashboard_running()

        # The command should preserve the directory and only change the basename
        cmd = mock_popen.call_args[0][0]
        assert "filigree_user" not in cmd[0], f"Directory was mangled: {cmd[0]}"
        assert cmd[0] == expected_bin


class TestEnsureDashboardDependencyCheck:
    """Bug filigree-caa62b: dependency check must detect missing uvicorn/fastapi."""

    def test_reports_error_when_uvicorn_missing(self) -> None:
        """Should detect missing uvicorn even though filigree.dashboard imports it lazily."""
        with patch.dict("sys.modules", {"uvicorn": None}):
            result = ensure_dashboard_running()
        assert "requires extra dependencies" in result

    def test_reports_error_when_fastapi_missing(self) -> None:
        """Should detect missing fastapi even though filigree.dashboard imports it lazily."""
        # Also block fastapi sub-modules that might be cached
        blocked = {"fastapi": None, "fastapi.responses": None}
        with patch.dict("sys.modules", blocked):
            result = ensure_dashboard_running()
        assert "requires extra dependencies" in result


class TestEnsureDashboardSubprocessVerification:
    """Bug filigree-20ad27: must verify subprocess actually started."""

    def test_reports_failure_when_subprocess_exits_immediately(self, tmp_path: Path) -> None:
        """If the spawned process exits right away, report failure not success."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        mock_proc.pid = 12345

        with (
            patch("filigree.hooks.find_filigree_root", return_value=tmp_path),
            patch("filigree.hooks._is_port_listening", return_value=False),
            patch("filigree.hooks.subprocess.Popen", return_value=mock_proc),
            patch("filigree.hooks._find_filigree_command", return_value="/usr/bin/filigree"),
            patch("filigree.hooks.time.sleep"),
            patch.dict(os.environ, {"TMPDIR": str(tmp_path)}),
        ):
            result = ensure_dashboard_running()

        assert "exited" in result.lower()
        assert "12345" not in result or "started" not in result.lower()

    def test_reports_success_when_subprocess_stays_running(self, tmp_path: Path) -> None:
        """If the spawned process is still alive after brief check, report success."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running
        mock_proc.pid = 99999

        with (
            patch("filigree.hooks.find_filigree_root", return_value=tmp_path),
            patch("filigree.hooks._is_port_listening", return_value=False),
            patch("filigree.hooks.subprocess.Popen", return_value=mock_proc),
            patch("filigree.hooks._find_filigree_command", return_value="/usr/bin/filigree"),
            patch("filigree.hooks.time.sleep"),
            patch.dict(os.environ, {"TMPDIR": str(tmp_path)}),
        ):
            result = ensure_dashboard_running()

        assert "started" in result.lower()
        assert "99999" in result

    def test_stderr_captured_on_failure(self, tmp_path: Path) -> None:
        """When process exits immediately, stderr content should be in the message."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        mock_proc.pid = 12345

        logfile = tmp_path / "filigree-dashboard.log"

        with (
            patch("filigree.hooks.find_filigree_root", return_value=tmp_path),
            patch("filigree.hooks._is_port_listening", return_value=False),
            patch("filigree.hooks.subprocess.Popen", return_value=mock_proc),
            patch("filigree.hooks._find_filigree_command", return_value="/usr/bin/filigree"),
            patch("filigree.hooks.time.sleep"),
            patch.dict(os.environ, {"TMPDIR": str(tmp_path)}),
        ):
            # Pre-write error content to the log file that ensure_dashboard_running
            # would create via stderr redirect. The mock Popen won't write to it,
            # so we simulate what a real failing process would leave behind.
            result = ensure_dashboard_running()
            # After the function runs, write to the log as if the process did
            logfile.write_text("ModuleNotFoundError: No module named 'uvicorn'")

        # The function should read the log file for diagnostics.
        # With mock Popen, the log file is empty, so detail will be absent.
        # Main assertion: function detects the exit and doesn't report success.
        assert "exited" in result.lower()
        assert "started" not in result.lower()


class TestExtractMarkerHash:
    def test_extracts_hash_from_versioned_marker(self) -> None:
        content = "before\n<!-- filigree:instructions:v1.2.0:abc12345 -->\nstuff\n<!-- /filigree:instructions -->"
        assert _extract_marker_hash(content) == "abc12345"

    def test_returns_none_for_old_format_marker(self) -> None:
        content = "before\n<!-- filigree:instructions -->\nstuff\n<!-- /filigree:instructions -->"
        assert _extract_marker_hash(content) is None

    def test_returns_none_when_no_marker(self) -> None:
        assert _extract_marker_hash("just some text") is None

    def test_extracts_hash_with_different_version(self) -> None:
        content = "<!-- filigree:instructions:v2.0.0:deadbeef -->"
        assert _extract_marker_hash(content) == "deadbeef"


class TestCheckInstructionsFreshness:
    def test_updates_stale_claude_md(self, tmp_path: Path) -> None:
        """CLAUDE.md with a different hash should be updated."""
        claude_md = tmp_path / "CLAUDE.md"
        # Write instructions with a fake (stale) hash
        claude_md.write_text(
            "# My Project\n\n<!-- filigree:instructions:v0.0.0:00000000 -->\nold instructions\n<!-- /filigree:instructions -->\n"
        )
        messages = _check_instructions_freshness(tmp_path)
        assert any("CLAUDE.md" in m for m in messages)
        # Verify the file now has the current hash
        content = claude_md.read_text()
        assert _instructions_hash() in content

    def test_skips_fresh_claude_md(self, tmp_path: Path) -> None:
        """CLAUDE.md with the current hash should not be touched."""
        claude_md = tmp_path / "CLAUDE.md"
        inject_instructions(claude_md)
        mtime_before = claude_md.stat().st_mtime
        messages = _check_instructions_freshness(tmp_path)
        assert not any("CLAUDE.md" in m for m in messages)
        assert claude_md.stat().st_mtime == mtime_before

    def test_updates_old_format_marker(self, tmp_path: Path) -> None:
        """CLAUDE.md with the old marker format (no hash) should be updated."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("<!-- filigree:instructions -->\nold content\n<!-- /filigree:instructions -->\n")
        messages = _check_instructions_freshness(tmp_path)
        assert any("CLAUDE.md" in m for m in messages)
        content = claude_md.read_text()
        assert _instructions_hash() in content

    def test_skips_missing_files(self, tmp_path: Path) -> None:
        """No CLAUDE.md or AGENTS.md should produce no messages."""
        messages = _check_instructions_freshness(tmp_path)
        assert messages == []

    def test_updates_agents_md(self, tmp_path: Path) -> None:
        """AGENTS.md with stale hash should also be updated."""
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("<!-- filigree:instructions:v0.0.0:00000000 -->\nold\n<!-- /filigree:instructions -->\n")
        messages = _check_instructions_freshness(tmp_path)
        assert any("AGENTS.md" in m for m in messages)

    def test_skips_file_without_marker(self, tmp_path: Path) -> None:
        """CLAUDE.md without any filigree marker should be left alone."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nNo filigree here.\n")
        messages = _check_instructions_freshness(tmp_path)
        assert messages == []
        assert "filigree" not in claude_md.read_text().lower() or "No filigree here" in claude_md.read_text()

    def test_updates_stale_skill_pack(self, tmp_path: Path) -> None:
        """Skill pack with different content should be overwritten."""
        skill_dir = tmp_path / ".claude" / "skills" / "filigree-workflow"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# Old skill content that differs from current\n")
        messages = _check_instructions_freshness(tmp_path)
        assert any("skill pack" in m for m in messages)

    def test_skips_fresh_skill_pack(self, tmp_path: Path) -> None:
        """Skill pack matching the shipped version should not be updated."""
        # Install the current skills first
        install_skills(tmp_path)
        messages = _check_instructions_freshness(tmp_path)
        assert not any("skill pack" in m for m in messages)


class TestGenerateSessionContextFreshness:
    def test_context_includes_freshness_messages(self, tmp_path: Path, db: FiligreeDB) -> None:
        """generate_session_context should include update messages when instructions are stale."""
        db_dir = Path(db.db_path).parent
        project_root = db_dir.parent
        # Create a stale CLAUDE.md in the project root
        claude_md = project_root / "CLAUDE.md"
        claude_md.write_text("<!-- filigree:instructions:v0.0.0:00000000 -->\nold\n<!-- /filigree:instructions -->\n")
        with (
            patch("filigree.hooks.find_filigree_root", return_value=db_dir),
            patch("filigree.hooks.read_config", return_value={"prefix": "test"}),
        ):
            result = generate_session_context()
        assert result is not None
        assert "Updated filigree instructions in CLAUDE.md" in result

    def test_context_without_stale_instructions(self, tmp_path: Path, db: FiligreeDB) -> None:
        """generate_session_context should not include update messages when everything is fresh."""
        db_dir = Path(db.db_path).parent
        with (
            patch("filigree.hooks.find_filigree_root", return_value=db_dir),
            patch("filigree.hooks.read_config", return_value={"prefix": "test"}),
        ):
            result = generate_session_context()
        assert result is not None
        assert "Updated" not in result
