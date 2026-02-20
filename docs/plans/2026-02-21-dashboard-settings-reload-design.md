# Dashboard Settings Gear with Soft Reload

**Date:** 2026-02-21
**Issue:** filigree-fb88d5

## Problem

Debugging multi-project dashboard issues requires restarting the server to
pick up registry changes or clear stale DB connections. Currently this means
killing the process and relaunching manually.

## Decision

Add a gear dropdown menu to the dashboard header and a `POST /api/reload`
endpoint for soft-reloading server state without process restart.

## Backend: `POST /api/reload`

Root-level endpoint (not project-scoped) that:

1. Calls `_project_manager.close_all()` — closes all cached DB connections
2. Re-reads the registry to discover new/removed projects
3. Re-registers projects, rebuilding `_paths` cache (connections reopen lazily)
4. Returns `{"ok": true, "projects": <count>}`

Uses existing `ProjectManager.close_all()`. No new methods on the class.

## Frontend: Gear dropdown

Replace the standalone theme toggle button with a gear icon that opens a
dropdown containing:

- **Reload server** — calls `POST /api/reload`, shows success/error feedback
  in the refresh indicator area, then triggers a full data refresh
- **Toggle theme** — existing `toggleTheme()` function, moved into the menu

Dropdown uses the existing `.popover` CSS class and closes on click-outside or
after action.

## Scope exclusions

- No hard process restart
- No template reload (separate mechanism exists)
- No HTML cache-busting
