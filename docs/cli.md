# CLI Reference

All commands support `--json` for machine-readable output. The global `--actor` flag sets identity for the audit trail (default: `cli`).

## Contents

- [Setup](#setup)
- [Creating and Updating](#creating-and-updating)
- [Listing and Search](#listing-and-search)
- [Dependencies](#dependencies)
- [Comments and Labels](#comments-and-labels)
- [Atomic Claiming](#atomic-claiming)
- [Batch Operations](#batch-operations)
- [Planning](#planning)
- [Workflow Templates](#workflow-templates)
- [Analytics and Events](#analytics-and-events)
- [Data Management](#data-management)

```bash
filigree --actor bot-1 create "Title"   # Set actor identity
filigree list --json                    # JSON output
filigree --version                      # Show version
```

## Setup

```bash
filigree init                              # Create .filigree/ in current directory
filigree install                           # Install MCP config, CLAUDE.md, .gitignore
filigree install --claude-code             # Claude Code only
filigree install --codex                   # OpenAI Codex only
filigree doctor                            # Health check
filigree doctor --fix                      # Auto-fix what's possible
```

## Creating and Updating

```bash
filigree create "Fix login bug" --type=bug --priority=1
filigree create "Add search" --type=feature -d "Full-text search" -l backend -l search
filigree update <id> --status=in_progress
filigree update <id> --priority=0 --assignee=alice
filigree update <id> --field severity=high --field component=auth
filigree close <id>
filigree close <id> --reason="Fixed in commit abc123"
filigree reopen <id>
filigree undo <id>                          # Undo last reversible action
```

### `create`

Create a new issue.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | string | required | Issue title (positional) |
| `--type` | string | `task` | Issue type (use `filigree types` to see options) |
| `--priority` | 0-4 | `2` | Priority level (0=critical, 4=backlog) |
| `-d`, `--description` | string | `""` | Issue description |
| `-l`, `--label` | string | — | Label (repeatable) |
| `--parent` | string | — | Parent issue ID |
| `--dep` | string | — | Dependency issue ID (repeatable) |
| `--field` | key=value | — | Custom field (repeatable) |
| `--notes` | string | `""` | Additional notes |

### `update`

Update an existing issue.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |
| `--status` | string | New status (use `filigree transitions <id>`) |
| `--priority` | 0-4 | New priority |
| `--assignee` | string | New assignee |
| `--title` | string | New title |
| `--description` | string | New description |
| `--notes` | string | New notes |
| `--field` | key=value | Custom field (repeatable) |
| `--parent` | string | New parent issue ID |

### `close`

Close an issue.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |
| `--reason` | string | Close reason |

### `reopen`

Reopen a closed issue, returning it to its type's initial state.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |

### `undo`

Undo the most recent reversible action on an issue. Covers status, title, priority, assignee, description, notes, claims, and dependency changes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |

## Listing and Search

```bash
filigree ready                              # Unblocked issues, sorted by priority
filigree list --status=open                 # All open-category issues
filigree list --status=in_progress          # All work-in-progress
filigree list --type=bug --priority=0       # Filter by type and priority
filigree list --assignee=bot-1              # Filter by assignee
filigree show <id>                          # Full issue details
filigree search "auth"                      # Search by title/description
filigree blocked                            # Issues waiting on blockers
filigree critical-path                      # Longest dependency chain
```

### `ready`

Show issues that are ready to work on — open, no blockers, sorted by priority.

### `list`

List issues with optional filters.

| Parameter | Type | Description |
|-----------|------|-------------|
| `--status` | string | Filter by status (or status category: `open`, `in_progress`, `closed`) |
| `--type` | string | Filter by issue type |
| `--priority` | 0-4 | Filter by priority |
| `--assignee` | string | Filter by assignee |
| `--parent` | string | Filter by parent issue ID |
| `--limit` | integer | Max results (default 100) |
| `--offset` | integer | Skip first N results |

### `show`

Show full details for an issue including deps, labels, children, and ready status.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |

### `search`

Search issues by title and description (uses FTS5 full-text search).

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | string | Search query (positional) |
| `--limit` | integer | Max results (default 100) |

### `blocked`

Show all blocked issues with their blocker lists.

### `critical-path`

Show the longest dependency chain among open issues. Useful for identifying what to prioritize.

## Dependencies

```bash
filigree add-dep <issue> <depends-on>       # issue is blocked by depends-on
filigree remove-dep <issue> <depends-on>
filigree blocked                            # Show all blocked issues
```

### `add-dep`

Add a dependency: the first issue depends on (is blocked by) the second.

| Parameter | Type | Description |
|-----------|------|-------------|
| `issue` | string | Issue that is blocked (positional) |
| `depends-on` | string | Issue that blocks (positional) |

### `remove-dep`

Remove a dependency between two issues.

| Parameter | Type | Description |
|-----------|------|-------------|
| `issue` | string | Issue that was blocked (positional) |
| `depends-on` | string | Issue that was blocking (positional) |

## Comments and Labels

```bash
filigree add-comment <id> "Found the root cause"
filigree get-comments <id>
filigree add-label <id> backend
filigree remove-label <id> backend
```

### `add-comment`

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |
| `text` | string | Comment text (positional) |

### `get-comments`

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |

### `add-label` / `remove-label`

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |
| `label` | string | Label name (positional) |

## Atomic Claiming

Prevents double-work when multiple agents are active. Claiming uses optimistic locking — if another agent already claimed the issue, the operation fails.

```bash
filigree claim <id> --assignee agent-1          # Claim specific issue
filigree claim-next --assignee agent-1          # Claim highest-priority ready issue
filigree claim-next --assignee agent-1 --type=bug --priority-max=1
filigree release <id>                           # Release back to open
```

### `claim`

Atomically claim an issue. Does NOT change status — use `update` to advance through the workflow after claiming.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |
| `--assignee` | string | Who is claiming (required) |

### `claim-next`

Claim the highest-priority ready issue.

| Parameter | Type | Description |
|-----------|------|-------------|
| `--assignee` | string | Who is claiming (required) |
| `--type` | string | Filter by issue type |
| `--priority-min` | 0-4 | Minimum priority (0=critical) |
| `--priority-max` | 0-4 | Maximum priority |

### `release`

Release a claimed issue back to open.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |

## Batch Operations

```bash
filigree batch-update <id1> <id2> --priority=0     # Update multiple issues
filigree batch-close <id1> <id2> --reason="Sprint complete"
```

### `batch-update`

Update multiple issues with the same changes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `ids` | string... | Issue IDs (positional, multiple) |
| `--status` | string | New status |
| `--priority` | 0-4 | New priority |
| `--assignee` | string | New assignee |
| `--field` | key=value | Custom field (repeatable) |

### `batch-close`

Close multiple issues.

| Parameter | Type | Description |
|-----------|------|-------------|
| `ids` | string... | Issue IDs (positional, multiple) |
| `--reason` | string | Close reason |

## Planning

```bash
filigree create-plan --file plan.json       # Create milestone/phase/step hierarchy
filigree plan <milestone-id>                # Show plan tree with progress
```

### `create-plan`

Create a full milestone/phase/step hierarchy from a JSON file.

| Parameter | Type | Description |
|-----------|------|-------------|
| `--file` | path | JSON plan file (required) |

Plan JSON structure:

```json
{
  "milestone": {"title": "v1.0 Release"},
  "phases": [
    {
      "title": "Core Implementation",
      "steps": [
        {"title": "Define schema"},
        {"title": "Build CLI", "deps": [0]}
      ]
    }
  ]
}
```

Step deps use indices: integer for same-phase, `"phase_idx.step_idx"` for cross-phase.

### `plan`

Show plan tree with progress for a milestone.

| Parameter | Type | Description |
|-----------|------|-------------|
| `milestone-id` | string | Milestone issue ID (positional) |

## Workflow Templates

```bash
filigree types                              # List all types with state flows
filigree type-info <type>                   # Full workflow definition
filigree transitions <id>                   # Valid next states for an issue
filigree validate <id>                      # Validate against template
filigree packs                              # List enabled packs
filigree guide <pack>                       # Workflow guide for a pack
filigree explain-state <type> <state>       # Explain a specific state
filigree workflow-states                    # All states grouped by category
```

See [Workflow Templates](workflows.md) for details on types, packs, and state machines.

## Analytics and Events

```bash
filigree stats                              # Counts by status, type, ready/blocked
filigree metrics --days=30                  # Cycle time, lead time, throughput
filigree changes --since 2026-01-01T00:00   # Events since timestamp
filigree events <id>                        # Event history for one issue
```

### `stats`

Project statistics: counts by status, type, ready/blocked.

### `metrics`

Flow metrics for retrospectives and velocity tracking.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--days` | integer | 30 | Lookback window in days |

### `changes`

Events since a timestamp. Used for session resumption.

| Parameter | Type | Description |
|-----------|------|-------------|
| `--since` | ISO timestamp | Start timestamp (required) |
| `--limit` | integer | Max events (default 100) |

### `events`

Event history for a specific issue, newest first.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |
| `--limit` | integer | Max events (default 50) |

## Data Management

```bash
filigree export backup.jsonl                # Export all data
filigree import backup.jsonl --merge        # Import (skip existing)
filigree archive --days=30                  # Archive old closed issues
filigree compact --keep=50                  # Compact event history
filigree doctor                             # Health check
filigree doctor --fix                       # Auto-fix what's possible
filigree dashboard --port=8377              # Launch web UI
```

### `export`

Export all project data (issues, deps, labels, comments, events) to JSONL.

| Parameter | Type | Description |
|-----------|------|-------------|
| `output` | path | Output file path (positional) |

### `import`

Import project data from JSONL.

| Parameter | Type | Description |
|-----------|------|-------------|
| `input` | path | Input file path (positional) |
| `--merge` | flag | Skip existing records instead of failing |

### `archive`

Archive old closed issues to reduce active issue count.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--days` | integer | 30 | Archive issues closed more than N days ago |

### `compact`

Remove old events for archived issues.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--keep` | integer | 50 | Keep N most recent events per archived issue |

### `doctor`

Run health checks on the filigree database.

| Parameter | Type | Description |
|-----------|------|-------------|
| `--fix` | flag | Auto-fix what's possible |

### `dashboard`

Launch the web dashboard.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--port` | integer | 8377 | Port to listen on |

