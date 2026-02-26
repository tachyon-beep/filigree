"""Tests for core plan operations — get_plan, create_plan, rollback."""

from __future__ import annotations

import pytest

from filigree.core import FiligreeDB


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


class TestCreatePlanRollback:
    """Bug fix: filigree-4135c6 — create_plan no rollback."""

    def test_bad_dep_reference_rolls_back(self, db: FiligreeDB) -> None:
        """create_plan with a bad dep index should not leave orphan milestone/phases."""
        issues_before = len(db.list_issues())

        with pytest.raises((IndexError, ValueError)):
            db.create_plan(
                milestone={"title": "Orphan Test Milestone"},
                phases=[
                    {
                        "title": "Phase 1",
                        "steps": [
                            {"title": "Step A"},
                            {
                                "title": "Step B",
                                "deps": [99],  # Invalid: no step at index 99
                            },
                        ],
                    }
                ],
            )

        # No orphan issues should remain after rollback
        issues_after = len(db.list_issues())
        assert issues_after == issues_before, f"Expected {issues_before} issues after rollback, got {issues_after}"

    def test_successful_plan_commits(self, db: FiligreeDB) -> None:
        """A valid plan should commit successfully."""
        plan = db.create_plan(
            milestone={"title": "Good Milestone"},
            phases=[
                {
                    "title": "Phase 1",
                    "steps": [
                        {"title": "Step A"},
                        {"title": "Step B", "deps": [0]},
                    ],
                }
            ],
        )
        assert plan["milestone"]["title"] == "Good Milestone"
        assert len(plan["phases"]) == 1
        assert plan["phases"][0]["total"] == 2
