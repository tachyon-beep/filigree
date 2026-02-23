# Module Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Decompose 5 oversized Python modules into 27+ domain-focused files using mixins, domain tool registries, and FastAPI routers — without changing any public API.

**Architecture:** Domain mixin classes compose into `FiligreeDB` via multiple inheritance. MCP tools, CLI commands, and dashboard routes split into domain-grouped subpackages. The facade pattern ensures zero caller changes.

**Tech Stack:** Python 3.12, SQLite, Click (CLI), FastAPI (dashboard), MCP SDK (mcp_server)

**Design doc:** `docs/plans/2026-02-24-module-architecture-design.md`

---

## Task 1: Extract EventsMixin from core.py

The most self-contained domain — event recording, undo, archive, compact. Only 10 other methods call `_record_event`, so this mixin has minimal inbound dependencies.

**Files:**
- Create: `src/filigree/db_events.py`
- Modify: `src/filigree/core.py:2098-3722` (remove event methods)
- Modify: `src/filigree/core.py:505-507` (add mixin to class definition)
- Test: `tests/test_module_split.py` (new — verifies mixin composition works)

**Step 1: Write the failing test**

Create `tests/test_module_split.py`:

```python
"""Verify mixin-based FiligreeDB composition works correctly."""

import tempfile
from pathlib import Path

from filigree.core import FiligreeDB
from filigree.db_events import EventsMixin


def _make_db():
    tmp = tempfile.mkdtemp()
    db = FiligreeDB(Path(tmp) / "test.db", prefix="test")
    db.initialize()
    return db


def test_events_mixin_is_base_class():
    """FiligreeDB should inherit from EventsMixin."""
    assert issubclass(FiligreeDB, EventsMixin)


def test_record_event_available():
    """_record_event should be callable on FiligreeDB instances."""
    db = _make_db()
    issue = db.create_issue(title="test")
    db._record_event(issue.id, "test_event", actor="test")
    events = db.get_issue_events(issue.id)
    assert any(e["event_type"] == "test_event" for e in events)
    db.close()


def test_undo_last_available():
    """undo_last should work through mixin composition."""
    db = _make_db()
    issue = db.create_issue(title="original")
    db.update_issue(issue.id, title="changed")
    result = db.undo_last(issue.id)
    assert result["event_type"] == "title_changed"
    db.close()


def test_archive_compact_available():
    """archive_closed and compact_events should work."""
    db = _make_db()
    archived = db.archive_closed(days_old=0)
    assert isinstance(archived, list)
    compacted = db.compact_events(keep_recent=50)
    assert isinstance(compacted, int)
    db.close()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_module_split.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'filigree.db_events'`

**Step 3: Create db_events.py with extracted methods**

Create `src/filigree/db_events.py` containing:
- All imports needed by the event methods
- `class EventsMixin:` with these methods extracted verbatim from `core.py`:
  - `_record_event` (lines 2100-2114)
  - `get_recent_events` (lines 2116-2122)
  - `get_events_since` (lines 2123-2133)
  - `get_issue_events` (lines 2134-2142)
  - `undo_last` (lines 2143-2273)
  - `archive_closed` (lines 3655-3687)
  - `compact_events` (lines 3688-3715)
  - `vacuum` (lines 3716-3719)
  - `analyze` (lines 3720-3722)

The mixin references `self.conn` and `self._record_event()` — both resolve at runtime via MRO.

Add `TYPE_CHECKING` imports for cross-domain types:
```python
from __future__ import annotations
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    import sqlite3
```

Move `_REVERSIBLE_EVENTS` and `_SKIP_EVENTS` constants from `core.py` to `db_events.py` (they're only used by `undo_last`).

**Step 4: Update core.py**

1. Remove the extracted methods from `FiligreeDB` class body (lines 2098-2273 and 3655-3722)
2. Remove `_REVERSIBLE_EVENTS` and `_SKIP_EVENTS` constants from top of file
3. Add import: `from filigree.db_events import EventsMixin`
4. Change class definition:
   ```python
   class FiligreeDB(EventsMixin):
   ```

**Step 5: Run full test suite**

Run: `uv run pytest tests/test_module_split.py tests/test_core.py tests/test_core_gaps.py tests/test_e2e_workflows.py -v`
Expected: ALL PASS

**Step 6: Run linting and type checking**

Run: `uv run ruff check src/filigree/core.py src/filigree/db_events.py && uv run mypy src/filigree/core.py src/filigree/db_events.py`
Expected: Clean

**Step 7: Commit**

```bash
git add src/filigree/db_events.py src/filigree/core.py tests/test_module_split.py
git commit -m "refactor: extract EventsMixin from FiligreeDB"
```

---

## Task 2: Extract WorkflowMixin from core.py

Template and workflow operations — validation, state resolution, template CRUD.

**Files:**
- Create: `src/filigree/db_workflow.py`
- Modify: `src/filigree/core.py:592-745` (remove workflow methods)
- Modify: `src/filigree/core.py:505` (add WorkflowMixin to bases)
- Test: `tests/test_module_split.py` (extend)

**Step 1: Add failing tests to test_module_split.py**

```python
from filigree.db_workflow import WorkflowMixin


def test_workflow_mixin_is_base_class():
    assert issubclass(FiligreeDB, WorkflowMixin)


def test_templates_available():
    db = _make_db()
    templates = db.list_templates()
    assert isinstance(templates, list)
    assert len(templates) > 0  # builtins exist
    db.close()


def test_validate_status():
    db = _make_db()
    # Should not raise for valid status
    db._validate_status("open", "task")
    db.close()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_module_split.py::test_workflow_mixin_is_base_class -v`
Expected: FAIL

**Step 3: Create db_workflow.py**

Extract from `core.py`:
- `templates` property (lines 592-607)
- `_seed_templates` (lines 608-612)
- `reload_templates` (lines 613-616)
- `get_template` (lines 617-649)
- `list_templates` (lines 650-663)
- `_validate_status` (lines 664-673)
- `_validate_parent_id` (lines 674-682)
- `_get_states_for_category` (lines 683-695)
- `_infer_status_category` (lines 696-705) — static method
- `_resolve_status_category` (lines 706-712)
- `_reserved_label_names` (lines 723-726) — property
- `_validate_label_name` (lines 727-745)

Note: `_validate_status` and `_validate_parent_id` are called by `IssuesMixin` methods — they resolve via `self` at runtime.

**Step 4: Update core.py**

1. Remove extracted methods
2. Add: `from filigree.db_workflow import WorkflowMixin`
3. Update: `class FiligreeDB(EventsMixin, WorkflowMixin):`

**Step 5: Run tests**

Run: `uv run pytest tests/test_module_split.py tests/test_templates.py tests/test_workflow_behavior.py -v`
Expected: ALL PASS

**Step 6: Lint + type check**

Run: `uv run ruff check src/filigree/db_workflow.py && uv run mypy src/filigree/db_workflow.py`

**Step 7: Commit**

```bash
git add src/filigree/db_workflow.py src/filigree/core.py tests/test_module_split.py
git commit -m "refactor: extract WorkflowMixin from FiligreeDB"
```

---

## Task 3: Extract MetaMixin from core.py

Comments, labels, stats, import/export, bulk operations.

**Files:**
- Create: `src/filigree/db_meta.py`
- Modify: `src/filigree/core.py` (remove meta methods)
- Test: `tests/test_module_split.py` (extend)

**Step 1: Add failing tests**

```python
from filigree.db_meta import MetaMixin


def test_meta_mixin_is_base_class():
    assert issubclass(FiligreeDB, MetaMixin)


def test_comments_available():
    db = _make_db()
    issue = db.create_issue(title="test")
    comment_id = db.add_comment(issue.id, "hello")
    comments = db.get_comments(issue.id)
    assert len(comments) == 1
    db.close()


def test_export_import_roundtrip():
    import tempfile, os
    db = _make_db()
    db.create_issue(title="export-test")
    out = os.path.join(tempfile.mkdtemp(), "export.jsonl")
    count = db.export_jsonl(out)
    assert count > 0
    db.close()
```

**Step 2: Verify failure**

Run: `uv run pytest tests/test_module_split.py::test_meta_mixin_is_base_class -v`

**Step 3: Create db_meta.py**

Extract:
- `add_comment` (lines 1988-1999)
- `get_comments` (lines 2000-2008)
- `add_label` (lines 2009-2017)
- `remove_label` (lines 2018-2027)
- `get_stats` (lines 2028-2099)
- `bulk_insert_issue` (lines 2274-2299)
- `bulk_insert_dependency` (lines 2300-2305)
- `bulk_insert_event` (lines 2306-2320)
- `bulk_commit` (lines 2321-2325)
- `export_jsonl` (lines 2326-2370)
- `import_jsonl` (lines 2371-2462)

**Step 4: Update core.py**

Add `MetaMixin` to the inheritance chain.

**Step 5: Run tests**

Run: `uv run pytest tests/test_module_split.py tests/test_core.py tests/test_migrate.py -v`

**Step 6: Lint + type check, then commit**

```bash
git add src/filigree/db_meta.py src/filigree/core.py tests/test_module_split.py
git commit -m "refactor: extract MetaMixin from FiligreeDB"
```

---

## Task 4: Extract PlanningMixin from core.py

Dependencies, plans, DAG queries (ready/blocked/critical path).

**Files:**
- Create: `src/filigree/db_planning.py`
- Modify: `src/filigree/core.py`
- Test: `tests/test_module_split.py` (extend)

**Step 1: Add failing tests**

```python
from filigree.db_planning import PlanningMixin


def test_planning_mixin_is_base_class():
    assert issubclass(FiligreeDB, PlanningMixin)


def test_dependency_management():
    db = _make_db()
    a = db.create_issue(title="a")
    b = db.create_issue(title="b")
    db.add_dependency(b.id, a.id)
    blocked = db.get_blocked()
    assert any(i.id == b.id for i in blocked)
    db.close()


def test_get_ready():
    db = _make_db()
    db.create_issue(title="ready-test")
    ready = db.get_ready()
    assert len(ready) >= 1
    db.close()
```

**Step 2: Verify failure, Step 3: Create db_planning.py**

Extract:
- `add_dependency` (lines 1596-1624)
- `_would_create_cycle` (lines 1625-1644)
- `remove_dependency` (lines 1645-1655)
- `get_all_dependencies` (lines 1656-1661)
- `get_ready` (lines 1662-1696)
- `get_blocked` (lines 1697-1730)
- `get_critical_path` (lines 1731-1795)
- `get_plan` (lines 1796-1830)
- `create_plan` (lines 1831-1987)

Cross-domain: `add_dependency`, `remove_dependency`, and `create_plan` call `self._record_event()` (EventsMixin) and `self.create_issue()` (IssuesMixin). These resolve via `self` at runtime.

**Step 4-6: Update core.py, run tests, lint, commit**

```bash
git commit -m "refactor: extract PlanningMixin from FiligreeDB"
```

---

## Task 5: Extract IssuesMixin from core.py

The largest domain — issue CRUD, batch operations, search, claiming.

**Files:**
- Create: `src/filigree/db_issues.py`
- Modify: `src/filigree/core.py`
- Test: `tests/test_module_split.py` (extend)

**Step 1: Add failing tests**

```python
from filigree.db_issues import IssuesMixin


def test_issues_mixin_is_base_class():
    assert issubclass(FiligreeDB, IssuesMixin)


def test_full_issue_lifecycle():
    db = _make_db()
    issue = db.create_issue(title="lifecycle")
    assert issue.title == "lifecycle"
    db.update_issue(issue.id, title="updated")
    updated = db.get_issue(issue.id)
    assert updated.title == "updated"
    db.close_issue(issue.id)
    closed = db.get_issue(issue.id)
    assert closed.status == "closed"
    db.close()


def test_batch_operations():
    db = _make_db()
    a = db.create_issue(title="batch-a")
    b = db.create_issue(title="batch-b")
    db.batch_update([a.id, b.id], priority=0)
    assert db.get_issue(a.id).priority == 0
    db.close()
```

**Step 2: Verify failure, Step 3: Create db_issues.py**

Extract:
- `create_issue` (lines 746-841)
- `get_issue` (lines 842-848)
- `_build_issue` (lines 849-856)
- `_build_issues_batch` (lines 857-953)
- `update_issue` (lines 954-1151)
- `close_issue` (lines 1152-1215)
- `reopen_issue` (lines 1216-1236)
- `claim_issue` (lines 1237-1292)
- `release_claim` (lines 1293-1316)
- `claim_next` (lines 1317-1349)
- `batch_close` (lines 1350-1371)
- `batch_update` (lines 1372-1405)
- `batch_add_label` (lines 1406-1432)
- `batch_add_comment` (lines 1433-1463)
- `list_issues` (lines 1464-1523)
- `search_issues` (lines 1524-1553)

Cross-domain calls: `create_issue` calls `self._record_event()`, `self._validate_status()`, `self._validate_label_name()`. All resolve via MRO.

**Step 4-6: Update core.py, run tests, lint, commit**

Run: `uv run pytest tests/ -x --tb=short` (full suite — this is the big one)

```bash
git commit -m "refactor: extract IssuesMixin from FiligreeDB"
```

---

## Task 6: Extract FilesMixin from core.py

File tracking, scan findings, associations, timeline — the largest single domain.

**Files:**
- Create: `src/filigree/db_files.py`
- Modify: `src/filigree/core.py`
- Test: `tests/test_module_split.py` (extend)

**Step 1: Add failing tests**

```python
from filigree.db_files import FilesMixin


def test_files_mixin_is_base_class():
    assert issubclass(FiligreeDB, FilesMixin)


def test_register_and_get_file():
    db = _make_db()
    f = db.register_file(path="src/example.py")
    retrieved = db.get_file(f.id)
    assert retrieved.path == "src/example.py"
    db.close()


def test_file_associations():
    db = _make_db()
    issue = db.create_issue(title="assoc-test")
    f = db.register_file(path="src/assoc.py")
    db.add_file_association(f.id, issue.id, assoc_type="bug_in")
    assocs = db.get_file_associations(f.id)
    assert len(assocs) == 1
    db.close()
```

**Step 2: Verify failure, Step 3: Create db_files.py**

Extract all 25 file-domain methods (lines 2463-3654). Move `_normalize_scan_path` (lines 141-333) too — it's only used by file methods.

Cross-domain: `_create_issue_for_finding` calls `self.create_issue()` (IssuesMixin). Resolves via `self`.

**Step 4-6: Update core.py, run full test suite, lint, commit**

Run: `uv run pytest tests/ -x --tb=short`

```bash
git commit -m "refactor: extract FilesMixin from FiligreeDB"
```

**Verification checkpoint:** After this task, `core.py` should be ~500 lines (down from 3,722). Run: `wc -l src/filigree/core.py` — expect <600.

---

## Task 7: Extract MCP tool modules from mcp_server.py

Split the 54-tool monolith into domain-grouped files.

**Files:**
- Create: `src/filigree/mcp_tools/__init__.py`
- Create: `src/filigree/mcp_tools/common.py`
- Create: `src/filigree/mcp_tools/issues.py`
- Create: `src/filigree/mcp_tools/planning.py`
- Create: `src/filigree/mcp_tools/files.py`
- Create: `src/filigree/mcp_tools/workflow.py`
- Create: `src/filigree/mcp_tools/meta.py`
- Modify: `src/filigree/mcp_server.py` (gut _dispatch and list_tools, replace with aggregation)
- Test: `tests/test_mcp.py` (existing — must still pass)

**Step 1: Write a structural test**

Add to `tests/test_module_split.py`:

```python
def test_mcp_tools_package_exists():
    from filigree.mcp_tools import issues, planning, files, workflow, meta
    # Each module should export a register() function
    for mod in [issues, planning, files, workflow, meta]:
        assert hasattr(mod, "register"), f"{mod.__name__} missing register()"
```

**Step 2: Verify failure**

Run: `uv run pytest tests/test_module_split.py::test_mcp_tools_package_exists -v`

**Step 3: Create mcp_tools/common.py**

Extract shared helpers from `mcp_server.py`:
- `_text()` (lines 128-131)
- `_get_db()` (lines 77-82) — make importable
- `_get_filigree_dir()` (lines 85-86)
- `_refresh_summary()` (lines 89-97)
- `_safe_path()` (lines 100-125)
- Constants: `_MAX_LIST_RESULTS`, `_SCAN_COOLDOWN_SECONDS`, `_scan_cooldowns`

**Step 4: Create mcp_tools/issues.py**

Move handler code for these 11 tools from `_dispatch()`:
`get_issue`, `list_issues`, `create_issue`, `update_issue`, `close_issue`, `reopen_issue`, `search_issues`, `claim_issue`, `release_claim`, `claim_next`, `batch_close`, `batch_update`

Each file exports:
```python
def register() -> tuple[list[Tool], dict[str, Callable]]:
    tools = [...]  # Tool schemas from list_tools()
    handlers = {...}  # name -> async handler
    return tools, handlers
```

**Step 5: Create remaining domain files**

- `mcp_tools/planning.py`: `add_dependency`, `remove_dependency`, `get_ready`, `get_blocked`, `get_plan`, `create_plan`, `get_critical_path`
- `mcp_tools/files.py`: `list_files`, `get_file`, `get_file_timeline`, `get_issue_files`, `add_file_association`, `register_file`, `list_scanners`, `trigger_scan`
- `mcp_tools/workflow.py`: `get_template`, `get_workflow_states`, `list_types`, `get_type_info`, `list_packs`, `get_valid_transitions`, `validate_issue`, `get_workflow_guide`, `explain_state`, `reload_templates`
- `mcp_tools/meta.py`: `add_comment`, `get_comments`, `add_label`, `remove_label`, `batch_add_label`, `batch_add_comment`, `get_changes`, `get_summary`, `get_stats`, `get_metrics`, `export_jsonl`, `import_jsonl`, `archive_closed`, `compact_events`, `undo_last`, `get_issue_events`

**Step 6: Update mcp_server.py**

Replace `list_tools()` and `_dispatch()` with aggregation:

```python
from filigree.mcp_tools import issues, planning, files, workflow, meta

_all_tools: list[Tool] = []
_all_handlers: dict[str, Any] = {}

for _mod in [issues, planning, files, workflow, meta]:
    _tools, _handlers = _mod.register()
    _all_tools.extend(_tools)
    _all_handlers.update(_handlers)

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return _all_tools

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = _all_handlers.get(name)
    if handler is None:
        return _text(f"Unknown tool: {name}")
    return await handler(arguments)
```

Keep resources, prompts, `create_mcp_app()`, and `main()` in `mcp_server.py`.

**Step 7: Run MCP tests**

Run: `uv run pytest tests/test_mcp.py -v --tb=short`
Expected: ALL PASS (tests call tools by name — the dispatch is transparent)

**Step 8: Lint + commit**

```bash
git add src/filigree/mcp_tools/ src/filigree/mcp_server.py tests/test_module_split.py
git commit -m "refactor: split MCP tools into domain modules"
```

**Verification:** `wc -l src/filigree/mcp_server.py` — expect <300.

---

## Task 8: Extract CLI command modules from cli.py

Split 62 Click commands into domain-grouped files.

**Files:**
- Create: `src/filigree/cli_commands/__init__.py`
- Create: `src/filigree/cli_commands/issues.py`
- Create: `src/filigree/cli_commands/planning.py`
- Create: `src/filigree/cli_commands/batch.py`
- Create: `src/filigree/cli_commands/workflow.py`
- Create: `src/filigree/cli_commands/meta.py`
- Create: `src/filigree/cli_commands/data.py`
- Create: `src/filigree/cli_commands/server.py`
- Create: `src/filigree/cli_commands/setup.py`
- Modify: `src/filigree/cli.py`
- Test: `tests/test_cli.py` (existing — must pass)

**Step 1: Write structural test**

```python
def test_cli_commands_package_exists():
    from filigree.cli_commands import (
        issues, planning, batch, workflow, meta, data, server, setup
    )
```

**Step 2: Create cli_commands/ package**

Each file defines Click commands. Example for `cli_commands/issues.py`:

```python
import click
from filigree.cli import _get_db, _refresh_summary, pass_actor

@click.command()
@click.argument("title")
# ... options ...
@pass_actor
def create(actor, title, ...):
    """Create a new issue."""
    db = _get_db()
    ...
```

Domain grouping:
- `issues.py`: create, show, list_issues, update, close, reopen
- `planning.py`: plan, add_dep, remove_dep, ready, blocked, critical_path
- `batch.py`: batch_update, batch_close, batch_add_label, batch_add_comment
- `workflow.py`: types_cmd, type_info, transitions_cmd, packs_cmd, validate_cmd, guide_cmd, explain_state, workflow_states, templates, templates_reload
- `meta.py`: add_comment, get_comments, add_label, remove_label, stats, search, undo, changes, events_cmd
- `data.py`: export_data, import_data, archive, clean_stale_findings, compact
- `server.py`: server (group), server_start, server_stop, server_status_cmd, server_register, server_unregister
- `setup.py`: init, install, doctor, migrate, dashboard, session_context, ensure_dashboard_cmd, metrics

**Step 3: Update cli.py**

Keep: `cli` group, `_get_db()`, `_refresh_summary()`, `pass_actor` decorator.

Add command registration:
```python
from filigree.cli_commands import issues, planning, batch, ...

cli.add_command(issues.create, "create")
cli.add_command(issues.show, "show")
# ...
```

**Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v --tb=short`

**Step 5: Commit**

```bash
git commit -m "refactor: split CLI commands into domain modules"
```

---

## Task 9: Extract dashboard route modules from dashboard.py

Split 38 API routes into domain-grouped FastAPI routers.

**Files:**
- Create: `src/filigree/dashboard_routes/__init__.py`
- Create: `src/filigree/dashboard_routes/common.py`
- Create: `src/filigree/dashboard_routes/issues.py`
- Create: `src/filigree/dashboard_routes/files.py`
- Create: `src/filigree/dashboard_routes/analytics.py`
- Create: `src/filigree/dashboard_routes/system.py`
- Modify: `src/filigree/dashboard.py`
- Test: `tests/test_dashboard.py` (existing — must pass)

**Step 1: Create dashboard_routes/common.py**

Extract helpers:
- `_error_response()` (lines 191-204)
- `_safe_int()` (lines 207-216)
- `_parse_bool_value()` (lines 225-236)
- `_parse_csv_param()` (lines 273-274)
- `_safe_bounded_int()` (lines 277-293)
- `_read_graph_runtime_config()` (lines 239-245)
- `_resolve_graph_runtime()` (lines 248-270)
- `_coerce_graph_mode()` (lines 296-308)

**Step 2: Create domain route files**

Each uses `APIRouter` and gets the DB via `Depends(_get_db)`:

```python
# dashboard_routes/issues.py
from fastapi import APIRouter, Depends, Request
from filigree.dashboard_routes.common import _error_response, _safe_int
from filigree.dashboard import _get_db

router = APIRouter()

@router.get("/api/issues")
async def list_issues(request: Request, db=Depends(_get_db)):
    ...
```

Route distribution:
- `issues.py` (20 routes): All `/api/issue*` and `/api/types`, `/api/search`, `/api/batch/*`, `/api/claim-next`
- `files.py` (11 routes): All `/api/files*` and `/api/v1/scan-results`, `/api/scan-runs`
- `analytics.py` (5 routes): `/api/graph`, `/api/stats`, `/api/metrics`, `/api/critical-path`, `/api/activity`
- `system.py` (2 routes): `/api/config`, `/api/files/_schema`

**Step 3: Update dashboard.py**

Replace `_create_project_router()` with router inclusion:

```python
from filigree.dashboard_routes import issues, files, analytics, system

def _create_project_router():
    router = APIRouter()
    router.include_router(issues.router)
    router.include_router(files.router)
    router.include_router(analytics.router)
    router.include_router(system.router)
    return router
```

Keep in `dashboard.py`: `ProjectStore`, `_get_db`, `create_app`, `main`, middleware.

**Step 4: Run dashboard tests**

Run: `uv run pytest tests/test_dashboard.py -v --tb=short`

**Step 5: Commit**

```bash
git commit -m "refactor: split dashboard routes into domain modules"
```

---

## Task 10: Extract install support modules

**Files:**
- Create: `src/filigree/install_support/__init__.py`
- Create: `src/filigree/install_support/doctor.py`
- Create: `src/filigree/install_support/hooks.py`
- Create: `src/filigree/install_support/integrations.py`
- Modify: `src/filigree/install.py`
- Test: `tests/test_install.py` (existing — must pass)

**Step 1: Create install_support/doctor.py**

Extract `run_doctor()` (lines 768-1168) and all `_doctor_*` helper functions.

**Step 2: Create install_support/hooks.py**

Extract `install_claude_code_hooks()` (lines 490-613) and helpers: `_hook_cmd_matches`, `_extract_hook_binary`.

**Step 3: Create install_support/integrations.py**

Extract: `install_codex_mcp`, `_install_mcp_ethereal_mode`, `_install_mcp_server_mode`.

**Step 4: Update install.py**

Import and delegate to support modules.

**Step 5: Run install tests, lint, commit**

Run: `uv run pytest tests/test_install.py -v --tb=short`

```bash
git commit -m "refactor: split install.py into support modules"
```

---

## Task 11: Final verification and cleanup

**Step 1: Run full test suite**

Run: `uv run pytest tests/ --tb=short -q`
Expected: ALL PASS, same test count as before refactoring

**Step 2: Run full linting pipeline**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
```

**Step 3: Verify file sizes**

Run: `find src/filigree -name "*.py" | xargs wc -l | sort -rn | head -20`
Expected: No file over 900 lines (db_files.py is the largest at ~900)

**Step 4: Verify public API unchanged**

```python
# Quick smoke test
from filigree.core import FiligreeDB, Issue, FileRecord, ScanFinding
from filigree.core import find_filigree_root, read_config, write_config
from filigree import FiligreeDB, Issue, __version__
```

**Step 5: Commit any cleanup**

```bash
git commit -m "refactor: final cleanup after module architecture split"
```
