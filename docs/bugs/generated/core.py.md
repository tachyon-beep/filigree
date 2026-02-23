## Summary
`rule_id: type-error` — `process_scan_results()` accepts non-string finding fields and then crashes with uncaught runtime errors instead of returning validation errors.

## Severity
- Severity: major
- Priority: P1

## Evidence
- `path` is only checked for presence, not type: `/home/john/filigree/src/filigree/core.py:2769`
- Non-string `path` can flow into SQL binding: `/home/john/filigree/src/filigree/core.py:2828`
- `suggestion` is used with `len()` without type validation: `/home/john/filigree/src/filigree/core.py:2856`
- API only catches `ValueError`, so `TypeError`/`sqlite3.ProgrammingError` become 500s: `/home/john/filigree/src/filigree/dashboard.py:1319`

```python
# core.py
if "path" not in f: ...
...
if isinstance(f["path"], str):
    f["path"] = _normalize_scan_path(f["path"])
...
existing_file = self.conn.execute("SELECT id FROM file_records WHERE path = ?", (path,)).fetchone()
...
suggestion = f.get("suggestion", "")
if len(suggestion) > 10_000:
```

## Root Cause Hypothesis
Input validation checks required keys but not full value types, so malformed scanner payloads reach SQL/json operations that expect concrete types.

## Suggested Fix
In `process_scan_results()`, validate and/or coerce all typed fields before mutation (`path`, `rule_id`, `message`, `severity`, `language`, `suggestion`, `metadata`, `line_start`, `line_end`) and raise `ValueError` for bad types so callers handle them consistently.

---
## Summary
`rule_id: error-handling` — `process_scan_results()` can leave a dirty transaction after mid-batch exceptions, allowing partial writes to be committed later by unrelated operations.

## Severity
- Severity: critical
- Priority: P0

## Evidence
- Writes occur throughout loop: `/home/john/filigree/src/filigree/core.py:2827`
- Commit only at end, with no rollback guard: `/home/john/filigree/src/filigree/core.py:2942`
- Exception points exist after writes (example): `/home/john/filigree/src/filigree/core.py:2857`, `/home/john/filigree/src/filigree/core.py:2892`
- Dashboard path has no transaction safety net like MCP’s `finally: rollback if in_transaction`: `/home/john/filigree/src/filigree/dashboard.py:303`, `/home/john/filigree/src/filigree/mcp_server.py:1077`

```python
# core.py
for f in findings:
    ...  # INSERT/UPDATE file_records and scan_findings
self.conn.commit()
```

Reproduction in-memory: first finding writes successfully, second malformed finding raises `TypeError`, and the first write remains pending and gets committed by a later successful DB operation.

## Root Cause Hypothesis
Method assumes all runtime failures are prevented by pre-validation, but post-validation exceptions still happen and are not rollback-protected.

## Suggested Fix
Wrap the mutation phase of `process_scan_results()` in `try/except` with `self.conn.rollback()` on any exception (or use an explicit transaction context) so partial writes cannot survive failures.

---
## Summary
`rule_id: logic-error` — `import_jsonl(merge=False)` silently ignores duplicate `event` conflicts and overcounts imported records, violating its own merge contract.

## Severity
- Severity: major
- Priority: P2

## Evidence
- Contract says `merge=False` should raise on conflict: `/home/john/filigree/src/filigree/core.py:2376`
- Conflict mode is computed: `/home/john/filigree/src/filigree/core.py:2381`
- Most branches use `INSERT {conflict}`: `/home/john/filigree/src/filigree/core.py:2393`, `/home/john/filigree/src/filigree/core.py:2415`, `/home/john/filigree/src/filigree/core.py:2425`, `/home/john/filigree/src/filigree/core.py:2430`
- `event` branch hardcodes `INSERT OR IGNORE`: `/home/john/filigree/src/filigree/core.py:2439`
- Count increments unconditionally even when insert was ignored: `/home/john/filigree/src/filigree/core.py:2456`

```python
conflict = "OR IGNORE" if merge else "OR ABORT"
...
elif record_type == "event":
    self.conn.execute("INSERT OR IGNORE INTO events ...")
...
count += 1
```

## Root Cause Hypothesis
The `event` branch drifted from shared conflict handling and uses processed-line counting instead of inserted-row counting.

## Suggested Fix
Use `INSERT {conflict}` for `event` records too, and increment `count` based on actual insertion (`cursor.rowcount`) so `merge=True` reports accurate imported rows and `merge=False` enforces abort semantics.