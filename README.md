# Filigree

Agent-native issue tracker with convention-based project discovery.

[![CI](https://github.com/tachyon-beep/filigree/actions/workflows/ci.yml/badge.svg)](https://github.com/tachyon-beep/filigree/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/filigree)](https://pypi.org/project/filigree/)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/filigree)](https://pypi.org/project/filigree/)
[![License: MIT](https://img.shields.io/pypi/l/filigree)](https://github.com/tachyon-beep/filigree/blob/main/LICENSE)

## What Is Filigree?

Filigree is a lightweight, SQLite-backed issue tracker designed for AI coding agents (Claude Code, Codex, etc.) to use as first-class citizens. It exposes 43 MCP tools so agents interact natively, plus a full CLI for humans and background subagents.

Traditional issue trackers are human-first — agents have to scrape CLI output or parse API responses. Filigree flips this: agents read a pre-computed `context.md` at session start, claim work with optimistic locking, follow enforced workflow state machines, and resume sessions via event streams.

Filigree is single-project and local-first. No server, no cloud, no accounts. Just a `.filigree/` directory (like `.git/`) containing a SQLite database, configuration, and auto-generated context summary.

### Key Features

- **MCP server** with 43 tools — agents interact natively without parsing text
- **Full CLI** with `--json` output for background subagents and `--actor` for audit trails
- **Workflow templates** — 24 issue types across 9 packs with enforced state machines
- **Dependency graph** — blockers, ready-queue, critical path analysis
- **Hierarchical planning** — milestone/phase/step hierarchies with automatic unblocking
- **Atomic claiming** — optimistic locking prevents double-work in multi-agent scenarios
- **Pre-computed context** — `context.md` regenerated on every mutation for instant agent orientation
- **Minimal dependencies** — just Python + SQLite + click (no framework overhead)
- **Session resumption** — `get_changes --since <timestamp>` to catch up after downtime

## Quick Start

```bash
pip install filigree        # or: uv add filigree
cd my-project
filigree init               # Create .filigree/ directory
filigree install             # Set up MCP, CLAUDE.md, .gitignore
filigree create "Set up CI pipeline" --type=task --priority=1
filigree ready               # See what's ready to work on
filigree update <id> --status=in_progress
filigree close <id>
```

## Installation

```bash
pip install filigree                     # Core CLI
pip install "filigree[mcp]"              # + MCP server
pip install "filigree[dashboard]"        # + Web dashboard
pip install "filigree[all]"              # Everything
```

Or from source:

```bash
git clone https://github.com/tachyon-beep/filigree.git
cd filigree && uv sync
```

### Entry Points

| Command | Purpose |
|---------|---------|
| `filigree` | CLI interface |
| `filigree-mcp` | MCP server (stdio transport) |
| `filigree-dashboard` | Web UI (port 8377) |

## Why Filigree?

| | Filigree | GitHub Issues | Jira | TODO files |
|-|----------|---------------|------|------------|
| Agent-native (MCP tools) | Yes | No | No | No |
| Works offline / local-first | Yes | No | No | Yes |
| Structured queries & filtering | Yes | Yes | Yes | No |
| Workflow state machines | Yes | Limited | Yes | No |
| Zero configuration | Yes | No | No | Yes |
| Dependency tracking | Yes | Limited | Yes | No |

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | 5-minute tutorial: install, init, first issue |
| [CLI Reference](docs/cli.md) | All CLI commands with full parameter docs |
| [MCP Server Reference](docs/mcp.md) | 43 MCP tools for agent-native interaction |
| [Workflow Templates](docs/workflows.md) | State machines, packs, field schemas, enforcement |
| [Agent Integration](docs/agent-integration.md) | Multi-agent patterns, claiming, session resumption |
| [Python API Reference](docs/api-reference.md) | FiligreeDB, Issue, TemplateRegistry for programmatic use |
| [Architecture](docs/architecture.md) | Source layout, DB schema, design decisions |
| [Examples](docs/examples/) | Runnable scripts: multi-agent, workflows, CLI scripting, planning |

## Priority Scale

See [Workflow Templates — Priority Scale](docs/workflows.md#priority-scale) for the full priority definitions (P0–P4).

## Development

Requires Python 3.11+. Developed on 3.13.

```bash
git clone https://github.com/tachyon-beep/filigree.git
cd filigree
uv sync --group dev

make ci              # ruff check + mypy strict + pytest
make lint            # Ruff check + format check
make format          # Auto-format with ruff
make typecheck       # Mypy strict mode
make test            # Pytest
make test-cov        # Pytest with coverage (fail-under=85%)
```

### Key Conventions

- **Ruff** for linting and formatting (line-length=120)
- **Mypy** in strict mode
- **Pytest** with pytest-asyncio for MCP server tests
- **Coverage** threshold at 85%
- Tests in `tests/`, source in `src/filigree/`

## License

[MIT](LICENSE) — Copyright (c) 2026 John Morrissey
