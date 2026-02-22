"""Tests for install.py — instructions, gitignore, doctor."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from filigree.core import (
    CONFIG_FILENAME,
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
    find_filigree_command,
)
from filigree.install import (
    FILIGREE_INSTRUCTIONS_MARKER,
    SKILL_NAME,
    CheckResult,
    _find_filigree_mcp_command,
    _has_hook_command,
    _instructions_hash,
    _instructions_version,
    ensure_gitignore,
    inject_instructions,
    install_claude_code_hooks,
    install_claude_code_mcp,
    install_codex_mcp,
    install_skills,
    run_doctor,
)


class TestInjectInstructions:
    def test_create_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        ok, _msg = inject_instructions(target)
        assert ok
        assert target.exists()
        content = target.read_text()
        assert FILIGREE_INSTRUCTIONS_MARKER in content
        assert "filigree ready" in content

    def test_append_to_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Existing content\n\nSome stuff here.\n")
        ok, _msg = inject_instructions(target)
        assert ok
        content = target.read_text()
        assert "Existing content" in content
        assert FILIGREE_INSTRUCTIONS_MARKER in content

    def test_replace_existing_block(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        # Write initial instructions
        inject_instructions(target)
        # Replace again — should be idempotent
        ok, msg = inject_instructions(target)
        assert ok
        assert "Updated" in msg
        content = target.read_text()
        # The marker prefix appears once in the opening tag
        assert content.count(FILIGREE_INSTRUCTIONS_MARKER) == 1

    def test_versioned_marker_format(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        inject_instructions(target)
        content = target.read_text()
        version = _instructions_version()
        h = _instructions_hash()
        assert f"<!-- filigree:instructions:v{version}:{h} -->" in content
        assert "<!-- /filigree:instructions -->" in content

    def test_replace_malformed_block(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text(f"Before\n{FILIGREE_INSTRUCTIONS_MARKER}\nsome old stuff without end marker")
        ok, _msg = inject_instructions(target)
        assert ok
        content = target.read_text()
        assert "Before" in content
        assert "<!-- /filigree:instructions -->" in content

    def test_end_marker_before_start_marker_does_not_corrupt(self, tmp_path: Path) -> None:
        """End marker appearing before start marker must not cause malformed output."""
        end_marker = "<!-- /filigree:instructions -->"
        target = tmp_path / "CLAUDE.md"
        # Craft content where end marker appears before start marker
        target.write_text(f"Preamble\n{end_marker}\nMiddle\n{FILIGREE_INSTRUCTIONS_MARKER}\nold content\n{end_marker}\nAfter\n")
        ok, _msg = inject_instructions(target)
        assert ok
        content = target.read_text()
        # Preamble and the stray end marker before start should be preserved
        assert "Preamble" in content
        # The "After" section should be preserved
        assert "After" in content
        # "old content" between the real markers must be replaced, not duplicated
        assert "old content" not in content
        # "Middle" (between stray end marker and real start) must appear exactly once
        assert content.count("Middle") == 1


class TestEnsureGitignore:
    def test_create_gitignore(self, tmp_path: Path) -> None:
        ok, _msg = ensure_gitignore(tmp_path)
        assert ok
        assert (tmp_path / ".gitignore").exists()
        assert ".filigree/" in (tmp_path / ".gitignore").read_text()

    def test_append_to_existing(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n")
        ok, _msg = ensure_gitignore(tmp_path)
        assert ok
        content = gitignore.read_text()
        assert "*.pyc" in content
        assert ".filigree/" in content

    def test_already_present(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".filigree/\n")
        ok, msg = ensure_gitignore(tmp_path)
        assert ok
        assert "already" in msg


class TestRunDoctor:
    def test_healthy_project(self, filigree_project: Path) -> None:
        # Add .gitignore
        (filigree_project / ".gitignore").write_text(".filigree/\n")
        # Initialize DB schema version
        filigree_dir = filigree_project / FILIGREE_DIR_NAME
        d = FiligreeDB(filigree_dir / DB_FILENAME, prefix="proj")
        d.initialize()
        d.close()

        results = run_doctor(filigree_project)
        # Should have at least a few passing checks
        passed = [r for r in results if r.passed]
        assert len(passed) >= 3

    def test_missing_filigree_dir(self, tmp_path: Path) -> None:
        results = run_doctor(tmp_path)
        assert any(not r.passed and "directory" in r.name.lower() for r in results)
        # Should short-circuit — can't proceed
        assert len(results) == 1

    def test_stale_context_md(self, filigree_project: Path) -> None:
        summary_path = filigree_project / FILIGREE_DIR_NAME / SUMMARY_FILENAME
        # Set mtime to 2 hours ago
        old_time = time.time() - 7200
        os.utime(str(summary_path), (old_time, old_time))
        results = run_doctor(filigree_project)
        context_check = next((r for r in results if "context" in r.name.lower()), None)
        assert context_check is not None
        assert not context_check.passed
        assert "stale" in context_check.message.lower()

    def test_schema_version_check(self, filigree_project: Path) -> None:
        """Doctor should report schema version."""
        filigree_dir = filigree_project / FILIGREE_DIR_NAME
        d = FiligreeDB(filigree_dir / DB_FILENAME, prefix="proj")
        d.initialize()
        d.close()
        results = run_doctor(filigree_project)
        version_check = next((r for r in results if "schema" in r.name.lower()), None)
        assert version_check is not None
        assert version_check.passed

    def test_config_json_decode_error(self, filigree_project: Path) -> None:
        """Doctor should detect invalid config.json."""
        config_path = filigree_project / FILIGREE_DIR_NAME / CONFIG_FILENAME
        config_path.write_text("{invalid json!!!")
        results = run_doctor(filigree_project)
        config_check = next((r for r in results if r.name == "config.json"), None)
        assert config_check is not None
        assert not config_check.passed
        assert "Invalid JSON" in config_check.message

    def test_non_dict_mcp_json_does_not_crash(self, filigree_project: Path) -> None:
        """Doctor should handle .mcp.json containing a list instead of a dict."""
        mcp_path = filigree_project / ".mcp.json"
        mcp_path.write_text("[]")
        results = run_doctor(filigree_project)
        mcp_check = next((r for r in results if "Claude Code MCP" in r.name), None)
        assert mcp_check is not None
        assert not mcp_check.passed

    def test_missing_config_json(self, filigree_project: Path) -> None:
        """Doctor should detect missing config.json."""
        config_path = filigree_project / FILIGREE_DIR_NAME / CONFIG_FILENAME
        config_path.unlink()
        results = run_doctor(filigree_project)
        config_check = next((r for r in results if r.name == "config.json"), None)
        assert config_check is not None
        assert not config_check.passed
        assert "Missing" in config_check.message

    def test_missing_db(self, filigree_project: Path) -> None:
        """Doctor should detect missing filigree.db."""
        db_path = filigree_project / FILIGREE_DIR_NAME / DB_FILENAME
        db_path.unlink()
        results = run_doctor(filigree_project)
        db_check = next((r for r in results if r.name == "filigree.db"), None)
        assert db_check is not None
        assert not db_check.passed
        assert "Missing" in db_check.message

    def test_db_error(self, filigree_project: Path) -> None:
        """Doctor should detect corrupted db."""
        db_path = filigree_project / FILIGREE_DIR_NAME / DB_FILENAME
        # Overwrite with invalid data
        db_path.write_text("not a sqlite database")
        results = run_doctor(filigree_project)
        db_check = next((r for r in results if r.name == "filigree.db"), None)
        assert db_check is not None
        assert not db_check.passed
        assert "Database error" in db_check.message

    def test_missing_gitignore(self, filigree_project: Path) -> None:
        """Doctor should warn when .gitignore is missing."""
        results = run_doctor(filigree_project)
        gi_check = next((r for r in results if r.name == ".gitignore"), None)
        assert gi_check is not None
        assert not gi_check.passed
        assert "No .gitignore" in gi_check.message

    def test_gitignore_without_filigree(self, filigree_project: Path) -> None:
        """Doctor should warn when .gitignore doesn't include .filigree/."""
        (filigree_project / ".gitignore").write_text("*.pyc\n")
        results = run_doctor(filigree_project)
        gi_check = next((r for r in results if r.name == ".gitignore"), None)
        assert gi_check is not None
        assert not gi_check.passed
        assert ".filigree/ not in .gitignore" in gi_check.message

    def test_mcp_json_missing(self, filigree_project: Path) -> None:
        """Doctor should warn when .mcp.json is absent."""
        results = run_doctor(filigree_project)
        mcp_check = next((r for r in results if r.name == "Claude Code MCP"), None)
        assert mcp_check is not None
        assert not mcp_check.passed
        assert "No .mcp.json" in mcp_check.message

    def test_mcp_json_without_filigree(self, filigree_project: Path) -> None:
        """Doctor should warn when .mcp.json lacks filigree entry."""
        (filigree_project / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
        results = run_doctor(filigree_project)
        mcp_check = next((r for r in results if r.name == "Claude Code MCP"), None)
        assert mcp_check is not None
        assert not mcp_check.passed
        assert "filigree not in .mcp.json" in mcp_check.message

    def test_mcp_json_invalid(self, filigree_project: Path) -> None:
        """Doctor should warn when .mcp.json is invalid JSON."""
        (filigree_project / ".mcp.json").write_text("{bad json")
        results = run_doctor(filigree_project)
        mcp_check = next((r for r in results if r.name == "Claude Code MCP"), None)
        assert mcp_check is not None
        assert not mcp_check.passed
        assert "Invalid .mcp.json" in mcp_check.message

    def test_mcp_json_with_filigree(self, filigree_project: Path) -> None:
        """Doctor should pass when .mcp.json has filigree configured."""
        (filigree_project / ".mcp.json").write_text(json.dumps({"mcpServers": {"filigree": {"type": "stdio"}}}))
        results = run_doctor(filigree_project)
        mcp_check = next((r for r in results if r.name == "Claude Code MCP"), None)
        assert mcp_check is not None
        assert mcp_check.passed

    def test_codex_not_configured(self, filigree_project: Path) -> None:
        """Doctor should warn when .codex/config.toml is absent."""
        results = run_doctor(filigree_project)
        codex_check = next((r for r in results if r.name == "Codex MCP"), None)
        assert codex_check is not None
        assert not codex_check.passed

    def test_codex_configured(self, filigree_project: Path) -> None:
        """Doctor should pass when codex config has filigree."""
        codex_dir = filigree_project / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[mcp_servers.filigree]\ncommand = 'filigree-mcp'\n")
        results = run_doctor(filigree_project)
        codex_check = next((r for r in results if r.name == "Codex MCP"), None)
        assert codex_check is not None
        assert codex_check.passed

    def test_codex_without_filigree(self, filigree_project: Path) -> None:
        """Doctor should warn when codex config exists but lacks filigree."""
        codex_dir = filigree_project / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[mcp_servers.other]\n")
        results = run_doctor(filigree_project)
        codex_check = next((r for r in results if r.name == "Codex MCP"), None)
        assert codex_check is not None
        assert not codex_check.passed

    def test_claude_md_missing(self, filigree_project: Path) -> None:
        """Doctor should warn when CLAUDE.md is absent."""
        results = run_doctor(filigree_project)
        claude_check = next((r for r in results if r.name == "CLAUDE.md"), None)
        assert claude_check is not None
        assert not claude_check.passed
        assert "File not found" in claude_check.message

    def test_claude_md_without_instructions(self, filigree_project: Path) -> None:
        """Doctor should warn when CLAUDE.md exists but has no instructions."""
        (filigree_project / "CLAUDE.md").write_text("# My Project\n")
        results = run_doctor(filigree_project)
        claude_check = next((r for r in results if r.name == "CLAUDE.md"), None)
        assert claude_check is not None
        assert not claude_check.passed
        assert "No filigree instructions" in claude_check.message

    def test_claude_md_with_instructions(self, filigree_project: Path) -> None:
        """Doctor should pass when CLAUDE.md has filigree instructions."""
        (filigree_project / "CLAUDE.md").write_text(f"# Project\n{FILIGREE_INSTRUCTIONS_MARKER}\n")
        results = run_doctor(filigree_project)
        claude_check = next((r for r in results if r.name == "CLAUDE.md"), None)
        assert claude_check is not None
        assert claude_check.passed

    def test_agents_md_without_instructions(self, filigree_project: Path) -> None:
        """Doctor should warn when AGENTS.md exists but has no instructions."""
        (filigree_project / "AGENTS.md").write_text("# Agents\n")
        results = run_doctor(filigree_project)
        agents_check = next((r for r in results if r.name == "AGENTS.md"), None)
        assert agents_check is not None
        assert not agents_check.passed

    def test_agents_md_with_instructions(self, filigree_project: Path) -> None:
        """Doctor should pass when AGENTS.md has filigree instructions."""
        (filigree_project / "AGENTS.md").write_text(f"# Agents\n{FILIGREE_INSTRUCTIONS_MARKER}\n")
        results = run_doctor(filigree_project)
        agents_check = next((r for r in results if r.name == "AGENTS.md"), None)
        assert agents_check is not None
        assert agents_check.passed

    def test_missing_context_md(self, filigree_project: Path) -> None:
        """Doctor should warn when context.md is missing."""
        summary_path = filigree_project / FILIGREE_DIR_NAME / SUMMARY_FILENAME
        summary_path.unlink()
        results = run_doctor(filigree_project)
        ctx_check = next((r for r in results if "context" in r.name.lower()), None)
        assert ctx_check is not None
        assert not ctx_check.passed
        assert "Missing" in ctx_check.message

    def test_git_status_check(self, filigree_project: Path) -> None:
        """Doctor should check git working tree when available."""
        # This test runs in a tmp dir that is not a git repo, so git check
        # should either not appear or report an issue. Just verify no crash.
        results = run_doctor(filigree_project)
        # Should complete without error
        assert len(results) >= 5


class TestFindFiligreeMcpCommand:
    def test_found_on_path(self, tmp_path: Path) -> None:
        """When filigree-mcp is on PATH, return its path."""

        def _fake_which(name: str) -> str | None:
            return f"/usr/bin/{name}" if name == "filigree-mcp" else None

        with patch("filigree.install.shutil.which", side_effect=_fake_which):
            result = _find_filigree_mcp_command()
            assert result == "/usr/bin/filigree-mcp"

    def test_fallback_to_sys_executable_sibling(self, tmp_path: Path) -> None:
        """When filigree-mcp not on PATH, look next to sys.executable."""
        fake_python = tmp_path / "python3"
        fake_python.touch()
        mcp_bin = tmp_path / "filigree-mcp"
        mcp_bin.touch()

        with (
            patch("filigree.install.shutil.which", return_value=None),
            patch("filigree.install.sys.executable", str(fake_python)),
        ):
            result = _find_filigree_mcp_command()
            assert result == str(mcp_bin)

    def test_fallback_to_filigree_sibling(self, tmp_path: Path) -> None:
        """When filigree-mcp not on PATH or next to python, look next to filigree."""
        filigree_bin = tmp_path / "filigree"
        filigree_bin.touch()
        mcp_bin = tmp_path / "filigree-mcp"
        mcp_bin.touch()

        def fake_which(name: str) -> str | None:
            if name == "filigree":
                return str(filigree_bin)
            return None

        with (
            patch("filigree.install.shutil.which", side_effect=fake_which),
            patch("filigree.install.sys.executable", "/nonexistent/python3"),
        ):
            result = _find_filigree_mcp_command()
            assert result == str(mcp_bin)

    def test_default_fallback(self) -> None:
        """When nothing found, return 'filigree-mcp'."""
        with (
            patch("filigree.install.shutil.which", return_value=None),
            patch("filigree.install.sys.executable", "/nonexistent/python3"),
        ):
            result = _find_filigree_mcp_command()
            assert result == "filigree-mcp"


class TestFindFiligreeCommand:
    def test_found_on_path(self) -> None:
        """When filigree is on PATH, return single-element list."""
        with patch("filigree.core.shutil.which", return_value="/usr/local/bin/filigree"):
            result = find_filigree_command()
            assert result == ["/usr/local/bin/filigree"]

    def test_fallback_to_sys_executable_sibling(self, tmp_path: Path) -> None:
        """When filigree not on PATH, look next to sys.executable."""
        fake_python = tmp_path / "python3"
        fake_python.touch()
        sibling = tmp_path / "filigree"
        sibling.touch()

        with (
            patch("filigree.core.shutil.which", return_value=None),
            patch("filigree.core.sys.executable", str(fake_python)),
        ):
            result = find_filigree_command()
            assert result == [str(sibling)]

    def test_default_fallback(self) -> None:
        """When nothing found, return python -m filigree tokens."""
        with (
            patch("filigree.core.shutil.which", return_value=None),
            patch("filigree.core.sys.executable", "/nonexistent/python3"),
        ):
            result = find_filigree_command()
            assert result == ["/nonexistent/python3", "-m", "filigree"]


class TestInstallClaudeCodeMcp:
    def test_writes_mcp_json(self, tmp_path: Path) -> None:
        """Should write .mcp.json when claude CLI is not available."""
        with patch("filigree.install.shutil.which", return_value=None):
            ok, _msg = install_claude_code_mcp(tmp_path)
        assert ok
        mcp_json = tmp_path / ".mcp.json"
        assert mcp_json.exists()
        data = json.loads(mcp_json.read_text())
        assert "filigree" in data["mcpServers"]

    def test_merges_with_existing_mcp_json(self, tmp_path: Path) -> None:
        """Should preserve existing entries in .mcp.json."""
        existing = {"mcpServers": {"other_tool": {"type": "stdio"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(existing))
        with patch("filigree.install.shutil.which", return_value=None):
            ok, _msg = install_claude_code_mcp(tmp_path)
        assert ok
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "other_tool" in data["mcpServers"]
        assert "filigree" in data["mcpServers"]

    def test_handles_non_dict_mcp_json(self, tmp_path: Path) -> None:
        """Non-object .mcp.json should be backed up and reset, not crash."""
        (tmp_path / ".mcp.json").write_text("[]")
        with patch("filigree.install.shutil.which", return_value=None):
            ok, _msg = install_claude_code_mcp(tmp_path)
        assert ok
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "filigree" in data["mcpServers"]

    def test_handles_non_dict_mcp_servers(self, tmp_path: Path) -> None:
        """mcpServers as a list should be replaced with {}, not crash."""
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": []}))
        with patch("filigree.install.shutil.which", return_value=None):
            ok, _msg = install_claude_code_mcp(tmp_path)
        assert ok
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert isinstance(data["mcpServers"], dict)
        assert "filigree" in data["mcpServers"]

    def test_handles_string_mcp_servers(self, tmp_path: Path) -> None:
        """mcpServers as a string should be replaced with {}, not crash."""
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": "bad"}))
        with patch("filigree.install.shutil.which", return_value=None):
            ok, _msg = install_claude_code_mcp(tmp_path)
        assert ok
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert isinstance(data["mcpServers"], dict)
        assert "filigree" in data["mcpServers"]


class TestInstallCodexMcp:
    def test_creates_codex_config(self, tmp_path: Path) -> None:
        """Should create .codex/config.toml with filigree config."""
        with patch("filigree.install.shutil.which", return_value=None):
            ok, _msg = install_codex_mcp(tmp_path)
        assert ok
        config = (tmp_path / ".codex" / "config.toml").read_text()
        assert "[mcp_servers.filigree]" in config

    def test_already_configured(self, tmp_path: Path) -> None:
        """Should detect when filigree is already configured."""
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[mcp_servers.filigree]\ncommand = 'filigree-mcp'\n")
        with patch("filigree.install.shutil.which", return_value=None):
            ok, msg = install_codex_mcp(tmp_path)
        assert ok
        assert "Already configured" in msg

    def test_escapes_double_quotes_in_path(self, tmp_path: Path) -> None:
        """Paths with double quotes must produce valid TOML."""
        import tomllib

        # Use a project root whose name contains a double quote
        weird_root = tmp_path / 'proj"name'
        weird_root.mkdir()
        with patch("filigree.install.shutil.which", return_value=None):
            ok, _msg = install_codex_mcp(weird_root)
        assert ok
        config_text = (weird_root / ".codex" / "config.toml").read_text()
        # Must be parseable as valid TOML
        parsed = tomllib.loads(config_text)
        assert "filigree" in parsed["mcp_servers"]


class TestInstallCodexMcpMalformedToml:
    """Bug filigree-d6bbbf: install_codex_mcp must fail on malformed TOML, not silently append."""

    def test_malformed_toml_returns_false(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[broken\nthis is not valid toml")
        with patch("filigree.install.shutil.which", return_value=None):
            ok, msg = install_codex_mcp(tmp_path)
        assert not ok
        assert "malformed TOML" in msg

    def test_malformed_toml_does_not_modify_file(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        original = "[broken\nthis is not valid toml"
        (codex_dir / "config.toml").write_text(original)
        with patch("filigree.install.shutil.which", return_value=None):
            install_codex_mcp(tmp_path)
        assert (codex_dir / "config.toml").read_text() == original


class TestInstallClaudeCodeHooks:
    MOCK_TOKENS = ["/mock/venv/bin/filigree"]  # noqa: RUF012
    MOCK_BIN = "/mock/venv/bin/filigree"

    def test_creates_settings_json(self, tmp_path: Path) -> None:
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert "hooks" in data
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        assert any("session-context" in c and self.MOCK_BIN in c for c in cmds)

    def test_merges_with_existing_settings(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {"someOtherKey": True}
        (claude_dir / "settings.json").write_text(json.dumps(existing))
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        data = json.loads((claude_dir / "settings.json").read_text())
        assert data["someOtherKey"] is True
        assert "hooks" in data

    def test_idempotent(self, tmp_path: Path) -> None:
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            install_claude_code_hooks(tmp_path)
            install_claude_code_hooks(tmp_path)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        # session-context should appear exactly once
        session_cmds = [c for c in cmds if "session-context" in c]
        assert len(session_cmds) == 1

    def test_handles_corrupt_settings(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{corrupt json!!!")
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        # Backup should exist
        assert (claude_dir / "settings.json.bak").exists()

    def test_dashboard_hook_conditional(self, tmp_path: Path) -> None:
        """Dashboard hook is added only when dashboard extra is importable."""
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        # filigree.dashboard is available in this test env
        # so ensure-dashboard should be registered
        try:
            import filigree.dashboard  # noqa: F401

            assert any("ensure-dashboard" in c and self.MOCK_BIN in c for c in cmds)
        except ImportError:
            assert not any("ensure-dashboard" in c for c in cmds)

    def test_upgrades_bare_to_absolute(self, tmp_path: Path) -> None:
        """Bare hook commands should be upgraded to resolved versions."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {"type": "command", "command": "filigree session-context", "timeout": 5000},
                        ]
                    }
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            ok, msg = install_claude_code_hooks(tmp_path)
        assert ok
        assert "Upgraded" in msg or "Registered" in msg
        data = json.loads((claude_dir / "settings.json").read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        assert f"{self.MOCK_BIN} session-context" in cmds

    def test_upgrades_stale_absolute_path(self, tmp_path: Path) -> None:
        """Old absolute-path hooks should be updated to current binary path."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        old_bin = "/old/venv/bin/filigree"
        hooks_list = [
            {
                "type": "command",
                "command": f"{old_bin} session-context",
                "timeout": 5000,
            },
        ]
        # Include dashboard hook too (if dashboard extra is available)
        try:
            import filigree.dashboard  # noqa: F401

            hooks_list.append(
                {
                    "type": "command",
                    "command": f"{old_bin} ensure-dashboard",
                    "timeout": 5000,
                }
            )
        except ImportError:
            pass
        settings = {"hooks": {"SessionStart": [{"hooks": hooks_list}]}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            ok, msg = install_claude_code_hooks(tmp_path)
        assert ok
        assert "Upgraded" in msg
        data = json.loads((claude_dir / "settings.json").read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        assert f"{self.MOCK_BIN} session-context" in cmds
        assert f"{old_bin} session-context" not in cmds

    def test_upgrades_module_form_to_current(self, tmp_path: Path) -> None:
        """python -m filigree commands should be recognized and upgraded."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/old/python -m filigree session-context",
                                "timeout": 5000,
                            },
                        ]
                    }
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        data = json.loads((claude_dir / "settings.json").read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        assert f"{self.MOCK_BIN} session-context" in cmds
        assert "/old/python -m filigree session-context" not in cmds

    def test_spaces_in_path_properly_quoted(self, tmp_path: Path) -> None:
        """Paths with spaces must be shell-quoted so they round-trip correctly."""
        import shlex

        spaced_tokens = ["/path with spaces/python", "-m", "filigree"]
        with patch("filigree.install.find_filigree_command", return_value=spaced_tokens):
            ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        session_cmd = next(c for c in cmds if "session-context" in c)
        # shlex.split must recover the original tokens
        parsed = shlex.split(session_cmd)
        assert parsed[0] == "/path with spaces/python"
        assert parsed[1:] == ["-m", "filigree", "session-context"]


class TestHasHookCommand:
    """Tests for _has_hook_command with malformed JSON structures."""

    def test_hooks_as_list(self) -> None:
        """settings.hooks as a list should return False, not crash."""
        from filigree.install import _has_hook_command

        assert _has_hook_command({"hooks": []}, "filigree session-context") is False

    def test_hooks_as_string(self) -> None:
        """settings.hooks as a string should return False, not crash."""
        from filigree.install import _has_hook_command

        assert _has_hook_command({"hooks": "bad"}, "filigree session-context") is False

    def test_session_start_as_string(self) -> None:
        """hooks.SessionStart as a string should return False, not crash."""
        from filigree.install import _has_hook_command

        assert _has_hook_command({"hooks": {"SessionStart": "bad"}}, "filigree session-context") is False

    def test_matcher_as_string(self) -> None:
        """Non-dict matcher entries should be skipped, not crash."""
        from filigree.install import _has_hook_command

        assert _has_hook_command({"hooks": {"SessionStart": ["bad"]}}, "filigree session-context") is False

    def test_hook_entry_as_string(self) -> None:
        """Non-dict hook entries within a matcher should be skipped."""
        from filigree.install import _has_hook_command

        settings = {"hooks": {"SessionStart": [{"hooks": ["bad"]}]}}
        assert _has_hook_command(settings, "filigree session-context") is False

    def test_non_dict_settings(self) -> None:
        """Non-dict settings should return False, not crash."""
        assert _has_hook_command([], "filigree session-context") is False  # type: ignore[arg-type]

    def test_matches_absolute_path_form(self) -> None:
        """Should detect '/path/to/filigree session-context' as a match."""
        settings = {"hooks": {"SessionStart": [{"hooks": [{"command": "/usr/local/bin/filigree session-context"}]}]}}
        assert _has_hook_command(settings, "filigree session-context") is True

    def test_does_not_false_match_similar_command(self) -> None:
        """Should reject 'not-filigree session-context'."""
        settings = {"hooks": {"SessionStart": [{"hooks": [{"command": "not-filigree session-context"}]}]}}
        assert _has_hook_command(settings, "filigree session-context") is False

    def test_matches_module_form(self) -> None:
        """Should detect 'python -m filigree session-context' as a match."""
        settings = {"hooks": {"SessionStart": [{"hooks": [{"command": "/usr/bin/python3 -m filigree session-context"}]}]}}
        assert _has_hook_command(settings, "filigree session-context") is True

    def test_matches_quoted_path_with_spaces(self) -> None:
        """Should detect quoted paths containing spaces."""
        import shlex

        cmd = shlex.join(["/path with spaces/filigree"]) + " session-context"
        settings = {"hooks": {"SessionStart": [{"hooks": [{"command": cmd}]}]}}
        assert _has_hook_command(settings, "filigree session-context") is True

    def test_matches_module_form_with_spaces(self) -> None:
        """Should detect quoted python -m form with spaces in python path."""
        import shlex

        cmd = shlex.join(["/Program Files/python", "-m", "filigree"]) + " session-context"
        settings = {"hooks": {"SessionStart": [{"hooks": [{"command": cmd}]}]}}
        assert _has_hook_command(settings, "filigree session-context") is True


class TestInstallHooksMalformedStructure:
    """Tests for install_claude_code_hooks with malformed existing settings."""

    MOCK_TOKENS = ["/mock/venv/bin/filigree"]  # noqa: RUF012

    def test_hooks_key_is_list(self, tmp_path: Path) -> None:
        """Existing settings.hooks as a list should be replaced, not crash."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": []}))
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        data = json.loads((claude_dir / "settings.json").read_text())
        assert isinstance(data["hooks"], dict)

    def test_session_start_is_string(self, tmp_path: Path) -> None:
        """Existing hooks.SessionStart as a string should be replaced, not crash."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": "bad"}}))
        with patch("filigree.install.find_filigree_command", return_value=self.MOCK_TOKENS):
            ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        data = json.loads((claude_dir / "settings.json").read_text())
        assert isinstance(data["hooks"]["SessionStart"], list)


class TestDoctorMalformedHooks:
    """Tests for run_doctor with malformed hooks in settings.json."""

    def test_hooks_as_list(self, filigree_project: Path) -> None:
        """Doctor should not crash when settings.hooks is a list."""
        claude_dir = filigree_project / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": []}))
        results = run_doctor(filigree_project)
        hooks_check = next((r for r in results if r.name == "Claude Code hooks"), None)
        assert hooks_check is not None
        assert not hooks_check.passed

    def test_non_dict_settings_json(self, filigree_project: Path) -> None:
        """Doctor should not crash when settings.json is a list."""
        claude_dir = filigree_project / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "settings.json").write_text("[]")
        results = run_doctor(filigree_project)
        hooks_check = next((r for r in results if r.name == "Claude Code hooks"), None)
        assert hooks_check is not None
        assert not hooks_check.passed


class TestDoctorHooksCheck:
    def test_passes_when_hooks_registered(self, filigree_project: Path) -> None:
        # Use a real path that exists so the binary path check passes
        mock_bin = str(filigree_project / "filigree")
        (filigree_project / "filigree").touch()
        with patch("filigree.install.find_filigree_command", return_value=[mock_bin]):
            install_claude_code_hooks(filigree_project)
        results = run_doctor(filigree_project)
        hooks_check = next((r for r in results if r.name == "Claude Code hooks"), None)
        assert hooks_check is not None
        assert hooks_check.passed

    def test_fails_when_settings_missing(self, filigree_project: Path) -> None:
        results = run_doctor(filigree_project)
        hooks_check = next((r for r in results if r.name == "Claude Code hooks"), None)
        assert hooks_check is not None
        assert not hooks_check.passed
        assert "No .claude/settings.json" in hooks_check.message

    def test_fails_when_hooks_absent(self, filigree_project: Path) -> None:
        claude_dir = filigree_project / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {}}))
        results = run_doctor(filigree_project)
        hooks_check = next((r for r in results if r.name == "Claude Code hooks"), None)
        assert hooks_check is not None
        assert not hooks_check.passed
        assert "session-context hook not found" in hooks_check.message


class TestDoctorMcpPathValidation:
    def test_stale_mcp_binary_detected(self, filigree_project: Path) -> None:
        """Doctor should detect nonexistent absolute path in .mcp.json command."""
        mcp_config = {
            "mcpServers": {
                "filigree": {
                    "type": "stdio",
                    "command": "/nonexistent/venv/bin/filigree-mcp",
                    "args": ["--project", str(filigree_project)],
                }
            }
        }
        (filigree_project / ".mcp.json").write_text(json.dumps(mcp_config))
        results = run_doctor(filigree_project)
        mcp_check = next((r for r in results if r.name == "Claude Code MCP"), None)
        assert mcp_check is not None
        assert not mcp_check.passed
        assert "Binary not found" in mcp_check.message

    def test_valid_mcp_binary_passes(self, filigree_project: Path) -> None:
        """Doctor should pass when MCP binary path exists."""
        fake_bin = filigree_project / "filigree-mcp"
        fake_bin.touch()
        mcp_config = {
            "mcpServers": {
                "filigree": {
                    "type": "stdio",
                    "command": str(fake_bin),
                    "args": ["--project", str(filigree_project)],
                }
            }
        }
        (filigree_project / ".mcp.json").write_text(json.dumps(mcp_config))
        results = run_doctor(filigree_project)
        mcp_check = next((r for r in results if r.name == "Claude Code MCP"), None)
        assert mcp_check is not None
        assert mcp_check.passed


class TestDoctorHookPathValidation:
    def test_stale_hook_binary_detected(self, filigree_project: Path) -> None:
        """Doctor should detect nonexistent absolute path in hook command."""
        claude_dir = filigree_project / ".claude"
        claude_dir.mkdir(exist_ok=True)
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/nonexistent/venv/bin/filigree session-context",
                                "timeout": 5000,
                            }
                        ]
                    }
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        results = run_doctor(filigree_project)
        hooks_check = next((r for r in results if r.name == "Claude Code hooks"), None)
        assert hooks_check is not None
        assert not hooks_check.passed
        assert "Binary not found" in hooks_check.message

    def test_valid_hook_binary_passes(self, filigree_project: Path) -> None:
        """Doctor should pass when hook binary path exists."""
        fake_bin = filigree_project / "filigree"
        fake_bin.touch()
        claude_dir = filigree_project / ".claude"
        claude_dir.mkdir(exist_ok=True)
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{fake_bin} session-context",
                                "timeout": 5000,
                            }
                        ]
                    }
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        results = run_doctor(filigree_project)
        hooks_check = next((r for r in results if r.name == "Claude Code hooks"), None)
        assert hooks_check is not None
        assert hooks_check.passed

    def test_quoted_path_with_spaces_passes(self, filigree_project: Path) -> None:
        """Doctor should extract binary correctly from quoted paths with spaces."""
        import shlex

        spaced_dir = filigree_project / "path with spaces"
        spaced_dir.mkdir()
        fake_bin = spaced_dir / "filigree"
        fake_bin.touch()
        cmd = shlex.join([str(fake_bin)]) + " session-context"
        claude_dir = filigree_project / ".claude"
        claude_dir.mkdir(exist_ok=True)
        settings = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": cmd, "timeout": 5000}]}]}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        results = run_doctor(filigree_project)
        hooks_check = next((r for r in results if r.name == "Claude Code hooks"), None)
        assert hooks_check is not None
        assert hooks_check.passed

    def test_bare_command_hook_still_passes(self, filigree_project: Path) -> None:
        """Doctor should accept bare commands without path validation."""
        claude_dir = filigree_project / ".claude"
        claude_dir.mkdir(exist_ok=True)
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "filigree session-context",
                                "timeout": 5000,
                            }
                        ]
                    }
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        results = run_doctor(filigree_project)
        hooks_check = next((r for r in results if r.name == "Claude Code hooks"), None)
        assert hooks_check is not None
        assert hooks_check.passed


class TestInstallSkills:
    def test_installs_skill_pack(self, tmp_path: Path) -> None:
        ok, _msg = install_skills(tmp_path)
        assert ok
        skill_md = tmp_path / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
        assert skill_md.exists()
        content = skill_md.read_text()
        assert "filigree-workflow" in content

    def test_overwrites_on_reinstall(self, tmp_path: Path) -> None:
        """Re-install should overwrite existing skill (picks up upgrades)."""
        install_skills(tmp_path)
        skill_md = tmp_path / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
        skill_md.write_text("stale content")
        install_skills(tmp_path)
        assert "filigree-workflow" in skill_md.read_text()

    def test_preserves_other_skills(self, tmp_path: Path) -> None:
        """Installing filigree skill should not touch other skills."""
        other_skill = tmp_path / ".claude" / "skills" / "other-skill"
        other_skill.mkdir(parents=True)
        (other_skill / "SKILL.md").write_text("other")
        install_skills(tmp_path)
        assert (other_skill / "SKILL.md").read_text() == "other"

    def test_includes_references(self, tmp_path: Path) -> None:
        install_skills(tmp_path)
        refs = tmp_path / ".claude" / "skills" / SKILL_NAME / "references"
        assert refs.is_dir()
        assert (refs / "workflow-patterns.md").exists()
        assert (refs / "team-coordination.md").exists()

    def test_includes_examples(self, tmp_path: Path) -> None:
        install_skills(tmp_path)
        examples = tmp_path / ".claude" / "skills" / SKILL_NAME / "examples"
        assert examples.is_dir()
        assert (examples / "sprint-plan.json").exists()


class TestDoctorSkillsCheck:
    def test_passes_when_skill_installed(self, filigree_project: Path) -> None:
        install_skills(filigree_project)
        results = run_doctor(filigree_project)
        check = next((r for r in results if r.name == "Claude Code skills"), None)
        assert check is not None
        assert check.passed

    def test_fails_when_skill_missing(self, filigree_project: Path) -> None:
        results = run_doctor(filigree_project)
        check = next((r for r in results if r.name == "Claude Code skills"), None)
        assert check is not None
        assert not check.passed
        assert "not found" in check.message


class TestDoctorConnectionLeak:
    """Bug filigree-3bbc6f: run_doctor must close SQLite connection even on failure."""

    def test_connection_closed_on_db_error(self, filigree_project: Path) -> None:
        """If conn.execute() raises, conn.close() should still be called."""
        import sqlite3
        from unittest.mock import MagicMock

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("table issues has no column named x")

        def fake_connect(*args: object, **kwargs: object) -> MagicMock:
            return mock_conn

        with patch("filigree.install.sqlite3.connect", side_effect=fake_connect):
            results = run_doctor(filigree_project)

        # Connection should have been closed despite the error
        mock_conn.close.assert_called_once()
        # Should report the DB error, not crash
        db_check = next((r for r in results if r.name == "filigree.db"), None)
        assert db_check is not None
        assert not db_check.passed


class TestInstallMcpServerMode:
    def test_server_mode_writes_streamable_http(self, tmp_path: Path) -> None:
        project_root = tmp_path
        filigree_dir = project_root / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "mode": "server"}
        (filigree_dir / "config.json").write_text(json.dumps(config))

        ok, _msg = install_claude_code_mcp(project_root, mode="server", server_port=8377)
        assert ok
        mcp = json.loads((project_root / ".mcp.json").read_text())
        server_config = mcp["mcpServers"]["filigree"]
        assert server_config["type"] == "streamable-http"
        assert "8377" in server_config["url"]

    def test_ethereal_mode_writes_stdio(self, tmp_path: Path) -> None:
        project_root = tmp_path
        ok, _msg = install_claude_code_mcp(project_root, mode="ethereal")
        assert ok
        mcp = json.loads((project_root / ".mcp.json").read_text())
        server_config = mcp["mcpServers"]["filigree"]
        assert server_config.get("type") == "stdio" or "command" in server_config


class TestCheckResult:
    def test_passed_icon(self) -> None:
        r = CheckResult("test", True, "ok")
        assert r.icon == "OK"

    def test_failed_icon(self) -> None:
        r = CheckResult("test", False, "bad")
        assert r.icon == "!!"

    def test_fix_hint(self) -> None:
        r = CheckResult("test", False, "bad", fix_hint="Run: filigree init")
        assert r.fix_hint == "Run: filigree init"


def _setup_project(tmp_path: Path, mode: str = "ethereal") -> Path:
    """Helper to create a minimal filigree project."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "mode": mode}
    (filigree_dir / "config.json").write_text(json.dumps(config))
    from filigree.core import DB_FILENAME, FiligreeDB

    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix="test")
    db.initialize()
    db.close()
    return filigree_dir


class TestDoctorModeChecks:
    def test_ethereal_checks_pid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Doctor in ethereal mode should check ephemeral.pid."""
        filigree_dir = _setup_project(tmp_path, mode="ethereal")
        # Write a stale PID (JSON format)
        (filigree_dir / "ephemeral.pid").write_text(json.dumps({"pid": 99999999, "cmd": "filigree"}))
        monkeypatch.chdir(tmp_path)

        results = run_doctor(project_root=tmp_path)
        names = [r.name for r in results]
        assert "Ephemeral PID" in names
        pid_result = next(r for r in results if r.name == "Ephemeral PID")
        assert not pid_result.passed  # stale PID

    def test_server_checks_daemon(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Doctor in server mode should check daemon health."""
        _setup_project(tmp_path, mode="server")
        monkeypatch.chdir(tmp_path)

        config_dir = tmp_path / ".server-config"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        results = run_doctor(project_root=tmp_path)
        names = [r.name for r in results]
        assert "Server daemon" in names
        daemon_result = next(r for r in results if r.name == "Server daemon")
        assert not daemon_result.passed  # not running
