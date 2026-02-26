"""Tests for template transition validation and enforcement."""

from __future__ import annotations

import pytest

from filigree.core import FiligreeDB
from filigree.templates import (
    FieldSchema,
    StateDefinition,
    TemplateRegistry,
    TransitionDefinition,
    TransitionResult,
    TypeTemplate,
)
from filigree.templates_data import BUILT_IN_PACKS


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


class TestIncidentResolvedCategory:
    """Bug fix: filigree-bf9926 — incident.resolved blocks close_issue()."""

    def test_incident_resolved_is_not_done_category(self) -> None:
        """incident.resolved must be 'wip', not 'done' — it has a mandatory
        resolved→closed transition requiring root_cause."""
        raw = BUILT_IN_PACKS["incident"]["types"]["incident"]
        states = {s["name"]: s["category"] for s in raw["states"]}
        assert states["resolved"] != "done", (
            "incident.resolved should not be 'done' — it has an outgoing transition to 'closed' that requires root_cause"
        )
        assert states["resolved"] == "wip"

    def test_close_issue_from_resolved_works(self, incident_db: FiligreeDB) -> None:
        """An incident in 'resolved' state should be closeable via close_issue()
        when the hard-required root_cause field is supplied."""
        issue = incident_db.create_issue("Outage", type="incident")

        # Walk the incident workflow: reported → triaging → investigating → resolved
        incident_db.update_issue(issue.id, status="triaging", fields={"severity": "sev2"})
        incident_db.update_issue(issue.id, status="investigating")
        incident_db.update_issue(issue.id, status="resolved")

        # Verify it's in resolved state
        resolved = incident_db.get_issue(issue.id)
        assert resolved.status == "resolved"

        # close_issue() should NOT raise "already closed" — but must satisfy
        # the hard-enforcement gate by providing root_cause (filigree-87e5e3)
        closed = incident_db.close_issue(
            issue.id,
            fields={"root_cause": "Config drift"},
            reason="Root cause: config drift",
        )
        assert closed.status_category == "done"
        assert closed.closed_at is not None

    def test_incident_closed_is_still_done(self) -> None:
        """incident.closed should remain 'done' (the terminal state)."""
        raw = BUILT_IN_PACKS["incident"]["types"]["incident"]
        states = {s["name"]: s["category"] for s in raw["states"]}
        assert states["closed"] == "done"
