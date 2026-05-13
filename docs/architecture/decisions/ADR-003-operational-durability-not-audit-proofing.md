# ADR-003: Operational Durability, Not Audit-Proof Records

**Status**: Accepted
**Date**: 2026-05-13
**Deciders**: John (project lead)
**Context**: Senior-user MCP reviews repeatedly surfaced tension between useful durable project history and audit-grade immutability.

## Summary

Filigree records are durable for operational utility, not audit-proof evidence.
The product should preserve enough history to help agents and humans resume,
coordinate, debug, and recover work, but it does not promise evidentiary
immutability, compliance retention, tamper resistance, or forensic audit
guarantees. Cleanup, compaction, archive, correction, and fixture-removal
features are allowed product behavior when they improve day-to-day usefulness.

## Context

Filigree stores issues, comments, observations, scan findings, file records,
annotations, and events. These records are valuable because they let agents and
humans answer operational questions:

- What is ready to work on?
- What changed since my last session?
- Why is this issue blocked?
- What observations or findings still need triage?
- What scratch data did a review session leave behind?
- Which files, findings, or annotations provide useful context for the next
  worker?

The 2026-05 senior-user reviews exposed a recurring ambiguity: if records are
treated as audit-proof, then deletion, compaction, cleanup, archive, and
correction features look suspicious. If records are treated as operational
state, then those features are not only acceptable, they are necessary to keep
the tool useful.

Filigree is an agent-native project coordination tool. It is not a compliance
ledger, evidence locker, legal record system, or regulated audit product. It
uses durable local storage because agents need continuity across sessions, not
because the system guarantees an immutable chain of custody.

### Constraints

- Filigree must remain useful in local, single-project, and lightweight
  multi-agent workflows.
- Agents must be able to clean up synthetic review data, stale observations,
  dismissed findings, abandoned claims, and outdated scratch records without
  pretending that every intermediate artifact is permanent evidence.
- Operational history should remain good enough for debugging and resumption.
- Public language must not imply audit-grade properties that the product does
  not and should not provide.
- HTTP generation stability from ADR-002 remains a contract boundary for API
  consumers; this ADR is about record semantics, not wire compatibility.

## Decision

We will define Filigree's persistence model as **operational durability**:

1. Records are durable by default so work can be resumed, coordinated, and
   explained.
2. Records are not audit-proof. Filigree does not promise immutability,
   tamper-evidence, non-repudiation, retention schedules, legal hold,
   append-only storage, cryptographic sealing, or compliance-grade audit logs.
3. Cleanup and correction operations are legitimate product features. This
   includes archiving, compaction, dismissal, purging scoped scratch data,
   resolving annotations, correcting stale state, and removing operationally
   useless residue.
4. When a destructive or lossy operation could surprise a user, the product
   should make scope and consequences clear through names, filters, previews,
   warnings, or explicit `force`-style options.
5. User-facing documentation should prefer terms such as "history", "event
   history", "activity", "provenance", "trace", or "operational record" unless
   a feature actually provides audit-grade guarantees.
6. Tests should verify product behavior and safety boundaries. They should not
   encode audit-proof expectations such as "records can never be removed" unless
   a specific feature explicitly chooses that behavior.

## Alternatives Considered

### Alternative 1: Treat Filigree as Audit-Proof

**Description**: Make all records append-only and design around immutability,
retention, forensic traceability, and legal-grade audit semantics.

**Pros**:

- Stronger evidentiary story.
- Simpler answer to "should this record ever disappear?".
- Could support regulated or compliance-heavy environments someday.

**Cons**:

- Misaligned with Filigree's current product purpose.
- Makes routine agent workflows noisier by preserving synthetic and stale data
  as first-class permanent material.
- Implies security, retention, identity, and tamper-evidence work that the
  product does not currently implement.
- Raises the bar for every cleanup feature without a clear user requirement.

**Why rejected**: This would make Filigree worse at its primary job: lightweight
agent and human coordination. Audit-proofing is a different product.

### Alternative 2: Treat All Records as Disposable Cache

**Description**: Keep only current issue state and allow history to disappear
freely, with minimal concern for resumption or provenance.

**Pros**:

- Simple cleanup story.
- Small storage footprint.
- Fewer retention decisions.

**Cons**:

- Breaks agent resumption and handoff.
- Makes debugging workflow transitions harder.
- Loses useful provenance from observations, findings, annotations, and file
  associations.
- Undermines the value of events and comments as operational memory.

**Why rejected**: Filigree's usefulness depends on durable context. The records
are not evidence-grade, but they are still valuable project memory.

### Alternative 3: Split Modes into "Operational" and "Audit"

**Description**: Add a project mode that enables audit-grade storage while the
default remains operational.

**Pros**:

- Preserves a future path for stricter deployments.
- Lets different users choose different retention guarantees.

**Cons**:

- Doubles semantic surface area before there is a concrete requirement.
- Makes MCP, CLI, HTTP, docs, and tests harder to reason about.
- Risks accidental misconfiguration: users may believe they are in audit mode
  when they are not.

**Why rejected for now**: This is plausible future work, but not a 2.0 product
commitment. If Filigree later needs audit-grade behavior, it should get a new
ADR, explicit requirements, and likely a separate storage/security design.

## Consequences

### Positive

- Cleanup and archive features can be designed directly around utility.
- Senior-user review findings about stale observations, scratch cleanup,
  dismissed findings, and old claims have a clear product principle.
- Documentation can stop implying stronger guarantees than the system provides.
- Agents can reason about Filigree as a durable working memory, not an evidence
  system.

### Negative

- Filigree is explicitly unsuitable as the system of record for compliance,
  legal, or forensic audit requirements.
- Users who need audit-grade guarantees must integrate with another system or
  wait for a future, separately designed mode.
- Some historical data may be removed, compacted, corrected, or hidden by
  product features when operational utility calls for it.

### Neutral

- This does not weaken API generation stability from ADR-002.
- This does not require immediate deletion or compaction features; it only
  establishes that those features are allowed and should be designed honestly.
- Individual features may still choose immutability where useful, but they must
  justify it as a product behavior rather than inheriting a global audit claim.

## Implementation Notes

- Update user-facing documentation and tool descriptions to avoid "audit"
  language where "history", "events", "activity", or "provenance" is more
  accurate.
- For cleanup operations, prefer explicit scoping: actor/session labels, age,
  status category, source type, and dry-run previews where practical.
- For lossy operations, return enough metadata to explain what changed.
- For compaction, preserve useful summaries or counters when full event detail
  is no longer worth retaining.
- For issue/event/finding/observation tests, verify operational invariants:
  discoverability, safe scoping, recovery, and clear warnings.
- Do not market Filigree as compliance/audit software unless a future ADR
  introduces and implements that product scope.

## Related Decisions

- **Related to**: [ADR-002: API Generations and the Federation-Component Posture](./ADR-002-api-generations-and-federation-posture.md)
- **Related to**: `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`

## References

- `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`
- `docs/plans/2026-05-12-mcp-senior-user-review-h.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-g.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-f.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-e.md`
