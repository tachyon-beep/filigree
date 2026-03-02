# Discovery Findings

## Project Identity
- **Name**: Filigree
- **Version**: 1.4.0 (in development, `v1.4.0-architectural-refactor` branch)
- **Purpose**: Agent-native issue tracker with convention-based project discovery
- **Repository**: github.com/tachyon-beep/filigree

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ (typed, `py.typed` marker) |
| Database | SQLite with WAL mode (no ORM) |
| CLI Framework | Click |
| Agent Protocol | MCP (Model Context Protocol) via `mcp` SDK |
| Dashboard API | FastAPI + Uvicorn |
| Dashboard Frontend | Vanilla JS + Tailwind CDN (single HTML file) |
| Process Management | portalocker (file locking), PID files |
| Build System | Hatchling |
| Package Manager | uv |
| Linting | Ruff (26 rule sets), Mypy (strict mode) |
| Testing | Pytest + pytest-asyncio, 85% coverage floor |
| CI/CD | GitHub Actions |
| Frontend Linting | Biome (for HTML/JS) |

## Architecture Style
- **Convention-based discovery**: walks up from CWD to find `.filigree/` directory
- **Three interface layers** expose the same core: CLI, MCP Server, Dashboard REST API
- **No ORM**: direct SQLite with parameterized queries and WAL mode
- **Mixin decomposition**: `FiligreeDB` composes 6 specialized mixins via multiple inheritance
- **Two installation modes**: "ethereal" (single-project, ephemeral dashboard) and "server" (multi-project persistent daemon)
- **Typed contracts**: TypedDicts for all API boundaries, frozen dataclasses for config

## Directory Organization
```
src/filigree/
├── core.py              # Central DB class (FiligreeDB) — composed from mixins
├── db_base.py           # DBMixinProtocol — shared Protocol for mixin type checking
├── db_issues.py         # IssuesMixin — CRUD, search, batch, claiming
├── db_files.py          # FilesMixin — file records, scan findings, associations
├── db_events.py         # EventsMixin — event log, undo, changes-since
├── db_planning.py       # PlanningMixin — plans, dependencies, critical path
├── db_meta.py           # MetaMixin — stats, import/export, archive
├── db_schema.py         # Schema SQL + version constant
├── db_workflow.py       # WorkflowMixin — template registration, state transitions
├── types/               # TypedDict contracts (no runtime imports from core!)
│   ├── core.py          # IssueDict, FileRecordDict, PaginatedResult, etc.
│   ├── api.py           # MCP/dashboard response types (50+ TypedDicts)
│   ├── inputs.py        # MCP tool argument types (TOOL_ARGS_MAP registry)
│   ├── events.py        # EventRecord, UndoResult
│   ├── files.py         # FileDetail, ScanIngestResult, etc.
│   ├── planning.py      # PlanTree, FlowMetrics, StatsResult
│   └── workflow.py      # TemplateInfo, TypeListItem, etc.
├── templates.py         # TemplateRegistry — workflow state machines
├── templates_data.py    # Built-in pack/type definitions (1700+ LOC data)
├── cli.py               # Click group entry point
├── cli_common.py        # Shared CLI utilities
├── cli_commands/         # Domain-grouped CLI subcommands
├── mcp_server.py        # MCP Server class + tool/prompt/resource registration
├── mcp_tools/           # Domain-grouped MCP tool handlers
├── dashboard.py         # FastAPI app factory, server/ethereal mode management
├── dashboard_routes/    # Domain-grouped REST API route modules
├── static/              # Single-file HTML dashboard
├── install.py           # MCP config, CLAUDE.md injection, skill install
├── install_support/     # Doctor, hooks, integration installers
├── hooks.py             # SessionStart hook logic (context building)
├── ephemeral.py         # Deterministic port, PID lifecycle (ethereal mode)
├── server.py            # Server mode config, daemon management
├── scanners.py          # External scanner TOML configs + process spawning
├── migrate.py           # Migration runner
├── migrations.py        # Schema migration definitions
├── summary.py           # context.md generation
├── analytics.py         # Cycle time, lead time, throughput metrics
├── validation.py        # Input sanitization (actor, etc.)
└── logging.py           # Structured logging setup
```

## Entry Points
1. `filigree` CLI → `cli.py:cli` (Click group)
2. `filigree-mcp` → `mcp_server.py:main` (stdio MCP server)
3. `filigree-dashboard` → `dashboard.py:main` (FastAPI/Uvicorn)

## Key Design Patterns

### 1. Mixin Composition
```
FiligreeDB(FilesMixin, IssuesMixin, EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin)
```
Each mixin inherits from `DBMixinProtocol` (a `typing.Protocol`) ensuring `self.conn`, `self.prefix`, `self.get_issue()` etc. are available for type checking without circular imports.

### 2. Convention-based Discovery
`find_filigree_root()` walks up from CWD to find `.filigree/`. This directory contains `filigree.db` (SQLite), `config.json` (prefix, packs), `context.md` (session snapshot), and ephemeral PID/port files.

### 3. Three Parallel Interface Layers
CLI, MCP, and Dashboard all wrap the same `FiligreeDB` core. Each has its own domain grouping:
- `cli_commands/{issues,planning,meta,workflow,admin,server}.py`
- `mcp_tools/{issues,files,meta,planning,workflow,common}.py`
- `dashboard_routes/{issues,files,analytics,releases,common}.py`

### 4. Workflow Template System
Types (bug, task, feature, epic, milestone, phase, step, release, requirement) are defined as state machines in `templates_data.py`. The `TemplateRegistry` (templates.py) provides transition enforcement, field validation, and workflow guides. Packs (core, planning, release, engineering) bundle related types.

### 5. Two Installation Modes
- **Ethereal**: single-project, ephemeral dashboard auto-launched per session
- **Server**: multi-project persistent daemon with `server.json` config at `~/.config/filigree/`

## Test Organization
```
tests/
├── core/         # FiligreeDB unit tests (CRUD, deps, batch, plans, workflow, undo)
├── api/          # Dashboard REST API tests (via httpx/AsyncClient)
├── cli/          # CLI command tests (via Click CliRunner)
├── mcp/          # MCP tool handler tests
├── templates/    # Template registry, transitions, DB integration
├── analytics/    # Analytics + summary generation
├── install/      # Installation, hooks, server management
├── migrations/   # Schema migration tests
├── static/       # XSS guard tests for dashboard HTML
├── unit/         # Validation utilities
├── util/         # Cross-cutting integration tests
└── workflows/    # End-to-end workflow tests
```

## Dependencies (Runtime)
- `click>=8.0` — CLI framework
- `mcp>=1.0,<2` — MCP protocol SDK
- `portalocker>=2.7,<4` — File locking (ephemeral mode)
- `fastapi>=0.115` (optional) — Dashboard API
- `uvicorn>=0.34` (optional) — ASGI server

## Preliminary Observations

### Strengths
- Clear layered architecture: DB → Core → Interface layers
- Strong type system with 90+ TypedDict contracts
- Mixin decomposition keeps individual files manageable
- Comprehensive test coverage (85%+ floor with 70+ test files)
- Strict mypy + extensive ruff ruleset

### Potential Concerns
- `templates_data.py` at 1,718 LOC is large data-as-code (but unlikely to grow arbitrarily)
- `db_files.py` (1,241 LOC) and `db_issues.py` (954 LOC) are the largest logic files
- Three interface layers means feature additions touch 4+ files (core + CLI + MCP + dashboard)
- Single-file dashboard HTML may become hard to maintain as features grow (currently 540 lines — manageable)
