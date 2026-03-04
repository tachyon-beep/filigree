# Agent Observations — Design Document

**Date:** 2026-03-05
**Status:** Draft (revised after systems thinking, architecture, and user-perspective reviews)

## Problem

Agents working on filigree tasks frequently notice bugs, inconsistencies, or suspicious
code in passing — but never report them. The friction of creating a formal issue (pick
a type, set fields, register files, add associations) is too high relative to the value
of a quick "I saw something weird" note. These observations evaporate.

## Solution

A lightweight **observation staging area** — a separate system from issues where agents
dump quick notes about things they noticed. Observations are disposable candidates, not
first-class records. They exist until a human or agent promotes them to a real issue or
dismisses them.

## Core Principles

1. **One tool call, fire-and-forget.** The agent calls `observe()` and moves on.
2. **Not issues.** Observations have no workflow, no states, no assignee. They're a scratchpad.
3. **Ephemeral.** Promote → creates a real issue, deletes the observation. Dismiss → logs
   and deletes it.
4. **Incomplete by design.** Observations won't have all the info needed for a proper issue.
   Promotion is when investigation happens.
5. **Lightweight UI.** A badge + popover on the dashboard, not a full tab. Triage happens
   in conversation, not in the UI.
6. **Bounded.** Observations auto-expire after 14 days. The table is a bounded buffer,
   not an infinite inbox.
7. **Nag when stale.** Session context and MCP prompts escalate urgency for observations
   older than 48 hours to prevent the inbox from being ignored.

## Schema

### `observations` table

```sql
CREATE TABLE IF NOT EXISTS observations (
    id              TEXT PRIMARY KEY,
    summary         TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    file_id         TEXT REFERENCES file_records(id) ON DELETE SET NULL,
    file_path       TEXT DEFAULT '',
    line            INTEGER,
    source_issue_id TEXT DEFAULT '',
    priority        INTEGER DEFAULT 3 CHECK (priority BETWEEN 0 AND 4),
    actor           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_priority ON observations(priority, created_at);
CREATE INDEX IF NOT EXISTS idx_observations_expires ON observations(expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_dedup
  ON observations(summary, file_path, coalesce(line, -1));
```

`expires_at` defaults to `created_at + 14 days`. The auto-sweep runs on every
`list_observations` / `observation_stats` call (piggyback cleanup, no daemon).
The sweep runs in its own savepoint to avoid interfering with in-flight transactions.

**Expiry behavior by priority:**
- **P0–P1 (critical/high):** Auto-promoted to a real issue with label `auto-promoted`
  and a note explaining the observation was never triaged. These are too important
  to silently discard.
- **P2–P4:** Logged to `dismissed_observations` with reason "expired (TTL)" and deleted.

The dedup index `UNIQUE(summary, file_path, coalesce(line, -1))` silently drops
duplicate observations (`INSERT OR IGNORE`), preventing multi-agent noise without
requiring explicit dedup logic.

### `dismissed_observations` table (lightweight audit log)

```sql
CREATE TABLE IF NOT EXISTS dismissed_observations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    obs_id      TEXT NOT NULL,
    summary     TEXT NOT NULL,
    actor       TEXT DEFAULT '',
    reason      TEXT DEFAULT '',
    dismissed_at TEXT NOT NULL
);
```

Not indexed, not queryable by the agent — just a safety net. Periodically truncated.
Records what was dismissed, by whom, and when. Enables "what did we throw away?"
audits without burdening the normal workflow.

## MCP Tools

### `observe` — the agent's fire-and-forget tool

```
observe(
  summary: str,              # required — what was noticed
  detail: str,               # optional — freeform markdown: traces, code, reasoning
  file: str,                 # optional — project-relative path
  line: int,                 # optional — approximate line number
  source_issue_id: str,      # optional — issue the agent was working on when it noticed this
  priority: 0-4,             # optional, default 3
  actor: str                 # optional
)
```

Internally: creates observation row with `expires_at = created_at + 14 days`,
auto-registers file if provided.
Returns: `{id, summary, file_path, created_at}`.

### `list_observations` — for triage

```
list_observations(limit?, offset?, file_path?)
```

Auto-sweeps expired observations before returning results.
Returns observations sorted by priority (ascending) then created_at (ascending).
Optional `file_path` filter matches observations whose `file_path` contains the
given substring (e.g., `file_path="src/api"` matches `src/api/routes.py`).

### `promote_observation` — graduate to a real issue

```
promote_observation(
  id: str,                   # required — observation to promote
  type: str,                 # optional, default "bug"
  priority: int,             # optional — overrides observation priority
  title: str,                # optional — overrides summary as issue title
  description: str,          # optional — prepended to observation detail
  actor: str                 # optional
)
```

**Atomic operation:** Uses `DELETE FROM observations WHERE id = ? RETURNING *`
as a single-statement claim — only one caller can promote a given observation.
If RETURNING yields no rows, the observation was already promoted or dismissed.
Creates a real issue with data from the returned row. If the observation has a
`source_issue_id`, includes "Observed while working on {source_issue_id}" in the
description. If the observation has a file_id, creates a `mentioned_in` file
association on the new issue. If open
scan_findings exist for the same file+line, surfaces them in the response so
the caller can link rather than duplicate.

Returns the new issue (and any matching scan findings).

### `dismiss_observation` — discard with audit

```
dismiss_observation(id: str, reason?: str, actor?: str)
```

Logs the dismissal to `dismissed_observations`, then deletes the observation.

### `batch_dismiss_observations` — bulk cleanup

```
batch_dismiss_observations(ids: list[str], reason?: str, actor?: str)
```

Logs and deletes multiple observations at once.

### `file_briefing` — "what should I know about this file?"

```
file_briefing(file: str)
```

Read-only aggregation. Takes a project-relative file path and returns everything
known about it in one call:

- **Observations** matching the file path (from `observations` table)
- **Associated issues** (from `file_associations` — bugs, tasks linked to this file)
- **Open scan findings** (from `scan_findings` where status is open/acknowledged)

No new tables or schema — purely a convenience query over existing data.
Auto-registers the file if not already tracked. Returns empty sections
rather than erroring if nothing is known about the file.

## Session Context & Agent Prompting

Observations integrate into the session start flow via `session_context` and the
`filigree-workflow` MCP prompt. The urgency escalates based on age:

### Gentle nudge (any pending observations)

Added to `session_context` output:

```
OBSERVATIONS: 5 pending (oldest: 1 day ago)
  Use `list_observations` to review, `promote_observation` to create issues,
  or `dismiss_observation` to clear.
```

### Stale notice (any observation older than 48 hours)

Added to `session_context` with emphasis:

```
⚠ STALE OBSERVATIONS: 3 observations older than 48 hours (oldest: 5 days ago)
  Total pending: 7. Run `list_observations` to review.
```

The same escalation appears in the `filigree-workflow` MCP prompt so agents see
it regardless of whether they call `session_context`.

### Implementation

`observation_stats()` returns a dict with age breakdown:

```python
{
    "count": 5,
    "oldest_hours": 52.3,
    "stale_count": 3,        # older than 48h
    "expiring_soon_count": 1  # expires within 24h
}
```

`generate_summary()` in `summary.py` and `_build_workflow_text()` in `mcp_server.py`
both call this and format the appropriate message.

## Dashboard UI

Minimal footprint — a **notification-style indicator** in the dashboard header:

- **Badge:** Shows count of pending observations (e.g., "7" in a small circle).
  Turns amber when any observation is older than 48 hours.
- **Popover on click:** Dropdown list showing observation summaries, file paths,
  priority, and age. Each row is a one-line summary you can mouseover for the
  detail preview. Stale observations (>48h) highlighted with amber background.
- **Actions in popover:** Checkbox per row for bulk selection. "Promote" and
  "Dismiss" buttons per observation, plus bulk action bar ("Promote Selected",
  "Dismiss Selected") when checkboxes are active. "Dismiss All" at the bottom.
- **Detail view:** Clicking an observation summary opens a modal with full detail
  (stack traces, code snippets, etc.) since popover mouseover is insufficient for
  long-form content.
- **Promote flow:** Small inline dialog — pick issue type and priority, confirm.
  Creates the issue and removes the observation from the list.

## Safeguards (from systems thinking review)

### Bounded accumulation
- 14-day TTL prevents unbounded growth
- Piggyback sweep on reads (in savepoint) means no daemon required
- P0/P1 observations auto-promote on expiry (never silently discarded)
- P2–P4 observations log to dismissed_observations before deletion
- Session prompts surface stale observations before expiry

### Dedup
- `UNIQUE(summary, file_path, coalesce(line, -1))` with `INSERT OR IGNORE`
- Multi-agent setups won't flood the inbox with identical observations
- Different summaries at the same location are still allowed

### Atomic promotion
- `promote_observation` uses `DELETE...RETURNING *` as a single-statement claim
- Prevents duplicate issues from concurrent promote attempts

### Dismissal audit trail
- Lightweight `dismissed_observations` table logs what was discarded
- No-overhead: INSERT before DELETE, same transaction
- Enables "what did we throw away?" forensics without burdening the workflow

### Scan findings cross-reference
- Promotion checks for existing open scan_findings at the same file+line
- Surfaces matches in the promote response to prevent duplicate issue creation

### No file_records pollution
- `observe()` only auto-registers files that actually exist on disk
- Path validation via `_safe_path()` prevents garbage entries

### Timestamp consistency
- All timestamps use `_now_iso()` from `db_base` (produces `+00:00` suffix)
- `expires_at` computed by adding timedelta then formatting with `.isoformat()`
- Consistent format ensures SQLite text comparisons in sweep queries are correct

### FK safety
- `file_id REFERENCES file_records(id) ON DELETE SET NULL` — if a file_record is
  cleaned up, the observation keeps its `file_path` text but loses the FK link

### Observation lifecycle not in events table
- Observations intentionally have no event trail in the `events` table
- They're disposable scratchpad items, not first-class records
- The `dismissed_observations` audit table provides forensic traceability where needed

## What This Does NOT Include

- No confidence field (per review: "warning signal with no connected actuator" — nothing branches on it)
- No fuzzy dedup — exact `(summary, file_path, line)` dedup only; similar but not identical observations coexist
- No auto-triage or severity inference
- No observation editing — they're write-once, read, promote-or-dismiss
- No observation-to-observation linking

## Migration

Schema version v6 → v7: adds `observations` and `dismissed_observations` tables.
No changes to existing tables.
