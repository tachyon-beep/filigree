## Summary
`error-handling`: `templates reload` can throw a raw `ValueError` when `.filigree/config.json` is corrupt instead of failing cleanly.

## Severity
- Severity: major
- Priority: P1

## Evidence
[workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:40) enters the reload path with no error handling, and [workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:44) prints success immediately afterward:

```python
with get_db() as db:
    db.reload_templates()
    click.echo("Templates reloaded")
```

[db_workflow.py](/home/john/filigree/src/filigree/db_workflow.py:119) shows `reload_templates()` re-reads config when no override is present, and [db_workflow.py](/home/john/filigree/src/filigree/db_workflow.py:132) raises `ValueError` if `config.json` cannot be parsed. [core.py](/home/john/filigree/src/filigree/core.py:280) is more permissive on normal startup, so a bad config can survive until this command. The MCP sibling already treats this as a handled validation failure at [mcp_tools/workflow.py](/home/john/filigree/src/filigree/mcp_tools/workflow.py:365) and [mcp_tools/workflow.py](/home/john/filigree/src/filigree/mcp_tools/workflow.py:369).

## Root Cause Hypothesis
The CLI assumes reload is just cache invalidation and cannot fail, but `db.reload_templates()` now performs config revalidation and can raise `ValueError`.

## Suggested Fix
Wrap `db.reload_templates()` in `try/except ValueError`, emit a normal CLI error, and exit 1 instead of letting the exception escape. Mirroring the MCP handler is the safest path.

---
## Summary
`api-misuse`: `templates reload` reports success before any real template reload is materialized, and it never refreshes `context.md`.

## Severity
- Severity: major
- Priority: P2

## Evidence
[workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:44) says `"Templates reloaded"` immediately after `db.reload_templates()`. But [db_workflow.py](/home/john/filigree/src/filigree/db_workflow.py:113) documents that `reload_templates()` only clears the cached registry so it reloads on the next access. The MCP implementation explicitly forces materialization at [mcp_tools/workflow.py](/home/john/filigree/src/filigree/mcp_tools/workflow.py:368) and refreshes summary output at [mcp_tools/workflow.py](/home/john/filigree/src/filigree/mcp_tools/workflow.py:371). The CLI has the corresponding helper available at [cli_common.py](/home/john/filigree/src/filigree/cli_common.py:54) but never calls it here. A regression test already exists for the MCP surface at [tests/mcp/test_tools.py](/home/john/filigree/tests/mcp/test_tools.py:925), which shows this stale-summary case is considered real project behavior.

## Root Cause Hypothesis
The command treats cache invalidation as equivalent to a completed reload. That hides deferred load failures until a later command and leaves template-derived summary/context data stale.

## Suggested Fix
After `db.reload_templates()`, immediately touch the registry with `db.templates.list_types()` so the new templates are actually loaded, then call `refresh_summary(db)`, and only then print success.

---
## Summary
`api-misuse`: the workflow CLI module violates the documented `--json` contract, making several automation paths non-machine-readable.

## Severity
- Severity: major
- Priority: P2

## Evidence
[docs/cli.md](/home/john/filigree/docs/cli.md:3) says all commands support `--json` for machine-readable output, and [agent-integration.md](/home/john/filigree/docs/agent-integration.md:18) says background agents rely on CLI `--json`.

That contract is broken in two ways inside the target file:

```python
@click.group(invoke_without_command=True)
@click.option("--type", "issue_type", default=None, help="Show specific template")
def templates(...)

if tpl is None:
    click.echo(f"Unknown type: {type_name}", err=True)
    sys.exit(1)
```

[workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:15) defines `templates` with no `--json` option at all, and [workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:40) does the same for `templates reload`. Separately, the JSON-capable commands still drop to plain-text errors: [workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:97) for `type-info`, [workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:155) for `transitions`, [workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:228) for `validate`, and [workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:307) plus [workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:316) for `explain-state`. By contrast, [workflow.py](/home/john/filigree/src/filigree/cli_commands/workflow.py:265) already shows the correct JSON error pattern in `guide`.

## Root Cause Hypothesis
This module added `--json` support only on selected success paths, but did not standardize option coverage or error-envelope handling across the workflow commands.

## Suggested Fix
Add `--json` to `templates` and `templates reload`, and make every error branch honor `as_json` by returning `{"error": ..., "code": ...}` with the appropriate `ErrorCode`. Add regression tests for `filigree templates --json`, `filigree templates reload --json`, and failing `--json` calls for `type-info`, `transitions`, `validate`, and `explain-state`.