# Roadmap

Filigree is an issue tracker designed for AI coding agents. Version 1.0 has shipped with a full feature set for local-first, MCP-native issue tracking. This roadmap outlines directions, not commitments.

## Shipped (v1.0)

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

## Near-term (1.x releases)

- **Hook system** -- extensible pre/post hooks on state transitions for custom automation
- **GitHub integration** -- sync issues bidirectionally with GitHub Issues
- **`filigree watch`** -- file-system triggered actions (auto-close on commit, etc.)
- **Dashboard enhancements** -- dependency graph visualization, drag-and-drop prioritization
- **Additional import formats** -- CSV, GitHub Issues JSON, Linear export

## Future

- **Multi-project support** -- cross-project dependencies and shared dashboards
- **Plugin system** -- custom integrations via Python entry points
- **Notification hooks** -- webhooks, Slack, and email on state changes
- **Hardening** -- performance optimization for large databases (10k+ issues), fuzz testing
- **Team coordination** -- enhanced multi-agent features (work queues, capacity tracking)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved. Feature requests and bug reports are welcome via [GitHub Issues](https://github.com/tachyon-beep/filigree/issues).
