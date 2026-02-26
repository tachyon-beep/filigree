"""CLI tests for workflow commands (types, transitions, validate, packs, guide, plan, batch, events, explain-state)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from filigree.cli import cli
from tests.cli.conftest import _extract_id


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
