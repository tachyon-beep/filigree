## Summary
`logic-error` Malformed `updated_at` values are treated as “now,” so stale WIP issues are silently excluded from the `Stale` section.

## Severity
- Severity: major
- Priority: P2

## Evidence
- `src/filigree/summary.py:51`-`src/filigree/summary.py:53`:
```python
except (ValueError, TypeError):
    return datetime.now(UTC)
```
- `src/filigree/summary.py:188`-`src/filigree/summary.py:190` only marks stale when parsed timestamp is older than cutoff:
```python
stale = [i for i in in_progress if _parse_iso(i.updated_at) < stale_cutoff]
```
If parsing fails, fallback is current time, so condition is false.
- `src/filigree/core.py:2406` imports `updated_at` without ISO validation, making malformed timestamps realistic in practice.

## Root Cause Hypothesis
The parse-failure fallback was chosen to avoid exceptions, but using `now` changes semantics and hides bad data from stale detection.

## Suggested Fix
Return a sentinel (`None`) on parse failure and handle it explicitly in stale logic (e.g., treat as stale/unknown and surface it in output rather than treating it as fresh).

---
## Summary
`resource-leak` `write_summary()` leaks a file descriptor when `os.fdopen()` fails.

## Severity
- Severity: major
- Priority: P1

## Evidence
- `src/filigree/summary.py:289` allocates raw FD:
```python
fd, tmp_name = tempfile.mkstemp(...)
```
- `src/filigree/summary.py:291` wraps it:
```python
with os.fdopen(fd, "w", encoding="utf-8") as f:
```
If `os.fdopen` raises, control goes to `except`.
- `src/filigree/summary.py:294`-`src/filigree/summary.py:296` cleanup only unlinks temp path:
```python
with contextlib.suppress(OSError):
    os.unlink(tmp_name)
```
No `os.close(fd)` occurs on this path.

## Root Cause Hypothesis
Cleanup logic assumes `os.fdopen` always succeeds and closes the descriptor via context manager, but failure before entering `with` leaves the FD open.

## Suggested Fix
Track whether FD ownership transferred to `fdopen`; if not, explicitly close it in the exception path (or in `finally`) before unlinking temp file.