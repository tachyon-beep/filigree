"""WorkflowMixin — template and workflow operations.

Extracted from core.py as part of the module architecture split.
Covers template access, status/parent validation, state resolution,
label validation, transition queries, and issue validation.

All methods access ``self.conn``, ``self.templates``, etc. via
Python's MRO when composed into ``FiligreeDB``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from filigree.db_base import DBMixinProtocol, StatusCategory, _now_iso
from filigree.types.workflow import (
    FieldSchemaInfo,
    StateInfo,
    TemplateInfo,
    TemplateListItem,
    TransitionInfo,
)

if TYPE_CHECKING:
    from filigree.templates import TemplateRegistry, TransitionOption, ValidationResult


class WorkflowMixin(DBMixinProtocol):
    """Template and workflow operations for FiligreeDB.

    Inherits ``DBMixinProtocol`` for type-safe access to shared attributes
    (``self.conn``, ``self.db_path``, ``self.get_issue()``, etc.). Actual
    implementations provided by ``FiligreeDB`` at composition time via MRO.
    """

    _template_registry: TemplateRegistry | None  # narrowing needed by templates property

    @property
    def templates(self) -> TemplateRegistry:
        """Lazy-loaded TemplateRegistry — created on first access.

        Uses runtime import to avoid circular dependency.
        Can be overridden via constructor injection for testing.
        """
        if self._template_registry is None:
            from filigree.templates import TemplateRegistry

            self._template_registry = TemplateRegistry()
            filigree_dir = self.db_path.parent
            self._template_registry.load(filigree_dir, enabled_packs=self._enabled_packs_override)
        return self._template_registry

    # -- Templates -----------------------------------------------------------

    def _seed_templates(self) -> None:
        """Seed built-in packs and type templates into the database."""
        from filigree.core import _seed_builtin_packs

        now = _now_iso()
        _seed_builtin_packs(self.conn, now)

    def reload_templates(self) -> None:
        """Clear the cached template registry so it reloads on next access."""
        self._template_registry = None

    def get_template(self, issue_type: str) -> TemplateInfo | None:
        """Get a template by type name from the registry."""
        tpl = self.templates.get_type(issue_type)
        if tpl is None:
            return None
        fields_schema: list[FieldSchemaInfo] = []
        for f in tpl.fields_schema:
            field_info: FieldSchemaInfo = FieldSchemaInfo(name=f.name, type=f.type, description=f.description)
            if f.options:
                field_info["options"] = list(f.options)
            if f.default is not None:
                field_info["default"] = f.default
            if f.required_at:
                field_info["required_at"] = list(f.required_at)
            fields_schema.append(field_info)
        return TemplateInfo(
            type=tpl.type,
            display_name=tpl.display_name,
            description=tpl.description,
            states=[StateInfo(name=s.name, category=s.category) for s in tpl.states],
            initial_state=tpl.initial_state,
            transitions=[
                TransitionInfo(**{
                    "from": t.from_state, "to": t.to_state,
                    "enforcement": t.enforcement, "requires_fields": list(t.requires_fields),
                })
                for t in tpl.transitions
            ],
            fields_schema=fields_schema,
        )

    def list_templates(self) -> list[TemplateListItem]:
        """List all registered templates via the registry (respects enabled_packs)."""
        result: list[TemplateListItem] = []
        for tpl in self.templates.list_types():
            fields: list[FieldSchemaInfo] = []
            for f in tpl.fields_schema:
                fi: FieldSchemaInfo = FieldSchemaInfo(name=f.name, type=f.type, description=f.description)
                if f.options:
                    fi["options"] = list(f.options)
                if f.default is not None:
                    fi["default"] = f.default
                if f.required_at:
                    fi["required_at"] = list(f.required_at)
                fields.append(fi)
            result.append(
                TemplateListItem(
                    type=tpl.type,
                    display_name=tpl.display_name,
                    description=tpl.description,
                    fields_schema=fields,
                )
            )
        return sorted(result, key=lambda t: t["type"])

    def _validate_status(self, status: str, issue_type: str = "task") -> None:
        """Validate status against type-specific states from templates.

        Unknown types (no template) skip validation — permissive for custom types.
        """
        valid_states = self.templates.get_valid_states(issue_type)
        if valid_states is not None and status not in valid_states:
            msg = f"Invalid status '{status}' for type '{issue_type}'. Valid states: {', '.join(valid_states)}"
            raise ValueError(msg)

    def _validate_parent_id(self, parent_id: str | None) -> None:
        """Raise ValueError if parent_id does not reference an existing issue."""
        if parent_id is None:
            return
        exists = self.conn.execute("SELECT 1 FROM issues WHERE id = ?", (parent_id,)).fetchone()
        if exists is None:
            msg = f"parent_id '{parent_id}' does not reference an existing issue"
            raise ValueError(msg)

    def _get_states_for_category(self, category: str) -> list[str]:
        """Collect all state names that map to a category across enabled types.

        Returns deduplicated list. Empty if no types are registered.
        """
        states: list[str] = []
        for tpl in self.templates.list_types():
            for s in tpl.states:
                if s.category == category and s.name not in states:
                    states.append(s.name)
        return states

    @staticmethod
    def _infer_status_category(status: str) -> StatusCategory:
        """Infer status category from status name when no template is available."""
        done_names = {"closed", "done", "resolved", "wont_fix", "cancelled", "archived"}
        wip_names = {"in_progress", "fixing", "verifying", "reviewing", "testing", "active"}
        if status in done_names:
            return "done"
        if status in wip_names:
            return "wip"
        return "open"

    def _resolve_status_category(self, issue_type: str, status: str) -> StatusCategory:
        """Resolve status category via template or fallback heuristic for unknown types."""
        cat = self.templates.get_category(issue_type, status)
        if cat is not None:
            return cat
        return self._infer_status_category(status)

    def _reserved_label_names(self) -> set[str]:
        """Issue type names are reserved and cannot be used as free-form labels."""
        return {tpl.type.casefold() for tpl in self.templates.list_types()}

    def _validate_label_name(self, label: str) -> str:
        """Normalize and validate a label before writing it."""
        if not isinstance(label, str):
            msg = "Label must be a string"
            raise ValueError(msg)
        normalized = label.strip()
        if not normalized:
            msg = "Label cannot be empty"
            raise ValueError(msg)
        if normalized.casefold() in self._reserved_label_names():
            msg = f"Label '{normalized}' is reserved as an issue type name; set the issue type explicitly instead."
            raise ValueError(msg)
        return normalized

    # -- Template-aware queries ----------------------------------------------

    def get_valid_transitions(self, issue_id: str) -> list[TransitionOption]:
        """Return valid next states for an issue with readiness info.

        Delegates to TemplateRegistry.get_valid_transitions() with the issue's
        current state and fields. Returns an empty list for unknown types.
        """
        issue = self.get_issue(issue_id)
        return self.templates.get_valid_transitions(issue.type, issue.status, issue.fields)

    def validate_issue(self, issue_id: str) -> ValidationResult:
        """Validate an issue against its template.

        Checks whether all fields required at the current state are populated.
        Also checks fields needed for next reachable transitions (upcoming requirements).
        Returns a ValidationResult with warnings for missing recommended fields.
        Unknown types validate as valid (no template to check against).
        """
        from filigree.templates import ValidationResult

        issue = self.get_issue(issue_id)
        tpl = self.templates.get_type(issue.type)
        if tpl is None:
            return ValidationResult(valid=True, warnings=(), errors=())

        warnings: list[str] = []

        # Check required_at fields for current state
        missing = self.templates.validate_fields_for_state(issue.type, issue.status, issue.fields)
        for field_name in missing:
            warnings.append(f"Field '{field_name}' is recommended at state '{issue.status}' for type '{issue.type}' but is not populated.")

        # Check upcoming requirements: fields needed for next transitions
        transitions = self.templates.get_valid_transitions(issue.type, issue.status, issue.fields)
        for t in transitions:
            if t.missing_fields:
                fields_str = ", ".join(t.missing_fields)
                warnings.append(f"Transition to '{t.to}' requires: {fields_str}")

        return ValidationResult(valid=True, warnings=tuple(warnings), errors=())
