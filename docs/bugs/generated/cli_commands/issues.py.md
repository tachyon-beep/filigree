## Summary
`api-misuse`: `filigree list --json` falls back to Click’s plain-text error path for invalid filters, so automation loses machine-readable output exactly when validation fails.

## Severity
- Severity: major
- Priority: P2

## Evidence
At [issues.py](/home/john/filigree/src/filigree/cli_commands/issues.py:167), the `list` command catches `db.list_issues()` validation failures and always re-raises them as `click.ClickException`, without checking `as_json`. One concrete trigger is [db_issues.py](/home/john/filigree/src/filigree/db_issues.py:1011), which rejects `--label-prefix` values that do not end with `:`. That contradicts the CLI contract in [docs/cli.md](/home/john/filigree/docs/cli.md:3), which says all commands support `--json`.

```python
try:
    issues = db.list_issues(...)
except ValueError as e:
    raise click.ClickException(str(e)) from e
```

A call like `filigree list --label-prefix cluster --json` will therefore print Click text instead of a JSON error envelope.

## Root Cause Hypothesis
`list_issues()` only applies `as_json` on the success path. Its validation branch was left using Click’s default exception renderer, unlike the other JSON-capable commands in this module.

## Suggested Fix
In the `except ValueError` block, honor `as_json`: emit `{"error": ..., "code": ...}` and exit non-zero, instead of raising `ClickException`. A regression test should cover an invalid filter such as `--label-prefix cluster --json`.

---
## Summary
`api-misuse`: `claim`, `claim-next`, and `release` hard-code JSON `code=CONFLICT` for every `ValueError`, including bad input that never reached a real ownership conflict.

## Severity
- Severity: minor
- Priority: P2

## Evidence
The JSON handlers at [issues.py](/home/john/filigree/src/filigree/cli_commands/issues.py:363), [issues.py](/home/john/filigree/src/filigree/cli_commands/issues.py:402), and [issues.py](/home/john/filigree/src/filigree/cli_commands/issues.py:440) map every `ValueError` to `ErrorCode.CONFLICT`.

```python
except ValueError as e:
    if as_json:
        click.echo(json_mod.dumps({"error": str(e), "code": ErrorCode.CONFLICT}))
```

But some of those `ValueError`s are plain validation failures:
- [db_issues.py](/home/john/filigree/src/filigree/db_issues.py:749) and [db_issues.py](/home/john/filigree/src/filigree/db_issues.py:856) reject blank or whitespace-only assignees before any claim attempt.
- [core.py](/home/john/filigree/src/filigree/core.py:561) raises `WrongProjectError` for foreign issue IDs, and [db_issues.py](/home/john/filigree/src/filigree/db_issues.py:752) plus [db_issues.py](/home/john/filigree/src/filigree/db_issues.py:806) call that guard before mutation.

So `filigree claim-next --assignee "   " --json` returns a conflict code for malformed input, and `filigree release foreign-abc123 --json` reports a conflict even though the real problem is “wrong project.”

## Root Cause Hypothesis
These CLI handlers assume every `ValueError` from claim/release flows means a state collision. The DB layer also uses `ValueError` subclasses for caller mistakes, so the CLI collapses validation and conflict into the same wire code.

## Suggested Fix
Pre-validate `assignee` in `claim` and `claim-next`, and catch `WrongProjectError` separately in `claim` and `release`. Keep `CONFLICT` only for genuine state/contention failures; return a validation-style code for malformed input.

---
## Summary
`performance`: `claim-next` rewrites `context.md` even when no issue was claimed.

## Severity
- Severity: minor
- Priority: P3

## Evidence
When `db.claim_next()` returns `None`, [issues.py](/home/john/filigree/src/filigree/cli_commands/issues.py:408) prints the empty result, but [issues.py](/home/john/filigree/src/filigree/cli_commands/issues.py:418) still calls `refresh_summary(db)` unconditionally.

```python
if issue is None:
    click.echo("No issues available")
else:
    click.echo(...)
refresh_summary(db)
```

That helper is explicitly mutation-oriented in [cli_common.py](/home/john/filigree/src/filigree/cli_common.py:54), and it is not cheap: [summary.py](/home/john/filigree/src/filigree/summary.py:70) regenerates stats, ready work, blocked work, WIP, and recent events, while [summary.py](/home/john/filigree/src/filigree/summary.py:323) always writes through a temp file and `os.replace()`.

This means repeated “no work available” polling still does full summary recomputation and filesystem churn.

## Root Cause Hypothesis
The summary refresh was placed after the whole command instead of inside the successful-claim branch, so a no-op path is treated like a mutating one.

## Suggested Fix
Only call `refresh_summary(db)` when `issue is not None`. Add a regression test that empty `claim-next` does not invoke the summary writer.

`uv run` repros were not possible in this sandbox because the filesystem is read-only, so these findings are source-traced rather than executed.