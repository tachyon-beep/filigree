"""CLI integration tests using Click's CliRunner."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.cli import cli
from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, read_config


@pytest.fixture
def cli_in_project(tmp_path: Path, cli_runner: CliRunner) -> tuple[CliRunner, Path]:
    """Initialize a filigree project in tmp_path and return (runner, project_root)."""
    original_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    result = cli_runner.invoke(cli, ["init", "--prefix", "test"])
    assert result.exit_code == 0
    yield cli_runner, tmp_path
    os.chdir(original_cwd)


class TestInit:
    def test_init_creates_filigree_dir(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = cli_runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert (tmp_path / FILIGREE_DIR_NAME).is_dir()
            assert (tmp_path / FILIGREE_DIR_NAME / DB_FILENAME).exists()
        finally:
            os.chdir(original)

    def test_init_with_prefix(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = cli_runner.invoke(cli, ["init", "--prefix", "myproj"])
            assert result.exit_code == 0
            config = read_config(tmp_path / FILIGREE_DIR_NAME)
            assert config["prefix"] == "myproj"
        finally:
            os.chdir(original)

    def test_init_already_exists(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "already exists" in result.output


class TestCreate:
    def test_create_basic(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Fix the bug"])
        assert result.exit_code == 0
        assert "Created" in result.output
        assert "Fix the bug" in result.output

    def test_create_with_options(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(
            cli,
            [
                "create",
                "New feature",
                "--type",
                "feature",
                "-p",
                "1",
                "-d",
                "A description",
                "--notes",
                "Some notes",
                "-l",
                "backend",
                "-l",
                "urgent",
            ],
        )
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_create_with_field(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "With field", "-f", "severity=major"])
        assert result.exit_code == 0

    def test_create_invalid_field(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Bad field", "-f", "no_equals_sign"])
        assert result.exit_code == 1


class TestShowAndList:
    def test_show_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Show me"])
        issue_id = result.output.split(":")[0].replace("Created ", "").strip()
        result = runner.invoke(cli, ["show", issue_id])
        assert result.exit_code == 0
        assert "Show me" in result.output

    def test_show_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["show", "nonexistent-abc"])
        assert result.exit_code == 1

    def test_list_all(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A"])
        runner.invoke(cli, ["create", "Issue B"])
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "2 issues" in result.output

    def test_list_filter_status(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Open one"])
        r = runner.invoke(cli, ["create", "Close one"])
        issue_id = r.output.split(":")[0].replace("Created ", "").strip()
        runner.invoke(cli, ["close", issue_id])
        result = runner.invoke(cli, ["list", "--status", "open"])
        assert "1 issues" in result.output


class TestUpdateAndClose:
    def test_update_status(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Update me"])
        issue_id = r.output.split(":")[0].replace("Created ", "").strip()
        result = runner.invoke(cli, ["update", issue_id, "--status", "in_progress"])
        assert result.exit_code == 0
        assert "in_progress" in result.output

    def test_update_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["update", "nonexistent-abc", "--title", "nope"])
        assert result.exit_code == 1

    def test_close_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Close me"])
        issue_id = r.output.split(":")[0].replace("Created ", "").strip()
        result = runner.invoke(cli, ["close", issue_id])
        assert result.exit_code == 0
        assert "Closed" in result.output

    def test_close_with_reason(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Close with reason"])
        issue_id = r.output.split(":")[0].replace("Created ", "").strip()
        result = runner.invoke(cli, ["close", issue_id, "--reason", "done"])
        assert result.exit_code == 0

    def test_close_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["close", "nonexistent-abc"])
        assert result.exit_code == 1
        assert "Not found" in result.output


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
        id1 = r1.output.split(":")[0].replace("Created ", "").strip()
        r2 = runner.invoke(cli, ["create", "Blocker"])
        id2 = r2.output.split(":")[0].replace("Created ", "").strip()
        runner.invoke(cli, ["add-dep", id1, id2])
        result = runner.invoke(cli, ["blocked"])
        assert result.exit_code == 0
        assert "1 blocked" in result.output


class TestDependencies:
    def test_dep_add_and_remove(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = r1.output.split(":")[0].replace("Created ", "").strip()
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = r2.output.split(":")[0].replace("Created ", "").strip()

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


def _extract_id(create_output: str) -> str:
    """Extract issue ID from 'Created test-abc123: Title' output."""
    return create_output.split(":")[0].replace("Created ", "").strip()


class TestReopen:
    def test_reopen_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Reopen me"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["close", issue_id])
        result = runner.invoke(cli, ["reopen", issue_id])
        assert result.exit_code == 0
        assert "Reopened" in result.output

    def test_reopen_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["reopen", "nonexistent-abc"])
        assert "Not found" in result.output


class TestCommentsCli:
    def test_add_comment(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Commentable"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["add-comment", issue_id, "My comment"])
        assert result.exit_code == 0
        assert "Added comment" in result.output

    def test_list_comments(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Commentable"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["add-comment", issue_id, "First comment"])
        runner.invoke(cli, ["add-comment", issue_id, "Second comment"])
        result = runner.invoke(cli, ["get-comments", issue_id])
        assert result.exit_code == 0
        assert "First comment" in result.output
        assert "Second comment" in result.output

    def test_comment_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["add-comment", "nonexistent-abc", "text"])
        assert result.exit_code == 1

    def test_comments_empty(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "No comments"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["get-comments", issue_id])
        assert result.exit_code == 0
        assert "No comments" in result.output


class TestLabelCli:
    def test_label_add(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Label me"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["add-label", issue_id, "urgent"])
        assert result.exit_code == 0
        assert "Added label" in result.output

    def test_label_remove(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Label me", "-l", "urgent"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["remove-label", issue_id, "urgent"])
        assert result.exit_code == 0
        assert "Removed label" in result.output

    def test_label_add_rejects_reserved_type_name(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Label me"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["add-label", issue_id, "bug"])
        assert result.exit_code == 1
        assert "reserved as an issue type" in result.output

    def test_label_add_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["add-label", "nonexistent-abc", "bug"])
        assert result.exit_code == 1


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
        id1 = r1.output.split(":")[0].strip().split()[-1]
        r2 = runner.invoke(cli, ["create", "Blocked"])
        id2 = r2.output.split(":")[0].strip().split()[-1]
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


class TestWorkflowCli:
    """Tests for workflow template CLI commands (types, type-info, transitions, packs, validate, guide)."""

    def test_types_lists_registered_types(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["types"])
        assert result.exit_code == 0
        # Core pack types should appear
        assert "task" in result.output
        assert "bug" in result.output

    def test_types_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["types", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        type_names = {t["type"] for t in data}
        assert "task" in type_names
        assert all("states" in t for t in data)

    def test_type_info_shows_workflow(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["type-info", "task"])
        assert result.exit_code == 0
        assert "States:" in result.output
        assert "Transitions:" in result.output

    def test_type_info_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["type-info", "task", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["type"] == "task"
        assert "states" in data
        assert "transitions" in data
        assert "initial_state" in data

    def test_type_info_unknown(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["type-info", "nonexistent_type"])
        assert result.exit_code == 1
        assert "Unknown type" in result.output

    def test_transitions_shows_valid_states(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Transitions test"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["transitions", issue_id])
        assert result.exit_code == 0
        # An open task should have at least one transition
        assert "→" in result.output or "Transitions from" in result.output

    def test_transitions_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Transitions JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["transitions", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        if data:
            assert "to" in data[0]
            assert "ready" in data[0]

    def test_transitions_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["transitions", "nonexistent-abc"])
        assert result.exit_code == 1
        assert "Not found" in result.output

    def test_packs_lists_enabled(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["packs"])
        assert result.exit_code == 0
        assert "core" in result.output

    def test_packs_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["packs", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        pack_names = {p["pack"] for p in data}
        assert "core" in pack_names

    def test_validate_clean_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Valid issue"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["validate", issue_id])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_validate_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Validate JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["validate", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "valid" in data
        assert "warnings" in data
        assert "errors" in data

    def test_validate_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["validate", "nonexistent-abc"])
        assert result.exit_code == 1
        assert "Not found" in result.output

    def test_guide_core_pack(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["guide", "core"])
        assert result.exit_code == 0
        # Guide should have some content (overview, tips, etc.)
        assert len(result.output) > 20

    def test_guide_unknown_pack(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["guide", "nonexistent_pack"])
        assert result.exit_code == 1
        assert "Unknown pack" in result.output

    def test_templates_group_default(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """filigree templates (no subcommand) still lists templates."""
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["templates"])
        assert result.exit_code == 0
        assert "task" in result.output

    def test_templates_reload(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["templates", "reload"])
        assert result.exit_code == 0
        assert "reloaded" in result.output.lower()


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


class TestClaimCli:
    def test_claim_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Claimable"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-1"])
        assert result.exit_code == 0
        assert "Claimed" in result.output
        assert "agent-1" in result.output

    def test_claim_already_claimed(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Claimable"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-1"])
        result = runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-2"])
        assert result.exit_code == 1

    def test_claim_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Claimable JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["assignee"] == "agent-1"

    def test_claim_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["claim", "nonexistent-abc", "--assignee", "a"])
        assert result.exit_code == 1


class TestClaimNextCli:
    def test_claim_next_basic(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Ready task", "-p", "1"])
        result = runner.invoke(cli, ["claim-next", "--assignee", "agent-1"])
        assert result.exit_code == 0
        assert "Claimed" in result.output

    def test_claim_next_empty(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["claim-next", "--assignee", "agent-1"])
        assert result.exit_code == 0
        assert "No issues available" in result.output

    def test_claim_next_with_type_filter(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "A task", "--type", "task"])
        runner.invoke(cli, ["create", "A bug", "--type", "bug"])
        result = runner.invoke(cli, ["claim-next", "--assignee", "a", "--type", "bug", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["type"] == "bug"

    def test_claim_next_json_empty(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["claim-next", "--assignee", "a", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "empty"

    def test_claim_next_whitespace_assignee_shows_clean_error(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "A task"])
        result = runner.invoke(cli, ["claim-next", "--assignee", "   "])
        assert result.exit_code == 1
        assert "Traceback" not in (result.output or "")


class TestCreatePlanCli:
    def test_create_plan_from_stdin(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        plan_json = json.dumps(
            {
                "milestone": {"title": "v1.0 Release"},
                "phases": [
                    {
                        "title": "Phase 1",
                        "steps": [
                            {"title": "Step A"},
                            {"title": "Step B", "deps": [0]},
                        ],
                    }
                ],
            }
        )
        result = runner.invoke(cli, ["create-plan"], input=plan_json)
        assert result.exit_code == 0
        assert "v1.0 Release" in result.output

    def test_create_plan_from_file(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, project_root = cli_in_project
        plan_file = project_root / "plan.json"
        plan_file.write_text(
            json.dumps(
                {
                    "milestone": {"title": "File Plan"},
                    "phases": [{"title": "Phase 1", "steps": [{"title": "Step 1"}]}],
                }
            )
        )
        result = runner.invoke(cli, ["create-plan", "--file", str(plan_file)])
        assert result.exit_code == 0
        assert "File Plan" in result.output

    def test_create_plan_json_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        plan_json = json.dumps(
            {
                "milestone": {"title": "JSON Plan"},
                "phases": [{"title": "P1", "steps": [{"title": "S1"}]}],
            }
        )
        result = runner.invoke(cli, ["create-plan", "--json"], input=plan_json)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "milestone" in data
        assert "phases" in data

    def test_create_plan_invalid_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create-plan"], input="not json")
        assert result.exit_code == 1
        assert "Invalid JSON" in result.output or "error" in result.output.lower()

    def test_create_plan_validation_error_exits_1(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Backend ValueError (e.g. empty title) should exit 1, not crash."""
        runner, _ = cli_in_project
        plan_json = json.dumps({"milestone": {"title": ""}, "phases": [{"title": "P1"}]})
        result = runner.invoke(cli, ["create-plan"], input=plan_json)
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_create_plan_bad_dep_ref_exits_1(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """IndexError from bad dep refs should exit 1, not crash."""
        runner, _ = cli_in_project
        plan_json = json.dumps(
            {
                "milestone": {"title": "MS"},
                "phases": [{"title": "P1", "steps": [{"title": "S1", "deps": [99]}]}],
            }
        )
        result = runner.invoke(cli, ["create-plan"], input=plan_json)
        assert result.exit_code == 1
        assert "error" in result.output.lower()


class TestBatchCli:
    def test_batch_update(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)
        result = runner.invoke(cli, ["batch-update", id1, id2, "--priority", "0"])
        assert result.exit_code == 0
        assert "Updated 2" in result.output

    def test_batch_update_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        result = runner.invoke(cli, ["batch-update", id1, "--priority", "1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "updated" in data
        assert "errors" in data

    def test_batch_update_json_malformed_field_returns_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """batch-update --json with bad --field must emit JSON error, not plain text."""
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        result = runner.invoke(cli, ["batch-update", id1, "--field", "no-equals-sign", "--json"])
        data = json.loads(result.output)
        assert "error" in data

    def test_batch_close(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)
        result = runner.invoke(cli, ["batch-close", id1, id2])
        assert result.exit_code == 0
        assert "Closed 2" in result.output

    def test_batch_close_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        result = runner.invoke(cli, ["batch-close", id1, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "closed" in data
        assert "errors" in data

    def test_batch_close_partial_failure(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        result = runner.invoke(cli, ["batch-close", id1, "nonexistent-abc", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["closed"]) == 1
        assert len(data["errors"]) == 1

    def test_batch_add_label_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)

        result = runner.invoke(cli, ["batch-add-label", "security", id1, id2, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["labeled"]) == 2
        assert data["errors"] == []

        listed = runner.invoke(cli, ["list", "--label", "security", "--json"])
        listed_data = json.loads(listed.output)
        listed_ids = {row["id"] for row in listed_data}
        assert id1 in listed_ids
        assert id2 in listed_ids

    def test_batch_add_label_partial_failure(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        result = runner.invoke(cli, ["batch-add-label", "security", id1, "nonexistent-abc", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["labeled"]) == 1
        assert len(data["errors"]) == 1
        assert data["errors"][0]["id"] == "nonexistent-abc"

    def test_batch_add_comment_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)

        result = runner.invoke(cli, ["batch-add-comment", "triage-complete", id1, id2, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["commented"]) == 2
        assert data["errors"] == []

        comments = runner.invoke(cli, ["get-comments", id1, "--json"])
        comments_data = json.loads(comments.output)
        assert any(c["text"] == "triage-complete" for c in comments_data)

    def test_batch_add_comment_partial_failure(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        result = runner.invoke(cli, ["batch-add-comment", "triage-complete", id1, "nonexistent-abc", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["commented"]) == 1
        assert len(data["errors"]) == 1
        assert data["errors"][0]["id"] == "nonexistent-abc"


class TestEventsCli:
    def test_changes_since(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Event test"])
        result = runner.invoke(cli, ["changes", "--since", "2020-01-01T00:00:00"])
        assert result.exit_code == 0
        assert "created" in result.output.lower()

    def test_changes_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Event JSON"])
        result = runner.invoke(cli, ["changes", "--since", "2020-01-01T00:00:00", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_changes_empty(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["changes", "--since", "2099-01-01T00:00:00"])
        assert result.exit_code == 0

    def test_events_for_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Track events"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["update", issue_id, "--title", "Changed"])
        result = runner.invoke(cli, ["events", issue_id])
        assert result.exit_code == 0

    def test_events_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Track JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["events", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_events_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["events", "nonexistent-abc"])
        assert result.exit_code == 1


class TestExplainStateCli:
    def test_explain_state_basic(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["explain-state", "task", "open"])
        assert result.exit_code == 0
        assert "open" in result.output

    def test_explain_state_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["explain-state", "task", "open", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["state"] == "open"
        assert "category" in data
        assert "inbound_transitions" in data
        assert "outbound_transitions" in data

    def test_explain_state_unknown_type(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["explain-state", "nonexistent", "open"])
        assert result.exit_code == 1
        assert "Unknown type" in result.output

    def test_explain_state_unknown_state(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["explain-state", "task", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown state" in result.output


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


class TestPlanCli:
    def test_plan_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["plan", "nonexistent-abc"])
        assert result.exit_code == 1
        assert "Not found" in result.output

    def test_plan_display(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        plan_json = json.dumps(
            {
                "milestone": {"title": "v1.0"},
                "phases": [{"title": "Phase 1", "steps": [{"title": "Step 1"}]}],
            }
        )
        r = runner.invoke(cli, ["create-plan"], input=plan_json)
        assert r.exit_code == 0
        # Extract milestone ID from output
        milestone_line = next(line for line in r.output.splitlines() if "v1.0" in line)
        # Parse ID from "Created plan: v1.0 (test-xxx)"
        ms_id = milestone_line.split("(")[1].rstrip(")")
        result = runner.invoke(cli, ["plan", ms_id])
        assert result.exit_code == 0
        assert "Milestone" in result.output
        assert "Phase 1" in result.output

    def test_plan_json_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        plan_json = json.dumps(
            {
                "milestone": {"title": "v2.0"},
                "phases": [{"title": "Phase 1", "steps": [{"title": "Step 1"}]}],
            }
        )
        r = runner.invoke(cli, ["create-plan"], input=plan_json)
        assert r.exit_code == 0
        milestone_line = next(line for line in r.output.splitlines() if "v2.0" in line)
        ms_id = milestone_line.split("(")[1].rstrip(")")
        result = runner.invoke(cli, ["plan", ms_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "milestone" in data


class TestReleaseCli:
    def test_release_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Releasable"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-1"])
        result = runner.invoke(cli, ["release", issue_id])
        assert result.exit_code == 0
        assert "Released" in result.output

    def test_release_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["release", "nonexistent-abc"])
        assert result.exit_code == 1

    def test_release_not_claimed(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Not claimed"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["release", issue_id])
        assert result.exit_code == 1


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
        assert "Import failed" in result.output or "Import failed" in (result.output + (result.output or ""))
        # Must NOT contain a raw Python traceback
        assert "Traceback" not in (result.output or "")

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


class TestCreatePlanMissingKeys:
    def test_missing_milestone_key(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create-plan"], input=json.dumps({"phases": []}))
        assert result.exit_code == 1


class TestCreatePlanMalformedInput:
    """Bug filigree-802ab8: wrong value types should exit 1, not crash with traceback."""

    def test_milestone_as_list(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """milestone as a list instead of dict should give clean error, not AttributeError."""
        runner, _ = cli_in_project
        plan_json = json.dumps({"milestone": ["not", "a", "dict"], "phases": []})
        result = runner.invoke(cli, ["create-plan"], input=plan_json)
        assert result.exit_code == 1
        assert "milestone" in result.output.lower()
        assert "object" in result.output.lower()
        assert "Traceback" not in result.output

    def test_phases_as_string(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """phases as a string instead of list should give clean error, not TypeError."""
        runner, _ = cli_in_project
        plan_json = json.dumps({"milestone": {"title": "MS"}, "phases": "not a list"})
        result = runner.invoke(cli, ["create-plan"], input=plan_json)
        assert result.exit_code == 1
        assert "phases" in result.output.lower()
        assert "list" in result.output.lower()
        assert "Traceback" not in result.output

    def test_phase_entry_as_string(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Non-dict phase entries should give clean error, not AttributeError."""
        runner, _ = cli_in_project
        plan_json = json.dumps({"milestone": {"title": "MS"}, "phases": ["not a dict"]})
        result = runner.invoke(cli, ["create-plan"], input=plan_json)
        assert result.exit_code == 1
        assert "phase 1" in result.output.lower()
        assert "object" in result.output.lower()
        assert "Traceback" not in result.output

    def test_data_as_list(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Top-level JSON as a list should give clean error, not crash."""
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create-plan"], input=json.dumps([1, 2, 3]))
        assert result.exit_code == 1
        # Should produce a user-visible error message (not empty from unhandled exception)
        assert result.output.strip()


class TestCreatePlanFileErrors:
    """Bug filigree-5cc1de: file I/O errors should give clean error, not unhandled traceback."""

    def test_directory_as_file_path(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Passing a directory instead of a file should exit cleanly."""
        runner, project_root = cli_in_project
        dir_path = project_root / "somedir"
        dir_path.mkdir()
        result = runner.invoke(cli, ["create-plan", "--file", str(dir_path)])
        assert result.exit_code != 0
        # Exception must be handled (SystemExit from sys.exit), not leaked raw
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_binary_file(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Binary file that can't be decoded as UTF-8 should give clean error."""
        runner, project_root = cli_in_project
        bin_file = project_root / "plan.bin"
        bin_file.write_bytes(b"\x80\x81\x82\xff\xfe")
        result = runner.invoke(cli, ["create-plan", "--file", str(bin_file)])
        assert result.exit_code != 0
        # Exception must be handled (SystemExit from sys.exit), not leaked raw
        assert result.exception is None or isinstance(result.exception, SystemExit)


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
