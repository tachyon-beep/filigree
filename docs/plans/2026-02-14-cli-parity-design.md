# CLI Parity with MCP Server

**Date:** 2026-02-14
**Status:** Approved
**Motivation:** Background subagents can't use MCP tools (Claude Code limitation). A fully-featured CLI lets agents interact with keel via `Bash` tool calls instead. Dogfooding session revealed the gap.

## Scope

Full pass: add 8 missing commands, retrofit `--actor` on all mutations, ensure `--json` on every command.

## 1. Global `--actor` Flag

Add `--actor` option to the `@click.group()`, stored in `ctx.obj["actor"]` (default: `"cli"`). All mutation commands read from context instead of hardcoding `actor="cli"`.

Commands affected: `create`, `update`, `close`, `reopen`, `dep-add`, `dep-remove`, `comment`, `label add`, `label remove`, `release`, `archive`, `undo`, plus all new commands.

The `comment` command uses `author=` parameter on `db.add_comment()` — thread `--actor` into that as well.

## 2. `--json` Retrofit

Add `@click.option("--json", "as_json", is_flag=True)` to these 11 commands:

- `comment` — output: `{"comment_id": "...", "issue_id": "..."}`
- `comments` — output: `[{"id": "...", "author": "...", "text": "...", "created_at": "..."}]`
- `dep-add` — output: `{"from_id": "...", "to_id": "...", "status": "added"}`
- `dep-remove` — output: `{"from_id": "...", "to_id": "...", "status": "removed"}`
- `workflow-states` — output: `{"open": [...], "wip": [...], "done": [...]}`
- `undo` — output: `{"undone": true, "event_type": "...", "event_id": ...}` or `{"undone": false, "reason": "..."}`
- `guide` — output: `{"pack": "...", "guide": {...}}`
- `archive` — output: `{"archived": [...], "count": N}`
- `compact` — output: `{"deleted_events": N}`
- `label add` — output: `{"issue_id": "...", "label": "...", "status": "added"}`
- `label remove` — output: `{"issue_id": "...", "label": "...", "status": "removed"}`

Also add `--json` to `close`, `reopen`, `create` which currently have no JSON output.

## 3. New Commands

### `keel claim <id> --assignee <name>`
Atomic claim with optimistic locking. Calls `db.claim_issue()`. Fails if already claimed or not open.

### `keel claim-next --assignee <name> [--type TYPE] [--priority-min N] [--priority-max N]`
Claim the highest-priority ready issue matching filters. Calls `db.claim_next()`. Prints claimed issue or "No issues available".

### `keel create-plan [--file FILE]`
Create milestone/phase/step hierarchy from JSON input. Reads from `--file` or stdin. JSON structure matches MCP's `create_plan` schema:
```json
{
  "milestone": {"title": "...", "description": "..."},
  "phases": [{"title": "...", "steps": [{"title": "...", "deps": []}]}]
}
```
Calls `db.create_plan()`. Outputs the plan tree.

### `keel batch-update <ids...> [--status S] [--priority N] [--assignee A] [--fields key=val]`
Update multiple issues with the same changes. Per-issue error reporting (continues on failure). Calls `db.batch_update()`.

### `keel batch-close <ids...> [--reason R]`
Close multiple issues with per-item error reporting. Separate from existing `close` to preserve its simple UX. Calls `db.batch_close()`.

### `keel changes --since <timestamp> [--limit N]`
Get events since a timestamp (ISO format). For session resumption. Calls `db.get_events_since()`.

### `keel events <id> [--limit N]`
Get event history for a specific issue, newest first. Calls `db.get_issue_events()`.

### `keel explain-state <type> <state>`
Explain a state within a type's workflow: category, inbound/outbound transitions, required fields. Uses template introspection (same logic as MCP's `explain_state` handler).

All new commands include `--json` flag from the start.

## 4. Implementation Order

1. Global `--actor` infrastructure (click group context)
2. Retrofit `--actor` on existing mutation commands
3. Add `--json` to commands missing it
4. Add 8 new commands
5. Tests (`tests/test_cli.py`)

## 5. Testing

Use Click's `CliRunner` with a `tmp_keel_dir` fixture. One test per new command, one test per `--json` retrofit verifying parseable output. Follow existing `test_mcp.py` patterns for fixture setup.

## 6. Non-Goals

- No changes to MCP server
- No new core.py methods (all needed methods exist)
- No output format changes to existing human-readable output
- No interactive prompts (all commands are non-interactive for agent compatibility)
