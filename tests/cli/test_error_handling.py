"""CLI error handling regression tests.

Covers: filigree-8a7e6a (reopen exit code), filigree-537425 (create --json field error)
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from filigree.cli import cli
from tests.cli.conftest import _extract_id


class TestReopenExitCode:
    """CLI reopen must exit non-zero on error."""

    def test_reopen_nonexistent_exits_nonzero(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["reopen", "nonexistent-abc"])
        assert result.exit_code != 0, f"Expected non-zero exit code, got {result.exit_code}"
        assert "Not found" in result.output

    def test_reopen_nonexistent_json_exits_nonzero(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["reopen", "nonexistent-abc", "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output.splitlines()[0])
        assert "error" in data

    def test_reopen_open_issue_exits_nonzero(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Reopening an already-open issue should fail (ValueError path)."""
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Not closed"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["reopen", issue_id])
        assert result.exit_code != 0


class TestCreateJsonFieldError:
    """create --json must emit JSON errors for bad field format."""

    def test_create_bad_field_json_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Bad field", "-f", "no_equals_sign", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "Invalid field format" in data["error"]

    def test_create_bad_field_text_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Without --json, error goes to stderr as text."""
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Bad field", "-f", "no_equals_sign"])
        assert result.exit_code == 1
        assert "Invalid field format" in result.output
