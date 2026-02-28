# Task 3: MCP & Dashboard API Response TypedDicts — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add TypedDicts for MCP tool handler and dashboard route responses that construct novel shapes (mutation sites, list/batch envelopes, error shapes).

**Architecture:** Single new `types/api.py` module with ~15 TypedDicts. Flat TypedDict inheritance for handlers that spread `to_dict()` + extra keys (preserves wire format). True envelope TypedDicts for list/batch/search wrappers. Handler code refactored from dict mutation to typed construction.

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
    CloseIssueResponse,
    DepDetail,
    EnrichedIssueDetail,
    ErrorResponse,
    IssueListResponse,
    IssueWithChangedFields,
    IssueWithTransitions,
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
# ---------------------------------------------------------------------------


class IssueWithTransitions(IssueDict, total=False):
    """Issue detail with optional valid_transitions (MCP get_issue)."""

    valid_transitions: list[dict[str, Any]]


class IssueWithChangedFields(IssueDict):
    """Issue update response with list of changed field names."""

    changed_fields: list[str]


class IssueWithUnblocked(IssueDict, total=False):
    """Issue close response with optional newly-unblocked issues."""

    newly_unblocked: list[SlimIssue]


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
    """Slim event shape from dashboard issue detail (5 columns, not full EventRecord)."""

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


class BatchCloseResponse(TypedDict, total=False):
    """Batch close result with optional newly-unblocked list.

    ``succeeded``, ``failed``, and ``count`` are always present.
    ``newly_unblocked`` is only included when issues were actually unblocked.
    """

    succeeded: list[str]
    failed: list[dict[str, Any]]
    count: int
    newly_unblocked: list[SlimIssue]


class PlanResponse(TypedDict):
    """Plan tree with computed progress percentage (MCP get_plan)."""

    milestone: dict[str, Any]
    phases: list[PlanPhase]
    total_steps: int
    completed_steps: int
    progress_pct: float
```

**Important notes on `BatchCloseResponse`:** It uses `total=False` because `newly_unblocked` is optional AND the required keys (`succeeded`, `failed`, `count`) are always present at runtime. An alternative is splitting into required/optional base classes, but `total=False` is simpler here and mypy still catches missing keys at construction sites. If you prefer strictness, use:

```python
class _BatchCloseRequired(TypedDict):
    succeeded: list[str]
    failed: list[dict[str, Any]]
    count: int

class BatchCloseResponse(_BatchCloseRequired, total=False):
    newly_unblocked: list[SlimIssue]
```

Use whichever pattern the implementer prefers — both are correct.

**Also note `IssueDetailEvent`:** The dashboard `api_issue_detail` handler runs a raw SQL query that selects only 5 columns (`event_type, actor, old_value, new_value, created_at`), NOT the full 8-column `EventRecord`. So we need a separate slim type. Do NOT reuse `EventRecord` here.

**Step 4: Update `types/__init__.py` re-exports**

Add the new types to the import block and `__all__` list in `src/filigree/types/__init__.py`:

```python
from filigree.types.api import (
    BatchCloseResponse,
    BatchUpdateResponse,
    ClaimNextResponse,
    CloseIssueResponse,
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

```python
# Before:
result: dict[str, Any] = dict(issue.to_dict())
if newly_unblocked:
    result["newly_unblocked"] = [{"id": i.id, "title": i.title, "priority": i.priority, "type": i.type} for i in newly_unblocked]
return _text(result)

# After:
from filigree.types.api import IssueWithUnblocked

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
Expected: All clean. Existing MCP tests pass (wire format unchanged).

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
from filigree.types.api import PlanResponse

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
        dep_details[did] = DepDetail(
            title=did,
            status="unknown",
            status_category="open",
            priority=2,
        )

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
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.types.api import BatchCloseResponse
        result = BatchCloseResponse(succeeded=["a"], failed=[], count=1)
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
| 1 | Create `types/api.py` + re-exports | types/api.py, types/__init__.py | 17 definitions |
| 2 | Refactor MCP issue handlers | mcp_tools/common.py, mcp_tools/issues.py | 10 used |
| 3 | Refactor MCP planning + dashboard routes | mcp_tools/planning.py, dashboard_routes/issues.py, dashboard_routes/analytics.py | 5 used |
| 4 | Shape contract tests | tests/util/test_type_contracts.py | 12 tested |

**CI gate after each task:** `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/filigree/ && uv run pytest --tb=short`
