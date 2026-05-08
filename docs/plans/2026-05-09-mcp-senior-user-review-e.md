# Filigree MCP Senior-User Friction Review (run e)

**Date:** 2026-05-09
**Reviewer:** Claude Opus, acting as senior-user agent (`actor=mcp-review-e`)
**Branch:** `2.0-project-management-extension`
**MCP schema:** v11 vs DB v12 → `get_mcp_status: schema_mismatch`. Despite that, every read/write I issued succeeded — see Finding 5.
**Method:** Live driving of `mcp__filigree__*` against this repo's `.filigree/`. Scratch issues labelled `cluster:mcp-review-e` / `mcp-review-scratch`; cleanup via `batch_close` + `archive_closed(label=cluster:mcp-review-e, days_old=0)`. Workflows A–F driven end-to-end. A parallel reviewer was running run `-d` against the same DB; collisions were avoided via cluster labels.

---

## 1. Executive summary

The MCP surface gets the headline workflows right — `start_next_work`, `create_plan`, `annotate_file`, the `INVALID_TRANSITION` envelope with `valid_transitions`/`hint`, and `report_finding`'s flat ScanFinding response are all genuinely agent-shaped. Friction is concentrated in three places: **(a)** the documented `SCHEMA_MISMATCH` gate doesn't actually gate writes (writes I issued succeeded under v11/v12 mismatch, contradicting the contract in `CLAUDE.md`); **(b)** workflow enforcement is asymmetric across tools — `update_issue` blocks `triage→closed`, while both `close_issue` *and* `batch_close` skip every intermediate state without `force=true`, and write tools (`update_issue`, `add_comment`, `add_label`) ignore claim ownership entirely so I renamed an issue held by another actor with no `CONFLICT`; **(c)** the finding↔observation parallel queue is still leaking — `report_finding` populates `observation_ids` in its response but the resulting observation row has `source_finding_id: ""` (the schema column was added but never populated), and `dismiss_finding` does not clean up the orphan.

---

## 2. Per-workflow walkthrough

### Workflow A — Cold start

**Tool calls:** `get_mcp_status` → `session_context` → `get_summary` → `get_ready(include_context=true)` → `get_stats` → `get_critical_path` → `list_types` → `get_schema` → `get_template task` → `get_template bug` → `create_issue` (×4 scratch) → `start_work` → `claim_issue` (as `other-actor-y`) → `start_next_work(priority_min=3, priority_max=3, type=task)`.

**What worked:**
- `get_ready(include_context=true)` returned `parent_issue_id` + `parent_title` (clean orientation).
- `get_summary` epic progress bar `[████░░░░] 3/6` is glanceable.
- `start_next_work(priority_min=3, priority_max=3)` correctly skipped the issue that `other-actor-y` had just claimed, and grabbed the next P3 unclaimed task — claim + transition + 48h lease in one call.
- `start_work` happy path returned the full transitioned issue with `claim_expires_at: 2026-05-10T19:52:28+00:00` (T+48h).
- `get_critical_path` empty case returned `{ path: [], length: 0, note: "no open dependency chains" }`. The `note` field is exactly the diagnostic an agent wants.

**What hurt:**
- **`get_mcp_status` reports `schema_mismatch` but *no* tool was actually gated.** I created issues, transitioned them, added labels, opened a plan tree, dismissed observations — all under `installed_schema_version: 11, database_schema_version: 12, schema_compatible: false`. CLAUDE.md says "most tool calls return an `ErrorResponse` with `code: SCHEMA_MISMATCH`." That is empirically false.
- **`get_schema.entity_id_prefixes` is stale.** The `issue.accepted_by_tools` list omits `batch_update`, `batch_close` (both accept issue IDs and worked fine in this run). The `observation.accepted_by_tools` list omits `batch_promote_observations` — already captured as `filigree-obs-e7b379ff6e` four days ago and still unfixed. An agent that uses `get_schema` to build its tool dispatch table will be missing batch operations.
- **Naming inconsistency: `parent_id` vs `parent_issue_id`.** `get_issue` returns `parent_id`. `get_ready` and `list_issues` return `parent_issue_id`. Same field, two names.
- **`get_valid_transitions` envelope diverges.** It returns a bare JSON array (`[]` for a closed issue), not `{items, has_more}` like every other list-shaped tool, not `{transitions: [...]}` like the embedded form on `INVALID_TRANSITION` errors. Three different shapes for the same data.

### Workflow B — Triage and grooming (search, labels, observations)

**Tool calls:** `search_issues` (×4 with different tokenisations) → `list_issues(label=cluster:mcp-review-e)` → `list_observations` → `get_label_taxonomy` → `add_label P0` → `add_label priority:high` → `add_label priority:0` (on the parallel review's release) → `add_label review:needed` → `add_label review:done` → `observe` → `promote_observation` → `batch_dismiss_observations`.

**What worked:**
- Reserved-label rejection is excellent: `P0`, `priority:high`, `priority:0` all rejected with the *same* error string pointing the agent at the priority field. No drift across the three reserved patterns.
- `get_label_taxonomy` partitioning (auto / virtual / manual_suggested / bare_labels.reserved) is the best self-describing surface in the API.
- `batch_dismiss_observations` mixed valid + bogus IDs: `succeeded: ["filigree-obs-9c65983d67"], failed: [{id, error: "Observation not found: ...", code: "NOT_FOUND"}]`. Clean partial-success.
- `promote_observation` returned a flat `PublicIssue` with a populated `fields.source_observation_id` — the linkage is preserved.

**What hurt:**
- **`search_issues` chokes on hyphens AND bracket-prefixed tokens.**
  - `query: "mcp-review-e"` → `items: []`
  - `query: "[mcp-review-e]"` → `items: []`
  - `query: "Scratch task"` → 6 hits including `"[mcp-review-e] Scratch task A — start_work probe"`
  - `query: "HIJACKED"` → 1 hit (single full word works)
  Agents prefix their work with `[mcp-review-X]` to find it later — and then can't. The FTS tokenisation isn't documented in the tool description.
- **`search_issues` returns `archived` issues alongside `closed`/`open`/`in_progress` with no filter.** My `Scratch task` query returned my live issues plus several `status: "archived"` results from runs c/d. There's no `status` / `category` filter on `search_issues`, so an agent searching for live work has to post-filter the response.
- **`promote_observation` discards cluster labels.** I observed with no labels (`observe` doesn't accept any) but the resulting issue inherited only `["from-observation"]` — none of the agent's session-cluster context. I had to manually re-add `cluster:mcp-review-e`. Sibling `promote_finding` has the same shape gap. An agent's session-tagging discipline silently breaks at the promote boundary.
- **Mutually-exclusive label displacement is silent.** Adding `review:done` after `review:needed` left `labels: [..., "review:done"]` with `label_result: "added"`. No `replaced_label`, no `data_warnings` entry. The `get_label_taxonomy` declares `mutually_exclusive: true` for the `review:` namespace, so the displacement is intentional, but the response gives the agent zero signal that anything was removed.
- **`list_observations` has only `file_id` / `file_path` filters.** I had to scan summary text by hand to separate review-c, review-d, review-e residue from real findings. Already on the roadmap as `filigree-b0af8a661b`.

### Workflow C — Multi-agent coordination (claims, leases, conflicts)

**Tool calls:** `start_work` (mine on A) → `claim_issue` (other-actor-y on B) → `start_work` (mine on B, expect CONFLICT) → `claim_issue` (mine on B, expect CONFLICT) → `heartbeat_work` (no actor) → `heartbeat_work` (explicit actor) → `add_comment` (mine on B held by other) → `update_issue` (rename B held by other) → `release_claim` on D → `get_stale_claims` → `add_dependency` (self) → `add_dependency` (cycle).

**What worked:**
- `start_work` and `claim_issue` on a held issue both returned identical CONFLICTs naming the holder: `"Cannot claim filigree-34c118fea0: already assigned to 'other-actor-y'"`. Symmetric.
- `heartbeat_work` with explicit `actor` and `lease_hours=2` shifted `claim_expires_at` from +48h to +2h cleanly.
- Self-dep: `"Cannot add self-dependency: filigree-ec07bee5e9"` (VALIDATION). Cycle: `"Dependency filigree-7e851a7f96 -> filigree-ec07bee5e9 would create a cycle"` (VALIDATION). Both actionable.

**What hurt:**
- **Write-path enforcement asymmetry — major.** With B held by `other-actor-y`, I (acting as `mcp-review-e`) successfully ran `add_comment(B, ...)` AND `update_issue(B, title="HIJACKED ...")`. No CONFLICT, no warning, no `expected_assignee` parameter exposed.
  ```json
  // B is held by other-actor-y
  add_comment(B, "...", actor="mcp-review-e")
  → assignee: "other-actor-y", comment_id: 97   // succeeded
  update_issue(B, title="...HIJACKED...", actor="mcp-review-e")
  → assignee: "other-actor-y", changed_fields: ["title"]   // succeeded
  ```
  But the same agent failed its own heartbeat without an explicit `actor`:
  ```json
  heartbeat_work(A)
  → "error": "Cannot heartbeat filigree-ec07bee5e9: assigned to 'mcp-review-e' (expected 'mcp')"
  ```
  Heartbeat/release/reclaim strictly enforce ownership; `update_issue`/`add_comment`/`add_label`/`close_issue`/`batch_update`/`batch_close` ignore it. Multi-agent guarantees built on `claim_issue + heartbeat` are silently overwriteable from the sibling tools.
- **`heartbeat_work` default-actor is the literal string `'mcp'`, not the assignee.** The docstring says "By default actor is treated as the expected current holder." That reads as "the assignee," but the implementation infers `'mcp'`. An agent that forgets to pass `actor` to a heartbeat will let its own lease expire silently.
- **`release_claim` on a wip-category issue strands it.** I had D in `in_progress` with assignee `mcp-review-e`; `release_claim(D)` cleared assignee but left status unchanged:
  ```json
  release_claim(filigree-7e851a7f96, actor="mcp-review-e")
  → status: "in_progress", assignee: "", is_ready: false
  ```
  The `task` template has no `in_progress → open` transition. The issue is now invisible to `get_ready` (wip), invisible to `get_blocked` (also excludes wip — see below), invisible to `get_summary` (no in-progress slot for unassigned wip). The only path back is `claim_issue` by an agent who already knows the issue ID. There's no "needs new owner" surface.
- **`get_blocked` returned `[]` while a wip issue had `blocked_by: [other_id]`.** I'd added `add_dependency(from=A, to=D)` while A was `in_progress`. `get_issue(A)` showed `blocked_by: ["filigree-7e851a7f96"]`, `is_ready: false` — clearly blocked. `get_blocked()` ignored it: `items: []`. An agent asking "what's stuck right now" misses every wip dead-end.
- **`get_stale_claims()` ignores `lease_hours`.** I had two active claims with 48h leases and one heartbeat shortened to 2h. None were stale by definition. Empty result is correct, but the absence of any "near-expiry" mode means an agent has no way to ask "what's about to expire so I can pre-emptively heartbeat?"

### Workflow D — Scans, findings, files, annotations

**Tool calls:** `list_scanners` → `list_findings(status=false_positive, limit=3)` → `report_finding` → `list_observations(file_path=...)` → `dismiss_finding` → `list_observations` (post-dismiss) → `preview_scan` → `annotate_file(intent=breadcrumb)` → `list_attention_annotations` → `resolve_annotation`.

**What worked:**
- `annotate_file` provenance is the gold standard of the surface: `commit_ref`, `branch`, `file_checksum`, `file_size`, `file_mtime`, `anchor_match_confidence: 1.0`, `worktree_diff_summary: "?? docs/bugs/"`, `provenance_flags: ["dirty_worktree"]`, `provenance_trust_level: "complete"`, plus an `events: [...]` audit trail. Every field an agent needs to defend its handoff trace.
- `list_scanners` returns risk metadata: `safe_preview_only: true`, `requires_approval: true`, `may_send_contents: true`, `risk_summary` string. Cautious-by-default.
- `preview_scan` returned the full command string, `valid: true`, and the same risk metadata before any process spawn — exactly the right shape for `requires_approval=true` workflows.
- `dismiss_finding` recorded `metadata.dismiss_reason: "mcp-review-e cleanup"` for audit.
- `resolve_annotation` returned the full annotation with `events: [{event_type: "created"}, {event_type: "resolved", reason: "mcp-review-e cleanup"}]` — clean event record.

**What hurt:**
- **`report_finding` still spawns an unlinked observation.** The response shape *appears* fixed:
  ```json
  report_finding({...}) → {
    finding_id: "filigree-sf-20d3b3d2e1",
    observations_created: 1,
    observation_ids: ["filigree-obs-9c65983d67"]
  }
  ```
  But the observation row itself has the linkage *erased*:
  ```json
  list_observations(file_path=...) → [{
    observation_id: "filigree-obs-9c65983d67",
    source_finding_id: "",       // empty — schema column exists but is never populated
    source_issue_id: "",
    actor: "scanner:agent",
    summary: "[agent] src/.../issues.py:1 -- Synthetic finding..."
  }]
  ```
  The `source_finding_id` field is in the schema (review-d's report assumed it didn't exist; it does — it's just always empty). Then:
  ```json
  dismiss_finding("filigree-sf-20d3b3d2e1") → status: "false_positive"
  list_observations(file_path=...) → [{ observation_id: "filigree-obs-9c65983d67", ... }]   // STILL THERE
  ```
  Every closed/dismissed/promoted finding leaves a 14-day zombie observation. The fix `filigree-42e0aa3c89` looks half-done: the response and column were added but the populate + cleanup paths weren't wired.
- **`dismiss_finding` only writes `false_positive`.** `update_finding`'s status enum has `acknowledged | false_positive | fixed | unseen_in_latest | open`. There's no path through `dismiss_finding` to record "won't fix in scope" or "duplicate." The natural verb forces the wrong status name.
- **`list_findings(status=false_positive)` returns dismissed findings forever.** My run-e finding from this session, plus run-d's finding from earlier today, plus run-c's `mcp-review-c-scratch` finding from 2026-05-07. There's no "archive findings" tool and no `dismissed_at` filter — the false-positive list grows monotonically. Compare with issues, which have `archive_closed(days_old=N)`.

### Workflow E — Planning (`create_plan`, `add_plan_step`, `label_plan_tree`)

**Tool calls:** `create_plan` (1 milestone × 2 phases × 2/1 steps with `[0]` same-phase + `"0.1"` cross-phase deps, with cluster labels on the milestone) → `create_plan` (with `deps: [99]` to probe error) → `get_plan` (implicit via `list_issues` post-cleanup).

**What worked:**
- `create_plan` did all of: 1 milestone + 2 phases + 3 steps + 2 dependency edges + label propagation `cluster:mcp-review-e, mcp-review-scratch` cascaded to all 6 descendants — single call. The response is fully populated: every step's `blocks`/`blocked_by` is correct, plus per-phase `{total, completed, ready}` aggregates.
- Bad-plan validation: `deps: [99]` returned `"Dep index out of range: step 99 in phase 0 (max=0)"` — the maximum index is named, which makes the fix obvious.

**What hurt:**
- **`create_plan` dep notation mixes `int` and `"p.s"` strings in the same array.** Code generators have to switch types based on whether the dep is same-phase or cross-phase. `add_plan_step` accepts full issue IDs in `deps`, which is cleaner. The two tools should have parity.
- **The `create_plan` response has issue IDs *embedded* in nested objects (`milestone.issue_id`, `phases[i].phase.issue_id`, `phases[i].steps[j].issue_id`).** Most other create-shaped tools put `issue_id` at the top of the object. An agent threading IDs from the response to `add_dependency` or `start_work` calls has to walk the tree.

### Workflow F — Recovery and edge cases

**Tool calls:** `update_issue(triage→closed)` → `close_issue(triage→closed)` → `reopen_issue` → `validate_issue` → `undo_last` (×1, on the hijack title) → `add_dependency(self)` → `add_dependency(cycle)` → `get_issue(bogus)` → `get_finding(bogus)` → `create_issue(type=no-such-type)` → `batch_close` (mixed valid + bogus, 11 IDs) → `archive_closed(days_old=0, label=cluster:mcp-review-e)`.

**What worked:**
- Error envelopes are consistent across NOT_FOUND / VALIDATION / INVALID_TRANSITION / CONFLICT, all with `code` strings matching the documented `ErrorCode` enum.
- `INVALID_TRANSITION` envelope is the best diagnostic in the surface:
  ```json
  update_issue(filigree-47c69fba21, status="closed") → {
    error: "Transition 'triage' -> 'closed' is not allowed for type 'bug'. Use get_valid_transitions() to see allowed transitions.",
    code: "INVALID_TRANSITION",
    valid_transitions: [{to: "confirmed", ...}, {to: "wont_fix", ...}, {to: "not_a_bug", ...}],
    hint: "Use get_valid_transitions to see allowed state changes"
  }
  ```
- `create_issue(type="no-such-type")` lists every valid type in the error string. `validate_issue` separates `valid: true, warnings: ["Transition to 'confirmed' requires: severity"], errors: []` cleanly.
- `undo_last` reverted the title hijack on B with `undone: true, event_type: "title_changed", event_id: 2493`. The title-change history was cleanly walked back.
- `batch_close` mixed 10 valid + 1 bogus: `succeeded: [10 SlimIssues], failed: [{id: "filigree-bogusbogus", error, code: "NOT_FOUND"}]`. Partial success works.
- `archive_closed(days_old=0, label="cluster:mcp-review-e")` swept exactly my 10 closed issues — `archived_count: 10, archived_ids: [...]`.

**What hurt:**
- **`close_issue` AND `batch_close` both bypass workflow enforcement that `update_issue` enforces.** Same target state, opposite contract.
  ```json
  // bug filigree-47c69fba21 in status "triage"
  update_issue(..., status="closed") → INVALID_TRANSITION (correct: triage→closed not allowed)
  close_issue(...)                   → status: "closed"   // bypassed
  ```
  And worse, `batch_close` smashed the bug *and* a milestone in `planning` straight to terminal:
  ```json
  // milestone filigree-1cb117bbb8 in status "planning"
  // milestone template: planning → active → closing → completed
  batch_close([..., milestone_id, ...]) → succeeded: [{milestone_id, status: "completed", ...}]   // skipped 2 statuses
  ```
  Review-d caught `close_issue`. `batch_close` has the same hole and is even more dangerous because it operates in bulk.
- **Archived issues' record output is misleading.** After `archive_closed`, `list_issues(label=cluster:mcp-review-e)` returned my 10 issues with `status: "archived"` but `status_category: "open"` and (for 8 of 10) `is_ready: true`. The query-time filters in `get_ready` correctly exclude them — `get_ready()` returned the project's 7 real ready items, none of mine. But every record an agent fetches via `list_issues` reports the wrong category and a misleading `is_ready` flag. There's already an open observation on this (`filigree-obs-d6cc014192`), but flagging again because it surfaces in record-level output, not just query plans.
- **Pagination shape is inconsistent across the surface.** `list_issues` / `list_findings` / `list_observations` / `list_files` use `{items, has_more, next_offset?}`. `get_changes` uses `{items, has_more, next_since: "<timestamp>"}`. `get_valid_transitions` uses a bare `[]`. `get_critical_path` uses `{path, length, note}`. Four shapes for "list-shaped" responses.
- **`undo_last` response buries the issue identity at the bottom.** Top-level keys are `title, status, ..., data_warnings, issue_id, undone, event_type, event_id`. Every other write tool puts `issue_id` first. Trivial papercut, but consistent ordering matters for agent parsers that key on the leading field.

---

## 3. Findings

### P1 — degrades real workflows

1. **P1 — `report_finding` spawns an unlinked, never-cleaned observation.** The `source_finding_id` column exists on observation rows but is *never populated*; `dismiss_finding` and `promote_finding` do not delete or otherwise touch the parallel observation. **Evidence:**
   ```json
   report_finding(...) → { finding_id: "filigree-sf-20d3b3d2e1", observations_created: 1, observation_ids: ["filigree-obs-9c65983d67"] }
   list_observations(...) → [{ observation_id: "filigree-obs-9c65983d67", source_finding_id: "", ... }]
   dismiss_finding("filigree-sf-20d3b3d2e1") → status: "false_positive"
   list_observations(...) → [{ observation_id: "filigree-obs-9c65983d67", source_finding_id: "", ... }]   // STILL PRESENT
   ```
   **Why it matters:** Every agent finding becomes 14 days of triage debt. The observation queue accumulates duplicates of work already triaged via the finding queue. The bug `filigree-42e0aa3c89` was supposed to fix this and the response/schema look right, but the populate + cleanup paths are not implemented. **Resolution:** populate `source_finding_id` on the observation at `report_finding` time; on `dismiss_finding` / `promote_finding`, delete or auto-dismiss the linked observation; or stop creating the observation by default behind an `also_observe=true` opt-in.

2. **P1 — Write tools ignore claim ownership while heartbeat/release/reclaim strictly enforce it.** **Evidence:** as `mcp-review-e`, with B claimed by `other-actor-y`:
   ```json
   add_comment(B, "...", actor="mcp-review-e") → comment_id: 97   // succeeded
   update_issue(B, title="HIJACKED ...", actor="mcp-review-e") → changed_fields: ["title"]   // succeeded
   ```
   Both calls left `assignee: "other-actor-y"` untouched. Meanwhile the rightful holder's empty-actor heartbeat fails. **Why it matters:** any multi-agent guarantee built on `claim_issue + heartbeat` is silently bypassable from `update_issue`, `add_comment`, `add_label`, `close_issue`, `batch_update`, `batch_close`. **Resolution:** add an optional `expected_assignee` to every write tool; when present, return CONFLICT just like `reclaim_issue`. Document the default (no check) so the asymmetry is at least visible.

3. **P1 — Both `close_issue` *and* `batch_close` bypass workflow enforcement that `update_issue` enforces.** **Evidence:**
   ```json
   // bug in status "triage"
   update_issue(B, status="closed") → INVALID_TRANSITION   // correct: triage→closed not allowed
   close_issue(B) → status: "closed"                       // bypassed

   // milestone in status "planning" (template: planning→active→closing→completed)
   batch_close([..., milestone_id, ...]) → succeeded: [{ milestone_id, status: "completed" }]   // skipped active + closing
   ```
   **Why it matters:** templates are advertised as workflow contracts in `list_types` and `get_template`. Agents reading a bug template see `triage → confirmed → fixing → verifying → closed` and assume the surface enforces it. `batch_close` is the more dangerous of the two because it operates in bulk. **Resolution:** route both `close_issue` and `batch_close` through the same transition validator that `update_issue` uses. If "rage-close from anywhere" is intended, gate it behind `force=true` (mirror `delete_file_record(force=true)`).

4. **P1 — `release_claim` strands wip-category issues with no discoverable handoff surface.** **Evidence:** `release_claim(D, actor="mcp-review-e")` cleared `assignee` but left `status: "in_progress"`. `task` template has no backwards transition. The issue is invisible to `get_ready` (wip), `get_blocked` (excludes wip — see Finding 8), `get_summary` (no slot for unassigned-wip), and only recoverable by `claim_issue` from an agent who already knows the ID. **Resolution:** either auto-revert wip→open category on release (template-aware target state), or ship a `list_handoff_pool` / extend `get_ready` with an `include_orphan_wip=true` flag.

### P2 — real friction, has workaround

5. **P2 — `SCHEMA_MISMATCH` from `get_mcp_status` does not actually gate any tool calls.** `installed_schema_version: 11, database_schema_version: 12, schema_compatible: false, code: "SCHEMA_MISMATCH"`. Yet `create_issue`, `start_work`, `update_issue`, `batch_close`, `archive_closed`, `create_plan`, `report_finding`, `annotate_file`, `dismiss_finding`, `promote_observation` all succeeded. **Why it matters:** `CLAUDE.md` says "most tool calls return an `ErrorResponse` with `code: SCHEMA_MISMATCH`" and instructs agents to surface the message and stop. If the gate isn't enforced, an agent following the doc is stopping work it could have done; if the gate *should* be enforced, the project DB is being mutated under a version skew the maintainer expected to be hard-fail. Either the doc is wrong or the gate is missing. **Resolution:** decide which side is canonical and align the other. If reads are exempt-by-design and writes go through, document that explicitly per-tool; if writes should fail, add the check.

6. **P2 — `heartbeat_work` defaults expected actor to literal `'mcp'`.** `heartbeat_work(A)` from the rightful holder fails with `"expected 'mcp'"`. Docstring says "By default actor is treated as the expected current holder," which reads as "the assignee" but resolves to a fixed string. **Resolution:** when `actor` is omitted, treat as actor-less (skip the holder check) and only set `expected_assignee` when the caller explicitly passes it. Or surface the literal default in the docstring.

7. **P2 — `search_issues` silently elides hyphens, brackets, and short tokens.** Same surface as review-d, extended:
   ```text
   search_issues("mcp-review-e")    → []
   search_issues("[mcp-review-e]")  → []
   search_issues("Scratch task")    → 6 hits including my "[mcp-review-e] Scratch task A"
   search_issues("HIJACKED")        → 1 hit (single word)
   ```
   Agents prefix their work with `[mcp-review-X]` to find it later — and then can't. **Resolution:** document FTS tokenisation in the tool description, switch to LIKE/substring pre-filter for short queries, or normalize bracket/hyphen punctuation before query parse.

8. **P2 — `search_issues` returns archived results indiscriminately.** No `status` / `category` filter on the tool. An agent searching for live work has to post-filter the response. **Resolution:** add a `status_category` parameter (default `["open", "wip"]`).

9. **P2 — `get_blocked` excludes wip-category issues.** A wip task with `blocked_by: [other_id]` does not appear. **Evidence:** task A in_progress with `blocked_by: ["filigree-7e851a7f96"]` — `get_blocked` returned `[]`. **Resolution:** include wip-category blocked issues, or accept a `categories` parameter.

10. **P2 — Archived issues report `status_category: "open"` and `is_ready: true` in record output.** `get_ready` filters them out at query time, but `list_issues` / `get_issue` records show stale category metadata. An agent that paginates `list_issues` and inspects records sees archived tasks tagged as ready open work. Already an open observation (`filigree-obs-d6cc014192`) but it's about the *architecture*; the record-output bug is the user-visible consequence. **Resolution:** treat `archived` as a `done` category (or a fourth `archived` category) consistently across record hydration and not just query plans.

11. **P2 — Mutual-exclusivity displacement is silent.** Adding `review:done` after `review:needed` removes the prior label with no `data_warnings`, no `replaced_label`, `label_result: "added"`. **Resolution:** when a mutually-exclusive sibling is displaced, return `label_result: "replaced"` and `replaced_label: "<old>"`; or surface in `data_warnings`.

12. **P2 — `promote_observation` and `promote_finding` discard cluster labels.** Promotion preserves only `from-observation` / `from-finding`. The agent's session-tagging discipline (`cluster:mcp-review-e`) silently breaks at the boundary. **Resolution:** carry the source's labels onto the new issue (or accept a `labels=[...]` override on promote tools).

13. **P2 — `get_schema.entity_id_prefixes` is partially stale.** `issue.accepted_by_tools` omits `batch_update`, `batch_close`. `observation.accepted_by_tools` omits `batch_promote_observations` (already noted in observation `filigree-obs-e7b379ff6e`). An agent that uses `get_schema` to build its dispatch table is missing batch operations. **Resolution:** auto-generate the table from the live tool registry (already on the roadmap as `filigree-b48cd07e68`).

### P3 — papercuts

14. **P3 — Pagination envelope is inconsistent.** `{items, has_more, next_offset?}` (list_*, batch), `{items, has_more, next_since}` (get_changes), bare array (get_valid_transitions), `{path, length, note}` (get_critical_path). **Resolution:** keep `next_since` for time-windowed reads but at minimum unify the rest.

15. **P3 — `parent_id` (get_issue) vs `parent_issue_id` (list_issues, get_ready).** Same field, two names. **Resolution:** pick `parent_issue_id` (the 2.0 form) and migrate `get_issue`.

16. **P3 — `dismiss_finding` only writes `status: "false_positive"`.** No path to `acknowledged` / `unseen_in_latest` / "won't fix in scope" through the natural verb. `update_finding` accepts the wider enum. **Resolution:** add `status` parameter to `dismiss_finding` (default `false_positive`).

17. **P3 — Dismissed findings persist as `false_positive` forever.** `list_findings(status=false_positive)` returned my run-e finding plus run-d's plus run-c's from days ago. No archival path. **Resolution:** add `archive_findings(days_old=N)` mirroring `archive_closed`, or auto-purge dismissed findings after a configurable window.

18. **P3 — `archive_closed(days_old=0)` is permissive.** It archived 10 issues I closed seconds earlier. The `label` filter saved my session. **Resolution:** require a non-empty `label` filter when `days_old < 7`, or emit `data_warnings: [...]` when archiving issues closed in the last hour.

19. **P3 — `get_valid_transitions` for a closed issue returns bare `[]`.** Different envelope from `list_*`. Not the cleanest signal that "no next states; use `reopen_issue`". **Resolution:** wrap as `{items: [], reopen_available: true}` so the recovery path is always discoverable.

20. **P3 — `create_plan` deps mix `int` and `"p.s"` strings in the same array.** Code generators must switch types per-edge based on cross-phase status. `add_plan_step` already accepts full issue IDs in deps. **Resolution:** also accept full issue IDs in `create_plan` deps, alongside the index sugar.

21. **P3 — `create_plan` response embeds `issue_id` deep in nested objects.** Threading IDs from response into follow-up calls requires tree walking. **Resolution:** mirror the flat hot-path layout of `start_work` / `start_next_work`.

22. **P3 — `undo_last` response buries `issue_id` at the bottom of the object.** Trivial parser papercut. **Resolution:** put `issue_id` first to match every other write-tool response.

23. **P3 — `get_stale_claims` has no near-expiry mode.** With 48h leases, an agent has no way to ask "what's about to expire so I can pre-emptively heartbeat?" — the tool only reports already-expired. **Resolution:** add `expires_within_hours=N` to surface near-expiry claims for proactive heartbeating.

---

## 4. What works well (don't refactor away)

- **`INVALID_TRANSITION` error envelope** — `valid_transitions[]`, `hint`, `reopen_available` (where applicable). Best diagnostic shape in the surface.
- **`annotate_file` provenance** — commit ref, branch, file checksum, anchor confidence, dirty-worktree flag, full event trail, `provenance_trust_level` rollup. Agent-grade traceability.
- **`get_label_taxonomy`** — auto/virtual/manual_suggested/reserved with reasons. Saves a doc lookup.
- **Reserved-label rejection consistency** — `P0`, `priority:high`, `priority:0` all rejected with the same actionable error string pointing at the priority field.
- **`start_next_work` happy path** — claim + transition + lease + heartbeat in one call. Skips already-claimed issues correctly.
- **`create_plan`** — milestone+phases+steps+deps+labels in one call, with per-phase `{total, completed, ready}` aggregates.
- **`list_scanners` + `preview_scan` risk metadata** — `safe_preview_only`, `requires_approval`, `may_send_contents`, `risk_summary`, full preview command string. Cautious-by-default.
- **Batch envelope `{succeeded, failed, newly_unblocked?}`** — `failed[]` always present (empty when all-OK); per-failure `{id, error, code}`. Mixed valid + bogus IDs work cleanly.
- **`archive_closed(label=...)`** — the cleanup story works when the agent uses the label filter.
- **`get_critical_path` empty-case** — returns `note: "no open dependency chains"` so the agent knows the empty result is intentional, not an error.
- **`get_ready(include_context=true)`** — opting into `parent_issue_id` + `parent_title` is the right shape for cold-start orientation while keeping the default response slim.

## 5. Open questions for the maintainer

1. **`SCHEMA_MISMATCH` semantics.** Are reads intentionally exempt and writes intended to go through (in which case `CLAUDE.md` is wrong), or is the gate missing on writes (in which case the doc is right and the implementation is incomplete)? Either way, `get_mcp_status: schema_mismatch` should be load-bearing in exactly one direction, not advertised one way and behaving the other.

2. **Is `report_finding`'s parallel observation intentional?** If yes, the linkage column needs to be populated and the cleanup path on `dismiss_finding` / `promote_finding` needs to be wired. If no, the default should be `also_observe=false` and the side-effect should be opt-in.

3. **Should `update_issue`/`batch_update`/`add_comment`/`add_label`/`close_issue`/`batch_close` accept an optional `expected_assignee`?** Symmetric with `reclaim_issue` and would close the write-path enforcement asymmetry without breaking existing callers.

4. **Is `close_issue`/`batch_close` intended as a workflow shortcut or a workflow-respecting tool?** If shortcut, gate behind `force=true`. If respecting, route through the transition validator. Right now they're both, which makes the template-vs-surface contract unanswerable from inside the API.

5. **What's the intended discovery path for unassigned wip issues after `release_claim`?** Currently invisible to `get_ready`, `get_blocked`, `get_summary` — only recoverable if an agent already knows the issue ID.

6. **Should `archived` be its own status category?** The current "archived stored as status, but is `status_category: open` in record output, but excluded from `get_ready` query plans" creates a three-way contradiction between query, record, and template.

7. **Should `promote_observation` / `promote_finding` carry source labels?** Right now they discard everything except `from-observation` / `from-finding`. A simple `inherit_labels=true` (default) would preserve session/cluster context across the promotion boundary.

8. **Pagination unification.** Is `next_since` (time-based) load-bearing for `get_changes`, or could it return `next_offset` like every other list tool? Consistent shape lets agents reuse one paginator.

---

**Cleanup state at end of run:** all 11 `cluster:mcp-review-e` issues closed via `batch_close` (10 succeeded + 1 expected NOT_FOUND on bogus ID); the promoted-observation issue `filigree-6341e29f0a` separately closed; `archive_closed(days_old=0, label=cluster:mcp-review-e)` swept the 10 closed scratch issues; the synthetic finding `filigree-sf-20d3b3d2e1` dismissed; the synthetic annotation `filigree-ann-a27dbc3d30` resolved; the auto-spawned observation `filigree-obs-9c65983d67` dismissed via `batch_dismiss_observations`. Database returned to a clean state with no run-e residue.
