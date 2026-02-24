# Release Tracking — Design

**Date:** 2026-02-24

## Problem Statement

Filigree has no way to group work into releases. Epics and milestones track individual efforts but there's no container for "Release 1.4 is these epics plus these bug fixes." A release pack already exists in `templates_data.py` (Tier 3) but is not enabled.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Approach | Enable existing release pack | Full workflow already designed, just needs activation |
| Granularity | Link epics/milestones/bugs/tasks directly | No release_item wrappers; children have their own tracking |
| Known bug | Fix rolled_back category now | Clean enablement, small fix |

## What Already Exists

`templates_data.py` defines a complete `release` pack with two types:

**`release`** — 7 states + cancelled:
```
planning(O) → development(W) → frozen(W) → testing(W) → staged(W) → released(D)
                                                                   ↘ rolled_back(W)
                                                                      ↘ development(W)
```
Hard gate: `development → frozen` requires `version` field.

Fields: `version`, `target_date`, `changelog`, `release_manager`, `rollback_plan`

**`release_item`** — per-change verification tracking:
```
queued(O) → included(W) → verified(D)
         ↘ excluded(D)  ↘ excluded(D)
```

## Changes Required

### 1. Fix `rolled_back` category (filigree-284665)

`rolled_back` is currently `category: "done"` but has a transition back to `development`. This is inconsistent — a done state shouldn't transition to an active state.

**Fix:** Change `rolled_back` from `category: "done"` to `category: "wip"`. This correctly models "we rolled back and may re-enter development."

### 2. Update `suggested_children` for `release`

Current: `["release_item"]`

Updated: `["release_item", "epic", "milestone", "task", "bug", "feature"]`

This is advisory only — `parent_id` has no type enforcement in core.py. But updating the suggestion communicates that releases are expected to contain mixed work items. The principle is that most release content should be epics and milestones, but practically a release will also include unrelated bug fixes and small tasks.

### 3. Enable the release pack

Add `"release"` to the project's enabled packs. The pack requires `core` and `planning` (both already enabled).

## What We're NOT Doing

- Not using `release_item` wrappers — epics/milestones/bugs link directly as children
- Not building new tables or schema — releases are issues with a workflow
- Not adding new CLI/MCP/dashboard features — releases work through existing commands
- Not modifying the `release_item` type — it stays available for future use if per-change verification is needed

## Usage Pattern

```bash
# Create a release
filigree create "Release 1.4" --type=release
filigree update <release-id> --fields='{"target_date": "2026-03-15"}'

# Link work items as children
filigree update <epic-id> --parent=<release-id>
filigree update <milestone-id> --parent=<release-id>
filigree update <bug-id> --parent=<release-id>

# Track progress
filigree show <release-id>          # Shows children and their statuses
filigree transitions <release-id>    # Valid next states

# Advance through lifecycle
filigree update <release-id> --status=development
filigree update <release-id> --status=frozen --fields='{"version": "1.4.0"}'
filigree update <release-id> --status=testing
filigree update <release-id> --status=staged
filigree update <release-id> --status=released --fields='{"changelog": "..."}'
```

### Example: Release 1.4

```
Release 1.4 (type: release, status: planning)
├── Structural Refactoring (type: epic)       — production code god class splits
├── Test Suite Reboot (type: milestone)        — filigree-9f5968
├── filigree-abc123 (type: bug)                — some P1 hotfix
└── filigree-def456 (type: task)               — some small cleanup
```

## Implementation Steps

1. Fix `rolled_back` category in `templates_data.py` (`"done"` → `"wip"`)
2. Update `suggested_children` for `release` type
3. Enable the release pack for this project
4. Close `filigree-284665` (rolled_back bug)
5. Create "Release 1.4" and link the two epics
