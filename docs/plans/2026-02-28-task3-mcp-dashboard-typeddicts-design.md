# Task 3: MCP & Dashboard API Response TypedDicts — Design

## Goal

Add TypedDicts for MCP tool handler and dashboard route responses that construct novel shapes (mutation sites, list/batch envelopes). Skip thin pass-throughs that already return typed DB results from Task 1.

## Architecture

Single new file `src/filigree/types/api.py` in the existing `types/` subpackage. All API response TypedDicts live here, organized by domain section.

**Key constraint:** No JSON key renames. Existing MCP consumers and `dashboard.html` JS read response keys by name. Wire format is preserved exactly.

**Two patterns based on response shape:**

1. **Flat inheritance** — for handlers that spread `to_dict()` + extra keys into a flat dict. Uses `class FooResponse(IssueDict, total=False)` to extend the base type. Preserves flat wire format.

2. **True envelopes** — for handlers that construct wrapper dicts with nested arrays/objects (list, batch, search responses). Uses composition: `issues: list[IssueDict]`.

## Scope: 15 TypedDicts across ~12 handlers

### Shared types

| TypedDict | Keys | Used by |
|-----------|------|---------|
| `SlimIssue` | `id, title, status, priority, type` | `_slim_issue()`, search, unblocked lists |
| `ErrorResponse` | `error, code` | All error returns |
| `TransitionError` | `error, code (Literal), current_status, valid_transitions, hint?` | `_build_transition_error()` |

### Flat inheritance (IssueDict + extra keys)

These preserve the existing flat wire format where `to_dict()` keys are spread alongside handler-added keys.

| TypedDict | Inherits | Extra keys | Handler |
|-----------|----------|------------|---------|
| `IssueWithTransitions` | `IssueDict, total=False` | `valid_transitions?` | `_handle_get_issue` |
| `IssueWithChangedFields` | `IssueDict` | `changed_fields: list[str]` | `_handle_update_issue` |
| `IssueWithUnblocked` | `IssueDict, total=False` | `newly_unblocked?: list[SlimIssue]` | `_handle_close_issue` |
| `IssueWithSelectionReason` | `IssueDict` | `selection_reason: str` | `_handle_claim_next` |
| `EnrichedIssueDetail` | `IssueDict` | `dep_details, events, comments` | `api_issue_detail` |
| `StatsWithPrefix` | `StatsResult` | `prefix: str` | `api_stats` |

### True envelopes (wrapper dicts)

| TypedDict | Keys | Handler |
|-----------|------|---------|
| `IssueListResponse` | `issues: list[IssueDict], limit, offset, has_more` | `_handle_list_issues` |
| `SearchResponse` | `issues: list[SlimIssue], limit, offset, has_more` | `_handle_search_issues` |
| `BatchUpdateResponse` | `succeeded, failed, count` | `_handle_batch_update` |
| `BatchCloseResponse` | `succeeded, failed, count, newly_unblocked?` | `_handle_batch_close` |
| `PlanResponse` | `milestone, phases, total_steps, completed_steps, progress_pct` | `_handle_get_plan` |

### Dashboard helper type

| TypedDict | Keys | Used by |
|-----------|------|---------|
| `DepDetail` | `title, status, status_category, priority` | Nested in `EnrichedIssueDetail.dep_details` |

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
