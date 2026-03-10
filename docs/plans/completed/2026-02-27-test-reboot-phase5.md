# Test Suite Reboot Phase 5: Templates & Migrations

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move template and migration test files into their dedicated directories, consolidate validation-fix tests, convert inline fixtures to `make_db`.

**Architecture:** Three template test files (`test_registry.py`, `test_transitions.py`, `test_db_integration.py`) replace four scattered source files. One migration test file moves from root to `tests/migrations/`. Fixtures convert to `make_db` factory pattern used by Phases 0-4.

**Tech Stack:** pytest, filigree.templates, filigree.core, tests._db_factory.make_db

---

## Source → Destination Map

### Template files (Work Unit A)

| Source file | Classes | Destination |
|-------------|---------|-------------|
| `tests/test_templates.py` | TestDataclasses, TestExceptions, TestTemplateRegistry, TestBuiltInPackData, TestTemplateLoading, TestQualityCheckDoneOutgoing | `tests/templates/test_registry.py` |
| `tests/test_templates.py` | TestTransitionValidation | `tests/templates/test_transitions.py` |
| `tests/test_template_validation_fixes.py` | TestStateCategoryValidation, TestDuplicateStateNameDetection, TestEnabledPacksValidation, TestParseTemplateMalformedTransitionsFields, TestFieldSchemaTypeValidation, TestDuplicateTransitionDetection, TestEnforcementNoneRejected, TestRolledBackCategoryFix | `tests/templates/test_registry.py` |
| `tests/test_template_validation_fixes.py` | TestIncidentResolvedCategory | `tests/templates/test_transitions.py` |
| `tests/test_filigreedb_templates.py` | TestFiligreeDBTemplatesProperty | `tests/templates/test_db_integration.py` |

### Migration files (Work Unit B)

| Source file | Destination |
|-------------|-------------|
| `tests/test_migrate.py` | `tests/migrations/test_migrate.py` |

### Shared fixtures

- `beads_db` stays in root `tests/conftest.py` — shared by both `test_migrate.py` and `test_peripheral_fixes.py`
- `incident_db` added to `tests/templates/conftest.py` using `make_db`

---

## Task 1: Update `tests/templates/conftest.py` with shared fixtures

**Files:**
- Modify: `tests/templates/conftest.py`

**Step 1: Add incident_db fixture**

The `TestIncidentResolvedCategory` class (moving to `test_transitions.py`) needs an `incident_db` fixture. Add it to the templates conftest using the `make_db` factory pattern.

```python
"""Fixtures for template system tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from tests._db_factory import make_db


@pytest.fixture
def incident_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + planning + incident packs enabled."""
    d = make_db(tmp_path, packs=["core", "planning", "incident"])
    yield d
    d.close()
```

**Step 2: Verify conftest loads**

Run: `uv run python -c "import tests.templates.conftest"`
Expected: No errors

---

## Task 2: Create `tests/templates/test_registry.py`

**Files:**
- Create: `tests/templates/test_registry.py`
- Source: `tests/test_templates.py` (lines 1-155, 155-430, 571-1445)
- Source: `tests/test_template_validation_fixes.py` (lines 88-478, excluding lines 44-86)

**Step 1: Compose file from sources**

Combine these classes in order:
1. TestDataclasses (from test_templates.py lines 30-115)
2. TestExceptions (from test_templates.py lines 118-152)
3. TestTemplateRegistry (from test_templates.py lines 155-430, including its `registry` fixture)
4. TestStateCategoryValidation (from test_template_validation_fixes.py)
5. TestDuplicateStateNameDetection (from test_template_validation_fixes.py)
6. TestEnabledPacksValidation (from test_template_validation_fixes.py)
7. TestParseTemplateMalformedTransitionsFields (from test_template_validation_fixes.py)
8. TestFieldSchemaTypeValidation (from test_template_validation_fixes.py)
9. TestDuplicateTransitionDetection (from test_template_validation_fixes.py)
10. TestEnforcementNoneRejected (from test_template_validation_fixes.py)
11. TestRolledBackCategoryFix (from test_template_validation_fixes.py)
12. TestBuiltInPackData (from test_templates.py lines 571-1121)
13. TestTemplateLoading (from test_templates.py lines 1123-1374)
14. TestQualityCheckDoneOutgoing (from test_templates.py lines 1377-1445)

Imports (union of both source files):
```python
"""Tests for the workflow template system — registry, validation, loading, packs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

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
```

**Step 2: Run the new file's tests**

Run: `uv run pytest tests/templates/test_registry.py -v --tb=short`
Expected: All pass (same tests, just moved)

---

## Task 3: Create `tests/templates/test_transitions.py`

**Files:**
- Create: `tests/templates/test_transitions.py`
- Source: `tests/test_templates.py` (TestTransitionValidation, lines 432-568)
- Source: `tests/test_template_validation_fixes.py` (TestIncidentResolvedCategory, lines 44-86)

**Step 1: Compose file from sources**

Combine:
1. TestTransitionValidation (with its inline `registry` fixture)
2. TestIncidentResolvedCategory (uses `incident_db` from conftest)

Imports:
```python
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
```

Note: `incident_db` fixture comes from `templates/conftest.py` (Task 1).

**Step 2: Run the new file's tests**

Run: `uv run pytest tests/templates/test_transitions.py -v --tb=short`
Expected: All pass

---

## Task 4: Create `tests/templates/test_db_integration.py`

**Files:**
- Create: `tests/templates/test_db_integration.py`
- Source: `tests/test_filigreedb_templates.py` (all content)

**Step 1: Copy file, convert 4 tests to make_db**

Convert these 4 tests that share identical `.filigree` + `write_config` + `FiligreeDB()` boilerplate:
- `test_templates_property_returns_registry` → `make_db(tmp_path, packs=["core", "planning"])`
- `test_templates_property_lazy` → `make_db(tmp_path)`
- `test_templates_property_has_types` → `make_db(tmp_path, packs=["core", "planning"])`
- `test_templates_property_uses_filigree_dir` → `make_db(tmp_path, packs=["core"])`

Keep these 4 tests with inline construction (they test constructor behavior):
- `test_templates_injectable` — tests `template_registry=` constructor arg
- `test_templates_no_circular_import` — no DB at all
- `test_templates_property_prefers_constructor_enabled_packs` — tests `enabled_packs=` override
- `test_templates_with_from_project` — tests `from_project()` class method

Imports:
```python
"""Tests for FiligreeDB.templates lazy property integration."""

from __future__ import annotations

from pathlib import Path

from filigree.core import FiligreeDB, write_config
from tests._db_factory import make_db
```

Note: `write_config` is still needed by the inline-construction tests.

**Step 2: Run the new file's tests**

Run: `uv run pytest tests/templates/test_db_integration.py -v --tb=short`
Expected: All 8 pass

---

## Task 5: Delete template source files and verify

**Files:**
- Delete: `tests/test_templates.py`
- Delete: `tests/test_template_validation_fixes.py`
- Delete: `tests/test_filigreedb_templates.py`

**Step 1: Delete the originals**

```bash
rm tests/test_templates.py tests/test_template_validation_fixes.py tests/test_filigreedb_templates.py
```

**Step 2: Run full test suite**

Run: `uv run pytest --tb=short`
Expected: All pass, same test count as before (no tests lost or duplicated)

**Step 3: Commit template consolidation**

```bash
git add tests/templates/ tests/test_templates.py tests/test_template_validation_fixes.py tests/test_filigreedb_templates.py
git commit -m "refactor: test suite reboot Phase 5 — template test consolidation"
```

---

## Task 6: Move `test_migrate.py` to `tests/migrations/`

**Files:**
- Move: `tests/test_migrate.py` → `tests/migrations/test_migrate.py`

**Step 1: Move the file**

```bash
git mv tests/test_migrate.py tests/migrations/test_migrate.py
```

No code changes needed — `beads_db` and `db` fixtures are in root conftest.py and discovered automatically by pytest.

**Step 2: Run migration tests**

Run: `uv run pytest tests/migrations/test_migrate.py -v --tb=short`
Expected: All pass

**Step 3: Run full test suite**

Run: `uv run pytest --tb=short`
Expected: All pass

**Step 4: Commit migration move**

```bash
git add tests/migrations/test_migrate.py tests/test_migrate.py
git commit -m "refactor: test suite reboot Phase 5 — move migration tests"
```

---

## Task 7: Close filigree issues

**Step 1: Close all 3 Phase 5 step issues**

- `filigree-bc539d` — Consolidate template and workflow tests
- `filigree-021a702c9f` — Move template test files (subsumed by step 1)
- `filigree-57c7947b6e` — Move migration test files

**Step 2: Close Phase 5**

- `filigree-64a72e` — Phase 5: Templates and migrations

---

## Pre-flight checks

Before starting, capture baseline test count:
```bash
uv run pytest --co -q | tail -1
```

After Task 5 and Task 6, the count must match exactly.
