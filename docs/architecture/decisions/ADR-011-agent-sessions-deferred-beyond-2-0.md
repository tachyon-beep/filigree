# ADR-011: Agent Sessions Deferred Beyond 2.0

**Status**: Accepted
**Date**: 2026-05-13
**Deciders**: John (project lead)
**Context**: Senior-user and agent-systems reviews identified first-class agent sessions as valuable, but broader than the immediate 2.0 ship surface.

## Summary

First-class agent session/run records are a desired coordination substrate, but
they are not required to ship Filigree 2.0. For 2.0, Filigree will continue to
use actor strings, issue claims, comments, observations, findings, and events as
the working coordination model, while preserving a clear path to add sessions
after the release.

## Context

Reviews identified a real gap: free-form actor strings cannot answer "what did
this agent session intend, touch, observe, verify, and leave unfinished?" A
session object could improve resumption, traceability in the operational sense,
delegation, and multi-agent coordination.

However, sessions affect many surfaces: claims, comments, events, observations,
findings, annotations, scanners, import/export, MCP resources, and possibly the
dashboard. Designing that substrate hastily would risk turning a real product
improvement into a partially wired identity layer.

## Decision

We will defer first-class agent sessions beyond the 2.0 ship decision:

1. Filigree 2.0 does not require session/run objects to ship.
2. Existing actor strings, claim metadata, comments, observations, findings, and
   events remain the 2.0 coordination model.
3. 2.0 fixes should not introduce ad hoc partial session columns unless they
   are clearly forward-compatible.
4. Post-2.0 session design should be tracked as a feature with explicit scope:
   start session, checkpoint, finish session, session-scoped changes, and links
   from observations/findings/comments/events/claims.
5. Documentation should avoid implying that actor strings provide durable
   session identity.

## Consequences

### Positive

- 2.0 can ship without a rushed cross-cutting identity model.
- Existing coordination fixes remain focused.
- The future session feature has a clearer boundary.

### Negative

- Agents still need manual discipline for session labels, comments, and
  handoffs in 2.0.
- Some cleanup/filtering features will use actor/label approximations until
  session records exist.

### Neutral

- This decision does not reject sessions. It explicitly defers them from the
  2.0 ship bar.

## Implementation Notes

- Keep `filigree-c2009921cf` or its successor as the tracking issue for the
  post-2.0 session model.
- Prefer actor/session-label filters in 2.0 cleanup tools so they can later map
  to true session IDs.
- If a new field is added before sessions, document whether it is an actor,
  session label, or future session ID.

## Related Decisions

- **Related to**: [ADR-008: Claim-Aware Write Defaults](./ADR-008-claim-aware-write-defaults.md)
- **Related to**: [ADR-003: Operational Durability, Not Audit-Proof Records](./ADR-003-operational-durability-not-audit-proofing.md)

## References

- `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`
- `filigree-c2009921cf`
