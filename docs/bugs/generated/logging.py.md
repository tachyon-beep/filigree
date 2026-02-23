## Summary
`setup_logging()` leaks and duplicates file handlers when called with a different `filigree_dir` in the same process, causing one log event to be written to multiple project log files.

## Severity
- Severity: major
- Priority: P1
- Rule ID: resource-leak

## Evidence
`/home/john/filigree/src/filigree/logging.py:49` creates a single process-global logger (`logging.getLogger("filigree")`).

`/home/john/filigree/src/filigree/logging.py:55` to `/home/john/filigree/src/filigree/logging.py:57` only short-circuits when an existing handler matches the same `target_filename`:

```python
for h in logger.handlers:
    if isinstance(h, RotatingFileHandler) and h.baseFilename == target_filename:
        return logger
```

`/home/john/filigree/src/filigree/logging.py:59` to `/home/john/filigree/src/filigree/logging.py:65` always adds another `RotatingFileHandler` otherwise, without removing/closing older file handlers for different paths:

```python
handler = RotatingFileHandler(str(log_path), ...)
logger.addHandler(handler)
```

So repeated calls with different directories accumulate open handlers and broadcast each record to all of them.

## Root Cause Hypothesis
The implementation treats setup as idempotent only per-path, but uses one global logger name across the process. That combination makes reconfiguration additive instead of replacing old file sinks.

## Suggested Fix
In `setup_logging()`, while holding `_setup_lock`, remove and close existing `RotatingFileHandler`s whose `baseFilename` differs from `target_filename` before adding the new handler (or use a per-path logger name). Example approach:
1. Collect `RotatingFileHandler`s on `logger.handlers`.
2. If one matches `target_filename`, return early.
3. For non-matching ones: `logger.removeHandler(h)` then `h.close()`.
4. Add exactly one handler for the requested path.