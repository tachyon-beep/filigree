# tests/test_templates.py
"""Tests for the workflow template system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from filigree.templates import (
    FieldSchema,
    HardEnforcementError,
    StateDefinition,
    TemplateRegistry,
    TransitionDefinition,
    TransitionNotAllowedError,
    TransitionOption,
    TransitionResult,
    TypeTemplate,
    ValidationResult,
    WorkflowPack,
)
from filigree.templates_data import BUILT_IN_PACKS

_ALL_PACKS = ["core", "planning", "risk", "spike", "requirements", "roadmap", "incident", "debt", "release"]
_PACKS_WITH_STATES_EXPLAINED = ["risk", "spike", "requirements", "roadmap", "incident", "debt", "release"]


class TestDataclasses:
    """Verify all template dataclasses are frozen and correctly structured."""

    def test_state_definition_frozen(self) -> None:
        sd = StateDefinition(name="triage", category="open")
        assert sd.name == "triage"
        assert sd.category == "open"
        with pytest.raises(AttributeError):
            sd.name = "other"  # type: ignore[misc]

    def test_transition_definition_defaults(self) -> None:
        td = TransitionDefinition(from_state="a", to_state="b", enforcement="soft")
        assert td.requires_fields == ()
        assert td.enforcement == "soft"

    def test_field_schema_with_options(self) -> None:
        fs = FieldSchema(
            name="severity",
            type="enum",
            options=("critical", "major"),
            description="Impact severity",
            required_at=("confirmed",),
        )
        assert fs.options == ("critical", "major")
        assert fs.required_at == ("confirmed",)

    def test_type_template_minimal(self) -> None:
        tpl = TypeTemplate(
            type="task",
            display_name="Task",
            description="A task",
            pack="core",
            states=(StateDefinition(name="open", category="open"), StateDefinition(name="closed", category="done")),
            initial_state="open",
            transitions=(TransitionDefinition(from_state="open", to_state="closed", enforcement="soft"),),
            fields_schema=(),
        )
        assert tpl.type == "task"
        assert tpl.initial_state == "open"

    def test_transition_result(self) -> None:
        tr = TransitionResult(allowed=True, enforcement="soft", missing_fields=(), warnings=("Watch out",))
        assert tr.allowed is True
        assert tr.warnings == ("Watch out",)

    def test_transition_option(self) -> None:
        to = TransitionOption(to="closed", category="done", enforcement="soft", requires_fields=(), missing_fields=(), ready=True)
        assert to.ready is True

    def test_validation_result(self) -> None:
        vr = ValidationResult(valid=True, warnings=(), errors=())
        assert vr.valid is True

    def test_state_definition_rejects_invalid_name(self) -> None:
        with pytest.raises(ValueError, match="Invalid state name"):
            StateDefinition(name="UPPER", category="open")
        with pytest.raises(ValueError, match="Invalid state name"):
            StateDefinition(name="has-dash", category="open")
        with pytest.raises(ValueError, match="Invalid state name"):
            StateDefinition(name="has space", category="open")
        with pytest.raises(ValueError, match="Invalid state name"):
            StateDefinition(name="", category="open")

    def test_state_definition_accepts_valid_names(self) -> None:
        """Underscore-separated lowercase names up to 64 chars are valid."""
        assert StateDefinition(name="a", category="open").name == "a"
        assert StateDefinition(name="in_progress", category="wip").name == "in_progress"
        assert StateDefinition(name="x" * 64, category="done").name == "x" * 64

    def test_state_definition_rejects_too_long_name(self) -> None:
        with pytest.raises(ValueError, match="Invalid state name"):
            StateDefinition(name="x" * 65, category="open")

    def test_workflow_pack_minimal(self) -> None:
        wp = WorkflowPack(
            pack="core",
            version="1.0",
            display_name="Core",
            description="Core types",
            types={},
            requires_packs=(),
            relationships=(),
            cross_pack_relationships=(),
            guide=None,
        )
        assert wp.pack == "core"


class TestExceptions:
    """Verify exception types carry structured data and remediation hints."""

    def test_transition_not_allowed_is_value_error(self) -> None:
        err = TransitionNotAllowedError("triage", "closed", "bug")
        assert isinstance(err, ValueError)
        assert "triage" in str(err)
        assert "closed" in str(err)
        assert "bug" in str(err)
        assert err.from_state == "triage"
        assert err.to_state == "closed"
        assert err.type_name == "bug"

    def test_transition_not_allowed_has_remediation(self) -> None:
        err = TransitionNotAllowedError("triage", "closed", "bug")
        assert "get_valid_transitions" in str(err)

    def test_hard_enforcement_is_value_error(self) -> None:
        err = HardEnforcementError("fixing", "verifying", "bug", ["fix_verification"])
        assert isinstance(err, ValueError)
        assert "fix_verification" in str(err)
        assert err.missing_fields == ["fix_verification"]
        assert err.from_state == "fixing"
        assert err.to_state == "verifying"
        assert err.type_name == "bug"

    def test_hard_enforcement_has_remediation(self) -> None:
        err = HardEnforcementError("verifying", "closed", "bug", ["fix_verification"])
        assert "get_type_info" in str(err)

    def test_hard_enforcement_multiple_fields(self) -> None:
        err = HardEnforcementError("assessing", "assessed", "risk", ["risk_score", "impact"])
        assert "risk_score" in str(err)
        assert "impact" in str(err)
        assert err.missing_fields == ["risk_score", "impact"]


class TestTemplateRegistry:
    """Test TemplateRegistry with manually registered templates."""

    @pytest.fixture
    def registry(self) -> TemplateRegistry:
        """A registry pre-loaded with a minimal core pack."""
        reg = TemplateRegistry()
        task_tpl = TypeTemplate(
            type="task",
            display_name="Task",
            description="General task",
            pack="core",
            states=(
                StateDefinition("open", "open"),
                StateDefinition("in_progress", "wip"),
                StateDefinition("closed", "done"),
            ),
            initial_state="open",
            transitions=(
                TransitionDefinition("open", "in_progress", "soft"),
                TransitionDefinition("in_progress", "closed", "soft"),
            ),
            fields_schema=(),
        )
        bug_tpl = TypeTemplate(
            type="bug",
            display_name="Bug",
            description="Bug report",
            pack="core",
            states=(
                StateDefinition("triage", "open"),
                StateDefinition("confirmed", "open"),
                StateDefinition("fixing", "wip"),
                StateDefinition("verifying", "wip"),
                StateDefinition("closed", "done"),
                StateDefinition("wont_fix", "done"),
            ),
            initial_state="triage",
            transitions=(
                TransitionDefinition("triage", "confirmed", "soft"),
                TransitionDefinition("triage", "wont_fix", "soft"),
                TransitionDefinition("confirmed", "fixing", "soft"),
                TransitionDefinition("fixing", "verifying", "soft", requires_fields=("fix_verification",)),
                TransitionDefinition("verifying", "closed", "hard", requires_fields=("fix_verification",)),
                TransitionDefinition("verifying", "fixing", "soft"),
            ),
            fields_schema=(
                FieldSchema("severity", "enum", options=("critical", "major", "minor", "cosmetic"), required_at=("confirmed",)),
                FieldSchema("fix_verification", "text", required_at=("verifying",)),
            ),
        )
        reg._register_type(task_tpl)
        reg._register_type(bug_tpl)
        return reg

    def test_get_type_found(self, registry: TemplateRegistry) -> None:
        tpl = registry.get_type("task")
        assert tpl is not None
        assert tpl.display_name == "Task"

    def test_get_type_not_found(self, registry: TemplateRegistry) -> None:
        assert registry.get_type("nonexistent") is None

    def test_list_types(self, registry: TemplateRegistry) -> None:
        types = registry.list_types()
        names = [t.type for t in types]
        assert "task" in names
        assert "bug" in names

    def test_get_initial_state_with_template(self, registry: TemplateRegistry) -> None:
        assert registry.get_initial_state("bug") == "triage"
        assert registry.get_initial_state("task") == "open"

    def test_get_initial_state_fallback(self, registry: TemplateRegistry) -> None:
        assert registry.get_initial_state("unknown_type") == "open"

    def test_get_category(self, registry: TemplateRegistry) -> None:
        assert registry.get_category("bug", "triage") == "open"
        assert registry.get_category("bug", "fixing") == "wip"
        assert registry.get_category("bug", "closed") == "done"
        assert registry.get_category("bug", "wont_fix") == "done"

    def test_get_category_cache_is_o1(self, registry: TemplateRegistry) -> None:
        """Category cache should be a hierarchical dict lookup, not iteration."""
        assert hasattr(registry, "_category_cache")
        assert "bug" in registry._category_cache
        assert "triage" in registry._category_cache["bug"]

    def test_get_category_unknown_state(self, registry: TemplateRegistry) -> None:
        """Unknown state for known type returns None."""
        assert registry.get_category("bug", "nonexistent") is None

    def test_get_category_unknown_type(self, registry: TemplateRegistry) -> None:
        """Unknown type returns None."""
        assert registry.get_category("unknown", "open") is None

    def test_override_type_clears_stale_category_cache(self, registry: TemplateRegistry) -> None:
        """Overriding a type must remove old state entries from _category_cache."""
        # Pre-condition: old states exist in cache
        assert registry.get_category("task", "open") == "open"
        assert registry.get_category("task", "in_progress") == "wip"
        assert registry.get_category("task", "closed") == "done"

        # Override "task" with completely different states
        override = TypeTemplate(
            type="task",
            display_name="Custom Task",
            description="Overridden",
            pack="core",
            states=(
                StateDefinition("todo", "open"),
                StateDefinition("doing", "wip"),
                StateDefinition("complete", "done"),
            ),
            initial_state="todo",
            transitions=(
                TransitionDefinition("todo", "doing", "soft"),
                TransitionDefinition("doing", "complete", "soft"),
            ),
            fields_schema=(),
        )
        registry._register_type(override)

        # New states should work
        assert registry.get_category("task", "todo") == "open"
        assert registry.get_category("task", "doing") == "wip"
        assert registry.get_category("task", "complete") == "done"

        # Old states must NOT be in cache — they're no longer valid
        assert registry.get_category("task", "open") is None
        assert registry.get_category("task", "in_progress") is None
        assert registry.get_category("task", "closed") is None

    def test_get_valid_states(self, registry: TemplateRegistry) -> None:
        states = registry.get_valid_states("bug")
        assert states is not None
        assert "triage" in states
        assert "closed" in states
        assert len(states) == 6

    def test_get_valid_states_unknown_type(self, registry: TemplateRegistry) -> None:
        assert registry.get_valid_states("unknown") is None

    def test_get_first_state_of_category(self, registry: TemplateRegistry) -> None:
        assert registry.get_first_state_of_category("bug", "open") == "triage"
        assert registry.get_first_state_of_category("bug", "wip") == "fixing"
        assert registry.get_first_state_of_category("bug", "done") == "closed"

    def test_get_first_state_of_category_unknown_type(self, registry: TemplateRegistry) -> None:
        assert registry.get_first_state_of_category("unknown", "open") is None

    def test_parse_type_from_dict(self) -> None:
        """Test parsing a type template from a raw dict (JSON-compatible)."""
        raw = {
            "type": "spike",
            "display_name": "Spike",
            "description": "Investigation",
            "pack": "spike",
            "states": [
                {"name": "proposed", "category": "open"},
                {"name": "investigating", "category": "wip"},
                {"name": "concluded", "category": "done"},
            ],
            "initial_state": "proposed",
            "transitions": [
                {"from": "proposed", "to": "investigating", "enforcement": "soft"},
                {"from": "investigating", "to": "concluded", "enforcement": "hard", "requires_fields": ["findings"]},
            ],
            "fields_schema": [
                {
                    "name": "findings",
                    "type": "text",
                    "description": "What was discovered",
                    "required_at": ["concluded"],
                },
                {"name": "time_box", "type": "text", "description": "Time limit"},
            ],
            "suggested_children": ["finding", "task"],
            "suggested_labels": ["research"],
        }
        tpl = TemplateRegistry.parse_type_template(raw)
        assert tpl.type == "spike"
        assert len(tpl.states) == 3
        assert len(tpl.transitions) == 2
        assert tpl.transitions[1].requires_fields == ("findings",)
        assert tpl.fields_schema[0].required_at == ("concluded",)
        assert tpl.suggested_children == ("finding", "task")

    def test_parse_rejects_invalid_type_name(self) -> None:
        raw = {
            "type": "INVALID",
            "display_name": "Bad",
            "description": "Bad type",
            "states": [{"name": "open", "category": "open"}],
            "initial_state": "open",
            "transitions": [],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match="Invalid type name"):
            TemplateRegistry.parse_type_template(raw)

    def test_parse_rejects_too_many_states(self) -> None:
        raw = {
            "type": "huge",
            "display_name": "Huge",
            "description": "Too many states",
            "states": [{"name": f"s{i}", "category": "open"} for i in range(51)],
            "initial_state": "s0",
            "transitions": [],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match="51 states"):
            TemplateRegistry.parse_type_template(raw)

    def test_validate_type_template_valid(self, registry: TemplateRegistry) -> None:
        tpl = registry.get_type("bug")
        assert tpl is not None
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == []

    def test_validate_type_template_invalid_initial_state(self) -> None:
        tpl = TypeTemplate(
            type="bad",
            display_name="Bad",
            description="Bad",
            pack="test",
            states=(StateDefinition("open", "open"),),
            initial_state="nonexistent",
            transitions=(),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("initial_state" in e for e in errors)

    def test_validate_type_template_invalid_transition_ref(self) -> None:
        tpl = TypeTemplate(
            type="bad",
            display_name="Bad",
            description="Bad",
            pack="test",
            states=(StateDefinition("open", "open"), StateDefinition("closed", "done")),
            initial_state="open",
            transitions=(TransitionDefinition("open", "nonexistent", "soft"),),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("nonexistent" in e for e in errors)

    def test_validate_type_template_invalid_required_at_ref(self) -> None:
        tpl = TypeTemplate(
            type="bad",
            display_name="Bad",
            description="Bad",
            pack="test",
            states=(StateDefinition("open", "open"),),
            initial_state="open",
            transitions=(),
            fields_schema=(FieldSchema("f1", "text", required_at=("nonexistent",)),),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("required_at" in e and "nonexistent" in e for e in errors)

    def test_validate_type_template_invalid_requires_fields_ref(self) -> None:
        tpl = TypeTemplate(
            type="bad",
            display_name="Bad",
            description="Bad",
            pack="test",
            states=(StateDefinition("open", "open"), StateDefinition("closed", "done")),
            initial_state="open",
            transitions=(TransitionDefinition("open", "closed", "soft", requires_fields=("ghost_field",)),),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("ghost_field" in e for e in errors)


class TestTransitionValidation:
    """Test validate_transition, get_valid_transitions, validate_fields_for_state."""

    @pytest.fixture
    def registry(self) -> TemplateRegistry:
        """A registry with bug type for transition testing."""
        reg = TemplateRegistry()
        bug_tpl = TypeTemplate(
            type="bug",
            display_name="Bug",
            description="Bug report",
            pack="core",
            states=(
                StateDefinition("triage", "open"),
                StateDefinition("confirmed", "open"),
                StateDefinition("fixing", "wip"),
                StateDefinition("verifying", "wip"),
                StateDefinition("closed", "done"),
                StateDefinition("wont_fix", "done"),
            ),
            initial_state="triage",
            transitions=(
                TransitionDefinition("triage", "confirmed", "soft"),
                TransitionDefinition("triage", "wont_fix", "soft"),
                TransitionDefinition("confirmed", "fixing", "soft"),
                TransitionDefinition("fixing", "verifying", "soft", requires_fields=("fix_verification",)),
                TransitionDefinition("verifying", "closed", "hard", requires_fields=("fix_verification",)),
                TransitionDefinition("verifying", "fixing", "soft"),
            ),
            fields_schema=(
                FieldSchema("severity", "enum", options=("critical", "major", "minor", "cosmetic"), required_at=("confirmed",)),
                FieldSchema("fix_verification", "text", required_at=("verifying",)),
            ),
        )
        reg._register_type(bug_tpl)
        return reg

    # -- validate_transition tests --

    def test_soft_transition_allowed(self, registry: TemplateRegistry) -> None:
        result = registry.validate_transition("bug", "triage", "confirmed", {})
        assert result.allowed is True
        assert result.enforcement == "soft"

    def test_soft_transition_warns_on_missing_fields(self, registry: TemplateRegistry) -> None:
        result = registry.validate_transition("bug", "triage", "confirmed", {})
        assert result.allowed is True
        assert len(result.warnings) >= 1

    def test_hard_transition_blocks_on_missing_fields(self, registry: TemplateRegistry) -> None:
        result = registry.validate_transition("bug", "verifying", "closed", {})
        assert result.allowed is False
        assert result.enforcement == "hard"
        assert "fix_verification" in result.missing_fields

    def test_hard_transition_allowed_when_fields_present(self, registry: TemplateRegistry) -> None:
        result = registry.validate_transition("bug", "verifying", "closed", {"fix_verification": "Tests pass"})
        assert result.allowed is True
        assert result.enforcement == "hard"

    def test_undefined_transition_rejected_for_known_type(self, registry: TemplateRegistry) -> None:
        """Transitions not in the table are rejected for known types."""
        result = registry.validate_transition("bug", "triage", "closed", {})
        assert result.allowed is False
        assert result.enforcement is None
        assert len(result.warnings) >= 1
        assert "not in the standard workflow" in result.warnings[0]

    def test_empty_string_treated_as_missing(self, registry: TemplateRegistry) -> None:
        result = registry.validate_transition("bug", "verifying", "closed", {"fix_verification": ""})
        assert result.allowed is False

    def test_whitespace_only_treated_as_missing(self, registry: TemplateRegistry) -> None:
        """Whitespace-only string should be treated as unpopulated."""
        result = registry.validate_transition("bug", "verifying", "closed", {"fix_verification": "   "})
        assert result.allowed is False

    def test_none_treated_as_missing(self, registry: TemplateRegistry) -> None:
        result = registry.validate_transition("bug", "verifying", "closed", {"fix_verification": None})
        assert result.allowed is False

    def test_unknown_type_always_allowed(self, registry: TemplateRegistry) -> None:
        """Unknown types get fallback: all transitions allowed (WFT-FR-016)."""
        result = registry.validate_transition("unknown", "open", "closed", {})
        assert result.allowed is True
        assert result.enforcement is None

    # -- get_valid_transitions tests --

    def test_get_valid_transitions_from_triage(self, registry: TemplateRegistry) -> None:
        options = registry.get_valid_transitions("bug", "triage", {})
        assert len(options) == 2
        targets = {o.to for o in options}
        assert targets == {"confirmed", "wont_fix"}

    def test_get_valid_transitions_readiness(self, registry: TemplateRegistry) -> None:
        """Options should show readiness based on missing fields."""
        options = registry.get_valid_transitions("bug", "fixing", {})
        verifying = next(o for o in options if o.to == "verifying")
        assert verifying.ready is False
        assert "fix_verification" in verifying.missing_fields

    def test_get_valid_transitions_ready_when_fields_present(self, registry: TemplateRegistry) -> None:
        options = registry.get_valid_transitions("bug", "fixing", {"fix_verification": "Tests pass"})
        verifying = next(o for o in options if o.to == "verifying")
        assert verifying.ready is True
        assert verifying.missing_fields == ()

    def test_get_valid_transitions_unknown_type(self, registry: TemplateRegistry) -> None:
        options = registry.get_valid_transitions("unknown", "open", {})
        assert options == []

    def test_get_valid_transitions_includes_category(self, registry: TemplateRegistry) -> None:
        options = registry.get_valid_transitions("bug", "triage", {})
        confirmed_opt = next(o for o in options if o.to == "confirmed")
        assert confirmed_opt.category == "open"
        wont_fix_opt = next(o for o in options if o.to == "wont_fix")
        assert wont_fix_opt.category == "done"

    # -- validate_fields_for_state tests --

    def test_validate_fields_for_state(self, registry: TemplateRegistry) -> None:
        missing = registry.validate_fields_for_state("bug", "confirmed", {})
        assert "severity" in missing

    def test_validate_fields_for_state_populated(self, registry: TemplateRegistry) -> None:
        missing = registry.validate_fields_for_state("bug", "confirmed", {"severity": "major"})
        assert missing == []

    def test_validate_fields_for_state_unknown_type(self, registry: TemplateRegistry) -> None:
        missing = registry.validate_fields_for_state("unknown", "open", {})
        assert missing == []

    def test_validate_fields_for_state_no_requirements(self, registry: TemplateRegistry) -> None:
        """State with no required_at fields returns empty list."""
        missing = registry.validate_fields_for_state("bug", "triage", {})
        assert missing == []


class TestBuiltInPackData:
    """Verify built-in pack definitions are structurally valid."""

    def test_core_pack_exists(self) -> None:
        assert "core" in BUILT_IN_PACKS

    def test_planning_pack_exists(self) -> None:
        assert "planning" in BUILT_IN_PACKS

    def test_core_pack_has_four_types(self) -> None:
        core = BUILT_IN_PACKS["core"]
        assert set(core["types"].keys()) == {"task", "bug", "feature", "epic"}

    def test_planning_pack_has_five_types(self) -> None:
        planning = BUILT_IN_PACKS["planning"]
        assert set(planning["types"].keys()) == {"milestone", "phase", "step", "work_package", "deliverable"}

    @pytest.mark.parametrize("type_name", ["task", "bug", "feature", "epic"])
    def test_core_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["core"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    @pytest.mark.parametrize("type_name", ["milestone", "phase", "step", "work_package", "deliverable"])
    def test_planning_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["planning"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    # -- Core pack structural tests --

    def test_core_task_uses_standard_states(self) -> None:
        """Task type must use open/in_progress/closed for backward compat."""
        raw = BUILT_IN_PACKS["core"]["types"]["task"]
        tpl = TemplateRegistry.parse_type_template(raw)
        state_names = [s.name for s in tpl.states]
        assert state_names == ["open", "in_progress", "closed"]

    def test_core_task_initial_state_is_open(self) -> None:
        raw = BUILT_IN_PACKS["core"]["types"]["task"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert tpl.initial_state == "open"

    def test_core_bug_has_six_states(self) -> None:
        raw = BUILT_IN_PACKS["core"]["types"]["bug"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert len(tpl.states) == 6

    def test_core_bug_has_hard_enforcement(self) -> None:
        raw = BUILT_IN_PACKS["core"]["types"]["bug"]
        tpl = TemplateRegistry.parse_type_template(raw)
        hard_transitions = [t for t in tpl.transitions if t.enforcement == "hard"]
        assert len(hard_transitions) >= 1  # verifying->closed at minimum

    def test_core_bug_hard_transition_requires_fix_verification(self) -> None:
        raw = BUILT_IN_PACKS["core"]["types"]["bug"]
        tpl = TemplateRegistry.parse_type_template(raw)
        hard_t = [t for t in tpl.transitions if t.enforcement == "hard"]
        assert any("fix_verification" in t.requires_fields for t in hard_t)

    def test_core_feature_has_deferred_state(self) -> None:
        raw = BUILT_IN_PACKS["core"]["types"]["feature"]
        tpl = TemplateRegistry.parse_type_template(raw)
        state_names = [s.name for s in tpl.states]
        assert "deferred" in state_names

    def test_core_epic_uses_standard_states(self) -> None:
        """Epic type uses open/in_progress/closed like task."""
        raw = BUILT_IN_PACKS["core"]["types"]["epic"]
        tpl = TemplateRegistry.parse_type_template(raw)
        state_names = [s.name for s in tpl.states]
        assert state_names == ["open", "in_progress", "closed"]

    # -- Planning pack structural tests --

    def test_planning_milestone_has_closing_state(self) -> None:
        raw = BUILT_IN_PACKS["planning"]["types"]["milestone"]
        tpl = TemplateRegistry.parse_type_template(raw)
        state_names = [s.name for s in tpl.states]
        assert "closing" in state_names

    def test_planning_phase_has_skipped_state(self) -> None:
        raw = BUILT_IN_PACKS["planning"]["types"]["phase"]
        tpl = TemplateRegistry.parse_type_template(raw)
        state_names = [s.name for s in tpl.states]
        assert "skipped" in state_names

    def test_planning_step_has_skipped_state(self) -> None:
        raw = BUILT_IN_PACKS["planning"]["types"]["step"]
        tpl = TemplateRegistry.parse_type_template(raw)
        state_names = [s.name for s in tpl.states]
        assert "skipped" in state_names

    def test_planning_work_package_has_assigned_state(self) -> None:
        raw = BUILT_IN_PACKS["planning"]["types"]["work_package"]
        tpl = TemplateRegistry.parse_type_template(raw)
        state_names = [s.name for s in tpl.states]
        assert "assigned" in state_names

    def test_planning_deliverable_has_review_cycle(self) -> None:
        """Deliverable should support reviewing->producing loop."""
        raw = BUILT_IN_PACKS["planning"]["types"]["deliverable"]
        tpl = TemplateRegistry.parse_type_template(raw)
        review_back = [t for t in tpl.transitions if t.from_state == "reviewing" and t.to_state == "producing"]
        assert len(review_back) == 1

    # -- Workflow guide tests (WFT-FR-031) --

    def test_core_pack_has_guide(self) -> None:
        guide = BUILT_IN_PACKS["core"].get("guide")
        assert guide is not None

    def test_planning_pack_has_guide(self) -> None:
        guide = BUILT_IN_PACKS["planning"].get("guide")
        assert guide is not None

    def test_risk_pack_has_guide(self) -> None:
        guide = BUILT_IN_PACKS["risk"].get("guide")
        assert guide is not None

    def test_spike_pack_has_guide(self) -> None:
        guide = BUILT_IN_PACKS["spike"].get("guide")
        assert guide is not None

    def test_requirements_pack_has_guide(self) -> None:
        assert BUILT_IN_PACKS["requirements"].get("guide") is not None

    def test_roadmap_pack_has_guide(self) -> None:
        assert BUILT_IN_PACKS["roadmap"].get("guide") is not None

    def test_incident_pack_has_guide(self) -> None:
        assert BUILT_IN_PACKS["incident"].get("guide") is not None

    def test_debt_pack_has_guide(self) -> None:
        assert BUILT_IN_PACKS["debt"].get("guide") is not None

    def test_release_pack_has_guide(self) -> None:
        assert BUILT_IN_PACKS["release"].get("guide") is not None

    @pytest.mark.parametrize("pack_name", _ALL_PACKS)
    def test_guide_has_required_fields(self, pack_name: str) -> None:
        guide = BUILT_IN_PACKS[pack_name]["guide"]
        assert "state_diagram" in guide
        assert "overview" in guide
        assert "when_to_use" in guide
        assert "tips" in guide
        assert "common_mistakes" in guide

    @pytest.mark.parametrize("pack_name", _ALL_PACKS)
    def test_guide_overview_under_50_words(self, pack_name: str) -> None:
        overview = BUILT_IN_PACKS[pack_name]["guide"]["overview"]
        word_count = len(overview.split())
        assert word_count <= 50, f"{pack_name} overview is {word_count} words (max 50)"

    @pytest.mark.parametrize("pack_name", _ALL_PACKS)
    def test_guide_when_to_use_under_30_words(self, pack_name: str) -> None:
        when = BUILT_IN_PACKS[pack_name]["guide"]["when_to_use"]
        word_count = len(when.split())
        assert word_count <= 30, f"{pack_name} when_to_use is {word_count} words (max 30)"

    @pytest.mark.parametrize("pack_name", _ALL_PACKS)
    def test_guide_tips_is_list(self, pack_name: str) -> None:
        tips = BUILT_IN_PACKS[pack_name]["guide"]["tips"]
        assert isinstance(tips, list)
        assert len(tips) >= 3

    @pytest.mark.parametrize("pack_name", _ALL_PACKS)
    def test_guide_common_mistakes_is_list(self, pack_name: str) -> None:
        mistakes = BUILT_IN_PACKS[pack_name]["guide"]["common_mistakes"]
        assert isinstance(mistakes, list)
        assert len(mistakes) >= 2

    @pytest.mark.parametrize("pack_name", _ALL_PACKS)
    def test_guide_state_diagram_is_string(self, pack_name: str) -> None:
        diagram = BUILT_IN_PACKS[pack_name]["guide"]["state_diagram"]
        assert isinstance(diagram, str)
        assert len(diagram) > 20  # Not empty/trivial

    @pytest.mark.parametrize("pack_name", _PACKS_WITH_STATES_EXPLAINED)
    def test_guide_states_explained_covers_all_states(self, pack_name: str) -> None:
        """states_explained should have entries for every state defined in the pack's types."""
        pack = BUILT_IN_PACKS[pack_name]
        guide = pack["guide"]
        assert "states_explained" in guide
        all_state_names: set[str] = set()
        for type_def in pack["types"].values():
            for state in type_def["states"]:
                all_state_names.add(state["name"])
        explained = set(guide["states_explained"].keys())
        missing = all_state_names - explained
        assert not missing, f"{pack_name} guide missing states_explained for: {missing}"

    # -- Pack metadata tests --

    def test_core_pack_version(self) -> None:
        assert BUILT_IN_PACKS["core"]["version"] == "1.0"

    def test_planning_pack_requires_core(self) -> None:
        assert "core" in BUILT_IN_PACKS["planning"]["requires_packs"]

    def test_core_pack_requires_nothing(self) -> None:
        assert BUILT_IN_PACKS["core"]["requires_packs"] == []

    def test_planning_pack_has_relationships(self) -> None:
        rels = BUILT_IN_PACKS["planning"]["relationships"]
        assert len(rels) >= 3  # milestone->phase, phase->step, work_package->milestone at minimum

    # -- All types have required fields --

    # -- Risk pack structural tests --

    def test_risk_pack_exists(self) -> None:
        assert "risk" in BUILT_IN_PACKS

    def test_risk_pack_has_two_types(self) -> None:
        risk = BUILT_IN_PACKS["risk"]
        assert set(risk["types"].keys()) == {"risk", "mitigation"}

    @pytest.mark.parametrize("type_name", ["risk", "mitigation"])
    def test_risk_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["risk"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    def test_risk_type_has_eight_states(self) -> None:
        raw = BUILT_IN_PACKS["risk"]["types"]["risk"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert len(tpl.states) == 8

    def test_risk_type_initial_state_is_identified(self) -> None:
        raw = BUILT_IN_PACKS["risk"]["types"]["risk"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert tpl.initial_state == "identified"

    def test_risk_type_has_three_hard_gates(self) -> None:
        raw = BUILT_IN_PACKS["risk"]["types"]["risk"]
        tpl = TemplateRegistry.parse_type_template(raw)
        hard = [t for t in tpl.transitions if t.enforcement == "hard"]
        assert len(hard) == 3

    def test_risk_assessment_gate_requires_score_and_impact(self) -> None:
        raw = BUILT_IN_PACKS["risk"]["types"]["risk"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.from_state == "assessing" and t.to_state == "assessed")
        assert "risk_score" in gate.requires_fields
        assert "impact" in gate.requires_fields

    def test_risk_acceptance_gate_requires_owner_and_rationale(self) -> None:
        raw = BUILT_IN_PACKS["risk"]["types"]["risk"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.from_state == "assessed" and t.to_state == "accepted")
        assert "risk_owner" in gate.requires_fields
        assert "acceptance_rationale" in gate.requires_fields

    def test_mitigation_type_has_five_states(self) -> None:
        raw = BUILT_IN_PACKS["risk"]["types"]["mitigation"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert len(tpl.states) == 5

    def test_mitigation_has_replan_loop(self) -> None:
        """ineffective -> planned replan transition should exist."""
        raw = BUILT_IN_PACKS["risk"]["types"]["mitigation"]
        tpl = TemplateRegistry.parse_type_template(raw)
        replan = [t for t in tpl.transitions if t.from_state == "ineffective" and t.to_state == "planned"]
        assert len(replan) == 1

    def test_risk_pack_has_relationships(self) -> None:
        rels = BUILT_IN_PACKS["risk"]["relationships"]
        assert len(rels) >= 2
        names = {r["name"] for r in rels}
        assert "mitigation_for" in names
        assert "risk_threatens" in names

    def test_risk_pack_has_cross_pack_relationships(self) -> None:
        cross = BUILT_IN_PACKS["risk"]["cross_pack_relationships"]
        assert len(cross) >= 1
        names = {r["name"] for r in cross}
        assert "spike_investigates_risk" in names

    # -- Spike pack structural tests --

    def test_spike_pack_exists(self) -> None:
        assert "spike" in BUILT_IN_PACKS

    def test_spike_pack_has_two_types(self) -> None:
        spike = BUILT_IN_PACKS["spike"]
        assert set(spike["types"].keys()) == {"spike", "finding"}

    @pytest.mark.parametrize("type_name", ["spike", "finding"])
    def test_spike_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["spike"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    def test_spike_type_has_five_states(self) -> None:
        raw = BUILT_IN_PACKS["spike"]["types"]["spike"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert len(tpl.states) == 5

    def test_spike_type_initial_state_is_proposed(self) -> None:
        raw = BUILT_IN_PACKS["spike"]["types"]["spike"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert tpl.initial_state == "proposed"

    def test_spike_type_has_one_hard_gate(self) -> None:
        raw = BUILT_IN_PACKS["spike"]["types"]["spike"]
        tpl = TemplateRegistry.parse_type_template(raw)
        hard = [t for t in tpl.transitions if t.enforcement == "hard"]
        assert len(hard) == 1

    def test_spike_conclusion_gate_requires_findings(self) -> None:
        raw = BUILT_IN_PACKS["spike"]["types"]["spike"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.from_state == "investigating" and t.to_state == "concluded")
        assert "findings" in gate.requires_fields

    def test_finding_type_has_two_states(self) -> None:
        raw = BUILT_IN_PACKS["spike"]["types"]["finding"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert len(tpl.states) == 2

    def test_finding_type_initial_state_is_draft(self) -> None:
        raw = BUILT_IN_PACKS["spike"]["types"]["finding"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert tpl.initial_state == "draft"

    def test_spike_pack_has_relationships(self) -> None:
        rels = BUILT_IN_PACKS["spike"]["relationships"]
        assert len(rels) >= 2
        names = {r["name"] for r in rels}
        assert "finding_from_spike" in names
        assert "spike_investigates" in names

    def test_spike_pack_has_cross_pack_relationships(self) -> None:
        cross = BUILT_IN_PACKS["spike"]["cross_pack_relationships"]
        assert len(cross) >= 2
        names = {r["name"] for r in cross}
        assert "spike_spawns_work" in names
        assert "spike_spawns_mitigation" in names

    def test_spike_spawns_direction_matches_dependency_contract(self) -> None:
        """Bug filigree-fa979c: spawned items must be from_types (they depend on the spike)."""
        cross = BUILT_IN_PACKS["spike"]["cross_pack_relationships"]
        spawns_work = next(r for r in cross if r["name"] == "spike_spawns_work")
        spawns_mitigation = next(r for r in cross if r["name"] == "spike_spawns_mitigation")
        # from_id depends on to_id — spawned items (from) depend on spike (to)
        assert "spike" in spawns_work["to_types"]
        assert "spike" not in spawns_work["from_types"]
        assert "spike" in spawns_mitigation["to_types"]
        assert "spike" not in spawns_mitigation["from_types"]

    # -- Requirements pack structural tests --

    def test_requirements_pack_exists(self) -> None:
        assert "requirements" in BUILT_IN_PACKS

    def test_requirements_pack_has_two_types(self) -> None:
        assert set(BUILT_IN_PACKS["requirements"]["types"].keys()) == {"requirement", "acceptance_criterion"}

    @pytest.mark.parametrize("type_name", ["requirement", "acceptance_criterion"])
    def test_requirements_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["requirements"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    def test_requirement_type_has_seven_states(self) -> None:
        raw = BUILT_IN_PACKS["requirements"]["types"]["requirement"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert len(tpl.states) == 7

    def test_requirement_verification_hard_gate(self) -> None:
        raw = BUILT_IN_PACKS["requirements"]["types"]["requirement"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.from_state == "implementing" and t.to_state == "verified")
        assert gate.enforcement == "hard"
        assert "verification_method" in gate.requires_fields

    # -- Roadmap pack structural tests --

    def test_roadmap_pack_exists(self) -> None:
        assert "roadmap" in BUILT_IN_PACKS

    def test_roadmap_pack_has_three_types(self) -> None:
        assert set(BUILT_IN_PACKS["roadmap"]["types"].keys()) == {"theme", "objective", "key_result"}

    @pytest.mark.parametrize("type_name", ["theme", "objective", "key_result"])
    def test_roadmap_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["roadmap"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    def test_key_result_has_two_hard_gates(self) -> None:
        raw = BUILT_IN_PACKS["roadmap"]["types"]["key_result"]
        tpl = TemplateRegistry.parse_type_template(raw)
        hard = [t for t in tpl.transitions if t.enforcement == "hard"]
        assert len(hard) == 2  # tracking->met and tracking->missed

    def test_key_result_met_requires_current_value(self) -> None:
        raw = BUILT_IN_PACKS["roadmap"]["types"]["key_result"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.to_state == "met")
        assert "current_value" in gate.requires_fields

    # -- Incident pack structural tests --

    def test_incident_pack_exists(self) -> None:
        assert "incident" in BUILT_IN_PACKS

    def test_incident_pack_has_two_types(self) -> None:
        assert set(BUILT_IN_PACKS["incident"]["types"].keys()) == {"incident", "postmortem"}

    @pytest.mark.parametrize("type_name", ["incident", "postmortem"])
    def test_incident_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["incident"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    def test_incident_type_has_six_states(self) -> None:
        raw = BUILT_IN_PACKS["incident"]["types"]["incident"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert len(tpl.states) == 6

    def test_incident_triage_requires_severity(self) -> None:
        raw = BUILT_IN_PACKS["incident"]["types"]["incident"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.from_state == "reported" and t.to_state == "triaging")
        assert gate.enforcement == "hard"
        assert "severity" in gate.requires_fields

    def test_incident_close_requires_root_cause(self) -> None:
        raw = BUILT_IN_PACKS["incident"]["types"]["incident"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.from_state == "resolved" and t.to_state == "closed")
        assert gate.enforcement == "hard"
        assert "root_cause" in gate.requires_fields

    def test_postmortem_publish_requires_action_items(self) -> None:
        raw = BUILT_IN_PACKS["incident"]["types"]["postmortem"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.from_state == "reviewing" and t.to_state == "published")
        assert gate.enforcement == "hard"
        assert "action_items" in gate.requires_fields

    # -- Debt pack structural tests --

    def test_debt_pack_exists(self) -> None:
        assert "debt" in BUILT_IN_PACKS

    def test_debt_pack_has_two_types(self) -> None:
        assert set(BUILT_IN_PACKS["debt"]["types"].keys()) == {"debt_item", "remediation"}

    @pytest.mark.parametrize("type_name", ["debt_item", "remediation"])
    def test_debt_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["debt"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    def test_debt_assessment_requires_category_and_impact(self) -> None:
        raw = BUILT_IN_PACKS["debt"]["types"]["debt_item"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.from_state == "identified" and t.to_state == "assessed")
        assert gate.enforcement == "hard"
        assert "debt_category" in gate.requires_fields
        assert "impact" in gate.requires_fields

    def test_debt_remediation_has_four_states(self) -> None:
        raw = BUILT_IN_PACKS["debt"]["types"]["remediation"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert len(tpl.states) == 4

    # -- Release pack structural tests --

    def test_release_pack_exists(self) -> None:
        assert "release" in BUILT_IN_PACKS

    def test_release_pack_has_two_types(self) -> None:
        assert set(BUILT_IN_PACKS["release"]["types"].keys()) == {"release", "release_item"}

    @pytest.mark.parametrize("type_name", ["release", "release_item"])
    def test_release_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["release"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    def test_release_type_has_nine_states(self) -> None:
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        tpl = TemplateRegistry.parse_type_template(raw)
        assert len(tpl.states) == 9

    def test_release_freeze_requires_version(self) -> None:
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        tpl = TemplateRegistry.parse_type_template(raw)
        gate = next(t for t in tpl.transitions if t.from_state == "development" and t.to_state == "frozen")
        assert gate.enforcement == "hard"
        assert "version" in gate.requires_fields

    def test_release_allows_rollback(self) -> None:
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        tpl = TemplateRegistry.parse_type_template(raw)
        rollback = [t for t in tpl.transitions if t.from_state == "released" and t.to_state == "rolled_back"]
        assert len(rollback) == 1

    # -- All types have required fields (all packs) --

    @pytest.mark.parametrize(
        ("pack_name", "type_name"),
        [
            ("core", "task"),
            ("core", "bug"),
            ("core", "feature"),
            ("core", "epic"),
            ("planning", "milestone"),
            ("planning", "phase"),
            ("planning", "step"),
            ("planning", "work_package"),
            ("planning", "deliverable"),
            ("risk", "risk"),
            ("risk", "mitigation"),
            ("spike", "spike"),
            ("spike", "finding"),
            ("requirements", "requirement"),
            ("requirements", "acceptance_criterion"),
            ("roadmap", "theme"),
            ("roadmap", "objective"),
            ("roadmap", "key_result"),
            ("incident", "incident"),
            ("incident", "postmortem"),
            ("debt", "debt_item"),
            ("debt", "remediation"),
            ("release", "release"),
            ("release", "release_item"),
        ],
    )
    def test_every_type_has_states_transitions_fields(self, pack_name: str, type_name: str) -> None:
        raw = BUILT_IN_PACKS[pack_name]["types"][type_name]
        assert "states" in raw
        assert len(raw["states"]) >= 2
        assert "transitions" in raw
        assert len(raw["transitions"]) >= 1
        assert "fields_schema" in raw
        assert "initial_state" in raw


class TestTemplateLoading:
    """Test three-layer template resolution."""

    @pytest.fixture
    def filigree_dir(self, tmp_path: Path) -> Path:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        return filigree_dir

    def test_load_built_ins(self, filigree_dir: Path) -> None:
        reg = TemplateRegistry()
        reg.load(filigree_dir)
        assert reg.get_type("task") is not None
        assert reg.get_type("bug") is not None
        assert reg.get_type("milestone") is not None

    def test_load_respects_enabled_packs(self, filigree_dir: Path) -> None:
        """Only types from enabled packs should be available."""
        reg = TemplateRegistry()
        reg.load(filigree_dir)
        # Core and planning enabled — their types exist
        assert reg.get_type("task") is not None
        assert reg.get_type("milestone") is not None

    def test_load_enabled_packs_override(self, filigree_dir: Path) -> None:
        """Explicit enabled_packs argument should override config selection."""
        reg = TemplateRegistry()
        reg.load(filigree_dir, enabled_packs=["core"])
        assert reg.get_type("task") is not None
        assert reg.get_type("milestone") is None

    def test_load_is_idempotent(self, filigree_dir: Path) -> None:
        reg = TemplateRegistry()
        reg.load(filigree_dir)
        types_count_1 = len(reg.list_types())
        reg.load(filigree_dir)
        types_count_2 = len(reg.list_types())
        assert types_count_1 == types_count_2

    def test_load_project_override(self, filigree_dir: Path) -> None:
        """Layer 3 (project-local) overrides built-in types."""
        templates_dir = filigree_dir / "templates"
        templates_dir.mkdir()
        custom_task = {
            "type": "task",
            "display_name": "Custom Task",
            "description": "Overridden task",
            "pack": "core",
            "states": [
                {"name": "todo", "category": "open"},
                {"name": "doing", "category": "wip"},
                {"name": "done", "category": "done"},
            ],
            "initial_state": "todo",
            "transitions": [
                {"from": "todo", "to": "doing", "enforcement": "soft"},
                {"from": "doing", "to": "done", "enforcement": "soft"},
            ],
            "fields_schema": [],
        }
        (templates_dir / "task.json").write_text(json.dumps(custom_task))

        reg = TemplateRegistry()
        reg.load(filigree_dir)
        task = reg.get_type("task")
        assert task is not None
        assert task.display_name == "Custom Task"
        assert task.initial_state == "todo"

    def test_load_skips_invalid_json(self, filigree_dir: Path) -> None:
        """Invalid JSON files in templates/ should be skipped, not crash."""
        templates_dir = filigree_dir / "templates"
        templates_dir.mkdir()
        (templates_dir / "broken.json").write_text("not valid json {{{")

        reg = TemplateRegistry()
        reg.load(filigree_dir)  # Should not raise
        assert reg.get_type("task") is not None  # Built-ins still loaded

    def test_load_missing_enabled_packs_defaults(self, filigree_dir: Path) -> None:
        """Config without enabled_packs defaults to core + planning."""
        config = {"prefix": "test", "version": 1}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        reg = TemplateRegistry()
        reg.load(filigree_dir)
        assert reg.get_type("task") is not None

    def test_load_installed_pack_layer2(self, filigree_dir: Path) -> None:
        """Layer 2: packs from .filigree/packs/*.json are loaded."""
        packs_dir = filigree_dir / "packs"
        packs_dir.mkdir()
        custom_pack = {
            "pack": "custom_pack",
            "version": "1.0",
            "display_name": "Custom",
            "description": "Custom installed pack",
            "requires_packs": [],
            "types": {
                "custom_type": {
                    "type": "custom_type",
                    "display_name": "Custom Type",
                    "description": "A custom type",
                    "pack": "custom_pack",
                    "states": [
                        {"name": "open", "category": "open"},
                        {"name": "closed", "category": "done"},
                    ],
                    "initial_state": "open",
                    "transitions": [
                        {"from": "open", "to": "closed", "enforcement": "soft"},
                    ],
                    "fields_schema": [],
                },
            },
            "relationships": [],
            "cross_pack_relationships": [],
            "guide": None,
        }
        (packs_dir / "custom_pack.json").write_text(json.dumps(custom_pack))

        # Enable the custom pack in config
        config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning", "custom_pack"]}
        (filigree_dir / "config.json").write_text(json.dumps(config))

        reg = TemplateRegistry()
        reg.load(filigree_dir)
        assert reg.get_type("custom_type") is not None

    def test_load_disabled_pack_not_loaded(self, filigree_dir: Path) -> None:
        """Packs not in enabled_packs should not have their types loaded."""
        packs_dir = filigree_dir / "packs"
        packs_dir.mkdir()
        extra_pack = {
            "pack": "extra",
            "version": "1.0",
            "display_name": "Extra",
            "description": "Not enabled",
            "requires_packs": [],
            "types": {
                "extra_type": {
                    "type": "extra_type",
                    "display_name": "Extra",
                    "description": "Extra type",
                    "pack": "extra",
                    "states": [{"name": "open", "category": "open"}],
                    "initial_state": "open",
                    "transitions": [],
                    "fields_schema": [],
                },
            },
            "relationships": [],
            "cross_pack_relationships": [],
            "guide": None,
        }
        (packs_dir / "extra.json").write_text(json.dumps(extra_pack))

        # Only core+planning enabled, NOT extra
        config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}
        (filigree_dir / "config.json").write_text(json.dumps(config))

        reg = TemplateRegistry()
        reg.load(filigree_dir)
        assert reg.get_type("extra_type") is None

    def test_load_non_dict_config_json_uses_defaults(self, tmp_path: Path) -> None:
        """config.json containing non-dict JSON (e.g. []) must not crash load()."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        # Write a valid JSON array — not a dict
        (filigree_dir / "config.json").write_text("[]")

        reg = TemplateRegistry()
        reg.load(filigree_dir)  # Should not raise
        # Should fall back to defaults (core + planning)
        assert reg.get_type("task") is not None
        assert reg.get_type("milestone") is not None

    def test_load_logs_quality_warnings(self, filigree_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Quality warnings should be logged during load (filigree-e71b54)."""
        import logging

        # Create a template with a dead-end non-done state (quality warning)
        templates_dir = filigree_dir / "templates"
        templates_dir.mkdir()
        dead_end_type = {
            "type": "deadend_test",
            "display_name": "Dead End Test",
            "description": "Type with a dead-end state for quality test",
            "pack": "core",
            "states": [
                {"name": "open", "category": "open"},
                {"name": "working", "category": "wip"},
                {"name": "stuck", "category": "wip"},
                {"name": "done", "category": "done"},
            ],
            "initial_state": "open",
            "transitions": [
                {"from": "open", "to": "working", "enforcement": "soft"},
                {"from": "open", "to": "stuck", "enforcement": "soft"},
                {"from": "working", "to": "done", "enforcement": "soft"},
                # stuck has no outgoing transition — dead end
            ],
            "fields_schema": [],
        }
        (templates_dir / "deadend_test.json").write_text(json.dumps(dead_end_type))

        reg = TemplateRegistry()
        with caplog.at_level(logging.WARNING, logger="filigree.templates"):
            reg.load(filigree_dir)

        # Type should still be registered (quality warnings are non-blocking)
        assert reg.get_type("deadend_test") is not None
        # But warning should be logged
        quality_warnings = [r for r in caplog.records if "dead end" in r.message]
        assert len(quality_warnings) > 0, "Expected dead-end quality warning in logs"

    def test_load_logs_done_state_outgoing_transition_warning(self, filigree_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Done-states with outgoing transitions should produce quality warning."""
        import logging

        templates_dir = filigree_dir / "templates"
        templates_dir.mkdir()
        done_outgoing_type = {
            "type": "done_outgoing_test",
            "display_name": "Done Outgoing Test",
            "description": "Type with a done state that has outgoing transitions",
            "pack": "core",
            "states": [
                {"name": "open", "category": "open"},
                {"name": "finished", "category": "done"},
                {"name": "reverted", "category": "done"},
            ],
            "initial_state": "open",
            "transitions": [
                {"from": "open", "to": "finished", "enforcement": "soft"},
                {"from": "finished", "to": "reverted", "enforcement": "soft"},
            ],
            "fields_schema": [],
        }
        (templates_dir / "done_outgoing_test.json").write_text(json.dumps(done_outgoing_type))

        reg = TemplateRegistry()
        with caplog.at_level(logging.WARNING, logger="filigree.templates"):
            reg.load(filigree_dir)

        # Type should still be registered
        assert reg.get_type("done_outgoing_test") is not None
        # Warning about done state with outgoing transition
        quality_warnings = [r for r in caplog.records if "finished" in r.message and "done" in r.message.lower()]
        assert len(quality_warnings) > 0, "Expected done-state-with-outgoing-transition warning"


class TestQualityCheckDoneOutgoing:
    """check_type_template_quality() detects done-states with outgoing transitions."""

    def test_done_state_with_outgoing_transition_warned(self) -> None:
        """A done-category state that has outgoing transitions produces a warning."""
        tpl = TypeTemplate(
            type="test_type",
            display_name="Test",
            description="",
            pack="test",
            states=(
                StateDefinition(name="open", category="open"),
                StateDefinition(name="released", category="done"),
                StateDefinition(name="rolled_back", category="done"),
            ),
            initial_state="open",
            transitions=(
                TransitionDefinition(from_state="open", to_state="released", enforcement="soft"),
                TransitionDefinition(from_state="released", to_state="rolled_back", enforcement="soft"),
            ),
            fields_schema=(),
        )
        warnings = TemplateRegistry.check_type_template_quality(tpl)
        done_outgoing = [w for w in warnings if "released" in w and "done" in w.lower()]
        assert len(done_outgoing) == 1

    def test_done_state_without_outgoing_no_warning(self) -> None:
        """A terminal done state should NOT produce a warning."""
        tpl = TypeTemplate(
            type="test_type",
            display_name="Test",
            description="",
            pack="test",
            states=(
                StateDefinition(name="open", category="open"),
                StateDefinition(name="closed", category="done"),
            ),
            initial_state="open",
            transitions=(TransitionDefinition(from_state="open", to_state="closed", enforcement="soft"),),
            fields_schema=(),
        )
        warnings = TemplateRegistry.check_type_template_quality(tpl)
        done_outgoing = [w for w in warnings if "done" in w.lower() and "outgoing" in w]
        assert done_outgoing == []

    def test_builtin_spike_concluded_warned(self) -> None:
        """spike.concluded (done) has outgoing transition to actioned — should warn."""
        from filigree.templates_data import BUILT_IN_PACKS

        raw = BUILT_IN_PACKS["spike"]["types"]["spike"]
        tpl = TemplateRegistry.parse_type_template(raw)
        warnings = TemplateRegistry.check_type_template_quality(tpl)
        concluded_warnings = [w for w in warnings if "concluded" in w]
        assert len(concluded_warnings) == 1

    def test_builtin_release_no_done_outgoing_warnings(self) -> None:
        """released→rolled_back is done→wip, which is allowed (reachable via update_issue).

        The quality check only warns about done→done transitions, which are truly
        unreachable since close_issue() rejects issues already in a done state.
        """
        from filigree.templates_data import BUILT_IN_PACKS

        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        tpl = TemplateRegistry.parse_type_template(raw)
        warnings = TemplateRegistry.check_type_template_quality(tpl)
        done_outgoing = [w for w in warnings if "done state" in w]
        assert len(done_outgoing) == 0
