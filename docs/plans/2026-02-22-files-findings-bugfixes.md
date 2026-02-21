# Files/Findings Bugfixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix four bugs in the files/findings flow: scan finding metadata dropped on ingest, min_findings miscounting, non-functional metadata timeline filter, and timeline pill styling reset.

**Architecture:** Tasks 1, 2, and 4 are isolated one-liner fixes in `core.py` and `files.js`. Task 3 adds a `file_events` table to the schema (with a v3→v4 migration for existing databases) and wires metadata change tracking into `register_file()` and `get_file_timeline()`. Task 1 also adds defensive JSON parsing in `_build_scan_finding` to prevent malformed metadata from crashing read paths.

**Tech Stack:** Python, SQLite, vanilla JS

## Execution Status (2026-02-21)

- [x] Task 1 complete
- [x] Task 2 complete
- [x] Task 3 complete
- [x] Task 4 complete
- [x] Task 5 complete

Validation notes:
- Timeline filter pills verified live in dashboard (All/Findings/Associations/Metadata).
- Seeded sample data includes finding, association, and metadata timeline events.
- Targeted regression + migration tests are passing.
- Full suite passed: `1633 passed`.
- Lint/format/type gates passed (`ruff check`, `ruff format --check`, `mypy`).

---

### Task 1: Persist scan finding metadata during upsert

**Files:**
- Modify: `src/filigree/core.py:2682-2688` (UPDATE branch of `process_scan_results`)
- Modify: `src/filigree/core.py:2694-2715` (INSERT branch of `process_scan_results`)
- Modify: `src/filigree/core.py:2343` (`_build_scan_finding` — add defensive JSON parsing)
- Test: `tests/test_files.py`

The `scan_findings` table has a `metadata TEXT` column and `_build_scan_finding` reads it back, but `process_scan_results` never writes it. Both the INSERT and UPDATE SQL need the column added. Additionally, `_build_scan_finding` parses metadata with bare `json.loads` — adding defensive error handling prevents malformed metadata from crashing all finding-reading endpoints.

**Step 1: Write failing tests**

Add to the `TestProcessScanResults` class in `tests/test_files.py`:

```python
def test_scan_metadata_persisted_on_create(self, db: FiligreeDB) -> None:
    db.process_scan_results(
        scan_source="ruff",
        findings=[
            {
                "path": "a.py",
                "rule_id": "E1",
                "severity": "low",
                "message": "m",
                "metadata": {"url": "https://example.com", "tags": ["style"]},
            },
        ],
    )
    f = db.get_file_by_path("a.py")
    findings = db.get_findings(f.id)
    assert findings[0].metadata == {"url": "https://example.com", "tags": ["style"]}

def test_scan_metadata_persisted_on_update(self, db: FiligreeDB) -> None:
    db.process_scan_results(
        scan_source="ruff",
        findings=[
            {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m",
             "metadata": {"v": 1}},
        ],
    )
    db.process_scan_results(
        scan_source="ruff",
        findings=[
            {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m2",
             "metadata": {"v": 2}},
        ],
    )
    f = db.get_file_by_path("a.py")
    findings = db.get_findings(f.id)
    assert findings[0].metadata == {"v": 2}

def test_scan_metadata_defaults_empty_dict(self, db: FiligreeDB) -> None:
    db.process_scan_results(
        scan_source="ruff",
        findings=[{"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m"}],
    )
    f = db.get_file_by_path("a.py")
    findings = db.get_findings(f.id)
    assert findings[0].metadata == {}
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_files.py::TestProcessScanResults::test_scan_metadata_persisted_on_create tests/test_files.py::TestProcessScanResults::test_scan_metadata_persisted_on_update tests/test_files.py::TestProcessScanResults::test_scan_metadata_defaults_empty_dict -v`
Expected: FAIL — metadata is always `{}` because it's never written.

**Step 3: Add defensive JSON parsing in `_build_scan_finding`**

In `src/filigree/core.py` at line 2343, wrap the bare `json.loads` in a try/except to prevent malformed metadata TEXT from crashing all finding-reading endpoints:

```python
        # Before:
        meta = json.loads(meta_raw) if meta_raw else {}

        # After:
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
```

**Step 4: Add metadata to INSERT SQL**

In `src/filigree/core.py`, the INSERT at line ~2694 already includes `metadata` in the column list and VALUES (added in a prior commit). Verify this is present — the column list should include `metadata` and the VALUES should include `json.dumps(f.get("metadata") or {})`:

```python
                self.conn.execute(
                    "INSERT INTO scan_findings "
                    "(id, file_id, scan_source, rule_id, severity, status, message, "
                    "suggestion, scan_run_id, "
                    "line_start, line_end, first_seen, updated_at, last_seen_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        finding_id,
                        file_id,
                        scan_source,
                        rule_id,
                        severity,
                        f.get("message", ""),
                        suggestion,
                        scan_run_id,
                        line_start,
                        f.get("line_end"),
                        now,
                        now,
                        now,
                        json.dumps(f.get("metadata") or {}),
                    ),
                )
```

**Step 5: Add metadata to UPDATE SQL**

In `src/filigree/core.py`, the UPDATE at line ~2682 already includes `metadata = ?` in the SET clause (added in a prior commit). Verify this is present:

```python
                self.conn.execute(
                    "UPDATE scan_findings SET message = ?, severity = ?, line_end = ?, "
                    "suggestion = ?, scan_run_id = ?, metadata = ?, "
                    "seen_count = seen_count + 1, updated_at = ?, last_seen_at = ?, "
                    "status = CASE WHEN status IN ('fixed', 'unseen_in_latest') THEN 'open' ELSE status END "
                    "WHERE id = ?",
                    (f.get("message", ""), severity, f.get("line_end"), suggestion, run_id_update, json.dumps(f.get("metadata") or {}), now, now, existing_finding["id"]),
                )
```

**Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_files.py::TestProcessScanResults -v`
Expected: All pass including the three new tests.

**Step 7: Commit**

```bash
git add src/filigree/core.py tests/test_files.py
git commit -m "fix: persist scan finding metadata on ingest create and update"
```

---

### Task 2: Count non-terminal statuses in min_findings filter

**Files:**
- Modify: `src/filigree/core.py:2489` (one line in `list_files_paginated`)
- Test: `tests/test_files.py`

The `min_findings` subquery counts only `status = 'open'`, but the rest of the API defines "open" as `status NOT IN ('fixed', 'false_positive')`. This means `acknowledged` and `unseen_in_latest` findings are invisible to `min_findings` while visible everywhere else.

**Step 1: Write failing test**

Add to the `TestMinFindingsFilter` class:

```python
def test_min_findings_counts_acknowledged(self, db: FiligreeDB) -> None:
    db.process_scan_results(
        scan_source="ruff",
        findings=[
            {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m1"},
            {"path": "a.py", "rule_id": "E2", "severity": "low", "message": "m2"},
        ],
    )
    f = db.get_file_by_path("a.py")
    findings = db.get_findings(f.id)
    # Mark one as acknowledged — should still count as active
    db.conn.execute("UPDATE scan_findings SET status = 'acknowledged' WHERE id = ?", (findings[0].id,))
    db.conn.commit()
    result = db.list_files_paginated(min_findings=2)
    assert result["total"] == 1  # Both findings are non-terminal

def test_min_findings_counts_unseen_in_latest(self, db: FiligreeDB) -> None:
    db.process_scan_results(
        scan_source="ruff",
        findings=[
            {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m1"},
            {"path": "a.py", "rule_id": "E2", "severity": "low", "message": "m2"},
        ],
    )
    f = db.get_file_by_path("a.py")
    findings = db.get_findings(f.id)
    # Mark one as unseen_in_latest — should still count as active
    db.conn.execute("UPDATE scan_findings SET status = 'unseen_in_latest' WHERE id = ?", (findings[0].id,))
    db.conn.commit()
    result = db.list_files_paginated(min_findings=2)
    assert result["total"] == 1  # Both findings are non-terminal
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_files.py::TestMinFindingsFilter::test_min_findings_counts_acknowledged tests/test_files.py::TestMinFindingsFilter::test_min_findings_counts_unseen_in_latest -v`
Expected: FAIL — `total` is 0 because non-open active findings aren't counted.

**Step 3: Fix the subquery**

In `src/filigree/core.py` at line 2488, change:

```python
# Before:
clauses.append("(SELECT COUNT(*) FROM scan_findings sf WHERE sf.file_id = file_records.id AND sf.status = 'open') >= ?")

# After:
clauses.append("(SELECT COUNT(*) FROM scan_findings sf WHERE sf.file_id = file_records.id AND sf.status NOT IN ('fixed', 'false_positive')) >= ?")
```

**Step 4: Run tests to verify all pass**

Run: `uv run pytest tests/test_files.py::TestMinFindingsFilter -v`
Expected: All pass (existing + new test).

**Step 5: Commit**

```bash
git add src/filigree/core.py tests/test_files.py
git commit -m "fix: min_findings filter counts all non-terminal finding statuses

Previously min_findings only counted status='open'. Now counts all
non-terminal statuses (open, acknowledged, unseen_in_latest). This
aligns with how the rest of the API defines active findings."
```

---

### Task 3: File metadata change tracking + timeline filter

**Files:**
- Modify: `src/filigree/core.py:249-252` (SCHEMA_SQL — add `file_events` table after `file_associations` indexes, before closing `"""`)
- Modify: `src/filigree/core.py:259` (bump `CURRENT_SCHEMA_VERSION` from 3 to 4)
- Modify: `src/filigree/core.py:2379-2399` (`register_file` — emit events on field changes)
- Modify: `src/filigree/core.py:3202-3208` (`get_file_timeline` — query file_events + handle filter)
- Modify: `src/filigree/migrations.py` (add `migrate_v3_to_v4`)
- Test: `tests/test_files.py`, `tests/test_migrations.py`

This task adds a lightweight `file_events` table that records when file metadata fields change. `register_file()` diffs old vs new values and inserts a row. `get_file_timeline()` includes these events and the `event_type=file_metadata_update` filter works. A v3→v4 migration ensures existing databases get the new table.

**Step 1: Write failing tests**

Add a new test class in `tests/test_files.py` after `TestFileTimeline`:

```python
class TestFileMetadataEvents:
    """register_file should emit file_metadata_update events on field changes."""

    def test_metadata_event_on_language_change(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", language="python")
        db.register_file("a.py", language="python3")
        tl = db.get_file_timeline(f.id)
        meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
        assert len(meta_events) == 1
        assert meta_events[0]["data"]["field"] == "language"
        assert meta_events[0]["data"]["old_value"] == "python"
        assert meta_events[0]["data"]["new_value"] == "python3"

    def test_metadata_event_on_metadata_change(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", metadata={"k": "v1"})
        db.register_file("a.py", metadata={"k": "v2"})
        tl = db.get_file_timeline(f.id)
        meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
        assert len(meta_events) == 1
        assert meta_events[0]["data"]["field"] == "metadata"

    def test_no_event_when_no_change(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", language="python")
        db.register_file("a.py", language="python")
        tl = db.get_file_timeline(f.id)
        meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
        assert len(meta_events) == 0

    def test_no_event_on_first_registration(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", language="python")
        tl = db.get_file_timeline(f.id)
        meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
        assert len(meta_events) == 0

    def test_timeline_filter_file_metadata_update(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", language="python")
        db.register_file("a.py", language="python3")
        issue = db.create_issue("Fix it")
        db.add_file_association(f.id, issue.id, "bug_in")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m"}],
        )
        # All events
        all_tl = db.get_file_timeline(f.id)
        assert all_tl["total"] >= 3  # finding + association + metadata

        # Filter to metadata only
        meta_tl = db.get_file_timeline(f.id, event_type="file_metadata_update")
        assert meta_tl["total"] == 1
        assert all(e["type"] == "file_metadata_update" for e in meta_tl["results"])

    def test_unknown_event_type_returns_empty(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m"}],
        )
        result = db.get_file_timeline(f.id, event_type="bogus_type")
        assert result["total"] == 0
        assert result["results"] == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_files.py::TestFileMetadataEvents -v`
Expected: FAIL — `file_events` table doesn't exist.

**Step 3: Add `file_events` table to SCHEMA_SQL**

In `src/filigree/core.py`, add the table definition after the `file_associations` indexes (after line 251, before the closing `"""` at line 252):

```sql
CREATE TABLE IF NOT EXISTS file_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     TEXT NOT NULL REFERENCES file_records(id),
    event_type  TEXT NOT NULL DEFAULT 'file_metadata_update',
    field       TEXT NOT NULL,
    old_value   TEXT DEFAULT '',
    new_value   TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_events_file ON file_events(file_id);
```

**Step 4: Bump schema version and add migration**

In `src/filigree/core.py` at line 259, change:
```python
CURRENT_SCHEMA_VERSION = 3
```
to:
```python
CURRENT_SCHEMA_VERSION = 4
```

In `src/filigree/migrations.py`, add the migration function and register it:

```python
def migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """v3 → v4: Add file_events table for metadata change tracking.

    Changes:
      - new table 'file_events' for tracking file metadata field changes
      - new index idx_file_events_file on file_events(file_id)
    """
    conn.execute("""\
        CREATE TABLE IF NOT EXISTS file_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id     TEXT NOT NULL REFERENCES file_records(id),
            event_type  TEXT NOT NULL DEFAULT 'file_metadata_update',
            field       TEXT NOT NULL,
            old_value   TEXT DEFAULT '',
            new_value   TEXT DEFAULT '',
            created_at  TEXT NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_events_file ON file_events(file_id)")
```

Update the `MIGRATIONS` dict:
```python
MIGRATIONS: dict[int, MigrationFn] = {
    1: migrate_v1_to_v2,
    2: migrate_v2_to_v3,
    3: migrate_v3_to_v4,
}
```

Add a migration test class in `tests/test_migrations.py` (after `TestMigrateV2ToV3`):

```python
class TestMigrateV3ToV4:
    """Tests for migration v3 → v4: file_events table."""

    @pytest.fixture
    def v3_db(self, tmp_path: Path) -> sqlite3.Connection:
        """Create a v3 database (file tables present, no file_events)."""
        from filigree.core import SCHEMA_V1_SQL
        from filigree.migrations import migrate_v1_to_v2, migrate_v2_to_v3

        conn = _make_db(tmp_path)
        conn.executescript(SCHEMA_V1_SQL)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        migrate_v1_to_v2(conn)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        migrate_v2_to_v3(conn)
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        return conn

    def test_migration_runs(self, v3_db: sqlite3.Connection) -> None:
        applied = apply_pending_migrations(v3_db, 4)
        assert applied == 1
        assert _get_schema_version(v3_db) == 4

    def test_table_created(self, v3_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v3_db, 4)
        cols = _get_table_columns(v3_db, "file_events")
        assert "file_id" in cols
        assert "event_type" in cols
        assert "field" in cols

    def test_index_created(self, v3_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v3_db, 4)
        indexes = _get_index_names(v3_db)
        assert "idx_file_events_file" in indexes

    def test_idempotent(self, v3_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v3_db, 4)
        # Running again should be a no-op
        applied = apply_pending_migrations(v3_db, 4)
        assert applied == 0
```

**Step 5: Emit events from `register_file()` on field changes**

In `src/filigree/core.py`, modify the update branch of `register_file()` (lines 2379-2399). Replace the existing update logic with diff-aware code that emits events:

```python
        if existing is not None:
            updates: list[str] = []
            params: list[Any] = []
            # Detect field changes and emit events
            changes: list[tuple[str, str, str]] = []  # (field, old, new)
            if language and language != (existing["language"] or ""):
                updates.append("language = ?")
                params.append(language)
                changes.append(("language", existing["language"] or "", language))
            if file_type and file_type != (existing["file_type"] or ""):
                updates.append("file_type = ?")
                params.append(file_type)
                changes.append(("file_type", existing["file_type"] or "", file_type))
            if metadata:
                old_meta_raw = existing["metadata"] or "{}"
                # Compare parsed dicts to avoid spurious events from JSON key ordering
                try:
                    old_meta_parsed = json.loads(old_meta_raw)
                except (json.JSONDecodeError, TypeError):
                    old_meta_parsed = {}
                if old_meta_parsed != metadata:
                    new_meta = json.dumps(metadata)
                    updates.append("metadata = ?")
                    params.append(new_meta)
                    changes.append(("metadata", old_meta_raw, new_meta))
            updates.append("updated_at = ?")
            params.append(now)
            params.append(existing["id"])
            self.conn.execute(
                f"UPDATE file_records SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            for field, old_val, new_val in changes:
                self.conn.execute(
                    "INSERT INTO file_events (file_id, event_type, field, old_value, new_value, created_at) "
                    "VALUES (?, 'file_metadata_update', ?, ?, ?, ?)",
                    (existing["id"], field, old_val, new_val, now),
                )
            self.conn.commit()
            return self.get_file(existing["id"])
```

This replaces the existing update branch. Key differences:
- Fields are only added to `updates` when they actually differ from the current value
- `changes` tracks what to emit as timeline events
- Metadata comparison uses parsed dicts (`json.loads(old) != metadata`) to avoid spurious events from JSON key ordering differences
- `updated_at` is still always bumped (preserves existing behavior)

**Step 6: Wire `file_events` into `get_file_timeline()`**

In `src/filigree/core.py`, in `get_file_timeline()` after the association entries block (after line ~3202), add a third query:

```python
        # 3. File metadata events
        meta_events = self.conn.execute(
            "SELECT id, field, old_value, new_value, created_at "
            "FROM file_events WHERE file_id = ? ORDER BY created_at DESC, id DESC",
            (file_id,),
        ).fetchall()
        for m in meta_events:
            entries.append(
                {
                    "type": "file_metadata_update",
                    "timestamp": m["created_at"],
                    "source_id": str(m["id"]),
                    "data": {
                        "field": m["field"],
                        "old_value": m["old_value"],
                        "new_value": m["new_value"],
                    },
                }
            )
```

Then update the filter block to handle the new type and reject unknown types:

```python
        # Filter by event type before sorting/paginating
        if event_type == "finding":
            entries = [e for e in entries if e["type"].startswith("finding_")]
        elif event_type == "association":
            entries = [e for e in entries if e["type"].startswith("association_")]
        elif event_type == "file_metadata_update":
            entries = [e for e in entries if e["type"] == "file_metadata_update"]
        elif event_type is not None:
            entries = []  # Unknown filter type → empty results
```

**Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_files.py::TestFileMetadataEvents tests/test_files.py::TestFileTimeline tests/test_migrations.py::TestMigrateV3ToV4 -v`
Expected: All pass.

**Step 8: Commit**

```bash
git add src/filigree/core.py src/filigree/migrations.py tests/test_files.py tests/test_migrations.py
git commit -m "feat: track file metadata changes as timeline events with filter support

Adds file_events table (schema v4) with v3→v4 migration for existing
databases. register_file() emits events on field changes.
get_file_timeline() includes metadata events and supports the
file_metadata_update filter type."
```

---

### Task 4: Render timeline pills from current filter state

**Files:**
- Modify: `src/filigree/static/js/state.js:143` (declare `timelineFilter` in state object)
- Modify: `src/filigree/static/js/views/files.js:552-558` (pill rendering)
- No automated test (UI-only, manual verification)

The pill HTML is rebuilt on every `loadTimelineTab` call with "All" hardcoded as active. It should read `state.timelineFilter` to set the correct active pill. Additionally, `timelineFilter` is never formally declared in `state.js` — it only works via dynamic property assignment. Declare it to prevent future reset/clone bugs.

**Step 1: Declare `timelineFilter` in state object**

In `src/filigree/static/js/state.js`, add `timelineFilter: null` to the state object (after `fileDetailTab: "findings"` at line 142):

```javascript
  fileDetailTab: "findings",
  timelineFilter: null,
  hotspots: null,
```

**Step 2: Fix pill rendering**

In `src/filigree/static/js/views/files.js`, replace the hardcoded pill HTML (lines ~552-558) with filter-aware rendering:

```javascript
    // Filter pills — render active state from current filter
    const activeFilter = state.timelineFilter || "all";
    const pillClass = (type) =>
      type === activeFilter
        ? "text-xs px-2 py-1 rounded bg-accent text-primary"
        : "text-xs px-2 py-1 rounded bg-overlay text-secondary bg-overlay-hover";

    let html =
      '<div class="flex gap-1 mb-3">' +
      `<button onclick="filterTimeline('all')" class="${pillClass('all')}" id="tlFilterAll">All</button>` +
      `<button onclick="filterTimeline('finding')" class="${pillClass('finding')}" id="tlFilterFinding">Findings</button>` +
      `<button onclick="filterTimeline('association')" class="${pillClass('association')}" id="tlFilterAssoc">Associations</button>` +
      `<button onclick="filterTimeline('file_metadata_update')" class="${pillClass('file_metadata_update')}" id="tlFilterMeta">Metadata</button>` +
      "</div>";
```

**Step 3: Manual verification**

1. Open the dashboard at `http://localhost:8377`
2. Navigate to a file with findings
3. Click "Timeline" tab
4. Click "Findings" pill → should stay highlighted after data loads
5. Click "Metadata" pill → should stay highlighted (shows empty or metadata events)
6. Click "All" → should reset to showing all events with "All" highlighted

**Step 4: Commit**

```bash
git add src/filigree/static/js/state.js src/filigree/static/js/views/files.js
git commit -m "fix: render timeline pill active state from current filter

Declares timelineFilter in state.js (was previously only set via
dynamic assignment in filterTimeline). Pill rendering now reads
state.timelineFilter to highlight the correct active filter."
```

---

### Task 5: Full test suite + lint pass

**Step 1: Run full test suite**

Run: `uv run pytest --tb=short`
Expected: All tests pass.

**Step 2: Run linter and type checker**

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/filigree/`
Expected: Clean.

**Step 3: Fix any issues found**

If ruff or mypy report errors, fix them and re-run.
