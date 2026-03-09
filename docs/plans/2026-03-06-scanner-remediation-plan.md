# Scanner System Remediation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close all scanner system usability gaps so AI agents can trigger scans, poll status, triage findings, and manage the full lifecycle through MCP tools without REST/Bash fallback.

**Architecture:** New `ScansMixin` (db_scans.py) owns scan_runs lifecycle. New `mcp_tools/scanners.py` handles scanner-domain tools. Finding triage tools added to `mcp_tools/files.py`. `process_scan_results` reworked to create observations instead of issues. Shared scanner pipeline extracted into `scripts/scan_utils.py` (existing CLI utils file — extended, not newly created).

**Tech Stack:** Python 3.11+, SQLite, MCP SDK, pytest, FastAPI (dashboard routes)

**Module layout (current state):**
- Scan methods: `FilesMixin` in `db_files.py` (`process_scan_results` at line 724, `get_scan_runs` at line 799, `update_finding` at line 830)
- Scanner registry: `src/filigree/scanners.py` (ScannerConfig, TOML definitions)
- Scanner MCP tools: `mcp_tools/files.py` (`list_scanners` def at line 135/handler 365, `trigger_scan` def at line 143/handler 382)
- Scan cooldown: IN-MEMORY dict at `mcp_server.py:60-62`, logic at `mcp_tools/files.py:447-505`
- Dashboard routes: `dashboard_routes/files.py` (POST /v1/scan-results at line 292, GET /scan-runs at line 323)
- Types: `types/files.py` (`ScanRunRecord` at line 88, `ScanIngestResult` at line 105)
- CLI scanner utils: `scripts/scan_utils.py` (NOT in src/ — CLI pipeline utilities)
- FiligreeDB class: `core.py:340` — MRO: FilesMixin, IssuesMixin, EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin, ObservationsMixin
- Schema version: 7 (at `db_schema.py:317`)
- Note: `db_scans.py` and `mcp_tools/scanners.py` do NOT exist yet — they are created by this plan

**Design doc:** `docs/plans/2026-03-06-scanner-remediation-design.md`

---

## Review Callouts (2026-03-06)

These were identified during plan review and verified against the codebase:

1. **`ScanRunDict` vs `ScanRunRecord`** — `types/files.py` already has `ScanRunRecord` (line 88, runs to ~line 103), which is a GROUP BY shape from scan_findings. The new `ScanRunDict` is a proper scan_runs table shape. Add a docstring to `ScanRunDict` clarifying the distinction: _"Shape for scan_runs table rows. Not to be confused with ScanRunRecord, which is a GROUP BY projection from scan_findings."_

2. **`check_scan_cooldown` LIKE query** — The `json_extract(file_paths, '$') LIKE '%"path"%'` pattern is fragile for paths containing JSON-special characters. Acceptable for MVP. Add a `# TODO: replace LIKE with json_each() for robustness` comment in the implementation.

3. **Task 7 / Task 10 overlap** — Both modify `dashboard_routes/files.py` for scan_run completion tracking. The executing agent MUST check whether Task 7's Step 6 already handled the scan_runs update before applying Task 10's Step 1. If it did, skip the duplicate code in Task 10.

4. **Task 6 extraction ordering** — When moving tools from `files.py` to `scanners.py`, do additive changes first: create `scanners.py`, register it in `mcp_server.py`, verify tests pass with both modules registering the tools, THEN remove from `files.py`. This avoids a window where tools vanish if something breaks mid-task.

5. **No FK on `scan_run_id`** — The `scan_findings.scan_run_id` column is a plain TEXT field with no foreign key to `scan_runs`. This is intentional (results can be POSTed without `trigger_scan`), but means orphaned references are possible. No action needed for MVP.

---

## Phase 1: Data Layer — `scan_runs` table and `ScansMixin`

### Task 1: Schema — add `scan_runs` table DDL

**Files:**
- Modify: `src/filigree/db_schema.py` (after line 170, after the `file_associations` index block)
- Test: `tests/core/test_files.py` (add schema test)

**Step 1: Write the failing test**

Add to `tests/core/test_files.py` in the `TestFileSchema` class:

```python
def test_scan_runs_table_exists(self, db: FiligreeDB) -> None:
    row = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scan_runs'"
    ).fetchone()
    assert row is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_files.py::TestFileSchema::test_scan_runs_table_exists -v`
Expected: FAIL — table does not exist

**Step 3: Add the DDL**

In `src/filigree/db_schema.py`, after the `file_associations` index block (after line 170 — the `file_associations` table is at lines 159-167, indexes at lines 169-170), add:

```sql
-- ---- Scan run lifecycle tracking ------------------------------------------

CREATE TABLE IF NOT EXISTS scan_runs (
    id            TEXT PRIMARY KEY,
    scanner_name  TEXT NOT NULL,
    scan_source   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    file_paths    TEXT NOT NULL DEFAULT '[]',
    file_ids      TEXT NOT NULL DEFAULT '[]',
    pid           INTEGER,
    api_url       TEXT DEFAULT '',
    log_path      TEXT DEFAULT '',
    started_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT,
    exit_code     INTEGER,
    findings_count INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'timeout'))
);

CREATE INDEX IF NOT EXISTS idx_scan_runs_status ON scan_runs(status);
CREATE INDEX IF NOT EXISTS idx_scan_runs_scanner ON scan_runs(scanner_name);
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_files.py::TestFileSchema -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/filigree/db_schema.py tests/core/test_files.py
git commit -m "feat(schema): add scan_runs table for scan lifecycle tracking"
```

---

### Task 2: Types — add `ScanRunDict` TypedDict

**Files:**
- Modify: `src/filigree/types/files.py` (add after `ScanRunRecord`)
- No test needed — type-only change, verified by mypy

**Step 1: Add `ScanRunDict` to `src/filigree/types/files.py`**

After the existing `ScanRunRecord` class (lines 88-103), add:

```python
class ScanRunDict(TypedDict):
    """Shape for scan_runs table rows returned by ScansMixin.

    Not to be confused with ScanRunRecord (line 88), which is a GROUP BY
    projection from scan_findings for legacy scan history queries.
    """

    id: str
    scanner_name: str
    scan_source: str
    status: str
    file_paths: list[str]
    file_ids: list[str]
    pid: int | None
    api_url: str
    log_path: str
    started_at: ISOTimestamp
    updated_at: ISOTimestamp
    completed_at: ISOTimestamp | None
    exit_code: int | None
    findings_count: int
    error_message: str
```

**Step 2: Add `ScanRunStatusDict` for the `get_scan_status` response**

```python
class ScanRunStatusDict(ScanRunDict):
    """Extended shape for get_scan_status with live process info and log tail."""

    process_alive: bool
    log_tail: list[str]
```

**Step 3: Run mypy**

Run: `uv run mypy src/filigree/types/files.py`
Expected: PASS

**Step 4: Commit**

```bash
git add src/filigree/types/files.py
git commit -m "feat(types): add ScanRunDict and ScanRunStatusDict TypedDicts"
```

---

### Task 3: `db_scans.py` — ScansMixin with CRUD and cooldown

**Files:**
- Create: `src/filigree/db_scans.py`
- Create: `tests/core/test_scans.py`

**Step 1: Write failing tests**

Create `tests/core/test_scans.py`:

```python
"""Tests for ScansMixin — scan run lifecycle tracking."""

from __future__ import annotations

import json

import pytest

from filigree.core import FiligreeDB


class TestCreateScanRun:
    def test_create_returns_dict(self, db: FiligreeDB) -> None:
        run = db.create_scan_run(
            scan_run_id="test-run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        assert run["id"] == "test-run-1"
        assert run["scanner_name"] == "codex"
        assert run["status"] == "pending"
        assert run["file_paths"] == ["src/main.py"]

    def test_create_duplicate_raises(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="test-run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        with pytest.raises(ValueError, match="already exists"):
            db.create_scan_run(
                scan_run_id="test-run-1",
                scanner_name="codex",
                scan_source="codex",
                file_paths=["src/main.py"],
                file_ids=["f-1"],
            )


class TestUpdateScanRunStatus:
    def test_transition_pending_to_running(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
            pid=1234,
        )
        db.update_scan_run_status("run-1", "running")
        run = db.get_scan_run("run-1")
        assert run["status"] == "running"

    def test_transition_running_to_completed(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "completed", exit_code=0, findings_count=5)
        run = db.get_scan_run("run-1")
        assert run["status"] == "completed"
        assert run["exit_code"] == 0
        assert run["findings_count"] == 5
        assert run["completed_at"] is not None

    def test_transition_running_to_failed(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=[],
            file_ids=[],
        )
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "failed", error_message="crash")
        run = db.get_scan_run("run-1")
        assert run["status"] == "failed"
        assert run["error_message"] == "crash"

    def test_invalid_transition_raises(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=[],
            file_ids=[],
        )
        with pytest.raises(ValueError, match="Invalid transition"):
            db.update_scan_run_status("run-1", "completed")

    def test_not_found_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.update_scan_run_status("no-such-run", "running")


class TestGetScanRun:
    def test_get_returns_dict(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["a.py", "b.py"],
            file_ids=["f-1", "f-2"],
        )
        run = db.get_scan_run("run-1")
        assert run["id"] == "run-1"
        assert run["file_paths"] == ["a.py", "b.py"]

    def test_get_not_found_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_scan_run("no-such-run")


class TestCooldownCheck:
    def test_no_recent_run_allows_trigger(self, db: FiligreeDB) -> None:
        assert db.check_scan_cooldown("codex", "src/main.py") is None

    def test_running_scan_blocks(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        db.update_scan_run_status("run-1", "running")
        result = db.check_scan_cooldown("codex", "src/main.py")
        assert result is not None  # returns blocking run info

    def test_failed_scan_does_not_block(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "failed")
        assert db.check_scan_cooldown("codex", "src/main.py") is None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_scans.py -v`
Expected: FAIL — `FiligreeDB` has no `create_scan_run` attribute

**Step 3: Implement `db_scans.py`**

Create `src/filigree/db_scans.py`:

```python
"""ScansMixin — scan run lifecycle tracking.

Owns the scan_runs table: CRUD, status transitions, cooldown checks, log tail.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from filigree.db_base import DBMixinProtocol, _now_iso
from filigree.types.files import ScanRunDict, ScanRunStatusDict

logger = logging.getLogger(__name__)

SCAN_COOLDOWN_SECONDS = 30

# Valid transitions: from_status -> set of valid to_statuses
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "failed"},
    "running": {"completed", "failed", "timeout"},
}


class ScansMixin(DBMixinProtocol):
    """Scan run lifecycle — create, update status, check cooldown, read logs."""

    def create_scan_run(
        self,
        *,
        scan_run_id: str,
        scanner_name: str,
        scan_source: str,
        file_paths: list[str],
        file_ids: list[str],
        pid: int | None = None,
        api_url: str = "",
        log_path: str = "",
    ) -> ScanRunDict:
        now = _now_iso()
        existing = self.conn.execute(
            "SELECT id FROM scan_runs WHERE id = ?", (scan_run_id,)
        ).fetchone()
        if existing:
            raise ValueError(f"Scan run {scan_run_id!r} already exists")
        self.conn.execute(
            "INSERT INTO scan_runs "
            "(id, scanner_name, scan_source, status, file_paths, file_ids, "
            "pid, api_url, log_path, started_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_run_id, scanner_name, scan_source,
                json.dumps(file_paths), json.dumps(file_ids),
                pid, api_url, log_path, now, now,
            ),
        )
        self.conn.commit()
        return self.get_scan_run(scan_run_id)

    def get_scan_run(self, scan_run_id: str) -> ScanRunDict:
        row = self.conn.execute(
            "SELECT * FROM scan_runs WHERE id = ?", (scan_run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Scan run not found: {scan_run_id!r}")
        return self._build_scan_run_dict(row)

    def update_scan_run_status(
        self,
        scan_run_id: str,
        status: str,
        *,
        exit_code: int | None = None,
        findings_count: int | None = None,
        error_message: str | None = None,
    ) -> ScanRunDict:
        current = self.get_scan_run(scan_run_id)
        current_status = current["status"]
        valid_next = _VALID_TRANSITIONS.get(current_status, set())
        if status not in valid_next:
            raise ValueError(
                f"Invalid transition: {current_status!r} -> {status!r}. "
                f"Valid: {sorted(valid_next)}"
            )
        now = _now_iso()
        updates = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, now]
        if status in ("completed", "failed", "timeout"):
            updates.append("completed_at = ?")
            params.append(now)
        if exit_code is not None:
            updates.append("exit_code = ?")
            params.append(exit_code)
        if findings_count is not None:
            updates.append("findings_count = ?")
            params.append(findings_count)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        params.append(scan_run_id)
        self.conn.execute(
            f"UPDATE scan_runs SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.get_scan_run(scan_run_id)

    def check_scan_cooldown(
        self, scanner_name: str, file_path: str
    ) -> ScanRunDict | None:
        """Check if a recent non-failed scan blocks triggering.

        Returns the blocking scan run dict, or None if trigger is allowed.
        Only 'running' and recently-'completed' scans block.
        """
        # TODO: replace LIKE with json_each() for robustness against
        # paths containing JSON-special characters (quotes, backslashes)
        row = self.conn.execute(
            "SELECT * FROM scan_runs "
            "WHERE scanner_name = ? "
            "AND json_extract(file_paths, '$') LIKE ? "
            "AND status IN ('pending', 'running', 'completed') "
            "AND updated_at >= datetime('now', ?) "
            "ORDER BY updated_at DESC LIMIT 1",
            (scanner_name, f'%"{file_path}"%', f"-{SCAN_COOLDOWN_SECONDS} seconds"),
        ).fetchone()
        if row is None:
            return None
        return self._build_scan_run_dict(row)

    def get_scan_status(
        self, scan_run_id: str, *, log_lines: int = 50
    ) -> ScanRunStatusDict:
        """Get scan run with live PID check and log tail."""
        run = self.get_scan_run(scan_run_id)
        process_alive = False
        if run["pid"] is not None and run["status"] == "running":
            try:
                os.kill(run["pid"], 0)
                process_alive = True
            except (OSError, ProcessLookupError):
                pass
        log_tail: list[str] = []
        if run["log_path"]:
            log_path = Path(run["log_path"])
            if log_path.is_file():
                try:
                    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    log_tail = lines[-log_lines:] if len(lines) > log_lines else lines
                except OSError:
                    pass
        return ScanRunStatusDict(
            **run,
            process_alive=process_alive,
            log_tail=log_tail,
        )

    def _build_scan_run_dict(self, row: Any) -> ScanRunDict:
        file_paths = json.loads(row["file_paths"]) if row["file_paths"] else []
        file_ids = json.loads(row["file_ids"]) if row["file_ids"] else []
        return ScanRunDict(
            id=row["id"],
            scanner_name=row["scanner_name"],
            scan_source=row["scan_source"],
            status=row["status"],
            file_paths=file_paths,
            file_ids=file_ids,
            pid=row["pid"],
            api_url=row["api_url"] or "",
            log_path=row["log_path"] or "",
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            exit_code=row["exit_code"],
            findings_count=row["findings_count"] or 0,
            error_message=row["error_message"] or "",
        )
```

**Step 4: Mix into `FiligreeDB`**

In `src/filigree/core.py`:
- Add import: `from filigree.db_scans import ScansMixin`
- Change class definition at line 340:
```python
class FiligreeDB(FilesMixin, ScansMixin, IssuesMixin, EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin, ObservationsMixin):
```

**Step 5: Run tests**

Run: `uv run pytest tests/core/test_scans.py -v`
Expected: ALL PASS

**Step 6: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: ALL PASS (no regressions)

**Step 7: Commit**

```bash
git add src/filigree/db_scans.py src/filigree/core.py tests/core/test_scans.py
git commit -m "feat(core): add ScansMixin with scan_runs lifecycle tracking"
```

---

## Phase 2: Finding Triage — DB methods and MCP tools

### Task 4: DB methods — `get_finding`, `list_findings_global`, `update_finding`, `promote_finding_to_observation`

**Files:**
- Modify: `src/filigree/db_files.py`
- Create: `tests/core/test_finding_triage.py`

**Step 1: Write failing tests**

Create `tests/core/test_finding_triage.py`:

```python
"""Tests for finding triage DB methods."""

from __future__ import annotations

import pytest

from filigree.core import FiligreeDB


def _seed_findings(db: FiligreeDB) -> dict[str, str]:
    """Create a file with 3 findings and return {name: finding_id}."""
    f = db.register_file("src/main.py", language="python")
    result = db.process_scan_results(
        scan_source="test-scanner",
        findings=[
            {"path": "src/main.py", "rule_id": "logic-error", "severity": "high", "message": "Off by one"},
            {"path": "src/main.py", "rule_id": "type-error", "severity": "medium", "message": "Wrong return type", "line_start": 42},
            {"path": "src/main.py", "rule_id": "injection", "severity": "critical", "message": "SQL injection", "line_start": 100},
        ],
    )
    ids = result["new_finding_ids"]
    return {"obo": ids[0], "type": ids[1], "sqli": ids[2]}


class TestGetFinding:
    def test_get_by_id(self, db: FiligreeDB) -> None:
        ids = _seed_findings(db)
        finding = db.get_finding(ids["obo"])
        assert finding["rule_id"] == "logic-error"
        assert finding["severity"] == "high"

    def test_not_found_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_finding("no-such-id")


class TestListFindingsGlobal:
    def test_returns_all_findings(self, db: FiligreeDB) -> None:
        _seed_findings(db)
        result = db.list_findings_global()
        assert len(result["findings"]) == 3

    def test_filter_by_severity(self, db: FiligreeDB) -> None:
        _seed_findings(db)
        result = db.list_findings_global(severity="critical")
        assert len(result["findings"]) == 1
        assert result["findings"][0]["rule_id"] == "injection"

    def test_filter_by_status(self, db: FiligreeDB) -> None:
        _seed_findings(db)
        result = db.list_findings_global(status="open")
        assert len(result["findings"]) == 3

    def test_filter_by_scan_run_id(self, db: FiligreeDB) -> None:
        db.register_file("src/main.py")
        db.process_scan_results(
            scan_source="s1",
            scan_run_id="run-1",
            findings=[{"path": "src/main.py", "rule_id": "r1", "severity": "info", "message": "m1"}],
        )
        db.process_scan_results(
            scan_source="s1",
            scan_run_id="run-2",
            findings=[{"path": "src/main.py", "rule_id": "r2", "severity": "info", "message": "m2"}],
        )
        result = db.list_findings_global(scan_run_id="run-2")
        assert len(result["findings"]) == 1
        assert result["findings"][0]["rule_id"] == "r2"

    def test_filter_by_issue_id(self, db: FiligreeDB) -> None:
        ids = _seed_findings(db)
        issue = db.create_issue("Test bug", type="bug")
        db.update_finding(ids["sqli"], issue_id=issue.id)
        result = db.list_findings_global(issue_id=issue.id)
        assert len(result["findings"]) == 1

    def test_pagination(self, db: FiligreeDB) -> None:
        _seed_findings(db)
        result = db.list_findings_global(limit=2, offset=0)
        assert len(result["findings"]) == 2
        assert result["total"] == 3


class TestUpdateFinding:
    def test_update_status(self, db: FiligreeDB) -> None:
        ids = _seed_findings(db)
        updated = db.update_finding(ids["obo"], status="acknowledged")
        assert updated["status"] == "acknowledged"

    def test_update_issue_id(self, db: FiligreeDB) -> None:
        ids = _seed_findings(db)
        issue = db.create_issue("Test bug", type="bug")
        updated = db.update_finding(ids["obo"], issue_id=issue.id)
        assert updated["issue_id"] == issue.id

    def test_invalid_status_raises(self, db: FiligreeDB) -> None:
        ids = _seed_findings(db)
        with pytest.raises(ValueError, match="Invalid finding status"):
            db.update_finding(ids["obo"], status="bogus")

    def test_not_found_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.update_finding("no-such-id", status="fixed")


class TestPromoteFindingToObservation:
    def test_creates_observation(self, db: FiligreeDB) -> None:
        ids = _seed_findings(db)
        obs = db.promote_finding_to_observation(ids["sqli"])
        assert obs["summary"].startswith("[test-scanner]")
        assert "SQL injection" in obs["summary"]
        assert obs["file_path"] == "src/main.py"
        assert obs["line"] == 100

    def test_priority_from_severity(self, db: FiligreeDB) -> None:
        ids = _seed_findings(db)
        obs = db.promote_finding_to_observation(ids["sqli"])
        assert obs["priority"] == 0  # critical -> P0

    def test_not_found_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.promote_finding_to_observation("no-such-id")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_finding_triage.py -v`
Expected: FAIL — `FiligreeDB` has no `get_finding` / `list_findings_global` / `promote_finding_to_observation` (note: `update_finding` exists but has a different signature — see below)

**Step 3: Implement the DB methods**

Add to `src/filigree/db_files.py` (after the existing `get_findings_paginated` method):

- `get_finding(finding_id)` — SELECT from scan_findings by ID, raise KeyError if not found
- `list_findings_global(severity?, status?, scan_source?, scan_run_id?, file_id?, issue_id?, limit=100, offset=0)` — project-wide query, returns `{"findings": [...], "total": N, "limit": ..., "offset": ...}`
- `update_finding` — **IMPORTANT:** This method ALREADY EXISTS at `db_files.py:830` with signature `(self, file_id, finding_id, *, status, issue_id) -> ScanFinding`. The existing implementation handles status validation and issue_id linking. Extend the existing `update_finding` at line 830 to make `file_id` optional (the plan's tests call it with just `finding_id`). Change the signature to `(self, finding_id, *, file_id=None, status, issue_id)` and look up `file_id` from the finding record when not provided. Do NOT create a second `update_finding` method.
- `promote_finding_to_observation(finding_id, priority?, actor?)` — read finding, call `self.create_observation(...)`, return observation dict

For `promote_finding_to_observation`, priority mapping from severity:
```python
_SEVERITY_TO_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 3}
```

**Step 4: Run tests**

Run: `uv run pytest tests/core/test_finding_triage.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/filigree/db_files.py tests/core/test_finding_triage.py
git commit -m "feat(core): add finding triage DB methods — get, list global, update, promote"
```

---

### Task 5: MCP tools — finding triage in `mcp_tools/files.py`

**Files:**
- Modify: `src/filigree/mcp_tools/files.py` (add 6 new tools + handlers)
- Modify: `src/filigree/types/inputs.py` (add TypedDicts + register in TOOL_ARGS_MAP)
- Test: `tests/api/test_files_api.py` (add MCP handler tests)

**Step 1: Add input TypedDicts to `src/filigree/types/inputs.py`**

In the `# files.py handlers` section, add:

```python
class GetFindingArgs(TypedDict):
    finding_id: str


class ListFindingsArgs(TypedDict):
    severity: NotRequired[str]
    status: NotRequired[str]
    scan_source: NotRequired[str]
    scan_run_id: NotRequired[str]
    file_id: NotRequired[str]
    issue_id: NotRequired[str]
    limit: NotRequired[int]
    offset: NotRequired[int]


class UpdateFindingArgs(TypedDict):
    finding_id: str
    status: NotRequired[str]
    issue_id: NotRequired[str]


class BatchUpdateFindingsArgs(TypedDict):
    finding_ids: list[str]
    status: str


class PromoteFindingArgs(TypedDict):
    finding_id: str
    priority: NotRequired[int]
    actor: NotRequired[str]


class DismissFindingArgs(TypedDict):
    finding_id: str
    reason: NotRequired[str]
```

Register all 6 in `TOOL_ARGS_MAP` under the `# files.py` section:
```python
"get_finding": GetFindingArgs,
"list_findings": ListFindingsArgs,
"update_finding": UpdateFindingArgs,
"batch_update_findings": BatchUpdateFindingsArgs,
"promote_finding": PromoteFindingArgs,
"dismiss_finding": DismissFindingArgs,
```

**Step 2: Add Tool definitions and handlers to `mcp_tools/files.py`**

Add 6 `Tool(...)` entries to the `tools` list in `register()`, and 6 `_handle_*` async functions. Follow the exact pattern of existing tools in the file (parse args, get db, validate, call db method, return `_text(result)`).

Key handler signatures:
- `_handle_get_finding(arguments)` — calls `db.get_finding(finding_id)`
- `_handle_list_findings(arguments)` — calls `db.list_findings_global(**filters)`
- `_handle_update_finding(arguments)` — calls `db.update_finding(finding_id, status=..., issue_id=...)`
- `_handle_batch_update_findings(arguments)` — loops `db.update_finding(id, status=...)` for each id, collects errors
- `_handle_promote_finding(arguments)` — calls `db.promote_finding_to_observation(finding_id, priority=..., actor=...)`
- `_handle_dismiss_finding(arguments)` — calls `db.update_finding(finding_id, status="false_positive")`

**Step 3: Run input type sync test**

Run: `uv run pytest tests/api/test_input_type_contracts.py -v`
Expected: PASS (TypedDicts match JSON schemas)

**Step 4: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/filigree/mcp_tools/files.py src/filigree/types/inputs.py
git commit -m "feat(mcp): add 6 finding triage MCP tools"
```

---

## Phase 3: Scanner Lifecycle — MCP tools and trigger rework

### Task 6: Create `mcp_tools/scanners.py` — move and extend scanner tools

**Files:**
- Create: `src/filigree/mcp_tools/scanners.py`
- Modify: `src/filigree/mcp_tools/files.py` (remove `list_scanners`, `trigger_scan` tools + handlers)
- Modify: `src/filigree/mcp_server.py` (add `scanners` import, remove `_scan_cooldowns`)
- Modify: `src/filigree/types/inputs.py` (add new TypedDicts, move `TriggerScanArgs` to scanners section)

**Step 1: Add input TypedDicts**

In `src/filigree/types/inputs.py`, add a new `# scanners.py handlers` section:

```python
# ---------------------------------------------------------------------------
# scanners.py handlers
# ---------------------------------------------------------------------------


class TriggerScanBatchArgs(TypedDict):
    scanner: str
    file_paths: list[str]
    api_url: NotRequired[str]


class GetScanStatusArgs(TypedDict):
    scan_run_id: str
    log_lines: NotRequired[int]


class PreviewScanArgs(TypedDict):
    scanner: str
    file_path: str
```

Move `TriggerScanArgs` from `# files.py handlers` to `# scanners.py handlers`.
Add `"list_scanners"` to the no-argument exclusion comment near TOOL_ARGS_MAP.
Register new tools in TOOL_ARGS_MAP:
```python
# scanners.py
"trigger_scan": TriggerScanArgs,
"trigger_scan_batch": TriggerScanBatchArgs,
"get_scan_status": GetScanStatusArgs,
"preview_scan": PreviewScanArgs,
```
Remove `"trigger_scan": TriggerScanArgs` from the `# files.py` section.

**Step 2: Create `src/filigree/mcp_tools/scanners.py`**

This file should:
1. Define `register()` returning 5 tools: `list_scanners`, `trigger_scan`, `trigger_scan_batch`, `get_scan_status`, `preview_scan`
2. Move `_handle_list_scanners` and `_handle_trigger_scan` from `files.py` (adapt `trigger_scan` to use `db.create_scan_run` + `db.update_scan_run_status` + `db.check_scan_cooldown` instead of in-memory cooldown dict)
3. Add `_handle_trigger_scan_batch` — same logic as trigger_scan but accepts `file_paths[]`, registers all files, passes all to scanner
4. Add `_handle_get_scan_status` — calls `db.get_scan_status(scan_run_id, log_lines=...)`
5. Add `_handle_preview_scan` — loads scanner config, calls `build_command()`, returns expanded command without spawning

Key changes to `trigger_scan`:
- Replace `_scan_cooldowns` check with `db.check_scan_cooldown(scanner_name, canonical_path)`
- After successful Popen: `db.create_scan_run(...)` then `db.update_scan_run_status(run_id, "running")`
- On spawn failure: `db.update_scan_run_status(run_id, "failed", error_message=...)`
- Update tool description to document the polling workflow

> **Current cooldown implementation being replaced:**
> The existing cooldown is IN-MEMORY, not DB-persisted:
> - `mcp_server.py:60-62`: `_scan_cooldowns: dict[tuple[str, str, str], float] = {}` (module-level dict) and `_SCAN_COOLDOWN_SECONDS = 30` (line 63)
> - `mcp_tools/files.py:447-505`: Full cooldown logic using `time.monotonic()`
> - Key structure: `(project_scope, scanner_name, canonical_path)` -> `float` (monotonic timestamp)
> - Cooldown cleanup: stale entries purged on each trigger check (lines 449-451)
> - Cooldown reservation: set BEFORE await points to prevent concurrent triggers (lines 463-465)
> - Cooldown release: deleted on subprocess failure (lines 478, 483, 505)
>
> The plan's `check_scan_cooldown` in `ScansMixin` replaces this with DB-persisted cooldown via the `scan_runs` table.

**Step 3: Register in `mcp_server.py` (additive — only NEW tools first)**

> **IMPORTANT (review callout #4):** Do NOT remove tools from `files.py` yet, and do NOT
> register overlapping tools from `scanners.py`. In this step, `scanners.py`'s `register()`
> must only return the 3 tools that don't exist in `files.py`: `trigger_scan_batch`,
> `get_scan_status`, and `preview_scan`. Guard the overlapping tools (`list_scanners`,
> `trigger_scan`) behind an `include_legacy=False` flag or simply don't add them to the
> returned `tools` list yet. They will be moved in Step 5.
>
> The MCP server will raise on duplicate tool names if both modules register
> `list_scanners` / `trigger_scan` simultaneously.

In `src/filigree/mcp_server.py`:
- Add to imports: `scanners as _scanners_mod,`
- Add to the `for _mod in (...)` tuple: `_scanners_mod`

In `src/filigree/mcp_tools/scanners.py`:
- The `register()` function returns only 3 tools: `trigger_scan_batch`, `get_scan_status`, `preview_scan`
- The handler implementations for `list_scanners` and `trigger_scan` should exist in the file (ready for Step 5) but NOT be included in the returned tools/handlers yet

**Step 4: Run tests to verify new tools work alongside old ones**

Run: `uv run pytest --tb=short -q`
Expected: ALL PASS (no tool name collisions because only the 3 new tools are registered from `scanners.py`)

**Step 5: Remove from `files.py` and clean up `mcp_server.py`**

Now that `scanners.py` is verified:
- Remove `list_scanners` and `trigger_scan` Tool definitions, handler functions, and their entries in `handlers` dict from `mcp_tools/files.py`. Remove scanner-related imports (`list_scanners as _list_scanners`, `load_scanner`, `validate_scanner_command`).
- In `mcp_server.py`: remove `_scan_cooldowns` dict (line 62) and `_SCAN_COOLDOWN_SECONDS` constant (line 63). This replaces the in-memory cooldown at `mcp_server.py:60-63` and the monotonic clock cooldown logic at `mcp_tools/files.py:447-505`. After implementing, also remove all `_scan_cooldowns` references and `time.monotonic()` cooldown logic from `mcp_tools/files.py`.

**Step 6: Run tests again**

Run: `uv run pytest --tb=short -q`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add src/filigree/mcp_tools/scanners.py src/filigree/mcp_tools/files.py \
    src/filigree/mcp_server.py src/filigree/types/inputs.py
git commit -m "feat(mcp): extract scanner lifecycle tools into mcp_tools/scanners.py"
```

---

## Phase 4: `process_scan_results` Rework

### Task 7: Replace `create_issues` with `create_observations`

> **Atomicity note:** This task changes the `process_scan_results` signature (a breaking
> change). If any step fails after Step 3, revert ALL changes in this task before retrying.
> Do not leave a half-migrated signature where `create_issues` is removed but
> `create_observations` is not yet wired up.

> **CHANGELOG note:** Removing `issues_created` and `issue_ids` from the `POST /api/v1/scan-results`
> response body is a breaking change for external consumers. Add an entry to `CHANGELOG.md`
> under `[Unreleased]` → `Changed`: _"`POST /api/v1/scan-results` response replaces
> `issues_created`/`issue_ids` with `observations_created` count."_

**Files:**
- Modify: `src/filigree/db_files.py` (change `process_scan_results` signature and internals)
- Modify: `src/filigree/dashboard_routes/files.py` (update REST endpoint)
- Modify: `src/filigree/types/files.py` (update `ScanIngestResult`)
- Modify: `tests/core/test_files.py` (update existing tests, add new ones)
- Modify: `CHANGELOG.md` (breaking change entry)

**Step 1: Write failing test for `create_observations`**

Add to `tests/core/test_files.py`:

```python
class TestProcessScanResultsCreateObservations:
    def test_creates_observations_for_new_findings(self, db: FiligreeDB) -> None:
        db.register_file("src/main.py")
        result = db.process_scan_results(
            scan_source="test",
            findings=[
                {"path": "src/main.py", "rule_id": "r1", "severity": "high", "message": "Bug found", "line_start": 10},
            ],
            create_observations=True,
        )
        assert result["findings_created"] == 1
        obs = db.list_observations()
        assert len(obs["observations"]) == 1
        assert "[test]" in obs["observations"][0]["summary"]
        assert obs["observations"][0]["priority"] == 1  # high -> P1

    def test_no_observations_when_flag_false(self, db: FiligreeDB) -> None:
        db.register_file("src/main.py")
        db.process_scan_results(
            scan_source="test",
            findings=[
                {"path": "src/main.py", "rule_id": "r1", "severity": "high", "message": "Bug"},
            ],
            create_observations=False,
        )
        obs = db.list_observations()
        assert len(obs["observations"]) == 0
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_files.py::TestProcessScanResultsCreateObservations -v`
Expected: FAIL — unexpected keyword argument `create_observations`

**Step 3: Modify `process_scan_results`**

> **Current state of `process_scan_results` (at `db_files.py:724`):**
> ```python
> def process_scan_results(
>     self,
>     *,
>     scan_source: str,
>     findings: list[dict[str, Any]],
>     scan_run_id: str = "",
>     mark_unseen: bool = False,
>     create_issues: bool = False,
> ) -> ScanIngestResult:
> ```
> The `create_issues` parameter is currently functional — it calls `_create_issue_for_finding()` (at line 625-702).
> `_mark_unseen_findings()` is at lines 704-723. These are separate methods, not lambdas.

In `src/filigree/db_files.py`:
1. Change parameter: `create_issues: bool = False` → `create_observations: bool = False`
2. Remove the entire `_create_issue_for_finding` method (lines 625-702)
3. Remove the `if create_issues:` block that called it
4. Add observation creation logic after finding insert (inside the `for f in findings:` loop, after a new finding is created):

```python
if create_observations and finding_id in stats["new_finding_ids"]:
    severity_to_priority = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 3}
    first_line = f.get("message", "").strip().splitlines()[0] if f.get("message", "").strip() else "Scanner finding"
    obs_summary = f"[{scan_source}] {path}:{f.get('line_start', '?')} -- {first_line}"
    obs_detail = f.get("message", "")
    if f.get("suggestion"):
        obs_detail += f"\n\nSuggested fix:\n{f['suggestion']}"
    self.create_observation(
        obs_summary,
        detail=obs_detail,
        file_path=path,
        line=f.get("line_start"),
        priority=severity_to_priority.get(f.get("severity", "info"), 3),
        actor=f"scanner:{scan_source}",
    )
```

4. Update `ScanIngestResult` in `types/files.py`: remove `issues_created` and `issue_ids` fields, add `observations_created: int`
5. Update stats tracking accordingly

**Step 4: Update REST endpoint**

In `src/filigree/dashboard_routes/files.py`, in `api_scan_results`:
- Remove the `create_issues` block that returns an error
- Add: `create_observations = body.get("create_observations", False)`
- Pass `create_observations=create_observations` to `db.process_scan_results`

**Step 5: Update existing tests that reference `create_issues`**

Search for `create_issues` in tests and update:
Run: `grep -r "create_issues" tests/`
Update any references to use `create_observations` or remove them.

**Step 6: Also update `scan_runs.findings_count` on ingestion**

In `process_scan_results`, after the main loop, if `scan_run_id` is non-empty:
```python
if scan_run_id:
    try:
        self.update_scan_run_status(
            scan_run_id, "completed",
            findings_count=stats["findings_created"] + stats["findings_updated"],
        )
    except (KeyError, ValueError):
        pass  # scan_run may not exist if results were POSTed without trigger_scan
```

**Step 7: Run tests**

Run: `uv run pytest --tb=short -q`
Expected: ALL PASS

**Step 8: Commit**

```bash
git add src/filigree/db_files.py src/filigree/dashboard_routes/files.py \
    src/filigree/types/files.py tests/ CHANGELOG.md
git commit -m "feat(core)!: replace create_issues with create_observations in process_scan_results"
```

---

## Phase 5: Claude Code Scanner

### Task 8: Refactor `scan_utils.py` — extract shared pipeline

> **IMPORTANT:** `scripts/scan_utils.py` already exists and contains CLI scanner pipeline utilities
> (find_files, parse_findings, post_to_api, severity_map, estimate_tokens, load_context). This task
> MODIFIES that existing file to add `run_scanner_pipeline`. Do NOT confuse this with any future
> `src/filigree/scan_utils.py` — if the architecture ever introduces a DB-layer scan utilities
> module in `src/`, it is a DIFFERENT file from this CLI-layer `scripts/scan_utils.py`.

**Files:**
- Modify: `scripts/scan_utils.py` (add `run_scanner_pipeline`)
- Modify: `scripts/codex_bug_hunt.py` (use shared pipeline)
- Test: `tests/util/test_scan_utils.py` (add pipeline test)

**Step 1: Extract shared pipeline**

Add to `scripts/scan_utils.py` a `run_scanner_pipeline()` function that encapsulates the shared logic from `codex_bug_hunt.py`:
- Arg parsing (shared CLI flags: `--root`, `--file`, `--max-files`, `--api-url`, `--scan-run-id`, `--dry-run`, `--no-ingest`, `--model`, `--timeout`, `--batch-size`, `--skip-existing`)
- File discovery via `find_files()`
- Context loading via `load_context()`
- Batch iteration with progress reporting
- Per-file: prompt formatting → executor call → `parse_findings()` → `post_to_api()`
- Summary stats

The function accepts an `executor` callback:
```python
async def run_scanner_pipeline(
    *,
    executor: Callable[..., Awaitable[None]],
    scan_source: str,
    description: str = "",
) -> int:
```

The `executor` signature matches `run_codex` / `run_claude_code`:
```python
async def executor(*, prompt: str, output_path: Path, model: str | None, repo_root: Path, timeout: int) -> None
```

**Step 2: Slim down `codex_bug_hunt.py`**

Replace the bulk of `codex_bug_hunt.py` with:
```python
from scan_utils import run_scanner_pipeline

async def main():
    return await run_scanner_pipeline(
        executor=run_codex_with_retry,
        scan_source="codex",
        description="Codex bug hunt",
    )
```

Keep only `run_codex()`, `run_codex_with_retry()`, and the prompt template in the file.

**Step 3: Verify existing codex scanner still works**

Run: `uv run python scripts/codex_bug_hunt.py --dry-run --root src/filigree/ --max-files 3`
Expected: lists 3 files with token estimates (no actual scan)

**Step 4: Commit**

```bash
git add scripts/scan_utils.py scripts/codex_bug_hunt.py
git commit -m "refactor(scripts): extract shared scanner pipeline into scan_utils.py"
```

---

### Task 9: Create Claude Code scanner script and TOML config

**Files:**
- Create: `scripts/claude_code_bug_hunt.py`
- Create: `.filigree/scanners/claude-code.toml`

**Step 1: Create the scanner script**

Create `scripts/claude_code_bug_hunt.py`:

```python
#!/usr/bin/env python3
"""Per-file bug hunt using Claude Code CLI — scanner for filigree.

Uses `claude --print` in read-only mode. Same prompt and parsing as codex scanner.
Requires: `claude` CLI on PATH (Claude Code).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan_utils import run_scanner_pipeline

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_S = 2
STDERR_TRUNCATE = 500
DEFAULT_TIMEOUT_S = 300


async def run_claude_code(
    *,
    prompt: str,
    output_path: Path,
    model: str | None,
    repo_root: Path,
    timeout: int,
) -> None:
    """Run `claude --print` once. Raises RuntimeError on failure."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = ["claude", "--print", "-p", prompt]
    if model:
        cmd.extend(["--model", model])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=repo_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"claude --print timed out after {timeout}s") from None
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[:STDERR_TRUNCATE]
        raise RuntimeError(f"claude --print failed (rc={proc.returncode}): {err}")

    output_path.write_bytes(stdout)


async def run_claude_code_with_retry(
    *,
    prompt: str,
    output_path: Path,
    model: str | None,
    repo_root: Path,
    timeout: int,
) -> None:
    """Run claude --print with exponential backoff retries."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await run_claude_code(
                prompt=prompt,
                output_path=output_path,
                model=model,
                repo_root=repo_root,
                timeout=timeout,
            )
            return
        except (RuntimeError, TimeoutError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = RETRY_BASE_S * (2 ** (attempt - 1))
                print(f"  retry {attempt}/{MAX_RETRIES} in {wait}s ...", file=sys.stderr)
                await asyncio.sleep(wait)
    raise RuntimeError(f"all {MAX_RETRIES} attempts failed") from last_exc


def main() -> int:
    return asyncio.run(
        run_scanner_pipeline(
            executor=run_claude_code_with_retry,
            scan_source="claude-code",
            description="Claude Code bug hunt",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 2: Create the TOML config**

Create `.filigree/scanners/claude-code.toml`:

```toml
[scanner]
name = "claude-code"
description = "Per-file bug hunt using Claude Code CLI"
command = "python scripts/claude_code_bug_hunt.py"
args = ["--root", "{project_root}", "--file", "{file}", "--max-files", "1", "--api-url", "{api_url}", "--scan-run-id", "{scan_run_id}"]
file_types = ["py"]
```

**Step 3: Verify dry-run works**

Run: `uv run python scripts/claude_code_bug_hunt.py --dry-run --root src/filigree/ --max-files 3`
Expected: lists files with token estimates

**Step 4: Verify scanner registration**

Run: `uv run filigree scanners` (or equivalent CLI command)
Expected: Shows both `codex` and `claude-code` scanners

**Step 5: Commit**

```bash
git add scripts/claude_code_bug_hunt.py .filigree/scanners/claude-code.toml
git commit -m "feat(scanners): add Claude Code scanner script and TOML config"
```

---

## Phase 6: REST and Dashboard Integration

### Task 10: Update `POST /api/v1/scan-results` and scan-runs endpoint

**Files:**
- Modify: `src/filigree/dashboard_routes/files.py`
- Test: `tests/api/test_files_dashboard.py`

This task's changes were partially covered in Task 7 (the `create_observations` flag). What remains:

> **IMPORTANT (review callout #3):** Before applying Step 1, check whether Task 7's
> Step 6 already added `update_scan_run_status` to `process_scan_results`. If it did,
> the DB-layer update already fires on ingestion and this REST-layer duplicate is
> unnecessary. Only add the REST-layer call if `process_scan_results` does NOT handle it.

**Step 1: Update `api_scan_results` to mark scan_runs completed**

In the `api_scan_results` handler, after `db.process_scan_results(...)` succeeds, if `scan_run_id` is non-empty, call:
```python
try:
    db.update_scan_run_status(
        scan_run_id, "completed",
        findings_count=result.get("findings_created", 0) + result.get("findings_updated", 0),
    )
except (KeyError, ValueError):
    pass  # scan_run may not exist (e.g., direct API POST without trigger_scan)
```

Note: this may already be handled in Task 7's step 6. If so, verify it works via the REST path too.

**Step 2: Update the `_schema` endpoint**

In the schema response, update the `scan-results` endpoint documentation to show `create_observations` instead of `create_issues`.

**Step 3: Run API tests**

Run: `uv run pytest tests/api/test_files_dashboard.py -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add src/filigree/dashboard_routes/files.py tests/api/test_files_dashboard.py
git commit -m "feat(api): update scan-results endpoint for create_observations and scan_runs tracking"
```

---

## Phase 7: Cleanup and Verification

### Task 11: Update `scan_utils.py` `post_to_api` — replace `create_issues` with `create_observations`

**Files:**
- Modify: `scripts/scan_utils.py`

**Step 1: Update `post_to_api`**

Change the `create_issues` parameter to `create_observations`:
```python
def post_to_api(
    *,
    api_url: str,
    scan_source: str,
    scan_run_id: str,
    findings: list[dict[str, Any]],
    create_observations: bool = False,
) -> bool:
```

Update the payload key from `"create_issues"` to `"create_observations"`.

**Step 2: Update callers**

Search `codex_bug_hunt.py` and `claude_code_bug_hunt.py` for `create_issues` references and update.

**Step 3: Run existing scan_utils tests**

Run: `uv run pytest tests/util/test_scan_utils.py -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add scripts/scan_utils.py scripts/codex_bug_hunt.py scripts/claude_code_bug_hunt.py
git commit -m "fix(scripts): update post_to_api to use create_observations"
```

---

### Task 12: Full CI verification

**Step 1: Lint**

Run: `uv run ruff check src/ tests/ scripts/`
Expected: PASS (no errors)

**Step 2: Format**

Run: `uv run ruff format --check src/ tests/ scripts/`
Expected: PASS

**Step 3: Type check**

Run: `uv run mypy src/filigree/`
Expected: PASS (or only pre-existing errors)

**Step 4: Full test suite**

Run: `uv run pytest --tb=short`
Expected: ALL PASS

**Step 5: Commit any fixes from CI checks**

If lint/format/mypy required fixes, commit them:
```bash
git commit -m "fix: lint and type fixes from CI verification"
```
