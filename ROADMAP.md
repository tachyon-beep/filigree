# Roadmap

Filigree is an issue tracker designed for AI coding agents. This roadmap outlines directions, not commitments.

## Shipped (v1.0–v1.1)

- **MCP server** with 43 tools for agent-native interaction
- **Full CLI** with `--json` output and `--actor` audit trails
- **Custom workflow packs** -- 9 built-in packs (24 issue types) with enforced state machines, plus user-defined templates in `.filigree/templates/`
- **Web dashboard** -- single-page UI with filtering, search, and real-time updates
- **Dependency graph** -- blockers, ready-queue, and critical path analysis
- **Hierarchical planning** -- milestone/phase/step hierarchies with automatic unblocking
- **Atomic claiming** -- optimistic locking for multi-agent coordination
- **Import/export** -- JSONL format for backup and migration
- **Session resumption** -- event streams with `get_changes --since` for agent catch-up
- **Pre-computed context** -- `context.md` regenerated on every mutation
- **Documentation** -- CLI reference, MCP tools, workflow guides, agent integration patterns
- **Claude Code session hooks** -- `filigree session-context` injects a project snapshot (in-progress, ready queue, critical path, stats) at session start; `filigree ensure-dashboard` auto-starts the web dashboard
- **Workflow skill pack** -- `filigree-workflow` skill teaches agents triage patterns, sprint planning, dependency management, and multi-agent team coordination via progressive disclosure
- **One-stop install** -- `filigree install` wires up MCP, hooks, skills, CLAUDE.md, and .gitignore; `filigree doctor` validates everything
- **MCP pagination** -- list endpoints cap at 50 results with `has_more` indicator and `no_limit` override

## Shipped (v1.2)

- **Multi-project dashboard** -- ephemeral project registry, `ProjectManager` connection pool, project switcher dropdown, per-project API routing; MCP servers self-register on startup
- **Dashboard UX overhaul** -- equal-width Kanban columns, drag-and-drop status changes with transition validation, header density reduction, type-filter/mode toggle conflict resolution, WCAG-compliant status badges, P0/P1 text priority labels, stale issue list, workflow auto-select, transition hints, claim modal improvements
- **Deep Teal color theme** -- full migration from hardcoded Tailwind colors to 20 CSS custom properties on `:root`/`[data-theme="light"]`, 15 utility classes, JS `THEME_COLORS` object for Cytoscape; dark and light themes with consistent palette

## Phase 1: Stability & Foundation (Immediate)

*Technical debt, bug fixes, and cross-platform reliability.*

- **Cross-platform parity** -- replace `fcntl` with a cross-platform file locking mechanism (e.g., `portalocker`) to enable Server Mode and Session Hooks on Windows; abstract PID verification beyond raw `/proc` dependency
- **Data integrity & scaling** -- increase ID entropy from 6 to 10 characters or switch to collision-resistant Crockford Base32; refactor `SCHEMA_V1_SQL` definition to be non-brittle
- **Core reliability** -- fix lossy `undo_last` for claims by recording previous assignee state in event log; standardize batch operations in CLI to match MCP server's per-issue error reporting; fix broken template requirement display (`required` -> `required_at`)

## Phase 2: Codebase Intelligence (Next)

*Bridging the gap between tasks (intent) and source code (implementation).*

- **File hotspot analysis** -- "Hotspots" section in `context.md` listing files with high churn and high scan finding density to warn agents before they start work
- **Traceability graph** -- automatic association of files to issues based on git commit messages parsed during `filigree watch`; bidirectional navigation between issues and file records
- **Schema-aware interaction** -- type coercion in CLI and Dashboard so custom fields respect template type hints (`boolean`, `number`, `date`) instead of defaulting to strings
- **Workflow visualization** -- `filigree visualize <type>` to export Mermaid/Graphviz diagrams of state machines

## Phase 3: Agentic Coordination (Strategic)

*Active collaboration protocols for multi-agent swarms.*

- **Semantic intelligence** -- lightweight vector store sidecar for semantic search to detect and prevent duplicate bugs/tasks at creation time
- **Explicit handoff protocols** -- `required_approver_role` on transitions (e.g., "Requirement" needs approval from a "Security Agent")
- **Event-driven UI (SSE)** -- Server-Sent Events in the dashboard for real-time updates when agents claim or close work
- **Async database layer** -- migrate from `sqlite3` to `aiosqlite` in `dashboard.py` to prevent synchronous blocking during heavy analytics queries

## Phase 4: Ecosystem & Enterprise (Future)

*Automation, integration, and large-scale use.*

- **Bottleneck analytics** -- state-level duration tracking (e.g., "Avg time in 'verifying'") to identify process stalls
- **Plugin system** -- pre/post transition hooks for custom automation (e.g., trigger a scan when a bug moves to 'verifying')
- **GitHub bidirectional sync** -- local proxy for GitHub Issues, supporting offline-first agent work with upstream sync
- **Visual workflow designer** -- drag-and-drop UI in the dashboard for creating and modifying workflow packs

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved. Feature requests and bug reports are welcome via [GitHub Issues](https://github.com/tachyon-beep/filigree/issues).
