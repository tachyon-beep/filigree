## Summary
`get_flow_metrics()` undercounts done issues by excluding items archived via `archive_closed()`.

## Severity
- rule_id: `logic-error`
- Severity: major
- Priority: P1

## Evidence
- `src/filigree/analytics.py:141` fetches only `db.list_issues(status="closed", ...)`.
- `src/filigree/core.py:3463` documents archival as preserving `closed_at`, and `src/filigree/core.py:3485` sets `status = 'archived'`.
- `src/filigree/core.py:1484` to `src/filigree/core.py:1495` shows `list_issues` category filtering only by template done states; archived is not in template done states.
- `src/filigree/core.py:696` to `src/filigree/core.py:704` classifies `archived` as done in fallback logic, so these are semantically done issues but missed by metrics filter.

## Root Cause Hypothesis
`analytics.get_flow_metrics()` treats “done” as “status filter = closed”, but archival rewrites status to `archived` while keeping `closed_at`. The analytics query path therefore drops valid done issues for wider windows (for example, `days > archive_closed(days_old)`).

## Suggested Fix
In `src/filigree/analytics.py`, stop filtering the initial issue set by `status="closed"` only. Fetch by `closed_at` presence/window (or include `archived` explicitly and dedupe), then keep only `issue.status_category == "done"`.

---
## Summary
Cycle-time computation is nondeterministic when multiple status events share the same timestamp.

## Severity
- rule_id: `logic-error`
- Severity: minor
- Priority: P2

## Evidence
- `src/filigree/analytics.py:43` orders events only by `created_at ASC`.
- `src/filigree/analytics.py:83` batch path also orders only by `issue_id ASC, created_at ASC`.
- `src/filigree/core.py:188` defines an autoincrement event `id`, and other event readers use tie-break ordering (`created_at, id`) at `src/filigree/core.py:2138`.
- `src/filigree/core.py:2440` to `src/filigree/core.py:2450` imports external event timestamps as-is, so equal timestamps are realistic.

## Root Cause Hypothesis
Analytics assumes lexical timestamp ordering is sufficient, but equal `created_at` values leave row order undefined. For same-time `wip` and `done` events, cycle-time start/end pairing can vary by insertion/order plan.

## Suggested Fix
Add deterministic tie-breaks in analytics queries:
- `ORDER BY created_at ASC, id ASC` in `cycle_time()`.
- `ORDER BY issue_id ASC, created_at ASC, id ASC` in `_fetch_status_events_by_issue()`.