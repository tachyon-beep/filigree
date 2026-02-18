"""Tests for install.py — instructions, gitignore, doctor."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from filigree.core import (
    CONFIG_FILENAME,
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
)
from filigree.install import (
    FILIGREE_INSTRUCTIONS_MARKER,
    SKILL_NAME,
    CheckResult,
    _find_filigree_mcp_command,
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
        assert content.count(FILIGREE_INSTRUCTIONS_MARKER) == 1

    def test_replace_malformed_block(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text(f"Before\n{FILIGREE_INSTRUCTIONS_MARKER}\nsome old stuff without end marker")
        ok, _msg = inject_instructions(target)
        assert ok
        content = target.read_text()
        assert "Before" in content
        assert "<!-- /filigree:instructions -->" in content


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

    def test_fallback_to_venv(self, tmp_path: Path) -> None:
        """When filigree-mcp not on PATH, look in same dir as filigree."""
        filigree_bin = tmp_path / "filigree"
        filigree_bin.touch()
        mcp_bin = tmp_path / "filigree-mcp"
        mcp_bin.touch()

        def fake_which(name: str) -> str | None:
            if name == "filigree":
                return str(filigree_bin)
            return None

        with patch("filigree.install.shutil.which", side_effect=fake_which):
            result = _find_filigree_mcp_command()
            assert result == str(mcp_bin)

    def test_default_fallback(self) -> None:
        """When nothing found, return 'filigree-mcp'."""
        with patch("filigree.install.shutil.which", return_value=None):
            result = _find_filigree_mcp_command()
            assert result == "filigree-mcp"


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


class TestInstallClaudeCodeHooks:
    def test_creates_settings_json(self, tmp_path: Path) -> None:
        ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert "hooks" in data
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        assert "filigree session-context" in cmds

    def test_merges_with_existing_settings(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {"someOtherKey": True}
        (claude_dir / "settings.json").write_text(json.dumps(existing))
        ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        data = json.loads((claude_dir / "settings.json").read_text())
        assert data["someOtherKey"] is True
        assert "hooks" in data

    def test_idempotent(self, tmp_path: Path) -> None:
        install_claude_code_hooks(tmp_path)
        install_claude_code_hooks(tmp_path)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        # Should appear exactly once
        assert cmds.count("filigree session-context") == 1

    def test_handles_corrupt_settings(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{corrupt json!!!")
        ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        # Backup should exist
        assert (claude_dir / "settings.json.bak").exists()

    def test_dashboard_hook_conditional(self, tmp_path: Path) -> None:
        """Dashboard hook is added only when dashboard extra is importable."""
        ok, _msg = install_claude_code_hooks(tmp_path)
        assert ok
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        # filigree.dashboard is available in this test env
        # so ensure-dashboard should be registered
        try:
            import filigree.dashboard  # noqa: F401

            assert "filigree ensure-dashboard" in cmds
        except ImportError:
            assert "filigree ensure-dashboard" not in cmds


class TestDoctorHooksCheck:
    def test_passes_when_hooks_registered(self, filigree_project: Path) -> None:
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
