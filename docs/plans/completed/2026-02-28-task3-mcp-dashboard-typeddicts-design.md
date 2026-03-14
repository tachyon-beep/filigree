# Task 3: MCP & Dashboard API Response TypedDicts — Design

## Goal

Add TypedDicts for MCP tool handler and dashboard route responses that construct novel shapes (mutation sites, list/batch envelopes). Skip thin pass-throughs that already return typed DB results from Task 1.

## Architecture

Single new file `src/filigree/types/api.py` in the existing `types/` subpackage. All API response TypedDicts live here, organized by domain section.

**Key constraint:** No JSON key renames. Existing MCP consumers and `dashboard.html` JS read response keys by name. Wire format is preserved exactly, with one deliberate additive change: `newly_unblocked` entries in close/batch-close responses gain a `status` key (4→5 keys) for consistency with `SlimIssue`. This is backward-compatible for consumers that ignore unknown keys.

**Two patterns based on response shape:**

1. **Flat inheritance** — for handlers that spread `to_dict()` + extra keys into a flat dict. Uses `NotRequired` on individual optional fields (e.g., `valid_transitions: NotRequired[list[...]]`) to keep inherited base keys required. For envelope types with a mix of required and optional keys, uses the split-base pattern (`_FooRequired` + `Foo(total=False)`) matching the established convention in `types/workflow.py`. Preserves flat wire format.

2. **True envelopes** — for handlers that construct wrapper dicts with nested arrays/objects (list, batch, search responses). Uses composition: `issues: list[IssueDict]`.

## Prerequisites

This plan requires Tasks 1A, 1B, and 1C to be complete. Specifically: `IssueDict` from `types.core`, and `StatsResult`, `CommentRecord`, `PlanPhase` from `types.planning` must exist.

## Scope: 17 TypedDicts across ~12 handlers

### Shared types

| TypedDict | Keys | Used by |
|-----------|------|---------|
| `SlimIssue` | `id, title, status, priority, type` | `_slim_issue()`, search, unblocked lists |
| `ErrorResponse` | `error, code` | All error returns |
| `TransitionError` | `error, code (Literal["invalid_transition"]), valid_transitions?, hint?` | `_build_transition_error()` |

### Flat inheritance (IssueDict + extra keys)

These preserve the existing flat wire format where `to_dict()` keys are spread alongside handler-added keys.

| TypedDict | Inherits | Extra keys | Handler |
|-----------|----------|------------|---------|
| `IssueWithTransitions` | `IssueDict` | `valid_transitions: NotRequired[list[...]]` | `_handle_get_issue` |
| `IssueWithChangedFields` | `IssueDict` | `changed_fields: list[str]` | `_handle_update_issue` |
| `IssueWithUnblocked` | `IssueDict` | `newly_unblocked: NotRequired[list[SlimIssue]]` | `_handle_close_issue` |
| `ClaimNextResponse` | `IssueDict` | `selection_reason: str` | `_handle_claim_next` |
| `EnrichedIssueDetail` | `IssueDict` | `dep_details, events, comments` | `api_issue_detail` |
| `StatsWithPrefix` | `StatsResult` | `prefix: str` | `api_stats` |

### True envelopes (wrapper dicts)

| TypedDict | Keys | Handler |
|-----------|------|---------|
| `IssueListResponse` | `issues: list[IssueDict], limit, offset, has_more` | `_handle_list_issues` |
| `SearchResponse` | `issues: list[SlimIssue], limit, offset, has_more` | `_handle_search_issues` |
| `BatchUpdateResponse` | `succeeded, failed, count` | `_handle_batch_update` |
| `BatchCloseResponse` | `succeeded, failed, count` (required) + `newly_unblocked?` (optional, split-base pattern) | `_handle_batch_close` |
| `PlanResponse` | `milestone, phases, total_steps, completed_steps, progress_pct` | `_handle_get_plan` |

### Dashboard helper types

| TypedDict | Keys | Used by |
|-----------|------|---------|
| `DepDetail` | `title, status, status_category, priority` | Nested in `EnrichedIssueDetail.dep_details` |
| `IssueDetailEvent` | `event_type, actor, old_value, new_value, created_at` | Slim 5-column projection in `EnrichedIssueDetail.events` (NOT full `EventRecord` — the dashboard SQL selects only 5 columns) |

## Handler refactoring pattern

**Before (flat mutation):**
```python
data: dict[str, Any] = dict(issue.to_dict())
data["changed_fields"] = changed
return _text(data)
```

**After (typed construction):**
```python
result = IssueWithChangedFields(**issue.to_dict(), changed_fields=changed)
return _text(result)
```

For `IssueDict` spread + optional keys, use `**` unpacking into the inherited TypedDict constructor. Mypy verifies all required keys are present and extra keys match the declared extensions.

**Known limitation:** mypy does not propagate granular type narrowing through `**TypedDict` unpacking. If `to_dict()` returns extra keys not in the TypedDict, they pass through silently at runtime (since TypedDicts are plain dicts). The shape contract tests in `test_type_contracts.py` catch drift by asserting exact key-set equality.

**`total=False` inheritance rule:** Never use `class Foo(IssueDict, total=False)` — this makes ALL inherited keys optional to mypy, defeating type safety. Instead use `NotRequired` on individual optional fields, or the split-base pattern for envelopes (see `types/workflow.py` for the established convention).

**Dashboard detail refactoring:**

```python
# Before: flat mutation
data = dict(issue.to_dict())
data["dep_details"] = dep_details
data["events"] = events
data["comments"] = comments

# After: typed construction
result = EnrichedIssueDetail(
    **issue.to_dict(),
    dep_details=dep_details,
    events=events,
    comments=comments,
)
```

## Testing strategy

1. **Shape tests** in `tests/util/test_type_contracts.py` — key-set matching via `get_type_hints()` for each new TypedDict against actual handler output. Reuses the existing fixture and pattern.

2. **Import constraint** — existing AST-based parametrized test already covers all `types/*.py` files, so `types/api.py` is automatically included.

3. **Existing API tests** — `tests/api/test_api.py` already covers the endpoints whose response shapes change. These serve as regression tests ensuring the refactoring doesn't break wire format.

## Files touched

- **Create:** `src/filigree/types/api.py` (~15 TypedDicts)
- **Modify:** `src/filigree/types/__init__.py` (add re-exports)
- **Modify:** `src/filigree/mcp_tools/issues.py` (5 handlers)
- **Modify:** `src/filigree/mcp_tools/planning.py` (1 handler)
- **Modify:** `src/filigree/dashboard_routes/issues.py` (1 handler)
- **Modify:** `src/filigree/dashboard_routes/analytics.py` (1 handler)
- **Modify:** `tests/util/test_type_contracts.py` (add shape tests)

## Out of scope

- MCP tool **input** argument TypedDicts (Task 6)
- MCP validation helper wiring (Task 4)
- Typing the ~35 pass-through handlers that already return typed DB results
- Workflow handler response shapes (complex, many unique shapes — would add ~10 more TypedDicts for marginal gain)
