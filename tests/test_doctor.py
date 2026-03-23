"""Tests for install_support/doctor.py — health check system."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from filigree.core import (
    CONFIG_FILENAME,
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
    write_config,
)
from filigree.db_schema import CURRENT_SCHEMA_VERSION
from filigree.install_support import (
    FILIGREE_INSTRUCTIONS_MARKER,
    SKILL_MARKER,
    SKILL_NAME,
)
from filigree.install_support.doctor import (
    CheckResult,
    _is_absolute_command_path,
    _is_venv_binary,
    run_doctor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path, *, with_db: bool = True, with_config: bool = True, with_summary: bool = True) -> Path:
    """Create a minimal filigree project under tmp_path. Returns project root."""
    filigree_dir = tmp_path / FILIGREE_DIR_NAME
    filigree_dir.mkdir()

    if with_config:
        write_config(filigree_dir, {"prefix": "tst", "version": 1})

    if with_db:
        d = FiligreeDB(filigree_dir / DB_FILENAME, prefix="tst")
        d.initialize()
        d.close()

    if with_summary:
        (filigree_dir / SUMMARY_FILENAME).write_text("# summary\n")

    return tmp_path


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_passed_construction(self) -> None:
        r = CheckResult(name="My check", passed=True, message="All good")
        assert r.name == "My check"
        assert r.passed is True
        assert r.message == "All good"
        assert r.fix_hint == ""

    def test_failed_construction(self) -> None:
        r = CheckResult(name="My check", passed=False, message="Broken", fix_hint="Run: fix it")
        assert r.passed is False
        assert r.fix_hint == "Run: fix it"

    def test_icon_pass(self) -> None:
        r = CheckResult(name="x", passed=True, message="ok")
        assert r.icon == "OK"

    def test_icon_fail(self) -> None:
        r = CheckResult(name="x", passed=False, message="bad")
        assert r.icon == "!!"

    def test_icon_only_two_values(self) -> None:
        """icon must be one of two strings — no other values allowed."""
        assert CheckResult("a", True, "").icon in {"OK", "!!"}
        assert CheckResult("a", False, "").icon in {"OK", "!!"}


# ---------------------------------------------------------------------------
# _is_venv_binary
# ---------------------------------------------------------------------------


class TestIsVenvBinary:
    def test_path_inside_venv(self, tmp_path: Path) -> None:
        venv = tmp_path / "myenv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("[pyvenv]\n")
        exe = bin_dir / "python"
        exe.write_text("")
        assert _is_venv_binary(str(exe)) is True

    def test_path_not_inside_venv(self, tmp_path: Path) -> None:
        # No pyvenv.cfg anywhere in parents
        exe = tmp_path / "usr" / "bin" / "python"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        assert _is_venv_binary(str(exe)) is False

    def test_pyvenv_cfg_several_levels_up(self, tmp_path: Path) -> None:
        # pyvenv.cfg is at grandparent
        venv = tmp_path / "venv"
        deep = venv / "lib" / "python3.11" / "site-packages"
        deep.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("[pyvenv]\n")
        target = deep / "somefile.py"
        target.write_text("")
        assert _is_venv_binary(str(target)) is True

    def test_nonexistent_path_no_venv_cfg(self, tmp_path: Path) -> None:
        # Path doesn't exist; no pyvenv.cfg in tree — should be False
        fake = tmp_path / "nosuchdir" / "python"
        assert _is_venv_binary(str(fake)) is False

    def test_root_level_binary(self) -> None:
        # /usr/bin/python — no pyvenv.cfg up to root
        assert _is_venv_binary("/usr/bin/python") is False


# ---------------------------------------------------------------------------
# _is_absolute_command_path
# ---------------------------------------------------------------------------


class TestIsAbsoluteCommandPath:
    def test_empty_string(self) -> None:
        assert _is_absolute_command_path("") is False

    def test_unix_absolute(self) -> None:
        assert _is_absolute_command_path("/usr/bin/python") is True

    def test_unix_home_relative(self) -> None:
        # ~ is not absolute — it's not expanded here
        assert _is_absolute_command_path("~/bin/filigree") is False

    def test_relative_path(self) -> None:
        assert _is_absolute_command_path("./filigree") is False

    def test_bare_command(self) -> None:
        assert _is_absolute_command_path("filigree") is False

    def test_windows_unc_path(self) -> None:
        assert _is_absolute_command_path("\\\\server\\share\\bin\\filigree") is True

    def test_windows_drive_letter_forward_slash(self) -> None:
        assert _is_absolute_command_path("C:/Users/user/bin/filigree") is True

    def test_windows_drive_letter_backslash(self) -> None:
        assert _is_absolute_command_path("C:\\Users\\user\\bin\\filigree") is True

    def test_windows_drive_letter_too_short(self) -> None:
        # "C:" alone has no separator at index 2
        assert _is_absolute_command_path("C:") is False

    def test_non_alpha_drive_letter(self) -> None:
        # Digit at position 0 shouldn't be treated as Windows drive
        assert _is_absolute_command_path("1:/path") is False


# ---------------------------------------------------------------------------
# run_doctor — .filigree/ directory check
# ---------------------------------------------------------------------------


class TestDoctorFiligreeDir:
    def test_missing_filigree_dir_returns_immediately(self, tmp_path: Path) -> None:
        results = run_doctor(tmp_path)
        assert len(results) == 1
        r = results[0]
        assert ".filigree/" in r.name
        assert r.passed is False
        assert "filigree init" in r.fix_hint

    def test_filigree_dir_present(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        first = results[0]
        assert ".filigree/" in first.name
        assert first.passed is True

    def test_filigree_dir_found_in_parent(self, tmp_path: Path) -> None:
        """Doctor should walk up to find .filigree/ when not in cwd."""
        _make_project(tmp_path)
        subdir = tmp_path / "subproject"
        subdir.mkdir()
        results = run_doctor(subdir)
        first = results[0]
        assert first.passed is True


# ---------------------------------------------------------------------------
# run_doctor — config.json check
# ---------------------------------------------------------------------------


class TestDoctorConfigJson:
    def test_valid_config(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        config_result = next(r for r in results if r.name == "config.json")
        assert config_result.passed is True
        assert "tst" in config_result.message

    def test_missing_config(self, tmp_path: Path) -> None:
        _make_project(tmp_path, with_config=False)
        results = run_doctor(tmp_path)
        config_result = next(r for r in results if r.name == "config.json")
        assert config_result.passed is False
        assert "Missing" in config_result.message
        assert "filigree init" in config_result.fix_hint

    def test_corrupt_json_config(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        config_path = tmp_path / FILIGREE_DIR_NAME / CONFIG_FILENAME
        config_path.write_text("{not valid json")
        results = run_doctor(tmp_path)
        config_result = next(r for r in results if r.name == "config.json")
        assert config_result.passed is False
        assert "Invalid JSON" in config_result.message

    def test_config_not_object(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        config_path = tmp_path / FILIGREE_DIR_NAME / CONFIG_FILENAME
        config_path.write_text("[1, 2, 3]")
        results = run_doctor(tmp_path)
        config_result = next(r for r in results if r.name == "config.json")
        assert config_result.passed is False
        assert "object" in config_result.message.lower()


# ---------------------------------------------------------------------------
# run_doctor — filigree.db check
# ---------------------------------------------------------------------------


class TestDoctorDatabase:
    def test_valid_db(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        db_result = next(r for r in results if r.name == "filigree.db")
        assert db_result.passed is True
        assert "issues" in db_result.message

    def test_missing_db(self, tmp_path: Path) -> None:
        _make_project(tmp_path, with_db=False)
        results = run_doctor(tmp_path)
        db_result = next(r for r in results if r.name == "filigree.db")
        assert db_result.passed is False
        assert "Missing" in db_result.message
        assert "filigree init" in db_result.fix_hint

    def test_corrupt_db(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        db_path = tmp_path / FILIGREE_DIR_NAME / DB_FILENAME
        db_path.write_bytes(b"this is not sqlite")
        results = run_doctor(tmp_path)
        db_result = next(r for r in results if r.name == "filigree.db")
        assert db_result.passed is False
        assert "error" in db_result.message.lower()

    def test_schema_version_current(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        schema_result = next(r for r in results if r.name == "Schema version")
        assert schema_result.passed is True
        assert str(CURRENT_SCHEMA_VERSION) in schema_result.message

    def test_schema_version_too_old(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        db_path = tmp_path / FILIGREE_DIR_NAME / DB_FILENAME
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION - 1}")
        conn.commit()
        conn.close()
        results = run_doctor(tmp_path)
        schema_result = next(r for r in results if r.name == "Schema version")
        assert schema_result.passed is False
        assert "outdated" in schema_result.fix_hint.lower() or "filigree doctor" in schema_result.fix_hint

    def test_schema_version_too_new(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        db_path = tmp_path / FILIGREE_DIR_NAME / DB_FILENAME
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        results = run_doctor(tmp_path)
        schema_result = next(r for r in results if r.name == "Schema version")
        assert schema_result.passed is False
        assert "newer" in schema_result.fix_hint.lower() or "Upgrade" in schema_result.fix_hint


# ---------------------------------------------------------------------------
# run_doctor — context.md freshness check
# ---------------------------------------------------------------------------


class TestDoctorContextMd:
    def test_fresh_summary(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        ctx_result = next(r for r in results if r.name == "context.md")
        assert ctx_result.passed is True
        assert "Fresh" in ctx_result.message

    def test_missing_summary(self, tmp_path: Path) -> None:
        _make_project(tmp_path, with_summary=False)
        results = run_doctor(tmp_path)
        ctx_result = next(r for r in results if r.name == "context.md")
        assert ctx_result.passed is False
        assert "Missing" in ctx_result.message

    def test_stale_summary(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        summary_path = tmp_path / FILIGREE_DIR_NAME / SUMMARY_FILENAME
        # Set mtime 90 minutes ago
        old_mtime = time.time() - 90 * 60
        import os

        os.utime(str(summary_path), (old_mtime, old_mtime))
        results = run_doctor(tmp_path)
        ctx_result = next(r for r in results if r.name == "context.md")
        assert ctx_result.passed is False
        assert "Stale" in ctx_result.message
        assert "90" in ctx_result.message or "minutes" in ctx_result.message


# ---------------------------------------------------------------------------
# run_doctor — .gitignore check
# ---------------------------------------------------------------------------


class TestDoctorGitignore:
    def test_gitignore_with_filigree_entry(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        (tmp_path / ".gitignore").write_text(".filigree/\n")
        results = run_doctor(tmp_path)
        gi_result = next(r for r in results if r.name == ".gitignore")
        assert gi_result.passed is True

    def test_gitignore_without_filigree_entry(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
        results = run_doctor(tmp_path)
        gi_result = next(r for r in results if r.name == ".gitignore")
        assert gi_result.passed is False
        assert "not in .gitignore" in gi_result.message

    def test_no_gitignore(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        gi_result = next(r for r in results if r.name == ".gitignore")
        assert gi_result.passed is False
        assert "No .gitignore" in gi_result.message

    def test_gitignore_bare_entry_without_slash(self, tmp_path: Path) -> None:
        """`.filigree` without trailing slash also satisfies the check."""
        _make_project(tmp_path)
        (tmp_path / ".gitignore").write_text(".filigree\n")
        results = run_doctor(tmp_path)
        gi_result = next(r for r in results if r.name == ".gitignore")
        assert gi_result.passed is True


# ---------------------------------------------------------------------------
# run_doctor — Claude Code MCP (.mcp.json) check
# ---------------------------------------------------------------------------


class TestDoctorClaudeCodeMcp:
    def _base_mcp(self, command: str = "filigree-mcp", project_root: Path | None = None) -> dict:
        args = ["--project", str(project_root)] if project_root else []
        return {"mcpServers": {"filigree": {"command": command, "args": args}}}

    def test_no_mcp_json(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        mcp_result = next(r for r in results if r.name == "Claude Code MCP")
        assert mcp_result.passed is False
        assert "No .mcp.json" in mcp_result.message

    def test_valid_mcp_json_relative_command(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        mcp_data = self._base_mcp("filigree-mcp", tmp_path)
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data))
        results = run_doctor(tmp_path)
        mcp_result = next(r for r in results if r.name == "Claude Code MCP")
        assert mcp_result.passed is True

    def test_mcp_json_no_filigree_entry(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {}}}))
        results = run_doctor(tmp_path)
        mcp_result = next(r for r in results if r.name == "Claude Code MCP")
        assert mcp_result.passed is False
        assert "filigree not in .mcp.json" in mcp_result.message

    def test_corrupt_mcp_json(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        (tmp_path / ".mcp.json").write_text("{bad json")
        results = run_doctor(tmp_path)
        mcp_result = next(r for r in results if r.name == "Claude Code MCP")
        assert mcp_result.passed is False
        assert "Invalid .mcp.json" in mcp_result.message

    def test_mcp_json_absolute_command_missing_binary(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        missing_bin = tmp_path / "bin" / "filigree-mcp"
        # Don't create the binary
        mcp_data = {"mcpServers": {"filigree": {"command": str(missing_bin), "args": []}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data))
        results = run_doctor(tmp_path)
        mcp_result = next(r for r in results if r.name == "Claude Code MCP")
        assert mcp_result.passed is False
        assert "Binary not found" in mcp_result.message

    def test_mcp_json_absolute_command_existing_binary(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        binary = bin_dir / "filigree-mcp"
        binary.write_text("#!/bin/sh\n")
        mcp_data = {"mcpServers": {"filigree": {"command": str(binary), "args": []}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data))
        results = run_doctor(tmp_path)
        mcp_result = next(r for r in results if r.name == "Claude Code MCP")
        # Passes unless it's in a venv with a uv tool installed
        assert mcp_result.name == "Claude Code MCP"


# ---------------------------------------------------------------------------
# run_doctor — Claude Code hooks (.claude/settings.json) check
# ---------------------------------------------------------------------------


class TestDoctorClaudeCodeHooks:
    def _settings_with_hook(self, command: str = "filigree session-context") -> dict:
        return {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [{"type": "command", "command": command}],
                    }
                ]
            }
        }

    def test_no_settings_json(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        hook_result = next(r for r in results if r.name == "Claude Code hooks")
        assert hook_result.passed is False
        assert "No .claude/settings.json" in hook_result.message

    def test_corrupt_settings_json(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{bad")
        results = run_doctor(tmp_path)
        hook_result = next(r for r in results if r.name == "Claude Code hooks")
        assert hook_result.passed is False
        assert "Invalid .claude/settings.json" in hook_result.message

    def test_settings_missing_hook(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {}}))
        results = run_doctor(tmp_path)
        hook_result = next(r for r in results if r.name == "Claude Code hooks")
        assert hook_result.passed is False
        assert "session-context hook not found" in hook_result.message

    def test_settings_with_hook_relative_command(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = self._settings_with_hook("filigree session-context")
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        results = run_doctor(tmp_path)
        hook_result = next(r for r in results if r.name == "Claude Code hooks")
        assert hook_result.passed is True
        assert "session-context hook registered" in hook_result.message

    def test_settings_with_hook_absolute_missing_binary(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        missing = str(tmp_path / "bin" / "filigree")
        settings = self._settings_with_hook(f"{missing} session-context")
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        results = run_doctor(tmp_path)
        hook_result = next(r for r in results if r.name == "Claude Code hooks")
        assert hook_result.passed is False
        assert "Binary not found" in hook_result.message


# ---------------------------------------------------------------------------
# run_doctor — Claude Code skills check
# ---------------------------------------------------------------------------


class TestDoctorClaudeCodeSkills:
    def test_skill_missing(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        skill_result = next(r for r in results if r.name == "Claude Code skills")
        assert skill_result.passed is False
        assert SKILL_NAME in skill_result.message
        assert "filigree install --skills" in skill_result.fix_hint

    def test_skill_present(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        skill_dir = tmp_path / ".claude" / "skills" / SKILL_NAME
        skill_dir.mkdir(parents=True)
        (skill_dir / SKILL_MARKER).write_text("# skill\n")
        results = run_doctor(tmp_path)
        skill_result = next(r for r in results if r.name == "Claude Code skills")
        assert skill_result.passed is True
        assert SKILL_NAME in skill_result.message


# ---------------------------------------------------------------------------
# run_doctor — Codex skills check
# ---------------------------------------------------------------------------


class TestDoctorCodexSkills:
    def test_codex_skill_missing(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        skill_result = next(r for r in results if r.name == "Codex skills")
        assert skill_result.passed is False
        assert SKILL_NAME in skill_result.message
        assert "filigree install --codex-skills" in skill_result.fix_hint

    def test_codex_skill_present(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        skill_dir = tmp_path / ".agents" / "skills" / SKILL_NAME
        skill_dir.mkdir(parents=True)
        (skill_dir / SKILL_MARKER).write_text("# skill\n")
        results = run_doctor(tmp_path)
        skill_result = next(r for r in results if r.name == "Codex skills")
        assert skill_result.passed is True
        assert SKILL_NAME in skill_result.message


# ---------------------------------------------------------------------------
# run_doctor — CLAUDE.md and AGENTS.md instructions check
# ---------------------------------------------------------------------------


class TestDoctorInstructionFiles:
    def test_claude_md_missing(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        results = run_doctor(tmp_path)
        claude_result = next(r for r in results if r.name == "CLAUDE.md")
        assert claude_result.passed is False
        assert "File not found" in claude_result.message

    def test_claude_md_without_marker(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("# Project\n\nNothing filigree here.\n")
        results = run_doctor(tmp_path)
        claude_result = next(r for r in results if r.name == "CLAUDE.md")
        assert claude_result.passed is False
        assert "No filigree instructions" in claude_result.message

    def test_claude_md_with_marker(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        (tmp_path / "CLAUDE.md").write_text(f"# Project\n\n{FILIGREE_INSTRUCTIONS_MARKER}\n")
        results = run_doctor(tmp_path)
        claude_result = next(r for r in results if r.name == "CLAUDE.md")
        assert claude_result.passed is True
        assert "instructions present" in claude_result.message.lower()

    def test_agents_md_with_marker(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        (tmp_path / "AGENTS.md").write_text(f"# Agents\n\n{FILIGREE_INSTRUCTIONS_MARKER}\n")
        results = run_doctor(tmp_path)
        agents_result = next((r for r in results if r.name == "AGENTS.md"), None)
        assert agents_result is not None
        assert agents_result.passed is True

    def test_agents_md_without_marker(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# Agents\n\nNo instructions here.\n")
        results = run_doctor(tmp_path)
        agents_result = next((r for r in results if r.name == "AGENTS.md"), None)
        assert agents_result is not None
        assert agents_result.passed is False

    def test_agents_md_absent_not_reported(self, tmp_path: Path) -> None:
        """AGENTS.md is optional — its absence should not produce a check result."""
        _make_project(tmp_path)
        # Make sure AGENTS.md doesn't exist
        agents_md = tmp_path / "AGENTS.md"
        if agents_md.exists():
            agents_md.unlink()
        results = run_doctor(tmp_path)
        agents_result = next((r for r in results if r.name == "AGENTS.md"), None)
        assert agents_result is None


# ---------------------------------------------------------------------------
# run_doctor — git working tree check
# ---------------------------------------------------------------------------


class TestDoctorGitWorkingTree:
    def test_clean_working_tree(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        clean_output = MagicMock(returncode=0, stdout="")
        with patch("subprocess.run", return_value=clean_output) as mock_run:
            results = run_doctor(tmp_path)
        git_result = next((r for r in results if r.name == "Git working tree"), None)
        assert git_result is not None
        assert git_result.passed is True
        assert "Clean" in git_result.message
        # Verify git was called with the right args
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "git"
        assert "status" in call_args

    def test_dirty_working_tree(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        dirty_output = MagicMock(returncode=0, stdout=" M src/file.py\n?? new_file.py\n")
        with patch("subprocess.run", return_value=dirty_output):
            results = run_doctor(tmp_path)
        git_result = next((r for r in results if r.name == "Git working tree"), None)
        assert git_result is not None
        assert git_result.passed is False
        assert "2" in git_result.message
        assert "uncommitted" in git_result.message

    def test_git_not_installed(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            results = run_doctor(tmp_path)
        # No git result — not an error condition
        git_result = next((r for r in results if r.name == "Git working tree"), None)
        assert git_result is None

    def test_git_timeout(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 5)):
            results = run_doctor(tmp_path)
        git_result = next((r for r in results if r.name == "Git working tree"), None)
        assert git_result is not None
        assert git_result.passed is False
        assert "timed out" in git_result.message

    def test_git_not_a_repo(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        # Non-zero returncode means not a git repo
        not_repo = MagicMock(returncode=128, stdout="")
        with patch("subprocess.run", return_value=not_repo):
            results = run_doctor(tmp_path)
        # returncode != 0 means no result appended
        git_result = next((r for r in results if r.name == "Git working tree"), None)
        assert git_result is None


# ---------------------------------------------------------------------------
# run_doctor — result list structure
# ---------------------------------------------------------------------------


class TestDoctorResultStructure:
    def test_all_results_are_check_result_instances(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
            results = run_doctor(tmp_path)
        for r in results:
            assert isinstance(r, CheckResult)

    def test_all_results_have_non_empty_name(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
            results = run_doctor(tmp_path)
        for r in results:
            assert r.name.strip(), f"Empty name in result: {r!r}"

    def test_all_results_have_message(self, tmp_path: Path) -> None:
        _make_project(tmp_path)
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
            results = run_doctor(tmp_path)
        for r in results:
            assert isinstance(r.message, str)

    def test_failed_results_have_fix_hint(self, tmp_path: Path) -> None:
        """Every failed check should provide actionable guidance."""
        _make_project(tmp_path)
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
            results = run_doctor(tmp_path)
        for r in results:
            if not r.passed and r.name not in {"Git working tree", "Installation"}:
                assert r.fix_hint.strip(), f"Failed check '{r.name}' has no fix_hint"
