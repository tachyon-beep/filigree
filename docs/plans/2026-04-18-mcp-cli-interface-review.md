# MCP vs CLI Interface: First-Principles Review

**Date:** 2026-04-18
**Author:** Claude (opus 4.7) with John Morrissey
**Branch:** `2.0-project-management-extension`
**Status:** Draft — not yet triaged into issues

## Context

Agents (both me and others) have been observed struggling with the filigree
MCP and CLI surfaces. This document reviews both from first principles: not
"is feature X missing" but "does the shape of the API match what an agent
actually tries to do with it?"

Sources read:

- `src/filigree/mcp_tools/*.py` — tool definitions and handlers
- `src/filigree/cli_commands/*.py` — click command definitions
- `src/filigree/types/inputs.py` — TypedDict contracts
- `docs/cli.md`, `docs/mcp.md`
- `CLAUDE.md` instructions (root and project)

## The lens

An agent wants five things from filigree:

1. What can I work on?
2. What's the state of X?
3. What can I do with X?
4. Record progress.
5. Close it.

Both surfaces cover all five. **The pain is not missing capability — it's
surface friction**: names differ, parameter shapes differ, semantics split
across tool boundaries.

Reading the two surfaces side by side makes one thing clear: they were
designed in isolation under their own idiomatic pressures. MCP follows
JSON-RPC/gRPC naming (`get_ready`, `list_issues`); CLI follows Unix (`git
status`, not `git get-status`). Both conventions are correct locally; the
agent straddles both and the "native idiom" becomes globally wrong.

---

## Pain point 1 — same concept, multiple names

### "The issue ID" has four names across MCP tools alone

- `id` — `get_issue`, `update_issue`, `close_issue`, `reopen_issue`,
  `claim_issue`, `release_claim`, `undo_last`
  (`src/filigree/mcp_tools/issues.py:65,171,194,208,246,262`)
- `issue_id` — `add_comment`, `get_comments`, `add_label`, `remove_label`,
  `get_issue_events`, `get_valid_transitions`, `validate_issue`,
  `get_issue_files`
  (`src/filigree/mcp_tools/meta.py:53,65,76,88`,
  `src/filigree/mcp_tools/workflow.py:87,98`)
- `milestone_id` — `get_plan` (`src/filigree/mcp_tools/planning.py:67`)
- `from_id` / `to_id` — dependency tools
  (`src/filigree/mcp_tools/planning.py:32-44`)

An agent has to keep a mental map of which tool uses which name.

### Verb/noun asymmetry between surfaces

| CLI | MCP |
|---|---|
| `ready`, `blocked`, `stats`, `labels`, `taxonomy` | `get_ready`, `get_blocked`, `get_stats`, `list_labels`, `get_label_taxonomy` |
| `plan`, `transitions`, `events`, `changes` | `get_plan`, `get_valid_transitions`, `get_issue_events`, `get_changes` |
| `add-dep`, `remove-dep`, `release`, `critical-path` | `add_dependency`, `remove_dependency`, `release_claim`, `get_critical_path` |
| `--parent` | `parent_id` |

CLAUDE.md tells agents to "fall back to CLI when MCP is unavailable" —
which is exactly the moment the vocabulary swap happens.

---

## Pain point 2 — `claim` is half a workflow

From `src/filigree/mcp_tools/issues.py:240`:

> "Atomically claim an open issue … Does NOT change status — use
> update_issue to advance through workflow after claiming."

But `update_issue --status=in_progress` doesn't check assignee. Two agents
can both transition to `in_progress`; only `claim_issue` has the optimistic
lock. Meanwhile the root CLAUDE.md says:

> 4. `filigree update <id> --status=in_progress` to claim it

which quietly conflates the two. `claim` (atomic CAS on assignee) and
`update --status=in_progress` (not atomic for contention) are **entirely
different operations**, but both docs and workflow use "claim" for both.

The recent `release_claim()` TOCTOU fix (commit `326fd9d`) is a symptom of
this area being genuinely contested design.

**First-principles fix:** add `start_work(id, assignee)` that composes
claim + transition atomically. Keep `claim_issue` as the atomic primitive
it is, but stop asking agents to orchestrate the composition.

---

## Pain point 3 — "state" vs "status" vs "status_category"

The DB column is `status`. But the API mixes three names:

- `get_workflow_states` (not `_statuses`), `explain_state(type, state)` —
  `src/filigree/mcp_tools/workflow.py:56,115`
- CLI `workflow-states`, `explain-state` —
  `src/filigree/cli_commands/workflow.py:47,297`
- `TransitionDetail.category` = status category —
  `src/filigree/mcp_tools/issues.py:376`
- `list_issues(status_category=...)` is an enum, but `status` is a free
  string — `src/filigree/mcp_tools/issues.py:86-93`

**Fix:** pick `status` everywhere user-facing; reserve `category` for the
three-valued roll-up (`open|wip|done`); retire "state" as a user-facing
word (it belongs in implementation, not API).

---

## Pain point 4 — response shapes don't match

Same operation, two shapes:

```text
# CLI close (batch):  {"closed": [full dicts], "errors": [...], "unblocked": [...]}
# MCP batch_close:    {"succeeded": [ids], "failed": [...], "count": N, "newly_unblocked": [...]}
```

Four differences between ostensibly the same call:

- `closed` vs `succeeded`
- Full issue dicts vs ID-only
- `errors` vs `failed`
- `unblocked` vs `newly_unblocked`

`batch_update`, `batch_add_label`, `close_issue`, `reopen_issue` all add
further drift. `batch_add_label` even returns **both** `succeeded` and
`results` (`src/filigree/mcp_tools/meta.py:399-406`).

### Error codes proliferate too

Fifteen distinct codes across MCP tools, several overlapping:

```
invalid, validation_error, invalid_transition, conflict, not_found,
invalid_api_url, command_not_found, stop_failed, permission_error,
not_initialized, scanner_not_found, io_error, db_error, invalid_path,
unknown_tool
```

`invalid` and `validation_error` mean the same thing in different files.

**Fix:** one batch-response envelope in `src/filigree/types/api.py`, one
closed enum of error codes, applied uniformly on both surfaces.

---

## Pain point 5 — CLI has capability holes

Three MCP domains have **no** CLI equivalent:

- **Observations** — `observe`, `list_observations`, `dismiss_observation`,
  `promote_observation`, `batch_dismiss_observations`. No `filigree observe`
  command exists.
- **Files & findings** — `list_files`, `get_file`, `list_findings`,
  `promote_finding`, `dismiss_finding`, `update_finding`,
  `batch_update_findings`, `get_finding`.
- **Scanners** — `trigger_scan`, `trigger_scan_batch`, `get_scan_status`,
  `preview_scan`, `report_finding`, `list_scanners`.

CLAUDE.md explicitly names CLI as the fallback, but for these domains
there **is** no fallback. A bash-hook-invoked script can't record an
observation or list findings without the MCP server up. Given
`src/filigree/hooks.py` already shells out to `filigree …`, this is a real
gap, not a theoretical one.

---

## Smaller friction worth noting

- **`get_issue` defaults `include_files=True`** (`issues.py:72`). Fine for
  humans, wasteful when an agent iterates.
- **`_MAX_LIST_RESULTS = 50` silently caps** with `has_more`
  (`common.py:39`). Easy to miss.
- **`create_plan`'s 3-level nested schema** with step deps toggling between
  `int` and `"p.s"` strings (`planning.py:107-112`) is a lot to construct
  correctly on first try — hence the 40-line validator at
  `planning.py:229-266` catching common mistakes.
- **`batch-add-label <label> <ids...>`** (`meta.py:316`) flips argument
  order vs `add-label <id> <label>` (`meta.py:68`). Same verb root,
  opposite positional order.
- **Schema version mismatch** at session start — installed `filigree` (v8
  via uv tool) against a local DB upgraded to v9. `filigree doctor` should
  detect this and suggest `uv tool install --upgrade filigree`, but
  currently doesn't route there.

---

## Insights

- **Verb-noun vs noun-only is a symptom of per-surface design.** MCP
  followed JSON-RPC idioms, CLI followed Unix idioms. Each is correct
  locally; the agent straddles both.
- **`claim_issue` is the right primitive, just uncomposed.** Atomic CAS on
  assignee is exactly what you want. The bug is at the orchestration layer
  — there's no `start_work` composing claim + transition. Good primitives
  + missing orchestration is a common API-maturity pattern; the fix is
  usually adding the composition, not weakening the primitive.
- **Shape drift is harder to detect than name drift.** Wrong parameter
  name throws a schema error the agent sees. But a shape mismatch between
  batch tools gives _plausible wrong behavior_ — an agent treats `failed:
  []` as success, doesn't notice a missing `unblocked` key. Silent misuse
  is the more dangerous failure mode.

---

## Ranked recommendations

Ordered by leverage (highest impact first):

1. **Unify `issue_id`** everywhere; retire bare `id`. One-time breaking
   change.
2. **Add `start_work(id, assignee)`** that composes claim + transition.
   Keep `claim_issue` as the atomic primitive. Stop asking agents to
   orchestrate.
3. **Normalize `status` vocabulary** — eliminate user-facing "state".
4. **Single batch-response envelope + closed error-code enum** in
   `src/filigree/types/api.py`, used on both surfaces.
5. **CLI parity** for observations, findings, files, scanners — at
   minimum, read/list operations so hook scripts work. Alias MCP verb-noun
   names as CLI forms (`filigree get-ready` alongside `filigree ready`).
6. **Compact-by-default `get_issue`** (no files), opt in via
   `include_files=true`.
7. **Fix schema-version mismatch UX** in `doctor` and dashboard startup.

The highest-leverage single change is probably **#4 (response envelope +
error codes)** — it's the friction that causes agents to silently
mishandle failures rather than fail loudly on names.

---

## Next steps (not yet decided)

- Triage the seven recommendations into filigree issues, grouped by
  whether they're breaking changes or additive.
- Decide migration strategy for breaking renames (recommendation #1) —
  supporting both `id` and `issue_id` during a deprecation window vs
  cutover.
- Verify the capability-hole claim (recommendation #5) against hooks that
  actually run today.
