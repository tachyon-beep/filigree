# Scanner System Remediation — Design Document

**Date:** 2026-03-06
**Branch:** feat/dashboard-ux-and-observations
**Status:** Approved

## Problem Statement

The scanner/file-finding system has a split-brain API surface: some operations are MCP tools, some are REST-only, and some don't exist at all. An AI agent using this system hits a wall every few steps because it can't complete workflows without switching protocols or falling back to Bash.

Key gaps:
- **No scan lifecycle tracking** — fire-and-forget subprocess with no status query
- **No finding triage tools** — agents can't update findings, dismiss false positives, or promote real bugs without REST fallback
- **No batch scanning** — 30s per-file cooldown makes multi-file scans impractical
- **Cooldown blocks retry after failure** — failed scans consume the cooldown window
- **Tool descriptions don't explain the workflow** — agents must reverse-engineer the async polling pattern
- **`create_issues` on `process_scan_results` is architecturally wrong** — scanners lack context to confirm bugs

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| API surface | MCP-first | Primary consumer is AI agents. Dashboard is the human endpoint. |
| Scan lifecycle storage | DB table (`scan_runs`) | Survives restarts, queryable, replaces hacky GROUP BY on findings |
| Batch scanning model | Single process, multiple files | Scanner handles iteration. One scan_run_id per batch. |
| Finding triage depth | Full suite (6 tools) | Closes all protocol-switching gaps |
| Findings → Issues pipeline | Findings → Observations → Issues | Scanners lack context to confirm bugs. Observations = untriaged signal. |
| `create_issues` param | Replace with `create_observations` | Dual-writes findings + observations so scanner output appears in agent scratchpad |
| Scan log access | Tail embedded in `get_scan_status` response | MCP-first; no separate REST endpoint needed |
| Claude Code scanner | CLI subprocess (`claude --print`) | Consistent with codex pattern, uses account quota |

## Mental Model: Observations, Findings, and Issues

Observations and findings are both **untriaged signal** — noise until a human or agent with full context reviews them.

- **Scanners** produce findings (structured, automated). Can also auto-create observations for scratchpad visibility.
- **Agents** use observations when they notice something in passing but aren't investigating now.
- **When an agent investigates and confirms a bug**, they skip observations entirely and `create_issue(type="bug")` directly.

Triage actions for findings:

| Confidence | Action | Result |
|------------|--------|--------|
| "This is real" | `create_issue(...)` + `update_finding(issue_id=...)` | Bug in tracker, finding linked |
| "Not sure, defer" | `promote_finding(finding_id)` | Observation in scratchpad for later |
| "False positive" | `dismiss_finding(finding_id, reason=...)` | Finding marked `false_positive` |

## Data Model

### New table: `scan_runs`

```sql
CREATE TABLE IF NOT EXISTS scan_runs (
    id            TEXT PRIMARY KEY,
    scanner_name  TEXT NOT NULL,
    scan_source   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    file_paths    TEXT NOT NULL DEFAULT '[]',
    file_ids      TEXT NOT NULL DEFAULT '[]',
    pid           INTEGER,
    api_url       TEXT DEFAULT '',
    log_path      TEXT DEFAULT '',
    started_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT,
    exit_code     INTEGER,
    findings_count INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'timeout'))
);

CREATE INDEX IF NOT EXISTS idx_scan_runs_status ON scan_runs(status);
CREATE INDEX IF NOT EXISTS idx_scan_runs_scanner ON scan_runs(scanner_name);
```

- `file_paths` / `file_ids` — JSON arrays, supports single-file and batch scans
- `status` state machine: `pending` → `running` → `completed` | `failed` | `timeout`
- `pid` enables live process checking in `get_scan_status`
- `findings_count` updated when `process_scan_results` ingests results for this run
- Cooldown checks query this table instead of in-memory dict. Failed scans don't block retry.

### Existing tables (unchanged)

- `file_records` — tracked files
- `scan_findings` — individual findings with severity, status, dedup index
- `file_associations` — file ↔ issue links

## Module Structure

### New files

| File | Purpose |
|------|---------|
| `src/filigree/db_scans.py` | `ScansMixin` — scan_runs CRUD, status transitions, cooldown logic, log tail reading |
| `src/filigree/mcp_tools/scanners.py` | MCP tools for scanner lifecycle (5 tools) |
| `.filigree/scanners/claude-code.toml` | Scanner config for Claude Code CLI |
| `scripts/claude_code_bug_hunt.py` | Scanner implementation using `claude --print` |

### Modified files

| File | Change |
|------|--------|
| `src/filigree/mcp_tools/files.py` | Remove `trigger_scan`, `list_scanners` (moved to scanners.py). Add 6 finding triage tools. |
| `src/filigree/db_files.py` | Add `update_finding()`, `get_finding()`, `list_findings_global()`, `promote_finding_to_observation()`. Replace `create_issues` with `create_observations` in `process_scan_results`. Update `findings_count` on `scan_runs` during ingestion. Remove `_create_issue_for_finding`. |
| `src/filigree/db_schema.py` | Add `scan_runs` table DDL |
| `src/filigree/core.py` | Mix in `ScansMixin` |
| `src/filigree/mcp_tools/__init__.py` | Register `scanners` module |
| `src/filigree/mcp_server.py` | Remove `_scan_cooldowns` dict and `_SCAN_COOLDOWN_SECONDS` |
| `src/filigree/dashboard_routes/files.py` | Support `create_observations` on `POST /api/v1/scan-results`. Update scan run status on ingestion. |
| `src/filigree/scanners.py` | No changes (stays pure TOML parsing) |
| `src/filigree/types/files.py` | Add `ScanRunDict` TypedDict |
| `src/filigree/types/inputs.py` | Add input arg types for new MCP tools |
| `scripts/scan_utils.py` | Extract shared scanner pipeline (`run_scanner_pipeline()` with executor callback) |

## MCP Tool Surface

### Scanner Lifecycle (`mcp_tools/scanners.py`)

| Tool | Description | Key params |
|------|-------------|------------|
| `list_scanners` | List registered scanner configs. Returns names, descriptions, file_types, and template variable docs. | *(none)* |
| `trigger_scan` | Trigger single-file scan. Returns scan_run_id, file_id. | `scanner`, `file_path`, `api_url?` |
| `trigger_scan_batch` | Trigger multi-file scan as one process, one scan_run_id. | `scanner`, `file_paths[]`, `api_url?` |
| `get_scan_status` | Check scan lifecycle + log tail. Uses DB + live PID check. | `scan_run_id`, `log_lines?` (default 50) |
| `preview_scan` | Dry-run: show expanded command without spawning. | `scanner`, `file_path` |

`trigger_scan` description explicitly documents the workflow:
> "Poll results via `get_scan_status(scan_run_id)`. When status is 'completed', findings are available via `get_file(file_id)` or `list_findings(scan_run_id=...)`."

`list_scanners` response includes template variable documentation:
```json
{
  "scanners": [...],
  "template_variables": {
    "{file}": "Target file path",
    "{api_url}": "Dashboard URL",
    "{project_root}": "Project root directory",
    "{scan_run_id}": "Correlation ID for tracking results"
  }
}
```

### Finding Triage (`mcp_tools/files.py`)

| Tool | Description | Key params |
|------|-------------|------------|
| `get_finding` | Get single finding by ID | `finding_id` |
| `list_findings` | Project-wide findings query, all filters optional | `severity?`, `status?`, `scan_source?`, `scan_run_id?`, `file_id?`, `issue_id?`, `limit`, `offset` |
| `update_finding` | Update finding status or issue linkage | `finding_id`, `status?`, `issue_id?` |
| `batch_update_findings` | Bulk status update | `finding_ids[]`, `status` |
| `promote_finding` | Create observation from finding (untriaged signal for scratchpad) | `finding_id`, `priority?`, `actor?` |
| `dismiss_finding` | Set status to `false_positive` with audit reason | `finding_id`, `reason?` |

`list_findings` description documents valid statuses:
- `open` — new, untriaged
- `acknowledged` — seen, under investigation
- `fixed` — confirmed fixed
- `false_positive` — dismissed
- `unseen_in_latest` — present in previous scan, missing from latest

### Existing tools (unchanged)

`list_files`, `get_file`, `get_file_timeline`, `get_issue_files`, `add_file_association`, `register_file`

## `process_scan_results` Rework

**Remove:** `create_issues` parameter and `_create_issue_for_finding()` method.

**Add:** `create_observations` parameter (default `False`). When `True`, each new finding also creates an observation:
- `summary`: `"[{scan_source}] {path}:{line} -- {message first line}"`
- `detail`: full message + suggestion
- `file_path` and `line` from finding
- `priority`: mapped from severity (critical→0, high→1, medium→2, low/info→3)

**REST:** `POST /api/v1/scan-results` accepts `create_observations` (replaces the blocked `create_issues`).

**Scan run update:** When ingesting results with a non-empty `scan_run_id`, update `scan_runs.findings_count` and set `status='completed'`.

## Claude Code Scanner

**Config:** `.filigree/scanners/claude-code.toml`
```toml
[scanner]
name = "claude-code"
description = "Per-file bug hunt using Claude Code CLI"
command = "python scripts/claude_code_bug_hunt.py"
args = ["--root", "{project_root}", "--file", "{file}", "--max-files", "1", "--api-url", "{api_url}", "--scan-run-id", "{scan_run_id}"]
file_types = ["py"]
```

**Script:** `scripts/claude_code_bug_hunt.py` — thin wrapper that defines a `run_claude_code()` executor:
```python
async def run_claude_code(*, prompt, output_path, model, repo_root, timeout):
    cmd = ["claude", "--print", "-p", prompt]
    if model:
        cmd.extend(["--model", model])
    # async subprocess, capture stdout, write to output_path
```

**Refactor:** Extract shared pipeline from `codex_bug_hunt.py` into `scan_utils.py` as `run_scanner_pipeline(executor, ...)`. Both scanner scripts become thin wrappers: define executor, call pipeline.

## Cooldown Behavior

- Cooldown checks query `scan_runs` table: "is there a `running` or recently-`completed` scan for this (scanner, file) within the last 30s?"
- `failed` and `timeout` scans do NOT count — retry is immediate
- In-memory `_scan_cooldowns` dict and `_SCAN_COOLDOWN_SECONDS` removed from `mcp_server.py`
- Cooldown documented in `trigger_scan` tool description
