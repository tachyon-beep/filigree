# ADR-010: Archived Status Model

**Status**: Accepted
**Date**: 2026-05-13
**Deciders**: John (project lead)
**Context**: Reviews found archived issues appearing as open or ready in some hydrated records while query paths special-cased them away.

## Summary

Archived work is outside active workflow. It must never hydrate or query as
ready/open work. Filigree will represent archived records consistently across
record output, stats, search, stale-claim discovery, cleanup, and ready/blocked
queues.

## Context

Archiving is an operational cleanup tool. Reviews found a three-way tension:
archived items could be stored as a status, hydrate with open-category metadata,
and be special-cased out of ready queries. That makes agents distrust both
record output and discovery queues.

ADR-003 clarifies that archive is allowed because records are operationally
durable, not audit-proof. This ADR clarifies what archive means in workflow
terms.

## Decision

We will treat `archived` as outside active workflow:

1. Archived issues are not ready, not blocked work, and not reclaimable stale
   claims by default.
2. Public record hydration must not report archived issues as `status_category:
   "open"`.
3. Search and list surfaces should exclude archived items by default when the
   tool is framed as live-work discovery, and should provide explicit
   `include_archived` or `status_category` controls where archived history is
   useful.
4. Stats should distinguish active workflow categories from archived records.
5. Cleanup tools may move done or scratch work into archive according to
   ADR-005, but the resulting records must leave active queues.

## Consequences

### Positive

- Agents can trust that active-work queues contain active work.
- Archive becomes a clear operational state rather than a query-time exception.
- Stale-claim and search tools stop surfacing archived tombstones by default.

### Negative

- Existing code paths that map statuses to only `open`, `wip`, and `done` need
  to handle archived explicitly or map it consistently to a non-active category.
- Some reports may need updated counts.

### Neutral

- This ADR does not decide whether archived is a fourth category or a done
  subcategory internally. It decides the public invariant: archived is inactive
  and not ready/open.

## Implementation Notes

- Pick and document the internal category representation during implementation.
- Update hydration, ready, blocked, stale claims, search, stats, and archive
  tests together.
- Prefer explicit filters over hidden query-time special cases.

## Related Decisions

- **Related to**: [ADR-003: Operational Durability, Not Audit-Proof Records](./ADR-003-operational-durability-not-audit-proofing.md)
- **Related to**: [ADR-005: Workflow Enforcement and Explicit Cleanup Paths](./ADR-005-workflow-enforcement-and-cleanup-paths.md)

## References

- `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`
- `filigree-aec52efb9b`
