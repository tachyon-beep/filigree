## Summary
`api-misuse`: `GET /api/files/{file_id}/findings` is explicitly cacheable for 30 seconds, so the UI can re-read stale findings immediately after a PATCH and show the old status.

## Severity
- Rule ID: `api-misuse`
- Severity: major
- Priority: P1

## Evidence
`src/filigree/dashboard_routes/files.py:213-224` returns the findings page with a positive cache lifetime:
```python
result = db.get_findings_paginated(...)
...
return JSONResponse(result, headers={"Cache-Control": "max-age=30"})
```

`src/filigree/static/js/views/files.js:608-619` immediately reloads the findings tab after mutating a finding:
```javascript
const result = await patchFileFinding(state.selectedFile, _selectedFinding.id, {
  status: "fixed",
});
...
await loadFindingsTab(state.selectedFile, 0);
```

`src/filigree/static/js/views/files.js:496-513` and `src/filigree/static/js/api.js:382-387` re-fetch the same GET endpoint without any cache-busting:
```javascript
const data = await fetchFileFindings(fileId, params);
```

```javascript
const resp = await fetch(apiUrl(`/files/${encodeURIComponent(fileId)}/findings` + qs));
```

Because the GET response is marked `max-age=30`, the browser is allowed to serve the cached pre-PATCH payload, so a finding can still appear `open` right after the user marks it `fixed`.

## Root Cause Hypothesis
The route was optimized for short-term caching without accounting for the dashboard’s read-after-write flow. Other mutable file endpoints already use `no-cache`, but this one still advertises freshness for 30 seconds.

## Suggested Fix
Change the findings endpoint in `src/filigree/dashboard_routes/files.py` to return `Cache-Control: no-cache` or `no-store` instead of `max-age=30`. If caching is still desired, use validators such as ETag plus `must-revalidate`; the current unconditional `max-age=30` is unsafe for immediate post-mutation reads.

---
## Summary
`api-misuse`: `POST /api/files/{file_id}/associations` always reports `201 Created` even when the association already existed and the DB performed no insert.

## Severity
- Rule ID: `api-misuse`
- Severity: minor
- Priority: P2

## Evidence
`src/filigree/dashboard_routes/files.py:291-295` always returns a created response:
```python
db.add_file_association(file_id, issue_id, cast(AssocType, assoc_type))
...
return JSONResponse({"status": "created"}, status_code=201)
```

But `src/filigree/db_files.py:1194-1209` defines the underlying operation as idempotent and uses `INSERT OR IGNORE`:
```python
"""Link a file to an issue. Idempotent (duplicates ignored)."""
...
self.conn.execute(
    "INSERT OR IGNORE INTO file_associations (...) VALUES (?, ?, ?, ?)",
    (file_id, issue_id, assoc_type, now),
)
```

`tests/core/test_files.py:1123-1129` confirms duplicates are intentionally ignored:
```python
db.add_file_association(f.id, issue.id, "bug_in")
db.add_file_association(f.id, issue.id, "bug_in")
assocs = db.get_file_associations(f.id)
assert len(assocs) == 1
```

So the HTTP layer currently tells clients that a new association was created even when the write was a no-op.

## Root Cause Hypothesis
The route treats “call succeeded” and “new row inserted” as the same outcome. Since `add_file_association()` does not expose whether `INSERT OR IGNORE` inserted or skipped, the handler unconditionally emits `201 created`.

## Suggested Fix
Make the route distinguish between “inserted” and “already existed.” The simplest fix in `src/filigree/dashboard_routes/files.py` is to check for an existing `(file_id, issue_id, assoc_type)` before calling `add_file_association()`, then return:
- `201 {"status": "created"}` when a new row is inserted
- `200 {"status": "exists"}` or `204` when the association was already present

An alternative is to change the DB method to return a boolean indicating whether an insert occurred, then branch on that result in this route.