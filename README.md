# Filigree

Agent-native issue tracker with convention-based project discovery.

![CI](https://github.com/tachyon-beep/filigree/actions/workflows/ci.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/filigree)
![Python 3.11+](https://img.shields.io/pypi/pyversions/filigree)
![License: MIT](https://img.shields.io/pypi/l/filigree)

<!-- TODO: Add asciinema terminal recording here -->

## Table of Contents

- [What Is Filigree?](#what-is-filigree)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Why Filigree?](#why-filigree)
- [CLI Reference](#cli-reference)
- [MCP Server](#mcp-server)
- [Workflow Templates](#workflow-templates)
- [Agent Integration](#agent-integration)
- [Project Structure](#project-structure)
- [Development](#development)
- [License](#license)

## What Is Filigree?

Filigree is a lightweight, SQLite-backed issue tracker designed for AI coding agents (Claude Code, Codex, etc.) to use as first-class citizens. It exposes 42 MCP tools so agents interact natively, plus a full CLI for humans and background subagents.

Traditional issue trackers are human-first -- agents have to scrape CLI output or parse API responses. Filigree flips this: agents read a pre-computed `context.md` at session start, claim work with optimistic locking, follow enforced workflow state machines, and resume sessions via event streams.

Filigree is single-project and local-first. No server, no cloud, no accounts. Just a `.filigree/` directory (like `.git/`) containing a SQLite database, configuration, and auto-generated context summary.

### Key Features

- **MCP server** with 42 tools -- agents interact natively without parsing text
- **Full CLI** with `--json` output for background subagents and `--actor` for audit trails
- **Workflow templates** -- 9 issue types across 2 packs with enforced state machines and transition validation
- **Dependency graph** -- blockers, ready-queue, critical path analysis
- **Hierarchical planning** -- milestone/phase/step hierarchies with automatic unblocking
- **Atomic claiming** -- optimistic locking prevents double-work in multi-agent scenarios
- **Pre-computed context** -- `context.md` regenerated on every mutation for instant agent orientation
- **Zero external runtime dependencies** -- just Python + SQLite (click is the only install dep)
- **Session resumption** -- `get_changes --since <timestamp>` to catch up after downtime

## Quick Start

```bash
# Install
pip install filigree              # or: uv add filigree

# Initialize in your project
cd my-project
filigree init

# Install MCP server, CLAUDE.md instructions, .gitignore entry
filigree install

# Create your first issue
filigree create "Set up CI pipeline" --type=task --priority=1

# See what's ready
filigree ready

# Work on it
filigree update <id> --status=in_progress

# Done
filigree close <id>
```

## Installation

### From PyPI

```bash
pip install filigree
```

### From source

```bash
git clone https://github.com/tachyon-beep/filigree.git
cd filigree
uv sync
```

### Optional extras

```bash
pip install "filigree[mcp]"         # MCP server (mcp>=1.0)
pip install "filigree[dashboard]"   # Web dashboard (FastAPI + Uvicorn)
pip install "filigree[all]"         # Everything
```

### Entry points

| Command | Purpose |
|---------|---------|
| `filigree` | CLI interface |
| `filigree-mcp` | MCP server (stdio transport) |
| `filigree-dashboard` | Web UI (port 8377) |

## Why Filigree?

Most issue trackers are built for humans with browsers. Filigree is built for AI agents that need structured task management without network access, authentication, or API rate limits.

| | Filigree | GitHub Issues | Jira | TODO files |
|-|----------|---------------|------|------------|
| Agent-native (MCP tools) | Yes | No | No | No |
| Works offline / local-first | Yes | No | No | Yes |
| Structured queries & filtering | Yes | Yes | Yes | No |
| Workflow state machines | Yes | Limited | Yes | No |
| Zero configuration | Yes | No | No | Yes |
| Dependency tracking | Yes | Limited | Yes | No |

Filigree uses **convention-based discovery**: agents find the `.filigree/` directory the same way git finds `.git/`. No config files to parse, no environment variables to set, no server URLs to resolve. A single SQLite file holds everything, and a pre-computed `context.md` gives agents instant orientation without querying.

## CLI Reference

All commands support `--json` for machine-readable output. The global `--actor` flag sets identity for the audit trail (default: `cli`).

```bash
filigree --actor bot-1 create "Title"   # Set actor identity
filigree list --json                    # JSON output
```

### Finding Work

```bash
filigree ready                              # Unblocked issues, sorted by priority
filigree list --status=open                 # All open-category issues (triage, proposed, pending, etc.)
filigree list --status=in_progress          # All work-in-progress (fixing, building, etc.)
filigree list --type=bug --priority=0       # Filter by type and priority
filigree list --assignee=bot-1              # Filter by assignee
filigree show <id>                          # Full issue details
filigree search "auth"                      # Search by title/description
filigree blocked                            # Issues waiting on blockers
filigree critical-path                      # Longest dependency chain
```

### Creating and Updating

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

### Dependencies

```bash
filigree add-dep <issue> <depends-on>       # issue is blocked by depends-on
filigree remove-dep <issue> <depends-on>
filigree blocked                            # Show all blocked issues
```

### Atomic Claiming

Prevents double-work when multiple agents are active:

```bash
filigree claim <id> --assignee agent-1          # Claim specific issue
filigree claim-next --assignee agent-1          # Claim highest-priority ready issue
filigree claim-next --assignee agent-1 --type=bug --priority-max=1
filigree release <id>                           # Release back to open
```

### Batch Operations

```bash
filigree batch-update <id1> <id2> --priority=0     # Update multiple issues
filigree batch-close <id1> <id2> --reason="Sprint complete"
```

### Planning

```bash
filigree create-plan --file plan.json       # Create milestone/phase/step hierarchy
filigree plan <milestone-id>                # Show plan tree with progress
```

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

### Workflow Templates

```bash
filigree types                              # List all types with state flows
filigree type-info <type>                   # Full workflow definition
filigree transitions <id>                   # Valid next states for an issue
filigree validate <id>                      # Validate against template
filigree packs                              # List enabled packs
filigree guide <pack>                       # Workflow guide for a pack
filigree explain-state <type> <state>       # Explain a specific state
```

### Comments and Labels

```bash
filigree add-comment <id> "Found the root cause"
filigree get-comments <id>
filigree add-label <id> backend
filigree remove-label <id> backend
```

### Analytics and Events

```bash
filigree stats                              # Counts by status, type, ready/blocked
filigree metrics --days=30                  # Cycle time, lead time, throughput
filigree changes --since 2026-01-01T00:00   # Events since timestamp
filigree events <id>                        # Event history for one issue
```

### Data Management

```bash
filigree export backup.jsonl                # Export all data
filigree import backup.jsonl --merge        # Import (skip existing)
filigree archive --days=30                  # Archive old closed issues
filigree compact --keep=50                  # Compact event history
filigree doctor                             # Health check
filigree doctor --fix                       # Auto-fix what's possible
filigree dashboard --port=8377              # Launch web UI
```

### Migration

```bash
filigree migrate --from-beads               # Migrate from .beads database
```

## MCP Server

### Setup

The simplest path:

```bash
filigree install --claude-code    # Writes .mcp.json (or uses `claude mcp add`)
filigree install --codex          # Writes .codex/config.toml
```

Or manually add to `.mcp.json`:

```json
{
  "mcpServers": {
    "filigree": {
      "type": "stdio",
      "command": "filigree-mcp",
      "args": ["--project", "/path/to/project"]
    }
  }
}
```

### Tools (42)

Grouped by category:

**Read**
| Tool | Description |
|------|-------------|
| `get_issue` | Full issue details with deps, labels, children, ready status |
| `list_issues` | Filter by status, type, priority, parent |
| `search_issues` | Search by title and description |
| `get_ready` | Unblocked issues sorted by priority |
| `get_blocked` | Blocked issues with their blocker lists |
| `get_plan` | Milestone plan tree with progress |
| `get_comments` | Comments on an issue |
| `get_stats` | Counts by status, type, ready/blocked |
| `get_summary` | Pre-computed project summary |
| `get_template` | Field schema for an issue type |
| `get_metrics` | Cycle time, lead time, throughput |
| `get_critical_path` | Longest dependency chain |
| `get_changes` | Events since a timestamp |
| `get_issue_events` | Event history for one issue |

**Write**
| Tool | Description |
|------|-------------|
| `create_issue` | Create with type, priority, deps, labels, fields |
| `update_issue` | Update status, priority, title, fields |
| `close_issue` | Close with optional reason |
| `add_dependency` | Add blocker relationship |
| `remove_dependency` | Remove blocker |
| `add_comment` | Add comment to issue |
| `add_label` | Add label |
| `remove_label` | Remove label |
| `create_plan` | Create milestone/phase/step hierarchy |
| `undo_last` | Undo most recent reversible action |

**Claiming**
| Tool | Description |
|------|-------------|
| `claim_issue` | Atomically claim (optimistic lock) |
| `claim_next` | Claim highest-priority ready issue |
| `release_claim` | Release back to open |

**Batch**
| Tool | Description |
|------|-------------|
| `batch_update` | Update multiple issues |
| `batch_close` | Close multiple with per-item errors |

**Workflow**
| Tool | Description |
|------|-------------|
| `list_types` | All registered types with pack info |
| `get_type_info` | Full workflow definition |
| `get_valid_transitions` | Valid next states with readiness |
| `validate_issue` | Validate against template |
| `list_packs` | Enabled workflow packs |
| `get_workflow_guide` | Pack documentation |
| `get_workflow_states` | States by category |
| `explain_state` | State transitions and required fields |
| `reload_templates` | Refresh from disk |

**Data Management**
| Tool | Description |
|------|-------------|
| `export_jsonl` | Export to JSONL |
| `import_jsonl` | Import from JSONL |
| `archive_closed` | Archive old closed issues |
| `compact_events` | Compact event history |

### Resource

- `filigree://context` -- auto-generated project summary (vitals, ready work, blockers, recent activity)

### Prompt

- `filigree-workflow` -- workflow guide with optional live project context. Agents use this to understand how to interact with filigree.

## Workflow Templates

Each issue type has a state machine with defined transitions. Transitions can be `enforced` (blocked if invalid) or `warned` (allowed with a warning). Some transitions require specific fields to be populated.

### Packs

| Pack | Types | Purpose |
|------|-------|---------|
| `core` | task, bug, feature, epic | Day-to-day development work |
| `planning` | milestone, phase, step, work_package, deliverable | Hierarchical project planning |

### Core Type Flows

**Task**: `open` -> `in_progress` -> `closed`

**Bug**: `triage` -> `confirmed` -> `fixing` -> `verifying` -> `closed` (or `wont_fix`)

**Feature**: `proposed` -> `approved` -> `building` -> `reviewing` -> `done` (or `deferred`)

**Epic**: `open` -> `in_progress` -> `closed`

### Discovering Workflows

```bash
filigree types                       # List all types with state flows
filigree type-info task              # Full definition: states, transitions, fields
filigree guide core                  # Workflow guide for the core pack
filigree transitions <id>            # Valid next states for a specific issue
filigree explain-state bug triage    # What "triage" means for bugs
filigree workflow-states             # All states grouped by category (open/wip/done)
```

### Priority Scale

| Level | Name | Meaning |
|-------|------|---------|
| P0 | Critical | Drop everything |
| P1 | High | Do next |
| P2 | Medium | Default |
| P3 | Low | When time permits |
| P4 | Backlog | Someday/maybe |

## Agent Integration

### How Agents Use Filigree

**Foreground agents** (Claude Code, Codex) use the MCP server directly -- 42 tools for full read/write access without parsing text.

**Background subagents** use the CLI with `--json` for structured output:

```bash
filigree --actor sub-agent-3 claim-next --assignee sub-agent-3 --json
filigree --actor sub-agent-3 close <id> --json
```

### The Agent Workflow Loop

1. Read `filigree://context` resource for project state
2. `get_ready` to find unblocked work sorted by priority
3. `claim_issue` or `claim_next` to atomically claim a task
4. `get_valid_transitions` before status changes
5. Work on the task, `add_comment` to log progress
6. `close_issue` when done -- response includes newly-unblocked items
7. Repeat

### Session Resumption

When an agent resumes after downtime:

```bash
filigree changes --since 2026-02-14T10:00:00 --json
```

Returns all events since the timestamp -- status changes, new issues, closed items, dependency changes. The agent can reconstruct what happened while it was offline.

### Audit Trail

Every mutation records an actor. The `--actor` flag (CLI) or `actor` parameter (MCP) sets who performed the action:

```bash
filigree --actor agent-alpha create "Fix auth"
filigree --actor agent-beta close filigree-a3f9b2
```

Event history is queryable per-issue (`filigree events <id>`) or globally (`filigree changes --since`).

## Project Structure

### `.filigree/` Directory

```
.filigree/
  config.json    # {"prefix": "myproj", "version": 1, "enabled_packs": ["core", "planning"]}
  filigree.db       # SQLite database (WAL mode)
  context.md     # Auto-generated project summary, refreshed on every mutation
```

Issue IDs use the format `{prefix}-{6hex}`, e.g., `myproj-a3f9b2`. The prefix is set during `filigree init` (defaults to the directory name).

### Source Layout

```
src/filigree/
  __init__.py        # Package init
  core.py            # FiligreeDB class, SQLite schema, Issue dataclass
  cli.py             # Click CLI (all commands)
  mcp_server.py      # MCP server (42 tools, 1 resource, 1 prompt)
  templates.py       # Workflow template engine
  templates_data.py  # Built-in template definitions (9 types, 2 packs)
  summary.py         # context.md generator
  analytics.py       # Flow metrics (cycle time, lead time, throughput)
  install.py         # MCP config, CLAUDE.md injection, doctor checks
  migrate.py         # Beads-to-filigree migration
  dashboard.py       # FastAPI web dashboard
  logging.py         # Logging configuration
```

## Development

Requires Python 3.11+. Developed on 3.13.

```bash
# Clone and install dev dependencies
git clone https://github.com/tachyon-beep/filigree.git
cd filigree
uv sync --group dev

# Run full CI locally
make ci                # ruff check + mypy strict + pytest

# Individual targets
make lint              # Ruff check + format check
make format            # Auto-format with ruff
make typecheck         # Mypy strict mode
make test              # Pytest
make test-cov          # Pytest with coverage (fail-under=85%)
make clean             # Remove build artifacts
```

### Key Conventions

- **Ruff** for linting and formatting (line-length=120)
- **Mypy** in strict mode
- **Pytest** with pytest-asyncio for MCP server tests
- **Coverage** threshold at 85%
- Tests in `tests/`, source in `src/filigree/`

## License

[MIT](LICENSE) -- Copyright (c) 2026 John Morrissey
