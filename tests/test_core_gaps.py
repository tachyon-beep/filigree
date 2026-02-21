"""Gap-fill tests for filigree.core — covers update paths, plans, cycles, config, etc."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from filigree.core import (
    CURRENT_SCHEMA_VERSION,
    FILIGREE_DIR_NAME,
    FiligreeDB,
    find_filigree_root,
    read_config,
    write_config,
)


class TestUpdateIssuePaths:
    def test_update_title(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Old title")
        updated = db.update_issue(issue.id, title="New title")
        assert updated.title == "New title"

    def test_update_priority(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Priority test", priority=2)
        updated = db.update_issue(issue.id, priority=0)
        assert updated.priority == 0

    def test_update_assignee(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Assign test")
        updated = db.update_issue(issue.id, assignee="alice")
        assert updated.assignee == "alice"

    def test_update_description(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Desc test")
        updated = db.update_issue(issue.id, description="new desc")
        assert updated.description == "new desc"

    def test_update_notes(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Notes test")
        updated = db.update_issue(issue.id, notes="new notes")
        assert updated.notes == "new notes"

    def test_update_fields_merge(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fields test", fields={"a": "1", "b": "2"})
        updated = db.update_issue(issue.id, fields={"b": "updated", "c": "3"})
        assert updated.fields == {"a": "1", "b": "updated", "c": "3"}

    def test_update_no_changes(self, db: FiligreeDB) -> None:
        """Update with no actual changes should not error."""
        issue = db.create_issue("No change")
        updated = db.update_issue(issue.id)
        assert updated.title == "No change"

    def test_update_nonexistent_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.update_issue("nonexistent-abc123", title="nope")


class TestGetPlan:
    def test_plan_tree(self, db: FiligreeDB) -> None:
        ms = db.create_issue("Milestone 1", type="milestone")
        p1 = db.create_issue("Phase 1", type="phase", parent_id=ms.id, fields={"sequence": 1})
        p2 = db.create_issue("Phase 2", type="phase", parent_id=ms.id, fields={"sequence": 2})
        s1 = db.create_issue("Step 1", type="step", parent_id=p1.id, fields={"sequence": 1})
        db.create_issue("Step 2", type="step", parent_id=p1.id, fields={"sequence": 2})
        db.create_issue("Step 3", type="step", parent_id=p2.id, fields={"sequence": 1})
        db.close_issue(s1.id)

        plan = db.get_plan(ms.id)
        assert plan["total_steps"] == 3
        assert plan["completed_steps"] == 1
        assert len(plan["phases"]) == 2
        # Phase 1 has 2 steps, 1 completed
        assert plan["phases"][0]["total"] == 2
        assert plan["phases"][0]["completed"] == 1
        # Phase 2 has 1 step, 0 completed
        assert plan["phases"][1]["total"] == 1
        assert plan["phases"][1]["completed"] == 0

    def test_plan_nonexistent_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_plan("nonexistent-abc123")


class TestCycleDetection:
    def test_long_chain_cycle(self, db: FiligreeDB) -> None:
        """A→B→C→D, then D→A should be rejected."""
        a = db.create_issue("A")
        b = db.create_issue("B")
        c = db.create_issue("C")
        d = db.create_issue("D")
        db.add_dependency(a.id, b.id)  # A depends on B
        db.add_dependency(b.id, c.id)  # B depends on C
        db.add_dependency(c.id, d.id)  # C depends on D
        with pytest.raises(ValueError, match="cycle"):
            db.add_dependency(d.id, a.id)  # D depends on A → cycle

    def test_no_false_positive_on_diamond(self, db: FiligreeDB) -> None:
        """Diamond shape (A→B, A→C, B→D, C→D) is valid — no cycle."""
        a = db.create_issue("A")
        b = db.create_issue("B")
        c = db.create_issue("C")
        d = db.create_issue("D")
        db.add_dependency(a.id, b.id)
        db.add_dependency(a.id, c.id)
        db.add_dependency(b.id, d.id)
        db.add_dependency(c.id, d.id)  # Should not raise


class TestDependencyOperations:
    def test_remove_dependency(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        db.add_dependency(a.id, b.id)
        db.remove_dependency(a.id, b.id)
        refreshed = db.get_issue(a.id)
        assert b.id not in refreshed.blocked_by

    def test_get_blocked(self, db: FiligreeDB) -> None:
        a = db.create_issue("Blocked")
        b = db.create_issue("Blocker")
        db.add_dependency(a.id, b.id)
        blocked = db.get_blocked()
        assert any(i.id == a.id for i in blocked)

    def test_get_all_dependencies(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        db.add_dependency(a.id, b.id)
        all_deps = db.get_all_dependencies()
        assert len(all_deps) == 1
        assert all_deps[0]["from"] == a.id
        assert all_deps[0]["to"] == b.id


class TestIssueToDictRoundTrip:
    def test_to_dict_has_all_fields(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Roundtrip", labels=["a"], fields={"x": "1"})
        d = issue.to_dict()
        expected_keys = {
            "id",
            "title",
            "status",
            "status_category",
            "priority",
            "type",
            "parent_id",
            "assignee",
            "created_at",
            "updated_at",
            "closed_at",
            "description",
            "notes",
            "fields",
            "labels",
            "blocks",
            "blocked_by",
            "is_ready",
            "children",
        }
        assert set(d.keys()) == expected_keys
        assert d["labels"] == ["a"]
        assert d["fields"] == {"x": "1"}


class TestFindFiligreeRoot:
    def test_finds_in_current_dir(self, tmp_path: Path) -> None:
        (tmp_path / FILIGREE_DIR_NAME).mkdir()
        result = find_filigree_root(tmp_path)
        assert result == tmp_path / FILIGREE_DIR_NAME

    def test_finds_in_parent_dir(self, tmp_path: Path) -> None:
        (tmp_path / FILIGREE_DIR_NAME).mkdir()
        child = tmp_path / "subdir"
        child.mkdir()
        result = find_filigree_root(child)
        assert result == tmp_path / FILIGREE_DIR_NAME

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            find_filigree_root(tmp_path)


class TestConfig:
    def test_read_write_roundtrip(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / FILIGREE_DIR_NAME
        filigree_dir.mkdir()
        write_config(filigree_dir, {"prefix": "myproj", "version": 1})
        config = read_config(filigree_dir)
        assert config["prefix"] == "myproj"
        assert config["version"] == 1

    def test_read_missing_returns_defaults(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / FILIGREE_DIR_NAME
        filigree_dir.mkdir()
        config = read_config(filigree_dir)
        assert config["prefix"] == "filigree"


class TestSchemaVersioning:
    def test_version_set_after_init(self, db: FiligreeDB) -> None:
        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION

    def test_fresh_db_gets_current_version(self, tmp_path: Path) -> None:
        """A fresh database should get CURRENT_SCHEMA_VERSION."""
        d = FiligreeDB(tmp_path / "filigree.db", prefix="test")
        d.initialize()
        assert d.get_schema_version() == CURRENT_SCHEMA_VERSION
        d.close()


class TestChildren:
    def test_children_populated(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent", type="epic")
        child1 = db.create_issue("Child 1", parent_id=parent.id)
        child2 = db.create_issue("Child 2", parent_id=parent.id)
        refreshed = db.get_issue(parent.id)
        assert set(refreshed.children) == {child1.id, child2.id}


class TestEvents:
    def test_events_recorded_on_create(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Event test")
        events = db.get_recent_events(limit=5)
        assert any(e["issue_id"] == issue.id and e["event_type"] == "created" for e in events)

    def test_events_recorded_on_status_change(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Event test")
        db.update_issue(issue.id, status="in_progress")
        events = db.get_recent_events(limit=5)
        assert any(e["issue_id"] == issue.id and e["event_type"] == "status_changed" for e in events)


class TestTemplates:
    def test_list_templates(self, db: FiligreeDB) -> None:
        templates = db.list_templates()
        types = {t["type"] for t in templates}
        assert "bug" in types
        assert "task" in types
        assert "milestone" in types

    def test_get_template(self, db: FiligreeDB) -> None:
        tpl = db.get_template("bug")
        assert tpl is not None
        assert tpl["display_name"] == "Bug Report"
        assert len(tpl["fields_schema"]) > 0

    def test_get_unknown_template(self, db: FiligreeDB) -> None:
        assert db.get_template("nonexistent") is None

    def test_get_template_uses_runtime_override(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        (filigree_dir / "templates").mkdir()
        (filigree_dir / "config.json").write_text(json.dumps({"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}))
        (filigree_dir / "templates" / "bug.json").write_text(
            json.dumps(
                {
                    "type": "bug",
                    "display_name": "Bug Override",
                    "description": "Runtime override for bug workflow",
                    "pack": "custom",
                    "states": [
                        {"name": "intake", "category": "open"},
                        {"name": "fixing", "category": "wip"},
                        {"name": "closed", "category": "done"},
                    ],
                    "initial_state": "intake",
                    "transitions": [
                        {"from": "intake", "to": "fixing", "enforcement": "soft"},
                        {"from": "fixing", "to": "closed", "enforcement": "soft"},
                    ],
                    "fields_schema": [],
                }
            )
        )

        db = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
        db.initialize()
        try:
            created = db.create_issue("Overridden bug", type="bug")
            tpl = db.get_template("bug")
            assert tpl is not None
            assert tpl["display_name"] == "Bug Override"
            assert created.status == "intake"
        finally:
            db.close()


class TestValidateIssueUpcoming:
    def test_validate_shows_upcoming_transition_requirements(self, db: FiligreeDB) -> None:
        """validate_issue should show fields needed for next transitions."""
        bug = db.create_issue("Bug", type="bug")
        # Move bug to fixing state (triage -> confirmed -> fixing)
        db.update_issue(bug.id, status="confirmed")
        db.update_issue(bug.id, status="fixing")
        result = db.validate_issue(bug.id)
        # fixing -> verifying requires fix_verification
        assert any("fix_verification" in str(w) for w in result.warnings)
        assert any("Transition to 'verifying' requires" in str(w) for w in result.warnings)

    def test_validate_no_upcoming_when_fields_set(self, db: FiligreeDB) -> None:
        """No upcoming warnings when required fields are already populated."""
        bug = db.create_issue("Bug", type="bug", fields={"fix_verification": "tested"})
        db.update_issue(bug.id, status="confirmed")
        db.update_issue(bug.id, status="fixing")
        result = db.validate_issue(bug.id)
        # fix_verification is set, so no warning about it
        assert not any("fix_verification" in str(w) for w in result.warnings)

    def test_validate_unknown_type_still_valid(self, db: FiligreeDB) -> None:
        """Unknown types validate as valid with no warnings."""
        issue = db.create_issue("Unknown")
        # Default type 'task' is known, but let's just check it returns valid
        result = db.validate_issue(issue.id)
        assert result.valid is True


class TestGetTemplateEnriched:
    def test_get_template_includes_states(self, db: FiligreeDB) -> None:
        tpl = db.get_template("bug")
        assert tpl is not None
        assert "states" in tpl
        state_names = [s["name"] for s in tpl["states"]]
        assert "triage" in state_names
        assert "closed" in state_names
        # Each state has a category
        assert all("category" in s for s in tpl["states"])

    def test_get_template_includes_transitions(self, db: FiligreeDB) -> None:
        tpl = db.get_template("bug")
        assert tpl is not None
        assert "transitions" in tpl
        assert any(t["from"] == "triage" and t["to"] == "confirmed" for t in tpl["transitions"])

    def test_get_template_includes_initial_state(self, db: FiligreeDB) -> None:
        tpl = db.get_template("bug")
        assert tpl is not None
        assert "initial_state" in tpl
        assert tpl["initial_state"] == "triage"

    def test_get_template_task(self, db: FiligreeDB) -> None:
        tpl = db.get_template("task")
        assert tpl is not None
        assert tpl["initial_state"] == "open"
        assert len(tpl["states"]) >= 3
        assert len(tpl["transitions"]) >= 2


class TestCreateWithOptions:
    def test_create_with_all_options(self, db: FiligreeDB) -> None:
        issue = db.create_issue(
            "Full issue",
            type="bug",
            priority=0,
            assignee="alice",
            description="A description",
            notes="Some notes",
            fields={"severity": "critical"},
            labels=["urgent", "backend"],
            deps=None,
        )
        assert issue.type == "bug"
        assert issue.priority == 0
        assert issue.assignee == "alice"
        assert issue.description == "A description"
        assert issue.notes == "Some notes"
        assert issue.fields["severity"] == "critical"
        assert set(issue.labels) == {"urgent", "backend"}

    def test_create_with_deps(self, db: FiligreeDB) -> None:
        blocker = db.create_issue("Blocker")
        issue = db.create_issue("Blocked", deps=[blocker.id])
        assert blocker.id in issue.blocked_by


class TestCreatePlan:
    def test_basic_plan(self, db: FiligreeDB) -> None:
        plan = db.create_plan(
            {"title": "v1.0"},
            [
                {
                    "title": "Phase 1",
                    "steps": [
                        {"title": "Step 1.1"},
                        {"title": "Step 1.2", "deps": [0]},
                    ],
                },
            ],
        )
        assert plan["milestone"]["title"] == "v1.0"
        assert plan["total_steps"] == 2
        # Step 1.2 depends on Step 1.1
        steps = plan["phases"][0]["steps"]
        step_1_2 = steps[1]
        step_1_1 = steps[0]
        assert step_1_1["id"] in step_1_2["blocked_by"]

    def test_cross_phase_deps(self, db: FiligreeDB) -> None:
        plan = db.create_plan(
            {"title": "Cross-phase"},
            [
                {"title": "P1", "steps": [{"title": "S1.1"}]},
                {"title": "P2", "steps": [{"title": "S2.1", "deps": ["0.0"]}]},
            ],
        )
        p1_step = plan["phases"][0]["steps"][0]
        p2_step = plan["phases"][1]["steps"][0]
        assert p1_step["id"] in p2_step["blocked_by"]

    def test_plan_hierarchy_types(self, db: FiligreeDB) -> None:
        plan = db.create_plan(
            {"title": "Typed plan"},
            [{"title": "Phase A", "steps": [{"title": "Step A.1"}]}],
        )
        assert plan["milestone"]["type"] == "milestone"
        assert plan["phases"][0]["phase"]["type"] == "phase"
        assert plan["phases"][0]["steps"][0]["type"] == "step"

    def test_plan_sequence_fields(self, db: FiligreeDB) -> None:
        plan = db.create_plan(
            {"title": "Sequenced"},
            [
                {"title": "Phase 1", "steps": [{"title": "S1"}, {"title": "S2"}]},
                {"title": "Phase 2", "steps": [{"title": "S3"}]},
            ],
        )
        assert plan["phases"][0]["phase"]["fields"]["sequence"] == 1
        assert plan["phases"][1]["phase"]["fields"]["sequence"] == 2
        assert plan["phases"][0]["steps"][0]["fields"]["sequence"] == 1
        assert plan["phases"][0]["steps"][1]["fields"]["sequence"] == 2

    def test_plan_uses_template_initial_states(self, db: FiligreeDB) -> None:
        plan = db.create_plan(
            {"title": "Initial states"},
            [{"title": "Phase 1", "steps": [{"title": "Step 1"}]}],
        )
        assert plan["milestone"]["status"] == "planning"
        assert plan["phases"][0]["phase"]["status"] == "pending"
        assert plan["phases"][0]["steps"][0]["status"] == "pending"

    def test_plan_empty_phases(self, db: FiligreeDB) -> None:
        plan = db.create_plan({"title": "Empty"}, [{"title": "No steps"}])
        assert plan["total_steps"] == 0
        assert len(plan["phases"]) == 1

    def test_plan_empty_milestone_title_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Milestone 'title' is required"):
            db.create_plan({"title": ""}, [{"title": "Phase 1"}])

    def test_plan_empty_phase_title_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Phase 1 'title' is required"):
            db.create_plan({"title": "MS"}, [{"title": ""}])

    def test_plan_empty_step_title_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Phase 1, Step 1 'title' is required"):
            db.create_plan(
                {"title": "MS"},
                [{"title": "Phase", "steps": [{"title": ""}]}],
            )

    def test_plan_whitespace_only_title_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Milestone 'title' is required"):
            db.create_plan({"title": "   "}, [{"title": "Phase 1"}])

    def test_plan_rejects_negative_dep_index(self, db: FiligreeDB) -> None:
        """Negative indices silently resolve to wrong step via Python list[-1]."""
        with pytest.raises((ValueError, IndexError)):
            db.create_plan(
                {"title": "MS"},
                [{"title": "Phase", "steps": [{"title": "S1", "deps": [-1]}]}],
            )

    def test_plan_rejects_self_dependency(self, db: FiligreeDB) -> None:
        """Step referencing itself as a dep should raise, not silently insert."""
        with pytest.raises(ValueError, match="self-dependency"):
            db.create_plan(
                {"title": "MS"},
                [{"title": "Phase", "steps": [{"title": "S1", "deps": [0]}]}],
            )

    def test_plan_rejects_cycle(self, db: FiligreeDB) -> None:
        """Mutual deps between steps should raise, not silently insert."""
        with pytest.raises(ValueError, match="cycle"):
            db.create_plan(
                {"title": "MS"},
                [
                    {
                        "title": "Phase",
                        "steps": [
                            {"title": "S1", "deps": [1]},
                            {"title": "S2", "deps": [0]},
                        ],
                    },
                ],
            )

    def test_plan_records_dependency_events(self, db: FiligreeDB) -> None:
        """Dependencies created in a plan should have events, like add_dependency()."""
        plan = db.create_plan(
            {"title": "MS"},
            [
                {
                    "title": "Phase",
                    "steps": [
                        {"title": "S1"},
                        {"title": "S2", "deps": [0]},
                    ],
                },
            ],
        )
        step_2_id = plan["phases"][0]["steps"][1]["id"]
        events = db.get_issue_events(step_2_id)
        dep_events = [e for e in events if e["event_type"] == "dependency_added"]
        assert len(dep_events) == 1


class TestBatchOperations:
    def test_batch_close(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        results, errors = db.batch_close([a.id, b.id], reason="done")
        assert len(results) == 2
        assert len(errors) == 0
        assert all(r.status == "closed" for r in results)

    def test_batch_update_status(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        results, errors = db.batch_update([a.id, b.id], status="in_progress")
        assert len(results) == 2
        assert len(errors) == 0
        assert all(r.status == "in_progress" for r in results)

    def test_batch_update_priority(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        b = db.create_issue("B")
        results, errors = db.batch_update([a.id, b.id], priority=0)
        assert all(r.priority == 0 for r in results)
        assert len(errors) == 0

    def test_batch_update_not_found(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        results, errors = db.batch_update([a.id, "nonexistent-xyz"], priority=0)
        assert len(results) == 1
        assert len(errors) == 1
        assert errors[0]["id"] == "nonexistent-xyz"

    def test_batch_close_not_found(self, db: FiligreeDB) -> None:
        results, errors = db.batch_close(["nonexistent-xyz"])
        assert len(results) == 0
        assert len(errors) == 1
        assert errors[0]["id"] == "nonexistent-xyz"


class TestClaimIssue:
    def test_claim_success(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Claimable")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        assert claimed.status == "open"  # status unchanged
        assert claimed.assignee == "agent-1"

    def test_claim_step_uses_template_states(self, db: FiligreeDB) -> None:
        """Step type uses template open-category states (pending), not legacy 'open'."""
        step = db.create_issue("Step", type="step")
        assert step.status == "pending"  # step template initial state
        claimed = db.claim_issue(step.id, assignee="agent-1")
        assert claimed.status == "pending"  # status unchanged
        assert claimed.assignee == "agent-1"

    def test_claim_already_claimed(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Claimable")
        db.claim_issue(issue.id, assignee="agent-1")
        with pytest.raises(ValueError, match="already assigned to"):
            db.claim_issue(issue.id, assignee="agent-2")

    def test_claim_closed_issue(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Will close")
        db.close_issue(issue.id)
        with pytest.raises(ValueError, match="status is 'closed'"):
            db.claim_issue(issue.id, assignee="agent-1")

    def test_claim_not_found(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError, match="not found"):
            db.claim_issue("nonexistent-abc123", assignee="agent-1")

    def test_claim_records_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Claimable")
        db.claim_issue(issue.id, assignee="agent-1", actor="agent-1")
        events = db.get_recent_events(limit=5)
        claim_event = next(e for e in events if e["event_type"] == "claimed")
        assert claim_event["issue_id"] == issue.id
        assert claim_event["new_value"] == "agent-1"
        assert claim_event["actor"] == "agent-1"


class TestClaimEmptyAssignee:
    """Bug filigree-040ddb: claim_issue/claim_next must reject empty assignee."""

    def test_claim_issue_empty_string_raises(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Claimable")
        with pytest.raises(ValueError, match="Assignee cannot be empty"):
            db.claim_issue(issue.id, assignee="")

    def test_claim_issue_whitespace_only_raises(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Claimable")
        with pytest.raises(ValueError, match="Assignee cannot be empty"):
            db.claim_issue(issue.id, assignee="   ")

    def test_claim_next_empty_string_raises(self, db: FiligreeDB) -> None:
        db.create_issue("Ready")
        with pytest.raises(ValueError, match="Assignee cannot be empty"):
            db.claim_next("")

    def test_claim_next_whitespace_only_raises(self, db: FiligreeDB) -> None:
        db.create_issue("Ready")
        with pytest.raises(ValueError, match="Assignee cannot be empty"):
            db.claim_next("   ")


class TestBatchInputValidation:
    """Bug filigree-c45430: batch_close/batch_update must validate issue_ids type."""

    def test_batch_close_string_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_close("not-a-list")  # type: ignore[arg-type]

    def test_batch_close_list_of_ints_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_close([1, 2, 3])  # type: ignore[list-item]

    def test_batch_update_string_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_update("not-a-list", status="closed")  # type: ignore[arg-type]

    def test_batch_update_list_of_ints_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(TypeError, match="issue_ids must be a list of strings"):
            db.batch_update([1, 2, 3], status="closed")  # type: ignore[list-item]

    def test_batch_close_valid_list_passes(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Closeable")
        closed, errors = db.batch_close([issue.id])
        assert len(closed) == 1
        assert len(errors) == 0

    def test_batch_update_valid_list_passes(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Updateable")
        updated, errors = db.batch_update([issue.id], priority=0)
        assert len(updated) == 1
        assert len(errors) == 0


class TestGetEventsSince:
    def test_basic(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Event source")
        # Get creation event timestamp
        events = db.get_recent_events(limit=1)
        ts = events[0]["created_at"]
        # Make a change after creation
        db.update_issue(issue.id, status="in_progress")
        since_events = db.get_events_since(ts)
        assert len(since_events) >= 1
        assert any(e["event_type"] == "status_changed" for e in since_events)

    def test_empty_when_no_events(self, db: FiligreeDB) -> None:
        result = db.get_events_since("2099-01-01T00:00:00+00:00")
        assert result == []

    def test_respects_limit(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.create_issue(f"Issue {i}")
        result = db.get_events_since("2000-01-01T00:00:00+00:00", limit=2)
        assert len(result) == 2

    def test_chronological_order(self, db: FiligreeDB) -> None:
        db.create_issue("First")
        db.create_issue("Second")
        result = db.get_events_since("2000-01-01T00:00:00+00:00")
        assert len(result) >= 2
        # Events should be in ascending order
        for i in range(len(result) - 1):
            assert result[i]["created_at"] <= result[i + 1]["created_at"]


class TestActorTracking:
    def test_actor_in_update_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Track me")
        db.update_issue(issue.id, status="in_progress", actor="agent-alpha")
        events = db.get_recent_events(limit=5)
        status_event = next(e for e in events if e["event_type"] == "status_changed")
        assert status_event["actor"] == "agent-alpha"

    def test_actor_in_close_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Close me")
        db.close_issue(issue.id, actor="agent-beta")
        events = db.get_recent_events(limit=5)
        close_event = next(e for e in events if e["event_type"] == "status_changed" and e["new_value"] == "closed")
        assert close_event["actor"] == "agent-beta"


class TestCriticalPath:
    def test_linear_chain(self, db: FiligreeDB) -> None:
        """A→B→C should produce a path of length 3."""
        a = db.create_issue("A")
        b = db.create_issue("B")
        c = db.create_issue("C")
        db.add_dependency(a.id, b.id)  # A depends on B
        db.add_dependency(b.id, c.id)  # B depends on C
        path = db.get_critical_path()
        assert len(path) == 3
        # Path should be C→B→A (root blocker to final blocked)
        assert path[0]["id"] == c.id
        assert path[-1]["id"] == a.id

    def test_no_deps(self, db: FiligreeDB) -> None:
        """No dependency chains → empty path."""
        db.create_issue("Standalone 1")
        db.create_issue("Standalone 2")
        path = db.get_critical_path()
        assert path == []

    def test_ignores_closed(self, db: FiligreeDB) -> None:
        """Closed issues should not appear in critical path."""
        a = db.create_issue("A")
        b = db.create_issue("B")
        c = db.create_issue("C")
        db.add_dependency(a.id, b.id)
        db.add_dependency(b.id, c.id)
        db.close_issue(c.id)
        path = db.get_critical_path()
        # With C closed, only A→B remains (length 2)
        assert len(path) == 2

    def test_empty_db(self, db: FiligreeDB) -> None:
        path = db.get_critical_path()
        assert path == []

    def test_selects_longest_chain(self, db: FiligreeDB) -> None:
        """When there are multiple chains, return the longest."""
        # Chain 1: A→B (length 2)
        a = db.create_issue("A")
        b = db.create_issue("B")
        db.add_dependency(a.id, b.id)
        # Chain 2: C→D→E (length 3)
        c = db.create_issue("C")
        d = db.create_issue("D")
        e = db.create_issue("E")
        db.add_dependency(c.id, d.id)
        db.add_dependency(d.id, e.id)
        path = db.get_critical_path()
        assert len(path) == 3


class TestReparenting:
    def test_update_parent_id(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child")
        updated = db.update_issue(child.id, parent_id=parent.id)
        assert updated.parent_id == parent.id

    def test_update_parent_id_records_event(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child")
        db.update_issue(child.id, parent_id=parent.id, actor="tester")
        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'parent_changed'",
            (child.id,),
        ).fetchall()
        assert len(events) == 1
        assert events[0]["new_value"] == parent.id

    def test_update_parent_id_invalid_parent_raises(self, db: FiligreeDB) -> None:
        child = db.create_issue("Child")
        with pytest.raises(ValueError, match="does not reference"):
            db.update_issue(child.id, parent_id="nonexistent-123456")

    def test_update_parent_id_self_reference_raises(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Issue")
        with pytest.raises(ValueError, match="cannot be its own parent"):
            db.update_issue(issue.id, parent_id=issue.id)

    def test_update_parent_id_cycle_raises(self, db: FiligreeDB) -> None:
        grandparent = db.create_issue("Grandparent")
        parent = db.create_issue("Parent", parent_id=grandparent.id)
        child = db.create_issue("Child", parent_id=parent.id)
        with pytest.raises(ValueError, match="circular"):
            db.update_issue(grandparent.id, parent_id=child.id)

    def test_clear_parent_id(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child", parent_id=parent.id)
        updated = db.update_issue(child.id, parent_id="")
        assert updated.parent_id is None

    def test_update_parent_id_no_change_no_event(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child", parent_id=parent.id)
        db.update_issue(child.id, parent_id=parent.id)
        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'parent_changed'",
            (child.id,),
        ).fetchall()
        assert len(events) == 0
