# Architecture

Filigree's internal architecture: directory layout, database schema, source modules, and key design decisions.

## Contents

- [`.filigree/` Directory](#filigree-directory)
- [Source Layout](#source-layout)
- [Database Schema](#database-schema)
- [Key Design Decisions](#key-design-decisions)

## `.filigree/` Directory

Every filigree project has a `.filigree/` directory at its root, discovered by walking up the filesystem (like `.git/`):

```
.filigree/
  config.json    # Project config: prefix, version, enabled_packs
  filigree.db    # SQLite database (WAL mode)
  context.md     # Auto-generated project summary, refreshed on every mutation
  templates/     # Project-local workflow template overrides (optional)
  packs/         # Installed workflow packs (optional)
```

### `config.json`

```json
{
  "prefix": "myproj",
  "version": 1,
  "enabled_packs": ["core", "planning"]
}
```

- **prefix** — used in issue IDs (`{prefix}-{6hex}`, e.g., `myproj-a3f9b2`)
- **version** — config format version
- **enabled_packs** — which workflow packs are active

## Source Layout

```
src/filigree/
  __init__.py        # Package version and metadata
  core.py            # FiligreeDB class, SQLite schema, Issue dataclass
  cli.py             # Click CLI (all commands)
  mcp_server.py      # MCP server (43 tools, 1 resource, 1 prompt)
  templates.py       # Workflow template engine (registry, validation, transitions)
  templates_data.py  # Built-in template definitions (24 types across 9 packs)
  summary.py         # context.md generator
  analytics.py       # Flow metrics (cycle time, lead time, throughput)
  install.py         # MCP config, CLAUDE.md injection, doctor checks
  migrate.py         # Beads-to-filigree migration
  dashboard.py       # FastAPI web dashboard
  logging.py         # Logging configuration
```

### Module Responsibilities

**`core.py`** — the heart of filigree. Contains the `FiligreeDB` class wrapping all SQLite operations, the `Issue` dataclass, schema DDL, and migration functions. Both `cli.py` and `mcp_server.py` import from here — no business logic is duplicated.

**`templates.py`** — the workflow engine. Defines `TemplateRegistry` (lazy-loaded singleton), frozen dataclasses for `StateDefinition`, `TransitionDefinition`, `FieldSchema`, and `TypeTemplate`. Handles transition validation, field requirement checking, and state category mapping.

**`templates_data.py`** — pure data, no logic. Contains all built-in pack definitions as nested dicts. Separated from `templates.py` so template definitions are readable and editable without touching engine code.

**`summary.py`** — generates `context.md` after every mutation. Queries the database for vitals, ready queue, blocked items, and recent activity, then writes a markdown file agents can read at session start.

**`analytics.py`** — computes flow metrics from the event stream: cycle time (start to close), lead time (create to close), throughput (issues closed per period).

## Database Schema

SQLite with WAL mode. Schema version 6.

### Tables

#### `issues`

```sql
CREATE TABLE issues (
    id          TEXT PRIMARY KEY,       -- {prefix}-{6hex}
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    priority    INTEGER NOT NULL DEFAULT 2,  -- 0-4
    type        TEXT NOT NULL DEFAULT 'task',
    parent_id   TEXT REFERENCES issues(id) ON DELETE SET NULL,
    assignee    TEXT DEFAULT '',
    created_at  TEXT NOT NULL,          -- ISO 8601
    updated_at  TEXT NOT NULL,
    closed_at   TEXT,
    description TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    fields      TEXT DEFAULT '{}'       -- JSON custom fields
);
```

Indexed on `status`, `type`, `parent_id`, `priority`.

#### `dependencies`

```sql
CREATE TABLE dependencies (
    issue_id       TEXT NOT NULL REFERENCES issues(id),
    depends_on_id  TEXT NOT NULL REFERENCES issues(id),
    type           TEXT NOT NULL DEFAULT 'blocks',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (issue_id, depends_on_id)
);
```

#### `events`

```sql
CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    event_type TEXT NOT NULL,
    actor      TEXT DEFAULT '',
    old_value  TEXT,
    new_value  TEXT,
    comment    TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
```

Indexed on `issue_id` and `created_at`. Powers the audit trail, undo, session resumption, and analytics.

#### `comments`

```sql
CREATE TABLE comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    author     TEXT DEFAULT '',
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

#### `labels`

```sql
CREATE TABLE labels (
    issue_id TEXT NOT NULL REFERENCES issues(id),
    label    TEXT NOT NULL,
    PRIMARY KEY (issue_id, label)
);
```

#### `type_templates`

```sql
CREATE TABLE type_templates (
    type          TEXT PRIMARY KEY,
    pack          TEXT NOT NULL DEFAULT 'core',
    definition    TEXT NOT NULL,       -- JSON workflow definition
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
```

#### `packs`

```sql
CREATE TABLE packs (
    name          TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    definition    TEXT NOT NULL,       -- JSON pack definition
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    enabled       BOOLEAN NOT NULL DEFAULT 1
);
```

### FTS5 Full-Text Search

```sql
CREATE VIRTUAL TABLE issues_fts USING fts5(
    title, description, content='issues', content_rowid='rowid'
);
```

Kept in sync via triggers on INSERT, UPDATE, and DELETE. Used by `search_issues` for fast full-text search.

## Key Design Decisions

### Convention-Based Discovery

Filigree finds the project root by walking up the filesystem looking for `.filigree/`, the same way git finds `.git/`. This means:

- No environment variables to set
- No config files to parse
- No server URLs to resolve
- Works from any subdirectory of the project

### Template-Driven Validation

Workflows are defined as data (state machines with transitions and field requirements), not code. This means:

- New types can be added without code changes
- Packs can be installed from disk
- Validation rules are introspectable (agents can query valid transitions)

### Category Abstraction

Every state maps to one of three categories: `open`, `wip`, `done`. This allows:

- Cross-type queries (`list --status=open` finds bugs in `triage`, features in `proposed`, tasks in `open`)
- Universal ready-queue logic (any `open`-category issue with no blockers is "ready")
- Consistent progress metrics regardless of type-specific state names

### Event Sourcing

Every mutation creates an event record. This enables:

- **Audit trail** — who did what and when, per-issue or globally
- **Undo** — reverse the most recent reversible action
- **Session resumption** — agents catch up via `get_changes --since`
- **Analytics** — cycle time, lead time, and throughput computed from events
- **Archival** — old events can be compacted without losing issue state

### Batch Optimizations

Batch operations (`batch_update`, `batch_close`) execute in a single transaction to eliminate N+1 query patterns. Per-item errors are reported without aborting the entire batch.

### Atomic Claiming

`claim_issue` uses optimistic locking — it checks the current assignee and sets the new one in a single atomic operation. If another agent claimed the issue between the check and the update, the operation fails. This prevents double-work in multi-agent scenarios without requiring external locks.

### Lazy-Loaded Template Registry

The `TemplateRegistry` is loaded lazily on first access and cached for the session. This avoids startup overhead when templates aren't needed (e.g., simple CRUD operations) while ensuring templates are always available for validation.

### Pre-Computed Context

Rather than having agents query for project state at session start, `context.md` is regenerated on every mutation. This inverts the cost: writes are slightly slower, but reads (which happen at every agent session start) are instant.
