## Summary
`error-handling`: `create-plan` can still crash on malformed nested `steps` data because the CLI only validates the top-level JSON shape.

## Severity
- Severity: major
- Priority: P1

## Evidence
`/home/john/filigree/src/filigree/cli_commands/planning.py:237-244` checks that `phases` is a list and each phase is a dict, but it never validates that `phase["steps"]` is a list or that each step entry is a dict.
`/home/john/filigree/src/filigree/cli_commands/planning.py:246-260` then calls `db.create_plan(...)` and only catches `ValueError` / `TypeError`, with an explicit comment saying `AttributeError` should be treated as a programmer bug.
`/home/john/filigree/src/filigree/db_planning.py:371-378` immediately does `step_data.get(...)` for every entry in `phase_data.get("steps", [])`, so inputs like `{"steps": ["not-a-dict"]}` or `{"steps": {"title": "x"}}` raise `AttributeError` before any friendly validation message is produced.

## Root Cause Hypothesis
The CLI schema validation stops one level too early. It assumes that once each phase is a dict, the nested `steps` payload is well-formed, but the DB layer still dereferences each step as a mapping.

## Suggested Fix
In `planning.py`, extend the preflight validation to enforce:
- each `steps` value is a list when present
- each step entry is a dict
- optionally each `deps` value is a list when present

Then keep treating those failures as user validation errors instead of letting `AttributeError` escape.

---
## Summary
`logic-error`: `changes --since` accepts arbitrary timezone offsets but compares them lexically against UTC strings, which can return the wrong event set.

## Severity
- Severity: major
- Priority: P1

## Evidence
`/home/john/filigree/src/filigree/cli_commands/planning.py:16-34` documents that stored timestamps are UTC `+00:00` strings and that SQLite compares them lexically, but `_normalize_iso_timestamp()` only rewrites a trailing `Z`; any other offset is returned unchanged.
`/home/john/filigree/src/filigree/cli_commands/planning.py:280-282` passes that raw string directly into `db.get_events_since(...)`.
`/home/john/filigree/src/filigree/db_events.py:103-110` performs `WHERE e.created_at > ?` on the `TEXT` timestamp column, so ordering is string-based, not instant-based.

## Root Cause Hypothesis
The helper fixed only the `Z` vs `+00:00` mismatch, but not the broader problem that offset-bearing ISO strings must be normalized to the same UTC representation before a lexical SQLite comparison is safe.

## Suggested Fix
Parse the timestamp, normalize it to UTC, and serialize it back to the DB’s canonical format before querying. Also decide how naive timestamps should behave:
- either reject them
- or interpret them as UTC and append `+00:00`

A concrete approach is `dt = datetime.fromisoformat(...); normalized = dt.astimezone(UTC).isoformat()`.

---
## Summary
`performance`: `changes` accepts negative `--limit` values, and those flow straight into SQLite `LIMIT`, where `-1` means “no limit”.

## Severity
- Severity: minor
- Priority: P3

## Evidence
`/home/john/filigree/src/filigree/cli_commands/planning.py:276` declares `--limit` as `type=int`, unlike nearby commands that use bounded `click.IntRange(...)`.
`/home/john/filigree/src/filigree/cli_commands/planning.py:281-282` forwards that value unchanged to `db.get_events_since(...)`.
`/home/john/filigree/src/filigree/db_events.py:103-110` binds the value directly into `LIMIT ?`.
For comparison, the analogous event-history CLI already constrains its limit with `click.IntRange(min=0)` at `/home/john/filigree/src/filigree/cli_commands/meta.py:178-186`.

## Root Cause Hypothesis
This command skipped the same non-negative limit guard used elsewhere, so a mistyped or hostile `--limit -1` turns a bounded incremental query into a full-table scan/dump.

## Suggested Fix
Change the option type to `click.IntRange(min=0)` or explicitly reject negative values before calling `db.get_events_since(...)`. If you want parity with the rest of the CLI, mirror the `meta.events` option definition.