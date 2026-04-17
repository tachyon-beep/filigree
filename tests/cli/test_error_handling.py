"""CLI error handling regression tests.

Covers: filigree-8a7e6a (reopen exit code), filigree-537425 (create --json field error),
filigree-25daf4e886 (remove-label ValueError), filigree-565ff86495 (remove-dep WrongProjectError),
filigree-62c5b61f68 (refresh_summary OSError), filigree-3c4196854b (get_db corrupt conf).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.cli import cli
from tests.cli.conftest import _extract_id


class TestReopenExitCode:
    """CLI reopen must exit non-zero on error."""

    def test_reopen_nonexistent_exits_nonzero(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["reopen", "test-nonexistent"])
        assert result.exit_code != 0, f"Expected non-zero exit code, got {result.exit_code}"
        assert "Not found" in result.output

    def test_reopen_nonexistent_json_exits_nonzero(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["reopen", "test-nonexistent", "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert "errors" in data
        assert len(data["errors"]) == 1

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


class TestRemoveLabelValueError:
    """filigree-25daf4e886: remove-label must handle ValueError from label validation."""

    def test_reserved_namespace_text(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Target"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["remove-label", issue_id, "area:foo"])
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "area:" in result.output

    def test_reserved_namespace_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Target"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["remove-label", issue_id, "area:foo", "--json"])
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        data = json.loads(result.output)
        assert "error" in data


class TestRemoveDepWrongProjectError:
    """filigree-565ff86495: remove-dep must handle WrongProjectError (ValueError)."""

    def test_foreign_prefix_text(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Target"])
        issue_id = _extract_id(r.output)
        # Foreign prefix triggers WrongProjectError from _check_id_prefix
        result = runner.invoke(cli, ["remove-dep", issue_id, "foreign-abc1234567"])
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "wrong project" in result.output.lower() or "foreign" in result.output.lower() or "error" in result.output.lower()

    def test_foreign_prefix_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Target"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["remove-dep", issue_id, "foreign-abc1234567", "--json"])
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        data = json.loads(result.output)
        assert "error" in data


class TestRefreshSummaryOSError:
    """filigree-62c5b61f68: refresh_summary must not fail successful mutations on OSError."""

    def test_oserror_does_not_fail_successful_create(
        self,
        cli_in_project: tuple[CliRunner, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A disk/permission error during summary write must not fail the CLI."""
        runner, _ = cli_in_project

        def boom(db: object, path: object) -> None:
            raise OSError("simulated disk full")

        monkeypatch.setattr("filigree.cli_common.write_summary", boom)
        result = runner.invoke(cli, ["create", "After-failure create", "--json"])
        assert result.exit_code == 0, f"Expected success, got {result.exit_code}: {result.output}"
        # The mutation result JSON must be on stdout — parse without stripping tracebacks
        data = json.loads(result.output)
        assert "id" in data


class TestGetDbCorruptConf:
    """filigree-3c4196854b: get_db() must surface corrupt conf as a clean error."""

    def test_corrupt_conf_produces_clean_error(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        """A .filigree.conf that is not a JSON object must not crash raw."""
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            (tmp_path / ".filigree.conf").write_text('"not an object"')
            result = cli_runner.invoke(cli, ["stats"])
            assert result.exit_code != 0
            # Must be a clean click error, not an unhandled ValueError traceback
            assert result.exception is None or isinstance(result.exception, SystemExit), (
                f"Unhandled exception leaked: {type(result.exception).__name__}: {result.exception}"
            )
            assert "must be a JSON object" in result.output or "Error" in result.output
        finally:
            os.chdir(original_cwd)

    def test_conf_missing_keys_produces_clean_error(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        """A .filigree.conf missing required keys must not crash raw."""
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            (tmp_path / ".filigree.conf").write_text("{}")
            result = cli_runner.invoke(cli, ["stats"])
            assert result.exit_code != 0
            assert result.exception is None or isinstance(result.exception, SystemExit), (
                f"Unhandled exception leaked: {type(result.exception).__name__}: {result.exception}"
            )
        finally:
            os.chdir(original_cwd)
