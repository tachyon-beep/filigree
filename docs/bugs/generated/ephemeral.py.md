## Summary
[logic-error] `find_available_port()` retries one fewer deterministic candidate than documented/intended.

## Severity
- Severity: minor
- Priority: P3

## Evidence
`src/filigree/ephemeral.py:48` says it tries deterministic port, then up to `PORT_RETRIES` sequential ports.  
`src/filigree/ephemeral.py:23` sets `PORT_RETRIES = 5`.  
`src/filigree/ephemeral.py:57` uses `for offset in range(PORT_RETRIES):`, which checks only 5 total ports (`base..base+4`), not deterministic + 5 retries.  
`src/filigree/ephemeral.py:64` then falls back to random OS-assigned port.

## Root Cause Hypothesis
The loop bound uses `PORT_RETRIES` as total attempts instead of retries-after-first, creating an off-by-one mismatch with the documented behavior and constant naming.

## Suggested Fix
Change iteration to include the base plus retries, e.g. `for offset in range(PORT_RETRIES + 1):`, or rename/update docs to match the current behavior if current behavior is truly intended.

---
## Summary
[error-handling] `cleanup_legacy_tmp_files()` can raise `PermissionError` and abort dashboard startup.

## Severity
- Severity: major
- Priority: P1

## Evidence
`src/filigree/ephemeral.py:230` / `src/filigree/ephemeral.py:231` call `Path("/tmp", name).unlink(missing_ok=True)` without exception handling.  
`src/filigree/hooks.py:250` calls `cleanup_legacy_tmp_files()` before startup flow proceeds, with no local try/except.  
In sticky `/tmp`, deleting another user’s file can raise `PermissionError` even with `missing_ok=True`.

## Root Cause Hypothesis
The code assumes `missing_ok=True` makes deletion always safe, but that only suppresses `FileNotFoundError`, not permission-related errors.

## Suggested Fix
Wrap each unlink in `try/except OSError` (or specifically `PermissionError`) and continue, logging at debug/warn level so startup is not blocked by legacy cleanup failures.

---
## Summary
[api-misuse] PID `0` is treated as a valid live process, causing false “running” detections from corrupt PID files.

## Severity
- Severity: major
- Priority: P2

## Evidence
`src/filigree/ephemeral.py:102` parses legacy PID text with `int(text)` and accepts `0`.  
`src/filigree/ephemeral.py:110` uses `os.kill(pid, 0)`; for `pid == 0`, POSIX checks process-group permissions, not a specific process.  
`src/filigree/hooks.py:257`-`src/filigree/hooks.py:260` uses `is_pid_alive(pid_info["pid"])` to decide “dashboard already running,” so a `0` PID can produce a false positive when combined with a listening port value.

## Root Cause Hypothesis
No PID validity guard (`pid > 0`) exists before liveness checks, so special PID semantics leak into normal daemon health logic.

## Suggested Fix
Reject non-positive PIDs in `read_pid_file()` and/or `is_pid_alive()`:
- treat `pid <= 0` as invalid/dead
- return `None` for such PID-file contents to classify them as corrupt/stale.