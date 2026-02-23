## Summary
`start_daemon()` has a race that can delete the PID file of a successfully started daemon when two starts happen concurrently (`rule_id: race-condition`).

## Severity
- Severity: major
- Priority: P1

## Evidence
- `start_daemon()` does an unlocked check-then-start flow: `read_pid_file`/`is_pid_alive` precheck at `src/filigree/server.py:140` and process spawn at `src/filigree/server.py:156`.
- It writes a shared PID file immediately after spawn at `src/filigree/server.py:164`.
- On immediate child failure it unconditionally deletes that shared file at `src/filigree/server.py:169`.
- With two concurrent callers, loser process can hit the failure path and remove the winner’s PID file (both use the same `SERVER_PID_FILE`).

## Root Cause Hypothesis
Daemon startup is not serialized (no lock around precheck/spawn/pid-write/failure-cleanup), and cleanup does not verify PID-file ownership before unlinking.

## Suggested Fix
Add an inter-process startup lock (same `fcntl.flock` pattern used in registration) around the full start sequence. In the failure path, only unlink if PID file still points to `proc.pid` (compare current PID file content before removal).

---
## Summary
`start_daemon()` and `daemon_status()` accept any live PID as “the daemon,” so PID reuse/stale files can falsely report running and block startup (`rule_id: logic-error`).

## Severity
- Severity: major
- Priority: P1

## Evidence
- `start_daemon()` only checks `is_pid_alive(info["pid"])` at `src/filigree/server.py:141` before returning “already running” at `src/filigree/server.py:142`.
- `daemon_status()` likewise treats any live PID as running at `src/filigree/server.py:216`.
- The codebase already has ownership verification helper `verify_pid_ownership()` (`src/filigree/ephemeral.py:148`), and `stop_daemon()` uses it at `src/filigree/server.py:187`, showing identity checks are expected for safety.

## Root Cause Hypothesis
Liveness checks were implemented without identity validation, so stale/reused PID files can map to unrelated live processes.

## Suggested Fix
In both `start_daemon()` and `daemon_status()`, require `verify_pid_ownership(SERVER_PID_FILE, expected_cmd="filigree")` in addition to liveness. If PID is live but ownership check fails, treat it as stale/mismatched state and clear or ignore the PID file.

---
## Summary
`read_server_config()` does no schema/type validation for parsed JSON, causing downstream type crashes on valid-JSON-but-invalid-shape configs (`rule_id: type-error`).

## Severity
- Severity: major
- Priority: P2

## Evidence
- It directly trusts parsed fields: `port=data.get("port", DEFAULT_PORT)`, `projects=data.get("projects", {})` at `src/filigree/server.py:45` and `src/filigree/server.py:46`.
- `register_project()` assumes dict semantics (`config.projects.items()`) at `src/filigree/server.py:90`; malformed `projects` (e.g., list/string) will raise `AttributeError`.
- `meta.get(...)` is called at `src/filigree/server.py:93`, which also fails if `meta` is not dict-like.
- Related code explicitly validates schema shape in `src/filigree/dashboard.py:92` and `src/filigree/dashboard.py:95`, indicating this shape is required.

## Root Cause Hypothesis
Corruption handling only catches parse/IO errors, not semantic/schema mismatches after successful JSON parse.

## Suggested Fix
Harden `read_server_config()`:
- verify top-level is `dict`,
- coerce/validate `port` to `int` within valid range,
- require `projects` to be `dict[str, dict]` (or safely normalize invalid entries),
- on mismatch, log warning and fall back to `ServerConfig()` defaults.