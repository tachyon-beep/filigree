## Summary
Windows venv installs can write an unusable MCP command because `.exe` is never checked in sibling-path fallbacks (`logic-error`).

## Severity
- Severity: major
- Priority: P1

## Evidence
`/home/john/filigree/src/filigree/install.py:106` and `/home/john/filigree/src/filigree/install.py:113` build candidates as `... / "filigree-mcp"` only, then `/home/john/filigree/src/filigree/install.py:116` falls back to bare `"filigree-mcp"`.

That means on Windows (where the file is commonly `filigree-mcp.exe`), a non-activated venv can miss the real executable and persist a command that depends on PATH.

## Root Cause Hypothesis
The fallback resolution logic is POSIX-centric and does not account for Windows executable suffixes (`.exe`, and potentially PATHEXT-based wrappers).

## Suggested Fix
In `_find_filigree_mcp_command`, when checking sibling candidates, probe Windows executable variants (at minimum `filigree-mcp.exe`) before falling back to bare command. Prefer `is_file()` checks over `exists()` for command candidates.

---
## Summary
`run_doctor` can report Codex MCP as configured when it is only mentioned in comments/text (`logic-error`).

## Severity
- Severity: minor
- Priority: P2

## Evidence
`/home/john/filigree/src/filigree/install.py:950-953` checks:
- read file text
- `if "[mcp_servers.filigree]" in content: ... passed`

This is a raw substring check, so commented-out or incidental text can produce a false pass. In contrast, the installer’s own presence check uses TOML parsing at `/home/john/filigree/src/filigree/install.py:257-261`.

## Root Cause Hypothesis
Health-check logic uses a cheap string heuristic instead of structural TOML parsing, diverging from installer behavior.

## Suggested Fix
Parse `.codex/config.toml` with `tomllib.loads` in `run_doctor` and verify `parsed.get("mcp_servers", {}).get("filigree")` exists as a table/dict; on TOML decode error, report invalid config.

---
## Summary
Doctor’s “absolute path” validation misses Windows backslash paths for MCP and hook binaries (`logic-error`).

## Severity
- Severity: minor
- Priority: P2

## Evidence
`/home/john/filigree/src/filigree/install.py:906-909`:
- absolute-path validation is gated by `if "/" in mcp_command ...`

`/home/john/filigree/src/filigree/install.py:979-981`:
- same pattern for hooks: `if hook_binary and "/" in hook_binary ...`

Windows absolute paths like `C:\tools\filigree.exe` do not contain `/`, so stale binaries bypass validation.

## Root Cause Hypothesis
Path-type detection is implemented via slash substring checks rather than path semantics, which is not cross-platform.

## Suggested Fix
Replace slash checks with robust absolute-path detection (for example, `Path(cmd).is_absolute()` plus Windows-drive handling as needed), then apply existence checks consistently for both MCP command and hook binary.