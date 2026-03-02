"""CLI boundary validation tests for priority and actor."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from filigree.cli import cli
from tests.cli.conftest import _extract_id


class TestCLIPriorityValidation:
    """click.IntRange(0, 4) on all priority options."""

    def test_create_priority_too_high(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test", "--priority", "5"])
        assert result.exit_code != 0

    def test_create_priority_too_low(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test", "--priority", "-1"])
        assert result.exit_code != 0

    def test_create_priority_boundary_0(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test", "--priority", "0"])
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_create_priority_boundary_4(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test", "--priority", "4"])
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_list_priority_filter_too_high(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["list", "--priority", "5"])
        assert result.exit_code != 0

    def test_update_priority_too_high(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        create_result = runner.invoke(cli, ["create", "Target"])
        assert create_result.exit_code == 0
        issue_id = _extract_id(create_result.output)
        result = runner.invoke(cli, ["update", issue_id, "--priority", "5"])
        assert result.exit_code != 0

    def test_claim_next_priority_min_too_low(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["claim-next", "--assignee", "bot", "--priority-min", "-1"])
        assert result.exit_code != 0

    def test_claim_next_priority_max_too_high(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["claim-next", "--assignee", "bot", "--priority-max", "5"])
        assert result.exit_code != 0

    def test_batch_update_priority_too_high(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        create_result = runner.invoke(cli, ["create", "Target"])
        issue_id = _extract_id(create_result.output)
        result = runner.invoke(cli, ["batch-update", issue_id, "--priority", "5"])
        assert result.exit_code != 0


class TestCLIActorValidation:
    """Actor validation in CLI group callback."""

    def test_empty_actor(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["--actor", "", "create", "Test"])
        assert result.exit_code != 0

    def test_control_char_actor(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["--actor", "\x00bad", "create", "Test"])
        assert result.exit_code != 0

    def test_valid_actor(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["--actor", "my-bot", "create", "Test"])
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_default_actor_cli(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Default actor 'cli' should pass validation."""
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test"])
        assert result.exit_code == 0
