# Files/Findings Bugfixes — Remaining Work

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the two remaining correctness gaps from the files/findings work: metadata comparison uses string equality (generates spurious events), and one test case was never written.

**Architecture:** Two targeted fixes in `core.py` and `test_files.py`. No schema changes, no new tables, no migrations.

**Tech Stack:** Python, SQLite, pytest

**Prior work (already committed, do NOT redo):**
- `82c6298` — persist scan finding metadata on ingest (Task 1)
- `7b4853b` — min_findings counts all non-terminal statuses (Task 2)
- `c01ac5f` — file_events table, register_file event emission, timeline filter (Task 3)
- `025261d` — v3→v4 migration, TestMigrateV3ToV4, defensive JSON parsing in `_build_scan_finding`
- `b3b2e49` — scan-source filtering, timeline pill rendering fix (Task 4)

**Cross-plan dependency:** Task 1 must be completed before shipping MCP `trigger_scan` (Plan 2), otherwise repeated triggers can emit spurious `file_metadata_update` events.

---

### Task 1: Fix metadata semantic comparison in `register_file()`

**Files:**
- Modify: `src/filigree/core.py:2408-2414` (metadata comparison in `register_file` update branch)
- Test: `tests/test_files.py` (add to `TestFileMetadataEvents` class at line 1699)

The metadata change detection at `core.py:2411` uses raw string comparison (`old_meta != new_meta`). This means semantically identical dicts with different JSON key ordering (e.g., `{"a":1,"b":2}` vs `{"b":2,"a":1}`) generate spurious `file_metadata_update` events. The plan originally specified parsed-dict comparison but the implementation diverged.

**Step 1: Write failing regression test**

Add to `TestFileMetadataEvents` in `tests/test_files.py` (after `test_no_event_when_no_change` at line ~1727):

```python
def test_no_event_when_metadata_key_order_differs(self, db: FiligreeDB) -> None:
    """JSON key ordering should not cause spurious metadata events."""
    f = db.register_file("a.py", metadata={"a": 1, "b": 2})
    db.register_file("a.py", metadata={"b": 2, "a": 1})
    tl = db.get_file_timeline(f.id)
    meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
    assert len(meta_events) == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_files.py::TestFileMetadataEvents::test_no_event_when_metadata_key_order_differs -v`
Expected: FAIL — string comparison `'{"a": 1, "b": 2}' != '{"b": 2, "a": 1}'` triggers a spurious event.

**Step 3: Fix the comparison**

In `src/filigree/core.py`, replace the string comparison at lines 2408-2414:

```python
# Before (lines 2408-2414):
            if metadata:
                old_meta = existing["metadata"] or "{}"
                new_meta = json.dumps(metadata)
                if old_meta != new_meta:
                    updates.append("metadata = ?")
                    params.append(new_meta)
                    changes.append(("metadata", old_meta, new_meta))

# After:
            if metadata:
                old_meta_raw = existing["metadata"] or "{}"
                try:
                    old_meta_parsed = json.loads(old_meta_raw)
                except (json.JSONDecodeError, TypeError):
                    old_meta_parsed = {}
                if old_meta_parsed != metadata:
                    new_meta = json.dumps(metadata)
                    updates.append("metadata = ?")
                    params.append(new_meta)
                    changes.append(("metadata", old_meta_raw, new_meta))
```

Key changes:
- Parse old metadata with `json.loads` before comparing (dict equality, not string equality)
- Defensive `try/except` on the parse (consistent with `_build_scan_finding` pattern)
- `changes` tuple still uses the raw strings for the event `old_value`/`new_value` (preserves what was actually stored)

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_files.py::TestFileMetadataEvents -v`
Expected: All pass including the new regression test.

**Step 5: Commit**

```bash
git add src/filigree/core.py tests/test_files.py
git commit -m "fix: use parsed dict comparison for metadata change detection

Previously register_file() compared metadata as raw JSON strings,
causing spurious file_metadata_update events when dict key ordering
differed. Now parses stored JSON before comparing to avoid false
positives."
```

---

### Task 2: Add missing `min_findings` test for `unseen_in_latest`

**Files:**
- Modify: `tests/test_files.py` (add to `TestMinFindingsFilter` class at line 1358)

The fix for `min_findings` to count all non-terminal statuses was committed in `7b4853b`, but the test for `unseen_in_latest` was never written. Only `test_min_findings_counts_acknowledged` exists.

**Step 1: Add the test**

Add to `TestMinFindingsFilter` in `tests/test_files.py` (after `test_min_findings_counts_acknowledged` at line ~1403):

```python
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
    db.conn.execute(
        "UPDATE scan_findings SET status = 'unseen_in_latest' WHERE id = ?",
        (findings[0].id,),
    )
    db.conn.commit()
    result = db.list_files_paginated(min_findings=2)
    assert result["total"] == 1  # Both findings are non-terminal
```

**Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_files.py::TestMinFindingsFilter -v`
Expected: All pass (the fix is already in place, this just adds coverage).

**Step 3: Commit**

```bash
git add tests/test_files.py
git commit -m "test: add min_findings coverage for unseen_in_latest status

The fix for min_findings to count all non-terminal statuses was
committed in 7b4853b but this test case was omitted."
```

---

### Task 3: Full test suite + lint pass

**Step 1: Run full CI pipeline**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short
```

Expected: All clean.

**Step 2: Fix any issues found and re-run**
