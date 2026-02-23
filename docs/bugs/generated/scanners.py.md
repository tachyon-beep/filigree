## Summary
`_parse_toml` does not validate field types, which lets malformed scanner TOML crash `trigger_scan` with uncaught exceptions.

## Severity
- rule_id: type-error
- Severity: major
- Priority: P1

## Evidence
- `src/filigree/scanners.py:98` and `src/filigree/scanners.py:99` assign raw TOML values directly:
```py
args=scanner.get("args", []),
file_types=scanner.get("file_types", []),
```
- `src/filigree/scanners.py:55` calls `shlex.split(self.command)` but only catches `ValueError` at `src/filigree/scanners.py:56`; non-string `command` raises `TypeError`.
- `src/filigree/scanners.py:65` and `src/filigree/scanners.py:67` assume each arg has `.replace()`, so non-string list members raise `AttributeError`.
- `src/filigree/mcp_server.py:2077` catches only `ValueError` from `cfg.build_command(...)`, so these type errors escape tool handling.

## Root Cause Hypothesis
The scanner config parser enforces key presence but not schema types, while command construction assumes fully typed strings and only handles one exception class.

## Suggested Fix
Add strict schema validation in `_parse_toml`:
- require `name`, `description`, `command` to be `str`
- require `args` and `file_types` to be `list[str]`
- reject invalid files with a warning and return `None`

Also make `build_command` defensively wrap `TypeError`/`AttributeError` as `ValueError` so callers get `invalid_command` instead of an internal crash.

---
## Summary
Scanner identity is inconsistent: `list_scanners` exposes TOML `scanner.name`, but `trigger_scan` resolves scanners by filename stem.

## Severity
- rule_id: logic-error
- Severity: minor
- Priority: P2

## Evidence
- `src/filigree/scanners.py:95` sets config name from TOML (`name=scanner["name"]`).
- `src/filigree/scanners.py:73` exposes that name in `to_dict()`, used by scanner listing.
- `src/filigree/scanners.py:128` loads scanner by file path `f"{name}.toml"` (input name treated as filename).
- `src/filigree/mcp_server.py:1041` documents `trigger_scan.scanner` as “Scanner name (from list_scanners)”.
- `src/filigree/mcp_server.py:2016` calls `load_scanner(scanners_dir, scanner_name)`, and `src/filigree/mcp_server.py:2018` returns available names from listed configs (`s.name`).

## Root Cause Hypothesis
No invariant enforces that `[scanner].name` equals the TOML filename stem, so the listed identifier can diverge from the load key.

## Suggested Fix
In `scanners.py`, enforce one canonical identity:
- either validate `scanner.name == path.stem` during parse (reject otherwise), or
- ignore TOML `name` for lookup and store/use `path.stem` as canonical `ScannerConfig.name`.