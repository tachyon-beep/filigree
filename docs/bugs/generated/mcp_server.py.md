## Summary
`trigger_scan` cooldown enforcement can be bypassed by concurrent requests, allowing duplicate scanner process spawns (`rule_id: race-condition`).

## Severity
- Severity: major
- Priority: P1

## Evidence
`/home/john/filigree/src/filigree/mcp_server.py:2053` checks cooldown, but the cooldown timestamp is only written later at `/home/john/filigree/src/filigree/mcp_server.py:2124`, after an `await` at `/home/john/filigree/src/filigree/mcp_server.py:2110`.

```py
last_trigger = _scan_cooldowns.get(cooldown_key, 0.0)
if now_mono - last_trigger < _SCAN_COOLDOWN_SECONDS:
    return rate_limited
...
await asyncio.sleep(0.2)
...
_scan_cooldowns[cooldown_key] = now_mono
```

Global mutable state is shared at `/home/john/filigree/src/filigree/mcp_server.py:73` with no locking.

## Root Cause Hypothesis
Cooldown check and cooldown write are not atomic. Two requests for the same `(project, scanner, file)` can both pass the check before either writes the cooldown entry.

## Suggested Fix
Guard check+set with an `asyncio.Lock` (global or per-key), and reserve cooldown before the first `await`/spawn. If spawn fails, clear or shorten the reservation so retries remain possible.

---
## Summary
`add_file_association` misclassifies “issue does not exist” as `validation_error` instead of `not_found` (`rule_id: error-handling`).

## Severity
- Severity: minor
- Priority: P3

## Evidence
In MCP dispatch, missing file is mapped to `not_found`, but missing issue is not pre-checked:

- File check: `/home/john/filigree/src/filigree/mcp_server.py:1930`
- Generic `ValueError` mapping to `validation_error`: `/home/john/filigree/src/filigree/mcp_server.py:1935`

Core raises `ValueError` for missing issue in this path:

- `/home/john/filigree/src/filigree/core.py:3204`

```py
# mcp_server.py
try:
    tracker.add_file_association(file_id, issue_id, assoc_type)
except ValueError as e:
    return {"code": "validation_error", ...}

# core.py
row = self.conn.execute("SELECT 1 FROM issues WHERE id = ?", (issue_id,)).fetchone()
if row is None:
    raise ValueError("Issue not found ...")
```

## Root Cause Hypothesis
The MCP layer collapses distinct failure causes (invalid assoc type vs missing issue) into one `ValueError` branch.

## Suggested Fix
In `mcp_server.py`, pre-check `issue_id` via `tracker.get_issue(issue_id)` and return `{"code": "not_found"}` on `KeyError`, then keep `ValueError -> validation_error` for true validation problems (like bad `assoc_type`).