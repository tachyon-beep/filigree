# Workflow Templates Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend Keel with data-driven workflow templates, per-type state machines, and agent-facing discovery tools. Packs ship in tiers: core + planning (9 types) first, then risk + spike, then remaining packs based on usage data.

**Architecture:** New `templates.py` module owns template loading, caching, validation, and logging. `templates_data.py` holds built-in pack definitions as Python data. `core.py` gains a lazy `templates` property and delegates status validation to the registry. MCP and CLI get 12 new tools (including `reload_templates`, `claim_next`, `include_transitions` on get_issue, batch workflow validation), 11 new commands.

**PR Strategy:** Phase 1 is a single atomic PR with `enabled_packs: []` as a feature flag. Phases 2-5 are separate PRs.

**Tech Stack:** Python 3.11+, SQLite, Click, MCP, frozen dataclasses (no Pydantic)

**Prerequisites:**
- `uv sync --group dev` for dev environment
- `make ci` passes clean on current main (commit 2b3ad2f)
- Read design doc: `docs/plans/2026-02-11-workflow-templates-design.md`
- Read requirements: `docs/plans/2026-02-11-workflow-templates-requirements.md`

**Reference documents:**
- Design: `docs/plans/2026-02-11-workflow-templates-design.md` (1,037 lines)
- Requirements: `docs/plans/2026-02-11-workflow-templates-requirements.md` (127 requirements)
- Architecture: `docs/arch-analysis-2026-02-11-0856/01-discovery-findings.md`

---

## Phase 1: Template Engine Foundation

Phase 1 is the largest and most critical phase. It creates the template infrastructure, migrates the schema, modifies core.py, and establishes all the caching. Every subsequent phase depends on this.

**Important notes:**
- **Line numbers are approximate.** They are snapshots of commit `2b3ad2f`. As earlier tasks modify files, later task line numbers will shift. Always search by function/symbol name, not line number alone.
- **Coverage target:** 90% for all new code. Run `uv run pytest --cov=keel --cov-report=term-missing` after each task.
- **PR strategy:** Merge all of Phase 1 as a single atomic PR. Do not merge partial Phase 1 work.

**Requirements covered:** WFT-FR-001 through WFT-FR-018, WFT-FR-038, WFT-FR-046 through WFT-FR-058, WFT-FR-066, WFT-NFR-001 through WFT-NFR-006, WFT-NFR-008, WFT-NFR-011 through WFT-NFR-017, WFT-AR-001 through WFT-AR-004, WFT-AR-007, WFT-AR-009 through WFT-AR-012, WFT-SR-001 through WFT-SR-004, WFT-SR-006, WFT-SR-012, WFT-SR-013, WFT-SR-015, WFT-DR-008, WFT-DR-011

---

### Task 1.1: Dataclass Definitions and Type Aliases

Create the frozen dataclasses and type aliases that every other module will import. This is the foundation — no logic, just data structures.

**Files:**
- Create: `src/keel/templates.py`
- Test: `tests/test_templates.py`

**Step 1: Write the failing test**

```python
# tests/test_templates.py
"""Tests for the workflow template system."""

from __future__ import annotations

import pytest

from keel.templates import (
    EnforcementLevel,
    FieldSchema,
    FieldType,
    StateCategory,
    StateDefinition,
    TransitionDefinition,
    TransitionOption,
    TransitionResult,
    TypeTemplate,
    ValidationResult,
    WorkflowPack,
)


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
            states=(StateDefinition(name="open", category="open"),
                    StateDefinition(name="closed", category="done")),
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
        to = TransitionOption(to="closed", category="done", enforcement="soft",
                              requires_fields=(), missing_fields=(), ready=True)
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
```

**Why this test:** Verifies all 9 dataclasses exist, are frozen, and have correct field defaults. Catches import errors and structural issues immediately.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_templates.py::TestDataclasses -v`

Expected: `ModuleNotFoundError: No module named 'keel.templates'`

**Step 3: Write minimal implementation**

```python
# src/keel/templates.py
"""Workflow template system — loading, caching, and validation.

Provides TemplateRegistry for managing per-type state machines, transition
enforcement, and field validation. Type templates define states, transitions,
and field schemas. Workflow packs bundle related types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Type aliases (WFT-NFR-015)
# ---------------------------------------------------------------------------

StateCategory = Literal["open", "wip", "done"]
EnforcementLevel = Literal["hard", "soft"]
FieldType = Literal["text", "enum", "number", "date", "list", "boolean"]

# ---------------------------------------------------------------------------
# Frozen dataclasses (WFT-NFR-014)
# ---------------------------------------------------------------------------
# Templates use frozen=True for immutability (configuration data).
# This differs intentionally from the mutable Issue dataclass (domain entity).
# Frozen dataclasses prevent accidental mutation and enable dict caching.
# ---------------------------------------------------------------------------

import logging
import re

logger = logging.getLogger(__name__)

# State/type names must match this pattern to be safe for use in SQL queries
# and filesystem paths. Validated at parse time (review B1, B5).
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class StateDefinition:
    """A named state within a type's workflow, mapped to a universal category."""
    name: str
    category: StateCategory

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            msg = f"Invalid state name '{self.name}': must match ^[a-z][a-z0-9_]{{0,63}}$"
            raise ValueError(msg)


@dataclass(frozen=True)
class TransitionDefinition:
    """A valid state transition with enforcement level and field requirements."""
    from_state: str
    to_state: str
    enforcement: EnforcementLevel
    requires_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldSchema:
    """Schema for a custom field on an issue type."""
    name: str
    type: FieldType
    description: str = ""
    options: tuple[str, ...] = ()
    default: Any = None
    required_at: tuple[str, ...] = ()


@dataclass(frozen=True)
class TypeTemplate:
    """Complete workflow definition for an issue type."""
    type: str
    display_name: str
    description: str
    pack: str
    states: tuple[StateDefinition, ...]
    initial_state: str
    transitions: tuple[TransitionDefinition, ...]
    fields_schema: tuple[FieldSchema, ...]
    suggested_children: tuple[str, ...] = ()
    suggested_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowPack:
    """A bundle of related type templates with relationships and guidance."""
    pack: str
    version: str
    display_name: str
    description: str
    types: dict[str, TypeTemplate]
    requires_packs: tuple[str, ...]
    relationships: tuple[dict[str, Any], ...]
    cross_pack_relationships: tuple[dict[str, Any], ...]
    guide: dict[str, Any] | None


@dataclass(frozen=True)
class TransitionResult:
    """Result of validating a specific state transition."""
    allowed: bool
    enforcement: EnforcementLevel | None
    missing_fields: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class TransitionOption:
    """A possible next state from the current state."""
    to: str
    category: StateCategory
    enforcement: EnforcementLevel | None
    requires_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    ready: bool


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating an issue against its template."""
    valid: bool
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
```

**Why these choices:**
- Frozen dataclasses are intentional for template config — unlike the mutable `Issue` dataclass in `core.py:386`, templates are configuration data that must not be mutated after loading
- Tuples instead of lists for immutability in frozen dataclasses
- `StateDefinition.__post_init__` validates name format against `^[a-z][a-z0-9_]{0,63}$` — this prevents SQL injection via state names in category-aware queries (review B1)
- `WorkflowPack.types` is a dict (not frozen) — this is intentional, as packs need mutable type mappings during loading but the pack itself shouldn't be reassigned. If strict immutability is needed, we can use `MappingProxyType` later.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_templates.py::TestDataclasses -v`

Expected: All 8 tests pass.

**Step 5: Run full CI**

Run: `make ci`

Expected: All existing tests still pass. Ruff and mypy clean.

**Step 6: Commit**

```bash
git add src/keel/templates.py tests/test_templates.py
git commit -m "feat(templates): add frozen dataclasses, type aliases, and name validation

- StateDefinition, TransitionDefinition, FieldSchema, TypeTemplate
- WorkflowPack, TransitionResult, TransitionOption, ValidationResult
- Type aliases: StateCategory, EnforcementLevel, FieldType
- StateDefinition.__post_init__ validates name against ^[a-z][a-z0-9_]{0,63}$
- Frozen dataclasses for immutable config (intentionally different from mutable Issue)
- Adds logging infrastructure (logger = logging.getLogger(__name__))

Implements: WFT-NFR-014, WFT-NFR-015, WFT-FR-013, WFT-FR-014, WFT-FR-015

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

**Definition of Done:**
- [ ] All 9 dataclasses importable from `keel.templates`
- [ ] All dataclasses are frozen (assignment raises `AttributeError`)
- [ ] Type aliases defined for StateCategory, EnforcementLevel, FieldType
- [ ] StateDefinition rejects invalid names (uppercase, special chars, >64 chars)
- [ ] `import logging` and `logger` present in `templates.py`
- [ ] `make ci` passes clean

---

### Task 1.2: Custom Exception Types

Add the enforcement-specific exceptions that `validate_transition()` will raise.

**Files:**
- Modify: `src/keel/templates.py`
- Test: `tests/test_templates.py`

**Step 1: Write the failing test**

```python
# Add to tests/test_templates.py

from keel.templates import HardEnforcementError, TransitionNotAllowedError


class TestExceptions:
    def test_transition_not_allowed_is_value_error(self) -> None:
        err = TransitionNotAllowedError("triage", "closed", "bug")
        assert isinstance(err, ValueError)
        assert "triage" in str(err)
        assert "closed" in str(err)
        assert "bug" in str(err)

    def test_hard_enforcement_is_value_error(self) -> None:
        err = HardEnforcementError("fixing", "verifying", "bug", ["fix_verification"])
        assert isinstance(err, ValueError)
        assert "fix_verification" in str(err)
        assert err.missing_fields == ["fix_verification"]
        assert err.from_state == "fixing"
        assert err.to_state == "verifying"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_templates.py::TestExceptions -v`

Expected: `ImportError`

**Step 3: Write minimal implementation**

Add to `src/keel/templates.py` after the dataclass definitions:

```python
# ---------------------------------------------------------------------------
# Custom exceptions (WFT-AR-010)
# ---------------------------------------------------------------------------


class TransitionNotAllowedError(ValueError):
    """Raised when a transition is not defined in the type's transition table."""

    def __init__(self, from_state: str, to_state: str, type_name: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.type_name = type_name
        super().__init__(
            f"Transition '{from_state}' -> '{to_state}' is not defined for type '{type_name}'. "
            f"Use get_valid_transitions() to see available transitions."
        )


class HardEnforcementError(ValueError):
    """Raised when a hard-enforced transition fails field validation."""

    def __init__(self, from_state: str, to_state: str, type_name: str, missing_fields: list[str]) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.type_name = type_name
        self.missing_fields = missing_fields
        fields_str = ", ".join(missing_fields)
        super().__init__(
            f"Cannot transition '{from_state}' -> '{to_state}' for type '{type_name}': "
            f"missing required fields: {fields_str}. "
            f"Populate these fields before transitioning, or call get_type_info('{type_name}') for field details."
        )
```

**Step 4: Run test and CI**

Run: `uv run pytest tests/test_templates.py::TestExceptions -v && make ci`

**Step 5: Commit**

```bash
git add src/keel/templates.py tests/test_templates.py
git commit -m "feat(templates): add TransitionNotAllowedError and HardEnforcementError

- Both subclass ValueError for backward compatibility
- Include remediation guidance in error messages (WFT-SR-006)
- Store from_state, to_state, type_name, missing_fields as attributes

Implements: WFT-AR-010, WFT-DR-008, WFT-SR-006

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

**Definition of Done:**
- [ ] Both exceptions importable from `keel.templates`
- [ ] Both are `ValueError` subclasses
- [ ] Error messages include remediation hints
- [ ] `make ci` passes clean

---

### Task 1.3: TemplateRegistry — Template Parsing and Caching

Implement the core `TemplateRegistry` class with: `_parse_type_template()`, `_parse_pack()`, `get_type()`, `get_pack()`, `list_types()`, `list_packs()`, `get_initial_state()`, `get_category()`. No loading from DB/filesystem yet — that comes in Task 1.4.

**Files:**
- Modify: `src/keel/templates.py`
- Test: `tests/test_templates.py`

**Step 1: Write the failing tests**

```python
# Add to tests/test_templates.py

from keel.templates import TemplateRegistry


class TestTemplateRegistry:
    """Test TemplateRegistry with manually registered templates."""

    @pytest.fixture()
    def registry(self) -> TemplateRegistry:
        """A registry pre-loaded with a minimal core pack."""
        reg = TemplateRegistry()
        # Manually register for testing (before load() is implemented)
        task_tpl = TypeTemplate(
            type="task", display_name="Task", description="General task", pack="core",
            states=(StateDefinition("open", "open"), StateDefinition("in_progress", "wip"),
                    StateDefinition("closed", "done")),
            initial_state="open",
            transitions=(
                TransitionDefinition("open", "in_progress", "soft"),
                TransitionDefinition("in_progress", "closed", "soft"),
            ),
            fields_schema=(),
        )
        bug_tpl = TypeTemplate(
            type="bug", display_name="Bug", description="Bug report", pack="core",
            states=(
                StateDefinition("triage", "open"), StateDefinition("confirmed", "open"),
                StateDefinition("fixing", "wip"), StateDefinition("verifying", "wip"),
                StateDefinition("closed", "done"), StateDefinition("wont_fix", "done"),
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
                FieldSchema("severity", "enum", options=("critical", "major", "minor", "cosmetic"),
                            required_at=("confirmed",)),
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
        """Category cache should be a dict lookup, not iteration."""
        # This tests the implementation detail that _category_cache exists
        assert hasattr(registry, '_category_cache')
        assert ("bug", "triage") in registry._category_cache

    def test_get_category_unknown_state(self, registry: TemplateRegistry) -> None:
        """Unknown state for known type returns None."""
        assert registry.get_category("bug", "nonexistent") is None

    def test_get_category_unknown_type(self, registry: TemplateRegistry) -> None:
        """Unknown type returns None."""
        assert registry.get_category("unknown", "open") is None

    def test_get_valid_states(self, registry: TemplateRegistry) -> None:
        states = registry.get_valid_states("bug")
        assert "triage" in states
        assert "closed" in states
        assert len(states) == 6

    def test_get_valid_states_unknown_type(self, registry: TemplateRegistry) -> None:
        assert registry.get_valid_states("unknown") is None

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
                {"from": "investigating", "to": "concluded", "enforcement": "hard",
                 "requires_fields": ["findings"]},
            ],
            "fields_schema": [
                {"name": "findings", "type": "text", "description": "What was discovered",
                 "required_at": ["concluded"]},
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_templates.py::TestTemplateRegistry -v`

Expected: `AttributeError: type object 'TemplateRegistry' has no attribute 'parse_type_template'`

**Step 3: Write implementation**

Add to `src/keel/templates.py`:

```python
# ---------------------------------------------------------------------------
# TemplateRegistry (WFT-FR-001)
# ---------------------------------------------------------------------------


class TemplateRegistry:
    """Loads, caches, and queries workflow templates and packs.

    Templates are loaded once per instance and cached for the entire lifetime (WFT-NFR-005).
    The registry builds O(1) lookup caches for category mapping (WFT-SR-002) and
    transition validation (WFT-SR-003).
    """

    def __init__(self) -> None:
        self._types: dict[str, TypeTemplate] = {}
        self._packs: dict[str, WorkflowPack] = {}
        self._category_cache: dict[tuple[str, str], StateCategory] = {}
        self._transition_cache: dict[str, dict[tuple[str, str], TransitionDefinition]] = {}
        self._loaded = False

    # -- Parsing (from dict/JSON) -------------------------------------------

    # -- Size limits for custom templates (review B5) -------------------------
    MAX_STATES = 50
    MAX_TRANSITIONS = 200
    MAX_FIELDS = 50

    @staticmethod
    def parse_type_template(raw: dict[str, Any]) -> TypeTemplate:
        """Parse a type template from a JSON-compatible dict.

        Args:
            raw: Dictionary with type template fields as defined in design Section 3.2.

        Returns:
            A frozen TypeTemplate instance.

        Raises:
            ValueError: If required fields are missing, invalid, or exceed size limits.
            KeyError: If required keys are missing from the dict.
        """
        # Validate type name format (review B1, B5)
        type_name = raw["type"]
        if not _NAME_PATTERN.match(type_name):
            msg = f"Invalid type name '{type_name}': must match ^[a-z][a-z0-9_]{{0,63}}$"
            raise ValueError(msg)

        # Size limit checks (review B5 — prevent DoS via huge templates)
        if len(raw.get("states", [])) > TemplateRegistry.MAX_STATES:
            msg = f"Type '{type_name}' has {len(raw['states'])} states (max {TemplateRegistry.MAX_STATES})"
            raise ValueError(msg)
        if len(raw.get("transitions", [])) > TemplateRegistry.MAX_TRANSITIONS:
            msg = f"Type '{type_name}' has {len(raw['transitions'])} transitions (max {TemplateRegistry.MAX_TRANSITIONS})"
            raise ValueError(msg)
        if len(raw.get("fields_schema", [])) > TemplateRegistry.MAX_FIELDS:
            msg = f"Type '{type_name}' has {len(raw['fields_schema'])} fields (max {TemplateRegistry.MAX_FIELDS})"
            raise ValueError(msg)

        logger.debug("Parsing template for type: %s", type_name)

        # StateDefinition.__post_init__ validates each state name format
        states = tuple(
            StateDefinition(name=s["name"], category=s["category"])
            for s in raw["states"]
        )
        transitions = tuple(
            TransitionDefinition(
                from_state=t["from"],
                to_state=t["to"],
                enforcement=t["enforcement"],
                requires_fields=tuple(t.get("requires_fields", [])),
            )
            for t in raw.get("transitions", [])
        )
        fields_schema = tuple(
            FieldSchema(
                name=f["name"],
                type=f["type"],
                description=f.get("description", ""),
                options=tuple(f.get("options", [])),
                default=f.get("default"),
                required_at=tuple(f.get("required_at", [])),
            )
            for f in raw.get("fields_schema", [])
        )
        return TypeTemplate(
            type=type_name,
            display_name=raw["display_name"],
            description=raw.get("description", ""),
            pack=raw.get("pack", "custom"),
            states=states,
            initial_state=raw["initial_state"],
            transitions=transitions,
            fields_schema=fields_schema,
            suggested_children=tuple(raw.get("suggested_children", [])),
            suggested_labels=tuple(raw.get("suggested_labels", [])),
        )

    @staticmethod
    def validate_type_template(tpl: TypeTemplate) -> list[str]:
        """Validate a TypeTemplate for internal consistency (WFT-NFR-008).

        Returns:
            List of error messages. Empty list means valid.
        """
        errors: list[str] = []
        state_names = {s.name for s in tpl.states}

        if tpl.initial_state not in state_names:
            errors.append(f"initial_state '{tpl.initial_state}' is not in states list")

        for t in tpl.transitions:
            if t.from_state not in state_names:
                errors.append(f"transition from_state '{t.from_state}' is not in states list")
            if t.to_state not in state_names:
                errors.append(f"transition to_state '{t.to_state}' is not in states list")

        field_names = {f.name for f in tpl.fields_schema}
        for t in tpl.transitions:
            for rf in t.requires_fields:
                if rf not in field_names:
                    errors.append(f"transition {t.from_state}->{t.to_state} requires_fields '{rf}' not in fields_schema")

        for f in tpl.fields_schema:
            for ra in f.required_at:
                if ra not in state_names:
                    errors.append(f"field '{f.name}' required_at '{ra}' is not in states list")

        return errors

    # -- Registration (internal) --------------------------------------------

    def _register_type(self, tpl: TypeTemplate) -> None:
        """Register a type template and build caches."""
        logger.debug("Registering type: %s (pack=%s, %d states)", tpl.type, tpl.pack, len(tpl.states))
        self._types[tpl.type] = tpl

        # Build category cache (WFT-SR-002)
        for state in tpl.states:
            self._category_cache[(tpl.type, state.name)] = state.category

        # Build transition cache (WFT-SR-003)
        self._transition_cache[tpl.type] = {
            (t.from_state, t.to_state): t for t in tpl.transitions
        }

    def _register_pack(self, pack: WorkflowPack) -> None:
        """Register a workflow pack."""
        self._packs[pack.pack] = pack

    # -- Queries ------------------------------------------------------------

    def get_type(self, type_name: str) -> TypeTemplate | None:
        """Get a type template by name."""
        return self._types.get(type_name)

    def get_pack(self, pack_name: str) -> WorkflowPack | None:
        """Get a pack by name."""
        return self._packs.get(pack_name)

    def list_types(self) -> list[TypeTemplate]:
        """All types from enabled packs."""
        return list(self._types.values())

    def list_packs(self) -> list[WorkflowPack]:
        """All enabled packs."""
        return list(self._packs.values())

    def get_initial_state(self, type_name: str) -> str:
        """Initial state for a type. Falls back to 'open' if no template (WFT-FR-007)."""
        tpl = self._types.get(type_name)
        if tpl is None:
            logger.warning("Unknown type '%s' — falling back to initial state 'open'", type_name)
            return "open"
        return tpl.initial_state

    def get_category(self, type_name: str, state: str) -> StateCategory | None:
        """Map a (type, state) pair to its category via O(1) cache (WFT-SR-002)."""
        return self._category_cache.get((type_name, state))

    def get_valid_states(self, type_name: str) -> list[str] | None:
        """Return list of valid state names for a type, or None if type unknown."""
        tpl = self._types.get(type_name)
        if tpl is None:
            return None
        return [s.name for s in tpl.states]

    def get_first_state_of_category(self, type_name: str, category: StateCategory) -> str | None:
        """Return the first state of a given category (array order) (WFT-FR-010)."""
        tpl = self._types.get(type_name)
        if tpl is None:
            return None
        for state in tpl.states:
            if state.category == category:
                return state.name
        return None
```

**Step 4: Run test and CI**

Run: `uv run pytest tests/test_templates.py::TestTemplateRegistry -v && make ci`

**Step 5: Commit**

```bash
git add src/keel/templates.py tests/test_templates.py
git commit -m "feat(templates): TemplateRegistry with parsing, caching, validation, and logging

- parse_type_template() converts JSON dicts to frozen TypeTemplate
- parse_type_template() validates type name format (^[a-z][a-z0-9_]{0,63}$)
- parse_type_template() enforces size limits (50 states, 200 transitions, 50 fields)
- validate_type_template() checks internal consistency
- O(1) category cache via _category_cache dict
- O(1) transition lookup via _transition_cache dict
- get_initial_state() with 'open' fallback for unknown types (logs warning)
- get_first_state_of_category() uses array order per WFT-FR-010
- Logging: DEBUG for parsing/registration, WARNING for fallbacks

Implements: WFT-FR-001, WFT-FR-007, WFT-FR-010, WFT-NFR-002, WFT-NFR-003,
WFT-NFR-008, WFT-SR-001, WFT-SR-002, WFT-SR-003

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

**Definition of Done:**
- [ ] TemplateRegistry class exists with all query methods
- [ ] parse_type_template() converts raw dicts to TypeTemplate
- [ ] parse_type_template() rejects invalid type names and oversized templates
- [ ] validate_type_template() checks consistency
- [ ] O(1) category and transition caches built on registration
- [ ] Logging at DEBUG (parse/register) and WARNING (fallback) levels
- [ ] get_initial_state() falls back to "open"
- [ ] `make ci` passes clean

---

### Task 1.4: TemplateRegistry — Transition Validation

Implement `validate_transition()`, `get_valid_transitions()`, and `validate_fields_for_state()`. This is where soft/hard enforcement logic lives.

**Files:**
- Modify: `src/keel/templates.py`
- Test: `tests/test_templates.py`

**Step 1: Write the failing tests**

```python
# Add to tests/test_templates.py

class TestTransitionValidation:
    """Test transition validation with soft/hard enforcement."""

    @pytest.fixture()
    def registry(self) -> TemplateRegistry:
        """Registry with bug type for transition testing."""
        reg = TemplateRegistry()
        bug_tpl = TypeTemplate(
            type="bug", display_name="Bug", description="Bug report", pack="core",
            states=(
                StateDefinition("triage", "open"), StateDefinition("confirmed", "open"),
                StateDefinition("fixing", "wip"), StateDefinition("verifying", "wip"),
                StateDefinition("closed", "done"), StateDefinition("wont_fix", "done"),
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
                FieldSchema("severity", "enum", options=("critical", "major"),
                            required_at=("confirmed",)),
                FieldSchema("fix_verification", "text", required_at=("verifying",)),
            ),
        )
        reg._register_type(bug_tpl)
        return reg

    def test_valid_soft_transition(self, registry: TemplateRegistry) -> None:
        result = registry.validate_transition("bug", "triage", "confirmed", {})
        assert result.allowed is True
        assert result.enforcement == "soft"

    def test_valid_soft_transition_with_missing_soft_fields(self, registry: TemplateRegistry) -> None:
        """Soft transition with missing fields: allowed with warnings."""
        result = registry.validate_transition("bug", "fixing", "verifying", {})
        assert result.allowed is True
        assert result.enforcement == "soft"
        assert "fix_verification" in result.missing_fields

    def test_hard_transition_with_missing_fields(self, registry: TemplateRegistry) -> None:
        """Hard transition with missing fields: NOT allowed."""
        result = registry.validate_transition("bug", "verifying", "closed", {})
        assert result.allowed is False
        assert result.enforcement == "hard"
        assert "fix_verification" in result.missing_fields

    def test_hard_transition_with_populated_fields(self, registry: TemplateRegistry) -> None:
        """Hard transition with all required fields: allowed."""
        result = registry.validate_transition(
            "bug", "verifying", "closed", {"fix_verification": "Tests pass"}
        )
        assert result.allowed is True
        assert result.enforcement == "hard"
        assert len(result.missing_fields) == 0

    def test_undefined_transition_soft_warning(self, registry: TemplateRegistry) -> None:
        """Transition not in table: allowed with warning (WFT-FR-011)."""
        result = registry.validate_transition("bug", "triage", "closed", {})
        assert result.allowed is True
        assert result.enforcement is None
        assert len(result.warnings) > 0

    def test_empty_string_treated_as_missing(self, registry: TemplateRegistry) -> None:
        """Empty string should be treated as unpopulated (WFT-FR-012)."""
        result = registry.validate_transition(
            "bug", "verifying", "closed", {"fix_verification": ""}
        )
        assert result.allowed is False
        assert "fix_verification" in result.missing_fields

    def test_none_treated_as_missing(self, registry: TemplateRegistry) -> None:
        result = registry.validate_transition(
            "bug", "verifying", "closed", {"fix_verification": None}
        )
        assert result.allowed is False

    def test_unknown_type_always_allowed(self, registry: TemplateRegistry) -> None:
        """Unknown types get fallback: all transitions allowed (WFT-FR-016)."""
        result = registry.validate_transition("unknown", "open", "closed", {})
        assert result.allowed is True
        assert result.enforcement is None

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
        options = registry.get_valid_transitions(
            "bug", "fixing", {"fix_verification": "Tests pass"}
        )
        verifying = next(o for o in options if o.to == "verifying")
        assert verifying.ready is True

    def test_validate_fields_for_state(self, registry: TemplateRegistry) -> None:
        missing = registry.validate_fields_for_state("bug", "confirmed", {})
        assert "severity" in missing

    def test_validate_fields_for_state_populated(self, registry: TemplateRegistry) -> None:
        missing = registry.validate_fields_for_state("bug", "confirmed", {"severity": "major"})
        assert missing == []
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_templates.py::TestTransitionValidation -v`

Expected: `AttributeError: 'TemplateRegistry' object has no attribute 'validate_transition'`

**Step 3: Write implementation**

Add these methods to the `TemplateRegistry` class in `src/keel/templates.py`:

```python
    # -- Validation ---------------------------------------------------------

    @staticmethod
    def _is_field_populated(value: Any) -> bool:
        """Check if a field value is considered populated (WFT-FR-012)."""
        if value is None:
            return False
        if isinstance(value, str) and value.strip() == "":
            return False
        return True

    def validate_transition(
        self,
        type_name: str,
        from_state: str,
        to_state: str,
        fields: dict[str, Any],
    ) -> TransitionResult:
        """Validate a state transition (WFT-FR-011).

        Args:
            type_name: The issue type.
            from_state: Current state.
            to_state: Target state.
            fields: Current issue fields dict.

        Returns:
            TransitionResult indicating whether the transition is allowed,
            its enforcement level, any missing required fields, and warnings.
        """
        tpl = self._types.get(type_name)
        if tpl is None:
            # Fallback: unknown types allow all transitions (WFT-FR-016)
            return TransitionResult(allowed=True, enforcement=None, missing_fields=(), warnings=())

        transition_map = self._transition_cache.get(type_name, {})
        transition = transition_map.get((from_state, to_state))

        if transition is None:
            # Transition not in table: soft-warn (WFT-FR-011)
            return TransitionResult(
                allowed=True,
                enforcement=None,
                missing_fields=(),
                warnings=(
                    f"Transition '{from_state}' -> '{to_state}' is not in the standard workflow for '{type_name}'. "
                    f"Use get_valid_transitions() to see recommended transitions.",
                ),
            )

        # Check required fields for this transition
        missing = tuple(
            f for f in transition.requires_fields
            if not self._is_field_populated(fields.get(f))
        )

        # Also check fields required_at the target state
        state_required = self.validate_fields_for_state(type_name, to_state, fields)
        all_missing = tuple(dict.fromkeys(list(missing) + state_required))  # dedupe, preserve order

        warnings: list[str] = []

        if transition.enforcement == "hard" and all_missing:
            return TransitionResult(
                allowed=False,
                enforcement="hard",
                missing_fields=all_missing,
                warnings=(),
            )

        if transition.enforcement == "soft" and all_missing:
            warnings.append(
                f"Missing recommended fields for '{to_state}': {', '.join(all_missing)}"
            )

        return TransitionResult(
            allowed=True,
            enforcement=transition.enforcement,
            missing_fields=all_missing,
            warnings=tuple(warnings),
        )

    def get_valid_transitions(
        self,
        type_name: str,
        current_state: str,
        fields: dict[str, Any],
    ) -> list[TransitionOption]:
        """All valid transitions from current state with readiness info (WFT-FR-014).

        Args:
            type_name: The issue type.
            current_state: Current state.
            fields: Current issue fields dict.

        Returns:
            List of TransitionOption with readiness and field requirement info.
        """
        tpl = self._types.get(type_name)
        if tpl is None:
            return []

        options: list[TransitionOption] = []
        for t in tpl.transitions:
            if t.from_state != current_state:
                continue

            # Check missing fields for this transition
            missing_trans = [
                f for f in t.requires_fields
                if not self._is_field_populated(fields.get(f))
            ]
            missing_state = self.validate_fields_for_state(type_name, t.to_state, fields)
            all_missing = list(dict.fromkeys(missing_trans + missing_state))

            target_category = self._category_cache.get((type_name, t.to_state), "open")
            ready = len(all_missing) == 0 or t.enforcement != "hard"

            options.append(TransitionOption(
                to=t.to_state,
                category=target_category,
                enforcement=t.enforcement,
                requires_fields=t.requires_fields,
                missing_fields=tuple(all_missing),
                ready=ready,
            ))

        return options

    def validate_fields_for_state(
        self,
        type_name: str,
        state: str,
        fields: dict[str, Any],
    ) -> list[str]:
        """Return fields required at this state but not yet populated (WFT-FR-012).

        Args:
            type_name: The issue type.
            state: The state to check requirements for.
            fields: Current issue fields dict.

        Returns:
            List of field names that are required but missing.
        """
        tpl = self._types.get(type_name)
        if tpl is None:
            return []

        missing: list[str] = []
        for f in tpl.fields_schema:
            if state in f.required_at and not self._is_field_populated(fields.get(f.name)):
                missing.append(f.name)
        return missing
```

**Step 4: Run test and CI**

Run: `uv run pytest tests/test_templates.py::TestTransitionValidation -v && make ci`

**Step 5: Commit**

```bash
git add src/keel/templates.py tests/test_templates.py
git commit -m "feat(templates): transition validation with soft/hard enforcement

- validate_transition() checks enforcement level and required fields
- Hard enforcement rejects on missing fields; soft warns
- Undefined transitions treated as soft-warn by default
- Empty strings and None treated as unpopulated (WFT-FR-012)
- get_valid_transitions() returns readiness per option
- validate_fields_for_state() checks required_at declarations

Implements: WFT-FR-011, WFT-FR-012, WFT-FR-016, WFT-NFR-009

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

**Definition of Done:**
- [ ] validate_transition() handles soft, hard, and undefined transitions
- [ ] get_valid_transitions() returns per-option readiness
- [ ] validate_fields_for_state() checks required_at
- [ ] Empty strings treated as unpopulated
- [ ] Unknown types get fallback behavior
- [ ] `make ci` passes clean

---

### Task 1.5: Built-in Pack Data — Core Pack (Minimal)

Create `templates_data.py` with the `core` pack only (task, bug, feature, epic). This provides enough data for the engine integration in Tasks 1.6-1.9. Full pack data (all 9 packs) is in Phase 2.

**Files:**
- Create: `src/keel/templates_data.py`
- Test: `tests/test_templates.py` (add parametrized type validation tests)

**Step 1: Write the failing test**

```python
# Add to tests/test_templates.py
from keel.templates_data import BUILT_IN_PACKS


class TestBuiltInPackData:
    """Verify built-in pack definitions are structurally valid."""

    def test_core_pack_exists(self) -> None:
        assert "core" in BUILT_IN_PACKS

    def test_core_pack_has_four_types(self) -> None:
        core = BUILT_IN_PACKS["core"]
        assert set(core["types"].keys()) == {"task", "bug", "feature", "epic"}

    @pytest.mark.parametrize("type_name", ["task", "bug", "feature", "epic"])
    def test_core_types_parse_and_validate(self, type_name: str) -> None:
        raw = BUILT_IN_PACKS["core"]["types"][type_name]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors for {type_name}: {errors}"

    def test_core_task_uses_standard_states(self) -> None:
        """Task type must use open/in_progress/closed for backward compat."""
        raw = BUILT_IN_PACKS["core"]["types"]["task"]
        tpl = TemplateRegistry.parse_type_template(raw)
        state_names = [s.name for s in tpl.states]
        assert state_names == ["open", "in_progress", "closed"]

    def test_core_bug_has_hard_enforcement(self) -> None:
        raw = BUILT_IN_PACKS["core"]["types"]["bug"]
        tpl = TemplateRegistry.parse_type_template(raw)
        hard_transitions = [t for t in tpl.transitions if t.enforcement == "hard"]
        assert len(hard_transitions) >= 1  # verifying->closed at minimum

    def test_planning_pack_exists(self) -> None:
        assert "planning" in BUILT_IN_PACKS
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_templates.py::TestBuiltInPackData -v`

Expected: `ModuleNotFoundError: No module named 'keel.templates_data'`

**Step 3: Write implementation**

Create `src/keel/templates_data.py` with the core and planning packs. This is a data-only file — just dictionaries. The full content should match the state machines defined in design Section 10.2. Below is the structure; the actual content for all fields/states/transitions should be transcribed from the design doc.

```python
# src/keel/templates_data.py
"""Built-in workflow pack definitions.

This module contains the data definitions for all built-in packs (WFT-NFR-013).
Logic lives in templates.py; this file is pure data.

Each pack is a JSON-compatible dict matching the pack schema (design Section 4.2).
Types within packs match the type template schema (design Section 3.2).
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Core Pack — Foundational software development types
# ---------------------------------------------------------------------------

_CORE_PACK: dict[str, Any] = {
    "pack": "core",
    "version": "1.0",
    "display_name": "Core",
    "description": "Foundational software development types: tasks, bugs, features, and epics",
    "requires_packs": [],
    "types": {
        "task": {
            "type": "task",
            "display_name": "Task",
            "description": "General-purpose work item",
            "pack": "core",
            "states": [
                {"name": "open", "category": "open"},
                {"name": "in_progress", "category": "wip"},
                {"name": "closed", "category": "done"},
            ],
            "initial_state": "open",
            "transitions": [
                {"from": "open", "to": "in_progress", "enforcement": "soft"},
                {"from": "in_progress", "to": "closed", "enforcement": "soft"},
                {"from": "open", "to": "closed", "enforcement": "soft"},
            ],
            "fields_schema": [
                {"name": "context", "type": "text", "description": "Background context"},
                {"name": "done_definition", "type": "text", "description": "How to know this is complete"},
                {"name": "estimated_minutes", "type": "number", "description": "Rough time estimate"},
            ],
            "suggested_children": ["step"],
            "suggested_labels": ["chore", "cleanup", "setup"],
        },
        "bug": {
            # Full bug template as defined in design Section 3.1
            "type": "bug",
            "display_name": "Bug Report",
            "description": "Defects, regressions, and unexpected behavior",
            "pack": "core",
            "states": [
                {"name": "triage", "category": "open"},
                {"name": "confirmed", "category": "open"},
                {"name": "fixing", "category": "wip"},
                {"name": "verifying", "category": "wip"},
                {"name": "closed", "category": "done"},
                {"name": "wont_fix", "category": "done"},
            ],
            "initial_state": "triage",
            "transitions": [
                {"from": "triage", "to": "confirmed", "enforcement": "soft"},
                {"from": "triage", "to": "wont_fix", "enforcement": "soft"},
                {"from": "confirmed", "to": "fixing", "enforcement": "soft"},
                {"from": "fixing", "to": "verifying", "enforcement": "soft", "requires_fields": ["fix_verification"]},
                {"from": "verifying", "to": "closed", "enforcement": "hard", "requires_fields": ["fix_verification"]},
                {"from": "verifying", "to": "fixing", "enforcement": "soft"},
            ],
            "fields_schema": [
                {"name": "severity", "type": "enum", "options": ["critical", "major", "minor", "cosmetic"], "default": "major", "description": "Impact severity", "required_at": ["confirmed"]},
                {"name": "component", "type": "text", "description": "Affected subsystem"},
                {"name": "steps_to_reproduce", "type": "text", "description": "Numbered steps to trigger the bug"},
                {"name": "root_cause", "type": "text", "description": "Identified root cause", "required_at": ["fixing"]},
                {"name": "fix_verification", "type": "text", "description": "How to verify the fix works", "required_at": ["verifying"]},
                {"name": "expected_behavior", "type": "text", "description": "What should happen"},
                {"name": "actual_behavior", "type": "text", "description": "What actually happens"},
                {"name": "environment", "type": "text", "description": "Python version, OS, relevant config"},
                {"name": "error_output", "type": "text", "description": "Stack trace or error message"},
            ],
            "suggested_children": ["task"],
            "suggested_labels": ["regression", "ux", "perf", "security"],
        },
        "feature": {
            "type": "feature",
            "display_name": "Feature",
            "description": "User-facing functionality",
            "pack": "core",
            "states": [
                {"name": "proposed", "category": "open"},
                {"name": "approved", "category": "open"},
                {"name": "building", "category": "wip"},
                {"name": "reviewing", "category": "wip"},
                {"name": "done", "category": "done"},
                {"name": "deferred", "category": "done"},
            ],
            "initial_state": "proposed",
            "transitions": [
                {"from": "proposed", "to": "approved", "enforcement": "soft"},
                {"from": "proposed", "to": "deferred", "enforcement": "soft"},
                {"from": "approved", "to": "building", "enforcement": "soft"},
                {"from": "building", "to": "reviewing", "enforcement": "soft"},
                {"from": "reviewing", "to": "done", "enforcement": "soft"},
                {"from": "reviewing", "to": "building", "enforcement": "soft"},
            ],
            "fields_schema": [
                {"name": "user_story", "type": "text", "description": "As a [who], I want [what], so that [why]"},
                {"name": "acceptance_criteria", "type": "text", "description": "Testable conditions for done", "required_at": ["approved"]},
                {"name": "design_notes", "type": "text", "description": "Architecture / UX notes"},
                {"name": "test_strategy", "type": "text", "description": "How this will be tested"},
            ],
            "suggested_children": ["task", "bug"],
            "suggested_labels": ["mvp", "stretch", "v2"],
        },
        "epic": {
            "type": "epic",
            "display_name": "Epic",
            "description": "Large body of work spanning multiple features or tasks",
            "pack": "core",
            "states": [
                {"name": "open", "category": "open"},
                {"name": "in_progress", "category": "wip"},
                {"name": "closed", "category": "done"},
            ],
            "initial_state": "open",
            "transitions": [
                {"from": "open", "to": "in_progress", "enforcement": "soft"},
                {"from": "in_progress", "to": "closed", "enforcement": "soft"},
            ],
            "fields_schema": [
                {"name": "scope", "type": "text", "description": "What's in and out of scope"},
                {"name": "success_metrics", "type": "text", "description": "How we measure success"},
            ],
            "suggested_children": ["feature", "task", "bug"],
            "suggested_labels": [],
        },
    },
    "relationships": [
        {"name": "epic_contains", "from_types": ["task", "bug", "feature"], "to_types": ["epic"], "mechanism": "parent_id", "description": "Work items belong to an epic"},
    ],
    "cross_pack_relationships": [],
    "guide": {
        "overview": "Core software development types for everyday work. Tasks for general work, bugs for defects, features for new functionality, and epics for large initiatives.",
        "when_to_use": "Always enabled. These are your bread-and-butter types for any software project.",
        "states_explained": {
            "open": "Not yet started. Waiting for someone to pick it up.",
            "in_progress": "Actively being worked on by an assignee.",
            "closed": "Work is complete.",
            "triage": "Bug has been reported but not yet confirmed or prioritized.",
            "confirmed": "Bug has been verified and severity assessed. Ready for fixing.",
            "fixing": "Actively writing the fix.",
            "verifying": "Fix is written. Verifying it works correctly.",
            "wont_fix": "Deliberately choosing not to fix this bug.",
            "proposed": "Feature or idea has been suggested but not yet approved.",
            "approved": "Feature has been reviewed and accepted for development.",
            "building": "Actively developing the feature.",
            "reviewing": "Feature is built. Under review before marking done.",
            "done": "Feature is complete and merged.",
            "deferred": "Postponed to a future cycle.",
        },
        "typical_flow": "task: open -> in_progress -> closed. bug: triage -> confirmed -> fixing -> verifying -> closed. feature: proposed -> approved -> building -> reviewing -> done.",
        "tips": [
            "Use tasks for small, well-defined work items",
            "Bugs should always have steps_to_reproduce when possible",
            "Features need acceptance_criteria before being approved",
            "Use epics to group related features and tasks",
        ],
        "common_mistakes": [
            "Skipping triage on bugs — always assess severity first",
            "Closing bugs without fix_verification — how do you know it's fixed?",
            "Approving features without acceptance_criteria — no definition of done",
        ],
    },
}

# ---------------------------------------------------------------------------
# Planning Pack — PMBOK-lite project planning
# ---------------------------------------------------------------------------

_PLANNING_PACK: dict[str, Any] = {
    "pack": "planning",
    "version": "1.0",
    "display_name": "Planning",
    "description": "Hierarchical project planning: milestones, phases, steps, work packages, and deliverables",
    "requires_packs": ["core"],
    "types": {
        "milestone": {
            "type": "milestone",
            "display_name": "Milestone",
            "description": "Top-level delivery marker containing phases",
            "pack": "planning",
            "states": [
                {"name": "planning", "category": "open"},
                {"name": "active", "category": "wip"},
                {"name": "closing", "category": "wip"},
                {"name": "completed", "category": "done"},
            ],
            "initial_state": "planning",
            "transitions": [
                {"from": "planning", "to": "active", "enforcement": "soft"},
                {"from": "active", "to": "closing", "enforcement": "soft"},
                {"from": "closing", "to": "completed", "enforcement": "soft"},
            ],
            "fields_schema": [
                {"name": "target_date", "type": "date", "description": "Target completion date"},
                {"name": "success_criteria", "type": "text", "description": "How we know this is achieved"},
                {"name": "deliverables", "type": "list", "description": "Concrete outputs"},
                {"name": "risks", "type": "text", "description": "Known risks"},
                {"name": "scope_summary", "type": "text", "description": "What's in and out of scope"},
            ],
            "suggested_children": ["phase"],
            "suggested_labels": [],
        },
        "phase": {
            "type": "phase",
            "display_name": "Phase",
            "description": "Logical grouping of steps within a milestone",
            "pack": "planning",
            "states": [
                {"name": "pending", "category": "open"},
                {"name": "active", "category": "wip"},
                {"name": "completed", "category": "done"},
                {"name": "skipped", "category": "done"},
            ],
            "initial_state": "pending",
            "transitions": [
                {"from": "pending", "to": "active", "enforcement": "soft"},
                {"from": "pending", "to": "skipped", "enforcement": "soft"},
                {"from": "active", "to": "completed", "enforcement": "soft"},
            ],
            "fields_schema": [
                {"name": "sequence", "type": "number", "description": "Execution order within milestone"},
                {"name": "entry_criteria", "type": "text", "description": "What must be true before start"},
                {"name": "exit_criteria", "type": "text", "description": "What must be true for completion"},
                {"name": "estimated_effort", "type": "text", "description": "Rough effort estimate"},
            ],
            "suggested_children": ["step"],
            "suggested_labels": [],
        },
        "step": {
            "type": "step",
            "display_name": "Implementation Step",
            "description": "Atomic unit of work within a phase",
            "pack": "planning",
            "states": [
                {"name": "pending", "category": "open"},
                {"name": "in_progress", "category": "wip"},
                {"name": "completed", "category": "done"},
                {"name": "skipped", "category": "done"},
            ],
            "initial_state": "pending",
            "transitions": [
                {"from": "pending", "to": "in_progress", "enforcement": "soft"},
                {"from": "pending", "to": "skipped", "enforcement": "soft"},
                {"from": "in_progress", "to": "completed", "enforcement": "soft"},
            ],
            "fields_schema": [
                {"name": "sequence", "type": "number", "description": "Execution order within phase"},
                {"name": "target_files", "type": "list", "description": "Files to create/modify"},
                {"name": "verification", "type": "text", "description": "How to verify completion"},
                {"name": "implementation_notes", "type": "text", "description": "Technical guidance"},
                {"name": "estimated_minutes", "type": "number", "description": "Rough time estimate"},
                {"name": "done_definition", "type": "text", "description": "Definition of done"},
            ],
            "suggested_children": [],
            "suggested_labels": [],
        },
        "work_package": {
            "type": "work_package",
            "display_name": "Work Package",
            "description": "Bundled unit of assignable work within a project",
            "pack": "planning",
            "states": [
                {"name": "defined", "category": "open"},
                {"name": "assigned", "category": "open"},
                {"name": "executing", "category": "wip"},
                {"name": "delivered", "category": "done"},
            ],
            "initial_state": "defined",
            "transitions": [
                {"from": "defined", "to": "assigned", "enforcement": "soft"},
                {"from": "assigned", "to": "executing", "enforcement": "soft"},
                {"from": "executing", "to": "delivered", "enforcement": "soft"},
            ],
            "fields_schema": [
                {"name": "effort_estimate", "type": "text", "description": "Estimated effort"},
                {"name": "assigned_team", "type": "text", "description": "Team or person responsible"},
                {"name": "acceptance_criteria", "type": "text", "description": "Conditions for delivery"},
            ],
            "suggested_children": ["task"],
            "suggested_labels": [],
        },
        "deliverable": {
            "type": "deliverable",
            "display_name": "Deliverable",
            "description": "Concrete output produced by a work package",
            "pack": "planning",
            "states": [
                {"name": "planned", "category": "open"},
                {"name": "producing", "category": "wip"},
                {"name": "reviewing", "category": "wip"},
                {"name": "accepted", "category": "done"},
            ],
            "initial_state": "planned",
            "transitions": [
                {"from": "planned", "to": "producing", "enforcement": "soft"},
                {"from": "producing", "to": "reviewing", "enforcement": "soft"},
                {"from": "reviewing", "to": "accepted", "enforcement": "soft"},
                {"from": "reviewing", "to": "producing", "enforcement": "soft"},
            ],
            "fields_schema": [
                {"name": "format", "type": "text", "description": "Expected format (document, code, etc.)"},
                {"name": "audience", "type": "text", "description": "Who receives this deliverable"},
                {"name": "quality_criteria", "type": "text", "description": "Quality standards to meet"},
            ],
            "suggested_children": [],
            "suggested_labels": [],
        },
    },
    "relationships": [
        {"name": "milestone_contains_phase", "from_types": ["phase"], "to_types": ["milestone"], "mechanism": "parent_id", "description": "Phases belong to milestones"},
        {"name": "phase_contains_step", "from_types": ["step"], "to_types": ["phase"], "mechanism": "parent_id", "description": "Steps belong to phases"},
        {"name": "work_package_in_milestone", "from_types": ["work_package"], "to_types": ["milestone"], "mechanism": "parent_id", "description": "Work packages belong to milestones"},
        {"name": "deliverable_for_package", "from_types": ["deliverable"], "to_types": ["work_package"], "mechanism": "dependency", "description": "Deliverables are produced by work packages"},
    ],
    "cross_pack_relationships": [],
    "guide": {
        "overview": "PMBOK-lite project planning. Structure work as milestones containing phases containing steps. Use work packages for assignable bundles and deliverables for concrete outputs.",
        "when_to_use": "Use when you need structured project planning with clear phases and dependencies. Good for multi-week efforts with multiple people or agents.",
        "states_explained": {
            "planning": "Milestone is being defined. Scope, timeline, and phases not yet finalized.",
            "active": "Milestone or phase work is underway.",
            "closing": "Milestone is wrapping up. Final checks and deliverable acceptance.",
            "completed": "All work and acceptance criteria met.",
            "pending": "Phase or step not yet started. May be waiting on prior phase.",
            "skipped": "Deliberately omitted. Scope was reduced or no longer needed.",
            "in_progress": "Step is actively being worked.",
            "defined": "Work package scope is clear but not yet assigned.",
            "assigned": "Work package has an owner but work hasn't started.",
            "executing": "Work package is being executed.",
            "delivered": "Work package deliverables are complete.",
            "planned": "Deliverable is identified but not yet being produced.",
            "producing": "Deliverable is being created.",
            "reviewing": "Deliverable is under review.",
            "accepted": "Deliverable meets quality criteria and is accepted.",
        },
        "typical_flow": "milestone: planning -> active -> closing -> completed. phase: pending -> active -> completed. step: pending -> in_progress -> completed.",
        "tips": [
            "Start with milestones, then break into phases, then into steps",
            "Use dependencies between phases to enforce ordering",
            "Steps should be small enough to complete in a single session",
            "Work packages are useful when assigning to different agents or teams",
        ],
        "common_mistakes": [
            "Creating steps without phases — loses the grouping benefit",
            "Skipping entry/exit criteria on phases — no clear transition points",
            "Making steps too large — should be atomic, completable units",
        ],
    },
}

# ---------------------------------------------------------------------------
# Placeholder packs (Phase 2 will add full definitions)
# ---------------------------------------------------------------------------
# These are stubs so the migration can seed pack names.
# Full type definitions, guides, and relationships added in Phase 2.

_REQUIREMENTS_PACK: dict[str, Any] = {
    "pack": "requirements", "version": "1.0", "display_name": "Requirements",
    "description": "Requirements lifecycle: draft, review, approve, implement, verify",
    "requires_packs": ["core"], "types": {}, "relationships": [], "cross_pack_relationships": [],
    "guide": None,
}

_RISK_PACK: dict[str, Any] = {
    "pack": "risk", "version": "1.0", "display_name": "Risk Management",
    "description": "ISO 31000-lite: identify, assess, and manage project risks",
    "requires_packs": ["core"], "types": {}, "relationships": [], "cross_pack_relationships": [],
    "guide": None,
}

_ROADMAP_PACK: dict[str, Any] = {
    "pack": "roadmap", "version": "1.0", "display_name": "Roadmap",
    "description": "Strategic planning: themes, objectives, key results, initiatives",
    "requires_packs": ["core"], "types": {}, "relationships": [], "cross_pack_relationships": [],
    "guide": None,
}

_INCIDENT_PACK: dict[str, Any] = {
    "pack": "incident", "version": "1.0", "display_name": "Incident Management",
    "description": "ITIL-lite: incident response, problem management, change requests",
    "requires_packs": ["core"], "types": {}, "relationships": [], "cross_pack_relationships": [],
    "guide": None,
}

_DEBT_PACK: dict[str, Any] = {
    "pack": "debt", "version": "1.0", "display_name": "Technical Debt",
    "description": "Catalog and remediate technical debt",
    "requires_packs": ["core"], "types": {}, "relationships": [], "cross_pack_relationships": [],
    "guide": None,
}

_SPIKE_PACK: dict[str, Any] = {
    "pack": "spike", "version": "1.0", "display_name": "Spikes",
    "description": "Time-boxed investigation and research",
    "requires_packs": ["core"], "types": {}, "relationships": [], "cross_pack_relationships": [],
    "guide": None,
}

_RELEASE_PACK: dict[str, Any] = {
    "pack": "release", "version": "1.0", "display_name": "Release Management",
    "description": "Release coordination: planning, freezing, testing, shipping",
    "requires_packs": ["core"], "types": {}, "relationships": [], "cross_pack_relationships": [],
    "guide": None,
}

# ---------------------------------------------------------------------------
# Public export
# ---------------------------------------------------------------------------

BUILT_IN_PACKS: dict[str, dict[str, Any]] = {
    "core": _CORE_PACK,
    "planning": _PLANNING_PACK,
    "requirements": _REQUIREMENTS_PACK,
    "risk": _RISK_PACK,
    "roadmap": _ROADMAP_PACK,
    "incident": _INCIDENT_PACK,
    "debt": _DEBT_PACK,
    "spike": _SPIKE_PACK,
    "release": _RELEASE_PACK,
}
```

**Step 4: Run test and CI**

Run: `uv run pytest tests/test_templates.py::TestBuiltInPackData -v && make ci`

**Step 5: Commit**

```bash
git add src/keel/templates_data.py tests/test_templates.py
git commit -m "feat(templates): built-in pack data — core and planning packs

- Core pack: task, bug, feature, epic with full state machines
- Planning pack: milestone, phase, step, work_package, deliverable
- 7 remaining packs as stubs (types added in Phase 2)
- All types validated via parse_type_template + validate_type_template

Implements: WFT-NFR-013, WFT-FR-008 (partial), WFT-AR-003, WFT-AR-004

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

**Definition of Done:**
- [ ] `BUILT_IN_PACKS` dict with 9 packs (2 complete, 7 stubs)
- [ ] Core pack's 4 types have full state machines, transitions, fields
- [ ] Planning pack's 5 types have full state machines, transitions, fields
- [ ] All types pass parse_type_template and validate_type_template
- [ ] Task type uses open/in_progress/closed for backward compat
- [ ] `make ci` passes clean

---

### Task 1.6: TemplateRegistry — Three-Layer Loading

Implement `load()` method that loads from: (1) built-in Python data, (2) `.keel/packs/*.json`, (3) `.keel/templates/*.json`. Also implement loading into a DB for seeding.

**Files:**
- Modify: `src/keel/templates.py`
- Test: `tests/test_templates.py`

**Step 1: Write the failing tests**

```python
# Add to tests/test_templates.py
import json
from pathlib import Path


class TestTemplateLoading:
    """Test three-layer template resolution."""

    @pytest.fixture()
    def keel_dir(self, tmp_path: Path) -> Path:
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}
        (keel_dir / "config.json").write_text(json.dumps(config))
        return keel_dir

    def test_load_built_ins(self, keel_dir: Path) -> None:
        reg = TemplateRegistry()
        reg.load(keel_dir)
        assert reg.get_type("task") is not None
        assert reg.get_type("bug") is not None
        assert reg.get_type("milestone") is not None

    def test_load_respects_enabled_packs(self, keel_dir: Path) -> None:
        """Only types from enabled packs should be available."""
        reg = TemplateRegistry()
        reg.load(keel_dir)
        # Core and planning enabled — their types exist
        assert reg.get_type("task") is not None
        assert reg.get_type("milestone") is not None
        # Risk pack not enabled — risk type should NOT be available
        # (risk pack is a stub with no types in Phase 1, but test the principle)

    def test_load_is_idempotent(self, keel_dir: Path) -> None:
        reg = TemplateRegistry()
        reg.load(keel_dir)
        types_count_1 = len(reg.list_types())
        reg.load(keel_dir)
        types_count_2 = len(reg.list_types())
        assert types_count_1 == types_count_2

    def test_load_project_override(self, keel_dir: Path) -> None:
        """Layer 3 (project-local) overrides built-in types."""
        templates_dir = keel_dir / "templates"
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
        reg.load(keel_dir)
        task = reg.get_type("task")
        assert task is not None
        assert task.display_name == "Custom Task"
        assert task.initial_state == "todo"

    def test_load_skips_invalid_json(self, keel_dir: Path) -> None:
        """Invalid JSON files in templates/ should be skipped, not crash."""
        templates_dir = keel_dir / "templates"
        templates_dir.mkdir()
        (templates_dir / "broken.json").write_text("not valid json {{{")

        reg = TemplateRegistry()
        reg.load(keel_dir)  # Should not raise
        assert reg.get_type("task") is not None  # Built-ins still loaded

    def test_load_missing_enabled_packs_defaults(self, keel_dir: Path) -> None:
        """Config without enabled_packs defaults to core + planning."""
        config = {"prefix": "test", "version": 1}
        (keel_dir / "config.json").write_text(json.dumps(config))
        reg = TemplateRegistry()
        reg.load(keel_dir)
        assert reg.get_type("task") is not None
```

**Step 2: Run tests, implement, commit**

The `load()` method reads `config.json` for `enabled_packs`, then:
1. Iterates `BUILT_IN_PACKS` from `templates_data.py`, registering types from enabled packs
2. Scans `.keel/packs/*.json` for installed packs (Layer 2)
3. Scans `.keel/templates/*.json` for project-local overrides (Layer 3)

Implementation should use `_register_type()` and `_register_pack()` from Task 1.3, with `_loaded` flag for idempotency.

**Commit message:**
```
feat(templates): three-layer template loading with enabled_packs filtering

- load() reads config.json for enabled_packs (default: core, planning)
- Layer 1: Built-in packs from templates_data.py
- Layer 2: Installed packs from .keel/packs/*.json
- Layer 3: Project-local overrides from .keel/templates/*.json
- Idempotent: second load() call is no-op
- Skips invalid JSON with warning log

Implements: WFT-FR-002, WFT-FR-003, WFT-FR-004, WFT-FR-005, WFT-FR-057, WFT-AR-009
```

**Definition of Done:**
- [ ] load() reads config and loads all three layers
- [ ] Only types from enabled packs are registered
- [ ] Layer 3 overrides Layer 1 (whole-document replacement)
- [ ] Invalid JSON files are skipped with warning
- [ ] Missing enabled_packs defaults to ["core", "planning"]
- [ ] Idempotent (second call is no-op)
- [ ] `make ci` passes clean

---

### Task 1.7: Schema Migration v4 → v5

Add the v5 migration: create `type_templates` and `packs` tables, migrate old `templates` data, seed built-ins, drop old `templates` table. **This is a one-way door — includes backup, error handling, and post-migration validation (review B2).**

**Files:**
- Modify: `src/keel/core.py` (lines 136, 255-259 for CURRENT_SCHEMA_VERSION and MIGRATIONS)
- Test: `tests/test_migration_v5.py` (new file)

**Step 1: Write the failing tests**

Test fresh v5 creation, v4→v5 upgrade, old templates migration, built-in seeding, old table drop, **migration failure recovery, and post-migration validation**.

Add specific test:
```python
def test_v5_migration_failure_recovery(tmp_path: Path) -> None:
    """If seeding fails after table creation, old data should be recoverable."""
    # Create a v4 database with templates
    # Monkeypatch BUILT_IN_PACKS to raise during seeding
    # Run migration — should fail
    # Verify old templates still exist in _templates_v4_backup table
    # Verify migration can be retried after fixing the issue

def test_v5_migration_preserves_custom_template_states(tmp_path: Path) -> None:
    """v4 databases may have custom template states beyond the default 3 (W8).
    Migration should preserve these in the backup and migrate them correctly."""
    # Create a v4 database
    # Insert a template with custom states (e.g. "review", "approved")
    # Run migration
    # Verify custom states appear in type_templates definition
    # Verify _templates_v4_backup has the original data
```

**Step 2: Implement**

Add `_migrate_v5_workflow_templates(conn)` function in `core.py`:

1. **Backup first:** `CREATE TABLE _templates_v4_backup AS SELECT * FROM templates` (review B2)
2. Creates `type_templates` and `packs` tables (design Section 6.1)
3. Migrates rows from old `templates` table into `type_templates` with default 3-state definitions
4. Seeds all 9 built-in packs into `packs` table
5. Seeds built-in type templates into `type_templates` (from `templates_data.BUILT_IN_PACKS`)
6. **Validate:** Check `type_templates` row count >= number of built-in types. If validation fails, log error and raise without dropping old table.
7. Drop old `templates` table (only after successful validation)
8. Keep `_templates_v4_backup` table for one schema version (drop in hypothetical v6)
9. Updates CURRENT_SCHEMA_VERSION to 5
10. Adds `(5, _migrate_v5_workflow_templates)` to MIGRATIONS list

**Error handling pattern (review B2):**
```python
def _migrate_v5_workflow_templates(conn: sqlite3.Connection) -> None:
    logger.info("Starting v4→v5 migration: workflow templates")

    # Step 1: Backup old templates table
    conn.execute("CREATE TABLE IF NOT EXISTS _templates_v4_backup AS SELECT * FROM templates")
    logger.debug("Backed up templates table to _templates_v4_backup")

    # Step 2: Create new tables
    conn.execute("""CREATE TABLE IF NOT EXISTS type_templates (...)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS packs (...)""")

    # Step 3: Migrate old data + seed built-ins (wrapped in try/except)
    try:
        # ... migrate old templates rows ...
        # ... seed built-in packs and types ...
        conn.execute("COMMIT")
    except Exception:
        logger.error("v4→v5 migration failed during seeding — backup preserved in _templates_v4_backup")
        raise

    # Step 4: Post-migration validation
    row_count = conn.execute("SELECT COUNT(*) FROM type_templates").fetchone()[0]
    if row_count < len(EXPECTED_BUILTIN_TYPES):
        msg = f"Migration validation failed: expected >= {len(EXPECTED_BUILTIN_TYPES)} types, got {row_count}"
        logger.error(msg)
        raise RuntimeError(msg)

    # Step 5: Drop old table only after validation passes
    conn.execute("DROP TABLE IF EXISTS templates")
    logger.info("v4→v5 migration complete: %d types, backup in _templates_v4_backup", row_count)
```

Key implementation notes:
- `type_templates` schema: `type TEXT PK, pack TEXT NOT NULL DEFAULT 'core', definition TEXT NOT NULL, is_builtin BOOLEAN NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL`
- `packs` schema: `name TEXT PK, version TEXT NOT NULL, definition TEXT NOT NULL, is_builtin BOOLEAN NOT NULL DEFAULT 0, enabled BOOLEAN NOT NULL DEFAULT 1`
- Old `templates` rows get enriched with default states (open/in_progress/closed) and assigned to "custom" pack
- Use `INSERT OR IGNORE` for built-in seeding (idempotent)
- Log at INFO for start/complete, DEBUG for steps, ERROR for failures

**Commit message:**
```
feat(core): schema migration v4→v5 for workflow templates

- Backs up old templates table to _templates_v4_backup before migration
- Creates type_templates and packs tables
- Migrates old templates table data with default 3-state machines
- Seeds 9 built-in packs (only core+planning enabled by default)
- Seeds built-in type templates from templates_data
- Post-migration validation: verifies row counts before dropping old table
- Error recovery: backup table preserved on failure for manual recovery
- Drops old templates table only after validation passes
- CURRENT_SCHEMA_VERSION bumped to 5

Implements: WFT-FR-051 through WFT-FR-058, WFT-NFR-006
```

**Definition of Done:**
- [ ] Fresh DB creates v5 schema directly
- [ ] v4 DB upgrades to v5 with data preserved
- [ ] Old templates backed up to `_templates_v4_backup` before drop
- [ ] Old templates migrated to type_templates with enriched definitions
- [ ] 9 built-in packs seeded (core and planning enabled)
- [ ] Post-migration validation checks row counts
- [ ] Migration failure preserves backup and raises clear error
- [ ] Migration failure test passes (simulated seeding failure)
- [ ] Old templates table dropped only after validation
- [ ] Existing issue data untouched
- [ ] Migration logs at INFO/DEBUG/ERROR levels
- [ ] `make ci` passes clean (all existing tests still pass)

---

### Task 1.8: KeelDB Integration — Lazy TemplateRegistry

Wire TemplateRegistry into KeelDB as a lazy property, resolving the circular dependency (WFT-AR-001).

**Files:**
- Modify: `src/keel/core.py` (KeelDB class, lines 454-506)
- Test: `tests/test_templates.py`

**Step 1: Write the failing tests**

```python
class TestKeelDBTemplateIntegration:
    """Test TemplateRegistry integration into KeelDB."""

    @pytest.fixture()
    def keel_dir(self, tmp_path: Path) -> Path:
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}
        (keel_dir / "config.json").write_text(json.dumps(config))
        return keel_dir

    def test_templates_property_lazy(self, keel_dir: Path) -> None:
        from keel.core import KeelDB
        db = KeelDB(keel_dir / "keel.db", prefix="test")
        db.initialize()
        # First access should create the registry
        reg = db.templates
        assert reg is not None
        assert reg.get_type("task") is not None
        # Second access returns same instance
        assert db.templates is reg

    def test_templates_injectable(self, keel_dir: Path) -> None:
        from keel.core import KeelDB
        from keel.templates import TemplateRegistry
        custom_reg = TemplateRegistry()
        db = KeelDB(keel_dir / "keel.db", prefix="test", template_registry=custom_reg)
        db.initialize()
        assert db.templates is custom_reg
```

**Step 2: Implement**

Add to `KeelDB.__init__()`: `self._template_registry: TemplateRegistry | None = template_registry`

Add optional `template_registry` parameter to `__init__`.

Add `templates` property that lazy-creates the registry:
```python
@property
def templates(self) -> TemplateRegistry:
    if self._template_registry is None:
        from keel.templates import TemplateRegistry
        self._template_registry = TemplateRegistry()
        keel_dir = self.db_path.parent
        self._template_registry.load(keel_dir)
    return self._template_registry
```

Use `TYPE_CHECKING` guard for the type hint import (WFT-NFR-012):
```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from keel.templates import TemplateRegistry
```

**Commit message:**
```
feat(core): lazy TemplateRegistry property on KeelDB

- KeelDB.templates property with lazy initialization
- Runtime import inside property to avoid circular dependency
- TYPE_CHECKING guard for type hints
- Optional template_registry parameter for injection

Implements: WFT-AR-001, WFT-NFR-012
```

**Definition of Done:**
- [ ] `db.templates` returns a TemplateRegistry
- [ ] Registry is created lazily on first access
- [ ] No circular import at module load time
- [ ] Injectable via constructor parameter
- [ ] `make ci` passes clean

---

### Task 1.9: Core Engine — Per-Type Status Validation

Modify `_validate_status()`, `create_issue()`, `update_issue()`, `close_issue()`, `claim_issue()`, `release_claim()` to use the template system.

**Files:**
- Modify: `src/keel/core.py` (lines 567-845)
- Test: `tests/test_templates.py` (integration tests)

This is the highest-risk task. It changes the behavior of 6 existing methods. Must maintain backward compatibility (WFT-AR-011).

**Step 1: Write the failing tests**

Test that:
- `create_issue(type="bug")` creates with initial_state "triage" (not "open")
- `update_issue(status="confirmed")` succeeds for bug type
- `update_issue(status="nonexistent")` fails for bug type
- Hard enforcement: updating bug from verifying→closed without fix_verification raises ValueError
- Soft enforcement: updating bug triage→closed succeeds with warning event recorded
- `close_issue()` uses first done state for the type
- `close_issue(status="wont_fix")` uses specified done state
- `claim_issue()` transitions to first wip state for the type
- Atomic transition-with-fields: `update_issue(status="verifying", fields={"fix_verification": "tests pass"})` succeeds in one call for bug type (WFT-FR-069)
- Atomic transition-with-fields: on hard failure, neither fields nor status are saved (atomicity guarantee)
- Legacy behavior: task type still works with open/in_progress/closed
- Unknown type falls back to global workflow_states

**Step 2: Implement changes to core.py**

Key changes:
1. `_validate_status(status, type)` — check via `self.templates.get_valid_states(type)`, fall back to `self.workflow_states`
2. `create_issue()` — use `self.templates.get_initial_state(type)` instead of hardcoded `'open'`
3. `update_issue()` — when both `status` and `fields` are provided, merge fields into the issue FIRST, then validate the transition (WFT-FR-069 — atomic transition-with-fields). On hard failure, raise ValueError (no fields or status saved). On soft warning, record warning events and save both fields and status.
4. `close_issue()` — add optional `status` parameter; validate target is done-category; default to first done state
5. `claim_issue()` — use `self.templates.get_first_state_of_category(type, "wip")` instead of hardcoded `'in_progress'`; check current state is open-category
6. `release_claim()` — similar treatment, use first open-category state
7. `_build_issues_batch()` — `is_ready` uses category instead of `status == "open"` (line 718)
8. `update_issue()` — `closed_at` set when entering done-category, not just `status == "closed"`

**Commit message:**
```
feat(core): per-type status validation and transition enforcement

- _validate_status() checks per-type states via TemplateRegistry
- create_issue() uses get_initial_state() per type
- update_issue() validates transitions with soft/hard enforcement
- close_issue() supports optional status parameter for multi-done types
- claim_issue() transitions to first wip-category state
- _build_issues_batch() uses category for is_ready computation
- Soft enforcement warnings recorded as events
- Backward compatible: task/epic use open/in_progress/closed

Implements: WFT-FR-006, WFT-FR-007, WFT-FR-009, WFT-FR-017, WFT-FR-018,
WFT-FR-046, WFT-FR-047, WFT-FR-048, WFT-FR-049, WFT-FR-069, WFT-SR-004
```

**Definition of Done:**
- [ ] create_issue with typed issues uses type-specific initial state
- [ ] update_issue validates transitions with enforcement
- [ ] Hard enforcement rejects missing required fields
- [ ] Soft warnings recorded as events in events table
- [ ] close_issue accepts optional status parameter
- [ ] claim_issue uses first wip-category state
- [ ] is_ready uses category mapping
- [ ] All existing tests still pass (backward compat)
- [ ] `make ci` passes clean

---

### Task 1.10: Category-Aware Queries

Modify `list_issues()`, `get_ready()`, `get_blocked()`, and `get_critical_path()` to use category mapping instead of literal status strings.

**Files:**
- Modify: `src/keel/core.py` (lines 885-1089)
- Test: `tests/test_templates.py`

**Step 1: Write the failing tests**

Test that:
- `list_issues(status="open")` returns bugs in "triage" and "confirmed" states
- `list_issues(status="wip")` returns bugs in "fixing" and "verifying"
- `list_issues(status="triage")` returns only bugs in literal "triage" state
- `get_ready()` returns issues whose status maps to "open" category
- `get_blocked()` returns issues whose status maps to "open" category with open blockers

**Step 2: Implement**

The approach follows WFT-SR-012 (two-pass for v1):
- `list_issues(status=)`: If status is a category name ("open", "wip", "done"), build a state list from all enabled types' states with that category, then use `WHERE status IN (...)`. If status is a specific state name, use `WHERE status = ?`.
- `get_ready()` and `get_blocked()`: Replace `i.status = 'open'` with `i.status IN (...)` where the list comes from all open-category states. Replace `blocker.status != 'closed'` with `blocker.status NOT IN (...)` where the list is all done-category states.
- `get_critical_path()`: Replace `status != 'closed'` with category-based check.

Add helper method `_get_states_for_category(category)` that returns all state names mapping to the given category across all registered types.

**SQL safety requirements (review B1):**

1. **Use parameterized placeholders**, matching the existing pattern at `core.py:646-691` (`_build_issues_batch`):
   ```python
   def _get_states_for_category(self, category: StateCategory) -> list[str]:
       """Collect all state names that map to a category across enabled types."""
       states: list[str] = []
       for tpl in self.templates.list_types():
           for s in tpl.states:
               if s.category == category and s.name not in states:
                   states.append(s.name)
       return states

   # Usage in queries — ALWAYS use parameterized placeholders:
   states = self._get_states_for_category("open")
   if not states:
       return []  # Empty state list guard — return empty result, don't execute malformed SQL
   placeholders = ",".join("?" * len(states))
   self.conn.execute(f"SELECT * FROM issues WHERE status IN ({placeholders})", states)
   ```

2. **Empty state list guard:** If `_get_states_for_category()` returns an empty list, return an empty result set immediately without executing the query. `WHERE status IN ()` is malformed SQL in SQLite.

3. **State names are pre-validated:** `StateDefinition.__post_init__` (Task 1.1) enforces `^[a-z][a-z0-9_]{0,63}$` on all state names at parse time. The parameterized placeholders provide defense-in-depth.

**Commit message:**
```
feat(core): category-aware queries for list, ready, blocked, critical path

- list_issues(status=) accepts categories ('open', 'wip', 'done') and specific states
- get_ready() uses open-category states instead of literal 'open'
- get_blocked() uses open-category and done-category states
- get_critical_path() uses done-category for filtering
- Two-pass approach: gather state lists, use parameterized ? placeholders
- Empty state list guard: returns empty result instead of malformed SQL
- _get_states_for_category() helper collects states across all types

Implements: WFT-FR-009, WFT-FR-048, WFT-SR-012, WFT-SR-015
```

**Definition of Done:**
- [ ] list_issues accepts both categories and specific states
- [ ] get_ready returns issues in any open-category state
- [ ] get_blocked uses category-aware blocker check
- [ ] get_critical_path uses category-aware filtering
- [ ] All SQL uses parameterized `?` placeholders (no string interpolation of state names)
- [ ] Empty state list returns empty result (no `WHERE IN ()` executed)
- [ ] Existing tests pass (backward compat: "open" is both a category and a literal state)
- [ ] `make ci` passes clean

---

### Task 1.11: Issue.to_dict() Includes status_category

Add `status_category` to Issue serialization (WFT-FR-038).

**Files:**
- Modify: `src/keel/core.py` (Issue.to_dict, line 407)
- Test: `tests/test_templates.py`

**Step 1: Write test, implement, commit**

Add `status_category` computed field to `Issue` dataclass. In `_build_issues_batch()`, compute category via `self.templates.get_category(type, status)` and set it on each Issue. In `to_dict()`, include `status_category`.

For unknown types/states, default category to: "open" if status matches any known open-ish state, else infer from status name (fallback heuristic: "open" -> "open", "in_progress" -> "wip", "closed" -> "done").

**Commit message:**
```
feat(core): Issue.to_dict() includes status_category field

- Issue dataclass gains status_category computed field
- _build_issues_batch() resolves category via TemplateRegistry
- Fallback: open/in_progress/closed map to open/wip/done

Implements: WFT-FR-038
```

---

### Task 1.12: New KeelDB Methods — get_valid_transitions() and validate_issue()

**Files:**
- Modify: `src/keel/core.py`
- Test: `tests/test_templates.py`

Implement `KeelDB.get_valid_transitions(issue_id)` and `KeelDB.validate_issue(issue_id)` that delegate to TemplateRegistry with current issue state and fields. Return types are `list[TransitionOption]` and `ValidationResult`.

**Commit message:**
```
feat(core): add get_valid_transitions() and validate_issue() methods

- get_valid_transitions() returns TransitionOptions for an issue
- validate_issue() checks issue against its template
- Both delegate to TemplateRegistry with current state and fields

Implements: WFT-FR-050
```

---

### Task 1.13: Update _seed_templates() and Template-Related Methods

Update `_seed_templates()` to work with the new `type_templates` table instead of the old `templates` table. Update `get_template()` and `list_templates()` to read from `type_templates`.

**Files:**
- Modify: `src/keel/core.py` (lines 530-565)
- Modify: `src/keel/core.py` — remove old `BUILT_IN_TEMPLATES` list (lines 265-385)
- Test: existing tests should still pass

**Commit message:**
```
refactor(core): update template methods for new type_templates table

- _seed_templates() seeds from templates_data.BUILT_IN_PACKS
- get_template() reads from type_templates with enriched definitions
- list_templates() reads from type_templates
- Remove old BUILT_IN_TEMPLATES list (replaced by templates_data.py)

Implements: WFT-FR-054, WFT-FR-066
```

---

### Task 1.14: Config Enrichment

Update `read_config()` to default `enabled_packs` to `["core", "planning"]` when missing. Update `from_project()` to pass enabled packs through.

**Files:**
- Modify: `src/keel/core.py` (lines 45-57)
- Test: existing config tests

**Commit message:**
```
feat(core): config.json gains enabled_packs with backward-compatible default

- read_config() defaults enabled_packs to ["core", "planning"]
- from_project() preserves enabled_packs for TemplateRegistry

Implements: WFT-FR-057
```

---

### Task 1.15: Regression Test Suite

Run the full existing test suite and fix any failures caused by Phase 1 changes. Then add specific backward-compatibility regression tests.

**Files:**
- Modify: various test files as needed
- Create: `tests/test_backward_compat.py`

Tests to add:
- Create issues with old-style states (open, in_progress, closed) — still works
- list_issues(status="open") returns issues with literal "open" status
- get_ready() returns issues with literal "open" status
- Existing MCP tools return same results for old-style data
- Summary generation produces valid output with old-style data

**Commit message:**
```
test: backward compatibility regression tests for workflow templates

- Old-style states (open/in_progress/closed) continue to work
- Existing queries produce identical results for legacy data
- Summary generation unchanged for legacy projects

Validates: WFT-AR-011, WFT-SR-015
```

---

## Phase 2: Built-in Packs

Phase 2 delivers pack definitions with complete state machines, field schemas, workflow guides, and relationships. Delivered in tiers based on agent usage data (WFT-FR-074).

**Requirements covered:** WFT-FR-008, WFT-FR-019, WFT-FR-024, WFT-FR-025, WFT-FR-074, WFT-NFR-013, WFT-NFR-018, WFT-DR-001 through WFT-DR-007, WFT-DR-012, WFT-DR-014, WFT-SR-005, WFT-AR-005

> **Agent review — Tiered delivery (WFT-FR-074):**
> Agent self-review identified that core + planning cover ~90% of actual agent work. Delivering
> all 26 types simultaneously risks quality dilution across workflow guides and field schemas.
> Tier 1 (core + planning, 9 types) is completed in Phase 1. Tier 2 ships as Phase 2.

---

### Tier 2 — This Phase

### Task 2.1: Risk Pack (risk, mitigation)
### Task 2.2: Spike Pack (spike, finding)

> Risk and spike are recommended as the next priority based on agent usage patterns.
> These cover the most common non-task/non-bug workflows agents encounter.

Each task follows the same pattern:
1. Write parametrized tests validating all types in the pack parse and validate
2. Write the full pack definition in `templates_data.py` following the state machines in design Section 10.2
3. Include complete workflow guides with compact state diagrams, word limits per WFT-FR-031 (`overview` < 50 words, `when_to_use` < 30 words, `state_diagram` field required)
4. Include `tips` and `common_mistakes` as the highest-value agent-facing content
5. Include relationships and cross-pack relationships per design Section 10.3
6. Run `make ci`
7. Commit

### Task 2.3: End-to-End Workflow Tests

**Files:**
- Create: `tests/test_e2e_workflows.py`

Test 3-5 representative agent workflows:
- Risk workflow: create risk → assess → mitigate → close
- Spike → finding → spawned work items
- Planning workflow: milestone → phase → step with dependencies
- Bug lifecycle: triage → confirmed → fixing → verifying → closed

**Implements:** WFT-NFR-018

### Task 2.4: Parametrized Validation for All Shipped Types

**Files:**
- Modify: `tests/test_templates.py`

Add parametrized test that iterates all types from shipped packs (core, planning, risk, spike), parsing and validating each one. This catches typos and schema errors across the content corpus.

**Implements:** WFT-NFR-016

---

### Tier 3 — Deferred (ship incrementally based on usage data)

The following packs are deferred until there is evidence agents use them. Each follows the same task pattern as Tier 2 when delivered.

### Task 2.T3-1: Requirements Pack (requirement, test_case, decision_record)
### Task 2.T3-2: Roadmap Pack (theme, objective, key_result, initiative)
### Task 2.T3-3: Incident Pack (incident, problem, change_request)
### Task 2.T3-4: Debt Pack (tech_debt, refactoring)
### Task 2.T3-5: Release Pack (release, changelog_entry)

---

## Phase 3: Agent Interface

Phase 3 adds 12 new MCP tools (including `reload_templates`, `claim_next`, and `include_transitions` on get_issue) and 11 new CLI commands. These are the user-facing surfaces for the template system.

**Requirements covered:** WFT-FR-026 through WFT-FR-037, WFT-FR-039 through WFT-FR-045, WFT-FR-068, WFT-FR-070, WFT-FR-072, WFT-FR-073, WFT-FR-075, WFT-NFR-009, WFT-DR-003, WFT-DR-004, WFT-DR-010

---

### Task 3.1: MCP Tools — list_types, get_type_info, list_packs
### Task 3.2: MCP Tools — get_valid_transitions, validate_issue
### Task 3.3: MCP Tools — get_workflow_guide, explain_state
### Task 3.4: MCP — Status Parameter Category Enum + Warning Return Path
### Task 3.5: MCP — get_template Backward Compatibility
### Task 3.6: MCP — Pack-Aware Workflow Prompt
### Task 3.7: CLI — keel types, type-info, transitions
### Task 3.8: CLI — keel packs, validate, guide
### Task 3.9: Install — CLAUDE.md Instructions Update

Each MCP tool task:
1. Write test using the existing MCP test pattern (see `tests/test_mcp.py`)
2. Add tool registration and handler in `mcp_server.py`
3. Run `make ci`
4. Commit

> **Review note W6 — Preventing agent retry storms:**
> Hard enforcement (rejecting invalid transitions) can cause agents to retry the same
> invalid operation in a loop. MCP tool descriptions MUST include clear guidance:
> - `validate_issue`: description should say "Call get_valid_transitions first to see allowed states"
> - Error responses from transition validation should include `valid_transitions` list in the
>   response body so the agent can self-correct without a second round-trip
> - Consider including a `hint` field in error responses: `"hint": "Use get_valid_transitions to see allowed next states"`

Each CLI task:
1. Write test using Click test runner (see `tests/test_cli.py`)
2. Add Click command in `cli.py`
3. Run `make ci`
4. Commit

### Task 3.10: MCP + CLI — reload_templates (Cache Invalidation)

> **Review fix B3**: The MCP server is a long-lived subprocess. If an agent enables a pack via CLI, the MCP server's cached TemplateRegistry becomes stale. This task adds an explicit reload mechanism.

**Files:**
- Modify: `src/keel/core.py` — add `reload_templates()` method
- Modify: `src/keel/mcp_server.py` — add `reload_templates` MCP tool
- Modify: `src/keel/cli.py` — add `keel templates reload` CLI command
- Test: `tests/test_templates.py`, `tests/test_mcp.py`, `tests/test_cli.py`

**Step 1: Write the failing tests**

```python
# tests/test_templates.py
def test_reload_templates_clears_cache(tmp_path):
    """After reload, registry reflects newly enabled packs."""
    db = KeelDB(tmp_path / ".keel")
    db.initialize()
    # Initially only core+planning packs
    assert "risk" not in [t.name for t in db.templates.list_types()]
    # Enable risk pack via config
    config = db.read_config()
    config["enabled_packs"] = ["core", "planning", "risk"]
    db.write_config(config)
    # Stale cache — still no risk types
    assert "risk" not in [t.name for t in db.templates.list_types()]
    # Reload clears cache
    db.reload_templates()
    assert "risk" in [t.name for t in db.templates.list_types()]
```

**Step 2: Implement**

```python
# src/keel/core.py — KeelDB method
def reload_templates(self) -> None:
    """Clear cached TemplateRegistry so next access rebuilds from config."""
    self._template_registry = None
    logger.info("Template registry cache cleared — will reload on next access")
```

MCP tool handler:
```python
# src/keel/mcp_server.py
@server.tool()
async def reload_templates() -> str:
    """Reload template registry after pack configuration changes.

    Call this after enabling/disabling packs via CLI to refresh the MCP server's
    cached template definitions without restarting the server.
    """
    db.reload_templates()
    return json.dumps({"status": "ok", "message": "Template registry reloaded"})
```

CLI command:
```python
# src/keel/cli.py
@templates_group.command("reload")
@click.pass_context
def templates_reload(ctx):
    """Reload template registry from config."""
    db = ctx.obj["db"]
    db.reload_templates()
    click.echo("Template registry reloaded.")
```

**Step 3: Run tests, commit**

**Commit message:**
```
feat(core): add reload_templates() for cache invalidation

- KeelDB.reload_templates() clears cached TemplateRegistry
- MCP reload_templates tool lets agents refresh after pack changes
- CLI `keel templates reload` for manual refresh
- Prevents stale template cache in long-lived MCP server subprocess

Implements: WFT-FR-068 (review fix B3)
```

**Definition of Done:**
- [ ] reload_templates() clears registry cache
- [ ] MCP tool registered and callable
- [ ] CLI command works
- [ ] Test proves stale→fresh transition
- [ ] make ci passes

---

### Task 3.11: MCP — get_issue with include_transitions Parameter (WFT-FR-070)

Add an optional `include_transitions` parameter (default: false) to the `get_issue` MCP tool. When true, the response includes a `valid_transitions` array matching the `get_valid_transitions` output format.

**Files:**
- Modify: `src/keel/mcp_server.py` — update `get_issue` tool schema and handler
- Test: `tests/test_mcp.py`

**Why this matters:** Session resumption is the #2 most common agent workflow. Agents call `get_issue` + `get_valid_transitions` for each in-progress issue at session start. This collapses 2N calls to N calls.

**Step 1: Write the failing test**

```python
def test_get_issue_with_transitions(db, mcp_client):
    """get_issue with include_transitions=true embeds valid transitions."""
    issue = db.create_issue(title="Test bug", type="bug")
    result = await mcp_client.call_tool("get_issue", {"id": issue.id, "include_transitions": True})
    data = json.loads(result)
    assert "valid_transitions" in data
    assert isinstance(data["valid_transitions"], list)
    assert any(t["to"] for t in data["valid_transitions"])

def test_get_issue_without_transitions_unchanged(db, mcp_client):
    """get_issue without include_transitions has no valid_transitions key."""
    issue = db.create_issue(title="Test", type="task")
    result = await mcp_client.call_tool("get_issue", {"id": issue.id})
    data = json.loads(result)
    assert "valid_transitions" not in data
```

**Step 2: Implement, run tests, commit**

**Commit message:**
```
feat(mcp): get_issue gains include_transitions parameter

- Optional include_transitions=true embeds valid_transitions in response
- Collapses session resumption from 2N calls to N calls
- Default false — no change to existing behavior

Implements: WFT-FR-070
```

**Definition of Done:**
- [ ] include_transitions=true returns valid transitions in get_issue response
- [ ] Default (false) produces identical response to current behavior
- [ ] make ci passes

---

### Task 3.12: MCP — claim_next Compound Operation (WFT-FR-072)

Add a `claim_next` MCP tool that atomically selects and claims the highest-priority ready issue matching optional filters.

**Files:**
- Modify: `src/keel/core.py` — add `claim_next()` method
- Modify: `src/keel/mcp_server.py` — add `claim_next` MCP tool
- Test: `tests/test_templates.py`, `tests/test_mcp.py`

**Step 1: Write the failing tests**

```python
def test_claim_next_returns_highest_priority_ready(db):
    """claim_next picks the highest-priority unblocked issue."""
    db.create_issue(title="P2 task", type="task", priority=2)
    db.create_issue(title="P0 task", type="task", priority=0)
    db.create_issue(title="P1 task", type="task", priority=1)
    result = db.claim_next(assignee="agent-1")
    assert result is not None
    assert result.title == "P0 task"
    assert result.assignee == "agent-1"

def test_claim_next_skips_blocked_issues(db):
    """claim_next does not return issues with open blockers."""
    blocker = db.create_issue(title="Blocker", type="task")
    blocked = db.create_issue(title="Blocked", type="task", priority=0)
    db.add_dependency(blocked.id, blocker.id)
    result = db.claim_next(assignee="agent-1")
    assert result is not None
    assert result.id != blocked.id

def test_claim_next_empty_returns_none(db):
    """claim_next returns None when no ready work exists."""
    result = db.claim_next(assignee="agent-1")
    assert result is None

def test_claim_next_with_type_filter(db):
    """claim_next with type filter only considers matching types."""
    db.create_issue(title="Bug", type="bug", priority=0)
    db.create_issue(title="Task", type="task", priority=1)
    result = db.claim_next(assignee="agent-1", type_filter="task")
    assert result is not None
    assert result.type == "task"
```

**Step 2: Implement, run tests, commit**

```python
# src/keel/core.py
def claim_next(
    self, assignee: str, *, type_filter: str | None = None,
    priority_min: int | None = None, priority_max: int | None = None,
) -> Issue | None:
    """Atomically select and claim the highest-priority ready issue."""
    ready = self.get_ready()
    candidates = [i for i in ready if i.assignee is None]
    if type_filter:
        candidates = [i for i in candidates if i.type == type_filter]
    if priority_min is not None:
        candidates = [i for i in candidates if i.priority >= priority_min]
    if priority_max is not None:
        candidates = [i for i in candidates if i.priority <= priority_max]
    if not candidates:
        return None
    # Highest priority = lowest number (P0 > P1 > P2)
    candidates.sort(key=lambda i: (i.priority, i.created_at))
    for candidate in candidates:
        try:
            return self.claim_issue(candidate.id, assignee)
        except ValueError:
            continue  # Race condition: someone else claimed it
    return None
```

**Commit message:**
```
feat(core): add claim_next() compound operation

- Atomically select and claim highest-priority ready issue
- Optional filters: type, priority_min, priority_max
- Race-safe: retries next candidate if claim fails
- MCP claim_next tool wraps core method

Implements: WFT-FR-072
```

**Definition of Done:**
- [ ] claim_next returns highest-priority ready unclaimed issue
- [ ] Filters by type, priority range
- [ ] Returns None when no matching work exists
- [ ] Race-safe (retries on claim conflict)
- [ ] MCP tool registered and callable
- [ ] make ci passes

---

### Task 3.13: Batch Operations Under Workflow Templates (WFT-FR-073)

Update `batch_close` and `batch_update` to handle per-type validation, partial failures, and aggregate warning collection.

**Files:**
- Modify: `src/keel/core.py` — update batch methods
- Modify: `src/keel/mcp_server.py` — update batch tool response format
- Test: `tests/test_templates.py`, `tests/test_mcp.py`

**Step 1: Write the failing tests**

```python
def test_batch_close_mixed_types_partial_failure(db):
    """batch_close with mixed types: soft failures proceed, hard failures don't."""
    # Create a task (soft enforcement) and a bug requiring fix_verification (hard)
    task = db.create_issue(title="Task", type="task")
    bug = db.create_issue(title="Bug", type="bug")
    db.update_issue(bug.id, status="fixing")  # Bug needs fix_verification to close

    result = db.batch_close([task.id, bug.id])
    assert task.id in result["succeeded"]
    assert bug.id in result["failed"]
    assert any(f["id"] == bug.id for f in result["failed"])

def test_batch_close_collects_soft_warnings(db):
    """batch_close collects soft warnings per issue."""
    # Create issues with non-standard transition paths
    issue = db.create_issue(title="Bug", type="bug")
    result = db.batch_close([issue.id])
    assert issue.id in result["succeeded"]
    # Soft warning for skipping states
    if result["warnings"]:
        assert any(w["id"] == issue.id for w in result["warnings"])
```

**Step 2: Implement**

Return format:
```python
{
    "succeeded": ["issue-id-1", "issue-id-3"],
    "failed": [{"id": "issue-id-2", "error": "Missing required field: fix_verification", "valid_transitions": [...]}],
    "warnings": [{"id": "issue-id-1", "warnings": ["Skipped recommended state: verifying"]}]
}
```

**Commit message:**
```
feat(core): batch operations respect per-type workflow templates

- batch_close validates each issue individually against its type template
- Hard failures on one issue don't prevent processing others
- Soft warnings collected per-issue and returned in aggregate
- Response format: succeeded/failed/warnings arrays

Implements: WFT-FR-073
```

**Definition of Done:**
- [ ] batch_close validates per-issue against type templates
- [ ] Hard failures isolated — other issues still processed
- [ ] Soft warnings collected and returned in aggregate
- [ ] Failed items include error detail and valid_transitions
- [ ] MCP response format matches spec
- [ ] make ci passes

---

### Task 3.14: Hard Enforcement Errors Include Valid Transitions (WFT-FR-075)

Update hard enforcement error responses to include the `valid_transitions` list for the issue's current state, plus a `hint` field directing agents to self-correct.

**Files:**
- Modify: `src/keel/mcp_server.py` — update error response format
- Test: `tests/test_mcp.py`

**Step 1: Write the failing test**

```python
def test_hard_enforcement_error_includes_transitions(db, mcp_client):
    """Hard enforcement errors include valid_transitions for self-correction."""
    bug = db.create_issue(title="Bug", type="bug")
    db.update_issue(bug.id, status="fixing")
    # Attempt to close without fix_verification (hard enforcement)
    result = await mcp_client.call_tool("update_issue", {"id": bug.id, "status": "closed"})
    data = json.loads(result)
    assert data["error"]
    assert "valid_transitions" in data
    assert "hint" in data
```

**Step 2: Implement, run tests, commit**

**Commit message:**
```
feat(mcp): hard enforcement errors include valid_transitions and hint

- Error responses embed valid_transitions for current state
- Hint field directs agent to self-correct
- Eliminates need for separate get_valid_transitions call after error

Implements: WFT-FR-075
```

**Definition of Done:**
- [ ] Hard enforcement errors include valid_transitions array
- [ ] Hint field present in error responses
- [ ] Agent can self-correct from error response alone
- [ ] make ci passes

---

## Phase 4: Dashboard & Summary

Phase 4 updates the summary generator and dashboard to display workflow-aware information.

**Requirements covered:** WFT-FR-060 through WFT-FR-065, WFT-FR-071, WFT-NFR-010, WFT-DR-013, WFT-SR-013

> **Review note W3 — Summary regeneration contract:**
> All new mutation paths (template reload, pack enable/disable, pack install, template overrides)
> must call `_refresh_summary()` after modifying state. See Implementation Notes for full list.

---

### Task 4.1: Summary — Category-Based Vitals
### Task 4.2: Summary — Per-Type State Display in Issue Lines
### Task 4.3: Summary — Length Optimization

### Task 4.4: Summary — "Needs Attention" Section (WFT-FR-071)

Add a "Needs Attention" section to the summary listing in-progress issues that have missing required fields for their most likely next transition.

**Files:**
- Modify: `src/keel/summary.py`
- Test: `tests/test_summary.py`

**Output format in context.md:**
```
## Needs Attention
- P1 proj-a3f9b2 [bug] "Login crash" (fixing) — missing: fix_verification (required for → verifying)
- P2 proj-c8d1e4 [risk] "Data breach" (assessing) — missing: risk_score, impact (required for → assessed)
```

**Why this matters:** This is the workflow-template equivalent of the existing "Blocked" section. Without it, agents must call `validate_issue` on every in-progress issue to discover field gaps — defeating the purpose of the pre-computed summary.

**Implementation:**
For each in-progress (wip-category) issue, call `TemplateRegistry.get_valid_transitions()`. If the most likely next transition (first in list) has missing required fields, include the issue in this section. Limit to 10 items to keep summary compact.

**Commit message:**
```
feat(summary): add "Needs Attention" section for missing required fields

- Lists wip-category issues missing fields for their next transition
- Shows issue ref, current state, missing fields, and target state
- Limited to 10 items for summary compactness
- Workflow-template equivalent of the existing "Blocked" section

Implements: WFT-FR-071
```

**Definition of Done:**
- [ ] Needs Attention section appears in context.md when applicable
- [ ] Shows missing field names and target transition
- [ ] Limited to 10 items
- [ ] Empty section omitted (no "Needs Attention" header when nothing to show)
- [ ] make ci passes

### Task 4.5: Dashboard — Category Column Default
### Task 4.6: Dashboard — Type-Filtered Kanban
### Task 4.7: Dashboard — Type Info API Endpoint

---

## Phase 5: Pack Management

Phase 5 adds the full pack install/enable/disable workflow and doctor checks.

**Requirements covered:** WFT-FR-020 through WFT-FR-023, WFT-FR-043, WFT-FR-059, WFT-FR-067, WFT-NFR-007, WFT-AR-006, WFT-AR-008, WFT-DR-009, WFT-SR-007 through WFT-SR-011, WFT-SR-014

> **Review fix B5 — Security constraints for custom templates:**
> Phase 5 allows user-supplied pack definitions (JSON files) and project-local template overrides.
> State names from these templates flow into SQL queries. All custom template loading MUST
> use the validation framework established in Task 1.3's `parse_type_template()`:
>
> - **State name validation**: `_NAME_PATTERN` regex (`^[a-z][a-z0-9_]{0,63}$`) enforced in `StateDefinition.__post_init__`
> - **Type name validation**: Same `_NAME_PATTERN` regex in `parse_type_template()`
> - **Size limits**: MAX_STATES=50, MAX_TRANSITIONS=200, MAX_FIELDS=50 per type
> - **Pack size limit**: Maximum 20 types per pack (prevents abuse)
> - **Template file size**: Maximum 512 KB per pack JSON file (prevents DoS)
> - **Field schema validation**: Only permitted field types (`text`, `number`, `date`, `select`, `url`)
> - **Pack cycle detection**: Task 5.2 must check for circular pack dependencies
> - **Reject unknown keys**: `parse_type_template()` should warn on unrecognized keys in custom templates
>
> Tests for all validation paths are required in Tasks 5.1 and 5.4.

---

### Task 5.1: Pack Install from File (CLI + MCP)

> Security: Must validate all type definitions via `parse_type_template()` before persisting.
> Must enforce file size limit (512 KB). Must reject packs exceeding 20 types.
> Must run all validations **before** any database writes (fail-fast).
> Required tests: malformed JSON, oversized file, invalid state names, too many types.

### Task 5.2: Pack Enable/Disable with Dependency Validation

> Must check for circular pack dependencies (A requires B, B requires A).
> Use topological sort or simple DFS cycle detection on pack dependency graph.

### Task 5.3: Pack Disable Safety Check
### Task 5.4: Project-Local Template Overrides

> Security: Project-local overrides go through the same `parse_type_template()` validation
> as built-in types. Override scope is limited to state additions/transitions — cannot
> change the category mapping of built-in states. Must not allow overriding core pack types
> with conflicting state categories.
> Required tests: override with invalid state names, override exceeding size limits,
> override attempting category change on built-in state.

### Task 5.5: JSONL Export/Import Extensions
### Task 5.6: Doctor Pack Checks
### Task 5.7: Usage Metrics and Monitoring

---

## Implementation Notes

### Key file locations and line numbers (current codebase)

| File | Key Location | Line |
|------|-------------|------|
| `src/keel/core.py` | SCHEMA_SQL | 64 |
| `src/keel/core.py` | CURRENT_SCHEMA_VERSION | 136 |
| `src/keel/core.py` | MIGRATIONS list | 255-259 |
| `src/keel/core.py` | BUILT_IN_TEMPLATES | 265-385 |
| `src/keel/core.py` | Issue dataclass | 386-427 |
| `src/keel/core.py` | KeelDB.__init__ | 457-467 |
| `src/keel/core.py` | KeelDB.initialize | 501-506 |
| `src/keel/core.py` | _seed_templates | 532-537 |
| `src/keel/core.py` | _validate_status | 567-571 |
| `src/keel/core.py` | create_issue | 579-624 |
| `src/keel/core.py` | _build_issues_batch | 641-722 |
| `src/keel/core.py` | update_issue | 724-792 |
| `src/keel/core.py` | close_issue | 794-803 |
| `src/keel/core.py` | claim_issue | 805-825 |
| `src/keel/core.py` | release_claim | 827-845 |
| `src/keel/core.py` | list_issues | 885-922 |
| `src/keel/core.py` | get_ready | 1010-1022 |
| `src/keel/core.py` | get_blocked | 1024-1033 |
| `src/keel/core.py` | get_critical_path | 1037-1089 |
| `src/keel/summary.py` | generate_summary | 26 |
| `src/keel/mcp_server.py` | _refresh_summary | 60 |
| `src/keel/cli.py` | _refresh_summary | 58 |

### Summary regeneration contract (W3)

Every KeelDB mutation method calls `_refresh_summary()` to keep `context.md` in sync. New template
mutation paths introduced by this plan **must** also call `_refresh_summary()`:
- `reload_templates()` (Task 3.10) — call after cache clear
- Pack enable/disable (Task 5.2) — call after config write
- Pack install (Task 5.1) — call after pack persisted
- Template override (Task 5.4) — call after override saved

Consider adding an `_after_mutation()` hook that centralizes the refresh call, but at minimum
document the requirement so implementors don't forget.

### Ruff/mypy configuration notes
- `src/keel/core.py`: `# noqa: S608` is used for parameterized SQL (not injection)
- `src/keel/mcp_server.py`: `# noqa: E501` for long MCP tool schemas
- `src/keel/templates_data.py` will need `# noqa: E501` for long pack definition lines
- mypy strict mode — all new code needs full type annotations

### Testing commands
- Full CI: `make ci`
- Lint only: `make lint`
- Type check only: `make typecheck`
- Test only: `make test`
- Specific test: `uv run pytest tests/test_templates.py -v`
- Single test: `uv run pytest tests/test_templates.py::TestTransitionValidation::test_hard_transition_with_missing_fields -v`
- Coverage: `uv run pytest --cov=keel --cov-report=term-missing`
