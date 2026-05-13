# ADR-006: MCP Unknown Parameter Validation

**Status**: Accepted
**Date**: 2026-05-13
**Deciders**: John (project lead)
**Context**: Senior-user review G found MCP tools silently ignored plausible but unsupported parameters.

## Summary

MCP tools must reject unknown parameters with a validation error. Filigree will
not silently ignore extra MCP arguments and will not rely on soft warnings for
this class of mistake.

## Context

Agents compose tool calls from nearby examples, related tools, and natural API
expectations. Review G confirmed that plausible unsupported parameters such as
`get_ready(priority_min=...)`, `export_jsonl(label=...)`, and
`update_issue(add_labels=...)` could be accepted by the MCP layer and ignored by
the target handler.

That is worse than a hard error. A successful response makes the agent believe
its filter or mutation was applied.

## Decision

We will enforce strict MCP input schemas:

1. Unknown MCP parameters are invalid.
2. The error code is `VALIDATION`.
3. The error message names the unknown parameter and the target tool.
4. Tool schemas should set or generate the equivalent of
   `additionalProperties: false`.
5. If a parameter is likely to be useful, it should be implemented or explicitly
   documented as unsupported; it must not be accepted and ignored.

## Consequences

### Positive

- Agents fail fast when they pass the wrong shape.
- Filters and mutations cannot silently no-op.
- Tool metadata becomes a stronger contract.

### Negative

- Some forgiving clients may need to stop sending unused fields.
- The MCP schema generation path needs stricter tests.

### Neutral

- This decision applies to MCP tool inputs. HTTP compatibility remains governed
  by ADR-002 and generation-specific contracts.

## Implementation Notes

- Add schema tests that pass known bogus parameters to representative tools.
- Prefer central validation so new tools inherit the behavior automatically.
- Keep error payloads aligned with existing `ErrorResponse` conventions.

## Related Decisions

- **Related to**: [ADR-002: API Generations and the Federation-Component Posture](./ADR-002-api-generations-and-federation-posture.md)
- **Related to**: [ADR-009: Response Shape Philosophy](./ADR-009-response-shape-philosophy.md)

## References

- `docs/plans/2026-05-13-mcp-senior-user-review-master-checklist.md`
- `docs/plans/2026-05-09-mcp-senior-user-review-g.md`
