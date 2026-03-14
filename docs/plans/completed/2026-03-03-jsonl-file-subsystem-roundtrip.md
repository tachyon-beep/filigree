# JSONL File Subsystem Round-Trip Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `filigree export` / `filigree import` round-trip the full persisted project state, including `file_records`, `scan_findings`, `file_associations`, and `file_events`, while preserving `merge=True` semantics.

**Architecture:** Extend the JSONL transfer format with explicit file-domain record types and import them in foreign-key-safe order. The import path must reconcile file identities by path when `merge=True`, dedupe non-uniqued history rows (`comments`, `file_events`), and keep the whole restore transactional so partially imported file data never leaks into the tracker.

**Tech Stack:** Python 3.12, SQLite, Click CLI, MCP tools, pytest

**Prerequisites:**
- Existing import fixes for parent-link ordering, `Future` reconciliation, and comment merge idempotency should remain intact.
- Use the current repo venv via `uv run pytest ...`.

---

### Task 1: Write failing round-trip tests for file-domain export/import

**Files:**
- Modify: `tests/core/test_crud.py`
- Reference: `tests/core/test_files.py`
- Reference: `src/filigree/db_meta.py`

**Step 1: Add a fixture-level round-trip test that exports file-domain rows**

Add a new test near the JSONL tests that creates:
- one issue
- one file via `register_file(...)`
- one finding via direct `scan_findings` insert or `process_scan_results(...)`
- one file association
- one file event

Suggested test skeleton:

```python
def test_import_roundtrip_preserves_file_domain_rows(self, db: FiligreeDB, tmp_path: Path) -> None:
    issue = db.create_issue("Bug for file")
    file_rec = db.register_file("src/example.py", language="python", metadata={"owner": "core"})
    db.conn.execute(
        "INSERT INTO scan_findings "
        "(id, file_id, issue_id, scan_source, rule_id, severity, status, message, suggestion, "
        "scan_run_id, line_start, line_end, seen_count, first_seen, updated_at, last_seen_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "test-sf-1",
            file_rec.id,
            issue.id,
            "ruff",
            "F401",
            "medium",
            "open",
            "unused import",
            "",
            "run-1",
            10,
            10,
            1,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            '{"source":"test"}',
        ),
    )
    db.conn.execute(
        "INSERT INTO file_associations (file_id, issue_id, assoc_type, created_at) VALUES (?, ?, ?, ?)",
        (file_rec.id, issue.id, "bug_in", "2026-01-01T00:00:00+00:00"),
    )
    db.conn.execute(
        "INSERT INTO file_events (file_id, event_type, field, old_value, new_value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (file_rec.id, "file_metadata_update", "language", "", "python", "2026-01-01T00:00:00+00:00"),
    )
    db.conn.commit()

    out = tmp_path / "file-roundtrip.jsonl"
    export_count = db.export_jsonl(out)

    fresh = FiligreeDB(tmp_path / "fresh.db", prefix="test")
    fresh.initialize()
    import_count = fresh.import_jsonl(out)

    assert import_count == export_count
    assert fresh.get_file(file_rec.id).path == "src/example.py"
    assert fresh.conn.execute("SELECT COUNT(*) FROM scan_findings").fetchone()[0] == 1
    assert fresh.conn.execute("SELECT COUNT(*) FROM file_associations").fetchone()[0] == 1
    assert fresh.conn.execute("SELECT COUNT(*) FROM file_events").fetchone()[0] == 1
```

**Why this test:** It captures the missing functionality directly and proves JSONL now restores more than issue-only state.

**Step 2: Add a `merge=True` idempotency test for file-domain rows**

Add a test that imports the same JSONL twice with `merge=True` and asserts:
- `file_records` stays at 1
- `scan_findings` stays at 1
- `file_associations` stays at 1
- `file_events` stays at 1
- second import count is `0`

**Why this test:** It prevents “merge but duplicate history” regressions like the existing comment bug.

**Step 3: Add a `merge=True` path-conflict reconciliation test**

Create this scenario:
- destination DB already contains `src/example.py` but under a different `file_records.id`
- import JSONL from another DB with same path but different file ID
- assert imported findings and associations attach to the existing destination file row, not the stale source ID

Suggested assertion shape:

```python
dest_file = fresh.get_file_by_path("src/example.py")
assert dest_file is not None
finding = fresh.conn.execute("SELECT file_id FROM scan_findings WHERE rule_id = 'F401'").fetchone()
assert finding["file_id"] == dest_file.id
```

**Why this test:** This is the tricky merge bug that a naive “just export more tables” change will miss.

**Step 4: Run the targeted tests to verify RED**

Run:

```bash
uv run pytest tests/core/test_crud.py -q -k "roundtrip and (file or merge)"
```

Expected output:

```text
FAILED tests/core/test_crud.py::...
```

**Definition of Done:**
- [ ] Round-trip test exists for all four file-domain tables
- [ ] Merge idempotency test exists
- [ ] Path-conflict reconciliation test exists
- [ ] Tests fail for the current missing export/import behavior

---

### Task 2: Extend JSONL export with file-domain record types

**Files:**
- Modify: `src/filigree/db_meta.py`
- Test: `tests/core/test_crud.py`

**Step 1: Add new export sections in deterministic order**

Extend `export_jsonl()` to emit these new record types after issues and before associations/history rows:
- `_type = "file_record"`
- `_type = "scan_finding"`
- `_type = "file_association"`
- `_type = "file_event"`

Recommended export order:
1. `issue`
2. `file_record`
3. `scan_finding`
4. `dependency`
5. `label`
6. `comment`
7. `event`
8. `file_association`
9. `file_event`

Use SQL ordering that is stable and helps diffs:

```python
for row in self.conn.execute("SELECT * FROM file_records ORDER BY path").fetchall():
    record = dict(row)
    record["_type"] = "file_record"
    f.write(json.dumps(record, default=str) + "\n")
    count += 1
```

**Why this order:** Import will need issues before `scan_findings.issue_id`, file records before `scan_findings.file_id`, and both issues/files before `file_association`.

**Step 2: Keep record payloads lossless**

Do not strip these columns:
- `file_records.metadata`
- `scan_findings.metadata`
- `scan_findings.scan_run_id`
- `scan_findings.last_seen_at`
- `file_events.old_value` / `new_value`

**Why:** JSONL is positioned as backup/migration, so dropping metadata would be silent corruption.

**Step 3: Run the export-focused test slice**

Run:

```bash
uv run pytest tests/core/test_crud.py -q -k "export and file"
```

Expected output:

```text
PASSED
```

**Definition of Done:**
- [ ] Export emits the four new file-domain record types
- [ ] Export count includes file-domain rows
- [ ] Metadata-bearing columns survive export

---

### Task 3: Implement foreign-key-safe import with file ID reconciliation

**Files:**
- Modify: `src/filigree/db_meta.py`
- Reference: `src/filigree/db_schema.py`
- Test: `tests/core/test_crud.py`

**Step 1: Extend the import parser buckets**

Add import buckets for:

```python
file_records: list[dict[str, Any]] = []
scan_findings: list[dict[str, Any]] = []
file_associations: list[dict[str, Any]] = []
file_events: list[dict[str, Any]] = []
```

and classify by `_type`.

**Step 2: Build `source_file_id -> dest_file_id` reconciliation**

Import `file_records` before any file-dependent tables. Track a mapping:

```python
file_id_map: dict[str, str] = {}
```

Import rules:
- `merge=False`: insert exact row; destination ID must equal source ID
- `merge=True`:
  - first try exact insert
  - if skipped because path already exists under a different ID, look up by `path` and map source ID to destination ID
  - if exact ID already exists, map source ID to itself

Recommended logic:

```python
cursor = self.conn.execute(
    f"INSERT {conflict} INTO file_records (id, path, language, file_type, first_seen, updated_at, metadata) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)",
    (...),
)
if cursor.rowcount > 0:
    file_id_map[src_id] = src_id
else:
    existing = self.conn.execute("SELECT id FROM file_records WHERE path = ?", (record["path"],)).fetchone()
    if existing is None:
        existing = self.conn.execute("SELECT id FROM file_records WHERE id = ?", (src_id,)).fetchone()
    if existing is None:
        raise sqlite3.IntegrityError(...)
    file_id_map[src_id] = existing["id"]
```

**Why this matters:** Without remapping, `scan_findings.file_id` and `file_associations.file_id` will point at nonexistent source IDs during merge import.

**Step 3: Import `scan_findings` through the remap**

Before insert:
- rewrite `record["file_id"] = file_id_map[record["file_id"]]`
- leave `issue_id` intact; issues are already imported

Merge semantics:
- `merge=False`: direct insert
- `merge=True`: `INSERT OR IGNORE` is acceptable because the schema already has both a PK and a natural dedupe index
- if skipped, do not count it

**Step 4: Import `file_associations` and `file_events` through the remap**

For `file_association`:
- rewrite `file_id` via `file_id_map`
- use `INSERT OR IGNORE` under `merge=True` because table already has `UNIQUE(file_id, issue_id, assoc_type)`

For `file_event`:
- rewrite `file_id` via `file_id_map`
- do **not** rely on `INSERT OR IGNORE`, because there is no uniqueness constraint
- use `WHERE NOT EXISTS` under `merge=True` on natural history fields:

```sql
WHERE NOT EXISTS (
  SELECT 1 FROM file_events
  WHERE file_id = ? AND event_type = ? AND field = ? AND old_value = ? AND new_value = ? AND created_at = ?
)
```

**Step 5: Keep import transactional**

Do all file-domain inserts inside the same `try/except` rollback block already used by issue-domain imports.

**Step 6: Run focused tests to verify GREEN**

Run:

```bash
uv run pytest tests/core/test_crud.py -q -k "roundtrip and (file or merge)"
```

Expected output:

```text
PASSED
```

**Definition of Done:**
- [ ] File records import before dependent rows
- [ ] Merge imports reconcile file IDs by path
- [ ] File events are deduped under `merge=True`
- [ ] No partial writes survive a failing import

---

### Task 4: Cover CLI and MCP surfaces so backup/migration contract is true everywhere

**Files:**
- Modify: `tests/mcp/test_tools.py`
- Modify: `tests/cli/test_admin_commands.py`
- Modify: `src/filigree/mcp_tools/meta.py` (description only if wording needs correction)
- Modify: `src/filigree/types/api.py` (only if response types need extension)

**Step 1: Add an MCP round-trip regression**

Create an MCP-level test that:
- registers a file
- inserts a finding + association
- calls `export_jsonl`
- imports into a fresh tracker with `merge=True` or `merge=False` as appropriate
- asserts file-domain counts survive

**Why this test:** It validates the user-facing “backup/migration” contract, not just the DB mixin.

**Step 2: Update/help text only if needed**

If implementation remains “full project data”, keep the wording and ensure it is now true.
If any intentional exclusions remain, explicitly document them in:
- `src/filigree/mcp_tools/meta.py`
- `src/filigree/cli_commands/admin.py`

Preferred outcome for this task: no exclusions remain.

**Step 3: Run the public-surface test slice**

Run:

```bash
uv run pytest tests/cli/test_admin_commands.py tests/mcp/test_tools.py -q -k "import_jsonl or export_jsonl or roundtrip"
```

Expected output:

```text
PASSED
```

**Definition of Done:**
- [ ] MCP path verifies file-domain round-trip
- [ ] CLI/MCP wording matches actual behavior
- [ ] No structured-error regressions

---

### Task 5: Add a dedicated regression for `merge=True` with pre-existing file rows

**Files:**
- Modify: `tests/core/test_crud.py`
- Reference: `src/filigree/db_schema.py`

**Step 1: Create a destination DB with an existing file by path**

Use one DB as source and another as destination:
- source file ID: `src-f1`
- destination file ID: `dst-f1`
- shared path: `src/example.py`

After import with `merge=True`, assert:
- only one `file_records` row exists for that path
- `scan_findings.file_id` uses `dst-f1`
- `file_events.file_id` uses `dst-f1`
- `file_associations.file_id` uses `dst-f1`

**Why this deserves its own test:** It exercises the identity-reconciliation mechanism directly and protects against future refactors “simplifying” it away.

**Step 2: Run just that test**

Run:

```bash
uv run pytest tests/core/test_crud.py::TestImportJsonl::test_import_merge_reconciles_file_ids_by_path -q
```

Expected output:

```text
PASSED
```

**Definition of Done:**
- [ ] Imported rows never reference orphaned source file IDs
- [ ] Path-identity merge behavior is explicit and stable

---

### Task 6: Final verification and doc sanity check

**Files:**
- Modify: `tests/util/test_module_split.py` (only if module-split coverage should assert new behavior)
- Optional Docs: `docs/api-reference.md`, `docs/cli.md`

**Step 1: Run the full relevant verification suite**

Run:

```bash
uv run pytest tests/core/test_crud.py tests/core/test_files.py tests/mcp/test_tools.py tests/cli/test_admin_commands.py tests/util/test_module_split.py -q
```

Expected output:

```text
PASSED
```

**Step 2: Sanity-check import/export counts**

Run a small manual smoke check in a temp DB or add an assertion inside tests:
- export count equals imported count for fresh restore
- second import with `merge=True` reports `0` for already-present file-domain rows

**Step 3: If needed, update docs**

Only update docs if they currently under-specify or over-promise the JSONL scope:
- `docs/cli.md`
- `docs/api-reference.md`

**Definition of Done:**
- [ ] Full verification suite passes
- [ ] Fresh restore preserves file subsystem
- [ ] Merge restore is idempotent
- [ ] User-facing docs match reality

---

## Notes for Execution

- Keep the import order explicit and code-commented. This area now has multiple FK-sensitive domains.
- Do not change the JSONL envelope format beyond adding new `_type` values; backward compatibility matters.
- Preserve the existing issue-domain import fixes:
  - two-pass `parent_id` restore
  - `Future` singleton reconciliation
  - comment dedupe under `merge=True`
- If implementation reveals a need for helper methods, prefer small private helpers inside `MetaMixin` over pushing file-domain import logic into `FilesMixin`; the JSONL transfer path is already centralized in `db_meta.py`.

## Expected Risk Areas

- `merge=True` path reconciliation may pass tests for fresh restore but still break on existing trackers if `file_id_map` is incomplete.
- `file_events` lack a schema-level uniqueness constraint, so merge dedupe must be deliberate.
- `scan_findings.issue_id` is nullable and must remain intact when linked issues exist.

## Standby State

This plan is ready for execution, but no implementation has been started as requested.
