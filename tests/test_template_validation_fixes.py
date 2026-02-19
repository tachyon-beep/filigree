"""Tests for template validation bug fixes.

Covers:
- incident.resolved category fix (filigree-bf9926)
- StateDefinition.category validation (filigree-fe2078)
- Duplicate state name detection (filigree-eff214)
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from filigree.templates import StateDefinition, TemplateRegistry, TypeTemplate
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
            "incident.resolved should not be 'done' — it has an outgoing "
            "transition to 'closed' that requires root_cause"
        )
        assert states["resolved"] == "wip"

    def test_close_issue_from_resolved_works(self, incident_db: FiligreeDB) -> None:
        """An incident in 'resolved' state should be closeable via close_issue()."""
        issue = incident_db.create_issue("Outage", type="incident")

        # Walk the incident workflow: reported → triaging → investigating → resolved
        incident_db.update_issue(issue.id, status="triaging", fields={"severity": "sev2"})
        incident_db.update_issue(issue.id, status="investigating")
        incident_db.update_issue(issue.id, status="resolved")

        # Verify it's in resolved state
        resolved = incident_db.get_issue(issue.id)
        assert resolved.status == "resolved"

        # close_issue() should NOT raise "already closed"
        closed = incident_db.close_issue(issue.id, reason="Root cause: config drift")
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
        assert any("duplicate" in e.lower() or "open" in e for e in errors), (
            f"Expected duplicate state name error, got: {errors}"
        )

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
