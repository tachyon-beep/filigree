"""Tests for template validation bug fixes.

Covers:
- incident.resolved category fix (filigree-bf9926)
- StateDefinition.category validation (filigree-fe2078)
- Duplicate state name detection (filigree-eff214)
- enabled_packs config type validation (filigree-d3dd2e)
- parse_type_template() malformed transitions/fields (filigree-b25e83)
- FieldSchema.type parse-time validation (filigree-ca5711)
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import ClassVar

import pytest

from filigree.core import FiligreeDB
from filigree.templates import FieldSchema, StateDefinition, TemplateRegistry, TypeTemplate
from filigree.templates_data import BUILT_IN_PACKS

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def incident_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with incident pack enabled."""
    d = FiligreeDB(
        tmp_path / "filigree.db",
        prefix="test",
        enabled_packs=["core", "planning", "incident"],
    )
    d.initialize()
    yield d
    d.close()


# -- Bug: filigree-bf9926 — incident.resolved wrongly categorized as done ----


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


# -- Bug: filigree-fe2078 — StateDefinition.category never validated ----------


class TestStateCategoryValidation:
    """Bug fix: filigree-fe2078 — invalid categories silently accepted."""

    def test_valid_categories_accepted(self) -> None:
        """open, wip, done should be accepted without error."""
        for cat in ("open", "wip", "done"):
            sd = StateDefinition(name="test_state", category=cat)  # type: ignore[arg-type]
            assert sd.category == cat

    def test_invalid_category_raises_valueerror(self) -> None:
        """An invalid category string should be rejected at construction time."""
        with pytest.raises(ValueError, match=r"[Ii]nvalid.*category"):
            StateDefinition(name="broken_state", category="bogus")  # type: ignore[arg-type]

    def test_empty_category_raises_valueerror(self) -> None:
        """An empty category string should be rejected."""
        with pytest.raises(ValueError, match=r"[Ii]nvalid.*category"):
            StateDefinition(name="empty_cat", category="")  # type: ignore[arg-type]

    def test_typo_category_raises_valueerror(self) -> None:
        """Common typos like 'Done' or 'WIP' should be rejected (case-sensitive)."""
        with pytest.raises(ValueError, match=r"[Ii]nvalid.*category"):
            StateDefinition(name="typo_state", category="Done")  # type: ignore[arg-type]


# -- Bug: filigree-eff214 — Duplicate state names silently overwrite ----------


class TestDuplicateStateNameDetection:
    """Bug fix: filigree-eff214 — duplicate states silently overwrite in cache."""

    def test_duplicate_state_names_detected_in_validation(self) -> None:
        """validate_type_template should report duplicate state names as errors."""
        tpl = TypeTemplate(
            type="test_type",
            display_name="Test",
            description="",
            pack="test",
            states=(
                StateDefinition(name="open", category="open"),
                StateDefinition(name="closed", category="done"),
                StateDefinition(name="open", category="wip"),  # duplicate name!
            ),
            initial_state="open",
            transitions=(),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("duplicate" in e.lower() or "open" in e for e in errors), f"Expected duplicate state name error, got: {errors}"

    def test_parse_duplicate_state_names_raises(self) -> None:
        """parse_type_template should reject duplicate state names."""
        raw = {
            "type": "dup_test",
            "display_name": "Dup Test",
            "states": [
                {"name": "open", "category": "open"},
                {"name": "closed", "category": "done"},
                {"name": "open", "category": "wip"},  # duplicate!
            ],
            "initial_state": "open",
            "transitions": [],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match=r"[Dd]uplicate.*state"):
            TemplateRegistry.parse_type_template(raw)

    def test_no_false_positive_on_unique_states(self) -> None:
        """Templates with unique state names should validate cleanly."""
        tpl = TypeTemplate(
            type="clean_type",
            display_name="Clean",
            description="",
            pack="test",
            states=(
                StateDefinition(name="open", category="open"),
                StateDefinition(name="in_progress", category="wip"),
                StateDefinition(name="closed", category="done"),
            ),
            initial_state="open",
            transitions=(),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        dup_errors = [e for e in errors if "duplicate" in e.lower()]
        assert dup_errors == []


# -- Bug: filigree-d3dd2e — enabled_packs config not type-validated ----------


class TestEnabledPacksValidation:
    """Bug fix: filigree-d3dd2e — malformed enabled_packs crash or mis-select."""

    def test_string_enabled_packs_in_config(self, tmp_path: Path) -> None:
        """A string value for enabled_packs should not silently split into chars."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"enabled_packs": "core"}  # string, not list
        (filigree_dir / "config.json").write_text(json.dumps(config))
        reg = TemplateRegistry()
        reg.load(filigree_dir)
        # Should fall back to defaults or use ["core"], not ['c','o','r','e']
        assert reg.get_type("task") is not None

    def test_integer_enabled_packs_in_config(self, tmp_path: Path) -> None:
        """A non-iterable value should not crash — should fall back to defaults."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"enabled_packs": 42}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        reg = TemplateRegistry()
        reg.load(filigree_dir)  # Must not crash
        assert reg.get_type("task") is not None

    def test_list_with_non_string_elements(self, tmp_path: Path) -> None:
        """Elements that aren't strings should be handled gracefully."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"enabled_packs": ["core", 123, None]}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        reg = TemplateRegistry()
        reg.load(filigree_dir)  # Must not crash
        assert reg.get_type("task") is not None

    def test_string_enabled_packs_override_parameter(self, tmp_path: Path) -> None:
        """Passing a string as enabled_packs parameter should not split into chars."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        (filigree_dir / "config.json").write_text("{}")
        reg = TemplateRegistry()
        reg.load(filigree_dir, enabled_packs="core")  # type: ignore[arg-type]
        # Should load core pack, not ['c','o','r','e']
        assert reg.get_type("task") is not None


# -- Bug: filigree-b25e83 — parse_type_template() TypeError on malformed data -


class TestParseTemplateMalformedTransitionsFields:
    """Bug fix: filigree-b25e83 — raw TypeError for non-list transitions/fields."""

    _VALID_BASE: ClassVar[dict] = {
        "type": "test_type",
        "display_name": "Test",
        "states": [
            {"name": "open", "category": "open"},
            {"name": "closed", "category": "done"},
        ],
        "initial_state": "open",
    }

    def test_transitions_as_string_raises_valueerror(self) -> None:
        """A string 'transitions' should raise ValueError, not TypeError."""
        raw = {**self._VALID_BASE, "transitions": "not a list", "fields_schema": []}
        with pytest.raises(ValueError, match=r"transitions.*must be a list"):
            TemplateRegistry.parse_type_template(raw)

    def test_transitions_as_dict_raises_valueerror(self) -> None:
        raw = {**self._VALID_BASE, "transitions": {"from": "open"}, "fields_schema": []}
        with pytest.raises(ValueError, match=r"transitions.*must be a list"):
            TemplateRegistry.parse_type_template(raw)

    def test_fields_schema_as_string_raises_valueerror(self) -> None:
        raw = {**self._VALID_BASE, "transitions": [], "fields_schema": "not a list"}
        with pytest.raises(ValueError, match=r"fields_schema.*must be a list"):
            TemplateRegistry.parse_type_template(raw)

    def test_fields_schema_as_int_raises_valueerror(self) -> None:
        raw = {**self._VALID_BASE, "transitions": [], "fields_schema": 42}
        with pytest.raises(ValueError, match=r"fields_schema.*must be a list"):
            TemplateRegistry.parse_type_template(raw)

    def test_transition_element_not_dict_raises_valueerror(self) -> None:
        raw = {**self._VALID_BASE, "transitions": ["not a dict"], "fields_schema": []}
        with pytest.raises(ValueError, match=r"transition.*must be a dict"):
            TemplateRegistry.parse_type_template(raw)

    def test_field_element_not_dict_raises_valueerror(self) -> None:
        raw = {**self._VALID_BASE, "transitions": [], "fields_schema": ["not a dict"]}
        with pytest.raises(ValueError, match=r"field.*must be a dict"):
            TemplateRegistry.parse_type_template(raw)


# -- Bug: filigree-ca5711 — FieldSchema.type not validated at parse time ------


class TestFieldSchemaTypeValidation:
    """Bug fix: filigree-ca5711 — invalid FieldSchema.type silently accepted."""

    def test_valid_field_types_accepted(self) -> None:
        for ft in ("text", "enum", "number", "date", "list", "boolean"):
            fs = FieldSchema(name="test_field", type=ft)  # type: ignore[arg-type]
            assert fs.type == ft

    def test_invalid_field_type_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match=r"[Ii]nvalid.*field type"):
            FieldSchema(name="bad_field", type="integer")  # type: ignore[arg-type]

    def test_empty_field_type_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match=r"[Ii]nvalid.*field type"):
            FieldSchema(name="bad_field", type="")  # type: ignore[arg-type]

    def test_parse_template_rejects_invalid_field_type(self) -> None:
        """parse_type_template should reject fields with invalid type values."""
        raw = {
            "type": "test_type",
            "display_name": "Test",
            "states": [
                {"name": "open", "category": "open"},
                {"name": "closed", "category": "done"},
            ],
            "initial_state": "open",
            "transitions": [],
            "fields_schema": [{"name": "bad", "type": "integer"}],
        }
        with pytest.raises(ValueError, match=r"[Ii]nvalid.*field type"):
            TemplateRegistry.parse_type_template(raw)


# -- Bug: filigree-ab91b3 / filigree-3e3f12 — Duplicate transitions silently accepted


class TestDuplicateTransitionDetection:
    """Bug fix: filigree-ab91b3, filigree-3e3f12 — duplicate (from, to) transitions."""

    def test_parse_duplicate_transitions_raises(self) -> None:
        """parse_type_template should reject duplicate (from_state, to_state) pairs."""
        raw = {
            "type": "dup_trans",
            "display_name": "Dup Trans",
            "states": [
                {"name": "open", "category": "open"},
                {"name": "closed", "category": "done"},
            ],
            "initial_state": "open",
            "transitions": [
                {"from": "open", "to": "closed", "enforcement": "soft"},
                {"from": "open", "to": "closed", "enforcement": "hard"},  # duplicate!
            ],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match=r"[Dd]uplicate.*transition"):
            TemplateRegistry.parse_type_template(raw)

    def test_validate_duplicate_transitions_reported(self) -> None:
        """validate_type_template should report duplicate transitions as errors."""
        from filigree.templates import StateDefinition, TransitionDefinition

        tpl = TypeTemplate(
            type="dup_trans",
            display_name="Dup Trans",
            description="",
            pack="test",
            states=(
                StateDefinition(name="open", category="open"),
                StateDefinition(name="closed", category="done"),
            ),
            initial_state="open",
            transitions=(
                TransitionDefinition(from_state="open", to_state="closed", enforcement="soft"),
                TransitionDefinition(from_state="open", to_state="closed", enforcement="hard"),
            ),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("duplicate" in e.lower() and "transition" in e.lower() for e in errors)

    def test_no_false_positive_on_unique_transitions(self) -> None:
        """Templates with unique transitions should validate cleanly."""
        from filigree.templates import StateDefinition, TransitionDefinition

        tpl = TypeTemplate(
            type="clean",
            display_name="Clean",
            description="",
            pack="test",
            states=(
                StateDefinition(name="open", category="open"),
                StateDefinition(name="working", category="wip"),
                StateDefinition(name="closed", category="done"),
            ),
            initial_state="open",
            transitions=(
                TransitionDefinition(from_state="open", to_state="working", enforcement="soft"),
                TransitionDefinition(from_state="working", to_state="closed", enforcement="soft"),
            ),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        dup_errors = [e for e in errors if "duplicate" in e.lower() and "transition" in e.lower()]
        assert dup_errors == []

    def test_builtin_packs_have_no_duplicate_transitions(self) -> None:
        """All built-in pack types must have unique transitions."""
        for pack_name, pack_data in BUILT_IN_PACKS.items():
            for type_name, type_data in pack_data.get("types", {}).items():
                tpl = TemplateRegistry.parse_type_template(type_data)
                errors = TemplateRegistry.validate_type_template(tpl)
                dup_errors = [e for e in errors if "duplicate" in e.lower() and "transition" in e.lower()]
                assert dup_errors == [], f"{pack_name}/{type_name} has duplicate transitions: {dup_errors}"


# -- Bug: filigree-9b9e45 — Enforcement "none" accepted but not in type ------


class TestEnforcementNoneRejected:
    """Bug fix: filigree-9b9e45 — 'none' enforcement violates type contract."""

    def test_parse_rejects_none_enforcement(self) -> None:
        """parse_type_template should reject enforcement='none'."""
        raw = {
            "type": "none_enf",
            "display_name": "None Enforcement",
            "states": [
                {"name": "open", "category": "open"},
                {"name": "closed", "category": "done"},
            ],
            "initial_state": "open",
            "transitions": [
                {"from": "open", "to": "closed", "enforcement": "none"},
            ],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match=r"[Ii]nvalid.*enforcement"):
            TemplateRegistry.parse_type_template(raw)

    def test_parse_still_accepts_hard_and_soft(self) -> None:
        """hard and soft enforcement must still be accepted."""
        for enf in ("hard", "soft"):
            raw = {
                "type": "valid_enf",
                "display_name": "Valid",
                "states": [
                    {"name": "open", "category": "open"},
                    {"name": "closed", "category": "done"},
                ],
                "initial_state": "open",
                "transitions": [
                    {"from": "open", "to": "closed", "enforcement": enf},
                ],
                "fields_schema": [],
            }
            tpl = TemplateRegistry.parse_type_template(raw)
            assert tpl.transitions[0].enforcement == enf

    def test_builtin_packs_only_use_hard_or_soft(self) -> None:
        """No built-in template should use enforcement='none'."""
        for pack_name, pack_data in BUILT_IN_PACKS.items():
            for type_name, type_data in pack_data.get("types", {}).items():
                for t in type_data.get("transitions", []):
                    assert t["enforcement"] in ("hard", "soft"), (
                        f"{pack_name}/{type_name}: transition {t['from']}->{t['to']} "
                        f"uses enforcement='{t['enforcement']}' (only 'hard'/'soft' allowed)"
                    )


# -- Bug: filigree-284665 — rolled_back state category mismatch -----


class TestRolledBackCategoryFix:
    """Bug fix: filigree-284665 — release.rolled_back must not be 'done'."""

    def test_rolled_back_is_not_done(self) -> None:
        """rolled_back has outgoing transition to development, so it cannot be 'done'."""
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        states = {s["name"]: s["category"] for s in raw["states"]}
        assert states["rolled_back"] != "done", "release.rolled_back should not be 'done' — it has a transition to 'development'"

    def test_rolled_back_is_wip(self) -> None:
        """rolled_back should be 'wip' since it can resume development."""
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        states = {s["name"]: s["category"] for s in raw["states"]}
        assert states["rolled_back"] == "wip"

    def test_rolled_back_to_development_transition_exists(self) -> None:
        """The rolled_back→development transition should still exist."""
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        tpl = TemplateRegistry.parse_type_template(raw)
        rollback_to_dev = [t for t in tpl.transitions if t.from_state == "rolled_back" and t.to_state == "development"]
        assert len(rollback_to_dev) == 1

    def test_release_still_has_two_done_states(self) -> None:
        """released and cancelled should remain done (only rolled_back changes)."""
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        states = {s["name"]: s["category"] for s in raw["states"]}
        assert states["released"] == "done"
        assert states["cancelled"] == "done"
