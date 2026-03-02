# Task 1A: Dataclass to_dict() TypedDicts — Implementation Plan

> **STATUS: COMPLETED** — All tasks in this plan were implemented in commits `5f44056` through `0f972b2` on the `v1.4.0-architectural-refactor` branch. The `types/` subpackage exists with all modules populated, `to_dict()` methods return TypedDicts, backward-compat re-exports are in place, and contract tests are passing. Tasks 1B and 1C were also completed subsequently. **Do not re-execute this plan.**

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create `src/filigree/types/` subpackage with three foundational TypedDicts (IssueDict, FileRecordDict, ScanFindingDict), move existing PaginatedResult/ProjectConfig/ISOTimestamp there, convert `to_dict()` methods to use TypedDict constructors.

**Architecture:** New `types/` subpackage under `src/filigree/` with domain-split modules. TypedDicts only import from typing/stdlib/each other — never from core.py or mixins. Backward-compat re-exports in core.py keep all existing imports working.

**Tech Stack:** Python 3.11+ typing (TypedDict, NewType, NotRequired), mypy strict mode, pytest parametrize.

---

### Task 1: Create types/ subpackage scaffold

**Files:**
- Create: `src/filigree/types/__init__.py`
- Create: `src/filigree/types/core.py`
- Create: `src/filigree/types/files.py` (empty stub)
- Create: `src/filigree/types/events.py` (empty stub)
- Create: `src/filigree/types/planning.py` (empty stub)
- Create: `src/filigree/types/workflow.py` (empty stub)

**Step 1: Create the types/ directory and stub files**

```python
# src/filigree/types/__init__.py
# IMPORT CONSTRAINT: types/ modules must only import from typing, stdlib,
# and each other. NEVER import from core.py, db_base.py, or any mixin —
# this prevents circular imports.
"""Typed return-value contracts for filigree core and API layers."""

from __future__ import annotations

# core.py types (Task 1A)
from filigree.types.core import (
    FileRecordDict,
    ISOTimestamp,
    IssueDict,
    PaginatedResult,
    ProjectConfig,
    ScanFindingDict,
)

# Placeholder re-exports for parallel tasks 1B/1C:
# from filigree.types.files import ...       # Task 1B
# from filigree.types.events import ...      # Task 1C
# from filigree.types.planning import ...    # Task 1C
# from filigree.types.workflow import ...    # Task 1C

__all__ = [
    "FileRecordDict",
    "ISOTimestamp",
    "IssueDict",
    "PaginatedResult",
    "ProjectConfig",
    "ScanFindingDict",
]
```

```python
# src/filigree/types/core.py
"""Foundational TypedDicts for dataclass to_dict() returns."""

from __future__ import annotations

from typing import Any, NewType, TypedDict

ISOTimestamp = NewType("ISOTimestamp", str)


class ProjectConfig(TypedDict, total=False):
    """Shape of .filigree/config.json."""

    prefix: str
    name: str
    version: int
    enabled_packs: list[str]
    mode: str


class PaginatedResult(TypedDict):
    """Envelope returned by paginated query methods."""

    results: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    has_more: bool


class IssueDict(TypedDict):
    """Shape of Issue.to_dict() return value."""

    id: str
    title: str
    status: str
    status_category: str
    priority: int
    type: str
    parent_id: str | None
    assignee: str
    created_at: ISOTimestamp
    updated_at: ISOTimestamp
    closed_at: ISOTimestamp | None
    description: str
    notes: str
    fields: dict[str, Any]
    labels: list[str]
    blocks: list[str]
    blocked_by: list[str]
    is_ready: bool
    children: list[str]


class FileRecordDict(TypedDict):
    """Shape of FileRecord.to_dict() return value."""

    id: str
    path: str
    language: str
    file_type: str
    first_seen: ISOTimestamp
    updated_at: ISOTimestamp
    metadata: dict[str, Any]


class ScanFindingDict(TypedDict):
    """Shape of ScanFinding.to_dict() return value."""

    id: str
    file_id: str
    severity: str
    status: str
    scan_source: str
    rule_id: str
    message: str
    suggestion: str
    scan_run_id: str
    line_start: int | None
    line_end: int | None
    issue_id: str | None
    seen_count: int
    first_seen: ISOTimestamp
    updated_at: ISOTimestamp
    last_seen_at: ISOTimestamp | None
    metadata: dict[str, Any]
```

Empty stubs for 1B/1C modules:
```python
# src/filigree/types/files.py
"""TypedDicts for db_files.py return types (populated by Task 1B)."""

from __future__ import annotations
```

```python
# src/filigree/types/events.py
"""TypedDicts for db_events.py return types (populated by Task 1C)."""

from __future__ import annotations
```

```python
# src/filigree/types/planning.py
"""TypedDicts for db_planning.py, db_meta.py, and analytics.py return types (populated by Task 1C)."""

from __future__ import annotations
```

```python
# src/filigree/types/workflow.py
"""TypedDicts for db_workflow.py return types (populated by Task 1C)."""

from __future__ import annotations
```

**Step 2: Verify ruff + mypy pass on new files**

Run: `uv run ruff check src/filigree/types/ && uv run mypy src/filigree/types/`
Expected: clean

---

### Task 2: Move ISOTimestamp, ProjectConfig, PaginatedResult to types/core.py with backward-compat re-exports

**Files:**
- Modify: `src/filigree/core.py:24` (imports), `core.py:58-79` (remove class defs), `core.py:211` (remove ISOTimestamp)
- Modify: `src/filigree/db_files.py:20` (update PaginatedResult import)

**Step 1: Update core.py imports — import from types/ and re-export**

Replace the `ISOTimestamp`, `ProjectConfig`, and `PaginatedResult` definitions in `core.py` with imports from the new types/ package. Keep re-exports so all existing `from filigree.core import ...` statements continue to work.

In `core.py`, the import line (line 24) currently has:
```python
from typing import TYPE_CHECKING, Any, Literal, NewType, TypedDict
```
Change to:
```python
from typing import TYPE_CHECKING, Any, Literal
```

Remove these definitions from core.py (lines 58-79, 211):
- `class ProjectConfig(TypedDict, total=False)` (lines 62-69)
- `class PaginatedResult(TypedDict)` (lines 72-79)
- `ISOTimestamp = NewType("ISOTimestamp", str)` (line 211)

Add import at top of core.py (after the typing import):
```python
from filigree.types.core import ISOTimestamp, PaginatedResult, ProjectConfig
```

Update the `__all__` or just let the re-imports serve as the backward compat path.

**Step 2: Update db_files.py TYPE_CHECKING import**

`db_files.py:20` currently imports PaginatedResult from core:
```python
from filigree.core import FileRecord, Issue, PaginatedResult, ScanFinding
```
Change to:
```python
from filigree.core import FileRecord, Issue, ScanFinding
from filigree.types.core import PaginatedResult
```

**Step 3: Run full CI to verify nothing broke**

Run: `uv run ruff check src/ tests/ && uv run mypy src/filigree/ && uv run pytest --tb=short -q`
Expected: all pass, no import errors

**Step 4: Commit**

```bash
git add src/filigree/types/ src/filigree/core.py src/filigree/db_files.py
git commit -m "refactor: create types/ subpackage, move ISOTimestamp/ProjectConfig/PaginatedResult"
```

---

### Task 3: Convert Issue.to_dict() to return IssueDict

**Files:**
- Modify: `src/filigree/core.py:237-258` (Issue.to_dict)

**Step 1: Write baseline test for FileRecord and ScanFinding to_dict() stability**

Before touching any to_dict() methods, lock in the current shapes as regression tests (the critique addendum flagged this — Issue already has one in test_backward_compat.py but FileRecord and ScanFinding don't).

File: `tests/core/test_backward_compat.py` — add two new test classes after `TestToDictStability`:

```python
class TestFileRecordToDictStability:
    """FileRecord.to_dict() must include all expected keys."""

    def test_to_dict_keys(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        files = db.list_files_paginated(limit=1)
        fr = files["results"][0]
        required_keys = {"id", "path", "language", "file_type", "first_seen", "updated_at", "metadata"}
        assert set(fr.keys()) == required_keys

    def test_to_dict_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        files = db.list_files_paginated(limit=1)
        fr = files["results"][0]
        assert isinstance(fr["id"], str)
        assert isinstance(fr["path"], str)
        assert isinstance(fr["metadata"], dict)


class TestScanFindingToDictStability:
    """ScanFinding.to_dict() must include all expected keys."""

    def test_to_dict_keys(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        result = db.process_scan_results(
            scanner="test",
            file_path="/src/main.py",
            findings=[{
                "rule_id": "R001",
                "message": "test finding",
                "severity": "high",
                "line_start": 1,
                "line_end": 5,
            }],
        )
        findings = db.get_findings_paginated(file_id=file_id, limit=1)
        sf = findings["results"][0]
        required_keys = {
            "id", "file_id", "severity", "status", "scan_source", "rule_id",
            "message", "suggestion", "scan_run_id", "line_start", "line_end",
            "issue_id", "seen_count", "first_seen", "updated_at", "last_seen_at", "metadata",
        }
        assert set(sf.keys()) == required_keys

    def test_to_dict_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        db.process_scan_results(
            scanner="test",
            file_path="/src/main.py",
            findings=[{"rule_id": "R001", "message": "test", "severity": "high"}],
        )
        findings = db.get_findings_paginated(file_id=file_id, limit=1)
        sf = findings["results"][0]
        assert isinstance(sf["id"], str)
        assert isinstance(sf["severity"], str)
        assert isinstance(sf["seen_count"], int)
```

Run: `uv run pytest tests/core/test_backward_compat.py -v --tb=short`
Expected: all PASS (establishing baseline)

**Step 2: Convert Issue.to_dict() return type and body**

In `core.py`, change `Issue.to_dict()`:
```python
def to_dict(self) -> IssueDict:
    return IssueDict(
        id=self.id,
        title=self.title,
        status=self.status,
        status_category=self.status_category,
        priority=self.priority,
        type=self.type,
        parent_id=self.parent_id,
        assignee=self.assignee,
        created_at=self.created_at,
        updated_at=self.updated_at,
        closed_at=self.closed_at,
        description=self.description,
        notes=self.notes,
        fields=self.fields,
        labels=self.labels,
        blocks=self.blocks,
        blocked_by=self.blocked_by,
        is_ready=self.is_ready,
        children=self.children,
    )
```

Add import at top of core.py (extend existing types import):
```python
from filigree.types.core import ISOTimestamp, IssueDict, PaginatedResult, ProjectConfig
```

**Step 3: Run tests**

Run: `uv run pytest tests/core/test_backward_compat.py -v --tb=short`
Expected: all PASS

**Step 4: Convert FileRecord.to_dict() and ScanFinding.to_dict()**

```python
def to_dict(self) -> FileRecordDict:
    return FileRecordDict(
        id=self.id,
        path=self.path,
        language=self.language,
        file_type=self.file_type,
        first_seen=self.first_seen,
        updated_at=self.updated_at,
        metadata=self.metadata,
    )
```

```python
def to_dict(self) -> ScanFindingDict:
    return ScanFindingDict(
        id=self.id,
        file_id=self.file_id,
        severity=self.severity,
        status=self.status,
        scan_source=self.scan_source,
        rule_id=self.rule_id,
        message=self.message,
        suggestion=self.suggestion,
        scan_run_id=self.scan_run_id,
        line_start=self.line_start,
        line_end=self.line_end,
        issue_id=self.issue_id,
        seen_count=self.seen_count,
        first_seen=self.first_seen,
        updated_at=self.updated_at,
        last_seen_at=self.last_seen_at,
        metadata=self.metadata,
    )
```

Add imports to core.py:
```python
from filigree.types.core import (
    FileRecordDict,
    ISOTimestamp,
    IssueDict,
    PaginatedResult,
    ProjectConfig,
    ScanFindingDict,
)
```

**Step 5: Run full CI**

Run: `uv run ruff check src/ tests/ && uv run mypy src/filigree/ && uv run pytest --tb=short -q`
Expected: all pass

**Step 6: Commit**

```bash
git add src/filigree/core.py tests/core/test_backward_compat.py
git commit -m "feat(types): convert to_dict() methods to return IssueDict/FileRecordDict/ScanFindingDict"
```

---

### Task 4: Add runtime shape tests and import constraint test

**Files:**
- Create: `tests/util/test_type_contracts.py`

**Step 1: Write parametrized runtime shape tests**

```python
# tests/util/test_type_contracts.py
"""Contract tests for TypedDict shapes vs actual runtime return values."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_type_hints

import pytest

from filigree.types.core import FileRecordDict, IssueDict, ScanFindingDict
from tests._db_factory import make_db


@pytest.fixture
def db(tmp_path: Path):
    d = make_db(tmp_path)
    yield d
    d.close()


class TestIssueDictShape:
    def test_keys_match(self, db) -> None:
        issue = db.create_issue("Test", type="task")
        result = issue.to_dict()
        hints = get_type_hints(IssueDict)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db) -> None:
        issue = db.create_issue("Test", type="task", priority=1, labels=["a"])
        result = issue.to_dict()
        assert isinstance(result["id"], str)
        assert isinstance(result["title"], str)
        assert isinstance(result["priority"], int)
        assert isinstance(result["is_ready"], bool)
        assert isinstance(result["labels"], list)
        assert isinstance(result["blocks"], list)
        assert isinstance(result["blocked_by"], list)
        assert isinstance(result["children"], list)
        assert isinstance(result["fields"], dict)


class TestFileRecordDictShape:
    def test_keys_match(self, db) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        files = db.list_files_paginated(limit=1)
        result = files["results"][0]
        hints = get_type_hints(FileRecordDict)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        files = db.list_files_paginated(limit=1)
        result = files["results"][0]
        assert isinstance(result["id"], str)
        assert isinstance(result["path"], str)
        assert isinstance(result["metadata"], dict)


class TestScanFindingDictShape:
    def test_keys_match(self, db) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scanner="test", file_path="/src/main.py",
            findings=[{"rule_id": "R1", "message": "m", "severity": "high"}],
        )
        files = db.list_files_paginated(limit=1)
        findings = db.get_findings_paginated(file_id=files["results"][0]["id"], limit=1)
        result = findings["results"][0]
        hints = get_type_hints(ScanFindingDict)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scanner="test", file_path="/src/main.py",
            findings=[{"rule_id": "R1", "message": "m", "severity": "high", "line_start": 1}],
        )
        files = db.list_files_paginated(limit=1)
        findings = db.get_findings_paginated(file_id=files["results"][0]["id"], limit=1)
        result = findings["results"][0]
        assert isinstance(result["id"], str)
        assert isinstance(result["severity"], str)
        assert isinstance(result["seen_count"], int)


# ---------------------------------------------------------------------------
# Import constraint: types/ must not import from core, db_base, or db_*.py
# ---------------------------------------------------------------------------

TYPES_DIR = Path(__file__).resolve().parents[2] / "src" / "filigree" / "types"
FORBIDDEN_MODULES = {"filigree.core", "filigree.db_base"}
# Also match any filigree.db_* mixin
FORBIDDEN_PREFIXES = ("filigree.db_",)


def _get_imports_from_file(filepath: Path) -> list[str]:
    """Extract all import targets from a Python file using AST."""
    tree = ast.parse(filepath.read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


@pytest.mark.parametrize(
    "py_file",
    sorted(TYPES_DIR.glob("*.py")),
    ids=lambda p: p.name,
)
def test_types_module_import_constraint(py_file: Path) -> None:
    """types/ modules must never import from core.py, db_base.py, or db_*.py mixins."""
    imports = _get_imports_from_file(py_file)
    for mod in imports:
        assert mod not in FORBIDDEN_MODULES, (
            f"{py_file.name} imports {mod} — types/ must not import from core or db modules"
        )
        for prefix in FORBIDDEN_PREFIXES:
            assert not mod.startswith(prefix), (
                f"{py_file.name} imports {mod} — types/ must not import from db mixins"
            )


# ---------------------------------------------------------------------------
# Dashboard JSON key contract: TypedDict keys must be a superset of what
# the JS frontend consumes (prevents silent breakage on renames)
# ---------------------------------------------------------------------------

# Keys the JS frontend reads from issue objects (extracted from dashboard.html + static/js/)
DASHBOARD_ISSUE_KEYS = {
    "id", "title", "type", "status", "status_category", "priority",
    "assignee", "blocked_by", "blocks", "updated_at", "created_at",
    "is_ready", "children", "labels", "description", "notes",
}


def test_issue_dict_keys_cover_dashboard_contract() -> None:
    """IssueDict must contain all keys the dashboard JS reads from issue objects."""
    hints = get_type_hints(IssueDict)
    missing = DASHBOARD_ISSUE_KEYS - set(hints.keys())
    assert not missing, f"IssueDict missing keys consumed by dashboard JS: {missing}"
```

**Step 2: Run the new tests**

Run: `uv run pytest tests/util/test_type_contracts.py -v --tb=short`
Expected: all PASS

**Step 3: Run full CI**

Run: `uv run ruff check src/ tests/ && uv run mypy src/filigree/ && uv run pytest --tb=short -q`
Expected: all pass

**Step 4: Commit**

```bash
git add tests/util/test_type_contracts.py
git commit -m "test(types): add runtime shape tests, import constraint test, and dashboard key contract"
```

---

## Summary

| Task | Description | Files Created | Files Modified |
|------|-------------|---------------|----------------|
| 1 | Scaffold types/ subpackage | 6 new files | — |
| 2 | Move ISOTimestamp/ProjectConfig/PaginatedResult | — | core.py, db_files.py |
| 3 | Convert 3 to_dict() methods | — | core.py, test_backward_compat.py |
| 4 | Shape tests + import constraint test + dashboard contract | 1 new test file | — |

Total: 7 new files, 3 modified files, 4 commits.
