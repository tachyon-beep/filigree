"""CLI tests for admin commands (init modes, install, doctor, server, export/import, JSON retrofit)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.cli import cli
from tests.cli.conftest import _extract_id


class TestOnboardingBreadcrumbs:
    def test_init_shows_next(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = cli_runner.invoke(cli, ["init"])
            assert "Next: filigree install" in result.output
        finally:
            os.chdir(original)

    def test_init_creates_scanners_dir(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = cli_runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert (tmp_path / ".filigree" / "scanners").is_dir()
        finally:
            os.chdir(original)

    def test_create_shows_next(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test"])
        assert "Next: filigree ready" in result.output


class TestActorFlag:
    def test_create_with_actor(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["--actor", "test-agent", "create", "Actor test"])
        assert r.exit_code == 0
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["show", issue_id, "--json"])
        data = json.loads(result.output)
        assert data["title"] == "Actor test"

    def test_comment_with_actor(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Commentable"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["--actor", "bot-1", "add-comment", issue_id, "Hello"])
        result = runner.invoke(cli, ["get-comments", issue_id])
        assert "bot-1" in result.output

    def test_default_actor_is_cli(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Default actor"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["add-comment", issue_id, "Default"])
        result = runner.invoke(cli, ["get-comments", issue_id])
        assert "cli" in result.output


class TestJsonRetrofit:
    def test_create_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "JSON create", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["title"] == "JSON create"
        assert "id" in data

    def test_close_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Close JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["close", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "closed" in data
        assert "unblocked" in data
        assert data["closed"][0]["id"] == issue_id

    def test_reopen_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Reopen JSON"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["close", issue_id])
        result = runner.invoke(cli, ["reopen", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_comment_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Comment JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["add-comment", issue_id, "My comment", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "comment_id" in data
        assert data["issue_id"] == issue_id

    def test_comments_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Comments JSON"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["add-comment", issue_id, "A comment"])
        result = runner.invoke(cli, ["get-comments", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_dep_add_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)
        result = runner.invoke(cli, ["add-dep", id1, id2, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "added"

    def test_dep_remove_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)
        runner.invoke(cli, ["add-dep", id1, id2])
        result = runner.invoke(cli, ["remove-dep", id1, id2, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "removed"

    def test_workflow_states_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["workflow-states", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "open" in data
        assert "wip" in data
        assert "done" in data

    def test_undo_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Undo JSON"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["update", issue_id, "--title", "Changed"])
        result = runner.invoke(cli, ["undo", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["undone"] is True

    def test_guide_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["guide", "core", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "pack" in data
        assert "guide" in data

    def test_archive_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["archive", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "archived" in data
        assert "count" in data

    def test_archive_rejects_negative_days(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["archive", "--days", "-1"])
        assert result.exit_code != 0
        assert "Invalid value for '--days'" in result.output

    def test_compact_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["compact", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "deleted_events" in data

    def test_compact_rejects_negative_keep(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["compact", "--keep", "-1"])
        assert result.exit_code != 0
        assert "Invalid value for '--keep'" in result.output

    def test_clean_stale_findings_rejects_negative_days(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["clean-stale-findings", "--days", "-1"])
        assert result.exit_code != 0
        assert "Invalid value for '--days'" in result.output

    def test_label_add_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Label JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["add-label", issue_id, "urgent", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "added"

    def test_label_remove_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Label JSON", "-l", "urgent"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["remove-label", issue_id, "urgent", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "removed"


class TestInstallCli:
    def test_install_all(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["install"])
        assert result.exit_code == 0
        assert "installed successfully" in result.output

    def test_install_gitignore_only(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["install", "--gitignore"])
        assert result.exit_code == 0
        assert ".gitignore" in result.output

    def test_install_claude_md_only(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["install", "--claude-md"])
        assert result.exit_code == 0
        assert "CLAUDE.md" in result.output

    def test_install_codex_skills_flag(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, project = cli_in_project
        result = runner.invoke(cli, ["install", "--codex-skills"])
        assert result.exit_code == 0, result.output
        assert "Codex skills" in result.output
        skill_md = project / ".agents" / "skills" / "filigree-workflow" / "SKILL.md"
        assert skill_md.exists()


class TestDoctorCli:
    def test_doctor_basic(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "filigree doctor" in result.output

    def test_doctor_verbose(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["doctor", "--verbose"])
        assert result.exit_code == 0
        # Verbose should show all checks including passed ones
        assert "OK" in result.output

    def test_doctor_fix(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["doctor", "--fix"])
        assert result.exit_code == 0


class TestShowDetailedOutput:
    """Cover the human-readable show output branches."""

    def test_show_with_description_and_notes(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(
            cli,
            [
                "create",
                "Detailed issue",
                "-d",
                "A detailed description",
                "--notes",
                "Some notes",
                "-l",
                "backend",
            ],
        )
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["show", issue_id])
        assert result.exit_code == 0
        assert "Description" in result.output
        assert "A detailed description" in result.output
        assert "Notes" in result.output
        assert "Some notes" in result.output
        assert "backend" in result.output

    def test_show_with_fields(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Field issue", "-f", "severity=high"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["show", issue_id])
        assert result.exit_code == 0
        assert "Fields" in result.output
        assert "severity" in result.output

    def test_show_ready_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Ready issue"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["show", issue_id])
        assert result.exit_code == 0
        assert "Ready" in result.output

    def test_show_blocked_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "Blocked"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "Blocker"])
        id2 = _extract_id(r2.output)
        runner.invoke(cli, ["add-dep", id1, id2])
        result = runner.invoke(cli, ["show", id1])
        assert result.exit_code == 0
        assert "Blocked by" in result.output

    def test_show_with_parent_and_children(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r_parent = runner.invoke(cli, ["create", "Parent", "--type", "epic"])
        parent_id = _extract_id(r_parent.output)
        r_child = runner.invoke(cli, ["create", "Child", "--parent", parent_id])
        child_id = _extract_id(r_child.output)
        # Show child to see parent
        result = runner.invoke(cli, ["show", child_id])
        assert result.exit_code == 0
        assert "Parent" in result.output
        # Show parent to see children
        result = runner.invoke(cli, ["show", parent_id])
        assert result.exit_code == 0
        assert "Children" in result.output

    def test_show_closed_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Will close"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["close", issue_id])
        result = runner.invoke(cli, ["show", issue_id])
        assert result.exit_code == 0
        assert "Closed" in result.output

    def test_show_with_assignee(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Assigned"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-1"])
        result = runner.invoke(cli, ["show", issue_id])
        assert result.exit_code == 0
        assert "Assignee" in result.output
        assert "agent-1" in result.output


class TestUpdateEdgeCases:
    def test_update_with_json_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "JSON update"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["update", issue_id, "--title", "New title", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["title"] == "New title"

    def test_update_invalid_field_format(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Field test"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["update", issue_id, "-f", "badformat"])
        assert result.exit_code == 1

    def test_update_invalid_field_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Field test"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["update", issue_id, "-f", "badformat", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data

    def test_update_with_design_field(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Design test"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["update", issue_id, "--design", "Use pattern X"])
        assert result.exit_code == 0

    def test_update_not_found_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["update", "nonexistent-abc", "--title", "nope", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data

    def test_update_invalid_status_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Status test"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["update", issue_id, "--status", "bogus_state", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data


class TestInitMode:
    def test_init_default_mode_is_ethereal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "ethereal"

    def test_init_with_server_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(cli, ["init", "--mode", "server"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "server"

    def test_init_with_explicit_ethereal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(cli, ["init", "--mode", "ethereal"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "ethereal"

    def test_init_invalid_mode_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(cli, ["init", "--mode", "bogus"])
        assert result.exit_code != 0

    def test_init_existing_project_updates_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        """Running init --mode=server on an existing project updates the mode."""
        monkeypatch.chdir(tmp_path)
        cli_runner.invoke(cli, ["init"])
        result = cli_runner.invoke(cli, ["init", "--mode", "server"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "server"

    def test_init_invalid_mode_no_directory_created(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(cli, ["init", "--mode", "bogus"])
        assert result.exit_code != 0
        assert not (tmp_path / ".filigree").exists()


class TestInstallMode:
    def test_install_writes_mode_to_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        """install --mode=server persists the mode to config.json."""
        monkeypatch.chdir(tmp_path)
        # Set up a minimal project
        cli_runner.invoke(cli, ["init"])
        result = cli_runner.invoke(cli, ["install", "--mode", "server"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "server"

    def test_install_preserves_existing_mode_when_no_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
    ) -> None:
        """install without --mode keeps the existing mode."""
        monkeypatch.chdir(tmp_path)
        cli_runner.invoke(cli, ["init", "--mode", "server"])
        result = cli_runner.invoke(cli, ["install"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "server"


class TestInstallModeIntegration:
    def test_install_server_mode_registers_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        monkeypatch.chdir(tmp_path)
        cli_runner.invoke(cli, ["init"])

        config_dir = tmp_path / ".server-config"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        result = cli_runner.invoke(cli, ["install", "--mode", "server"])
        assert result.exit_code == 0

        from filigree.server import read_server_config

        sc = read_server_config()
        assert len(sc.projects) == 1

    def test_install_ethereal_mode_does_not_register(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        monkeypatch.chdir(tmp_path)
        cli_runner.invoke(cli, ["init"])

        config_dir = tmp_path / ".server-config"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        result = cli_runner.invoke(cli, ["install", "--mode", "ethereal"])
        assert result.exit_code == 0

        from filigree.server import read_server_config

        sc = read_server_config()
        assert len(sc.projects) == 0

    def test_install_server_mode_passes_mode_to_mcp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner) -> None:
        monkeypatch.chdir(tmp_path)
        cli_runner.invoke(cli, ["init"])

        config_dir = tmp_path / ".server-config"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        result = cli_runner.invoke(cli, ["install", "--mode", "server"])
        assert result.exit_code == 0
        assert "Server registration" in result.output

    def test_install_server_mode_uses_configured_server_port(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cli_runner.invoke(cli, ["init"])

        config_dir = tmp_path / ".server-config"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        from filigree.server import ServerConfig, write_server_config

        write_server_config(ServerConfig(port=9911))
        result = cli_runner.invoke(cli, ["install", "--mode", "server"])
        assert result.exit_code == 0

        mcp = json.loads((tmp_path / ".mcp.json").read_text())
        prefix = json.loads((tmp_path / ".filigree" / "config.json").read_text())["prefix"]
        assert mcp["mcpServers"]["filigree"]["type"] == "streamable-http"
        assert mcp["mcpServers"]["filigree"]["url"] == f"http://localhost:9911/mcp/?project={prefix}"


class TestServerRegisterReload:
    def test_server_register_reloads_running_daemon(self, cli_in_project: tuple[CliRunner, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        runner, _ = cli_in_project

        from filigree.server import DaemonStatus

        observed: dict[str, object] = {}

        def _register(filigree_dir: Path) -> None:
            observed["registered"] = str(filigree_dir)

        class _Resp:
            status = 200

            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                return False

        def _urlopen(req: object, timeout: int = 0) -> _Resp:
            observed["reload_url"] = getattr(req, "full_url", "")
            observed["reload_timeout"] = timeout
            return _Resp()

        monkeypatch.setattr("filigree.server.register_project", _register)
        monkeypatch.setattr("filigree.server.daemon_status", lambda: DaemonStatus(running=True, pid=123, port=9911, project_count=1))
        monkeypatch.setattr("urllib.request.urlopen", _urlopen)

        result = runner.invoke(cli, ["server", "register", "."])
        assert result.exit_code == 0
        assert "Registered" in result.output
        assert "Reloaded running daemon" in result.output
        assert observed["reload_url"] == "http://127.0.0.1:9911/api/reload"

    def test_server_unregister_reloads_running_daemon(
        self, cli_in_project: tuple[CliRunner, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, _ = cli_in_project

        from filigree.server import DaemonStatus

        observed: dict[str, object] = {}

        def _unregister(filigree_dir: Path) -> None:
            observed["unregistered"] = str(filigree_dir)

        class _Resp:
            status = 200

            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                return False

        def _urlopen(req: object, timeout: int = 0) -> _Resp:
            observed["reload_url"] = getattr(req, "full_url", "")
            observed["reload_timeout"] = timeout
            return _Resp()

        monkeypatch.setattr("filigree.server.unregister_project", _unregister)
        monkeypatch.setattr("filigree.server.daemon_status", lambda: DaemonStatus(running=True, pid=123, port=9911, project_count=1))
        monkeypatch.setattr("urllib.request.urlopen", _urlopen)

        result = runner.invoke(cli, ["server", "unregister", "."])
        assert result.exit_code == 0
        assert "Unregistered" in result.output
        assert "Reloaded running daemon" in result.output
        assert observed["reload_url"] == "http://127.0.0.1:9911/api/reload"

    def test_server_register_skips_reload_when_daemon_not_running(
        self, cli_in_project: tuple[CliRunner, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, _ = cli_in_project

        from filigree.server import DaemonStatus

        monkeypatch.setattr("filigree.server.register_project", lambda _p: None)
        monkeypatch.setattr("filigree.server.daemon_status", lambda: DaemonStatus(running=False))

        result = runner.invoke(cli, ["server", "register", "."])
        assert result.exit_code == 0
        assert "Registered" in result.output
        assert "Reloaded running daemon" not in result.output


class TestDashboardServerModePidTracking:
    def test_dashboard_server_mode_claims_pid_for_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
    ) -> None:
        config_dir = tmp_path / ".server-config"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")
        # The test process is pytest, not filigree — stub ownership check so
        # PID tracking logic (the real subject under test) isn't blocked.
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)

        observed: dict[str, object] = {}

        def _fake_dashboard_main(port: int, no_browser: bool, server_mode: bool) -> None:
            from filigree.server import SERVER_PID_FILE, daemon_status

            status = daemon_status()
            observed["port_arg"] = port
            observed["no_browser_arg"] = no_browser
            observed["server_mode_arg"] = server_mode
            observed["status_running"] = status.running
            observed["status_port"] = status.port
            observed["pid_file_exists_during_run"] = SERVER_PID_FILE.exists()

        monkeypatch.setattr("filigree.dashboard.main", _fake_dashboard_main)

        result = cli_runner.invoke(cli, ["dashboard", "--server-mode", "--no-browser", "--port", "9911"])
        assert result.exit_code == 0
        assert observed["port_arg"] == 9911
        assert observed["no_browser_arg"] is True
        assert observed["server_mode_arg"] is True
        assert observed["status_running"] is True
        assert observed["status_port"] == 9911
        assert observed["pid_file_exists_during_run"] is True
        assert not (config_dir / "server.pid").exists()

    def test_dashboard_server_mode_does_not_override_live_tracked_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
    ) -> None:
        config_dir = tmp_path / ".server-config"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text('{"pid": 54321, "cmd": "filigree"}')

        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)
        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: pid == 54321)
        # Stub ownership so the claim path respects the existing live PID
        # without doing real OS process inspection on the fake PID.
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)

        observed: dict[str, object] = {}

        def _fake_dashboard_main(port: int, no_browser: bool, server_mode: bool) -> None:
            from filigree.server import daemon_status

            status = daemon_status()
            observed["status_running"] = status.running
            observed["status_pid"] = status.pid

        monkeypatch.setattr("filigree.dashboard.main", _fake_dashboard_main)

        result = cli_runner.invoke(cli, ["dashboard", "--server-mode", "--no-browser"])
        assert result.exit_code == 0
        assert observed["status_running"] is True
        assert observed["status_pid"] == 54321
        assert json.loads(pid_file.read_text())["pid"] == 54321


class TestNoFiligreeDir:
    def test_commands_fail_without_init(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = cli_runner.invoke(cli, ["list"])
            assert result.exit_code == 1
            assert "filigree init" in result.output.lower()
        finally:
            os.chdir(original)


class TestExportImportCli:
    def test_export_import(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, project_root = cli_in_project
        runner.invoke(cli, ["create", "Export me"])
        export_path = str(project_root / "export.jsonl")
        result = runner.invoke(cli, ["export", export_path])
        assert result.exit_code == 0
        assert "Exported" in result.output

    def test_import_merge(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, project_root = cli_in_project
        runner.invoke(cli, ["create", "Export me"])
        export_path = str(project_root / "export.jsonl")
        runner.invoke(cli, ["export", export_path])
        result = runner.invoke(cli, ["import", export_path, "--merge"])
        assert result.exit_code == 0
        assert "Imported" in result.output

    def test_import_conflict_without_merge_shows_clean_error(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Import without --merge on duplicate data should show clean error, not traceback."""
        runner, project_root = cli_in_project
        runner.invoke(cli, ["create", "Conflict me"])
        export_path = str(project_root / "export.jsonl")
        runner.invoke(cli, ["export", export_path])
        # Import same data again without --merge → should fail cleanly
        result = runner.invoke(cli, ["import", export_path])
        assert result.exit_code != 0
        assert "Import failed" in result.output
        # Must NOT contain a raw Python traceback
        assert "Traceback" not in (result.output or "")

    def test_export_empty_db(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, project_root = cli_in_project
        export_path = str(project_root / "empty.jsonl")
        result = runner.invoke(cli, ["export", export_path])
        assert result.exit_code == 0
        assert "0 records" in result.output

    def test_import_oserror_shows_clean_error(self, cli_in_project: tuple[CliRunner, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError during import should show clean error, not traceback."""
        runner, project_root = cli_in_project
        bad_file = project_root / "data.jsonl"
        bad_file.write_text("{}\n")

        def _raise_oserror(*a: object, **kw: object) -> None:
            raise OSError("disk read error")

        monkeypatch.setattr("filigree.core.FiligreeDB.import_jsonl", _raise_oserror)
        result = runner.invoke(cli, ["import", str(bad_file)])
        assert result.exit_code != 0
        assert "Import failed" in (result.output or "")
        assert "Traceback" not in (result.output or "")
