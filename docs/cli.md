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
filigree init --prefix=myproject           # Custom ID prefix
filigree install                           # Install everything: MCP, instructions, .gitignore
filigree install --claude-code             # Claude Code MCP server only
filigree install --codex                   # OpenAI Codex MCP server only
filigree install --claude-md               # Inject instructions into CLAUDE.md only
filigree install --agents-md               # Inject instructions into AGENTS.md only
filigree install --gitignore               # Add .filigree/ to .gitignore only
filigree doctor                            # Health check
filigree doctor --fix                      # Auto-fix what's possible
filigree doctor --verbose                  # Show all checks including passed
```

### `init`

Initialize `.filigree/` in the current directory.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--prefix` | string | directory name | ID prefix for issues |

### `install`

Install filigree into the current project. With no flags, installs everything: MCP servers, instructions, and gitignore. With specific flags, installs only the selected components.

| Parameter | Type | Description |
|-----------|------|-------------|
| `--claude-code` | flag | Install MCP config for Claude Code only |
| `--codex` | flag | Install MCP config for Codex only |
| `--claude-md` | flag | Inject instructions into CLAUDE.md only |
| `--agents-md` | flag | Inject instructions into AGENTS.md only |
| `--gitignore` | flag | Add `.filigree/` to .gitignore only |

### `doctor`

Run health checks on the filigree installation.

| Parameter | Type | Description |
|-----------|------|-------------|
| `--fix` | flag | Auto-fix what's possible |
| `--verbose` | flag | Show all checks including passed |

## Creating and Updating

```bash
filigree create "Fix login bug" --type=bug --priority=1
filigree create "Add search" --type=feature -d "Full-text search" -l backend -l search
filigree update <id> --status=in_progress
filigree update <id> --priority=0 --assignee=alice
filigree update <id> --field severity=high --field component=auth
filigree close <id>
filigree close <id1> <id2> <id3>                # Close multiple at once
filigree close <id> --reason="Fixed in commit abc123"
filigree reopen <id>
filigree reopen <id1> <id2>                     # Reopen multiple at once
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
| `--design` | string | New design field (shorthand for `--field design=...`) |
| `--parent` | string | New parent issue ID (empty string to clear) |

### `close`

Close one or more issues. Accepts multiple IDs.

| Parameter | Type | Description |
|-----------|------|-------------|
| `ids` | string... | One or more issue IDs (positional, variadic) |
| `--reason` | string | Close reason |

When using `--json`, the output includes a `closed` array and an `unblocked` array showing issues that became ready after the close.

### `reopen`

Reopen one or more closed issues, returning them to their type's initial state. Accepts multiple IDs.

| Parameter | Type | Description |
|-----------|------|-------------|
| `ids` | string... | One or more issue IDs (positional, variadic) |

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
| `--label` | string | Filter by label |
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
| `--offset` | integer | Skip first N results |

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
filigree batch-add-label security <id1> <id2>      # Add same label to many issues
filigree batch-add-comment "triage complete" <id1> <id2>
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

### `batch-add-label`

Add the same label to multiple issues.

| Parameter | Type | Description |
|-----------|------|-------------|
| `label` | string | Label name (positional) |
| `ids` | string... | Issue IDs (positional, multiple) |

### `batch-add-comment`

Add the same comment to multiple issues.

| Parameter | Type | Description |
|-----------|------|-------------|
| `text` | string | Comment text (positional) |
| `ids` | string... | Issue IDs (positional, multiple) |

## Planning

```bash
filigree create-plan --file plan.json       # Create from JSON file
cat plan.json | filigree create-plan        # Create from stdin
filigree plan <milestone-id>                # Show plan tree with progress
```

### `create-plan`

Create a full milestone/phase/step hierarchy from JSON. Reads from `--file` if provided, otherwise reads from stdin.

| Parameter | Type | Description |
|-----------|------|-------------|
| `--file` | path | JSON plan file (optional; reads from stdin if omitted) |

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
filigree templates                          # List available templates
filigree templates --type=bug               # Show specific template fields
filigree templates reload                   # Reload templates from disk
```

See [Workflow Templates](workflows.md) for details on types, packs, and state machines.

### `types`

List all registered issue types with their pack and state flow.

### `type-info`

Show the full workflow definition for an issue type: states, transitions, fields, and enforcement rules.

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | string | Issue type name (positional) |

### `transitions`

Show valid next states for an issue, with readiness indicators and missing field warnings.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |

### `validate`

Validate an issue against its type template. Returns warnings for missing recommended fields.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Issue ID (positional) |

### `packs`

List all enabled workflow packs with their types and metadata.

### `guide`

Display the workflow guide for a pack, including state diagram, tips, and common mistakes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `pack` | string | Pack name (positional) |

### `explain-state`

Explain a state within a type's workflow: its category, inbound/outbound transitions, and fields required at that state.

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | string | Issue type name (positional) |
| `state` | string | State name (positional) |

### `workflow-states`

Show all workflow states grouped by category (open, wip, done) from enabled templates.

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
filigree migrate --from-beads              # Migrate from beads tracker
filigree dashboard --port=8377              # Launch web UI
filigree dashboard --no-browser            # Launch without opening browser
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

### `migrate`

Migrate issues from another system. Currently supports migrating from the beads issue tracker.

| Parameter | Type | Description |
|-----------|------|-------------|
| `--from-beads` | flag | Migrate from `.beads` database |
| `--beads-db` | path | Path to beads.db (default: `.beads/beads.db`) |

### `dashboard`

Launch the web dashboard. Requires `filigree[dashboard]` extra.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--port` | integer | 8377 | Port to listen on |
| `--no-browser` | flag | — | Don't auto-open browser |

## Dashboard

```bash
filigree dashboard                    # Opens browser at localhost:8377
filigree dashboard --port 9000        # Custom port
filigree dashboard --no-browser       # Skip auto-open
```

### `dashboard`

Launch an interactive web dashboard at `http://localhost:8377`. Features:

| View | Description |
|------|-------------|
| **Kanban** | Three-column board (open/wip/done) with cluster mode grouping by epic |
| **Graph** | Cytoscape.js dependency graph with critical path overlay |
| **Metrics** | Throughput, cycle time, lead time with agent workload chart |
| **Activity** | Chronological event feed across all issues |
| **Workflow** | State machine visualization for any issue type |

**Interactive features:** Inline status transitions, priority/assignee changes, comments, issue creation, claim/release, dependency management, batch operations, keyboard navigation (`?` for shortcuts), filter presets, dark/light theme toggle, auto-refresh with change highlighting.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--port` | int | 8377 | Port to serve on |
| `--no-browser` | flag | false | Don't auto-open browser |

The dashboard connects to `.filigree/` in the current directory. All write operations record `"dashboard"` as the actor for audit trail.
