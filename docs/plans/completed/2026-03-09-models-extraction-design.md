# Design: Extract Dataclasses to `models.py`

**Date:** 2026-03-09
**Status:** Approved

## Problem

Circular dependency between `core.py` and `types/core.py`:

```
core.py  ──imports──>  types/core.py
  │                        │
  │  defines Issue         │  needs Issue for
  │  dataclass             │  PromoteObservationResult
  │                        │
  └────── circular ◄───────┘
```

`core.py` imports `IssueDict`, `StatusCategory`, etc. from `types/core.py`.
`types/core.py` cannot import `Issue` back without creating a cycle.

### Current workarounds

- `PromoteObservationResult.issue` typed as `Any` (loss of type safety)
- 5 mixin files use `if TYPE_CHECKING:` guards for Issue/FileRecord/ScanFinding
- 3 runtime local imports inside method bodies (`db_files.py` x2, `db_issues.py` x1)

### Root cause

Issue, FileRecord, and ScanFinding are pure dataclasses with no DB dependencies.
They live in `core.py` only for historical reasons — `core.py` was the original
god module before the mixin split.

## Solution

Create `src/filigree/models.py` containing the three dataclasses and the
`_EMPTY_TS` sentinel. The dependency chain becomes a strict DAG:

```
types/core.py  <──  models.py  <──  core.py
                        │          <──  db_*.py mixins
                        │
                    Issue, FileRecord, ScanFinding, _EMPTY_TS
```

## Key Decisions

### 1. ScanFinding validation without db_files dependency

`ScanFinding.__post_init__` currently does a local import of
`VALID_FINDING_STATUSES` and `VALID_SEVERITIES` from `db_files.py`. In
`models.py`, we derive these directly using `get_args()` on the `Severity` and
`FindingStatus` Literal types from `types/core.py`. This keeps `models.py`
dependency-free from the mixin layer.

### 2. Backward compatibility via re-export

`core.py` re-exports all three dataclasses and `_EMPTY_TS` through its existing
`__all__`. No downstream breakage — test files and external consumers that import
from `filigree.core` continue to work unchanged.

### 3. Fix the Any escape hatch

`PromoteObservationResult.issue` in `types/core.py` gets properly typed as
`Issue` (imported from `models.py`). This is the original motivation for the
refactor.

### 4. Replace TYPE_CHECKING guards with real imports

All 5 mixin files switch from `if TYPE_CHECKING:` guards to real module-level
imports from `models.py`. The 3 runtime local imports inside method bodies are
also promoted to module-level.

## Files Changed

| File | Change |
|------|--------|
| `models.py` (new) | Issue, FileRecord, ScanFinding, `_EMPTY_TS` |
| `core.py` | Remove dataclass defs, import + re-export from models |
| `types/core.py` | `PromoteObservationResult.issue: Issue` (was `Any`) |
| `db_base.py` | TYPE_CHECKING guard -> real import from models |
| `db_files.py` | TYPE_CHECKING guard + 2 local imports -> real import |
| `db_issues.py` | TYPE_CHECKING guard + 1 local import -> real import |
| `db_planning.py` | TYPE_CHECKING guard -> real import from models |
| `__init__.py` | Import Issue from models instead of core |
| `mcp_tools/common.py` | Import Issue from models instead of core |
| `analytics.py` | Import Issue from models instead of core |
| `summary.py` | Import Issue from models instead of core |
| `dashboard_routes/analytics.py` | Import Issue from models instead of core |

Test files left unchanged (work through core.py re-exports).

## Not In Scope

- Moving VALID_* constants to types/core.py (get_args() in models.py suffices)
- Import linter rules (follow-up)
- Test file import migration (follow-up)
- Deprecation warnings on core.py re-exports (follow-up)
