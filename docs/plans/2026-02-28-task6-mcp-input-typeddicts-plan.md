# MCP Input TypedDicts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `arguments: dict[str, Any]` access in all 53 MCP handlers with TypedDicts so mypy can verify argument access at the handler level.

**Architecture:** Define TypedDicts in `src/filigree/types/inputs.py` (shared by MCP + dashboard layers). Each handler casts `dict[str, Any]` → TypedDict via a `_parse_args()` bridge in `common.py`. A parametrized sync test verifies TypedDict keys/required match JSON Schema properties/required — prevents silent drift.

**Tech Stack:** Python 3.11+ `TypedDict` with `Required`/`NotRequired`, `typing.cast`, `get_type_hints`, `__required_keys__`/`__optional_keys__` introspection.

---

## Scope Summary

| Module | Handlers | With Args | No Args (skip) |
|--------|----------|-----------|-----------------|
| issues.py | 12 | 12 | 0 |
| meta.py | 16 | 14 | 2 (get_summary, get_stats) |
| planning.py | 7 | 4 | 3 (get_ready, get_blocked, get_critical_path) |
| workflow.py | 10 | 6 | 4 (get_workflow_states, list_types, list_packs, reload_templates) |
| files.py | 8 | 7 | 1 (list_scanners) |
| **TOTAL** | **53** | **43** | **10** |

Plus 3 nested helper TypedDicts for `create_plan`: `MilestoneInput`, `PhaseInput`, `StepInput`.

**Grand total: 46 TypedDict classes.**

---

## Task 1: Sync Test Infrastructure + `_parse_args` Helper

**Files:**
- Create: `src/filigree/types/inputs.py`
- Modify: `src/filigree/mcp_tools/common.py`
- Create: `tests/util/test_input_type_contracts.py`

### Step 1: Create `types/inputs.py` skeleton

Create the module with the `TOOL_ARGS_MAP` registry (empty for now) and the import constraint header.

```python
# IMPORT CONSTRAINT: types/ modules must only import from typing, stdlib, and each other.
# NEVER import from core.py, db_base.py, or any mixin — this prevents circular imports.
"""TypedDict contracts for MCP tool handler input arguments.

Each TypedDict mirrors the JSON Schema ``inputSchema`` on the corresponding
``mcp.types.Tool`` definition.  The ``TOOL_ARGS_MAP`` registry maps tool names
to their TypedDict class so the sync test can verify structural agreement.

Safety note on cast():
    The MCP SDK validates argument presence/types against JSON Schema before
    handler invocation.  Core validates authoritatively.  The TypedDicts here
    are a *static-analysis* tool — ``cast()`` provides type narrowing only,
    not runtime validation.  Direct handler calls that bypass MCP SDK
    validation are unsafe — callers must pre-validate arguments.
"""

# NOTE: Do NOT add ``from __future__ import annotations`` to this module.
# It breaks TypedDict.__required_keys__ / __optional_keys__ introspection
# on Python <3.14, which the sync test in test_input_type_contracts.py
# depends on for verifying required/optional agreement with JSON Schema.

from typing import Any

# Registry: tool_name -> TypedDict class.
# Populated as TypedDicts are defined below.
# No-argument tools (empty inputSchema properties) are intentionally excluded.
TOOL_ARGS_MAP: dict[str, type] = {}
```

### Step 2: Add `_parse_args()` to `common.py`

Add at the top of `common.py`, after existing imports:

```python
from typing import Any, TypeVar, cast

_T = TypeVar("_T")


def _parse_args(arguments: dict[str, Any], cls: type[_T]) -> _T:
    """Cast MCP arguments to a typed dict for static analysis.

    Safety: MCP SDK validates argument presence/types against JSON Schema
    before handler invocation. Core validates authoritatively. This cast()
    provides mypy type narrowing only — no runtime validation.
    """
    return cast(_T, arguments)
```

Note: `common.py` already imports `Any` from `typing`. Add `TypeVar` and `cast` to that import. Keep the `_T` TypeVar and `_parse_args` near the top of the file, after the existing constants.

### Step 3: Write the parametrized sync test

Create `tests/util/test_input_type_contracts.py`:

```python
"""Sync test: MCP JSON Schema <-> TypedDict structural agreement.

Parametrized test that introspects each MCP tool's inputSchema and verifies
the corresponding TypedDict's keys and required/optional annotations match.
Analogous to test_mixin_contracts.py but for the MCP input boundary.

Scope: This test verifies *structural* agreement (key names and
required/optional status). It does NOT verify type-level agreement
(e.g. str vs int) — that is left to mypy via the TypedDict annotations.
"""

from __future__ import annotations

import importlib
from typing import Any, get_type_hints

import pytest
from mcp.types import Tool

from filigree.types.inputs import TOOL_ARGS_MAP

# ---------------------------------------------------------------------------
# Discovery: collect all MCP tools from all modules
# ---------------------------------------------------------------------------

_MCP_MODULES = [
    "filigree.mcp_tools.issues",
    "filigree.mcp_tools.planning",
    "filigree.mcp_tools.meta",
    "filigree.mcp_tools.workflow",
    "filigree.mcp_tools.files",
]


def _discover_tools() -> list[tuple[str, Tool]]:
    """Call register() on each MCP module, collect (tool_name, Tool) pairs."""
    result: list[tuple[str, Tool]] = []
    for mod_path in _MCP_MODULES:
        mod = importlib.import_module(mod_path)
        tools, _ = mod.register()
        for tool in tools:
            result.append((tool.name, tool))
    return result


_ALL_TOOLS = _discover_tools()

# Tools with non-empty properties (need TypedDicts)
_TOOLS_WITH_ARGS = [
    (name, tool)
    for name, tool in _ALL_TOOLS
    if tool.inputSchema.get("properties", {})
]

# Tools with empty properties (should NOT be in TOOL_ARGS_MAP)
_TOOLS_WITHOUT_ARGS = [
    (name, tool)
    for name, tool in _ALL_TOOLS
    if not tool.inputSchema.get("properties", {})
]


# ---------------------------------------------------------------------------
# Section 1: Structural sync — keys and required/optional
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,tool",
    _TOOLS_WITH_ARGS,
    ids=[name for name, _ in _TOOLS_WITH_ARGS],
)
class TestSchemaTypedDictSync:
    """Verify each tool's JSON Schema matches its TypedDict structurally."""

    def test_typeddict_registered(self, tool_name: str, tool: Tool) -> None:
        """Every tool with arguments must have a TypedDict in TOOL_ARGS_MAP."""
        assert tool_name in TOOL_ARGS_MAP, (
            f"Tool '{tool_name}' has inputSchema properties but no TypedDict "
            f"in TOOL_ARGS_MAP. Add one to types/inputs.py."
        )

    def test_keys_match(self, tool_name: str, tool: Tool) -> None:
        """TypedDict annotation keys == JSON Schema property keys."""
        if tool_name not in TOOL_ARGS_MAP:
            pytest.skip("No TypedDict registered yet")
        td_cls = TOOL_ARGS_MAP[tool_name]
        schema_keys = set(tool.inputSchema.get("properties", {}).keys())
        td_keys = set(get_type_hints(td_cls).keys())
        assert td_keys == schema_keys, (
            f"Key mismatch for '{tool_name}':\n"
            f"  TypedDict extra: {td_keys - schema_keys}\n"
            f"  Schema extra:    {schema_keys - td_keys}"
        )

    def test_required_fields_match(self, tool_name: str, tool: Tool) -> None:
        """TypedDict __required_keys__ == JSON Schema required array."""
        if tool_name not in TOOL_ARGS_MAP:
            pytest.skip("No TypedDict registered yet")
        td_cls = TOOL_ARGS_MAP[tool_name]
        schema_required = set(tool.inputSchema.get("required", []))
        # Python 3.11+ TypedDict exposes __required_keys__ / __optional_keys__
        td_required = td_cls.__required_keys__
        assert td_required == schema_required, (
            f"Required mismatch for '{tool_name}':\n"
            f"  TypedDict required: {td_required}\n"
            f"  Schema required:    {schema_required}"
        )

    def test_optional_fields_match(self, tool_name: str, tool: Tool) -> None:
        """TypedDict __optional_keys__ == schema properties minus required."""
        if tool_name not in TOOL_ARGS_MAP:
            pytest.skip("No TypedDict registered yet")
        td_cls = TOOL_ARGS_MAP[tool_name]
        schema_props = set(tool.inputSchema.get("properties", {}).keys())
        schema_required = set(tool.inputSchema.get("required", []))
        schema_optional = schema_props - schema_required
        td_optional = td_cls.__optional_keys__
        assert td_optional == schema_optional, (
            f"Optional mismatch for '{tool_name}':\n"
            f"  TypedDict optional: {td_optional}\n"
            f"  Schema optional:    {schema_optional}"
        )


# ---------------------------------------------------------------------------
# Section 2: No-arg tools should NOT be in the map
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,tool",
    _TOOLS_WITHOUT_ARGS,
    ids=[name for name, _ in _TOOLS_WITHOUT_ARGS],
)
def test_no_arg_tool_excluded(tool_name: str, tool: Tool) -> None:
    """Tools with empty inputSchema should not have a TypedDict mapping."""
    assert tool_name not in TOOL_ARGS_MAP, (
        f"Tool '{tool_name}' has no inputSchema properties but is "
        f"registered in TOOL_ARGS_MAP — remove it."
    )


# ---------------------------------------------------------------------------
# Section 3: Coverage guards
# ---------------------------------------------------------------------------


def test_all_mcp_modules_covered() -> None:
    """Ensure we're scanning all mcp_tools modules."""
    from pathlib import Path

    mcp_dir = Path(__file__).resolve().parents[2] / "src" / "filigree" / "mcp_tools"
    actual_modules = {
        f.stem for f in mcp_dir.glob("*.py") if f.stem not in ("__init__", "common")
    }
    scanned_modules = {m.rsplit(".", 1)[-1] for m in _MCP_MODULES}
    assert actual_modules == scanned_modules, (
        f"Module mismatch:\n"
        f"  On disk: {actual_modules}\n"
        f"  Scanned: {scanned_modules}"
    )


def test_tools_discovered() -> None:
    """Sanity: we find a reasonable number of tools."""
    assert len(_ALL_TOOLS) >= 50, (
        f"Expected >=50 tools, found {len(_ALL_TOOLS)}. "
        f"Did a module's register() break?"
    )


def test_args_map_not_empty() -> None:
    """Guard against vacuous parametrization — map should grow as tasks complete."""
    # This threshold increases as each task adds TypedDicts.
    # Final target: 43 (all tools with arguments).
    assert len(TOOL_ARGS_MAP) >= 0  # Start at 0, bump after each task
```

### Step 4: Run the sync test to verify it discovers tools but fails on missing TypedDicts

Run: `uv run pytest tests/util/test_input_type_contracts.py -v --tb=short 2>&1 | head -80`

Expected: `test_typeddict_registered` fails for all 43 tools-with-args. `test_no_arg_tool_excluded` passes for all 10 no-arg tools. Coverage guards pass.

### Step 5: Verify existing tests still pass

Run: `uv run pytest tests/ -x --tb=short -q`

Expected: All pass (new test failures are expected and isolated to the new test file).

### Step 6: Commit

```bash
git add src/filigree/types/inputs.py src/filigree/mcp_tools/common.py tests/util/test_input_type_contracts.py
git commit -m "feat(types): add sync test infrastructure for MCP input TypedDicts

Add types/inputs.py skeleton with TOOL_ARGS_MAP registry, _parse_args()
bridge helper in common.py, and parametrized sync test that verifies
TypedDict keys/required match JSON Schema inputSchema definitions."
```

---

## Task 2: Issue Domain TypedDicts (12 handlers)

**Files:**
- Modify: `src/filigree/types/inputs.py`
- Modify: `src/filigree/mcp_tools/issues.py`

### Step 1: Define 12 TypedDicts in `types/inputs.py`

Add after the existing `TOOL_ARGS_MAP` declaration:

```python
from typing import Any, NotRequired, TypedDict

# ---------------------------------------------------------------------------
# issues.py handlers
# ---------------------------------------------------------------------------


class GetIssueArgs(TypedDict):
    id: str
    include_transitions: NotRequired[bool]


class ListIssuesArgs(TypedDict):
    status: NotRequired[str]
    status_category: NotRequired[str]
    type: NotRequired[str]
    priority: NotRequired[int]
    parent_id: NotRequired[str]
    assignee: NotRequired[str]
    label: NotRequired[str]
    limit: NotRequired[int]
    offset: NotRequired[int]
    no_limit: NotRequired[bool]


class CreateIssueArgs(TypedDict):
    title: str
    type: NotRequired[str]
    priority: NotRequired[int]
    parent_id: NotRequired[str]
    description: NotRequired[str]
    notes: NotRequired[str]
    fields: NotRequired[dict[str, Any]]
    labels: NotRequired[list[str]]
    deps: NotRequired[list[str]]
    actor: NotRequired[str]


class UpdateIssueArgs(TypedDict):
    id: str
    status: NotRequired[str]
    priority: NotRequired[int]
    title: NotRequired[str]
    assignee: NotRequired[str]
    description: NotRequired[str]
    notes: NotRequired[str]
    parent_id: NotRequired[str]
    fields: NotRequired[dict[str, Any]]
    actor: NotRequired[str]


class CloseIssueArgs(TypedDict):
    id: str
    reason: NotRequired[str]
    actor: NotRequired[str]
    fields: NotRequired[dict[str, Any]]


class ReopenIssueArgs(TypedDict):
    id: str
    actor: NotRequired[str]


class SearchIssuesArgs(TypedDict):
    query: str
    limit: NotRequired[int]
    offset: NotRequired[int]
    no_limit: NotRequired[bool]


class ClaimIssueArgs(TypedDict):
    id: str
    assignee: str
    actor: NotRequired[str]


class ReleaseClaimArgs(TypedDict):
    id: str
    actor: NotRequired[str]


class ClaimNextArgs(TypedDict):
    assignee: str
    type: NotRequired[str]
    priority_min: NotRequired[int]
    priority_max: NotRequired[int]
    actor: NotRequired[str]


class BatchCloseArgs(TypedDict):
    ids: list[str]
    reason: NotRequired[str]
    actor: NotRequired[str]


class BatchUpdateArgs(TypedDict):
    ids: list[str]
    status: NotRequired[str]
    priority: NotRequired[int]
    assignee: NotRequired[str]
    fields: NotRequired[dict[str, Any]]
    actor: NotRequired[str]
```

Register them in `TOOL_ARGS_MAP`:

```python
TOOL_ARGS_MAP: dict[str, type] = {
    # issues.py
    "get_issue": GetIssueArgs,
    "list_issues": ListIssuesArgs,
    "create_issue": CreateIssueArgs,
    "update_issue": UpdateIssueArgs,
    "close_issue": CloseIssueArgs,
    "reopen_issue": ReopenIssueArgs,
    "search_issues": SearchIssuesArgs,
    "claim_issue": ClaimIssueArgs,
    "release_claim": ReleaseClaimArgs,
    "claim_next": ClaimNextArgs,
    "batch_close": BatchCloseArgs,
    "batch_update": BatchUpdateArgs,
}
```

### Step 2: Wire TypedDicts into `issues.py` handlers

Add import at top of `issues.py`:

```python
from filigree.mcp_tools.common import (
    ...,  # existing imports
    _parse_args,
)
from filigree.types.inputs import (
    BatchCloseArgs,
    BatchUpdateArgs,
    ClaimIssueArgs,
    ClaimNextArgs,
    CloseIssueArgs,
    CreateIssueArgs,
    GetIssueArgs,
    ListIssuesArgs,
    ReleaseClaimArgs,
    ReopenIssueArgs,
    SearchIssuesArgs,
    UpdateIssueArgs,
)
```

For each handler, add `args = _parse_args(arguments, XxxArgs)` as the first line, then replace all `arguments["key"]` and `arguments.get("key")` with `args["key"]` and `args.get("key")`.

**Pattern for every handler (showing `_handle_get_issue` as example):**

Before:
```python
async def _handle_get_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    try:
        issue = tracker.get_issue(arguments["id"])
        if arguments.get("include_transitions"):
            transitions = tracker.get_valid_transitions(arguments["id"])
```

After:
```python
async def _handle_get_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, GetIssueArgs)
    tracker = _get_db()
    try:
        issue = tracker.get_issue(args["id"])
        if args.get("include_transitions"):
            transitions = tracker.get_valid_transitions(args["id"])
```

**Important:** For handlers that call `_resolve_pagination(arguments)`, keep passing `arguments` (the original untyped dict) since `_resolve_pagination` takes `dict[str, Any]`:

```python
async def _handle_list_issues(arguments: dict[str, Any]) -> list[TextContent]:
    args = _parse_args(arguments, ListIssuesArgs)
    priority = args.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
    ...
    effective_limit, offset = _resolve_pagination(arguments)  # keep raw dict
```

Apply this pattern to all 12 handlers in issues.py:
- `_handle_get_issue` → `GetIssueArgs`
- `_handle_list_issues` → `ListIssuesArgs`
- `_handle_create_issue` → `CreateIssueArgs`
- `_handle_update_issue` → `UpdateIssueArgs`
- `_handle_close_issue` → `CloseIssueArgs`
- `_handle_reopen_issue` → `ReopenIssueArgs`
- `_handle_search_issues` → `SearchIssuesArgs`
- `_handle_claim_issue` → `ClaimIssueArgs`
- `_handle_release_claim` → `ReleaseClaimArgs`
- `_handle_claim_next` → `ClaimNextArgs`
- `_handle_batch_close` → `BatchCloseArgs`
- `_handle_batch_update` → `BatchUpdateArgs`

### Step 3: Run sync test for issues module

Run: `uv run pytest tests/util/test_input_type_contracts.py -v -k "issues" --tb=short`

Expected: All `test_typeddict_registered`, `test_keys_match`, `test_required_fields_match`, `test_optional_fields_match` pass for the 12 issue tools.

### Step 4: Run mypy on issues module

Run: `uv run mypy src/filigree/mcp_tools/issues.py src/filigree/types/inputs.py --strict`

Expected: Clean pass. If `_resolve_pagination(arguments)` flags a type error, keep it as `arguments` (raw dict).

### Step 5: Run full test suite

Run: `uv run pytest tests/ -x --tb=short -q`

Expected: All existing tests pass.

### Step 6: Commit

```bash
git add src/filigree/types/inputs.py src/filigree/mcp_tools/issues.py
git commit -m "feat(types): add input TypedDicts for 12 issue MCP handlers

Define GetIssueArgs through BatchUpdateArgs in types/inputs.py.
Wire into handlers via _parse_args() bridge. Sync test passes."
```

---

## Task 3: Meta Domain TypedDicts (14 handlers)

**Files:**
- Modify: `src/filigree/types/inputs.py`
- Modify: `src/filigree/mcp_tools/meta.py`

### Step 1: Define 14 TypedDicts in `types/inputs.py`

Add after the issues section:

```python
# ---------------------------------------------------------------------------
# meta.py handlers
# ---------------------------------------------------------------------------


class AddCommentArgs(TypedDict):
    issue_id: str
    text: str
    actor: NotRequired[str]


class GetCommentsArgs(TypedDict):
    issue_id: str


class AddLabelArgs(TypedDict):
    issue_id: str
    label: str


class RemoveLabelArgs(TypedDict):
    issue_id: str
    label: str


class BatchAddLabelArgs(TypedDict):
    ids: list[str]
    label: str
    actor: NotRequired[str]


class BatchAddCommentArgs(TypedDict):
    ids: list[str]
    text: str
    actor: NotRequired[str]


class GetChangesArgs(TypedDict):
    since: str
    limit: NotRequired[int]


class GetMetricsArgs(TypedDict):
    days: NotRequired[int]


class ExportJsonlArgs(TypedDict):
    output_path: str


class ImportJsonlArgs(TypedDict):
    input_path: str
    merge: NotRequired[bool]


class ArchiveClosedArgs(TypedDict):
    days_old: NotRequired[int]
    actor: NotRequired[str]


class CompactEventsArgs(TypedDict):
    keep_recent: NotRequired[int]


class UndoLastArgs(TypedDict):
    id: str
    actor: NotRequired[str]


class GetIssueEventsArgs(TypedDict):
    issue_id: str
    limit: NotRequired[int]
```

Register in `TOOL_ARGS_MAP` (add to existing dict):

```python
    # meta.py
    "add_comment": AddCommentArgs,
    "get_comments": GetCommentsArgs,
    "add_label": AddLabelArgs,
    "remove_label": RemoveLabelArgs,
    "batch_add_label": BatchAddLabelArgs,
    "batch_add_comment": BatchAddCommentArgs,
    "get_changes": GetChangesArgs,
    "get_metrics": GetMetricsArgs,
    "export_jsonl": ExportJsonlArgs,
    "import_jsonl": ImportJsonlArgs,
    "archive_closed": ArchiveClosedArgs,
    "compact_events": CompactEventsArgs,
    "undo_last": UndoLastArgs,
    "get_issue_events": GetIssueEventsArgs,
```

### Step 2: Wire into `meta.py` handlers

Add imports to `meta.py`:

```python
from filigree.mcp_tools.common import _parse_args, _text, _validate_actor
from filigree.types.inputs import (
    AddCommentArgs,
    AddLabelArgs,
    ArchiveClosedArgs,
    BatchAddCommentArgs,
    BatchAddLabelArgs,
    CompactEventsArgs,
    ExportJsonlArgs,
    GetChangesArgs,
    GetCommentsArgs,
    GetIssueEventsArgs,
    GetMetricsArgs,
    ImportJsonlArgs,
    RemoveLabelArgs,
    UndoLastArgs,
)
```

Apply the `args = _parse_args(arguments, XxxArgs)` pattern to all 14 handlers. Skip `_handle_get_summary` and `_handle_get_stats` (they don't access arguments).

Handler → TypedDict mapping:
- `_handle_add_comment` → `AddCommentArgs`
- `_handle_get_comments` → `GetCommentsArgs`
- `_handle_add_label` → `AddLabelArgs`
- `_handle_remove_label` → `RemoveLabelArgs`
- `_handle_batch_add_label` → `BatchAddLabelArgs`
- `_handle_batch_add_comment` → `BatchAddCommentArgs`
- `_handle_get_changes` → `GetChangesArgs`
- `_handle_get_metrics` → `GetMetricsArgs`
- `_handle_export_jsonl` → `ExportJsonlArgs`
- `_handle_import_jsonl` → `ImportJsonlArgs`
- `_handle_archive_closed` → `ArchiveClosedArgs`
- `_handle_compact_events` → `CompactEventsArgs`
- `_handle_undo_last` → `UndoLastArgs`
- `_handle_get_issue_events` → `GetIssueEventsArgs`

### Step 3: Run sync test + mypy + full tests

```bash
uv run pytest tests/util/test_input_type_contracts.py -v --tb=short
uv run mypy src/filigree/mcp_tools/meta.py src/filigree/types/inputs.py --strict
uv run pytest tests/ -x --tb=short -q
```

### Step 4: Commit

```bash
git add src/filigree/types/inputs.py src/filigree/mcp_tools/meta.py
git commit -m "feat(types): add input TypedDicts for 14 meta MCP handlers

Define AddCommentArgs through GetIssueEventsArgs. Wire via _parse_args()."
```

---

## Task 4: Planning Domain TypedDicts (4 handlers + 3 nested helpers)

**Files:**
- Modify: `src/filigree/types/inputs.py`
- Modify: `src/filigree/mcp_tools/planning.py`

### Step 1: Define TypedDicts including nested structures

The `create_plan` handler has deeply nested schemas (milestone → phases → steps). Define sub-TypedDicts:

```python
# ---------------------------------------------------------------------------
# planning.py handlers
# ---------------------------------------------------------------------------


class AddDependencyArgs(TypedDict):
    from_id: str
    to_id: str
    actor: NotRequired[str]


class RemoveDependencyArgs(TypedDict):
    from_id: str
    to_id: str
    actor: NotRequired[str]


class GetPlanArgs(TypedDict):
    milestone_id: str


class StepInput(TypedDict):
    title: str
    priority: NotRequired[int]
    description: NotRequired[str]
    deps: NotRequired[list[Any]]


class PhaseInput(TypedDict):
    title: str
    priority: NotRequired[int]
    description: NotRequired[str]
    steps: NotRequired[list[StepInput]]


class MilestoneInput(TypedDict):
    title: str
    priority: NotRequired[int]
    description: NotRequired[str]


class CreatePlanArgs(TypedDict):
    milestone: MilestoneInput
    phases: list[PhaseInput]
    actor: NotRequired[str]
```

Register in `TOOL_ARGS_MAP`:

```python
    # planning.py
    "add_dependency": AddDependencyArgs,
    "remove_dependency": RemoveDependencyArgs,
    "get_plan": GetPlanArgs,
    "create_plan": CreatePlanArgs,
```

**Note:** `StepInput`, `PhaseInput`, `MilestoneInput` are NOT in `TOOL_ARGS_MAP` — they're nested helper types, not top-level tool inputs. The sync test only checks top-level keys of `CreatePlanArgs` (`milestone`, `phases`, `actor`).

### Step 2: Wire into `planning.py` handlers

Add imports to `planning.py`:

```python
from filigree.mcp_tools.common import _parse_args, _slim_issue, _text, _validate_actor, _validate_int_range
from filigree.types.inputs import (
    AddDependencyArgs,
    CreatePlanArgs,
    GetPlanArgs,
    RemoveDependencyArgs,
)
```

Apply pattern to 4 handlers (skip `get_ready`, `get_blocked`, `get_critical_path`):
- `_handle_add_dependency` → `AddDependencyArgs`
- `_handle_remove_dependency` → `RemoveDependencyArgs`
- `_handle_get_plan` → `GetPlanArgs`
- `_handle_create_plan` → `CreatePlanArgs`

For `_handle_create_plan`, the nested access changes:

Before:
```python
    milestone = arguments["milestone"]
    err = _validate_int_range(milestone.get("priority"), ...)
    for pi, phase in enumerate(arguments.get("phases", [])):
```

After:
```python
    args = _parse_args(arguments, CreatePlanArgs)
    milestone = args["milestone"]
    err = _validate_int_range(milestone.get("priority"), ...)
    for pi, phase in enumerate(args["phases"]):  # phases is Required, no default needed
```

### Step 3: Run sync test + mypy + full tests

```bash
uv run pytest tests/util/test_input_type_contracts.py -v -k "planning or dependency or plan" --tb=short
uv run mypy src/filigree/mcp_tools/planning.py src/filigree/types/inputs.py --strict
uv run pytest tests/ -x --tb=short -q
```

### Step 4: Commit

```bash
git add src/filigree/types/inputs.py src/filigree/mcp_tools/planning.py
git commit -m "feat(types): add input TypedDicts for 4 planning MCP handlers

Define AddDependencyArgs, RemoveDependencyArgs, GetPlanArgs, CreatePlanArgs
with nested MilestoneInput/PhaseInput/StepInput sub-types."
```

---

## Task 5: Workflow Domain TypedDicts (6 handlers)

**Files:**
- Modify: `src/filigree/types/inputs.py`
- Modify: `src/filigree/mcp_tools/workflow.py`

### Step 1: Define 6 TypedDicts

```python
# ---------------------------------------------------------------------------
# workflow.py handlers
# ---------------------------------------------------------------------------


class GetTemplateArgs(TypedDict):
    type: str


class GetTypeInfoArgs(TypedDict):
    type: str


class GetValidTransitionsArgs(TypedDict):
    issue_id: str


class ValidateIssueArgs(TypedDict):
    issue_id: str


class GetWorkflowGuideArgs(TypedDict):
    pack: str


class ExplainStateArgs(TypedDict):
    type: str
    state: str
```

Register in `TOOL_ARGS_MAP`:

```python
    # workflow.py
    "get_template": GetTemplateArgs,
    "get_type_info": GetTypeInfoArgs,
    "get_valid_transitions": GetValidTransitionsArgs,
    "validate_issue": ValidateIssueArgs,
    "get_workflow_guide": GetWorkflowGuideArgs,
    "explain_state": ExplainStateArgs,
```

### Step 2: Wire into `workflow.py` handlers

Add imports to `workflow.py`:

```python
from filigree.mcp_tools.common import _parse_args, _text
from filigree.types.inputs import (
    ExplainStateArgs,
    GetTemplateArgs,
    GetTypeInfoArgs,
    GetValidTransitionsArgs,
    GetWorkflowGuideArgs,
    ValidateIssueArgs,
)
```

Apply pattern to 6 handlers (skip `get_workflow_states`, `list_types`, `list_packs`, `reload_templates`):
- `_handle_get_template` → `GetTemplateArgs`
- `_handle_get_type_info` → `GetTypeInfoArgs`
- `_handle_get_valid_transitions` → `GetValidTransitionsArgs`
- `_handle_validate_issue` → `ValidateIssueArgs`
- `_handle_get_workflow_guide` → `GetWorkflowGuideArgs`
- `_handle_explain_state` → `ExplainStateArgs`

### Step 3: Run sync test + mypy + full tests

```bash
uv run pytest tests/util/test_input_type_contracts.py -v -k "workflow or template or transition or validate or explain or pack or guide" --tb=short
uv run mypy src/filigree/mcp_tools/workflow.py src/filigree/types/inputs.py --strict
uv run pytest tests/ -x --tb=short -q
```

### Step 4: Commit

```bash
git add src/filigree/types/inputs.py src/filigree/mcp_tools/workflow.py
git commit -m "feat(types): add input TypedDicts for 6 workflow MCP handlers

Define GetTemplateArgs through ExplainStateArgs. Wire via _parse_args()."
```

---

## Task 6: Files Domain TypedDicts (7 handlers)

**Files:**
- Modify: `src/filigree/types/inputs.py`
- Modify: `src/filigree/mcp_tools/files.py`

### Step 1: Define 7 TypedDicts

```python
# ---------------------------------------------------------------------------
# files.py handlers
# ---------------------------------------------------------------------------


class ListFilesArgs(TypedDict):
    limit: NotRequired[int]
    offset: NotRequired[int]
    language: NotRequired[str]
    path_prefix: NotRequired[str]
    min_findings: NotRequired[int]
    has_severity: NotRequired[str]
    scan_source: NotRequired[str]
    sort: NotRequired[str]
    direction: NotRequired[str]


class GetFileArgs(TypedDict):
    file_id: str


class GetFileTimelineArgs(TypedDict):
    file_id: str
    limit: NotRequired[int]
    offset: NotRequired[int]
    event_type: NotRequired[str]


class GetIssueFilesArgs(TypedDict):
    issue_id: str


class AddFileAssociationArgs(TypedDict):
    file_id: str
    issue_id: str
    assoc_type: str


class RegisterFileArgs(TypedDict):
    path: str
    language: NotRequired[str]
    file_type: NotRequired[str]
    metadata: NotRequired[dict[str, Any]]


class TriggerScanArgs(TypedDict):
    scanner: str
    file_path: str
    api_url: NotRequired[str]
```

Register in `TOOL_ARGS_MAP`:

```python
    # files.py
    "list_files": ListFilesArgs,
    "get_file": GetFileArgs,
    "get_file_timeline": GetFileTimelineArgs,
    "get_issue_files": GetIssueFilesArgs,
    "add_file_association": AddFileAssociationArgs,
    "register_file": RegisterFileArgs,
    "trigger_scan": TriggerScanArgs,
```

### Step 2: Wire into `files.py` handlers

Add imports to `files.py`:

```python
from filigree.mcp_tools.common import _parse_args, _text, _validate_int_range, _validate_str
from filigree.types.inputs import (
    AddFileAssociationArgs,
    GetFileArgs,
    GetFileTimelineArgs,
    GetIssueFilesArgs,
    ListFilesArgs,
    RegisterFileArgs,
    TriggerScanArgs,
)
```

Apply pattern to 7 handlers (skip `list_scanners`):
- `_handle_list_files` → `ListFilesArgs`
- `_handle_get_file` → `GetFileArgs`
- `_handle_get_file_timeline` → `GetFileTimelineArgs`
- `_handle_get_issue_files` → `GetIssueFilesArgs`
- `_handle_add_file_association` → `AddFileAssociationArgs`
- `_handle_register_file` → `RegisterFileArgs`
- `_handle_trigger_scan` → `TriggerScanArgs`

### Step 3: Run sync test + mypy + full tests

```bash
uv run pytest tests/util/test_input_type_contracts.py -v --tb=short
uv run mypy src/filigree/mcp_tools/files.py src/filigree/types/inputs.py --strict
uv run pytest tests/ -x --tb=short -q
```

### Step 4: Commit

```bash
git add src/filigree/types/inputs.py src/filigree/mcp_tools/files.py
git commit -m "feat(types): add input TypedDicts for 7 files MCP handlers

Define ListFilesArgs through TriggerScanArgs. Wire via _parse_args().
All 43 tools-with-args now have TypedDicts — sync test fully green."
```

---

## Task 7: Final Verification + Cleanup

**Files:**
- Modify: `src/filigree/types/__init__.py` (add exports if needed)
- Modify: `tests/util/test_input_type_contracts.py` (bump coverage guard)

### Step 1: Update `types/__init__.py`

The input TypedDicts are MCP-layer types imported directly by handler modules. They do NOT need to be re-exported from `types/__init__.py` unless dashboard routes start importing them. **Skip this step unless dashboard imports are needed.**

However, `TOOL_ARGS_MAP` should be accessible. Verify the import constraint test (`test_types_module_import_constraint` in `test_type_contracts.py`) passes for the new `inputs.py` module.

### Step 2: Bump sync test coverage guard

In `tests/util/test_input_type_contracts.py`, update:

```python
def test_args_map_not_empty() -> None:
    assert len(TOOL_ARGS_MAP) >= 43  # All tools-with-args covered
```

### Step 3: Run full CI pipeline

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short
```

All four must pass clean.

### Step 4: Verify the sync test catches drift

Quick smoke test: temporarily add a fake key to one TypedDict, run the sync test, verify it fails. Remove the fake key.

### Step 5: Commit

```bash
git add tests/util/test_input_type_contracts.py
git commit -m "chore: bump input TypedDict sync test coverage guard to 43

All 43 MCP tools-with-args now have TypedDicts with structural
sync verification. Closes the MCP input TypedDict task."
```

---

## Appendix: Complete TypedDict ↔ Tool Name Mapping

For reference, the final `TOOL_ARGS_MAP` should contain exactly these 43 entries:

| Tool Name | TypedDict Class | Module | Required Keys |
|-----------|----------------|--------|---------------|
| `get_issue` | `GetIssueArgs` | issues | `id` |
| `list_issues` | `ListIssuesArgs` | issues | *(none)* |
| `create_issue` | `CreateIssueArgs` | issues | `title` |
| `update_issue` | `UpdateIssueArgs` | issues | `id` |
| `close_issue` | `CloseIssueArgs` | issues | `id` |
| `reopen_issue` | `ReopenIssueArgs` | issues | `id` |
| `search_issues` | `SearchIssuesArgs` | issues | `query` |
| `claim_issue` | `ClaimIssueArgs` | issues | `id`, `assignee` |
| `release_claim` | `ReleaseClaimArgs` | issues | `id` |
| `claim_next` | `ClaimNextArgs` | issues | `assignee` |
| `batch_close` | `BatchCloseArgs` | issues | `ids` |
| `batch_update` | `BatchUpdateArgs` | issues | `ids` |
| `add_comment` | `AddCommentArgs` | meta | `issue_id`, `text` |
| `get_comments` | `GetCommentsArgs` | meta | `issue_id` |
| `add_label` | `AddLabelArgs` | meta | `issue_id`, `label` |
| `remove_label` | `RemoveLabelArgs` | meta | `issue_id`, `label` |
| `batch_add_label` | `BatchAddLabelArgs` | meta | `ids`, `label` |
| `batch_add_comment` | `BatchAddCommentArgs` | meta | `ids`, `text` |
| `get_changes` | `GetChangesArgs` | meta | `since` |
| `get_metrics` | `GetMetricsArgs` | meta | *(none)* |
| `export_jsonl` | `ExportJsonlArgs` | meta | `output_path` |
| `import_jsonl` | `ImportJsonlArgs` | meta | `input_path` |
| `archive_closed` | `ArchiveClosedArgs` | meta | *(none)* |
| `compact_events` | `CompactEventsArgs` | meta | *(none)* |
| `undo_last` | `UndoLastArgs` | meta | `id` |
| `get_issue_events` | `GetIssueEventsArgs` | meta | `issue_id` |
| `add_dependency` | `AddDependencyArgs` | planning | `from_id`, `to_id` |
| `remove_dependency` | `RemoveDependencyArgs` | planning | `from_id`, `to_id` |
| `get_plan` | `GetPlanArgs` | planning | `milestone_id` |
| `create_plan` | `CreatePlanArgs` | planning | `milestone`, `phases` |
| `get_template` | `GetTemplateArgs` | workflow | `type` |
| `get_type_info` | `GetTypeInfoArgs` | workflow | `type` |
| `get_valid_transitions` | `GetValidTransitionsArgs` | workflow | `issue_id` |
| `validate_issue` | `ValidateIssueArgs` | workflow | `issue_id` |
| `get_workflow_guide` | `GetWorkflowGuideArgs` | workflow | `pack` |
| `explain_state` | `ExplainStateArgs` | workflow | `type`, `state` |
| `list_files` | `ListFilesArgs` | files | *(none)* |
| `get_file` | `GetFileArgs` | files | `file_id` |
| `get_file_timeline` | `GetFileTimelineArgs` | files | `file_id` |
| `get_issue_files` | `GetIssueFilesArgs` | files | `issue_id` |
| `add_file_association` | `AddFileAssociationArgs` | files | `file_id`, `issue_id`, `assoc_type` |
| `register_file` | `RegisterFileArgs` | files | `path` |
| `trigger_scan` | `TriggerScanArgs` | files | `scanner`, `file_path` |

**10 no-arg tools excluded:** `get_ready`, `get_blocked`, `get_critical_path`, `get_summary`, `get_stats`, `get_workflow_states`, `list_types`, `list_packs`, `reload_templates`, `list_scanners`.

---

## Appendix: Known Risks & Mitigations

1. **`cast()` provides no runtime safety.** Mitigated by MCP SDK JSON Schema pre-validation + core authoritative validation (Task 4 boundary validation already landed). The `_parse_args()` docstring documents this assumption explicitly.

2. **`_resolve_pagination(arguments)` type compatibility.** If mypy flags passing a TypedDict to `dict[str, Any]`, pass the original `arguments` to pagination helpers. This is noted in Task 2.

3. **Nested schemas in `create_plan`.** The sync test only checks top-level keys. Nested TypedDicts (`MilestoneInput`, etc.) are verified by mypy structural typing, not by the sync test.

4. **Import constraint.** `types/inputs.py` must NOT import from `core.py`, `db_base.py`, or any mixin. Only `typing` and `stdlib` imports. The existing AST-based import constraint test in `test_type_contracts.py` covers this automatically.
