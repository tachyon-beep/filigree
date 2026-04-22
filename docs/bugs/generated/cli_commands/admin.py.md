## Summary
`filigree init` and `filigree doctor --fix` reopen legacy databases with the wrong default prefix, so schema repair can seed or operate under a foreign project identity

## Severity
- Rule ID: logic-error
- Severity: major
- Priority: P1

## Evidence
`src/filigree/cli_commands/admin.py:48-53` opens an existing project with `prefix=config.get("prefix", "filigree")`, and the same hardcoded fallback is repeated in the schema-fix path at `src/filigree/cli_commands/admin.py:326-330`. That bypasses the safer constructor documented in `src/filigree/core.py:455-470`, which exists specifically because legacy installs without an explicit `prefix` must fall back to `filigree_dir.parent.name`, not `"filigree"`. `src/filigree/core.py:638-646` then shows `initialize()` will create the auto-seeded `Future` release with whatever `self.prefix` was passed. The regression test at `tests/core/test_project_anchor.py:492-524` captures the concrete failure mode: the `"filigree"` fallback makes a legacy project behave like a foreign project and causes write paths to hit `WrongProjectError`.

## Root Cause Hypothesis
These two admin paths were written against `read_config()`'s defaulted dict instead of the newer `FiligreeDB.from_filigree_dir()` logic, so they reintroduced the exact legacy-prefix bug the core layer already fixed elsewhere.

## Suggested Fix
In both places, stop constructing `FiligreeDB` with `config.get("prefix", "filigree")`. Use `FiligreeDB.from_filigree_dir(filigree_dir)` or replicate its raw-prefix fallback logic so legacy projects inherit `cwd.name` when `config.json` lacks `prefix`.

---
## Summary
`filigree init` does not backfill or refresh `.filigree.conf` when `.filigree/` already exists, leaving legacy projects without the v2 anchor even after rerunning the explicit migration command

## Severity
- Rule ID: logic-error
- Severity: major
- Priority: P1

## Evidence
The existing-project branch at `src/filigree/cli_commands/admin.py:45-75` migrates the DB and optionally updates `config.json`, then returns. It never calls `write_conf(...)`. The fresh-init branch does write the anchor at `src/filigree/cli_commands/admin.py:86-97`. Core discovery explicitly says backfill must happen through an explicit write path like `filigree init` at `src/filigree/core.py:198-200`, and `src/filigree/cli_common.py:32-34` says normal discovery is read-only and will not create the conf for you.

## Root Cause Hypothesis
The idempotent "already exists" fast path was extended for migrations and config updates, but the later v2 anchor-migration step was only added to the fresh-install path.

## Suggested Fix
When `.filigree/` already exists, synthesize the same `conf_data` and call `write_conf(cwd / CONF_FILENAME, conf_data)` if the anchor is missing. If the anchor already exists, refresh its contents from current config so rerunning `init` keeps the explicit anchor in sync.

---
## Summary
`filigree install` reports component failures but still exits successfully, so automation cannot detect broken installs

## Severity
- Rule ID: error-handling
- Severity: major
- Priority: P2

## Evidence
`src/filigree/cli_commands/admin.py:177-238` accumulates `(name, ok, msg)` tuples, prints them, and always finishes with a success count. There is no `sys.exit(1)` or `ClickException` when any selected component returns `ok=False`. That failure path is real: `src/filigree/install_support/integrations.py:298-307` makes `install_codex_mcp()` return `False` on malformed `~/.codex/config.toml`, so `filigree install --codex` can print a failure and still exit 0.

## Root Cause Hypothesis
The command treats installer failures as informational status lines instead of command failure, so the human-readable summary is correct but the process contract is wrong.

## Suggested Fix
After printing results, exit non-zero when any requested install step returned `ok=False`. That preserves the current output while making `&&`, CI jobs, and scripts fail correctly.

---
## Summary
`filigree import` only catches `sqlite3.IntegrityError`, so common SQLite failures like `OperationalError` escape the clean CLI error path

## Severity
- Rule ID: error-handling
- Severity: minor
- Priority: P2

## Evidence
The import handler at `src/filigree/cli_commands/admin.py:524-529` catches `sqlite3.IntegrityError` but not the broader `sqlite3.Error` family. Meanwhile `src/filigree/db_meta.py:986-989` rolls back and re-raises any exception from the transaction body, so lock errors, missing-table errors, or disk I/O failures can bubble out uncaught. The MCP implementation already treats this as a broader SQLite-family problem at `src/filigree/mcp_tools/meta.py:510-519`, and its test at `tests/mcp/test_tools.py:1329-1339` explicitly expects `sqlite3.OperationalError` to be handled gracefully.

## Root Cause Hypothesis
The CLI catch block was written around duplicate-row conflicts and never widened when `import_jsonl()` began surfacing general SQLite failures from the transaction body.

## Suggested Fix
Change the CLI handler to catch `sqlite3.Error` instead of only `sqlite3.IntegrityError`, keeping the existing `Import failed: ...` behavior for all SQLite failures.