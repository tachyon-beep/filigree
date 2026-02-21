---
name: filigree-workflow
description: >
  This skill should be used when the user asks to "track work", "create an issue",
  "find something to work on", "what should I work on next", "triage bugs", "close
  an issue", "check what's blocked", "plan a milestone", "review sprint progress",
  "coordinate agents", or when working in a project that uses filigree for issue
  tracking. Provides workflow patterns, team coordination protocols, and operational
  guidance for the filigree issue tracker.
---

# Filigree Workflow

Filigree is an agent-native issue tracker that stores data locally in `.filigree/`.
This skill provides procedural knowledge for using filigree effectively — as a solo
agent or in a multi-agent swarm.

## Core Workflow

Every task follows this lifecycle:

```
filigree ready          → find available work (no blockers)
filigree show <id>      → read requirements and context
filigree transitions <id> → check valid state changes
filigree update <id> --status=in_progress  → claim the work
[do the work, commit code]
filigree close <id> --reason="summary of what was done"
```

Always close with a `--reason` — it becomes audit trail for the next agent.

## Priority Semantics

| Priority | Meaning | Action |
|----------|---------|--------|
| P0 | Critical | Drop everything. Production is broken. |
| P1 | High | Do next. Current sprint must-have. |
| P2 | Medium | Default. Normal backlog work. |
| P3 | Low | Nice to have. Do when P1/P2 are clear. |
| P4 | Backlog | Someday. Don't schedule unless promoted. |

When triaging, use `filigree batch-update <ids...> --priority=N` for bulk changes.

## Claiming Work

### Solo Agent

Use `filigree update <id> --status=in_progress` to signal active work.

### Multi-Agent Swarm

Use atomic claiming to prevent races:

```bash
filigree claim <id> --assignee <agent-name>     # specific issue
filigree claim-next --assignee <agent-name>      # highest-priority ready
```

Claiming sets the assignee atomically — if two agents race, only one wins.
After claiming, advance state with `update --status=in_progress`.

## Key Commands

### Finding Work

```bash
filigree ready                    # ready issues sorted by priority
filigree list --status=open       # all open issues
filigree search "auth"            # full-text search
filigree critical-path            # longest dependency chain
```

### Creating Issues

```bash
filigree create "Title" --type=bug --priority=1
filigree create "Title" --type=task -d "description" --dep <blocker-id>
filigree create-plan --file plan.json   # milestone/phase/step hierarchy
```

### Managing Dependencies

```bash
filigree add-dep <issue> <depends-on>     # A depends on B
filigree remove-dep <issue> <depends-on>
filigree blocked                          # show all blocked issues
```

### Context and Handoff

```bash
filigree add-comment <id> "what I found / what's left to do"
filigree get-comments <id>                # read previous context
filigree show <id>                        # full details including deps
```

Always add a comment before closing or handing off — the next agent has no memory
of the current conversation.

## Workflow Patterns

### Before Starting Work

1. Run `filigree ready` to see available work
2. Check `filigree critical-path` — unblocking the critical path has highest leverage
3. Pick work that matches the current session's context (e.g., if code is already open)

### When Finishing Work

1. Add a comment summarising what was done and any follow-up needed
2. Close with a reason: `filigree close <id> --reason="implemented X, tested Y"`
3. Check if closing this issue unblocks anything: `filigree ready`

### When Blocked

1. Add a comment explaining the blocker
2. Create the blocking issue if it doesn't exist
3. Add the dependency: `filigree add-dep <blocked> <blocker>`
4. Move to other available work

## Guidance Sheets

For detailed patterns, consult these reference files:

- **`references/workflow-patterns.md`** — Triage flows, sprint planning,
  dependency management, bug lifecycle patterns
- **`references/team-coordination.md`** — Multi-agent swarm protocols,
  handoff conventions, claiming strategies, status update patterns
- **`examples/sprint-plan.json`** — Complete create-plan input template
  with cross-phase dependencies

Load these when facing a specific workflow challenge rather than reading upfront.

## File Records & Scan Findings

The dashboard API tracks files and scan findings across the project. Use the
schema discovery endpoint to find valid values and available endpoints:

```
GET /api/files/_schema
```

This returns valid severities, finding statuses, association types, sort fields,
and a full endpoint catalog. When linking issues to files, use file associations:

| Association Type | Meaning |
|-----------------|---------|
| `bug_in` | Bug reported in this file |
| `task_for` | Task related to this file |
| `scan_finding` | Automated scan finding |
| `mentioned_in` | File referenced in issue |

## Health and Diagnostics

```bash
filigree doctor           # check installation health
filigree stats            # project-wide counts
filigree metrics          # cycle time, lead time, throughput
filigree events <id>      # audit trail for a specific issue
```

## Quick Decision Guide

| Situation | Action |
|-----------|--------|
| "What should I work on?" | `filigree ready`, pick highest priority |
| "Is this blocked?" | `filigree show <id>`, check blocked_by |
| "Multiple agents need work" | `filigree claim-next --assignee <name>` |
| "I found a new bug" | `filigree create "..." --type=bug --priority=1` |
| "This task is bigger than expected" | Create sub-tasks, add deps |
| "I'm done" | Comment, close with reason, check `ready` |
| "Something changed while I worked" | `filigree changes --since <timestamp>` |
