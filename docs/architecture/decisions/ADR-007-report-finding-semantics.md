# ADR-007: `report_finding` Semantics

**Status**: Accepted
**Date**: 2026-05-13
**Deciders**: John (project lead)
**Context**: Senior-user reviews found `report_finding` mixing manual finding creation, scanner-ingest counters, and observation side effects.

## Summary

`report_finding` is a manual single-finding write by default. It may optionally
create a paired observation, but that side effect must be explicit, documented,
actor-attributed, linked to the finding, and reflected with a slim response.

## Context

The current tool is useful because agents can quickly record a finding while
working. Reviews found two sources of confusion:

- The tool can auto-create an observation, which turns one finding into two
  triage queues.
- The response contains batch-ingest style counters for a single manual write.

Older reviews also found the paired observation could be unlinked or not cleaned
up; later reviews reported linkage and cleanup improved. The remaining problem
is product semantics: what does this tool mean?

## Decision

We will define `report_finding` as:

1. A single manual finding write by default.
2. Actor-aware: manual findings must be able to record who reported them.
3. Observation creation is opt-in or otherwise explicitly requested by a named
   parameter; it is not a hidden side effect.
4. When a paired observation is created, it must carry `source_finding_id` and
   be cleaned up or resolved when the finding is dismissed or promoted.
5. The response should be slim: the finding identity and status, plus optional
   `observation_id` when a paired observation is created. Batch counters belong
   on batch scanner ingest surfaces, not this single-write tool.

## Consequences

### Positive

- The finding and observation queues stop duplicating work by surprise.
- Agents can reason about manual finding creation without learning scanner
  ingest internals.
- Actor attribution becomes available for file/finding operational history.

### Negative

- Callers that depended on automatic observation creation need to pass the new
  explicit option.
- Existing tests may need to move aggregate counter assertions to scanner ingest
  paths.

### Neutral

- Automated scanners can still create findings and observations through
  scanner-specific or batch-ingest paths where aggregate counters make sense.

## Implementation Notes

- Add an explicit option such as `create_observation` or `also_observe`.
- Add `actor` support to manual finding writes.
- Keep source linkage and cleanup transactional.
- Update docs to distinguish manual finding reporting from scanner ingest.

## Related Decisions

- **Related to**: [ADR-003: Operational Durability, Not Audit-Proof Records](./ADR-003-operational-durability-not-audit-proofing.md)
- **Related to**: [ADR-009: Response Shape Philosophy](./ADR-009-response-shape-philosophy.md)

## References

- `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`
- `docs/plans/2026-05-12-mcp-senior-user-review-h.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-g.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-f.md`
