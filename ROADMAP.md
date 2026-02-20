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

## Near-term (1.x releases)

- **Event-driven plugin system** -- extensible pre/post hooks on state transitions for custom automation
- **GitHub integration** -- sync issues bidirectionally with GitHub Issues
- **`filigree watch`** -- file-system triggered actions (auto-close on commit, etc.)
- **Additional import formats** -- CSV, GitHub Issues JSON, Linear export

## Future

- **Multi-project federation** -- cross-project dependencies and shared dashboards
- **Notification hooks** -- webhooks, Slack, and email on state changes
- **Hardening** -- performance optimization for large databases (10k+ issues), fuzz testing
- **Team coordination** -- enhanced multi-agent features (work queues, capacity tracking)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved. Feature requests and bug reports are welcome via [GitHub Issues](https://github.com/tachyon-beep/filigree/issues).
