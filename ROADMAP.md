# Roadmap

Filigree is in early development (0.x). This roadmap outlines directions, not commitments.

## Completed

- **Custom workflow packs** -- 9 built-in packs with enable/disable via `config.json` (`enabled_packs`), plus support for user-defined templates in `.filigree/templates/`

## Near-term (0.x releases)

- **Documentation site** -- mkdocs-based docs with tutorials and API reference
- **GitHub integration** -- sync issues bidirectionally with GitHub Issues
- **`filigree watch`** -- file-system triggered actions (auto-close on commit, etc.)
- **Dashboard improvements** -- filtering, search, and dependency graph visualization
- **Import/export formats** -- CSV, GitHub Issues JSON, Linear export

## Future

- **Multi-project support** -- cross-project dependencies and shared dashboards
- **Plugin system** -- custom integrations via Python entry points
- **Notification hooks** -- webhooks, Slack, and email on state changes
- **Team coordination** -- enhanced multi-agent features (work queues, capacity tracking)
- **Performance at scale** -- optimization for large databases (10k+ issues)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved. Feature requests and bug reports are welcome via [GitHub Issues](https://github.com/tachyon-beep/filigree/issues).
