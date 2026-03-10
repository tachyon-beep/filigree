# MCP Scan Trigger — Design

**Date:** 2026-02-22
**Status:** Approved

## Problem

Agents using filigree via MCP have no way to trigger a bug scan on a file.
Scanner scripts exist (`scripts/codex_bug_hunt.py`, `scripts/claude_bug_hunt.py`)
but must be run manually from the terminal. An MCP tool would let agents
trigger scans inline during their workflow.

## Design

### Scanner TOML Registry

Scanner definitions live in `.filigree/scanners/*.toml`. Each file defines
one scanner:

```toml
# .filigree/scanners/claude.toml
[scanner]
name = "claude"
description = "Bug hunt using Claude CLI"
command = "python scripts/claude_bug_hunt.py"
args = ["--root", "{file}", "--max-files", "1", "--api-url", "{api_url}"]
file_types = ["py"]
```

Template variables: `{file}` (target path), `{api_url}` (dashboard URL,
default `http://localhost:8377`), `{project_root}` (filigree project root).

The directory is created by `filigree init` but starts empty. Users add
scanner configs to activate them. Example TOMLs ship in
`scripts/scanners/*.toml.example`.

### MCP Tools

**`list_scanners`** — no parameters. Reads `.filigree/scanners/*.toml`,
returns `{scanners: [{name, description, file_types}]}`. Empty array if
no scanners registered.

**`trigger_scan`** — parameters: `scanner` (required), `file_path`
(required), `api_url` (optional, default `http://localhost:8377`).

Behavior:
1. Validate scanner exists in registry, file exists on disk
2. Register file in `file_records` via `db.register_file()` → get `file_id`
3. Generate `scan_run_id` as `{scanner}-{ISO-timestamp}`
4. Build command from TOML template, substituting `{file}`, `{api_url}`,
   `{project_root}`
5. Spawn detached subprocess (`start_new_session=True`, stdout/stderr to
   devnull) so it survives MCP server lifecycle
6. Return `{scan_run_id, file_id, file_path, scanner, pid}`

### Async Model

Fire-and-forget. The scanner subprocess POSTs findings to the scan API
when done. The agent checks results later via the file's findings:

1. `list_scanners` → discover available scanners
2. `trigger_scan(scanner="claude", file_path="src/foo.py")` → get `file_id`
3. (Later) Check `GET /api/files/{file_id}/findings` for results

No new DB tables, no in-memory state tracking, no new API endpoints.

### Documentation Updates

- `docs/mcp.md` — add Scanning tool category with `list_scanners` and
  `trigger_scan`
- `CLAUDE.md` — add scanner registry and MCP workflow to instructions
- `scripts/scanners/*.toml.example` — example configs for codex and claude

## Non-Goals

- No in-DB scan status tracking (running/done/failed)
- No scan queue or concurrency limits
- No auto-discovery of scanner CLIs on PATH
- No `filigree scan` CLI command (scanners remain external)
