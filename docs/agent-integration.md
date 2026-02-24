# Agent Integration

Filigree is built for AI coding agents. This guide covers how foreground agents, background subagents, and multi-agent teams interact with filigree.

## Foreground Agents

Foreground agents (Claude Code, Codex) use the **MCP server** directly — 53 tools for full read/write access without parsing text.

```bash
filigree install --claude-code    # Set up MCP for Claude Code
filigree install --codex          # Set up MCP for Codex
```

Once installed, agents call MCP tools like `get_ready`, `claim_issue`, and `close_issue` natively. See [MCP Server Reference](mcp.md) for the full tool list.

## Background Subagents

Background subagents use the **CLI with `--json`** for structured output:

```bash
filigree --actor sub-agent-3 claim-next --assignee sub-agent-3 --json
filigree --actor sub-agent-3 close <id> --json
```

The `--json` flag returns machine-readable responses. The `--actor` flag sets the identity in the audit trail so you can track which agent performed each action.

## The Agent Workflow Loop

The recommended pattern for agents working with filigree:

1. **Orient** — read `filigree://context` resource for project state
2. **Find work** — `get_ready` to find unblocked work sorted by priority
3. **Claim** — `claim_issue` or `claim_next` to atomically claim a task
4. **Check transitions** — `get_valid_transitions` before status changes
5. **Work** — do the task, `add_comment` to log progress
6. **Close** — `close_issue` when done (response includes newly-unblocked items)
7. **Repeat** — loop back to step 2

## Session Resumption

When an agent resumes after downtime, it can catch up on what happened:

```bash
filigree changes --since 2026-02-14T10:00:00 --json
```

Via MCP:

```
get_changes(since="2026-02-14T10:00:00")
```

Returns all events since the timestamp — status changes, new issues, closed items, dependency changes. The agent can reconstruct what happened while it was offline and adjust its plan accordingly.

## Multi-Agent Coordination

### Atomic Claiming

When multiple agents are active, **claiming prevents double-work**. The `claim_issue` and `claim_next` tools use optimistic locking — if another agent already claimed the issue, the operation fails immediately rather than silently overwriting.

```bash
# Agent 1 claims successfully
filigree --actor agent-1 claim-next --assignee agent-1
# Returns: {"id": "proj-a3f9b2", "title": "Fix auth bug", ...}

# Agent 2 tries the same issue — fails
filigree --actor agent-2 claim proj-a3f9b2 --assignee agent-2
# Returns: error — already claimed by agent-1
```

Via MCP:

```
claim_next(assignee="agent-1")     # Claim highest-priority ready issue
claim_issue(id="...", assignee="agent-2")  # Claim specific issue
release_claim(id="...")            # Release back to open
```

### Filtering by Type or Priority

Agents can specialize by claiming only certain types or priority ranges:

```bash
filigree --actor bug-fixer claim-next --assignee bug-fixer --type=bug
filigree --actor critical-agent claim-next --assignee critical-agent --priority-max=1
```

## Audit Trail

Every mutation records an **actor**. The `--actor` flag (CLI) or `actor` parameter (MCP) sets who performed the action:

```bash
filigree --actor agent-alpha create "Fix auth"
filigree --actor agent-beta close proj-a3f9b2
```

Via MCP, every write tool accepts an `actor` parameter:

```
create_issue(title="Fix auth", actor="agent-alpha")
close_issue(id="proj-a3f9b2", actor="agent-beta")
```

Event history is queryable per-issue or globally:

```bash
filigree events <id>                        # Per-issue history
filigree changes --since 2026-02-14T10:00   # Global event stream
```

## Pre-Computed Context

Filigree generates a `context.md` file on every mutation, stored at `.filigree/context.md`. This file contains:

- Project vitals (prefix, enabled packs, issue counts)
- Ready work queue (unblocked, sorted by priority)
- Blocked issues with their blocker details
- Recent activity

Agents read this via the `filigree://context` MCP resource or `get_summary` tool at session start. Because it's pre-computed, there's no query overhead — the agent gets instant orientation.

## Example: Multi-Agent Setup

A typical multi-agent setup with filigree:

```
Team Lead (foreground, MCP)
├── Reads context.md at session start
├── Creates and prioritizes issues
├── Monitors progress via get_stats
│
Worker Agent 1 (background, CLI --json)
├── claim-next --assignee worker-1 --type=task
├── Works on claimed issue
├── close when done, claim-next again
│
Worker Agent 2 (background, CLI --json)
├── claim-next --assignee worker-2 --type=bug
├── Specializes in bug fixes
├── Uses add-comment to log investigation progress
```

Each agent uses a distinct `--actor` identity, claims work atomically, and the event stream provides full visibility into who did what and when.
