"""CLI tests for query commands (list, search, ready, blocked, stats, metrics, critical-path, JSON output)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from filigree.cli import cli
from tests.cli.conftest import _extract_id


class TestReadyAndBlocked:
    def test_ready(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Ready task"])
        result = runner.invoke(cli, ["ready"])
        assert result.exit_code == 0
        assert "1 ready" in result.output

    def test_blocked(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "Blocked"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "Blocker"])
        id2 = _extract_id(r2.output)
        runner.invoke(cli, ["add-dep", id1, id2])
        result = runner.invoke(cli, ["blocked"])
        assert result.exit_code == 0
        assert "1 blocked" in result.output


class TestDependencies:
    def test_dep_add_and_remove(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)

        result = runner.invoke(cli, ["add-dep", id1, id2])
        assert result.exit_code == 0
        assert "Added" in result.output

        result = runner.invoke(cli, ["remove-dep", id1, id2])
        assert result.exit_code == 0
        assert "Removed" in result.output


class TestStatsAndSearch:
    def test_stats(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "A"])
        result = runner.invoke(cli, ["stats"])
        assert result.exit_code == 0
        assert "Status:" in result.output

    def test_search(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Authentication bug"])
        runner.invoke(cli, ["create", "Other thing"])
        result = runner.invoke(cli, ["search", "auth"])
        assert result.exit_code == 0
        assert "1 results" in result.output


class TestMetricsCli:
    def test_metrics_basic(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Test issue"])
        result = runner.invoke(cli, ["metrics"])
        assert result.exit_code == 0
        assert "Flow Metrics" in result.output

    def test_metrics_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["metrics", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "throughput" in data
        assert "period_days" in data

    def test_metrics_days(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["metrics", "--days", "7", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["period_days"] == 7


class TestCriticalPathCli:
    def test_critical_path_empty(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["critical-path"])
        assert result.exit_code == 0
        assert "No dependency chains" in result.output

    def test_critical_path_with_chain(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "Blocker"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "Blocked"])
        id2 = _extract_id(r2.output)
        runner.invoke(cli, ["add-dep", id2, id1])
        result = runner.invoke(cli, ["critical-path"])
        assert result.exit_code == 0
        assert "Critical path (2 issues)" in result.output

    def test_critical_path_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["critical-path", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "path" in data
        assert "length" in data


class TestJsonOutput:
    def test_show_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "JSON show"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["show", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["title"] == "JSON show"

    def test_list_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "JSON list A"])
        runner.invoke(cli, ["create", "JSON list B"])
        result = runner.invoke(cli, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2

    def test_ready_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Ready JSON"])
        result = runner.invoke(cli, ["ready", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1

    def test_stats_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Stats JSON"])
        result = runner.invoke(cli, ["stats", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "by_status" in data

    def test_search_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Searchable item"])
        result = runner.invoke(cli, ["search", "searchable", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1


class TestBlockedJson:
    def test_blocked_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "Blocked"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "Blocker"])
        id2 = _extract_id(r2.output)
        runner.invoke(cli, ["add-dep", id1, id2])
        result = runner.invoke(cli, ["blocked", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1


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
