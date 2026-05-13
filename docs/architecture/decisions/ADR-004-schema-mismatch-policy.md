# ADR-004: Schema Mismatch Policy

**Status**: Accepted
**Date**: 2026-05-13
**Deciders**: John (project lead)
**Context**: Senior-user MCP reviews found that `get_mcp_status` could report `SCHEMA_MISMATCH` while normal write tools still succeeded.

## Summary

`SCHEMA_MISMATCH` means the installed Filigree code is not safe to use for
normal project mutation against the opened database. In that state, diagnostic
tools may remain available, but writes must fail closed. If a schema version
difference is intentionally compatible, the product must not call it
`SCHEMA_MISMATCH`; it should be reported as advisory drift with explicit safe
capabilities.

## Context

The agent instructions say that when the MCP server detects a database schema
newer than the installed Filigree code, most tools return an error with
`code: SCHEMA_MISMATCH`, while `get_mcp_status` remains available as a safe
diagnostic. Later live reviews observed a contradictory state: status reported
schema mismatch, but create/update/close/report tools continued to mutate the
database.

That is a bad contract for agents. If the tool says "schema mismatch", agents
should not have to guess whether writes are safe. If writes are safe, the status
name must communicate compatibility, not mismatch.

## Decision

We will treat `SCHEMA_MISMATCH` as a hard safety boundary:

1. Normal write tools must fail with `code: SCHEMA_MISMATCH` when the opened
   database schema is newer than the installed code understands.
2. Safe diagnostic tools may remain available. At minimum, `get_mcp_status`
   should report the installed schema version, database schema version,
   compatibility state, binary path if available, and upgrade guidance.
3. If a future migration is known to be backward-compatible for a subset of
   operations, the status must not be `SCHEMA_MISMATCH`. Use a distinct advisory
   state such as `schema_drift` or `degraded_compatible`, and list which
   operations are safe.
4. Session-start hooks and diagnostics should identify which Filigree binary is
   producing a warning so a stale uv-tool dashboard cannot be confused with a
   healthy repo-local MCP process.

## Consequences

### Positive

- Agents can branch on `SCHEMA_MISMATCH` without ambiguity.
- Database mutation under unknown schema skew fails closed.
- Compatible drift remains possible, but must be named honestly.

### Negative

- Some operations that might have happened to work will be blocked until the
  user upgrades Filigree.
- The MCP server needs a clear distinction between diagnostic-safe and
  mutation-capable tools.

### Neutral

- This does not require every minor schema delta to be fatal. It requires fatal
  states to be called mismatch and advisory states to be called something else.

## Implementation Notes

- Add tests proving write tools fail during true schema mismatch.
- Keep `get_mcp_status` available in mismatch mode.
- Update docs and instructions to describe `schema_drift` or equivalent only if
  such a state is implemented.

## Related Decisions

- **Related to**: [ADR-002: API Generations and the Federation-Component Posture](./ADR-002-api-generations-and-federation-posture.md)
- **Related to**: [ADR-003: Operational Durability, Not Audit-Proof Records](./ADR-003-operational-durability-not-audit-proofing.md)

## References

- `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-f.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-e.md`
- `docs/plans/2026-05-12-mcp-senior-user-review-h.md`
