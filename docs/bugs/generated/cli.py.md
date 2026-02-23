## Summary
`import` has an uncaught file I/O failure path (`rule_id: error-handling`).

## Severity
- Severity: major
- Priority: P1

## Evidence
`src/filigree/cli.py:1079` only catches `(JSONDecodeError, KeyError, ValueError, sqlite3.IntegrityError)` around `db.import_jsonl(...)`.
`src/filigree/core.py:2383` opens the file with `Path(input_path).open()`, which can raise `OSError`/`PermissionError` for unreadable files.
That exception is not caught in the CLI command, so users get an unhandled exception path.

## Root Cause Hypothesis
The CLI assumes `click.Path(exists=True)` is sufficient validation, but existence does not guarantee readability.

## Suggested Fix
In `import_data`, also catch `OSError` (and ideally `UnicodeDecodeError` explicitly for clarity) and return a normal CLI error message with exit code 1.

---
## Summary
`claim-next` can crash on invalid assignee input because `ValueError` is uncaught (`rule_id: error-handling`).

## Severity
- Severity: minor
- Priority: P3

## Evidence
`src/filigree/cli.py:1459` calls `db.claim_next(...)` without a `try/except`.
`src/filigree/core.py:1332` raises `ValueError("Assignee cannot be empty")` when assignee is blank/whitespace.
A user can pass whitespace (for example `--assignee "   "`), triggering an unhandled exception path.

## Root Cause Hypothesis
Validation is delegated to core, but this CLI command omitted the error handling pattern used by `claim`.

## Suggested Fix
Wrap `db.claim_next(...)` in `try/except ValueError` and emit structured JSON/plain-text errors like other commands, then `sys.exit(1)`.

---
## Summary
`server register` does not handle expected registration validation failures (`rule_id: error-handling`).

## Severity
- Severity: major
- Priority: P1

## Evidence
`src/filigree/cli.py:1882` directly calls `register_project(filigree_dir)` with no error handling.
`src/filigree/server.py:76` and `src/filigree/server.py:95` can raise `ValueError` (unsupported schema version, prefix collision).
Those become uncaught CLI exceptions instead of user-facing command errors.

## Root Cause Hypothesis
The command validates path existence but not domain-level registration errors returned by server registration logic.

## Suggested Fix
Add `try/except` around `register_project(...)` in `server_register`, catch `ValueError`/`OSError`, print a clean error, and exit with code 1.