## Summary
`mark_unseen` is coerced with `bool(...)`, so string values like `"false"` are treated as `True` and can incorrectly mark findings as unseen (`rule_id: type-error`).

## Severity
- Severity: major
- Priority: P1

## Evidence
`src/filigree/dashboard.py:1317`:
```python
mark_unseen=bool(body.get("mark_unseen", False)),
```
`bool("false")` is `True`, so non-empty strings flip behavior.

`src/filigree/core.py:2930` applies that flag destructively:
```python
if mark_unseen:
    ... UPDATE scan_findings SET status = 'unseen_in_latest' ...
```

Expected behavior is explicitly tested when `mark_unseen=False` (no status changes): `tests/test_files.py:694`.

## Root Cause Hypothesis
The handler uses Python truthiness coercion instead of strict JSON boolean validation/parsing.

## Suggested Fix
In `api_scan_results`, require `mark_unseen` to be a real boolean (or parse string values explicitly with `_parse_bool_value`), and return `400` on invalid types.

---
## Summary
`/api/v1/scan-results` does not validate `scan_source` type; non-string values can trigger uncaught DB binding errors (500) (`rule_id: error-handling`).

## Severity
- Severity: major
- Priority: P2

## Evidence
`src/filigree/dashboard.py:1307-1309` only checks truthiness:
```python
scan_source = body.get("scan_source", "")
if not scan_source:
    return ...
```
No `isinstance(scan_source, str)` check.

It only catches `ValueError` from DB call (`src/filigree/dashboard.py:1319`), but DB uses `scan_source` as SQL param (`src/filigree/core.py:2867-2869`), where unsupported types (e.g. list) raise `sqlite3.ProgrammingError`, not `ValueError`.

## Root Cause Hypothesis
Input validation is incomplete, and exception mapping assumes only domain-validation failures.

## Suggested Fix
Validate `scan_source` as a non-empty string before DB call; optionally broaden exception handling to convert DB binding/type failures into `400 VALIDATION_ERROR`.

---
## Summary
Pagination `limit`/`offset` values accept negatives, enabling effectively unbounded queries (`rule_id: performance`).

## Severity
- Severity: major
- Priority: P2

## Evidence
`src/filigree/dashboard.py:199-203` (`_safe_int`) only parses integer, no bounds:
```python
return int(value)
```

Used directly for limits in multiple endpoints:
- `src/filigree/dashboard.py:1116` (`/api/files`)
- `src/filigree/dashboard.py:1142` (`/api/files/hotspots`)
- `src/filigree/dashboard.py:1239` (`/api/files/{file_id}/findings`)
- `src/filigree/dashboard.py:1327` (`/api/scan-runs`)

These flow into SQL `LIMIT ? OFFSET ?`:
- `src/filigree/core.py:2719`
- `src/filigree/core.py:3090`
- `src/filigree/core.py:3307`
- `src/filigree/core.py:2961`

In SQLite, `LIMIT -1` means “no limit”, so negative values bypass intended caps.

## Root Cause Hypothesis
The generic int parser is reused for pagination without endpoint-specific lower bounds.

## Suggested Fix
Use bounded validation for pagination (`limit >= 1`, `offset >= 0`) in dashboard handlers, ideally via `_safe_bounded_int` for consistency.