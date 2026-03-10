# Task 3: MCP & Dashboard API Response TypedDicts — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add TypedDicts for MCP tool handler and dashboard route responses that construct novel shapes (mutation sites, list/batch envelopes, error shapes).

**Prerequisites:** Tasks 1A, 1B, and 1C must be complete. Specifically: `IssueDict` from `types.core`, and `StatsResult`, `CommentRecord`, `PlanPhase` from `types.planning` must exist.

**Architecture:** Single new `types/api.py` module with 17 TypedDicts serving both MCP and dashboard layers. Uses `NotRequired` on individual optional fields for flat inheritance types (not `total=False` on the class, which would make inherited keys optional). Uses the split-base pattern for envelope types with mixed required/optional keys (matching convention in `types/workflow.py`). Handler code refactored from dict mutation to typed construction.

**Wire format note:** One deliberate additive change: `newly_unblocked` entries in close/batch-close responses gain a `status` key (4→5 keys) for consistency with `SlimIssue`. This is backward-compatible for consumers that ignore unknown keys.

**Tech Stack:** Python 3.11+ TypedDicts with `NotRequired`, mypy strict, ruff, pytest

**Design doc:** `docs/plans/2026-02-28-task3-mcp-dashboard-typeddicts-design.md`

---

### Task 1: Create `types/api.py` with all TypedDict definitions

**Files:**
- Create: `src/filigree/types/api.py`
- Modify: `src/filigree/types/__init__.py`
- Test: `tests/util/test_type_contracts.py` (import constraint test auto-covers new file)

**Context:** This is the foundational file. All subsequent tasks import from here. The existing `types/` subpackage has a strict import constraint: modules in `types/` must only import from `typing`, stdlib, and each other — never from `core.py`, `db_base.py`, or any mixin. An AST-based parametrized test in `tests/util/test_type_contracts.py` enforces this automatically for all `types/*.py` files.

**Step 1: Write the failing test**

Add a basic import test to verify the module exists and exports the expected types.

```python
# In tests/util/test_type_contracts.py, add to imports at top:
from filigree.types.api import (
    BatchCloseResponse,
    BatchUpdateResponse,
    ClaimNextResponse,
    DepDetail,
    EnrichedIssueDetail,
    ErrorResponse,
    IssueDetailEvent,
    IssueListResponse,
    IssueWithChangedFields,
    IssueWithTransitions,
    IssueWithUnblocked,
    PlanResponse,
    SearchResponse,
    SlimIssue,
    StatsWithPrefix,
    TransitionError,
)

# Add a test class near the other shape tests:
class TestSlimIssueShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        hints = get_type_hints(SlimIssue)
        assert set(hints.keys()) == {"id", "title", "status", "priority", "type"}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/util/test_type_contracts.py::TestSlimIssueShape -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'filigree.types.api'`

**Step 3: Write the full `types/api.py` module**

Create `src/filigree/types/api.py` with these exact contents:

```python
"""TypedDicts for MCP tool handler and dashboard route API responses."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from filigree.types.core import IssueDict
from filigree.types.planning import CommentRecord, PlanPhase, StatsResult


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


class SlimIssue(TypedDict):
    """Reduced 5-key issue shape for search results and unblocked lists."""

    id: str
    title: str
    status: str
    priority: int
    type: str


class ErrorResponse(TypedDict):
    """Standard error envelope returned by MCP/dashboard error paths."""

    error: str
    code: str


class TransitionError(TypedDict):
    """Extended error for invalid status transitions.

    Includes valid_transitions hint to guide the caller toward correct states.
    """

    error: str
    code: Literal["invalid_transition"]
    valid_transitions: NotRequired[list[dict[str, Any]]]
    hint: NotRequired[str]


# ---------------------------------------------------------------------------
# Flat inheritance — IssueDict + extra keys (preserves wire format)
#
# IMPORTANT: Never use `class Foo(IssueDict, total=False)` — this makes ALL
# inherited IssueDict keys optional to mypy, defeating type safety. Instead
# use `NotRequired` on individual optional fields.
#
# NOTE on **spread: `Foo(**issue.to_dict(), extra=val)` silently passes through
# any extra keys that to_dict() might add in the future. The shape contract
# tests in test_type_contracts.py catch drift by asserting exact key-set equality.
# ---------------------------------------------------------------------------


class IssueWithTransitions(IssueDict):
    """Issue detail with optional valid_transitions (MCP get_issue)."""

    valid_transitions: NotRequired[list[dict[str, Any]]]


class IssueWithChangedFields(IssueDict):
    """Issue update response with list of changed field names."""

    changed_fields: list[str]


class IssueWithUnblocked(IssueDict):
    """Issue close response with optional newly-unblocked issues."""

    newly_unblocked: NotRequired[list[SlimIssue]]


class ClaimNextResponse(IssueDict):
    """Claimed issue with human-readable selection reason."""

    selection_reason: str


# ---------------------------------------------------------------------------
# Flat inheritance — StatsResult + prefix
# ---------------------------------------------------------------------------


class StatsWithPrefix(StatsResult):
    """Project stats with project prefix for dashboard display."""

    prefix: str


# ---------------------------------------------------------------------------
# Dashboard detail — IssueDict + dep/event/comment data
# ---------------------------------------------------------------------------


class DepDetail(TypedDict):
    """Minimal dependency info for dep_details lookup in issue detail."""

    title: str
    status: str
    status_category: str
    priority: int


class IssueDetailEvent(TypedDict):
    """Slim 5-column projection of EventRecord — only the columns selected by
    api_issue_detail's SQL query. Do NOT extend to full EventRecord; that is
    a separate type in types/events.py."""

    event_type: str
    actor: str
    old_value: str | None
    new_value: str | None
    created_at: str


class EnrichedIssueDetail(IssueDict):
    """Full issue detail with dependency info, events, and comments."""

    dep_details: dict[str, DepDetail]
    events: list[IssueDetailEvent]
    comments: list[CommentRecord]


# ---------------------------------------------------------------------------
# True envelopes — list / search / batch wrappers
#
# For types with mixed required/optional keys, use the split-base pattern
# matching the convention in types/workflow.py (FieldSchemaInfo).
# ---------------------------------------------------------------------------


class IssueListResponse(TypedDict):
    """Paginated issue list (MCP list_issues)."""

    issues: list[IssueDict]
    limit: int
    offset: int
    has_more: bool


class SearchResponse(TypedDict):
    """Paginated search results with slim issues (MCP search_issues)."""

    issues: list[SlimIssue]
    limit: int
    offset: int
    has_more: bool


class BatchUpdateResponse(TypedDict):
    """Batch update result with succeeded IDs and failures."""

    succeeded: list[str]
    failed: list[dict[str, Any]]
    count: int


class _BatchCloseRequired(TypedDict):
    """Required keys for BatchCloseResponse (always present)."""

    succeeded: list[str]
    failed: list[dict[str, Any]]
    count: int


class BatchCloseResponse(_BatchCloseRequired, total=False):
    """Batch close result with optional newly-unblocked list.

    ``succeeded``, ``failed``, and ``count`` are always present (enforced
    by ``_BatchCloseRequired``). ``newly_unblocked`` is only included when
    issues were actually unblocked.
    """

    newly_unblocked: list[SlimIssue]


class PlanResponse(TypedDict):
    """Plan tree with computed progress percentage (MCP get_plan)."""

    milestone: dict[str, Any]
    phases: list[PlanPhase]
    total_steps: int
    completed_steps: int
    progress_pct: float
```

**Important notes on `BatchCloseResponse`:** It uses the split-base pattern (`_BatchCloseRequired` + `BatchCloseResponse(total=False)`) to keep `succeeded`, `failed`, and `count` as required keys while making `newly_unblocked` optional. This matches the established convention in `types/workflow.py` (`_FieldSchemaRequired` + `FieldSchemaInfo`). Do NOT use `total=False` directly on a TypedDict with required keys — mypy would treat all keys as optional, silently accepting empty construction.

**Also note `IssueDetailEvent`:** The dashboard `api_issue_detail` handler runs a raw SQL query that selects only 5 columns (`event_type, actor, old_value, new_value, created_at`), NOT the full 8-column `EventRecord`. So we need a separate slim type. Do NOT reuse `EventRecord` here.

**Step 4: Update `types/__init__.py` re-exports**

Add the new types to the import block and `__all__` list in `src/filigree/types/__init__.py`:

```python
from filigree.types.api import (
    BatchCloseResponse,
    BatchUpdateResponse,
    ClaimNextResponse,
    DepDetail,
    EnrichedIssueDetail,
    ErrorResponse,
    IssueDetailEvent,
    IssueListResponse,
    IssueWithChangedFields,
    IssueWithTransitions,
    IssueWithUnblocked,
    PlanResponse,
    SearchResponse,
    SlimIssue,
    StatsWithPrefix,
    TransitionError,
)
```

And add all names to `__all__` in alphabetical order. Keep the imports in a single continuous block (no blank lines between `filigree.types.*` imports) to satisfy ruff's I001 isort rule.

**Step 5: Run tests to verify**

Run: `uv run pytest tests/util/test_type_contracts.py -v`
Expected: PASS (including the new `TestSlimIssueShape` and the import constraint parametrized test for `api.py`)

Run: `uv run ruff check src/filigree/types/ && uv run mypy src/filigree/types/`
Expected: All checks passed, no mypy errors

**Step 6: Commit**

```bash
git add src/filigree/types/api.py src/filigree/types/__init__.py tests/util/test_type_contracts.py
git commit -m "feat(types): add api.py with 17 MCP/dashboard response TypedDicts"
```

---

### Task 2: Refactor MCP issue handlers to use typed responses

**Files:**
- Modify: `src/filigree/mcp_tools/common.py` (return type of `_slim_issue`, `_build_transition_error`)
- Modify: `src/filigree/mcp_tools/issues.py` (5 handlers)
- Test: existing `tests/mcp/` tests serve as regression

**Context:** The 5 MCP issue handlers that mutate `to_dict()` results need refactoring. The pattern is: replace `dict[str, Any]` intermediaries with typed construction. The `_slim_issue()` helper in `common.py` currently returns `dict[str, Any]` — change it to return `SlimIssue`. Similarly `_build_transition_error()` returns `dict[str, Any]` — change to `TransitionError`.

**Import convention:** All `from filigree.types.api import ...` statements must go at the **module level** (top of the file), not inside handler function bodies. The inline imports shown in individual steps below are for clarity — the delivered code must consolidate them into a single module-level import block per file.

**Step 1: Update `_slim_issue()` return type**

In `src/filigree/mcp_tools/common.py`, change:

```python
# Before (line 27):
def _slim_issue(issue: Issue) -> dict[str, Any]:

# After:
from filigree.types.api import SlimIssue

def _slim_issue(issue: Issue) -> SlimIssue:
```

The body already constructs the exact keys, so it needs a `cast()` or explicit `SlimIssue(...)` constructor:

```python
def _slim_issue(issue: Issue) -> SlimIssue:
    """Return a lightweight dict for search result listings."""
    return SlimIssue(
        id=issue.id,
        title=issue.title,
        status=issue.status,
        priority=issue.priority,
        type=issue.type,
    )
```

**Step 2: Update `_build_transition_error()` return type**

In `src/filigree/mcp_tools/common.py`, change:

```python
# Before (line 90-96):
def _build_transition_error(
    tracker: Any,
    issue_id: str,
    error: str,
    *,
    include_ready: bool = True,
) -> dict[str, Any]:

# After:
from filigree.types.api import TransitionError

def _build_transition_error(
    tracker: Any,
    issue_id: str,
    error: str,
    *,
    include_ready: bool = True,
) -> TransitionError:
```

The body builds the dict incrementally (conditional `valid_transitions` and `hint`). Since `TransitionError` uses `NotRequired` for those keys, the existing pattern of conditionally adding keys still works. But the intermediate `data` variable should be typed:

```python
def _build_transition_error(
    tracker: Any,
    issue_id: str,
    error: str,
    *,
    include_ready: bool = True,
) -> TransitionError:
    """Build a structured error dict with valid-transition hints."""
    data: TransitionError = {"error": error, "code": "invalid_transition"}
    try:
        transitions = tracker.get_valid_transitions(issue_id)
        if include_ready:
            data["valid_transitions"] = [{"to": t.to, "category": t.category, "ready": t.ready} for t in transitions]
        else:
            data["valid_transitions"] = [{"to": t.to, "category": t.category} for t in transitions]
        data["hint"] = "Use get_valid_transitions to see allowed state changes"
    except KeyError:
        pass
    return data
```

**Step 3: Refactor `_handle_get_issue`**

In `src/filigree/mcp_tools/issues.py`, change lines 301-323:

```python
# Before:
async def _handle_get_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db
    tracker = _get_db()
    try:
        issue = tracker.get_issue(arguments["id"])
        data: dict[str, Any] = dict(issue.to_dict())
        if arguments.get("include_transitions"):
            ...
            data["valid_transitions"] = [...]
        return _text(data)
    except KeyError:
        return _text({"error": ..., "code": "not_found"})

# After:
from filigree.types.api import ErrorResponse, IssueWithTransitions

async def _handle_get_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db
    tracker = _get_db()
    try:
        issue = tracker.get_issue(arguments["id"])
        if arguments.get("include_transitions"):
            transitions = tracker.get_valid_transitions(arguments["id"])
            result = IssueWithTransitions(
                **issue.to_dict(),
                valid_transitions=[
                    {
                        "to": t.to,
                        "category": t.category,
                        "enforcement": t.enforcement,
                        "requires_fields": list(t.requires_fields),
                        "missing_fields": list(t.missing_fields),
                        "ready": t.ready,
                    }
                    for t in transitions
                ],
            )
            return _text(result)
        return _text(issue.to_dict())
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {arguments['id']}", code="not_found"))
```

**Step 4: Refactor `_handle_update_issue`**

In `src/filigree/mcp_tools/issues.py`, change the result construction (lines 404-423):

```python
# Before:
result: dict[str, Any] = dict(issue.to_dict())
changed: list[str] = []
# ... build changed list ...
result["changed_fields"] = changed
return _text(result)

# After:
from filigree.types.api import IssueWithChangedFields

changed: list[str] = []
# ... build changed list (same logic) ...
result = IssueWithChangedFields(**issue.to_dict(), changed_fields=changed)
return _text(result)
```

**Step 5: Refactor `_handle_close_issue`**

In `src/filigree/mcp_tools/issues.py`, change lines 445-448:

> **Wire format change:** The current code produces 4-key dicts for `newly_unblocked` entries: `{id, title, priority, type}`. Using `_slim_issue()` produces 5-key `SlimIssue` dicts: `{id, title, status, priority, type}`. This adds `status` — a deliberate additive enrichment, backward-compatible for consumers that ignore unknown keys.

```python
# Before (4-key newly_unblocked):
result: dict[str, Any] = dict(issue.to_dict())
if newly_unblocked:
    result["newly_unblocked"] = [{"id": i.id, "title": i.title, "priority": i.priority, "type": i.type} for i in newly_unblocked]
return _text(result)

# After (5-key SlimIssue — adds "status"):
if newly_unblocked:
    result = IssueWithUnblocked(
        **issue.to_dict(),
        newly_unblocked=[_slim_issue(i) for i in newly_unblocked],
    )
else:
    result = IssueWithUnblocked(**issue.to_dict())
return _text(result)
```

**Step 6: Refactor `_handle_claim_next`**

In `src/filigree/mcp_tools/issues.py`, change lines 540-546:

```python
# Before:
result: dict[str, Any] = dict(claimed.to_dict())
parts = [f"P{claimed.priority}"]
if claimed.type != "task":
    parts.append(f"type={claimed.type}")
parts.append("ready issue (no blockers)")
result["selection_reason"] = f"Highest-priority {', '.join(parts)}"
return _text(result)

# After:
from filigree.types.api import ClaimNextResponse

parts = [f"P{claimed.priority}"]
if claimed.type != "task":
    parts.append(f"type={claimed.type}")
parts.append("ready issue (no blockers)")
result = ClaimNextResponse(
    **claimed.to_dict(),
    selection_reason=f"Highest-priority {', '.join(parts)}",
)
return _text(result)
```

**Step 7: Refactor `_handle_batch_close` and `_handle_batch_update`**

In `src/filigree/mcp_tools/issues.py`:

```python
# _handle_batch_close — change lines 565-572:
from filigree.types.api import BatchCloseResponse

batch_result = BatchCloseResponse(
    succeeded=[i.id for i in closed],
    failed=failed,
    count=len(closed),
)
if newly_unblocked:
    batch_result["newly_unblocked"] = [_slim_issue(i) for i in newly_unblocked]
return _text(batch_result)

# _handle_batch_update — change lines 594-600:
from filigree.types.api import BatchUpdateResponse

return _text(
    BatchUpdateResponse(
        succeeded=[i.id for i in updated],
        failed=update_failed,
        count=len(updated),
    )
)
```

**Step 8: Refactor `_handle_list_issues` and `_handle_search_issues`**

```python
# _handle_list_issues — change lines 352-359:
from filigree.types.api import IssueListResponse

return _text(
    IssueListResponse(
        issues=[i.to_dict() for i in issues],
        limit=effective_limit,
        offset=offset,
        has_more=has_more,
    )
)

# _handle_search_issues — change lines 484-491:
from filigree.types.api import SearchResponse

return _text(
    SearchResponse(
        issues=[_slim_issue(i) for i in issues],
        limit=effective_limit,
        offset=offset,
        has_more=has_more,
    )
)
```

**Step 9: Run verification**

Run: `uv run ruff check src/filigree/mcp_tools/ && uv run mypy src/filigree/ && uv run pytest tests/mcp/ -v --tb=short`
Expected: All clean. Existing MCP tests pass. Note: `newly_unblocked` entries gain a `status` key (4→5 keys) — this is a deliberate additive change for `SlimIssue` consistency, backward-compatible for consumers that ignore unknown keys.

**Step 10: Commit**

```bash
git add src/filigree/mcp_tools/common.py src/filigree/mcp_tools/issues.py
git commit -m "refactor(mcp): use typed responses for issue handlers"
```

---

### Task 3: Refactor MCP planning handler and dashboard routes

**Files:**
- Modify: `src/filigree/mcp_tools/planning.py` (1 handler)
- Modify: `src/filigree/dashboard_routes/issues.py` (1 handler)
- Modify: `src/filigree/dashboard_routes/analytics.py` (1 handler)
- Test: existing `tests/api/` tests serve as regression

**Context:** Three remaining mutation sites — the MCP `_handle_get_plan` (adds `progress_pct`), dashboard `api_issue_detail` (adds `dep_details`, `events`, `comments`), and dashboard `api_stats` (adds `prefix`).

**Step 1: Refactor `_handle_get_plan`**

In `src/filigree/mcp_tools/planning.py`, change lines 188-200:

```python
# Before:
plan_tree = tracker.get_plan(arguments["milestone_id"])
plan_data: dict[str, Any] = dict(plan_tree)
total = plan_data.get("total_steps", 0)
completed = plan_data.get("completed_steps", 0)
plan_data["progress_pct"] = round(completed / total * 100, 1) if total > 0 else 0.0
return _text(plan_data)

# After:
# NOTE: This changes error behavior — the old .get() silently defaulted to 0,
# the new direct key access will raise KeyError (caught by the existing handler).
# This is an improvement: fail-fast enforces the PlanTree contract rather than
# silently returning bogus progress_pct=0.0 for malformed plan trees.
plan_tree = tracker.get_plan(arguments["milestone_id"])
total = plan_tree["total_steps"]
completed = plan_tree["completed_steps"]
result = PlanResponse(
    milestone=plan_tree["milestone"],
    phases=plan_tree["phases"],
    total_steps=total,
    completed_steps=completed,
    progress_pct=round(completed / total * 100, 1) if total > 0 else 0.0,
)
return _text(result)
```

**Step 2: Refactor `api_issue_detail`**

In `src/filigree/dashboard_routes/issues.py`, change lines 40-80:

```python
# Before:
data: dict[str, Any] = dict(issue.to_dict())
# ... build dep_details ...
data["dep_details"] = dep_details
data["events"] = [dict(e) for e in events]
data["comments"] = db.get_comments(issue_id)
return JSONResponse(data)

# After:
from filigree.types.api import DepDetail, EnrichedIssueDetail, IssueDetailEvent

# ... build dep_details using DepDetail constructor ...
dep_details: dict[str, DepDetail] = {}
for did in dep_ids:
    try:
        dep = db.get_issue(did)
        dep_details[did] = DepDetail(
            title=dep.title,
            status=dep.status,
            status_category=dep.status_category,
            priority=dep.priority,
        )
    except KeyError:
        logger.warning("dep resolution failed for %s in issue %s", did, issue_id)
        dep_details[did] = DepDetail(
            title=did,
            status="unknown",
            status_category="open",
            priority=2,
        )

# NOTE: The SQL column list below must stay in sync with IssueDetailEvent fields.
# IssueDetailEvent is a slim 5-column projection — NOT full EventRecord.
# See types/events.py for the full EventRecord type.
events = db.conn.execute(
    "SELECT event_type, actor, old_value, new_value, created_at FROM events WHERE issue_id = ? ORDER BY created_at DESC LIMIT 20",
    (issue_id,),
).fetchall()
event_list: list[IssueDetailEvent] = [
    IssueDetailEvent(**dict(e)) for e in events
]

result = EnrichedIssueDetail(
    **issue.to_dict(),
    dep_details=dep_details,
    events=event_list,
    comments=db.get_comments(issue_id),
)
return JSONResponse(result)
```

**Step 3: Refactor `api_stats`**

In `src/filigree/dashboard_routes/analytics.py`, change lines 430-433:

```python
# Before:
stats_data: dict[str, Any] = dict(db.get_stats())
stats_data["prefix"] = db.prefix
return JSONResponse(stats_data)

# After:
from filigree.types.api import StatsWithPrefix

result = StatsWithPrefix(**db.get_stats(), prefix=db.prefix)
return JSONResponse(result)
```

**Step 4: Run verification**

Run: `uv run ruff check src/ && uv run mypy src/filigree/ && uv run pytest tests/api/ tests/mcp/ -v --tb=short`
Expected: All clean. Existing API and MCP tests pass.

**Step 5: Commit**

```bash
git add src/filigree/mcp_tools/planning.py src/filigree/dashboard_routes/issues.py src/filigree/dashboard_routes/analytics.py
git commit -m "refactor(api): use typed responses for plan, issue detail, and stats"
```

---

### Task 4: Add shape contract tests for all new TypedDicts

**Files:**
- Modify: `tests/util/test_type_contracts.py`
- Test: self-testing (the tests ARE the deliverable)

**Context:** Following the same pattern as Tasks 1A-1C, add key-set and value-type shape tests for the new API response TypedDicts. These tests exercise the actual handlers and verify the returned dicts match the declared TypedDict shapes.

**Step 1: Write all shape tests**

Add to `tests/util/test_type_contracts.py`. Import the new types at the top of the file (see Task 1 Step 1 for the import block). Then add test classes:

```python
# ---------------------------------------------------------------------------
# API response shape tests (types/api.py)
# ---------------------------------------------------------------------------


class TestSlimIssueShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.mcp_tools.common import _slim_issue
        issue = db.create_issue("Test", type="task")
        result = _slim_issue(issue)
        hints = get_type_hints(SlimIssue)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        from filigree.mcp_tools.common import _slim_issue
        issue = db.create_issue("Test", type="task")
        result = _slim_issue(issue)
        assert isinstance(result["id"], str)
        assert isinstance(result["priority"], int)


class TestIssueWithChangedFieldsShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, title="Updated")
        updated = db.get_issue(issue.id)
        from filigree.types.api import IssueWithChangedFields
        result = IssueWithChangedFields(**updated.to_dict(), changed_fields=["title"])
        hints = get_type_hints(IssueWithChangedFields)
        assert set(result.keys()) == set(hints.keys())


class TestIssueListResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.create_issue("Test", type="task")
        from filigree.types.api import IssueListResponse
        issues = db.list_issues(limit=1)
        result = IssueListResponse(
            issues=[i.to_dict() for i in issues],
            limit=1, offset=0, has_more=False,
        )
        hints = get_type_hints(IssueListResponse)
        assert set(result.keys()) == set(hints.keys())


class TestSearchResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.mcp_tools.common import _slim_issue
        from filigree.types.api import SearchResponse
        issue = db.create_issue("Searchable", type="task")
        result = SearchResponse(
            issues=[_slim_issue(issue)],
            limit=10, offset=0, has_more=False,
        )
        hints = get_type_hints(SearchResponse)
        assert set(result.keys()) == set(hints.keys())


class TestBatchUpdateResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.types.api import BatchUpdateResponse
        result = BatchUpdateResponse(succeeded=["a"], failed=[], count=1)
        hints = get_type_hints(BatchUpdateResponse)
        assert set(result.keys()) == set(hints.keys())


class TestBatchCloseResponseShape:
    def test_required_keys(self, db: FiligreeDB) -> None:
        """Required keys (succeeded, failed, count) are always present."""
        from filigree.types.api import BatchCloseResponse
        result = BatchCloseResponse(succeeded=["a"], failed=[], count=1)
        assert {"succeeded", "failed", "count"} <= set(result.keys())

    def test_with_newly_unblocked(self, db: FiligreeDB) -> None:
        """All 4 keys present when newly_unblocked is populated."""
        from filigree.types.api import BatchCloseResponse
        result = BatchCloseResponse(
            succeeded=["a"], failed=[], count=1,
            newly_unblocked=[SlimIssue(id="x", title="t", status="open", priority=2, type="task")],
        )
        hints = get_type_hints(BatchCloseResponse)
        assert set(result.keys()) == set(hints.keys())


class TestErrorResponseShape:
    def test_keys_match(self) -> None:
        from filigree.types.api import ErrorResponse
        result = ErrorResponse(error="not found", code="not_found")
        hints = get_type_hints(ErrorResponse)
        assert set(result.keys()) == set(hints.keys())


class TestTransitionErrorShape:
    def test_keys_match(self) -> None:
        from filigree.types.api import TransitionError
        result = TransitionError(error="bad", code="invalid_transition")
        hints = get_type_hints(TransitionError)
        # NotRequired keys may be absent — check required subset
        assert {"error", "code"} <= set(result.keys())


class TestDepDetailShape:
    def test_keys_match(self) -> None:
        from filigree.types.api import DepDetail
        result = DepDetail(title="t", status="open", status_category="open", priority=2)
        hints = get_type_hints(DepDetail)
        assert set(result.keys()) == set(hints.keys())


class TestStatsWithPrefixShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.types.api import StatsWithPrefix
        stats = db.get_stats()
        result = StatsWithPrefix(**stats, prefix="TEST")
        hints = get_type_hints(StatsWithPrefix)
        assert set(result.keys()) == set(hints.keys())


class TestPlanResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.types.api import PlanResponse
        milestone = db.create_issue("M", type="milestone")
        phase = db.create_issue("P", type="phase", parent_id=milestone.id)
        db.create_issue("S", type="step", parent_id=phase.id)
        plan = db.get_plan(milestone.id)
        result = PlanResponse(
            milestone=plan["milestone"],
            phases=plan["phases"],
            total_steps=plan["total_steps"],
            completed_steps=plan["completed_steps"],
            progress_pct=0.0,
        )
        hints = get_type_hints(PlanResponse)
        assert set(result.keys()) == set(hints.keys())


class TestEnrichedIssueDetailShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.types.api import DepDetail, EnrichedIssueDetail, IssueDetailEvent
        issue = db.create_issue("Test", type="task")
        result = EnrichedIssueDetail(
            **issue.to_dict(),
            dep_details={},
            events=[],
            comments=[],
        )
        hints = get_type_hints(EnrichedIssueDetail)
        assert set(result.keys()) == set(hints.keys())


# ---------------------------------------------------------------------------
# Guard: ensure TYPES_DIR exists to prevent vacuous parametrize pass (W8)
# ---------------------------------------------------------------------------

def test_types_dir_exists() -> None:
    """Sanity check: TYPES_DIR must exist, otherwise the import constraint
    test would produce zero parametrize cases and pass vacuously."""
    assert TYPES_DIR.exists(), f"types dir not found at {TYPES_DIR}"


# ---------------------------------------------------------------------------
# Dashboard JS contract: enriched issue detail keys (W9)
# ---------------------------------------------------------------------------

# Keys the JS frontend reads from the enriched issue detail endpoint
DASHBOARD_ENRICHED_KEYS = DASHBOARD_ISSUE_KEYS | {
    "dep_details", "events", "comments",
}


def test_enriched_issue_detail_keys_cover_dashboard_contract() -> None:
    """EnrichedIssueDetail must contain all keys the dashboard JS reads
    from the issue detail endpoint."""
    hints = get_type_hints(EnrichedIssueDetail)
    missing = DASHBOARD_ENRICHED_KEYS - set(hints.keys())
    assert not missing, f"EnrichedIssueDetail missing keys consumed by dashboard JS: {missing}"
```

**Step 2: Run all contract tests**

Run: `uv run pytest tests/util/test_type_contracts.py -v --tb=short`
Expected: All tests pass (old + new)

**Step 3: Run full CI**

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/filigree/ && uv run pytest --tb=short`
Expected: All green

**Step 4: Commit**

```bash
git add tests/util/test_type_contracts.py
git commit -m "test(types): add shape contract tests for API response TypedDicts"
```

---

## Summary

| Task | What | Files | TypedDicts |
|------|------|-------|------------|
| 1 | Create `types/api.py` + re-exports | types/api.py, types/__init__.py | 18 definitions (incl. `_BatchCloseRequired` base) |
| 2 | Refactor MCP issue handlers | mcp_tools/common.py, mcp_tools/issues.py | 10 used |
| 3 | Refactor MCP planning + dashboard routes | mcp_tools/planning.py, dashboard_routes/issues.py, dashboard_routes/analytics.py | 5 used |
| 4 | Shape contract tests + guards + dashboard contract | tests/util/test_type_contracts.py | 14 tested |

**CI gate after each task:** `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/filigree/ && uv run pytest --tb=short`

## Review panel changes applied (2026-02-28)

| ID | Type | Fix applied |
|----|------|-------------|
| B1 | Blocking | Removed phantom `CloseIssueResponse` from imports/re-exports, replaced with `IssueWithUnblocked` |
| B2 | Blocking | `BatchCloseResponse` now uses split-base pattern; `IssueWithTransitions`/`IssueWithUnblocked` use `NotRequired` instead of `total=False` |
| B3 | Blocking | Documented wire format change: `newly_unblocked` gains `status` key (4→5 keys) |
| B4 | Blocking | Fixed `TestBatchCloseResponseShape` to use subset assertion for required keys + full equality test with `newly_unblocked` |
| W1 | Warning | Added prerequisites section |
| W3 | Warning | Aligned naming to `ClaimNextResponse` (was `IssueWithSelectionReason` in design doc) |
| W4 | Warning | Added `**spread` risk comments in flat inheritance section |
| W5 | Warning | Added SQL↔TypedDict cross-reference comment for `IssueDetailEvent` |
| W6 | Warning | Added `test_with_newly_unblocked` test case for populated path |
| W7 | Warning | Acknowledged `.get()` → direct access behavioral change as intentional improvement |
| W8 | Warning | Added `test_types_dir_exists()` guard against vacuous parametrize |
| W9 | Warning | Added `DASHBOARD_ENRICHED_KEYS` contract test for `EnrichedIssueDetail` |
| QW6 | Warning | Added `logger.warning` for dangling dep_id in `api_issue_detail` |
| AW5 | Warning | Added import convention note: all imports must be module-level in delivered code |

## Review panel amendments — round 2 (2026-02-28)

Full 4-reviewer panel (Reality, Architecture, Quality, Systems) identified 10 warnings, 0 blockers. Verdict: **APPROVED_WITH_WARNINGS**. The following amendments must be applied during implementation.

### Amendment A: Add `logger` to `dashboard_routes/issues.py` (Task 3 Step 2)

`dashboard_routes/issues.py` has no `logger` in scope. Before adding `logger.warning()` for dangling dep_id, add at the module level:

```python
import logging

logger = logging.getLogger(__name__)
```

### Amendment B: Add 3 missing shape tests to Task 4

Task 4 tests 14 of 17 TypedDicts. Add shape tests for the 3 missing types. These should exercise both the absent and populated paths for `NotRequired` fields:

```python
class TestIssueWithTransitionsShape:
    def test_keys_without_transitions(self, db: FiligreeDB) -> None:
        from filigree.types.api import IssueWithTransitions
        issue = db.create_issue("Test", type="task")
        result = IssueWithTransitions(**issue.to_dict())
        # NotRequired keys may be absent
        assert {"id", "title", "status"} <= set(result.keys())

    def test_keys_with_transitions(self, db: FiligreeDB) -> None:
        from filigree.types.api import IssueWithTransitions
        issue = db.create_issue("Test", type="task")
        result = IssueWithTransitions(**issue.to_dict(), valid_transitions=[])
        hints = get_type_hints(IssueWithTransitions)
        assert set(result.keys()) == set(hints.keys())


class TestIssueWithUnblockedShape:
    def test_keys_without_unblocked(self, db: FiligreeDB) -> None:
        from filigree.types.api import IssueWithUnblocked
        issue = db.create_issue("Test", type="task")
        result = IssueWithUnblocked(**issue.to_dict())
        assert {"id", "title", "status"} <= set(result.keys())

    def test_keys_with_unblocked(self, db: FiligreeDB) -> None:
        from filigree.types.api import IssueWithUnblocked, SlimIssue
        issue = db.create_issue("Test", type="task")
        result = IssueWithUnblocked(
            **issue.to_dict(),
            newly_unblocked=[SlimIssue(id="x", title="t", status="open", priority=2, type="task")],
        )
        hints = get_type_hints(IssueWithUnblocked)
        assert set(result.keys()) == set(hints.keys())


class TestClaimNextResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.types.api import ClaimNextResponse
        issue = db.create_issue("Test", type="task")
        result = ClaimNextResponse(**issue.to_dict(), selection_reason="P2 ready issue")
        hints = get_type_hints(ClaimNextResponse)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        from filigree.types.api import ClaimNextResponse
        issue = db.create_issue("Test", type="task")
        result = ClaimNextResponse(**issue.to_dict(), selection_reason="P2 ready issue")
        assert isinstance(result["selection_reason"], str)
```

### Amendment C: Wire format regression guard (Task 2 Step 9)

After refactoring `_handle_close_issue` and `_handle_batch_close`, update the existing regression tests in `tests/mcp/test_tools.py` to lock in the 5-key `SlimIssue` contract:

```python
# In TestProactiveContext.test_close_returns_newly_unblocked:
assert set(data["newly_unblocked"][0].keys()) == {"id", "title", "status", "priority", "type"}
```

### Amendment D: `IssueDetailEvent` populated-path test (Task 4 Step 1)

Add a test that exercises `IssueDetailEvent` construction from a real SQL row:

```python
class TestIssueDetailEventFromSQL:
    def test_construction_from_sql_row(self, db: FiligreeDB) -> None:
        from filigree.types.api import IssueDetailEvent
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, status="in_progress")
        rows = db.conn.execute(
            "SELECT event_type, actor, old_value, new_value, created_at "
            "FROM events WHERE issue_id = ? LIMIT 1",
            (issue.id,),
        ).fetchall()
        assert len(rows) >= 1
        event = IssueDetailEvent(**dict(rows[0]))
        hints = get_type_hints(IssueDetailEvent)
        assert set(event.keys()) == set(hints.keys())
```

### Amendment E: `selection_reason` assertion (Task 2 Step 9)

In the existing `test_claim_next_success` MCP test, add:

```python
assert "selection_reason" in data
assert isinstance(data["selection_reason"], str)
```

### Amendment F: Extension key namespace documentation (Task 1 Step 3)

Add a comment in `types/api.py` near the flat-inheritance section listing reserved extension key names:

```python
# RESERVED EXTENSION KEYS — these names must never be added to IssueDict:
# valid_transitions, changed_fields, newly_unblocked, selection_reason,
# dep_details, events, comments
```

### Amendment G: Dangling dep_id fallback test (Task 4 Step 1)

Add a test verifying the fallback `DepDetail` for a deleted dependency:

```python
class TestDanglingDepDetail:
    def test_fallback_for_missing_dep(self, db: FiligreeDB) -> None:
        from filigree.types.api import DepDetail
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        db.add_dependency(a.id, b.id)
        db.conn.execute("DELETE FROM issues WHERE id = ?", (b.id,))
        db.conn.commit()
        # Simulate what api_issue_detail does for dangling deps
        try:
            db.get_issue(b.id)
            assert False, "Should have raised KeyError"
        except KeyError:
            fallback = DepDetail(title=b.id, status="unknown", status_category="open", priority=2)
            assert set(fallback.keys()) == {"title", "status", "status_category", "priority"}
```

### Amendment summary

| ID | Source | Fix |
|----|--------|-----|
| A | Reality W2 | Add `logger` setup to `dashboard_routes/issues.py` |
| B | Quality W1 | Add 3 missing shape tests (IssueWithTransitions, IssueWithUnblocked, ClaimNextResponse) |
| C | Quality W2 + Architecture R2 | Wire format regression guard for `newly_unblocked` 5-key contract |
| D | Quality W5 + Systems W1 | `IssueDetailEvent` populated-path test from real SQL |
| E | Quality W6 | `selection_reason` assertion in existing MCP test |
| F | Systems W2 | Extension key namespace documentation in `types/api.py` |
| G | Quality W4 | Dangling dep_id fallback test |
