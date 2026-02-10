# Pattern 2 + DX Enrichment — Design

Date: 2026-02-14

## Pattern 2: Transition Enforcement Ripple Effects

### 2A: Add `reopen_issue` MCP Tool + Core Method

Add `KeelDB.reopen_issue(id, actor)` that calls `update_issue(id, status=initial_state, _skip_transition_check=True)`. Also clears `closed_at`. Add MCP tool `reopen_issue` with `id` and `actor` params.

### 2B: Claim = Assign Only (No Status Change)

`claim_issue` sets assignee only, does not change status. `release_claim` clears assignee only, does not change status. `claim_next` unchanged in selection logic, but the claim it performs is assign-only. Update MCP descriptions accordingly.

### 2C: Close keel-b65fc8 and keel-0a0e56

Both are resolved by the claim semantics change.

## Pattern 1: DX Enrichment

### keel-bcfc94: Enrich missing_fields in get_valid_transitions

Change missing_fields from string list to objects with field metadata (name, description, type, options).

### keel-4df44e: get_workflow_guide type-to-pack fallback

When pack not found, check if it's a type name and suggest the correct pack in the error message.

### keel-dea118: validate_issue checks upcoming requirements

Add `upcoming_requirements` to validate_issue response — fields needed for the next reachable transitions.

### keel-ea3b72: get_template includes states and transitions

Add states (with categories) and transitions to get_template response.

### keel-971846: update_issue returns changed_fields

Add `changed_fields` list to update_issue MCP response.

### keel-8500b6: claim_next explains selection

Add `selection_reason` string to claim_next MCP response.

### keel-13079e: create_plan specific validation errors

Specify which field failed in create_plan validation errors.

### keel-ad4c4f: get_plan shows progress detail

Add per-phase completed/total counts to get_plan response.
