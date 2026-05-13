# ADR-009: Response Shape Philosophy

**Status**: Accepted
**Date**: 2026-05-13
**Deciders**: John (project lead)
**Context**: Senior-user reviews repeatedly found response-shape drift across MCP, CLI, and HTTP living surfaces.

## Summary

Filigree will use predictable envelopes and slim-by-default payloads, with
explicit `response_detail` controls where callers need full records. Consistency
matters, but default responses should not be bloated just to avoid a follow-up
call in less common workflows.

## Context

Earlier reviews wanted mutation responses to return full post-mutation records
so agents could immediately continue. Later reviews noted that common writes and
lists became too chatty when every operation returned full `PublicIssue`
payloads. Reviews also found list-shaped tools returning bare arrays, empty
results using different shapes from success, and counters duplicated across
single-write responses.

The correct principle is not "always full" or "always tiny". It is predictable
shape plus caller-controlled detail.

## Decision

We will follow these response rules for living surfaces:

1. List-shaped tools return an envelope, normally `{items, has_more,
   next_offset?}` or a named equivalent for time cursors such as `next_since`.
   Bare arrays are not used for public list-shaped responses.
2. Batch tools return `{succeeded, failed, newly_unblocked?}` style envelopes,
   with `failed` always present.
3. Mutations return a slim operational result by default: the primary entity ID,
   operation status/result, and any immediately useful metadata.
4. Tools that often need the full post-mutation record should offer
   `response_detail=slim|full`.
5. Empty success states should share the same top-level envelope family as
   non-empty success states where practical.
6. Single-write tools should not return batch-ingest counters unless the
   operation is actually batch-like.
7. Compatibility aliases are allowed during migration, but docs must identify
   the canonical shape.

## Consequences

### Positive

- Agents can reuse parsing logic across tools.
- Common calls remain reasonably small.
- Full records remain available where they materially reduce follow-up calls.

### Negative

- Some existing response shapes will need migration or compatibility aliases.
- Tests must distinguish canonical fields from compatibility fields.

### Neutral

- ADR-002 still governs named HTTP generation compatibility. This ADR describes
  the living surface direction.

## Implementation Notes

- Add contract tests for representative list, batch, mutation, empty, and error
  shapes.
- Prefer adding `response_detail` before changing a widely used response from
  full to slim.
- Keep error envelopes stable: `{error, code, details?}` plus tool-specific
  recovery fields where useful.

## Related Decisions

- **Related to**: [ADR-002: API Generations and the Federation-Component Posture](./ADR-002-api-generations-and-federation-posture.md)
- **Related to**: [ADR-006: MCP Unknown Parameter Validation](./ADR-006-mcp-unknown-parameter-validation.md)

## References

- `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`
- `docs/plans/2026-05-12-mcp-senior-user-review-h.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-g.md`
