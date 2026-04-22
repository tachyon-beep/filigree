## Summary
`get_flow_metrics()` paginates mutable `closed`/`archived` buckets with `OFFSET`, so concurrent archival or status changes can silently skip issues and undercount metrics.

## Severity
- Rule ID: `race-condition`
- Severity: `major`
- Priority: `P1`

## Evidence
[analytics.py](/home/john/filigree/src/filigree/analytics.py:164) pages through two live status buckets with `OFFSET`:

```python
for status_filter in ("closed", "archived"):
    offset = 0
    while True:
        page = db.list_issues(status=status_filter, limit=page_size, offset=offset)
        ...
        offset += page_size
```

[db_issues.py](/home/john/filigree/src/filigree/db_issues.py:1093) shows that `list_issues()` uses offset-based pagination over a status-filtered result set:

```python
SELECT i.id FROM issues i{where} ORDER BY i.priority, i.created_at LIMIT ? OFFSET ?
```

[db_events.py](/home/john/filigree/src/filigree/db_events.py:304) rewrites matching issues from done states to literal `archived` and commits the change:

```python
UPDATE issues SET status = 'archived', updated_at = ? WHERE id = ?
...
self.conn.commit()
```

The comment in [analytics.py](/home/john/filigree/src/filigree/analytics.py:167) already acknowledges that concurrent `archive_closed()` can move issues between buckets, but the current fix only dedupes duplicates by id. It does not prevent skipped rows when page membership shifts between `OFFSET` reads.

## Root Cause Hypothesis
The code treats a moving set as if it were a stable snapshot. If one issue from page 1 is archived after page 0 is read, every later row in the `closed` result shifts left; the next `OFFSET 1000` read can skip what used to be row 1001. Deduplication handles overlap, not omission.

## Suggested Fix
Replace the two-bucket `OFFSET` scan with a single stable query in `analytics.py`, ideally filtering directly on `closed_at >= cutoff` and selecting all done-or-archived issues in one pass. If pagination is still needed, page by an immutable cursor such as `(closed_at, id)` inside a read transaction instead of `OFFSET`.

---
## Summary
`lead_time()` drops archived issues from lead-time calculations even though `get_flow_metrics()` explicitly includes archived issues in throughput.

## Severity
- Rule ID: `logic-error`
- Severity: `major`
- Priority: `P2`

## Evidence
[analytics.py](/home/john/filigree/src/filigree/analytics.py:172) intentionally includes archived issues:

```python
for status_filter in ("closed", "archived"):
```

But [analytics.py](/home/john/filigree/src/filigree/analytics.py:136) rejects anything whose current `status_category` is not `done`:

```python
if issue.status_category != "done" or issue.closed_at is None:
    return None
```

[db_events.py](/home/john/filigree/src/filigree/db_events.py:307) says archival preserves `closed_at` while rewriting status to literal `archived`:

```python
Sets their status to 'archived' (preserving closed_at).
```

[db_issues.py](/home/john/filigree/src/filigree/db_issues.py:427) derives `Issue.status_category` from the current status text, and [db_workflow.py](/home/john/filigree/src/filigree/db_workflow.py:229) falls back to `"open"` for unknown states:

```python
cat = _BUILTIN_CATEGORY_BY_TYPE_STATE.get((issue_type, status))
...
return "open"
```

So an archived issue still has a valid `closed_at`, still contributes to `throughput`, but `lead_time()` returns `None` for it.

## Root Cause Hypothesis
`lead_time()` assumes that any legitimately closed issue will still carry a done-category workflow state. That assumption stopped being true once archival introduced a synthetic `archived` status outside the normal workflow taxonomy.

## Suggested Fix
In `analytics.py`, treat archived issues as done for lead-time purposes. The safest fix is to compute lead time from parseable `created_at` and `closed_at` for issues already selected by `get_flow_metrics()`, or explicitly allow `issue.status == "archived"` in `lead_time()`.

---
## Summary
The per-type `count` field is actually “issues with non-null cycle time”, not “closed issues of this type”, so types closed without a WIP transition are undercounted or disappear entirely.

## Severity
- Rule ID: `logic-error`
- Severity: `minor`
- Priority: `P2`

## Evidence
A closed issue can legitimately have no cycle time: [test_analytics.py](/home/john/filigree/tests/analytics/test_analytics.py:93) verifies `cycle_time()` returns `None` when an issue is closed without entering WIP.

In [analytics.py](/home/john/filigree/src/filigree/analytics.py:194), the type bucket is only updated when `ct is not None`:

```python
if ct is not None:
    cycle_times.append(ct)
    by_type.setdefault(issue.type, []).append(ct)
```

Then [analytics.py](/home/john/filigree/src/filigree/analytics.py:207) reports `count` as `len(times)`:

```python
type_metrics[issue_type] = {
    "avg_cycle_time_hours": round(sum(times) / len(times), 1) if times else None,
    "count": len(times),
}
```

Downstream code presents that count as closed-issue count, e.g. [admin.py](/home/john/filigree/src/filigree/cli_commands/admin.py:498):

```python
click.echo(f"    {t:<12} {m['count']} closed, avg cycle: {ct_str}")
```

## Root Cause Hypothesis
`analytics.py` uses one list to represent two different concepts: the sample set for averaging cycle times and the total number of closed issues per type. Once those were conflated, `count` started excluding valid closed issues that never entered WIP.

## Suggested Fix
Track per-type throughput separately from per-type cycle-time samples. For example, increment `type_counts[issue.type]` for every `recent_closed` issue, collect cycle-time samples only when `ct is not None`, and emit `avg_cycle_time_hours=None` with the correct closed count when a type has no measurable cycle-time samples.