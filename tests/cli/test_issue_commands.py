"""CLI tests for issue CRUD commands (create, show, update, close, reopen, claim, comments, labels)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from filigree.cli import cli
from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, read_config
from tests.cli.conftest import _extract_id


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
        issue_id = _extract_id(result.output)
        result = runner.invoke(cli, ["show", issue_id])
        assert result.exit_code == 0
        assert "Show me" in result.output

    def test_show_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["show", "test-nonexistent"])
        assert result.exit_code == 1

    def test_list_all(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A"])
        runner.invoke(cli, ["create", "Issue B"])
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        # 2 created + auto-seeded "Future" release = 3
        assert "3 issues" in result.output

    def test_list_filter_status(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Open one"])
        r = runner.invoke(cli, ["create", "Close one"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["close", issue_id])
        result = runner.invoke(cli, ["list", "--status", "open"])
        # 1 open task + auto-seeded "Future" release (planning = open category) = 2
        assert "2 issues" in result.output


class TestUpdateAndClose:
    def test_update_status(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Update me"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["update", issue_id, "--status", "in_progress"])
        assert result.exit_code == 0
        assert "in_progress" in result.output

    def test_update_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["update", "test-nonexistent", "--title", "nope"])
        assert result.exit_code == 1

    def test_update_design_sets_field(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Design shorthand"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["update", issue_id, "--design", "draft v1"])
        assert result.exit_code == 0

        show = runner.invoke(cli, ["show", issue_id, "--json"])
        data = json.loads(show.output)
        assert data["fields"]["design"] == "draft v1"

    def test_update_design_empty_string_clears_field(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """filigree-613e9f5f66: `--design=` must clear the field via shorthand, not silently no-op."""
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Design clear"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["update", issue_id, "--design", "initial"])

        # Clear via empty-string shorthand.
        result = runner.invoke(cli, ["update", issue_id, "--design", ""])
        assert result.exit_code == 0

        show = runner.invoke(cli, ["show", issue_id, "--json"])
        data = json.loads(show.output)
        assert data["fields"].get("design") == ""

    def test_close_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Close me"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["close", issue_id])
        assert result.exit_code == 0
        assert "Closed" in result.output

    def test_close_with_reason(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Close with reason"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["close", issue_id, "--reason", "done"])
        assert result.exit_code == 0

    def test_close_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["close", "test-nonexistent"])
        assert result.exit_code == 1
        assert "Not found" in result.output

    def test_close_json_partial_failure_preserves_successes(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """When closing multiple issues with --json and one fails, successes must still appear."""
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Good one"])
        good_id = _extract_id(r.output)
        result = runner.invoke(cli, ["close", good_id, "test-nonexistent", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert len(data["closed"]) == 1
        assert data["closed"][0]["id"] == good_id
        assert len(data["errors"]) == 1
        assert data["errors"][0]["id"] == "test-nonexistent"

    def test_close_json_all_success(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        r2 = runner.invoke(cli, ["create", "B"])
        id1, id2 = _extract_id(r1.output), _extract_id(r2.output)
        result = runner.invoke(cli, ["close", id1, id2, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["closed"]) == 2
        assert "errors" not in data

    def test_close_json_unblocked_only_newly_unblocked(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """`unblocked` must contain only issues that became ready from this close, not pre-existing ready issues."""
        runner, _ = cli_in_project
        already_ready = _extract_id(runner.invoke(cli, ["create", "Already ready"]).output)
        dep = _extract_id(runner.invoke(cli, ["create", "Dep"]).output)
        blocked = _extract_id(runner.invoke(cli, ["create", "Blocked"]).output)
        assert runner.invoke(cli, ["add-dep", blocked, dep]).exit_code == 0

        result = runner.invoke(cli, ["close", dep, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        unblocked_ids = {i["id"] for i in data["unblocked"]}
        assert blocked in unblocked_ids, "issue whose only dep closed must appear in unblocked"
        assert already_ready not in unblocked_ids, f"pre-existing ready issue must NOT appear in unblocked; got {unblocked_ids}"


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
        result = runner.invoke(cli, ["reopen", "test-nonexistent"])
        assert result.exit_code == 1
        assert "Not found" in result.output

    def test_reopen_json_partial_failure_preserves_successes(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """When reopening multiple issues with --json and one fails, successes must still appear."""
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Reopen me"])
        good_id = _extract_id(r.output)
        runner.invoke(cli, ["close", good_id])
        result = runner.invoke(cli, ["reopen", good_id, "test-nonexistent", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert len(data["reopened"]) == 1
        assert data["reopened"][0]["id"] == good_id
        assert len(data["errors"]) == 1

    def test_reopen_json_all_success(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        r2 = runner.invoke(cli, ["create", "B"])
        id1, id2 = _extract_id(r1.output), _extract_id(r2.output)
        runner.invoke(cli, ["close", id1])
        runner.invoke(cli, ["close", id2])
        result = runner.invoke(cli, ["reopen", id1, id2, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["reopened"]) == 2
        assert "errors" not in data


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
        result = runner.invoke(cli, ["add-comment", "test-nonexistent", "text"])
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
        result = runner.invoke(cli, ["add-label", "test-nonexistent", "bug"])
        assert result.exit_code == 1


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
        result = runner.invoke(cli, ["claim", "test-nonexistent", "--assignee", "a"])
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
        # Claim the auto-seeded "Future" release first so the queue is truly empty
        runner.invoke(cli, ["claim-next", "--assignee", "drain"])
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
        # Claim the auto-seeded "Future" release first so the queue is truly empty
        runner.invoke(cli, ["claim-next", "--assignee", "drain"])
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
        result = runner.invoke(cli, ["release", "test-nonexistent"])
        assert result.exit_code == 1

    def test_release_not_claimed(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Not claimed"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["release", issue_id])
        assert result.exit_code == 1

    def test_release_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "JSON release"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-1"])
        result = runner.invoke(cli, ["release", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == issue_id
        assert data["assignee"] == ""

    def test_release_json_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["release", "test-nonexistent", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data


class TestListLabelQuery:
    def test_list_label_prefix(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A", "-l", "cluster:broad-except"])
        runner.invoke(cli, ["create", "Issue B", "-l", "effort:m"])
        result = runner.invoke(cli, ["list", "--label-prefix", "cluster:"])
        assert result.exit_code == 0
        assert "Issue A" in result.output

    def test_list_not_label(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A", "-l", "wont-fix"])
        runner.invoke(cli, ["create", "Issue B", "-l", "needs-review"])
        result = runner.invoke(cli, ["list", "--not-label", "wont-fix"])
        assert result.exit_code == 0
        assert "Issue A" not in result.output

    def test_list_multiple_labels_and(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A", "-l", "needs-review", "-l", "urgent"])
        runner.invoke(cli, ["create", "Issue B", "-l", "needs-review"])
        result = runner.invoke(cli, ["list", "-l", "needs-review", "-l", "urgent"])
        assert result.exit_code == 0
        assert "Issue A" in result.output

    def test_list_virtual_label_via_cli(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Fresh issue"])
        result = runner.invoke(cli, ["list", "-l", "age:fresh"])
        assert result.exit_code == 0
        assert "Fresh issue" in result.output
