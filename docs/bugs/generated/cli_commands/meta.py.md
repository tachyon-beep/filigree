## Summary
Wrong-project IDs are misreported as `NOT_FOUND` by the single-item mutating meta commands.

## Severity
- Severity: minor
- Priority: P2
- Rule ID: error-handling

## Evidence
`src/filigree/cli_commands/meta.py:22-29` does a preflight existence check and immediately exits as not found:

```python
try:
    db.get_issue(issue_id)
except KeyError:
    ...
    sys.exit(1)
```

The same pattern is used by `add-label` and `remove-label` at `src/filigree/cli_commands/meta.py:76-83` and `src/filigree/cli_commands/meta.py:110-117`.

But `get_issue()` is explicitly a read path that does not validate cross-project prefixes: `src/filigree/db_issues.py:331-335`.

```python
def get_issue(self, issue_id: str) -> Issue:
    # Reads do not enforce prefix-matching — cross-project lookups simply
    # return KeyError if not found.
    return self._build_issue(issue_id)
```

The actual write paths for these commands do validate prefixes:
- `src/filigree/db_meta.py:34-38` for `add_comment`
- `src/filigree/db_meta.py:64-71` for `add_label`
- `src/filigree/db_meta.py:89-92` for `remove_label`

Those call `_check_id_prefix()`, which raises `WrongProjectError` (`src/filigree/core.py:144-150`, `src/filigree/core.py:561-586`).

## Root Cause Hypothesis
`meta.py` tries to turn nonexistent issues into a clean `NOT_FOUND` before calling the mutator, but it uses `get_issue()` for that check. Because `get_issue()` intentionally skips prefix validation, a foreign ID is downgraded from a helpful “wrong project” validation failure into a misleading “not found” error.

## Suggested Fix
Make the preflight prefix-aware before translating errors. The safest fix in this file is to validate the ID prefix before calling `get_issue()`, or to centralize a helper that does:

1. `_check_id_prefix(issue_id)` first, mapping `WrongProjectError` to a validation-style CLI error.
2. `get_issue(issue_id)` second, mapping true absence to `NOT_FOUND`.

That preserves the current clean nonexistent-issue UX without masking cross-project mistakes.

---
## Summary
`batch-add-label` text output can claim the raw argv label was applied even when the stored label was normalized to a different canonical value.

## Severity
- Severity: trivial
- Priority: P3
- Rule ID: logic-error

## Evidence
Single-item label commands correctly use the canonical label returned from the DB:
- `src/filigree/cli_commands/meta.py:85-99`
- `src/filigree/cli_commands/meta.py:119-133`

`db.add_label()` normalizes labels before storage, including stripping whitespace: `src/filigree/db_workflow.py:263-288`, then returns the canonical value via `src/filigree/db_meta.py:64-87`.

By contrast, `batch-add-label` ignores canonicalization and echoes the raw CLI argument:
- `src/filigree/cli_commands/meta.py:324`
- `src/filigree/cli_commands/meta.py:338-342`

```python
if row["status"] == "added":
    click.echo(f"  Added label '{label_name}' to {row['id']}")
```

So `filigree batch-add-label "  urgent  " <id>` stores `urgent`, but stdout says it added `'  urgent  '`.

## Root Cause Hypothesis
The batch DB API only returns `{id, status}` rows, so this CLI path fell back to printing the original argument instead of the canonicalized label. The single-item commands were fixed to use canonical labels, but the batch variant was left behind.

## Suggested Fix
Normalize `label_name` before rendering, or better, carry the canonical label through the batch result and print that instead. Matching the single-item command behavior is the least surprising option.