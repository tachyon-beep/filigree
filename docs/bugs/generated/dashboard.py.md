## Summary
`[race-condition]` Concurrent cache misses in `ProjectStore.get_db()` can open multiple `FiligreeDB` connections for the same project, cache only one of them, and leak the others.

## Severity
- Severity: major
- Priority: P1

## Evidence
[dashboard.py](/home/john/filigree/src/filigree/dashboard.py:125) lines 125-141 does an unsynchronized check-then-open on shared state:

```python
if key not in self._dbs:
    ...
    db = FiligreeDB.from_filigree_dir(filigree_path, check_same_thread=False)
    self._dbs[key] = db
```

[dashboard.py](/home/john/filigree/src/filigree/dashboard.py:197) lines 197-214 shows `_get_db()` calls `ProjectStore.get_db()` for every server-mode request, and async routes depend on that resolver, e.g. [issues.py](/home/john/filigree/src/filigree/dashboard_routes/issues.py:52) lines 52-53 and [analytics.py](/home/john/filigree/src/filigree/dashboard_routes/analytics.py:463) lines 463-464.

[core.py](/home/john/filigree/src/filigree/core.py:471) lines 471-480 shows `FiligreeDB.from_filigree_dir()` creates and initializes a fresh DB object each time, so two racing requests really do create two separate SQLite handles.

## Root Cause Hypothesis
`ProjectStore` treats `_dbs` as a thread-safe cache, but it is mutated from a plain `def` dependency path. Under FastAPI, concurrent requests can resolve that dependency in parallel, so two requests can both observe “cache miss”, both open a DB, and the later assignment overwrites the earlier one. The overwritten handle is never closed.

## Suggested Fix
Add synchronization around the lazy-open path in `ProjectStore`, for example a `threading.Lock` or per-key lock. The open/store sequence needs to be atomic: re-check the cache under the lock, create exactly one `FiligreeDB`, cache it once, and close any loser if a race is detected. The same lock should guard other `_dbs` mutations like `close_all()` and `reload()`.

---
## Summary
`[error-handling]` FastAPI query-parameter validation errors bypass the dashboard’s flat 2.0 error envelope and return the default `{"detail": [...]}` 422 body instead.

## Severity
- Severity: major
- Priority: P2

## Evidence
[dashboard.py](/home/john/filigree/src/filigree/dashboard.py:296) lines 296-334 only installs an exception handler for `starlette.exceptions.HTTPException`:

```python
@app.exception_handler(_StarletteHTTPException)
async def _http_exception_to_envelope(...):
    ...
```

That same block claims to normalize dashboard errors into the flat envelope and even maps status `422` to `ErrorCode.VALIDATION` at [dashboard.py](/home/john/filigree/src/filigree/dashboard.py:302) lines 302-310, but FastAPI’s automatic parameter coercion failures are raised as `RequestValidationError`, not `HTTPException`.

Several routes rely on typed query params, so invalid input never reaches route code:
- [analytics.py](/home/john/filigree/src/filigree/dashboard_routes/analytics.py:463) lines 463-469: `api_metrics(days: int = 30, ...)`
- [analytics.py](/home/john/filigree/src/filigree/dashboard_routes/analytics.py:490) lines 490-493: `api_activity(limit: int = 50, since: str = "", ...)`
- [issues.py](/home/john/filigree/src/filigree/dashboard_routes/issues.py:305) lines 305-309: `api_search(q: str = "", limit: int = 50, offset: int = 0, ...)`

A request like `/api/metrics?days=abc` or `/api/search?limit=abc` therefore produces FastAPI’s default validation payload instead of the promised flat `{error, code, details?}` contract.

## Root Cause Hypothesis
The dashboard centralizes only `HTTPException` translation, but some validation happens before route execution in FastAPI’s request parsing layer. Those failures take a different exception path, so the custom envelope code never runs.

## Suggested Fix
Add a `RequestValidationError` handler in `create_app()` that returns the same flat envelope shape, for example with `ErrorCode.VALIDATION` and `details=exc.errors()`. Keep the status consistent with the intended contract, either preserving FastAPI’s `422` or normalizing to `400`, but make it uniform across all dashboard endpoints.