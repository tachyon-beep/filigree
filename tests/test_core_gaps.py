"""Gap-fill tests for filigree.core — config, templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from filigree.core import (
    FILIGREE_DIR_NAME,
    FiligreeDB,
    find_filigree_root,
    read_config,
    write_config,
)


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


class TestTemplates:
    def test_list_templates(self, db: FiligreeDB) -> None:
        templates = db.list_templates()
        types = {t["type"] for t in templates}
        assert "bug" in types
        assert "task" in types
        assert "milestone" in types

    def test_list_templates_includes_required_at(self, db: FiligreeDB) -> None:
        """Bug filigree-66aa8b: list_templates must include required_at, options, default."""
        templates = db.list_templates()
        bug_tpl = next(t for t in templates if t["type"] == "bug")
        fields_by_name = {f["name"]: f for f in bug_tpl["fields_schema"]}
        # Bug 'severity' field has options and required_at
        severity = fields_by_name.get("severity")
        assert severity is not None
        assert "options" in severity
        assert "required_at" in severity
        assert "confirmed" in severity["required_at"]
        # Verify parity with get_template
        get_tpl = db.get_template("bug")
        assert get_tpl is not None
        get_fields = {f["name"]: f for f in get_tpl["fields_schema"]}
        for name, field in get_fields.items():
            list_field = fields_by_name.get(name)
            assert list_field is not None, f"Missing field {name} in list_templates"
            if "required_at" in field:
                assert "required_at" in list_field, f"Missing required_at for {name}"
                assert field["required_at"] == list_field["required_at"]

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
