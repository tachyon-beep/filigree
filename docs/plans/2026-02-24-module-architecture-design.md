# Module Architecture Design

**Date:** 2026-02-24
**Status:** Draft — awaiting review panel
**Branch:** feat/dashboard-modularization (v1.3.0)

---

## Problem

Five Python source files have grown into monoliths that concentrate too many responsibilities:

| File | Lines | Key Issue |
|------|------:|-----------|
| `core.py` | 3,722 | `FiligreeDB` class has 95 methods spanning 6 domains |
| `mcp_server.py` | 2,285 | 40 MCP tools registered in a single 827-line function |
| `cli.py` | 1,961 | 62 Click commands in one flat module |
| `dashboard.py` | 1,605 | 844-line router factory with all API routes inline |
| `install.py` | 1,168 | 400-line `run_doctor()` + mixed installation concerns |

This creates: merge conflicts when multiple changes touch the same file, difficulty reasoning about which code belongs to which domain, inability to test domains in isolation, and cognitive overload for contributors.

## Decisions

1. **Scope:** All five files
2. **Compatibility:** Facade pattern — `FiligreeDB` stays as the public API, backed by domain mixins. No caller changes.
3. **Organization:** Domain-grouped files for all layers (core, MCP, CLI, dashboard)

## Architecture

### 1. core.py → Domain Mixins

Split `FiligreeDB`'s 95 methods into mixin classes, composed via multiple inheritance.

#### Target Structure

```
src/filigree/
├── core.py              (~500 lines)  — FiligreeDB shell, __init__, conn, initialize,
│                                        close, dataclasses (Issue, FileRecord, ScanFinding),
│                                        project discovery functions, constants
├── db_issues.py         (~700 lines)  — IssuesMixin
├── db_files.py          (~900 lines)  — FilesMixin
├── db_events.py         (~350 lines)  — EventsMixin
├── db_planning.py       (~400 lines)  — PlanningMixin
├── db_workflow.py       (~250 lines)  — WorkflowMixin
└── db_meta.py           (~400 lines)  — MetaMixin
```

#### Method Assignment

**IssuesMixin** (`db_issues.py`):
- `create_issue`, `get_issue`, `_build_issue`, `_build_issues_batch`
- `update_issue`, `close_issue`, `reopen_issue`
- `claim_issue`, `release_claim`, `claim_next`
- `list_issues`, `search_issues`
- `batch_close`, `batch_update`, `batch_add_label`, `batch_add_comment`

> **Note:** `_validate_status` and `_validate_parent_id` are in **WorkflowMixin**, not here. IssuesMixin calls them via `self` (MRO resolution).

**FilesMixin** (`db_files.py`):
- `register_file`, `get_file`, `get_file_by_path`
- `list_files`, `list_files_paginated`
- `process_scan_results`, `_create_issue_for_finding`
- `update_finding`, `clean_stale_findings`
- `get_findings`, `get_findings_paginated`, `get_file_findings_summary`, `get_global_findings_stats`
- `get_file_detail`, `add_file_association`, `get_file_associations`
- `get_issue_files`, `get_issue_findings`, `get_file_hotspots`, `get_file_timeline`
- `_generate_file_id`, `_generate_finding_id`, `_build_file_record`, `_build_scan_finding`
- `_normalize_scan_path` (module-level, stays in core.py or moves to a shared utils)

**EventsMixin** (`db_events.py`):
- `_record_event`, `get_recent_events`, `get_events_since`, `get_issue_events`
- `undo_last`
- `archive_closed`, `compact_events`, `vacuum`, `analyze`

**PlanningMixin** (`db_planning.py`):
- `create_plan`, `get_plan`
- `add_dependency`, `remove_dependency`, `get_all_dependencies`, `_would_create_cycle`
- `get_ready`, `get_blocked`, `get_critical_path`

**WorkflowMixin** (`db_workflow.py`):
- `_validate_status`, `_validate_parent_id` (called cross-mixin by IssuesMixin and MetaMixin)
- `get_valid_transitions`, `validate_issue`
- `_get_states_for_category`, `_infer_status_category`, `_resolve_status_category`
- `_seed_templates`, `reload_templates`, `get_template`, `list_templates`
- `_reserved_label_names`, `_validate_label_name`

**MetaMixin** (`db_meta.py`):
- `add_comment`, `get_comments`, `add_label`, `remove_label`
- `get_stats`
- `export_jsonl`, `import_jsonl`
- `bulk_insert_issue`, `bulk_insert_dependency`, `bulk_insert_event`, `bulk_commit`

#### Composition

```python
# core.py
from filigree.db_issues import IssuesMixin
from filigree.db_files import FilesMixin
from filigree.db_events import EventsMixin
from filigree.db_planning import PlanningMixin
from filigree.db_workflow import WorkflowMixin
from filigree.db_meta import MetaMixin

class FiligreeDB(
    IssuesMixin,
    FilesMixin,
    EventsMixin,
    PlanningMixin,
    WorkflowMixin,
    MetaMixin,
):
    """Direct SQLite operations. No daemon, no sync. Importable by CLI and MCP."""

    def __init__(self, db_path: str | Path, ...):
        # Connection setup, schema init — unchanged
        ...
```

#### Cross-Domain Calls

Methods that cross domains use `self` resolution at runtime:
- `IssuesMixin.update_issue()` calls `self._record_event()` → resolves to `EventsMixin`
- `FilesMixin._create_issue_for_finding()` calls `self.create_issue()` → resolves to `IssuesMixin`
- `MetaMixin.import_jsonl()` calls `self.bulk_insert_issue()` → resolves to self

Each mixin uses `TYPE_CHECKING` imports for type hints on cross-domain return types:
```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from filigree.core import FiligreeDB
```

### 2. mcp_server.py → Domain Tool Modules

#### Target Structure

```
src/filigree/
├── mcp_server.py            (~150 lines)  — Server setup, main(), tool aggregation
└── mcp_tools/
    ├── __init__.py
    ├── common.py            (~80 lines)   — _text(), _get_db(), _refresh_summary()
    ├── issues.py            (~400 lines)  — create/update/close/reopen/list/search/claim tools
    ├── planning.py          (~200 lines)  — plan/deps/ready/blocked/critical-path tools
    ├── files.py             (~250 lines)  — file list/detail/associations/scan-trigger tools
    ├── workflow.py          (~200 lines)  — templates/transitions/validate/states/packs tools
    └── meta.py              (~200 lines)  — comments/labels/stats/export/import/archive tools
```

#### Registration Pattern

Each domain file exports a `register()` function:

```python
# mcp_tools/issues.py
from filigree.mcp_tools.common import _text, _get_db, _refresh_summary

def register() -> tuple[list[Tool], dict[str, Callable]]:
    """Return (tool_schemas, handler_map) for issue-related tools."""
    tools = []
    handlers = {}

    tools.append(Tool(name="create_issue", description="...", inputSchema={...}))
    async def handle_create_issue(arguments: dict) -> list[TextContent]:
        db = _get_db()
        ...
    handlers["create_issue"] = handle_create_issue

    return tools, handlers
```

The main `mcp_server.py` aggregates:

```python
from filigree.mcp_tools import issues, planning, files, workflow, meta

_all_tools: list[Tool] = []
_all_handlers: dict[str, Callable] = {}

for module in [issues, planning, files, workflow, meta]:
    tools, handlers = module.register()
    _all_tools.extend(tools)
    _all_handlers.update(handlers)

@server.list_tools()
async def list_tools() -> list[Tool]:
    return _all_tools

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = _all_handlers.get(name)
    ...
```

### 3. cli.py → Domain Command Modules

#### Target Structure

```
src/filigree/
├── cli.py                   (~150 lines)  — main group, common helpers, _get_db
└── cli_commands/
    ├── __init__.py
    ├── issues.py            (~400 lines)  — create/show/list/update/close/reopen
    ├── planning.py          (~200 lines)  — plan/add-dep/remove-dep/ready/blocked/critical-path
    ├── batch.py             (~200 lines)  — batch-update/batch-close/batch-add-label/batch-add-comment
    ├── workflow.py          (~200 lines)  — types/type-info/transitions/packs/validate/guide/explain-state
    ├── meta.py              (~200 lines)  — comments/labels/stats/search/undo/changes/events
    ├── data.py              (~150 lines)  — export/import/archive/compact
    ├── server.py            (~150 lines)  — server start/stop/status/register/unregister
    └── setup.py             (~200 lines)  — init/install/doctor/migrate/dashboard/session-context
```

Each file defines Click commands. The main `cli.py` imports and registers:

```python
from filigree.cli_commands import issues, planning, batch, ...

cli.add_command(issues.create)
cli.add_command(issues.show)
cli.add_command(planning.plan)
# ...
```

### 4. dashboard.py → Domain Route Modules

#### Target Structure

```
src/filigree/
├── dashboard.py             (~200 lines)  — app creation, middleware, ProjectStore, main()
└── dashboard_routes/
    ├── __init__.py
    ├── common.py            (~100 lines)  — _error_response, _safe_int, _parse_bool_value, etc.
    ├── issues.py            (~300 lines)  — CRUD, batch, transitions, comments, labels
    ├── files.py             (~250 lines)  — file list/detail, findings, associations, scan-results
    ├── analytics.py         (~150 lines)  — stats, metrics, activity, plan, critical-path
    └── system.py            (~100 lines)  — health, workflow-states, graph config, schema
```

Each uses FastAPI's `APIRouter`:

```python
# dashboard_routes/issues.py
from fastapi import APIRouter, Depends
router = APIRouter()

@router.get("/api/issues")
async def list_issues(db=Depends(_get_db)):
    ...
```

Main dashboard includes them:

```python
from filigree.dashboard_routes import issues, files, analytics, system
app.include_router(issues.router)
app.include_router(files.router)
...
```

### 5. install.py → Support Modules

```
src/filigree/
├── install.py               (~300 lines)  — main install orchestration, inject_instructions
└── install_support/
    ├── doctor.py            (~400 lines)  — run_doctor + all check functions
    ├── hooks.py             (~200 lines)  — claude code hook installation
    └── integrations.py      (~200 lines)  — MCP, codex, ethereal mode setup
```

## Final Directory Structure

```
src/filigree/
├── __init__.py              (unchanged — re-exports FiligreeDB, Issue)
├── __main__.py              (unchanged)
├── core.py                  (~500 lines, down from 3,722)
├── db_issues.py             (NEW ~700 lines)
├── db_files.py              (NEW ~900 lines)
├── db_events.py             (NEW ~350 lines)
├── db_planning.py           (NEW ~400 lines)
├── db_workflow.py           (NEW ~250 lines)
├── db_meta.py               (NEW ~400 lines)
├── mcp_server.py            (~150 lines, down from 2,285)
├── mcp_tools/               (NEW package)
│   ├── __init__.py
│   ├── common.py
│   ├── issues.py
│   ├── planning.py
│   ├── files.py
│   ├── workflow.py
│   └── meta.py
├── cli.py                   (~150 lines, down from 1,961)
├── cli_commands/             (NEW package)
│   ├── __init__.py
│   ├── issues.py
│   ├── planning.py
│   ├── batch.py
│   ├── workflow.py
│   ├── meta.py
│   ├── data.py
│   ├── server.py
│   └── setup.py
├── dashboard.py             (~200 lines, down from 1,605)
├── dashboard_routes/         (NEW package)
│   ├── __init__.py
│   ├── common.py
│   ├── issues.py
│   ├── files.py
│   ├── analytics.py
│   └── system.py
├── install.py               (~300 lines, down from 1,168)
├── install_support/          (NEW package)
│   ├── doctor.py
│   ├── hooks.py
│   └── integrations.py
├── templates.py             (unchanged)
├── templates_data.py        (unchanged — pure data)
├── migrations.py            (unchanged)
├── hooks.py                 (unchanged)
├── summary.py               (unchanged)
├── server.py                (unchanged)
├── migrate.py               (unchanged)
├── ephemeral.py             (unchanged)
├── scanners.py              (unchanged)
├── analytics.py             (unchanged)
├── logging.py               (unchanged)
└── static/                  (unchanged — already modularized)
```

**Before:** 5 files totaling ~11,241 lines (avg 2,248 lines/file)
**After:** 5 shell files (~1,200 lines total) + 27 domain files (~5,800 lines total, avg ~215 lines/file)
**Largest remaining file:** `db_files.py` at ~900 lines — acceptable given the domain complexity.

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Circular imports between mixins | Mixins only import from `core.py` (dataclasses, constants). Cross-mixin calls go through `self` at runtime. `TYPE_CHECKING` guards for type hints. |
| Python MRO (method resolution order) surprises | All mixins are flat (no diamond inheritance). MRO is deterministic and documented. |
| Breaking the public API | `FiligreeDB` remains the only public class. `__init__.py` still exports `FiligreeDB` and `Issue`. |
| Test suite breakage | Tests import `FiligreeDB` and call its methods — facade means zero test changes. |
| Increased import complexity | Each mixin file has a clear, self-documenting name. `core.py` is the single entry point. |
| `from filigree.core import ...` in external callers | All current imports from `core` are `FiligreeDB`, `Issue`, constants, and utility functions — all stay in `core.py`. |

## Extraction Order

Bottom-up, starting from the modules with fewest cross-domain dependencies:

1. **`db_events.py`** — most self-contained (event recording, undo, archive)
2. **`db_workflow.py`** — template operations, minimal cross-deps
3. **`db_meta.py`** — comments, labels, stats, I/O
4. **`db_planning.py`** — deps, plans, DAG queries
5. **`db_issues.py`** — depends on events + workflow
6. **`db_files.py`** — depends on events + issues (for `_create_issue_for_finding`)
7. **`mcp_tools/`** — parallels the core split
8. **`cli_commands/`** — parallels the core split
9. **`dashboard_routes/`** — parallels the core split
10. **`install_support/`** — independent of other splits

Each extraction is independently verifiable: after extracting a mixin, `pytest` should pass identically.
