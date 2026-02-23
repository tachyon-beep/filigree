## Summary
`[type-error]` Non-numeric `priority` values from beads can crash migration and roll back everything.

## Severity
- Severity: major
- Priority: P1

## Evidence
- `src/filigree/migrate.py:93` reads raw `row["priority"]` directly.
- `src/filigree/migrate.py:94` does `max(0, min(4, priority))`, which raises `TypeError` if `priority` is a `str` like `"high"`.
- SQLite accepts non-numeric text in `INTEGER`-affinity columns, so this is reachable with real-world dirty data.
- `src/filigree/migrate.py:215` rolls back on any exception, so one bad row aborts the whole migration.

## Root Cause Hypothesis
Priority normalization assumes the source value is already numeric and comparable to `int`.

## Suggested Fix
Coerce safely before clamping:
- Parse with `int(...)` when not `None`.
- On `TypeError`/`ValueError`, fall back to default `2`.
- Then clamp to `[0, 4]`.

---
## Summary
`[logic-error]` Missing timestamps are forced to `""`, which silently corrupts ordering, dedup behavior, and metrics.

## Severity
- Severity: major
- Priority: P1

## Evidence
- `src/filigree/migrate.py:122-123` writes `created_at`/`updated_at` as `""` when source is null.
- `src/filigree/migrate.py:166` writes event `created_at` as `""` when source is null.
- `src/filigree/migrate.py:202` writes comment `created_at` as `""` when source is null.
- `src/filigree/core.py:201-203` event dedup uniqueness includes `created_at`; collapsing nulls to `""` can cause distinct events to be dropped by `INSERT OR IGNORE`.
- `src/filigree/analytics.py:15-27` treats invalid timestamps as unparsable (`None`), so lead/cycle calculations are skipped.
- `src/filigree/summary.py:40-52` replaces invalid timestamps with current time, skewing recency views.

## Root Cause Hypothesis
Migration uses empty strings as sentinel timestamp values instead of preserving nullability semantics or generating valid ISO timestamps.

## Suggested Fix
Do not emit `""` timestamps:
- For issues/events/comments, use source timestamp if present, otherwise a valid ISO fallback (`now`).
- For bulk inserts, prefer omitting timestamp keys when null so core defaults apply (`src/filigree/core.py:2291-2292`, `src/filigree/core.py:2317`).