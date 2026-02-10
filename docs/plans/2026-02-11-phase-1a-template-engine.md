# Phase 1A: Template Engine (Standalone)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create the template engine as a standalone module -- no core.py changes, no schema migration, no behavior changes. Pure additive code.

**PR Strategy:** Single PR. Zero risk of regressions since nothing in the existing codebase imports or uses the new modules.

**Prerequisites:**
- `uv sync --group dev` for dev environment
- `make ci` passes clean on current main (commit 2b3ad2f)

**Parent plan:** `2026-02-11-workflow-templates-implementation.md`

**Design doc:** `2026-02-11-workflow-templates-design.md`

**Requirements doc:** `2026-02-11-workflow-templates-requirements.md`

---

## Task Summary

| Task | What | New files | Lines (est) |
|------|------|-----------|-------------|
| 1.1 | Frozen dataclasses, type aliases, name validation | `src/keel/templates.py`, `tests/test_templates.py` | ~120 impl, ~80 test |
| 1.2 | Custom exception types | (modify above) | ~40 impl, ~30 test |
| 1.3 | TemplateRegistry -- parsing, caching, queries | (modify above) | ~180 impl, ~120 test |
| 1.4 | TemplateRegistry -- transition validation | (modify above) | ~120 impl, ~150 test |
| 1.5 | Built-in pack data (core + planning, 9 types) | `src/keel/templates_data.py` | ~500 data, ~100 test |

---

## Task 1.1: Dataclass Definitions and Type Aliases

Create the frozen dataclasses and type aliases that every other module will import. This is the foundation -- no logic, just data structures.

**Files:**
- Create: `src/keel/templates.py`
- Create: `tests/test_templates.py`

**Requirements covered:** WFT-NFR-014 (frozen dataclasses), WFT-NFR-015 (type aliases), WFT-FR-013 (StateDefinition), WFT-FR-014 (TransitionDefinition), WFT-FR-015 (FieldSchema)

### Step 1: Write the failing test

Create `tests/test_templates.py`:

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
```

**Why this test:** Verifies all 9 dataclasses exist, are frozen, and have correct field defaults. Catches import errors and structural issues immediately. Tests name validation edge cases (valid and invalid).

### Step 2: Run test to verify it fails

```bash
uv run pytest tests/test_templates.py::TestDataclasses -v
```

Expected: `ModuleNotFoundError: No module named 'keel.templates'`

### Step 3: Write minimal implementation

Create `src/keel/templates.py`:

```python
# src/keel/templates.py
"""Workflow template system -- loading, caching, and validation.

Provides TemplateRegistry for managing per-type state machines, transition
enforcement, and field validation. Type templates define states, transitions,
and field schemas. Workflow packs bundle related types.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Logging (review B4)
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# State/type names must match this pattern to be safe for use in SQL queries
# and filesystem paths. Validated at parse time (review B1, B5).
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

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
# This differs intentionally from the mutable Issue dataclass (domain entity
# in core.py). Frozen dataclasses prevent accidental mutation after loading
# and enable safe caching in dict lookups.
# ---------------------------------------------------------------------------


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
- Frozen dataclasses are intentional for template config -- unlike the mutable `Issue` dataclass in `core.py:386`, templates are configuration data that must not be mutated after loading
- Tuples instead of lists for immutability in frozen dataclasses
- `StateDefinition.__post_init__` validates name format against `^[a-z][a-z0-9_]{0,63}$` -- prevents SQL injection via state names in category-aware queries (review B1)
- `WorkflowPack.types` is a dict (not frozen) -- packs need mutable type mappings during loading but the pack itself should not be reassigned
- Logging infrastructure (`logger`, `_NAME_PATTERN`) established early for use by all subsequent tasks

### Step 4: Run test to verify it passes

```bash
uv run pytest tests/test_templates.py::TestDataclasses -v
```

Expected: All 10 tests pass.

### Step 5: Run full CI

```bash
make ci
```

Expected: All existing tests still pass. Ruff and mypy clean. No changes to existing files.

### Step 6: Commit

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

### Definition of Done
- [ ] All 9 dataclasses importable from `keel.templates`
- [ ] All dataclasses are frozen (assignment raises `AttributeError`)
- [ ] Type aliases defined for StateCategory, EnforcementLevel, FieldType
- [ ] StateDefinition rejects invalid names (uppercase, dashes, spaces, empty, >64 chars)
- [ ] StateDefinition accepts valid names (lowercase, underscores, 1-64 chars)
- [ ] `import logging` and `logger` present in `templates.py`
- [ ] `_NAME_PATTERN` regex present and used
- [ ] `make ci` passes clean

---

## Task 1.2: Custom Exception Types

Add the enforcement-specific exceptions that `validate_transition()` will raise. Both subclass `ValueError` for backward compatibility with existing error handling patterns in core.py.

**Files:**
- Modify: `src/keel/templates.py`
- Modify: `tests/test_templates.py`

**Requirements covered:** WFT-AR-010 (structured exceptions), WFT-DR-008 (agent-friendly errors), WFT-SR-006 (remediation guidance)

### Step 1: Write the failing test

Add to `tests/test_templates.py`:

```python
from keel.templates import HardEnforcementError, TransitionNotAllowedError


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
```

### Step 2: Run test to verify it fails

```bash
uv run pytest tests/test_templates.py::TestExceptions -v
```

Expected: `ImportError: cannot import name 'TransitionNotAllowedError' from 'keel.templates'`

### Step 3: Write minimal implementation

Add to `src/keel/templates.py` after the dataclass definitions (before the end of file):

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

### Step 4: Run test to verify it passes

```bash
uv run pytest tests/test_templates.py::TestExceptions -v
```

Expected: All 5 tests pass.

### Step 5: Run full CI

```bash
make ci
```

Expected: All tests pass. Ruff and mypy clean.

### Step 6: Commit

```bash
git add src/keel/templates.py tests/test_templates.py
git commit -m "feat(templates): add TransitionNotAllowedError and HardEnforcementError

- Both subclass ValueError for backward compatibility
- Include remediation guidance in error messages (WFT-SR-006)
- Store from_state, to_state, type_name, missing_fields as attributes
- Agent-friendly: messages suggest get_valid_transitions() and get_type_info()

Implements: WFT-AR-010, WFT-DR-008, WFT-SR-006

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Definition of Done
- [ ] Both exceptions importable from `keel.templates`
- [ ] Both are `ValueError` subclasses
- [ ] Error messages include remediation hints (tool names to call)
- [ ] Structured attributes (from_state, to_state, type_name, missing_fields)
- [ ] `make ci` passes clean

---

## Task 1.3: TemplateRegistry -- Template Parsing and Caching

Implement the core `TemplateRegistry` class with: `parse_type_template()`, `validate_type_template()`, `_register_type()`, `_register_pack()`, `get_type()`, `get_pack()`, `list_types()`, `list_packs()`, `get_initial_state()`, `get_category()`, `get_valid_states()`, `get_first_state_of_category()`.

**Files:**
- Modify: `src/keel/templates.py`
- Modify: `tests/test_templates.py`

**Requirements covered:** WFT-FR-001 (registry), WFT-FR-007 (initial state fallback), WFT-FR-010 (first state of category), WFT-NFR-002 (O(1) lookup), WFT-NFR-003 (cache), WFT-NFR-008 (validation), WFT-SR-001 (size limits), WFT-SR-002 (category cache), WFT-SR-003 (transition cache)

### Step 1: Write the failing tests

Add to `tests/test_templates.py`:

```python
from keel.templates import TemplateRegistry


class TestTemplateRegistry:
    """Test TemplateRegistry with manually registered templates."""

    @pytest.fixture()
    def registry(self) -> TemplateRegistry:
        """A registry pre-loaded with a minimal core pack."""
        reg = TemplateRegistry()
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
        assert hasattr(registry, "_category_cache")
        assert ("bug", "triage") in registry._category_cache

    def test_get_category_unknown_state(self, registry: TemplateRegistry) -> None:
        """Unknown state for known type returns None."""
        assert registry.get_category("bug", "nonexistent") is None

    def test_get_category_unknown_type(self, registry: TemplateRegistry) -> None:
        """Unknown type returns None."""
        assert registry.get_category("unknown", "open") is None

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
            type="bad", display_name="Bad", description="Bad", pack="test",
            states=(StateDefinition("open", "open"),),
            initial_state="nonexistent",
            transitions=(),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("initial_state" in e for e in errors)

    def test_validate_type_template_invalid_transition_ref(self) -> None:
        tpl = TypeTemplate(
            type="bad", display_name="Bad", description="Bad", pack="test",
            states=(StateDefinition("open", "open"), StateDefinition("closed", "done")),
            initial_state="open",
            transitions=(TransitionDefinition("open", "nonexistent", "soft"),),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("nonexistent" in e for e in errors)

    def test_validate_type_template_invalid_required_at_ref(self) -> None:
        tpl = TypeTemplate(
            type="bad", display_name="Bad", description="Bad", pack="test",
            states=(StateDefinition("open", "open"),),
            initial_state="open",
            transitions=(),
            fields_schema=(FieldSchema("f1", "text", required_at=("nonexistent",)),),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("required_at" in e and "nonexistent" in e for e in errors)

    def test_validate_type_template_invalid_requires_fields_ref(self) -> None:
        tpl = TypeTemplate(
            type="bad", display_name="Bad", description="Bad", pack="test",
            states=(StateDefinition("open", "open"), StateDefinition("closed", "done")),
            initial_state="open",
            transitions=(TransitionDefinition("open", "closed", "soft", requires_fields=("ghost_field",)),),
            fields_schema=(),
        )
        errors = TemplateRegistry.validate_type_template(tpl)
        assert any("ghost_field" in e for e in errors)
```

### Step 2: Run test to verify it fails

```bash
uv run pytest tests/test_templates.py::TestTemplateRegistry -v
```

Expected: `ImportError: cannot import name 'TemplateRegistry' from 'keel.templates'`

### Step 3: Write implementation

Add to `src/keel/templates.py` after the exceptions (before end of file):

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

    # Size limits for custom templates (review B5 -- prevent DoS via huge templates)
    MAX_STATES = 50
    MAX_TRANSITIONS = 200
    MAX_FIELDS = 50

    def __init__(self) -> None:
        self._types: dict[str, TypeTemplate] = {}
        self._packs: dict[str, WorkflowPack] = {}
        self._category_cache: dict[tuple[str, str], StateCategory] = {}
        self._transition_cache: dict[str, dict[tuple[str, str], TransitionDefinition]] = {}
        self._loaded = False

    # -- Parsing (from dict/JSON) -------------------------------------------

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

        # Size limit checks (review B5 -- prevent DoS via huge templates)
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
                    errors.append(
                        f"transition {t.from_state}->{t.to_state} requires_fields '{rf}' not in fields_schema"
                    )

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

        # Build category cache -- O(1) lookup (WFT-SR-002)
        for state in tpl.states:
            self._category_cache[(tpl.type, state.name)] = state.category

        # Build transition cache -- O(1) lookup (WFT-SR-003)
        self._transition_cache[tpl.type] = {
            (t.from_state, t.to_state): t for t in tpl.transitions
        }

    def _register_pack(self, pack: WorkflowPack) -> None:
        """Register a workflow pack."""
        logger.debug("Registering pack: %s (version=%s, %d types)", pack.pack, pack.version, len(pack.types))
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
            logger.warning("Unknown type '%s' -- falling back to initial state 'open'", type_name)
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

### Step 4: Run test to verify it passes

```bash
uv run pytest tests/test_templates.py::TestTemplateRegistry -v
```

Expected: All 20 tests pass.

### Step 5: Run full CI

```bash
make ci
```

Expected: All tests pass. Ruff and mypy clean.

### Step 6: Commit

```bash
git add src/keel/templates.py tests/test_templates.py
git commit -m "feat(templates): TemplateRegistry with parsing, caching, validation, and logging

- parse_type_template() converts JSON dicts to frozen TypeTemplate
- parse_type_template() validates type name format (^[a-z][a-z0-9_]{0,63}$)
- parse_type_template() enforces size limits (50 states, 200 transitions, 50 fields)
- validate_type_template() checks internal consistency (dangling refs)
- O(1) category cache via _category_cache dict
- O(1) transition lookup via _transition_cache dict
- get_initial_state() with 'open' fallback for unknown types (logs warning)
- get_first_state_of_category() uses array order per WFT-FR-010
- Logging: DEBUG for parsing/registration, WARNING for fallbacks

Implements: WFT-FR-001, WFT-FR-007, WFT-FR-010, WFT-NFR-002, WFT-NFR-003,
WFT-NFR-008, WFT-SR-001, WFT-SR-002, WFT-SR-003

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Definition of Done
- [ ] TemplateRegistry class exists with all query methods
- [ ] parse_type_template() converts raw dicts to TypeTemplate
- [ ] parse_type_template() rejects invalid type names and oversized templates
- [ ] validate_type_template() catches dangling state/field/transition references
- [ ] O(1) category and transition caches built on registration
- [ ] Logging at DEBUG (parse/register) and WARNING (fallback) levels
- [ ] get_initial_state() falls back to "open" for unknown types
- [ ] get_first_state_of_category() returns first by array order
- [ ] `make ci` passes clean

---

## Task 1.4: TemplateRegistry -- Transition Validation

Implement `validate_transition()`, `get_valid_transitions()`, and `validate_fields_for_state()`. This is where soft/hard enforcement logic lives.

**Files:**
- Modify: `src/keel/templates.py`
- Modify: `tests/test_templates.py`

**Requirements covered:** WFT-FR-011 (transition validation), WFT-FR-012 (field population check), WFT-FR-014 (valid transitions list), WFT-FR-016 (unknown type fallback), WFT-NFR-009 (enforcement levels)

### Step 1: Write the failing tests

Add to `tests/test_templates.py`:

```python
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
                FieldSchema("severity", "enum", options=("critical", "major", "minor", "cosmetic"),
                            required_at=("confirmed",)),
                FieldSchema("fix_verification", "text", required_at=("verifying",)),
            ),
        )
        reg._register_type(bug_tpl)
        return reg

    # -- validate_transition tests --

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
        assert "not in the standard workflow" in result.warnings[0]

    def test_empty_string_treated_as_missing(self, registry: TemplateRegistry) -> None:
        """Empty string should be treated as unpopulated (WFT-FR-012)."""
        result = registry.validate_transition(
            "bug", "verifying", "closed", {"fix_verification": ""}
        )
        assert result.allowed is False
        assert "fix_verification" in result.missing_fields

    def test_whitespace_only_treated_as_missing(self, registry: TemplateRegistry) -> None:
        """Whitespace-only string should be treated as unpopulated."""
        result = registry.validate_transition(
            "bug", "verifying", "closed", {"fix_verification": "   "}
        )
        assert result.allowed is False

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
        options = registry.get_valid_transitions(
            "bug", "fixing", {"fix_verification": "Tests pass"}
        )
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
```

### Step 2: Run test to verify it fails

```bash
uv run pytest tests/test_templates.py::TestTransitionValidation -v
```

Expected: `AttributeError: 'TemplateRegistry' object has no attribute 'validate_transition'`

### Step 3: Write implementation

Add these methods to the `TemplateRegistry` class in `src/keel/templates.py`:

```python
    # -- Validation ---------------------------------------------------------

    @staticmethod
    def _is_field_populated(value: Any) -> bool:
        """Check if a field value is considered populated (WFT-FR-012).

        None, empty strings, and whitespace-only strings are unpopulated.
        """
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
            # Transition not in table: allowed with soft-warn (WFT-FR-011)
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
            # ready = True if no hard-enforcement missing fields
            # For soft enforcement, missing fields don't block readiness
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

### Step 4: Run test to verify it passes

```bash
uv run pytest tests/test_templates.py::TestTransitionValidation -v
```

Expected: All 18 tests pass.

### Step 5: Run full CI

```bash
make ci
```

Expected: All tests pass. Ruff and mypy clean.

### Step 6: Commit

```bash
git add src/keel/templates.py tests/test_templates.py
git commit -m "feat(templates): transition validation with soft/hard enforcement

- validate_transition() checks enforcement level and required fields
- Hard enforcement rejects on missing fields; soft warns
- Undefined transitions treated as soft-warn by default (WFT-FR-011)
- Empty strings, whitespace, and None treated as unpopulated (WFT-FR-012)
- get_valid_transitions() returns per-option readiness status
- validate_fields_for_state() checks required_at declarations
- Unknown types: all transitions allowed (WFT-FR-016)

Implements: WFT-FR-011, WFT-FR-012, WFT-FR-014, WFT-FR-016, WFT-NFR-009

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Definition of Done
- [ ] validate_transition() handles soft, hard, and undefined transitions
- [ ] Hard enforcement blocks when required fields are missing
- [ ] Soft enforcement allows with warnings when fields are missing
- [ ] Undefined transitions allowed with advisory warning
- [ ] get_valid_transitions() returns per-option readiness
- [ ] validate_fields_for_state() checks required_at
- [ ] Empty strings and whitespace treated as unpopulated
- [ ] Unknown types get fallback behavior (all transitions allowed)
- [ ] `make ci` passes clean

---

## Task 1.5: Built-in Pack Data -- Core and Planning Packs

Create `src/keel/templates_data.py` with complete data definitions for the `core` pack (task, bug, feature, epic) and `planning` pack (milestone, phase, step, work_package, deliverable). All 9 types have full state machines, transitions, field schemas, and workflow guides.

**Files:**
- Create: `src/keel/templates_data.py`
- Modify: `tests/test_templates.py`

**Requirements covered:** WFT-NFR-013 (built-in data), WFT-FR-008 (pack definitions), WFT-FR-031 (workflow guides with state_diagram, word limits), WFT-AR-003 (core pack), WFT-AR-004 (planning pack)

### Step 1: Write the failing tests

Add to `tests/test_templates.py`:

```python
from keel.templates_data import BUILT_IN_PACKS


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

    @pytest.mark.parametrize("pack_name", ["core", "planning"])
    def test_guide_has_required_fields(self, pack_name: str) -> None:
        guide = BUILT_IN_PACKS[pack_name]["guide"]
        assert "state_diagram" in guide
        assert "overview" in guide
        assert "when_to_use" in guide
        assert "tips" in guide
        assert "common_mistakes" in guide

    @pytest.mark.parametrize("pack_name", ["core", "planning"])
    def test_guide_overview_under_50_words(self, pack_name: str) -> None:
        overview = BUILT_IN_PACKS[pack_name]["guide"]["overview"]
        word_count = len(overview.split())
        assert word_count <= 50, f"{pack_name} overview is {word_count} words (max 50)"

    @pytest.mark.parametrize("pack_name", ["core", "planning"])
    def test_guide_when_to_use_under_30_words(self, pack_name: str) -> None:
        when = BUILT_IN_PACKS[pack_name]["guide"]["when_to_use"]
        word_count = len(when.split())
        assert word_count <= 30, f"{pack_name} when_to_use is {word_count} words (max 30)"

    @pytest.mark.parametrize("pack_name", ["core", "planning"])
    def test_guide_tips_is_list(self, pack_name: str) -> None:
        tips = BUILT_IN_PACKS[pack_name]["guide"]["tips"]
        assert isinstance(tips, list)
        assert len(tips) >= 3

    @pytest.mark.parametrize("pack_name", ["core", "planning"])
    def test_guide_common_mistakes_is_list(self, pack_name: str) -> None:
        mistakes = BUILT_IN_PACKS[pack_name]["guide"]["common_mistakes"]
        assert isinstance(mistakes, list)
        assert len(mistakes) >= 2

    @pytest.mark.parametrize("pack_name", ["core", "planning"])
    def test_guide_state_diagram_is_string(self, pack_name: str) -> None:
        diagram = BUILT_IN_PACKS[pack_name]["guide"]["state_diagram"]
        assert isinstance(diagram, str)
        assert len(diagram) > 20  # Not empty/trivial

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

    @pytest.mark.parametrize("pack_name,type_name", [
        ("core", "task"), ("core", "bug"), ("core", "feature"), ("core", "epic"),
        ("planning", "milestone"), ("planning", "phase"), ("planning", "step"),
        ("planning", "work_package"), ("planning", "deliverable"),
    ])
    def test_every_type_has_states_transitions_fields(self, pack_name: str, type_name: str) -> None:
        raw = BUILT_IN_PACKS[pack_name]["types"][type_name]
        assert "states" in raw and len(raw["states"]) >= 2
        assert "transitions" in raw and len(raw["transitions"]) >= 1
        assert "fields_schema" in raw
        assert "initial_state" in raw
```

### Step 2: Run test to verify it fails

```bash
uv run pytest tests/test_templates.py::TestBuiltInPackData -v
```

Expected: `ModuleNotFoundError: No module named 'keel.templates_data'`

### Step 3: Write implementation

Create `src/keel/templates_data.py`:

```python
# src/keel/templates_data.py
"""Built-in workflow pack definitions.

This module contains the data definitions for all built-in packs (WFT-NFR-013).
Logic lives in templates.py; this file is pure data.

Each pack is a JSON-compatible dict matching the pack schema (design Section 4.2).
Types within packs match the type template schema (design Section 3.2).

Pack tiers:
  - Tier 1 (this file, complete): core (4 types), planning (5 types)
  - Tier 2+ (stubs, filled in later phases): risk, spike, requirements, roadmap,
    incident, debt, release
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Core Pack -- Foundational software development types
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
        "bug": {  # noqa: E501
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
                {"name": "scope", "type": "text", "description": "What is in and out of scope"},
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
        "state_diagram": (
            "task:    open(O) --> in_progress(W) --> closed(D)\n"
            "                 \\-> closed(D)\n"
            "\n"
            "bug:     triage(O) --> confirmed(O) --> fixing(W) --> verifying(W) --> closed(D)\n"
            "                  \\-> wont_fix(D)                  \\-> fixing(W)  [loop]\n"
            "         HARD: verifying-->closed requires fix_verification\n"
            "\n"
            "feature: proposed(O) --> approved(O) --> building(W) --> reviewing(W) --> done(D)\n"
            "                    \\-> deferred(D)                  \\-> building(W) [loop]\n"
            "\n"
            "epic:    open(O) --> in_progress(W) --> closed(D)"
        ),
        "overview": "Core software development types for everyday work. Tasks for general work, bugs for defects, features for new functionality, and epics for large initiatives.",
        "when_to_use": "Always enabled. Bread-and-butter types for any software project.",
        "tips": [
            "Use tasks for small, well-defined work items that one agent can complete in a session",
            "Bugs should always have steps_to_reproduce when possible -- without them, fixing is guesswork",
            "Features need acceptance_criteria before approval -- otherwise 'done' is ambiguous",
            "Use epics to group related features and tasks under a single objective",
            "Set severity on bugs during triage, not later -- it drives priority ordering",
        ],
        "common_mistakes": [
            "Skipping triage on bugs -- always assess severity first, even for obvious fixes",
            "Closing bugs without fix_verification -- the verifying->closed transition requires it for good reason",
            "Approving features without acceptance_criteria -- you need a definition of done before building",
            "Creating tasks when you mean steps -- tasks are standalone; steps belong to a phase in a plan",
        ],
    },
}

# ---------------------------------------------------------------------------
# Planning Pack -- PMBOK-lite project planning
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
                {"name": "scope_summary", "type": "text", "description": "What is in and out of scope"},
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
                {"name": "target_files", "type": "list", "description": "Files to create or modify"},
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
            "description": "Concrete output produced by a work package or phase",
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
                {"name": "format", "type": "text", "description": "Expected format (document, code, artifact, etc.)"},
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
        "state_diagram": (
            "milestone: planning(O) --> active(W) --> closing(W) --> completed(D)\n"
            "\n"
            "phase:     pending(O) --> active(W) --> completed(D)\n"
            "                     \\-> skipped(D)\n"
            "\n"
            "step:      pending(O) --> in_progress(W) --> completed(D)\n"
            "                     \\-> skipped(D)\n"
            "\n"
            "work_package: defined(O) --> assigned(O) --> executing(W) --> delivered(D)\n"
            "\n"
            "deliverable:  planned(O) --> producing(W) --> reviewing(W) --> accepted(D)\n"
            "                                           \\-> producing(W) [rework loop]"
        ),
        "overview": "PMBOK-lite project planning. Structure work as milestones containing phases containing steps. Work packages for assignable bundles, deliverables for concrete outputs.",
        "when_to_use": "Structured multi-phase projects needing clear hierarchy and progress tracking.",
        "tips": [
            "Start with a milestone, then break into phases, then into steps -- top-down decomposition",
            "Use phase dependencies to enforce ordering -- phase 2 should depend on phase 1",
            "Steps should be small enough to complete in a single session -- if it takes multiple days, it is a phase",
            "Work packages are useful for delegating to different agents or teams with clear acceptance criteria",
            "Set sequence fields on phases and steps to maintain intended execution order",
            "Use deliverables to track concrete outputs -- code, documents, test reports, artifacts",
        ],
        "common_mistakes": [
            "Creating steps without phases -- you lose the grouping benefit and cannot track phase-level progress",
            "Skipping entry/exit criteria on phases -- without them, you have no clear transition points",
            "Making steps too large -- each step should be an atomic, completable unit of work",
            "Forgetting to set sequence numbers -- without ordering, agents will not know which step comes next",
        ],
    },
}

# ---------------------------------------------------------------------------
# Stub packs (Phase 2+ will add full definitions)
# ---------------------------------------------------------------------------
# These are stubs so the pack names exist in the registry.
# Full type definitions, guides, and relationships are added in later phases.

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

**Why these choices:**
- State machines match design doc Section 10.2 exactly (task: 3 states, bug: 6 states, feature: 6 states, epic: 3 states, milestone: 4, phase: 4, step: 4, work_package: 4, deliverable: 4)
- Task and epic use `open/in_progress/closed` for backward compatibility with existing issues
- Bug has the only hard enforcement in core pack: `verifying->closed` requires `fix_verification`
- Planning types use skipped states for scope reduction (phase, step)
- Deliverable supports a review loop (`reviewing->producing`) for rework cycles
- Workflow guides include `state_diagram` with compact ASCII notation showing categories (O/W/D), loops, and hard enforcement
- `overview` stays under 50 words, `when_to_use` under 30 words per WFT-FR-031
- Tips and common_mistakes are practical, agent-actionable guidance -- not generic filler
- Stub packs have `types: {}` and `guide: None` -- they exist for name resolution but carry no type definitions until later phases

### Step 4: Run test to verify it passes

```bash
uv run pytest tests/test_templates.py::TestBuiltInPackData -v
```

Expected: All tests pass (approximately 35+ tests).

### Step 5: Run full CI

```bash
make ci
```

Expected: All tests pass. Ruff and mypy clean. No changes to existing files.

**Ruff note:** The `templates_data.py` file may need `# noqa: E501` on long pack definition lines. If ruff reports line-length violations on the inline dict lines (like the bug fields_schema), add per-file ignore in `pyproject.toml`:

```toml
"src/keel/templates_data.py" = ["E501"]  # Pack definitions have long inline dict lines
```

Add this to the `[tool.ruff.lint.per-file-ignores]` section. This is the standard pattern already used for `mcp_server.py` and `dashboard.py`.

### Step 6: Commit

```bash
git add src/keel/templates_data.py tests/test_templates.py
# If pyproject.toml was modified for E501:
git add pyproject.toml
git commit -m "feat(templates): built-in pack data -- core and planning packs with guides

Core pack (4 types):
- task: open/in_progress/closed (backward compatible)
- bug: triage/confirmed/fixing/verifying/closed/wont_fix (hard: verifying->closed)
- feature: proposed/approved/building/reviewing/done/deferred
- epic: open/in_progress/closed (backward compatible)

Planning pack (5 types):
- milestone: planning/active/closing/completed
- phase: pending/active/completed/skipped
- step: pending/in_progress/completed/skipped
- work_package: defined/assigned/executing/delivered
- deliverable: planned/producing/reviewing/accepted (review loop)

Both packs include workflow guides with:
- Compact ASCII state diagrams
- Overview (<50 words) and when_to_use (<30 words) per WFT-FR-031
- Practical tips and common_mistakes for agent guidance

7 remaining packs as stubs (types added in later phases)

Implements: WFT-NFR-013, WFT-FR-008, WFT-FR-031, WFT-AR-003, WFT-AR-004

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Definition of Done
- [ ] `BUILT_IN_PACKS` dict with 9 packs (2 complete, 7 stubs)
- [ ] Core pack: 4 types with full state machines, transitions, fields
- [ ] Planning pack: 5 types with full state machines, transitions, fields
- [ ] All 9 types pass `parse_type_template()` and `validate_type_template()`
- [ ] Task and epic use `open/in_progress/closed` for backward compatibility
- [ ] Bug `verifying->closed` is hard-enforced requiring `fix_verification`
- [ ] Both packs have workflow guides with `state_diagram`, `overview`, `when_to_use`, `tips`, `common_mistakes`
- [ ] Guide `overview` under 50 words, `when_to_use` under 30 words
- [ ] Tips and common_mistakes are practical, agent-actionable (not placeholder text)
- [ ] Planning pack's `requires_packs` includes `"core"`
- [ ] Planning pack has relationships (milestone->phase, phase->step, etc.)
- [ ] Deliverable supports reviewing->producing rework loop
- [ ] Phase and step support skipped state
- [ ] `make ci` passes clean
- [ ] Coverage >= 90% on new code

---

## Definition of Done (Phase 1A -- All Tasks)

- [ ] `src/keel/templates.py` exists with:
  - [ ] 9 frozen dataclasses (StateDefinition, TransitionDefinition, FieldSchema, TypeTemplate, WorkflowPack, TransitionResult, TransitionOption, ValidationResult)
  - [ ] 3 type aliases (StateCategory, EnforcementLevel, FieldType)
  - [ ] 2 exception classes (TransitionNotAllowedError, HardEnforcementError)
  - [ ] TemplateRegistry class with parsing, caching, validation, and query methods
  - [ ] StateDefinition name validation via `_NAME_PATTERN` regex
  - [ ] O(1) category cache and transition cache
  - [ ] Soft/hard enforcement logic in validate_transition()
  - [ ] Logging at DEBUG/WARNING/ERROR levels
- [ ] `src/keel/templates_data.py` exists with:
  - [ ] Core pack: task, bug, feature, epic (4 types, complete)
  - [ ] Planning pack: milestone, phase, step, work_package, deliverable (5 types, complete)
  - [ ] 7 stub packs (requirements, risk, roadmap, incident, debt, spike, release)
  - [ ] Workflow guides with state_diagram, overview, when_to_use, tips, common_mistakes
- [ ] `tests/test_templates.py` exists with comprehensive tests:
  - [ ] TestDataclasses -- frozen behavior, defaults, name validation
  - [ ] TestExceptions -- structured data, remediation hints
  - [ ] TestTemplateRegistry -- parsing, caching, queries, validation
  - [ ] TestTransitionValidation -- soft/hard enforcement, readiness, field checks
  - [ ] TestBuiltInPackData -- structural validation, state machines, guides
- [ ] Nothing in core.py, cli.py, mcp_server.py, or summary.py is modified
- [ ] `make ci` passes clean (ruff + mypy strict + pytest)
- [ ] Coverage >= 90% on new code (`uv run pytest --cov=keel --cov-report=term-missing`)

---

## Appendix A: Complete File Listing After Phase 1A

```
src/keel/templates.py       # ~350 lines (dataclasses + registry + validation)
src/keel/templates_data.py  # ~400 lines (pack definitions)
tests/test_templates.py     # ~400 lines (comprehensive tests)
```

No other files are created or modified. The template engine is fully standalone and ready for integration in Phase 1B.

## Appendix B: pyproject.toml Changes

If ruff reports E501 on `templates_data.py`, add to `[tool.ruff.lint.per-file-ignores]`:

```toml
"src/keel/templates_data.py" = ["E501"]  # Pack definitions have long inline dict lines
```

This is the only change to existing configuration files.
