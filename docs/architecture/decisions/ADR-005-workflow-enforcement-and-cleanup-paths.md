# ADR-005: Workflow Enforcement and Explicit Cleanup Paths

**Status**: Accepted
**Date**: 2026-05-13
**Deciders**: John (project lead)
**Context**: Senior-user reviews found tension between workflow-respecting close operations and practical cleanup of scratch/review data.

## Summary

Normal issue lifecycle operations must respect workflow templates. Cleanup and
archive operations are allowed to bypass ordinary lifecycle transitions, but
only through explicit cleanup paths with clear scoping, naming, and consequences.

## Context

Earlier reviews found `close_issue` and `batch_close` bypassing transitions that
`update_issue` rejected. That made templates feel advisory rather than
contractual. Later reviews found that after close paths were made stricter,
routine session cleanup of mixed-type scratch data became clumsy unless agents
used `force=true`.

Both observations are correct. "Close this issue as normal work" and "clean up
review fixtures" are different operations.

## Decision

We will maintain two explicit lanes:

1. **Workflow lane**: `close_issue`, `batch_close`, and ordinary status
   transitions respect the active workflow template by default.
2. **Cleanup lane**: cleanup/archive tools may skip workflow transitions when
   their purpose is operational housekeeping, scratch cleanup, or archival.
3. Cleanup lane operations must make their scope explicit through filters such
   as actor, session, label, age, status category, source type, or issue IDs.
4. Broad or lossy cleanup should support dry-run previews or return enough
   metadata to explain what changed.
5. `force=true` is acceptable for explicit cleanup flows, but should not be the
   only discoverable way to perform common end-of-session cleanup.

## Consequences

### Positive

- Workflow templates remain real contracts for normal work.
- Agents get a sanctioned way to remove scratch/review residue.
- Cleanup behavior aligns with ADR-003's operational durability model.

### Negative

- Some cleanup flows need dedicated tools or extra options.
- Documentation must teach users which lane they are using.

### Neutral

- This does not require deleting operational history. Cleanup can mean close,
  archive, compact, dismiss, or hide from active queues depending on the record
  type.

## Implementation Notes

- Keep workflow validation central so all normal status-changing operations use
  the same rules.
- Add or document cleanup tools for mixed-type scratch data.
- Prefer session-unique labels or actor/session filters for review cleanup.
- Ensure cleanup tools cannot accidentally sweep unrelated active work.

## Related Decisions

- **Related to**: [ADR-003: Operational Durability, Not Audit-Proof Records](./ADR-003-operational-durability-not-audit-proofing.md)
- **Related to**: [ADR-010: Archived Status Model](./ADR-010-archived-status-model.md)

## References

- `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`
- `docs/plans/2026-05-12-mcp-senior-user-review-h.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-e.md`
