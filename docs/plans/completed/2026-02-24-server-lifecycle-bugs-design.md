# Server Lifecycle Bug Cluster — Design

**Date:** 2026-02-24
**Bugs:** filigree-11862e, filigree-ddceff (dup), filigree-f6c971, filigree-f56a78, filigree-186813
**Files:** `src/filigree/server.py`, `tests/test_server.py`

## Problem Summary

Five scanner-found bugs (4 unique) in the daemon lifecycle code in `server.py`:

1. **Config validation** (11862e + ddceff): `read_server_config()` doesn't validate JSON shape/types after parsing. Valid JSON with wrong structure crashes downstream.
2. **Start race** (f6c971): `start_daemon()` check-then-act on PID file is not serialized — concurrent calls can both pass the "already running?" check.
3. **PID ownership** (f56a78): `start_daemon()` and `daemon_status()` trust `is_pid_alive()` without verifying the PID belongs to filigree. Reused PIDs cause false positives.
4. **SIGKILL verification** (186813): `stop_daemon()` claims success after SIGKILL without confirming the process actually died.

## Fix Designs

### Fix 1: Config Validation

After JSON parse, validate:
- Top-level must be `dict` (else warn + return defaults)
- `port`: coerce to `int`, clamp to 1–65535, default on failure
- `projects`: must be `dict`, values must be `dict`; drop invalid entries

### Fix 2: Start Race Serialization

Wrap `start_daemon()` check-then-act in `fcntl.flock` on existing `server.lock`. Same pattern as `register_project()`.

### Fix 3: PID Ownership Verification

Replace bare `is_pid_alive()` calls with `verify_pid_ownership()` in:
- `start_daemon()` line 141: if alive but not owned, clean stale PID file and proceed
- `daemon_status()` line 216: if alive but not owned, report not running

### Fix 4: SIGKILL Verification

After SIGKILL + sleep, re-check `is_pid_alive()`. Split exception handling: `ProcessLookupError` = already dead (success), `PermissionError` = failure. Only claim success when confirmed dead.

## Testing

New test class `TestDaemonLifecycleBugFixes` covering:
- Config validation: non-dict JSON, bad port types, out-of-range port, non-dict projects
- Race serialization: verify `fcntl.flock` called during `start_daemon()`
- Ownership: stale PID (alive but wrong process) → start proceeds, status reports not running
- SIGKILL failure: process survives SIGKILL → stop returns failure

## Duplicate Closure

Close filigree-ddceff as duplicate of filigree-11862e.
