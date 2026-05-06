# MCP Shared File Annotations Design

**Date:** 2026-05-07 local time
**Status:** Draft design, revised after conceptual review
**Tracker:** `filigree-360ac7fc4c` under `filigree-ed2ccaf10d`
**Audience:** Filigree maintainers and agents using the MCP surface

## Summary

Add a shared annotation system for durable, provenance-rich file notes. An annotation is not a bug report and not a task. It is project context anchored to a file or line range, created when an agent wants future agents to understand what it was looking at and why that context matters.

The key difference from observations:

- **Observation:** "I noticed something that may deserve triage."
- **Annotation:** "Future agents working near this file or linked work item should know this."

Annotations should be shared by default, DB-backed, line/snippet anchored, linked to any relevant work items, and automatically capture commit/diff/checksum provenance so a later agent can answer: "what exactly were they looking at when they wrote this?"

The first version should be intentionally narrow: annotations are file-anchored,
project-shared notes with computed drift state. They surface important context;
they do not create hard workflow blocks, replace observations, or become a
general graph-note system.

## Goals

- Let agents leave durable shared notes on files without editing source comments.
- Preserve the viewing context automatically: commit, branch, dirty state, file checksum, anchor snippet, and relevant diff.
- Link one annotation to many V1 targets: issues and epics through issue records, observations, findings, and file records.
- Support a boolean `critical` flag that means "surface this more aggressively."
- Surface annotations from both directions: while reading a file and while working a linked issue or epic.
- Track staleness when files drift from the version where the note was made.

## Non-Goals

- Replace source comments or docstrings.
- Replace observations, findings, comments, or issues.
- Add a full priority system for annotations. Tickets already own scheduling priority; annotations only need attention criticality.
- Build semantic code indexing in the first version. Start with line ranges, snippets, checksums, and diff provenance.
- Store unlimited raw diffs or sensitive patch content.
- Create annotations directly on non-file targets in V1. Non-file relevance is represented through links from a file-anchored annotation.

## V1 Scope Decisions

- **File anchored:** every annotation has a `file_id`/`file_path`; line ranges
  are optional but preferred.
- **Project shared:** V1 omits session-private annotations. If agents need
  temporary notes, observations already cover low-friction ephemeral capture.
- **Advisory attention:** `critical=true` changes surfacing and closeout
  warnings, but does not hard-block issue or epic closure in V1.
- **Constrained links:** V1 links annotations to `issue`, `file`, `finding`,
  and `observation`. Epics are issues with `type="epic"` and are reached through
  the normal issue graph. Sessions, commits, and pull requests can be added
  after the session/checkpoint model exists.
- **Computed drift:** annotation lifecycle is human-managed; anchor state is
  computed on read and returned separately.
- **Context tools:** V1 adds annotation-specific list/read/context helpers first.
  Broader all-in-one file or issue context aggregation can layer on top once the
  annotation records are stable.

## When To Use Each Primitive

| Need | Use | Why |
| --- | --- | --- |
| "I noticed something possibly worth triage, but I should not derail." | `observe` | Ambient, expiring, low-validation capture. |
| "A scanner or agent found a structured code concern." | `report_finding` / finding tools | Findings carry severity, rule identity, evidence, and triage status. |
| "This issue needs conversation, handoff, or status explanation." | Issue comment | Comments belong to the work item discussion and audit trail. |
| "This should become scheduled work." | Issue or epic | Issues own priority, assignee, status, dependencies, and closure. |
| "Future agents working near this file or linked issue need durable context." | `annotate_file` | Annotations preserve file-local context and provenance without changing source. |

The decision rule is actionability plus durability:

- uncertain and unscheduled -> observation;
- validated code concern -> finding;
- scheduled work -> issue;
- conversation about scheduled work -> comment;
- durable file context for future agents -> annotation.

## Core Model

An annotation combines five things:

1. **Note:** what the agent wants future readers to know.
2. **Anchor:** where the agent was looking.
3. **Provenance:** what repository state the agent saw.
4. **Context:** what the agent was doing when it made the note.
5. **Links:** which work items or artifacts the note matters to.

Suggested top-level fields:

```text
annotation_id
file_id
file_path
line_start
line_end
anchor_snippet
note
context_summary
intent
critical
status
actor
session_ref
created_at
updated_at
resolved_at
```

Suggested enums:

```text
intent:
  explanation
  warning
  breadcrumb
  hypothesis
  decision
  handoff
  gotcha

status:
  active
  resolved
  superseded
  promoted
```

`status` is the human lifecycle. It should not include drift states such as
`stale`; drift is computed separately so a still-valid warning does not become
"resolved" merely because line numbers changed.

`session_ref` is optional opaque provenance. It can hold the current agent run ID
when one exists, but V1 must not require the separate session/checkpoint feature.

## Provenance Snapshot

Every annotation should automatically capture repository and file state.

Suggested fields:

```text
commit_ref
branch
repo_root
worktree_root
git_state
worktree_dirty
file_checksum
file_size
file_mtime
dirty_diff_hash
dirty_diff_summary
file_diff
worktree_diff_summary
anchor_context_before
anchor_context_after
provenance_trust_level
provenance_flags
provenance_warnings
```

The checksum should be for the file content at annotation time. The diff should be capped:

- Store file-local diff in full when below a size limit.
- Store a hash plus summary when too large.
- Store whole-worktree diff summary and hash, not necessarily the whole patch.
- Redact likely secrets before storing diff text; if redaction triggers, store
  `provenance_flags=["redacted"]` plus a warning.

This is the critical trust feature. A later agent should be able to tell whether the note was written against clean `HEAD`, against uncommitted edits, or against a file that no longer matches.

Provenance capture must be explicit about partial states:

| Situation | Expected behavior |
| --- | --- |
| Clean tracked file | Capture commit, branch, checksum, snippet, and clean status. |
| Dirty tracked file | Capture commit, branch, checksum, file-local diff metadata, and worktree diff summary/hash. |
| Untracked file | Capture checksum/snippet and mark `git_state="untracked"`; `commit_ref` is the repo `HEAD`, not proof that the file existed there. |
| Detached `HEAD` | Store `commit_ref`; leave `branch` empty or record detached label. |
| Missing git metadata | Store file checksum/snippet where possible and mark provenance partial. |
| Binary file | Store checksum, size, and MIME/binary flag; omit snippet and raw diff. |
| Large or generated file | Store checksum and capped context; omit or summarize oversized snippets/diffs with a warning. |
| Multi-worktree checkout | Store both `repo_root` and `worktree_root` so later agents know which checkout produced the note. |

MCP responses should return provenance warnings rather than fail the annotation
when a useful partial record can still be captured. The call should fail only
when the file anchor itself cannot be resolved.

Use flags, not one mutually exclusive provenance status. Real annotations can
be dirty, redacted, large, untracked, and partial at the same time.

```text
provenance_trust_level:
  complete
  partial
  minimal

provenance_flags:
  dirty_worktree
  redacted
  oversized_diff
  oversized_file
  binary_file
  generated_file
  untracked_file
  detached_head
  missing_git_metadata
  commit_unavailable
```

## Links

Annotations need many-to-many links. A file invariant can matter to several issues; an epic can collect important notes from many files.

Suggested link table:

```text
annotation_link_id
annotation_id
target_type
target_id
relationship
created_at
actor
```

V1 targets:

```text
issue
observation
finding
file
```

Epics use `target_type="issue"` because they are issue records with an epic
type. This keeps link traversal aligned with existing file traceability and
parent/child issue APIs.

V1 relationships:

```text
relevant_to
must_consider
evidence_for
explains
created_from
promoted_to
```

An annotation linked to an epic with `relationship="must_consider"` and `critical=true` means: when an agent works this epic, this file note should be brought into its working context.

Relationship behavior:

- `relevant_to`: include in expanded context, but do not elevate.
- `must_consider`: elevate in context for the target and show in closeout
  warnings while active.
- `evidence_for`: present as supporting evidence for findings or issues.
- `explains`: show as explanatory file context.
- `created_from`: records promotion or conversion source.
- `promoted_to`: records the target issue/observation/finding created from the
  annotation.

## Critical Flag

Use a boolean:

```text
critical: true | false
```

Do not use P0-P4 for annotations. Priorities schedule work; annotation criticality routes attention.

Critical annotations should:

- appear first in file context;
- appear in linked issue/epic context;
- be called out if their computed `anchor_state` becomes stale or drifted;
- appear in closeout warnings for linked open issues or epics when the
  relationship is `must_consider`;
- require an explicit agent decision to resolve, supersede, promote, or carry
  forward when a closeout helper is used.

V1 should warn and list, not block. A close operation on an issue with active
critical `must_consider` annotations should remain possible, but the tool should
return a structured warning containing annotation IDs and suggested actions.
Agents can then call `resolve_annotation`, `supersede_annotation`,
`promote_annotation`, or `carry_forward_annotation`. Hard blocking can be a V2
policy decision after agents prove the warning path is not noisy.

Carry-forward semantics:

- `carry_forward_annotation(annotation_id, from_target_id, to_target_id, reason)`
  creates a new `must_consider` link to `to_target_id` unless one already
  exists.
- It preserves `critical` and leaves the annotation `active`.
- It writes an audit record with actor, reason, old target, and new target.
- It marks the old target's closeout warning as acknowledged, so the old issue
  or epic can close without losing the annotation for follow-on work.

## Staleness And Anchor Drift

Annotation reads should compute anchor state by comparing stored provenance to current file state. This is not the same thing as annotation lifecycle status.

Suggested computed fields:

```text
anchor_state:
  current
  line_drifted
  content_changed_anchor_found
  stale
  file_missing

anchor_match_confidence
anchor_match_count
current_line_start
current_line_end
commit_available
```

Behavior:

- `current`: checksum still matches.
- `line_drifted`: checksum changed but anchor snippet still matches at a different line.
- `content_changed_anchor_found`: checksum changed and snippet still exists near the original line.
- `stale`: checksum changed and anchor cannot be found.
- `file_missing`: tracked file path no longer exists.

`commit_available` is separate because commit availability can coexist with any
anchor state. A file can be `current` while the original commit is unavailable
in a shallow clone.

The first version can compute this lazily in read tools instead of storing it eagerly.

## MCP Tools

Minimum useful MCP surface:

```text
annotate_file(
  file_path,
  note,
  line_start?,
  line_end?,
  context_summary?,
  intent?,
  critical?,
  links?
)

list_annotations(
  file_path?,
  file_id?,
  issue_id?,
  target_type?,
  target_id?,
  actor?,
  intent?,
  critical?,
  status?,
  anchor_state?,
  response_detail?,
  limit?
)

get_annotation(annotation_id)

update_annotation(
  annotation_id,
  note?,
  context_summary?,
  intent?,
  critical?,
  status?
)

resolve_annotation(annotation_id, reason)

supersede_annotation(annotation_id, replacement_annotation_id, reason)

promote_annotation(annotation_id, target_type="issue|observation", title?, reason, keep_active=true)

carry_forward_annotation(annotation_id, from_target_id, to_target_id, reason)

link_annotation(annotation_id, target_type, target_id, relationship)

unlink_annotation(annotation_id, target_type, target_id, relationship?)
```

High-leverage context tools:

```text
get_file_annotations(file_path, response_detail="summary")
get_issue_annotations(issue_id, response_detail="summary")
list_attention_annotations(target_id?, file_path?, critical=true, status="active")
```

`get_file_annotations` and `get_issue_annotations` keep V1 focused on the new
primitive. Broader `get_file_context` and `get_issue_context` tools can compose
annotations with file records, findings, observations, comments, and associated
issues later; they should not be required to ship the annotation core.

Context tools should be conservative by default:

- return active critical annotations first;
- include counts for hidden resolved/superseded/promoted annotations;
- cap note/provenance text in summary mode;
- include `has_more`/`next_offset` for annotation lists;
- let callers filter by relationship, lifecycle status, critical flag, and
  computed anchor state;
- use `response_detail="full"` only when the agent explicitly needs full
  provenance, links, and audit records.

## CLI Shape

The CLI should exist for background agents and humans:

```bash
filigree annotate-file src/foo.py \
  --line 42 \
  --intent warning \
  --critical \
  --link issue:filigree-abc123:must_consider \
  "This validation protects the legacy CLI path."

filigree list-annotations --file src/foo.py --detail summary --json
filigree resolve-annotation filigree-ann-abc123 --reason "Invariant moved to docs"
filigree carry-forward-annotation filigree-ann-abc123 \
  --from filigree-epic123 \
  --to filigree-phase2 \
  --reason "Still applies to phase 2 work"
```

CLI JSON should use the same envelopes as MCP.

## Example

```text
annotate_file(
  file_path="src/filigree/mcp_tools/scanners.py",
  line_start=301,
  line_end=306,
  note="report_finding creates observations implicitly here; any cleanup of finding/observation flow must preserve or make explicit this dual-write behavior.",
  context_summary="Noticed while designing shared file annotations after the MCP agent-systems review.",
  intent="warning",
  critical=true,
  links=[
    {
      target_type: "issue",
      target_id: "filigree-ed2ccaf10d",
      relationship: "must_consider"
    },
    {
      target_type: "issue",
      target_id: "filigree-42e0aa3c89",
      relationship: "evidence_for"
    }
  ]
)
```

Future file context might show:

```text
CRITICAL annotation filigree-ann-123
intent: warning
anchor_state: line_drifted, snippet found at line 318, confidence high
made_at: commit abc123, dirty worktree true
linked: filigree-ed2ccaf10d (must_consider), filigree-42e0aa3c89 (evidence_for)
note: report_finding creates observations implicitly here...
```

## Relationship To Existing Concepts

Annotations should coexist with existing Filigree primitives:

- **Comments** are issue conversation and handoff.
- **Observations** are triage candidates that may become work.
- **Findings** are scanner or agent-discovered structured code concerns.
- **Issues/epics** are scheduled work.
- **Annotations** are durable file context with provenance.

Promotion paths:

- annotation -> observation when the note becomes a triage candidate;
- annotation -> issue when it becomes work;
- observation -> annotation when triage decides the item is useful context but not a defect;
- finding -> annotation when a finding is intentionally accepted or documented as context.

Promotion is an audit-preserving transition, not a destructive move:

- The source record remains visible unless separately resolved or dismissed by
  its own lifecycle.
- The created target is linked with `created_from`.
- `promote_annotation(..., keep_active=true)` is the default. It creates the
  target record or link, adds `promoted_to`, and leaves the annotation `active`
  as file context.
- `promote_annotation(..., keep_active=false)` moves the source annotation to
  `status="promoted"` and writes an audit record. Use this only when the new
  issue or observation should replace the annotation as the surfaced file context.
- Promoted observations and findings should preserve their original evidence,
  actor, timestamps, and source IDs in links or audit records.

## Data Storage Sketch

Tables:

```text
annotations
annotation_links
annotation_provenance
annotation_resolutions
```

`annotations` stores the note, anchor, lifecycle, and display fields.
`annotation_provenance` stores commit/diff/checksum fields, separated so large diff metadata does not crowd normal list queries.
`annotation_links` stores many-to-many typed relationships.
`annotation_resolutions` stores audit records for resolve, supersede, promote,
and carry-forward operations.

The storage layer should keep lifecycle status and computed anchor state
separate. `annotations.status` stores human lifecycle. Read APIs compute
`anchor_state`, match confidence, current line, commit availability, provenance
flags, and provenance warnings from the current worktree.

## Acceptance Criteria

- Agents can create a shared project annotation on a file through MCP in one call.
- The annotation automatically records current commit, branch or detached state, worktree root, dirty state, file checksum, anchor snippet when text is available, and capped file-local diff metadata.
- Provenance capture reports partial, redacted, large-file, binary-file, untracked-file, and missing-git states without pretending they are clean snapshots.
- An annotation can be linked to multiple issues, files, findings, or observations with typed V1 relationships.
- A boolean `critical` flag changes surfacing behavior without introducing priority semantics or hard close blockers.
- `get_file_annotations` shows active annotations for a file and computes anchor state separately from lifecycle status.
- `get_issue_annotations` shows linked critical annotations for an issue or epic in summary form by default.
- Resolving an annotation preserves an audit trail.
- Existing observations, findings, comments, and issues remain distinct; annotations do not replace them.

## Open Questions

- What diff size cap is appropriate for inline storage?
- Should V2 add session-private annotations, or are observations plus future session checkpoints enough?
- Should V2 add direct annotation-to-annotation links, or keep all relationships target-oriented?
- Should accepted or false-positive findings offer an explicit "convert to annotation" action in the same triage flow?
