# ADR-008: Claim-Aware Write Defaults

**Status**: Accepted
**Date**: 2026-05-13
**Deciders**: John (project lead)
**Context**: Claim-aware writes gained `expected_assignee`, but reviews found the safety contract remained opt-in.

## Summary

When a write tool receives an `actor` and the target issue is held by an
assignee, Filigree should treat the actor as the expected holder by default.
Mutating another actor's held work must require an explicit override.

## Context

Filigree has claim, heartbeat, release, and reclaim tools to prevent agents from
double-working the same issue. Later fixes added `expected_assignee` to write
tools so agents can opt into claim-aware mutation. Review G confirmed that this
worked when passed, but the default remained unsafe: an agent that did not know
about the option could still mutate someone else's held issue.

Safety that only works when every agent remembers a niche parameter is fragile.

## Decision

We will make claim-aware writes safe by default:

1. If a write tool is called with `actor` and the issue has a non-empty
   assignee, the default expected assignee is `actor`.
2. If the observed assignee differs from the expected assignee, return
   `CONFLICT`.
3. To intentionally mutate another actor's held issue, callers must use an
   explicit override such as `force=true` or `expected_assignee=null`, depending
   on the tool.
4. Error messages must name the observed holder and expected holder.
5. Documentation should still teach explicit `expected_assignee` for critical
   coordination paths, but correctness must not depend on it.

## Consequences

### Positive

- Multi-agent coordination becomes the default rather than an expert mode.
- Accidental cross-claim writes fail fast.
- Existing `expected_assignee` behavior remains useful for compare-and-swap
  style flows.

### Negative

- Some scripts that pass `actor` while intentionally editing held work will need
  explicit override.
- Tools need a consistent override story.

### Neutral

- Actorless writes may remain permissive for local/manual workflows unless a
  specific tool chooses stricter behavior.

## Implementation Notes

- Apply this consistently to issue writes: update, comment, label, close, batch
  update, and batch close.
- Extend to file/finding writes only where issue ownership is the relevant
  coordination boundary.
- Keep `CONFLICT` semantics aligned with claim/reclaim errors.

## Related Decisions

- **Related to**: [ADR-005: Workflow Enforcement and Explicit Cleanup Paths](./ADR-005-workflow-enforcement-and-cleanup-paths.md)
- **Related to**: [ADR-011: Agent Sessions Deferred Beyond 2.0](./ADR-011-agent-sessions-deferred-beyond-2-0.md)

## References

- `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-g.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-e.md`
