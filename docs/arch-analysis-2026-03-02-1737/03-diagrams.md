# Architecture Diagrams

## C4 Level 1 — System Context

```
┌─────────────────────────────────────────────────────────────────┐
│                        EXTERNAL ACTORS                          │
│                                                                 │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────────────┐    │
│  │ Developer │   │ AI Agent     │   │ External Scanner     │    │
│  │ (Human)   │   │ (Claude,     │   │ (Codex bug-hunt,     │    │
│  │           │   │  Codex, etc) │   │  custom TOML-defined)│    │
│  └─────┬─────┘   └──────┬───────┘   └──────────┬───────────┘    │
│        │                │                       │                │
│    CLI │           MCP  │              POST     │                │
│  + Web │         (stdio │            /api/v1/   │                │
│        │          or    │          scan-results │                │
│        │          HTTP) │                       │                │
└────────┼────────────────┼───────────────────────┼────────────────┘
         │                │                       │
         ▼                ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│                     FILIGREE v1.4.0                              │
│                                                                 │
│              Agent-native issue tracker with                    │
│          convention-based project discovery (.filigree/)         │
│                                                                 │
│        ┌──────────────────────────────────────┐                 │
│        │         SQLite (WAL mode)            │                 │
│        │      .filigree/filigree.db           │                 │
│        └──────────────────────────────────────┘                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## C4 Level 2 — Container Diagram

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                              FILIGREE                                          │
│                                                                                │
│  ┌──────────────┐   ┌──────────────────┐   ┌────────────────────────────────┐  │
│  │   CLI        │   │   MCP Server     │   │   Dashboard                    │  │
│  │              │   │                  │   │                                │  │
│  │  Click       │   │  53 Tools        │   │  ┌─────────────┐  ┌────────┐  │  │
│  │  56 commands │   │  1 Resource      │   │  │ FastAPI API  │  │ SPA    │  │  │
│  │  6 domain    │   │  1 Prompt        │   │  │ 4 route      │  │ 8 views│  │  │
│  │  modules     │   │  5 domain        │   │  │ modules      │  │ ES mods│  │  │
│  │              │   │  modules         │   │  │              │  │        │  │  │
│  │  Entry:      │   │                  │   │  │              │  │ 7.3K   │  │  │
│  │  filigree    │   │  Transports:     │   │  │  Dual-mount  │  │ LOC JS │  │  │
│  │              │   │  stdio (ethereal)│   │  │  (server     │  │        │  │  │
│  │              │   │  HTTP  (server)  │   │  │   mode)      │  │        │  │  │
│  └──────┬───────┘   └────────┬─────────┘   └──┬─────────────┘  └────────┘  │  │
│         │                    │                 │                             │  │
│         │      All three interface layers      │                             │  │
│         │      share the same core             │                             │  │
│         └──────────┬─────────┘                 │                             │  │
│                    │                           │                             │  │
│                    ▼                           │                             │  │
│  ┌─────────────────────────────────────────────┼──────────────────────────┐  │  │
│  │              FiligreeDB (Core)              │                          │  │  │
│  │                                             │                          │  │  │
│  │  ┌──────────────┐  ┌───────────────┐  ┌────┴─────────┐               │  │  │
│  │  │ IssuesMixin  │  │ FilesMixin    │  │ EventsMixin  │               │  │  │
│  │  │ CRUD, batch, │  │ files, scans, │  │ event log,   │               │  │  │
│  │  │ search, claim│  │ associations, │  │ undo, archive│               │  │  │
│  │  │ (954 LOC)    │  │ timeline      │  │ (296 LOC)    │               │  │  │
│  │  └──────────────┘  │ (1241 LOC)    │  └──────────────┘               │  │  │
│  │                    └───────────────┘                                   │  │  │
│  │  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐               │  │  │
│  │  │WorkflowMixin │  │ MetaMixin     │  │PlanningMixin │               │  │  │
│  │  │ templates,   │  │ comments,     │  │ deps, ready, │               │  │  │
│  │  │ transitions  │  │ labels, stats,│  │ blocked,     │               │  │  │
│  │  │ (250 LOC)    │  │ export/import │  │ critical path│               │  │  │
│  │  └──────────────┘  │ (334 LOC)     │  │ (575 LOC)    │               │  │  │
│  │                    └───────────────┘  └──────────────┘               │  │  │
│  │                                                                       │  │  │
│  │                        DBMixinProtocol                                │  │  │
│  │                   (typing.Protocol for mypy)                         │  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │  │
│                                                                             │  │
│  ┌───────────────────────┐  ┌───────────────────────────────────────────┐   │  │
│  │  Workflow Templates   │  │  Type System                              │   │  │
│  │                       │  │                                           │   │  │
│  │  TemplateRegistry     │  │  83 TypedDicts in 7 modules               │   │  │
│  │  9 packs, 20+ types   │  │  Zero outbound imports (prevents cycles)  │   │  │
│  │  State machines       │  │  TOOL_ARGS_MAP for sync testing           │   │  │
│  │  Frozen dataclasses   │  │                                           │   │  │
│  └───────────────────────┘  └───────────────────────────────────────────┘   │  │
│                                                                             │  │
│  ┌──────────────────────────────────────────────────────────────────────┐   │  │
│  │                        Infrastructure                                │   │  │
│  │                                                                      │   │  │
│  │  install.py + install_support/   │  hooks.py + ephemeral.py          │   │  │
│  │  (MCP config, doctor, hooks,     │  (SessionStart, PID/port,         │   │  │
│  │   CLAUDE.md injection)           │   deterministic port)             │   │  │
│  │                                  │                                   │   │  │
│  │  migrations.py + migrate.py      │  server.py                       │   │  │
│  │  (schema versioning, upgrade)    │  (multi-project daemon)           │   │  │
│  │                                  │                                   │   │  │
│  │  summary.py + analytics.py       │  scanners.py                     │   │  │
│  │  (context.md, flow metrics)      │  (TOML configs, process spawn)   │   │  │
│  └──────────────────────────────────────────────────────────────────────┘   │  │
│                                                                             │  │
│  ┌──────────────────────────────────────────────────────────────────────┐   │  │
│  │                         SQLite Database                              │   │  │
│  │  .filigree/filigree.db  (WAL mode, FK ON, 5s busy timeout)          │   │  │
│  │  10 tables + FTS5 virtual table  │  PRAGMA user_version = 5         │   │  │
│  └──────────────────────────────────────────────────────────────────────┘   │  │
│                                                                             │  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## C4 Level 3 — Component Dependency Graph

```
                           ┌─────────┐
                           │ CLI     │
                           │ (Click) │
                           └────┬────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        │                       │                        │
        ▼                       ▼                        ▼
  ┌───────────┐         ┌──────────────┐         ┌──────────────┐
  │ MCP Server│         │  Dashboard   │         │Infrastructure│
  │ (53 tools)│◄────────│  (FastAPI)   │         │  (install,   │
  └─────┬─────┘  mounts │              │         │   hooks,     │
        │        /mcp   └──────┬───────┘         │   migrate)   │
        │                      │                 └──────┬───────┘
        │                      │                        │
        └──────────┬───────────┘                        │
                   │                                    │
                   ▼                                    │
        ┌──────────────────┐                            │
        │    FiligreeDB    │◄───────────────────────────┘
        │   (core.py)      │
        │  6 mixins via MI │
        └────────┬─────────┘
                 │
        ┌────────┼────────────────┐
        │        │                │
        ▼        ▼                ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐
  │Templates │ │Type      │ │SQLite DB │
  │Registry  │ │System    │ │(WAL)     │
  │(state    │ │(TypedDict│ │          │
  │ machines)│ │contracts)│ │          │
  └──────────┘ └──────────┘ └──────────┘
```

## Data Flow Diagram — Issue Lifecycle

```
  Agent/User
      │
      │  create_issue(title, type, priority, fields)
      ▼
  ┌──────────┐     ┌──────────────┐     ┌─────────────┐
  │Interface │────▶│  FiligreeDB  │────▶│  Templates   │
  │ Layer    │     │              │     │  Registry    │
  │(CLI/MCP/ │     │  validates:  │     │              │
  │Dashboard)│     │  - status    │     │  enforces:   │
  │          │     │  - type      │     │  - state     │
  └──────────┘     │  - fields    │     │    machine   │
                   │  - parent_id │     │  - field     │
                   │              │     │    schemas   │
                   │  generates:  │     │  - hard/soft │
                   │  - prefix-ID │     │    gates     │
                   │              │     │              │
                   │  records:    │     └─────────────┘
                   │  - event     │
                   │  - labels    │
                   │              │
                   │  refreshes:  │
                   │  - context.md│
                   │              │
                   └──────┬───────┘
                          │
                          ▼
                   ┌──────────────┐
                   │  SQLite DB   │
                   │              │
                   │  issues      │
                   │  events      │
                   │  labels      │
                   │  dependencies│
                   │  comments    │
                   └──────────────┘
```

## Cross-Mixin Dependency Graph

```
                ┌──────────────────┐
                │  WorkflowMixin   │  ◄── Base provider (no deps)
                │  templates,      │
                │  transitions,    │
                │  category resolve│
                └────────┬─────────┘
                         │
            ┌────────────┼─────────────┐
            │            │             │
            ▼            ▼             ▼
   ┌──────────────┐ ┌──────────┐ ┌──────────────┐
   │ EventsMixin  │ │MetaMixin │ │PlanningMixin │
   │ _record_event│ │ labels,  │ │ deps, ready, │
   │ undo, archive│ │ comments,│ │ critical path│
   └──────┬───────┘ │ stats,   │ │ plans        │
          │         │ export   │ └──────┬───────┘
          │         └──────────┘        │
          │              ▲              │
          │              │              │
          ▼              │              ▼
   ┌──────────────┐      │       ┌──────────────┐
   │ IssuesMixin  │──────┘       │ FilesMixin   │
   │ CRUD, batch, │              │ files, scans │
   │ search, claim│              │ associations │
   │              │              │ timeline     │
   └──────────────┘              └──────────────┘
          ▲                            │
          └────────────────────────────┘
                    depends on
```

## Installation Mode Comparison

```
┌─────────────────────────────────┬──────────────────────────────────┐
│        ETHEREAL MODE            │         SERVER MODE              │
│     (default, per-session)      │     (persistent daemon)          │
├─────────────────────────────────┼──────────────────────────────────┤
│                                 │                                  │
│  SessionStart Hook              │  SessionStart Hook               │
│       │                         │       │                          │
│       ▼                         │       ▼                          │
│  ensure_dashboard_running()     │  register_project()              │
│       │                         │       │                          │
│       ▼                         │       ▼                          │
│  check PID/port files           │  write server.json               │
│       │                         │  (portalocker)                   │
│       ▼                         │       │                          │
│  portalocker LOCK_EX            │       ▼                          │
│       │                         │  POST /api/reload                │
│       ▼                         │                                  │
│  spawn detached dashboard       │  Dashboard already running       │
│  write PID + port files         │  at ~/.config/filigree/          │
│       │                         │                                  │
│       ▼                         │                                  │
│  MCP: stdio transport           │  MCP: streamable-HTTP            │
│  (per-session process)          │  (mounted at /mcp)               │
│                                 │                                  │
│  DB: single-project             │  DB: ProjectStore                │
│  (module-level _db)             │  (ContextVar per-request)        │
│                                 │                                  │
│  Port: 8400 + hash(path) % 1000│  Port: from server.json          │
│                                 │  (default 8377)                  │
└─────────────────────────────────┴──────────────────────────────────┘
```

## Frontend Module Architecture

```
                    dashboard.html (541 LOC)
                    ┌─────────────┐
                    │ HTML shell  │
                    │ CSS theming │
                    │ CDN + SRI   │
                    └──────┬──────┘
                           │  <script type="module">
                           ▼
                    ┌─────────────┐
                    │   app.js    │  Entry point (675 LOC)
                    │ Module      │  - wires callbacks
                    │ orchestrator│  - keyboard shortcuts
                    │ ~90 window  │  - 15s auto-refresh
                    │ exports     │  - init sequence
                    └──────┬──────┘
                           │ imports
            ┌──────────────┼──────────────┬──────────────┐
            │              │              │              │
            ▼              ▼              ▼              ▼
      ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
      │ state.js │  │ api.js   │  │router.js │  │filters.js│
      │ (174)    │  │ (337)    │  │ (186)    │  │ (427)    │
      │ global   │  │ REST     │  │ hash     │  │ filter   │
      │ state    │  │ client   │  │ routing  │  │ engine   │
      └──────────┘  └──────────┘  └──────────┘  └──────────┘
            │
            │  imported by all views
            ▼
      ┌──────────┐
      │  ui.js   │  (511 LOC)
      │ escHtml  │  XSS prevention
      │ toasts   │  Tour system
      │ modals   │  Theme toggle
      └──────────┘
            │
            │
      ┌─────┴──────────────────────────────────────────────┐
      │                   VIEWS                             │
      │                                                     │
      │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ │
      │  │graph.js │ │kanban.js│ │detail.js│ │files.js │ │
      │  │(1223)   │ │(414)    │ │(598)    │ │(777)    │ │
      │  │Cytoscape│ │drag&drop│ │inline   │ │findings │ │
      │  │dagre    │ │clusters │ │editing  │ │scans    │ │
      │  └─────────┘ └─────────┘ └─────────┘ └─────────┘ │
      │                                                     │
      │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ │
      │  │releases │ │metrics  │ │activity │ │health.js│ │
      │  │(679)    │ │(223)    │ │(82)     │ │(259)    │ │
      │  │roadmap  │ │sparkline│ │event    │ │hotspots │ │
      │  │tree     │ │charts   │ │feed     │ │severity │ │
      │  └─────────┘ └─────────┘ └─────────┘ └─────────┘ │
      │                                                     │
      │  ┌─────────┐                                       │
      │  │workflow │                                       │
      │  │(246)    │                                       │
      │  │state    │                                       │
      │  │diagram  │                                       │
      │  └─────────┘                                       │
      └────────────────────────────────────────────────────┘
```
