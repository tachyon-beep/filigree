## Summary
`error-handling`: `get_db()` still lets malformed `.filigree.conf` values crash the CLI with a raw `TypeError`.

## Severity
- Severity: major
- Priority: P2

## Evidence
[cli_common.py](/home/john/filigree/src/filigree/cli_common.py:36) says `get_db()` is supposed to turn corrupt-conf failures into clean CLI exits, but [cli_common.py](/home/john/filigree/src/filigree/cli_common.py:45) only catches `ValueError`, `OSError`, and `sqlite3.Error` after discovery:

```python
except (ValueError, OSError, sqlite3.Error) as exc:
    click.echo(f"Error opening project database: {exc}", err=True)
    sys.exit(1)
```

That misses real startup failures from the conf path:
- [core.py](/home/john/filigree/src/filigree/core.py:445) explicitly raises `TypeError` when `enabled_packs` is a bare string.
- [core.py](/home/john/filigree/src/filigree/core.py:497) builds `db_path` directly from `data["db"]`, so a non-string `db` value also raises `TypeError`.
- [core.py](/home/john/filigree/src/filigree/core.py:258) `read_conf()` only checks that `prefix` and `db` keys exist; it does not validate their types before `from_conf()` uses them.

That means a malformed but JSON-valid config like `{"prefix":"x","db":123}` or `{"prefix":"x","db":"...","enabled_packs":"core"}` bypasses the clean-error path and explodes out of every CLI command that calls `get_db()`.

## Root Cause Hypothesis
`get_db()` was hardened around `ValueError`-style config failures, but later config validation added `TypeError` paths in `FiligreeDB.__init__()` and `FiligreeDB.from_conf()` without updating this shared CLI wrapper.

## Suggested Fix
Catch `TypeError` in [cli_common.py](/home/john/filigree/src/filigree/cli_common.py:45) alongside the existing startup exceptions. A stronger follow-up would be to validate `.filigree.conf` field types in `read_conf()` or `from_conf()` so the error can name the bad key directly.

---
## Summary
`error-handling`: `get_db()` does not normalize discovery failures when the current working directory itself is invalid.

## Severity
- Severity: minor
- Priority: P3

## Evidence
[cli_common.py](/home/john/filigree/src/filigree/cli_common.py:40) wraps discovery in:

```python
try:
    project_root, conf_path = find_filigree_anchor()
except ProjectNotInitialisedError as exc:
    click.echo(str(exc), err=True)
    sys.exit(1)
```

But [core.py](/home/john/filigree/src/filigree/core.py:211) starts discovery with:

```python
current = (start or Path.cwd()).resolve()
```

If the shell is sitting in a deleted directory, `Path.cwd()` raises `FileNotFoundError` before `find_filigree_anchor()` can raise `ProjectNotInitialisedError`. The same block can also raise other low-level path-resolution errors before the helper’s friendly message path is reached. Because `get_db()` only catches `ProjectNotInitialisedError` here, those cases leak a raw traceback out of every CLI command.

## Root Cause Hypothesis
The wrapper assumes all discovery failures will be expressed as Filigree’s own `ProjectNotInitialisedError`, but `Path.cwd()` and path resolution can fail earlier with plain OS/runtime exceptions.

## Suggested Fix
Broaden the first `except` in [cli_common.py](/home/john/filigree/src/filigree/cli_common.py:40) to also catch discovery-time `OSError`/`FileNotFoundError` and convert them into a clean CLI error. If you want to be defensive against symlink-resolution edge cases too, include `RuntimeError` with a message that tells the user to `cd` into a valid directory first.