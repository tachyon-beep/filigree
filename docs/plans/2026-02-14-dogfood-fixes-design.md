# Dogfood Bug Fixes — Design

Date: 2026-02-14

## 1. State Transition Enforcement (keel-ab92aa, P1)

**Problem**: Undefined transitions (e.g. triage→verifying) are silently allowed with just a warning event. Workflow templates are decorative.

**Fix**: In `templates.py:validate_transition`, return `allowed=False` when a transition is not in the transition table for a known type. In `core.py:update_issue`, this causes a `ValueError` with the list of valid next states.

- Unknown types (no template) still allow all transitions (WFT-FR-016)
- Hard/soft field enforcement unchanged
- Error message includes valid transitions for agent self-correction

## 2. Allow Reparenting (keel-908d0e, P1)

**Problem**: `update_issue` doesn't accept `parent_id`, so MCP calls with it silently do nothing.

**Fix**: Add `parent_id` parameter to `core.py:update_issue`. Validate parent exists, check no cycles (parent can't be descendant). Expose in MCP schema and CLI `--parent` flag.

## 3. Close Pagination Issue (keel-2d1d0b, P2)

Already fixed — `list_issues` has `limit=100, offset=0` defaults. Close the issue.

## 4. Housekeeping

- Close 5 positive-feedback issues (keel-a0cd06, keel-7ceb24, keel-e31f0a, keel-2b180e, keel-885345)
- Deduplicate keel-a67afa / keel-8500b6 (close keel-a67afa, reference keel-8500b6)
