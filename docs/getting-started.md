# Getting Started

Get up and running with Filigree in 5 minutes.

## Prerequisites

- Python 3.11 or later

## Install

### From PyPI

```bash
pip install filigree
```

### With uv

```bash
uv add filigree
```

### From source

```bash
git clone https://github.com/tachyon-beep/filigree.git
cd filigree
uv sync
```

## Initialize a Project

Navigate to your project root and run:

```bash
cd my-project
filigree init
```

```
Initialized filigree project with prefix 'my-project'
Created .filigree/config.json
Created .filigree/filigree.db
```

This creates a `.filigree/` directory containing:

- `filigree.db` — SQLite database (WAL mode)
- `config.json` — project prefix, enabled packs
- `context.md` — auto-generated project summary

Issue IDs use the format `{prefix}-{6hex}` (e.g., `myproj-a3f9b2`). The prefix defaults to your directory name.

## Set Up Integrations

```bash
filigree install
```

This command:

- Writes `.mcp.json` for Claude Code (MCP server config)
- Injects usage instructions into `CLAUDE.md`
- Adds `.filigree/` entries to `.gitignore`

For specific integrations:

```bash
filigree install --claude-code    # Claude Code only
filigree install --codex          # OpenAI Codex only
```

## Create Your First Issue

```bash
filigree create "Set up CI pipeline" --type=task --priority=1
```

```
Created task myproj-a3f9b2: Set up CI pipeline (P1)
```

## View the Ready Queue

```bash
filigree ready
```

```
P1  myproj-a3f9b2  task  Set up CI pipeline
```

Shows all unblocked issues sorted by priority. This is what agents check first to find work.

## Work on an Issue

```bash
filigree update myproj-a3f9b2 --status=in_progress
```

## Close an Issue

```bash
filigree close myproj-a3f9b2
```

Or with a reason:

```bash
filigree close myproj-a3f9b2 --reason="Implemented in commit abc123"
```

## Optional Extras

### MCP Server

Install the MCP extra for native agent integration:

```bash
pip install "filigree[mcp]"
```

The MCP server exposes 43 tools so agents interact with filigree without parsing CLI output. See [MCP Server Reference](mcp.md).

### Web Dashboard

```bash
pip install "filigree[dashboard]"
filigree dashboard --port=8377
```

### Everything

```bash
pip install "filigree[all]"
```

## Entry Points

| Command | Purpose |
|---------|---------|
| `filigree` | CLI interface |
| `filigree-mcp` | MCP server (stdio transport) |
| `filigree-dashboard` | Web UI (port 8377) |

## What Next?

- [CLI Reference](cli.md) — full command reference with parameter docs
- [MCP Server Reference](mcp.md) — 43 tools for agent-native interaction
- [Workflow Templates](workflows.md) — state machines, packs, and field schemas
- [Agent Integration](agent-integration.md) — multi-agent patterns and session resumption
- [Architecture](architecture.md) — source layout, DB schema, design decisions
