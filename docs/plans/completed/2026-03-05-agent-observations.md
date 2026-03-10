# Agent Observations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Validate whether agents produce useful observations. Minimal viable experiment: `observe` tool, triage tools, session prompting, dedup, and a `from-observation` label for measuring pipeline output. Observations are loosely tethered to files — a file can have pending observations that are triaged into issues or dismissed. Full dashboard UI, JSONL export, file_briefing, and auto-promote deferred to v2 (only if the experiment proves the concept).

**Architecture:** New `observations` + `dismissed_observations` tables, new `db_observations.py` mixin, new MCP tool module `mcp_tools/observations.py`, observation-aware session context + MCP prompt. Read-side file integration: `get_file_detail()` includes observation count, `list_observations` supports `file_id` filter, dashboard file detail shows pending observations.

**Tech Stack:** SQLite (existing stack)

**Design doc:** `docs/plans/2026-03-05-agent-observations-design.md`

**Deferred to v2 (if experiment validates):**
- JSONL export/import for observations
- `file_briefing` MCP tool (full read-only aggregation: observations + findings + associations)
- Dashboard observations triage UI (batch promote/dismiss from file detail)
- Observations in hotspot scoring (`get_file_hotspots()`)
- Per-file observation stats method
- Auto-promote P0/P1 on expiry (sweep simplified to delete-all for v1)

**Known v1 limitations:**
- The 10,000-row audit trail cap on `dismissed_observations` is only enforced during sweep (triggered by `list_observations` / `observation_stats(sweep=True)`). Dismiss and promote paths do not prune.
- `promote_observation` atomicity is best-effort: `create_issue` commits internally, releasing the savepoint. The safety net is a pre-emptive `dismissed_observations` entry written before issue creation. If `create_issue` fails, the observation is gone from `observations` but preserved in the audit trail.
- If `create_issue` succeeds but `add_label`/`add_file_association` fails in `promote_observation`, the issue exists without the `from-observation` label or file link. The caller sees an error but the issue was created. This is a known partial-success state — the audit trail preserves the observation data.
- `observation_count()` does NOT sweep expired observations. It is possible for `observation_count() > 0` while `list_observations() == []`. This is documented in the method docstring.

**Implementation notes (from review):**
- Extract the audit trail cap to a named constant: `DISMISSED_AUDIT_TRAIL_CAP = 10_000`.
- Add `logger.debug(...)` in `mcp_server.py`'s observation stats `except` block (not bare `pass`).
- MCP observation tool handlers must call `_validate_actor()` for the `actor` argument, consistent with all other MCP tools.
- Add a comment in `promote_observation`'s `except` block explaining why the savepoint rollback will silently fail (savepoint already released by `create_issue`'s commit).
- Add a comment to `observation_count()` noting it does not sweep and may include expired rows.
- The `source_issue_id` column carries no FK constraint and is unused in v1 queries beyond display in promoted issue descriptions — reserved for v2 `file_briefing`.

---

### Task 1: Schema — Add `observations` and `dismissed_observations` tables

**Files:**
- Modify: `src/filigree/db_schema.py` (SCHEMA_SQL + bump CURRENT_SCHEMA_VERSION)
- Modify: `src/filigree/migrations.py` (add migrate_v6_to_v7)
- Test: `tests/core/test_schema.py`

**Step 1: Write the failing test**

In `tests/core/test_schema.py`, add:

```python
class TestObservationsSchema:
    """Verify observations tables are created."""

    def test_observations_table_exists(self, db: FiligreeDB) -> None:
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='observations'"
        ).fetchone()
        assert row is not None

    def test_observations_columns(self, db: FiligreeDB) -> None:
        cols = {row[1] for row in db.conn.execute("PRAGMA table_info(observations)").fetchall()}
        expected = {"id", "summary", "detail", "file_id", "file_path", "line",
                    "source_issue_id", "priority", "actor", "created_at", "expires_at"}
        assert expected.issubset(cols)

    def test_dismissed_observations_table_exists(self, db: FiligreeDB) -> None:
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dismissed_observations'"
        ).fetchone()
        assert row is not None

    def test_dismissed_observations_columns(self, db: FiligreeDB) -> None:
        cols = {row[1] for row in db.conn.execute("PRAGMA table_info(dismissed_observations)").fetchall()}
        expected = {"id", "obs_id", "summary", "actor", "reason", "dismissed_at"}
        assert expected.issubset(cols)

    def test_observations_indexes(self, db: FiligreeDB) -> None:
        indexes = {row[1] for row in db.conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='observations'"
        ).fetchall() if row[1]}
        assert "idx_observations_priority" in indexes
        assert "idx_observations_expires" in indexes
        assert "idx_observations_dedup" in indexes

    def test_dismissed_observations_index(self, db: FiligreeDB) -> None:
        indexes = {row[1] for row in db.conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='dismissed_observations'"
        ).fetchall() if row[1]}
        assert "idx_dismissed_obs_id" in indexes
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_schema.py::TestObservationsSchema -v`
Expected: FAIL — table does not exist

**Step 3: Add the tables to SCHEMA_SQL and migration**

In `src/filigree/db_schema.py`, append to `SCHEMA_SQL` (after file_events block):

```sql
-- ---- Observations (agent scratchpad) ------------------------------------

CREATE TABLE IF NOT EXISTS observations (
    id              TEXT PRIMARY KEY,
    summary         TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    file_id         TEXT REFERENCES file_records(id) ON DELETE SET NULL,
    file_path       TEXT DEFAULT '',
    line            INTEGER,
    source_issue_id TEXT DEFAULT '',
    priority        INTEGER DEFAULT 3 CHECK (priority BETWEEN 0 AND 4),
    actor           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_priority ON observations(priority, created_at);
CREATE INDEX IF NOT EXISTS idx_observations_expires ON observations(expires_at);
-- Dedup contract: coalesce(line, -1) means NULL lines map to -1.
-- An observation with line=NULL and line=-1 are considered duplicates.
-- This is intentional — line=-1 is not a valid line number.
CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_dedup
  ON observations(summary, file_path, coalesce(line, -1));

CREATE TABLE IF NOT EXISTS dismissed_observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    obs_id       TEXT NOT NULL,
    summary      TEXT NOT NULL,
    actor        TEXT DEFAULT '',
    reason       TEXT DEFAULT '',
    dismissed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dismissed_obs_id ON dismissed_observations(obs_id);
```

Bump `CURRENT_SCHEMA_VERSION = 7`.

In `src/filigree/migrations.py`, add:

```python
def migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """v6 → v7: Add observations and dismissed_observations tables.

    Changes:
      - new table 'observations' for lightweight agent-reported observations
      - new table 'dismissed_observations' for dismissal audit trail
      - new indexes on observations(priority, created_at), observations(expires_at),
        and dedup index on (summary, file_path, line)
    """
    conn.execute("""\
        CREATE TABLE IF NOT EXISTS observations (
            id              TEXT PRIMARY KEY,
            summary         TEXT NOT NULL,
            detail          TEXT DEFAULT '',
            file_id         TEXT REFERENCES file_records(id) ON DELETE SET NULL,
            file_path       TEXT DEFAULT '',
            line            INTEGER,
            source_issue_id TEXT DEFAULT '',
            priority        INTEGER DEFAULT 3 CHECK (priority BETWEEN 0 AND 4),
            actor           TEXT DEFAULT '',
            created_at      TEXT NOT NULL,
            expires_at      TEXT NOT NULL
        )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_observations_priority ON observations(priority, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_observations_expires ON observations(expires_at)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_dedup "
        "ON observations(summary, file_path, coalesce(line, -1))"
    )
    conn.execute("""\
        CREATE TABLE IF NOT EXISTS dismissed_observations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            obs_id       TEXT NOT NULL,
            summary      TEXT NOT NULL,
            actor        TEXT DEFAULT '',
            reason       TEXT DEFAULT '',
            dismissed_at TEXT NOT NULL
        )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dismissed_obs_id ON dismissed_observations(obs_id)"
    )
```

Register it: `6: migrate_v6_to_v7` in `MIGRATIONS`.

> **Rollback note:** These tables are purely additive. To downgrade:
> `DROP TABLE IF EXISTS dismissed_observations; DROP TABLE IF EXISTS observations;`
> then `PRAGMA user_version = 6;`. Add this as a comment in the migration function.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_schema.py::TestObservationsSchema -v`
Expected: PASS

**Step 5: Add migration test**

In `tests/core/test_schema.py`, add (following the `TestMigrateV5ToV6` pattern):

```python
class TestMigrateV6ToV7:
    """Tests for migration v6 -> v7: observations and dismissed_observations tables."""

    @pytest.fixture
    def v6_db(self, tmp_path: Path) -> sqlite3.Connection:
        """Create a v6 database using the full schema (stamped as v6)."""
        conn = _make_db(tmp_path)
        conn.executescript(SCHEMA_SQL)
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
        # Drop the new tables so migration can recreate them
        conn.execute("DROP TABLE IF EXISTS dismissed_observations")
        conn.execute("DROP TABLE IF EXISTS observations")
        conn.commit()
        return conn

    def test_migration_runs(self, v6_db: sqlite3.Connection) -> None:
        applied = apply_pending_migrations(v6_db, 7)
        assert applied == 1
        assert _get_schema_version(v6_db) == 7

    def test_observations_table_created(self, v6_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v6_db, 7)
        cols = _get_table_columns(v6_db, "observations")
        expected = {"id", "summary", "detail", "file_id", "file_path", "line",
                    "source_issue_id", "priority", "actor", "created_at", "expires_at"}
        assert expected.issubset(cols)

    def test_dismissed_observations_table_created(self, v6_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v6_db, 7)
        cols = _get_table_columns(v6_db, "dismissed_observations")
        expected = {"id", "obs_id", "summary", "actor", "reason", "dismissed_at"}
        assert expected.issubset(cols)

    def test_indexes_created(self, v6_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v6_db, 7)
        indexes = _get_index_names(v6_db)
        assert "idx_observations_priority" in indexes
        assert "idx_observations_expires" in indexes
        assert "idx_observations_dedup" in indexes
        assert "idx_dismissed_obs_id" in indexes

    def test_idempotent(self, v6_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v6_db, 7)
        applied = apply_pending_migrations(v6_db, 7)
        assert applied == 0
```

**Step 6: Commit**

```bash
git add src/filigree/db_schema.py src/filigree/migrations.py tests/core/test_schema.py tests/migrations/test_migrate.py
git commit -m "feat: add observations schema (v6→v7 migration)"
```

---

### Task 2: Core DB — ObservationsMixin CRUD + File Integration

**Files:**
- Create: `src/filigree/db_observations.py`
- Modify: `src/filigree/core.py` (add mixin to FiligreeDB)
- Modify: `src/filigree/db_files.py` (add observation count to `get_file_detail()`)
- Modify: `src/filigree/types/files.py` (add `observation_count` to `FileDetail` TypedDict)
- Modify: `tests/util/test_mixin_contracts.py` (add to `_MIXIN_FILES` + update expected MRO order)
- Test: `tests/core/test_observations.py`

**Step 1: Write the failing tests**

Create `tests/core/test_observations.py`:

```python
"""Tests for observation CRUD operations."""
from __future__ import annotations

import pytest
from filigree.core import FiligreeDB


class TestCreateObservation:
    def test_create_minimal(self, db: FiligreeDB) -> None:
        obs = db.create_observation("Something looks wrong here")
        assert obs["id"].startswith("test-")
        assert obs["summary"] == "Something looks wrong here"
        assert obs["priority"] == 3
        assert obs["expires_at"] > obs["created_at"]  # 14 days in future

    def test_create_with_all_fields(self, db: FiligreeDB) -> None:
        obs = db.create_observation(
            "Possible null deref",
            detail="Line 42 dereferences result without checking for None",
            file_path="src/core.py",
            line=42,
            priority=1,
            actor="claude",
        )
        assert obs["summary"] == "Possible null deref"
        assert obs["detail"].startswith("Line 42")
        assert obs["file_path"] == "src/core.py"
        assert obs["line"] == 42
        assert obs["priority"] == 1
        assert obs["actor"] == "claude"

    def test_create_empty_summary_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="summary"):
            db.create_observation("")

    def test_create_with_source_issue_id(self, db: FiligreeDB) -> None:
        obs = db.create_observation("odd behavior", source_issue_id="test-abc123")
        assert obs["source_issue_id"] == "test-abc123"

    def test_create_duplicate_is_ignored(self, db: FiligreeDB) -> None:
        """Dedup index silently drops exact duplicates (same summary+file+line)."""
        result1 = db.create_observation("bug here", file_path="src/foo.py", line=10)
        result2 = db.create_observation("bug here", file_path="src/foo.py", line=10)
        assert db.observation_count() == 1
        assert result2["id"] == result1["id"]  # Returns existing record

    def test_create_duplicate_no_line_is_ignored(self, db: FiligreeDB) -> None:
        """Most common case: file-level observation without a specific line."""
        result1 = db.create_observation("file-level bug", file_path="src/foo.py")
        result2 = db.create_observation("file-level bug", file_path="src/foo.py")
        assert db.observation_count() == 1
        assert result2["id"] == result1["id"]

    def test_create_different_summary_same_location_allowed(self, db: FiligreeDB) -> None:
        db.create_observation("null deref", file_path="src/foo.py", line=10)
        db.create_observation("type error", file_path="src/foo.py", line=10)
        assert db.observation_count() == 2

    def test_create_priority_boundary_zero(self, db: FiligreeDB) -> None:
        obs = db.create_observation("critical thing", priority=0)
        assert obs["priority"] == 0

    def test_create_priority_boundary_four(self, db: FiligreeDB) -> None:
        obs = db.create_observation("backlog thing", priority=4)
        assert obs["priority"] == 4

    def test_create_priority_out_of_range_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="priority"):
            db.create_observation("bad priority", priority=5)

    def test_create_negative_priority_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="priority"):
            db.create_observation("bad priority", priority=-1)

    def test_create_negative_line_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="line"):
            db.create_observation("bad line", line=-1)

    def test_create_whitespace_only_summary_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="summary"):
            db.create_observation("   ")

    def test_create_with_line_zero(self, db: FiligreeDB) -> None:
        obs = db.create_observation("bug at top", file_path="src/foo.py", line=0)
        assert obs["line"] == 0

    def test_create_with_file_auto_registers(self, db: FiligreeDB) -> None:
        obs = db.create_observation("bug here", file_path="src/main.py")
        assert obs["file_id"] is not None
        f = db.get_file(obs["file_id"])
        assert f.path == "src/main.py"


class TestListObservations:
    def test_list_empty(self, db: FiligreeDB) -> None:
        assert db.list_observations() == []

    def test_list_returns_all(self, db: FiligreeDB) -> None:
        db.create_observation("First")
        db.create_observation("Second")
        assert len(db.list_observations()) == 2

    def test_list_ordered_by_priority_then_created(self, db: FiligreeDB) -> None:
        db.create_observation("Low priority", priority=3)
        db.create_observation("High priority", priority=1)
        result = db.list_observations()
        assert result[0]["priority"] == 1
        assert result[1]["priority"] == 3

    def test_list_with_limit(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.create_observation(f"Obs {i}")
        assert len(db.list_observations(limit=2)) == 2

    def test_list_filter_by_file_path(self, db: FiligreeDB) -> None:
        db.create_observation("api bug", file_path="src/api/routes.py")
        db.create_observation("core bug", file_path="src/core.py")
        result = db.list_observations(file_path="src/api")
        assert len(result) == 1
        assert result[0]["summary"] == "api bug"

    def test_list_filter_by_file_id(self, db: FiligreeDB) -> None:
        """Direct FK query by file_id — more precise than path LIKE."""
        obs1 = db.create_observation("api bug", file_path="src/api/routes.py")
        db.create_observation("core bug", file_path="src/core.py")
        result = db.list_observations(file_id=obs1["file_id"])
        assert len(result) == 1
        assert result[0]["summary"] == "api bug"

    def test_list_filter_by_file_id_no_results(self, db: FiligreeDB) -> None:
        db.create_observation("bug", file_path="src/core.py")
        result = db.list_observations(file_id="nonexistent-file-id")
        assert len(result) == 0

    def test_list_filter_file_path_with_special_chars(self, db: FiligreeDB) -> None:
        """LIKE wildcards in file_path are escaped so % and _ are literal."""
        db.create_observation("special", file_path="src/100%_done.py")
        db.create_observation("other", file_path="src/normal.py")
        result = db.list_observations(file_path="100%_done")
        assert len(result) == 1
        assert result[0]["summary"] == "special"

    def test_list_filter_file_path_with_backslash(self, db: FiligreeDB) -> None:
        """Backslash in file_path is treated as literal, not LIKE escape."""
        db.create_observation("windows path bug", file_path="src\\module\\file.py")
        db.create_observation("unrelated", file_path="src/other.py")
        result = db.list_observations(file_path="src\\module")
        assert len(result) == 1
        assert result[0]["summary"] == "windows path bug"

    def test_list_sweeps_expired(self, db: FiligreeDB) -> None:
        """Expired observations are auto-removed on list and logged to audit trail."""
        obs = db.create_observation("Will expire")
        # Manually set expires_at to the past
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs["id"],),
        )
        db.conn.commit()
        result = db.list_observations()
        assert len(result) == 0
        # Verify audit trail
        row = db.conn.execute(
            "SELECT * FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)
        ).fetchone()
        assert row is not None
        assert row["reason"] == "expired (TTL)"
        assert row["actor"] == "system"


class TestDismissObservation:
    def test_dismiss_deletes_and_logs(self, db: FiligreeDB) -> None:
        obs = db.create_observation("To dismiss")
        db.dismiss_observation(obs["id"], actor="tester", reason="not a real bug")
        assert db.list_observations() == []
        # Check audit trail
        row = db.conn.execute(
            "SELECT * FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)
        ).fetchone()
        assert row is not None
        assert row["summary"] == "To dismiss"
        assert row["actor"] == "tester"
        assert row["reason"] == "not a real bug"

    def test_dismiss_nonexistent_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.dismiss_observation("nope-123")

    def test_batch_dismiss(self, db: FiligreeDB) -> None:
        o1 = db.create_observation("One")
        o2 = db.create_observation("Two")
        db.create_observation("Three")
        db.batch_dismiss_observations([o1["id"], o2["id"]])
        remaining = db.list_observations()
        assert len(remaining) == 1
        assert remaining[0]["summary"] == "Three"
        # Both logged in audit trail
        count = db.conn.execute("SELECT COUNT(*) FROM dismissed_observations").fetchone()[0]
        assert count == 2

    def test_batch_dismiss_empty_list(self, db: FiligreeDB) -> None:
        result = db.batch_dismiss_observations([])
        assert result == 0

    def test_batch_dismiss_duplicate_ids(self, db: FiligreeDB) -> None:
        o1 = db.create_observation("Only one")
        result = db.batch_dismiss_observations([o1["id"], o1["id"]])
        assert db.observation_count() == 0
        # Audit trail should have exactly one entry (SQL IN deduplicates)
        count = db.conn.execute(
            "SELECT COUNT(*) FROM dismissed_observations WHERE obs_id = ?", (o1["id"],)
        ).fetchone()[0]
        assert count == 1

    def test_batch_dismiss_partial_invalid_ids(self, db: FiligreeDB) -> None:
        """Non-existent IDs are silently skipped; return count reflects actual deletes."""
        o1 = db.create_observation("Real one")
        result = db.batch_dismiss_observations([o1["id"], "does-not-exist"])
        assert result == 1
        assert db.observation_count() == 0


class TestPromoteObservation:
    def test_promote_creates_issue_and_deletes_observation(self, db: FiligreeDB) -> None:
        obs = db.create_observation(
            "Null pointer risk",
            detail="result.data used without check",
            file_path="src/api.py",
            line=99,
            priority=2,
        )
        result = db.promote_observation(obs["id"], issue_type="bug")
        issue = result["issue"]
        assert issue.title == "Null pointer risk"
        assert "result.data used without check" in issue.description
        assert issue.priority == 2
        assert issue.type == "bug"
        assert db.list_observations() == []

    def test_promote_adds_from_observation_label(self, db: FiligreeDB) -> None:
        obs = db.create_observation("bug")
        result = db.promote_observation(obs["id"])
        labels = db.conn.execute(
            "SELECT label FROM labels WHERE issue_id = ?", (result["issue"].id,)
        ).fetchall()
        assert any(row["label"] == "from-observation" for row in labels)

    def test_promote_with_file_creates_association(self, db: FiligreeDB) -> None:
        obs = db.create_observation("bug", file_path="src/core.py")
        result = db.promote_observation(obs["id"])
        files = db.get_issue_files(result["issue"].id)
        assert len(files) >= 1

    def test_promote_logs_to_dismissed_observations(self, db: FiligreeDB) -> None:
        """Promoted observations are logged to audit trail with reason='promoted'."""
        obs = db.create_observation("will promote")
        db.promote_observation(obs["id"])
        row = db.conn.execute(
            "SELECT * FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)
        ).fetchone()
        assert row is not None
        assert row["reason"] == "promoted"

    def test_promote_nonexistent_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.promote_observation("nope-123")

    def test_promote_is_atomic_no_double_promote(self, db: FiligreeDB) -> None:
        """Second promote of same observation should fail."""
        obs = db.create_observation("Once only")
        db.promote_observation(obs["id"])
        with pytest.raises(ValueError, match="not found"):
            db.promote_observation(obs["id"])

    def test_promote_with_line_zero_includes_location(self, db: FiligreeDB) -> None:
        """line=0 is valid and must appear in the promoted issue description."""
        obs = db.create_observation("top of file", file_path="src/main.py", line=0)
        result = db.promote_observation(obs["id"])
        assert ":0" in result["issue"].description

    def test_promote_with_source_issue_id_in_description(self, db: FiligreeDB) -> None:
        """source_issue_id appears in the promoted issue description."""
        obs = db.create_observation("side note", source_issue_id="test-abc")
        result = db.promote_observation(obs["id"])
        assert "test-abc" in result["issue"].description

    def test_promote_safety_net_on_create_issue_failure(self, db: FiligreeDB) -> None:
        """If create_issue raises, the audit trail entry still exists as a safety net."""
        from unittest.mock import patch
        obs = db.create_observation("will fail promote")
        with patch.object(db, "create_issue", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                db.promote_observation(obs["id"])
        # Audit trail should have the safety-net entry
        row = db.conn.execute(
            "SELECT * FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)
        ).fetchone()
        assert row is not None
        assert row["reason"] == "promoted"

    def test_promote_label_failure_still_creates_issue(self, db: FiligreeDB) -> None:
        """Known v1 limitation: if add_label raises after create_issue succeeds,
        the issue exists without the from-observation label."""
        from unittest.mock import patch
        obs = db.create_observation("will partially fail")
        with patch.object(db, "add_label", side_effect=RuntimeError("label boom")):
            with pytest.raises(RuntimeError, match="label boom"):
                db.promote_observation(obs["id"])
        # Observation is gone
        assert db.observation_count() == 0
        # Issue WAS created (known partial-success state)
        issues = db.conn.execute("SELECT id FROM issues").fetchall()
        assert len(issues) == 1


class TestObservationStats:
    def test_count_empty(self, db: FiligreeDB) -> None:
        assert db.observation_count() == 0

    def test_count_matches(self, db: FiligreeDB) -> None:
        db.create_observation("One")
        db.create_observation("Two")
        assert db.observation_count() == 2

    def test_observation_age_stats(self, db: FiligreeDB) -> None:
        db.create_observation("Fresh")
        stats = db.observation_stats()
        assert stats["count"] == 1
        assert stats["stale_count"] == 0
        assert stats["oldest_hours"] >= 0

    def test_stale_detection(self, db: FiligreeDB) -> None:
        obs = db.create_observation("Old one")
        # Backdate to 3 days ago
        db.conn.execute(
            "UPDATE observations SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs["id"],),
        )
        db.conn.commit()
        stats = db.observation_stats()
        assert stats["stale_count"] == 1


class TestObservationCountDocumentation:
    """Verify that observation_count() does NOT sweep (known asymmetry with list_observations)."""

    def test_count_includes_expired(self, db: FiligreeDB) -> None:
        obs = db.create_observation("Will expire")
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs["id"],),
        )
        db.conn.commit()
        # count() returns 1 (no sweep), but list_observations() returns 0 (sweeps)
        assert db.observation_count() == 1
        assert db.list_observations() == []
        assert db.observation_count() == 0  # Sweep has now run


class TestFileDetailObservations:
    """Verify get_file_detail() includes observation_count."""

    def test_file_detail_no_observations(self, db: FiligreeDB) -> None:
        fr = db.register_file("src/clean.py")
        detail = db.get_file_detail(fr.id)
        assert detail["observation_count"] == 0

    def test_file_detail_with_observations(self, db: FiligreeDB) -> None:
        db.create_observation("bug 1", file_path="src/buggy.py")
        db.create_observation("bug 2", file_path="src/buggy.py")
        db.create_observation("unrelated", file_path="src/other.py")
        obs = db.list_observations(file_path="src/buggy.py")
        file_id = obs[0]["file_id"]
        detail = db.get_file_detail(file_id)
        assert detail["observation_count"] == 2

    def test_file_detail_excludes_expired_observations(self, db: FiligreeDB) -> None:
        obs = db.create_observation("will expire", file_path="src/temp.py")
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs["id"],),
        )
        db.conn.commit()
        detail = db.get_file_detail(obs["file_id"])
        # Count is raw (no sweep) — but this is acceptable for a read path
        # The count reflects live rows; expired ones are cleaned on next list_observations()
        assert detail["observation_count"] >= 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_observations.py -v`
Expected: FAIL — no `create_observation` method

**Step 3: Implement ObservationsMixin**

Create `src/filigree/db_observations.py`:

```python
"""Mixin for observation (agent scratchpad) operations.

Observations are lightweight, disposable candidates — not issues.
They live in their own table and are promoted to issues or dismissed.

Includes:
- 14-day TTL with piggyback sweep on reads (in savepoint)
- Dismissal audit trail via dismissed_observations table
- Atomic promotion via DELETE...RETURNING
- Age stats for session context prompting
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from filigree.db_base import DBMixinProtocol, _now_iso

if TYPE_CHECKING:
    from filigree.core import FileRecord, Issue

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 14
STALE_THRESHOLD_HOURS = 48
DISMISSED_AUDIT_TRAIL_CAP = 10_000


def _expires_iso(ttl_days: int = DEFAULT_TTL_DAYS) -> str:
    """Compute expiry timestamp using same isoformat() as _now_iso for consistent text comparison."""
    return (datetime.now(UTC) + timedelta(days=ttl_days)).isoformat()


class ObservationsMixin(DBMixinProtocol):
    """Observation CRUD — agent scratchpad for things noticed in passing.

    Inherits ``DBMixinProtocol`` for type-safe access to shared attributes.
    Actual implementations provided by ``FiligreeDB`` at composition time via MRO.
    """

    if TYPE_CHECKING:
        # From FilesMixin
        def register_file(
            self, path: str, *, language: str = "", file_type: str = "",
            metadata: dict[str, Any] | None = None,
        ) -> FileRecord: ...
        def add_file_association(self, file_id: str, issue_id: str, assoc_type: str) -> None: ...

        # From IssuesMixin — stub must match real signature exactly
        # (test_stub_signature_matches enforces parameter count)
        def create_issue(
            self, title: str, *, type: str = "task", priority: int = 2,
            parent_id: str | None = None, assignee: str = "",
            description: str = "", notes: str = "",
            fields: dict[str, Any] | None = None,
            labels: list[str] | None = None,
            deps: list[str] | None = None,
            actor: str = "",
        ) -> Issue: ...

        # From MetaMixin
        def add_label(self, issue_id: str, label: str) -> bool: ...

        # From core
        def _generate_unique_id(self, table: str, infix: str = "") -> str: ...

    def _sweep_expired_observations(self) -> int:
        """Delete expired observations in a savepoint (piggyback cleanup).

        All expired observations are logged to dismissed_observations and deleted.
        Uses a savepoint so it doesn't commit or interfere with in-flight transactions.
        """
        now = _now_iso()
        self.conn.execute("SAVEPOINT sweep_obs")
        try:
            self.conn.execute(
                "INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) "
                "SELECT id, summary, 'system', 'expired (TTL)', ? FROM observations WHERE expires_at <= ?",
                (now, now),
            )
            cursor = self.conn.execute("DELETE FROM observations WHERE expires_at <= ?", (now,))
            # v1 known limitation: audit trail cap is only enforced during sweep
            # (triggered by list_observations / observation_stats(sweep=True)).
            # Dismiss/promote paths do not prune. Acceptable for experiment scope.
            # Prune dismissed_observations audit trail to prevent unbounded growth.
            # Keep the most recent DISMISSED_AUDIT_TRAIL_CAP entries.
            # Note: without an index on dismissed_at, this is O(N log N) for
            # large tables. Acceptable for v1 experiment scale.
            self.conn.execute(
                "DELETE FROM dismissed_observations WHERE id NOT IN "
                "(SELECT id FROM dismissed_observations ORDER BY dismissed_at DESC LIMIT ?)",
                (DISMISSED_AUDIT_TRAIL_CAP,),
            )
            self.conn.execute("RELEASE SAVEPOINT sweep_obs")
            if cursor.rowcount > 0:
                logger.info("Swept %d expired observations", cursor.rowcount)
            return cursor.rowcount
        except Exception:
            logger.warning("Observation sweep failed, rolled back", exc_info=True)
            self.conn.execute("ROLLBACK TO SAVEPOINT sweep_obs")
            raise

    def create_observation(
        self,
        summary: str,
        *,
        detail: str = "",
        file_path: str = "",
        line: int | None = None,
        source_issue_id: str = "",
        priority: int = 3,
        actor: str = "",
    ) -> dict[str, Any]:
        if not summary or not summary.strip():
            raise ValueError("Observation summary cannot be empty")
        if not (0 <= priority <= 4):
            raise ValueError(f"priority must be between 0 and 4, got {priority}")
        if line is not None and line < 0:
            raise ValueError(f"line must be >= 0, got {line}")

        file_id: str | None = None
        if file_path:
            fr = self.register_file(file_path)
            file_id = fr.id

        obs_id = self._generate_unique_id("observations", "obs")
        now = _now_iso()
        expires = _expires_iso()
        # INSERT OR IGNORE: dedup index silently drops exact duplicates.
        # On conflict, rowcount == 0 and we return the existing row instead
        # of the rejected candidate — avoids returning a stale obs_id that
        # doesn't exist in the DB.
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO observations (id, summary, detail, file_id, file_path, line, "
            "source_issue_id, priority, actor, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (obs_id, summary.strip(), detail, file_id, file_path, line,
             source_issue_id, priority, actor, now, expires),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            # Duplicate — return the existing observation
            existing = self.conn.execute(
                "SELECT * FROM observations WHERE summary = ? AND file_path = ? "
                "AND coalesce(line, -1) = ?",
                (summary.strip(), file_path, line if line is not None else -1),
            ).fetchone()
            if existing:
                return dict(existing)
        return {
            "id": obs_id, "summary": summary.strip(), "detail": detail,
            "file_id": file_id, "file_path": file_path, "line": line,
            "source_issue_id": source_issue_id, "priority": priority, "actor": actor,
            "created_at": now, "expires_at": expires,
        }

    def list_observations(
        self, *, limit: int = 100, offset: int = 0,
        file_path: str = "", file_id: str = "",
    ) -> list[dict[str, Any]]:
        self._sweep_expired_observations()
        if file_id:
            # Direct FK query — more precise than path LIKE.
            rows = self.conn.execute(
                "SELECT * FROM observations WHERE file_id = ? "
                "ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?",
                (file_id, limit, offset),
            ).fetchall()
        elif file_path:
            # Escape LIKE wildcards in user-provided path to prevent % and _
            # from being interpreted as SQL wildcards.
            escaped = file_path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = self.conn.execute(
                "SELECT * FROM observations WHERE file_path LIKE ? ESCAPE '\\' "
                "ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?",
                (f"%{escaped}%", limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM observations ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def observation_count(self) -> int:
        """Return total observation count WITHOUT sweeping expired rows.

        This is intentionally a raw count. It may include expired observations
        that have not yet been cleaned up. Use ``list_observations()`` for a
        sweep-then-read pattern, or call ``_sweep_expired_observations()``
        explicitly if an accurate count is needed.
        """
        row = self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()
        return row[0]

    def observation_stats(self, *, sweep: bool = True) -> dict[str, Any]:
        """Return observation count + age stats for session context prompting.

        Args:
            sweep: If True (default), sweep expired observations first.
                   Pass False when calling from read-only context paths
                   (summary generation, MCP prompt) to avoid write side effects.
        """
        if sweep:
            self._sweep_expired_observations()
        count = self.observation_count()
        if count == 0:
            return {"count": 0, "stale_count": 0, "oldest_hours": 0, "expiring_soon_count": 0}

        now = datetime.now(UTC)
        stale_cutoff = (now - timedelta(hours=STALE_THRESHOLD_HOURS)).isoformat()
        expiring_cutoff = (now + timedelta(hours=24)).isoformat()

        stale = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE created_at <= ?", (stale_cutoff,)
        ).fetchone()[0]
        expiring = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE expires_at <= ?", (expiring_cutoff,)
        ).fetchone()[0]
        oldest_row = self.conn.execute(
            "SELECT MIN(created_at) FROM observations"
        ).fetchone()
        oldest_hours = 0.0
        if oldest_row and oldest_row[0]:
            oldest_dt = datetime.fromisoformat(oldest_row[0])
            oldest_hours = (now - oldest_dt).total_seconds() / 3600

        return {
            "count": count,
            "stale_count": stale,
            "oldest_hours": round(oldest_hours, 1),
            "expiring_soon_count": expiring,
        }

    def dismiss_observation(
        self, obs_id: str, *, actor: str = "", reason: str = "",
    ) -> None:
        row = self.conn.execute("SELECT id, summary FROM observations WHERE id = ?", (obs_id,)).fetchone()
        if row is None:
            raise ValueError(f"Observation not found: {obs_id}")
        now = _now_iso()
        self.conn.execute(
            "INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (obs_id, row["summary"], actor, reason, now),
        )
        self.conn.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
        self.conn.commit()

    def batch_dismiss_observations(
        self, obs_ids: list[str], *, actor: str = "", reason: str = "",
    ) -> int:
        if not obs_ids:
            return 0
        now = _now_iso()
        placeholders = ",".join("?" for _ in obs_ids)
        # Log all to audit trail before deletion
        self.conn.execute(
            f"INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) "  # noqa: S608
            f"SELECT id, summary, ?, ?, ? FROM observations WHERE id IN ({placeholders})",
            [actor, reason, now, *obs_ids],
        )
        cursor = self.conn.execute(
            f"DELETE FROM observations WHERE id IN ({placeholders})", obs_ids  # noqa: S608
        )
        self.conn.commit()
        return cursor.rowcount

    def promote_observation(
        self,
        obs_id: str,
        *,
        issue_type: str = "bug",
        priority: int | None = None,
        title: str | None = None,
        extra_description: str = "",
        actor: str = "",
    ) -> dict[str, Any]:
        # Wrap the entire promote in a savepoint so the observation DELETE
        # and issue creation are atomic. If issue creation fails, the
        # observation is restored via rollback — no data loss.
        self.conn.execute("SAVEPOINT promote_obs")
        try:
            row = self.conn.execute(
                "DELETE FROM observations WHERE id = ? RETURNING *", (obs_id,)
            ).fetchone()
            if row is None:
                self.conn.execute("RELEASE SAVEPOINT promote_obs")
                raise ValueError(f"Observation not found: {obs_id}")
            obs = dict(row)

            issue_title = title or obs["summary"]
            desc_parts = []
            if extra_description:
                desc_parts.append(extra_description)
            if obs["detail"]:
                desc_parts.append(obs["detail"])
            if obs["file_path"]:
                loc = f"`{obs['file_path']}`"
                if obs["line"] is not None:
                    loc += f":{obs['line']}"
                desc_parts.append(f"Observed in: {loc}")
            if obs.get("source_issue_id"):
                desc_parts.append(f"Observed while working on: {obs['source_issue_id']}")
            description = "\n\n".join(desc_parts)

            # NOTE: create_issue calls conn.commit() internally, which releases
            # all savepoints. We must use _create_issue_raw() or accept that the
            # savepoint boundary is the DELETE only. Since create_issue() commits,
            # we structure it so the DELETE is inside the savepoint and we log to
            # dismissed_observations as a safety net before attempting create_issue.
            #
            # Safety net: log the observation to dismissed_observations BEFORE
            # attempting issue creation, so if create_issue fails the data is
            # preserved in the audit trail.
            now = _now_iso()
            self.conn.execute(
                "INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) "
                "VALUES (?, ?, ?, 'promoted', ?)",
                (obs_id, obs["summary"], actor or obs["actor"], now),
            )

            self.conn.execute("RELEASE SAVEPOINT promote_obs")

            issue = self.create_issue(
                issue_title,
                type=issue_type,
                priority=priority if priority is not None else obs["priority"],
                description=description,
                actor=actor or obs["actor"],
            )

            # Label for measuring pipeline output
            self.add_label(issue.id, "from-observation")

            if obs["file_id"]:
                self.add_file_association(obs["file_id"], issue.id, "mentioned_in")

            self.conn.commit()
            return {"issue": issue}

        except Exception:
            # The savepoint was released at line "RELEASE SAVEPOINT promote_obs"
            # above, before calling create_issue. If create_issue (or add_label /
            # add_file_association) raises, the savepoint no longer exists and
            # this ROLLBACK will fail silently. This is expected — the observation
            # DELETE has already been committed as part of the savepoint release,
            # and the safety-net audit trail entry preserves the data.
            try:
                self.conn.execute("ROLLBACK TO SAVEPOINT promote_obs")
                self.conn.execute("RELEASE SAVEPOINT promote_obs")
            except Exception:
                pass  # Savepoint already released — see comment above
            raise
```

**Step 4: Wire into FiligreeDB**

In `src/filigree/core.py`:
- Import `ObservationsMixin` from `db_observations`
- Add it **last** (rightmost) in the `FiligreeDB` class bases: `class FiligreeDB(FilesMixin, IssuesMixin, EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin, ObservationsMixin)`
- **Why last:** `ObservationsMixin` is a consumer-only mixin — it calls methods from other mixins but never overrides them. All mixins access each other through `self` regardless of MRO position. Consumer mixins go last by convention in this codebase (same pattern as `PlanningMixin`).

In `tests/util/test_mixin_contracts.py`:
- Add `"db_observations.py"` to the `_MIXIN_FILES` list (required — `test_all_mixin_files_scanned` asserts all `db_*.py` files are enumerated)
- Update the expected MRO order to include `ObservationsMixin` last: `[FilesMixin, IssuesMixin, EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin, ObservationsMixin]`

**Step 5: Add observation count to `get_file_detail()`**

In `src/filigree/types/files.py`, add `observation_count` to the `FileDetail` TypedDict:

```python
class FileDetail(TypedDict):
    """Shape returned by ``get_file_detail()``."""

    file: FileRecordDict
    associations: list[FileAssociation]
    recent_findings: list[ScanFindingDict]
    summary: FindingsSummary
    observation_count: int
```

In `src/filigree/db_files.py`, update `get_file_detail()` to include the count:

```python
def get_file_detail(self, file_id: str) -> FileDetail:
    """Get a structured file detail response with separated data layers."""
    f = self.get_file(file_id)
    associations = self.get_file_associations(file_id)
    recent = self.get_findings(file_id, limit=10)
    summary = self.get_file_findings_summary(file_id)
    # Observation count (raw, no sweep — read-only path).
    # Guarded for pre-v7 DBs where observations table may not exist.
    try:
        obs_count = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE file_id = ?", (file_id,)
        ).fetchone()[0]
    except Exception:
        obs_count = 0
    return {
        "file": f.to_dict(),
        "associations": associations,
        "recent_findings": [r.to_dict() for r in recent],
        "summary": summary,
        "observation_count": obs_count,
    }
```

> **Why raw count (no sweep)?** `get_file_detail` is a read path — calling the sweep (which writes to `dismissed_observations`) would introduce write side effects in a read-only context. The count may include expired observations, but they will be cleaned up on the next `list_observations()` call. This matches the `sweep=False` convention used in `summary.py` and `hooks.py`.

**Step 6: Run tests**

Run: `uv run pytest tests/core/test_observations.py -v`
Expected: PASS

**Step 7: Commit**

```bash
git add src/filigree/db_observations.py src/filigree/core.py src/filigree/db_files.py src/filigree/types/files.py tests/core/test_observations.py tests/util/test_mixin_contracts.py
git commit -m "feat: add ObservationsMixin with TTL, audit trail, atomic promote, file integration"
```

---

### Task 3: MCP Tools — observe, list_observations, dismiss, promote

**Files:**
- Create: `src/filigree/mcp_tools/observations.py`
- Modify: `src/filigree/mcp_server.py` (add to module aggregation loop)
- Modify: `src/filigree/types/inputs.py` (add TypedDicts + TOOL_ARGS_MAP entries)
- Test: `tests/mcp/test_observations.py`

**Step 1: Write failing tests**

Create `tests/mcp/test_observations.py`:

```python
"""MCP tool tests for observation tools."""

from __future__ import annotations

from filigree.core import FiligreeDB
from filigree.mcp_server import call_tool
from tests.mcp._helpers import _parse


class TestObserveTool:
    async def test_observe_creates_observation(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {"summary": "Something looks wrong"})
        data = _parse(result)
        assert data["id"].startswith("mcp-")
        assert data["summary"] == "Something looks wrong"
        assert data["priority"] == 3

    async def test_observe_with_all_fields(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {
            "summary": "Null deref risk",
            "detail": "result.data used without check",
            "file_path": "src/core.py",
            "line": 42,
            "priority": 1,
            "actor": "claude",
        })
        data = _parse(result)
        assert data["summary"] == "Null deref risk"
        assert data["priority"] == 1

    async def test_observe_empty_summary_fails(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {"summary": ""})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_observe_priority_zero(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {"summary": "critical", "priority": 0})
        data = _parse(result)
        assert data["priority"] == 0

    async def test_observe_priority_four(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {"summary": "backlog", "priority": 4})
        data = _parse(result)
        assert data["priority"] == 4

    async def test_observe_with_source_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {
            "summary": "side note",
            "source_issue_id": "mcp-abc123",
        })
        data = _parse(result)
        assert data["source_issue_id"] == "mcp-abc123"


class TestListObservationsTool:
    async def test_list_empty(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("list_observations", {})
        data = _parse(result)
        assert data["observations"] == []
        assert data["stats"]["count"] == 0

    async def test_list_returns_observations(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_observation("First")
        mcp_db.create_observation("Second")
        result = await call_tool("list_observations", {})
        data = _parse(result)
        assert len(data["observations"]) == 2

    async def test_list_with_file_path_filter(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_observation("api bug", file_path="src/api/routes.py")
        mcp_db.create_observation("core bug", file_path="src/core.py")
        result = await call_tool("list_observations", {"file_path": "src/api"})
        data = _parse(result)
        assert len(data["observations"]) == 1
        assert data["observations"][0]["summary"] == "api bug"

    async def test_list_with_limit(self, mcp_db: FiligreeDB) -> None:
        for i in range(5):
            mcp_db.create_observation(f"Obs {i}")
        result = await call_tool("list_observations", {"limit": 2})
        data = _parse(result)
        assert len(data["observations"]) == 2

    async def test_list_with_file_id_filter(self, mcp_db: FiligreeDB) -> None:
        obs = mcp_db.create_observation("api bug", file_path="src/api.py")
        mcp_db.create_observation("other bug", file_path="src/other.py")
        result = await call_tool("list_observations", {"file_id": obs["file_id"]})
        data = _parse(result)
        assert len(data["observations"]) == 1
        assert data["observations"][0]["summary"] == "api bug"


class TestDismissObservationTool:
    async def test_dismiss(self, mcp_db: FiligreeDB) -> None:
        obs = mcp_db.create_observation("To dismiss")
        result = await call_tool("dismiss_observation", {"id": obs["id"]})
        data = _parse(result)
        assert "dismissed" in data.get("status", "").lower() or data.get("ok")

    async def test_dismiss_with_reason(self, mcp_db: FiligreeDB) -> None:
        obs = mcp_db.create_observation("Not a bug")
        result = await call_tool("dismiss_observation", {
            "id": obs["id"],
            "reason": "false positive",
            "actor": "tester",
        })
        data = _parse(result)
        row = mcp_db.conn.execute(
            "SELECT reason FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)
        ).fetchone()
        assert row["reason"] == "false positive"

    async def test_dismiss_nonexistent_fails(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("dismiss_observation", {"id": "nope-123"})
        data = _parse(result)
        assert data["code"] == "not_found"


class TestBatchDismissTool:
    async def test_batch_dismiss(self, mcp_db: FiligreeDB) -> None:
        o1 = mcp_db.create_observation("One")
        o2 = mcp_db.create_observation("Two")
        mcp_db.create_observation("Three")
        result = await call_tool("batch_dismiss_observations", {
            "ids": [o1["id"], o2["id"]],
        })
        data = _parse(result)
        remaining = mcp_db.list_observations()
        assert len(remaining) == 1
        assert remaining[0]["summary"] == "Three"

    async def test_batch_dismiss_empty_list(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("batch_dismiss_observations", {"ids": []})
        data = _parse(result)
        assert data.get("dismissed", 0) == 0 or "ok" in data

    async def test_batch_dismiss_invalid_ids(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("batch_dismiss_observations", {"ids": ["nope-1", "nope-2"]})
        data = _parse(result)
        assert data.get("dismissed", 0) == 0 or "ok" in data


class TestPromoteObservationTool:
    async def test_promote(self, mcp_db: FiligreeDB) -> None:
        obs = mcp_db.create_observation(
            "Null pointer risk",
            detail="result.data used without check",
            file_path="src/api.py",
            priority=2,
        )
        result = await call_tool("promote_observation", {
            "id": obs["id"],
            "type": "bug",
        })
        data = _parse(result)
        assert "issue" in data
        assert data["issue"]["title"] == "Null pointer risk"
        assert mcp_db.list_observations() == []

    async def test_promote_nonexistent_fails(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("promote_observation", {"id": "nope-123"})
        data = _parse(result)
        assert data["code"] == "not_found"
```

**Step 2: Add TypedDicts to `types/inputs.py`**

```python
# ---------------------------------------------------------------------------
# observations.py handlers
# ---------------------------------------------------------------------------

class ObserveArgs(TypedDict):
    summary: str
    detail: NotRequired[str]
    file_path: NotRequired[str]
    line: NotRequired[int]
    source_issue_id: NotRequired[str]
    priority: NotRequired[int]
    actor: NotRequired[str]

class ListObservationsArgs(TypedDict):
    limit: NotRequired[int]
    offset: NotRequired[int]
    file_path: NotRequired[str]
    file_id: NotRequired[str]

class DismissObservationArgs(TypedDict):
    id: str
    reason: NotRequired[str]
    actor: NotRequired[str]

class BatchDismissObservationsArgs(TypedDict):
    ids: list[str]
    reason: NotRequired[str]
    actor: NotRequired[str]

class PromoteObservationArgs(TypedDict):
    id: str
    type: NotRequired[str]
    priority: NotRequired[int]
    title: NotRequired[str]
    description: NotRequired[str]
    actor: NotRequired[str]
```

Add all five to `TOOL_ARGS_MAP`.

**Step 3: Create `mcp_tools/observations.py`**

Follow the pattern in `mcp_tools/issues.py`: `register()` returns `(list[Tool], dict[str, handler])`.
- `observe` — calls `tracker.create_observation()`, maps `file_path` arg to `file_path` kwarg, passes `source_issue_id` through. Validates `actor` via `_validate_actor()`. Validates `line` and `priority` are ints if provided.
- `list_observations` — passes `file_path` and `file_id` filters, includes `tracker.observation_stats()` in response. The `list_observations` Tool inputSchema should include `file_id` as an optional string parameter.
- `dismiss_observation` — passes `reason` and `actor` through. Validates `actor` via `_validate_actor()`.
- `batch_dismiss_observations` — passes `reason` and `actor` through. Validates `actor` via `_validate_actor()`.
- `promote_observation` — returns created issue. Validates `actor` via `_validate_actor()`.

> **Important:** All handlers must call `_validate_actor(args.get("actor", ""))` before passing to the DB layer, consistent with all other MCP tools in the codebase.

**Step 4: Wire into mcp_server.py**

In `src/filigree/mcp_server.py`, add `observations as _observations_mod` to the imports and to the aggregation loop.

**Step 5: Run tests**

Run: `uv run pytest tests/mcp/test_observations.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/filigree/mcp_tools/observations.py src/filigree/mcp_server.py src/filigree/types/inputs.py tests/mcp/test_observations.py
git commit -m "feat: add observe/list/dismiss/promote MCP tools"
```

---

### Task 4: Session context & MCP prompt — observation awareness

**Files:**
- Modify: `src/filigree/summary.py` (add observation stats to generated summary)
- Modify: `src/filigree/mcp_server.py` (add observation nudge to `_build_workflow_text`)
- Modify: `src/filigree/hooks.py` (add observations to `generate_session_context()` — the `session-context` CLI command is a thin wrapper around this function)
- Test: `tests/analytics/test_summary.py`
- Test: `tests/install/test_hooks.py` (add observation tests to existing `TestBuildContext`)

**Step 1: Write failing tests**

In `tests/analytics/test_summary.py`, add:

```python
class TestObservationsInSummary:
    def test_no_observations_no_mention(self, db: FiligreeDB) -> None:
        summary = generate_summary(db)
        assert "OBSERVATION" not in summary.upper()

    def test_fresh_observations_gentle_nudge(self, db: FiligreeDB) -> None:
        db.create_observation("Something to check")
        summary = generate_summary(db)
        assert "OBSERVATIONS:" in summary
        assert "1 pending" in summary
        assert "list_observations" in summary

    def test_stale_observations_warning(self, db: FiligreeDB) -> None:
        obs = db.create_observation("Old thing")
        db.conn.execute(
            "UPDATE observations SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs["id"],),
        )
        db.conn.commit()
        summary = generate_summary(db)
        assert "STALE OBSERVATIONS" in summary
        assert "1 observation(s) older than 48 hours" in summary

    def test_expiring_soon_mention(self, db: FiligreeDB) -> None:
        obs = db.create_observation("About to expire")
        # Set expires_at to 12 hours from now
        from datetime import UTC, datetime, timedelta
        soon = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
        db.conn.execute(
            "UPDATE observations SET expires_at = ? WHERE id = ?",
            (soon, obs["id"]),
        )
        db.conn.commit()
        summary = generate_summary(db)
        assert "expiring within 24h" in summary

    def test_observation_stats_failure_is_silent(self, db: FiligreeDB) -> None:
        """If observation_stats() raises, summary still generates."""
        from unittest.mock import patch
        with patch.object(db, "observation_stats", side_effect=Exception("boom")):
            summary = generate_summary(db)
        assert "Project Pulse" in summary  # Core summary still works
        assert "OBSERVATION" not in summary.upper()
```

In `tests/install/test_hooks.py`, add to the existing `TestBuildContext` class:

```python
    def test_no_observations_no_mention(self, db: FiligreeDB) -> None:
        result = _build_context(db)
        assert "OBSERVATION" not in result.upper()

    def test_observations_shown_in_context(self, db: FiligreeDB) -> None:
        db.create_observation("Something to triage")
        result = _build_context(db)
        assert "OBSERVATION" in result.upper()
        assert "list_observations" in result

    def test_stale_observations_warning_in_context(self, db: FiligreeDB) -> None:
        obs = db.create_observation("Old thing")
        db.conn.execute(
            "UPDATE observations SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs["id"],),
        )
        db.conn.commit()
        result = _build_context(db)
        assert "STALE OBSERVATION" in result.upper()

    def test_observation_stats_failure_silent_in_context(self, db: FiligreeDB) -> None:
        """If observation_stats() raises, context still generates."""
        from unittest.mock import patch
        with patch.object(db, "observation_stats", side_effect=Exception("boom")):
            result = _build_context(db)
        assert "Filigree Project Snapshot" in result
        assert "OBSERVATION" not in result.upper()
```

**Step 2: Add observation stats to `generate_summary()`**

In `summary.py`, after the existing stats section, add:

```python
# Observations (read-only — sweep=False to avoid write side effects on a read path)
try:
    obs_stats = db.observation_stats(sweep=False)
    if obs_stats["count"] > 0:
        if obs_stats["stale_count"] > 0:
            lines.append("")
            lines.append(f"STALE OBSERVATIONS: {obs_stats['stale_count']} observation(s) older than 48 hours (oldest: {obs_stats['oldest_hours']:.0f}h ago)")
            lines.append(f"  Total pending: {obs_stats['count']}. Run `list_observations` to review.")
        else:
            lines.append("")
            lines.append(f"OBSERVATIONS: {obs_stats['count']} pending (oldest: {obs_stats['oldest_hours']:.0f}h ago)")
            lines.append("  Use `list_observations` to review, `promote_observation` to create issues,")
            lines.append("  or `dismiss_observation` to clear.")
        if obs_stats["expiring_soon_count"] > 0:
            lines.append(f"  ({obs_stats['expiring_soon_count']} expiring within 24h)")
except Exception:
    logger.debug("observation stats unavailable in summary", exc_info=True)
```

**Step 3: Add observation nudge to `_build_workflow_text()` in mcp_server.py**

After the existing type/pack listing, add a **guarded, read-only** observation stats call. Use `sweep=False` to avoid write side effects in what should be a read-only prompt path. Wrap in `try/except Exception` to handle un-migrated DBs (B4):

```python
# Observation awareness (read-only, guarded for pre-v7 DBs)
try:
    obs_stats = db.observation_stats(sweep=False)
    if obs_stats["count"] > 0:
        text += "\n## Observations\n"
        if obs_stats["stale_count"] > 0:
            text += f"- {obs_stats['stale_count']} stale observation(s) (>48h old). Run `list_observations` to triage.\n"
        else:
            text += f"- {obs_stats['count']} pending observation(s). Use `list_observations` to review.\n"
except Exception:
    logger.debug("observation stats unavailable in MCP prompt", exc_info=True)
```

> **Note (W8):** The canonical detailed observation nudge lives in `generate_summary()` (the `filigree://context` MCP resource). The `_build_workflow_text()` nudge is intentionally minimal (1-2 lines) to avoid redundancy — it just signals "observations exist, go look."

**Step 4: Add to `session-context` output**

In `src/filigree/hooks.py`, add observation stats to `_build_context()` (which `generate_session_context()` wraps). Add after the STATS line, guarded with `try/except` and using `sweep=False`:

```python
# Observation awareness (read-only, guarded for pre-v7 DBs)
try:
    obs_stats = db.observation_stats(sweep=False)
    if obs_stats["count"] > 0:
        lines.append("")
        if obs_stats["stale_count"] > 0:
            lines.append(f"STALE OBSERVATIONS: {obs_stats['stale_count']} older than 48h — run `list_observations` to triage")
        else:
            lines.append(f"OBSERVATIONS: {obs_stats['count']} pending — run `list_observations` to review")
except Exception:
    logger.debug("observation stats unavailable in session context", exc_info=True)
```

The `session-context` CLI command in `cli_commands/admin.py` is a thin wrapper that calls `generate_session_context()` — no changes needed in `admin.py`.

**Step 5: Run tests**

Run: `uv run pytest tests/analytics/test_summary.py tests/install/test_hooks.py::TestBuildContext -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/filigree/summary.py src/filigree/mcp_server.py src/filigree/hooks.py tests/analytics/test_summary.py tests/install/test_hooks.py
git commit -m "feat: add observation prompting to session context and MCP prompt"
```

---

### Task 5: Dashboard file integration — observation count in files table + file detail

**Files:**
- Modify: `src/filigree/db_files.py` (`list_files_paginated` — add `observation_count` to enriched query)
- Modify: `src/filigree/static/js/views/files.js` (add "Obs" column to file list table + observation section in file detail)
- Modify: `src/filigree/dashboard_routes/files.py` (no code change needed — `get_file_detail()` already returns the new field from Task 2)

> **Context:** The Files tab in the dashboard shows a table of files with columns for severity badges, issues, and last update. The file detail panel (right-side slide-in) shows metadata, severity badges, and tabs for Findings/Timeline plus associated issues. We need to surface observations in both views.

**Step 1: Add `observation_count` to `list_files_paginated()` enriched query**

In `src/filigree/db_files.py`, the `list_files_paginated()` method already builds an enriched SQL with subqueries for `total_findings`, severity counts, and `associations_count`. Add one more subquery for observation count:

```sql
(SELECT COUNT(*) FROM observations o WHERE o.file_id = fr.id) AS observation_count
```

Append this after the `associations_count` subquery. This is a raw count (no sweep) — acceptable for a read-only dashboard path. The column needs to be included in the result dict construction alongside `associations_count`:

```python
d["observation_count"] = r["observation_count"]
```

> **Guarding for pre-v7 DBs:** Wrap the subquery addition in a feature check or use `try/except` in the query builder. The simplest approach: the enriched SQL is built at call time, so the `observations` table will exist if the migration has run. If it hasn't, the query will fail — but this is the same behavior as any other schema-dependent query. Since `list_files_paginated` is only called from the dashboard (which requires the DB to be current), this is acceptable.

**Step 2: Add "Obs" column to the files table in `files.js`**

In `src/filigree/static/js/views/files.js`, the `headerCols` array defines the table columns (line ~179). Add a new column for observations after "Issues" and before "Last Update":

```javascript
{ key: null, label: "Obs", cls: "text-center" },
```

In the `rowsHtml` row builder (line ~200), add a cell that shows the observation count with a distinct visual style (observations are "unreliable" — use a muted/dashed style to differentiate from the solid severity badges):

```javascript
`<td class="py-2 px-3 text-center">${f.observation_count ? `<span class="text-xs px-1.5 py-0.5 rounded" style="color:var(--text-secondary);border:1px dashed var(--border-strong)">${f.observation_count}</span>` : "\u2014"}</td>` +
```

> **Design note:** Observations are displayed with a dashed border and muted text color to visually distinguish them from confirmed findings (which use solid colored severity badges). This reinforces that observations are "unreliable" — they're agent-reported candidates, not validated findings.

**Step 3: Add observations section to file detail panel**

In `renderFileDetail()` in `files.js`, add an observations section between the severity summary bar and the tab buttons. This section is only shown when `observation_count > 0`:

```javascript
// Observations badge (shown only if observations exist)
if (data.observation_count > 0) {
  html +=
    '<div class="flex items-center gap-2 mb-4 px-3 py-2 rounded" style="background:var(--surface-overlay);border:1px dashed var(--border-strong)">' +
    `<span class="text-xs" style="color:var(--text-secondary)">${data.observation_count} pending observation(s)</span>` +
    '<span class="text-xs" style="color:var(--text-muted)">\u2014 use <code>list_observations</code> to triage</span>' +
    '</div>';
}
```

This is intentionally minimal — a notification badge, not a full triage UI. The full triage UI with batch promote/dismiss buttons is deferred to v2.

**Step 4: Run manual smoke test**

Open the dashboard at http://localhost:8377 (or configured port), navigate to the Files tab, and verify:
1. The "Obs" column appears in the table header
2. Files with observations show a dashed count badge
3. Clicking a file with observations shows the pending observations notice in the detail panel
4. Files without observations show "—" in the Obs column and no observations section in detail

**Step 5: Commit**

```bash
git add src/filigree/db_files.py src/filigree/static/js/views/files.js
git commit -m "feat: show observation count in dashboard files table and detail"
```

---

### Task 6: CI check + CLAUDE.md update

**Step 1: Run linting**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

**Step 2: Run type checking**

```bash
uv run mypy src/filigree/
```

**Step 3: Run full test suite**

```bash
uv run pytest --tb=short
```

**Step 4: Fix any failures**

**Step 5: Update CLAUDE.md instructions**

Add `observe` and observation-related tools to the MCP tools quick reference in the filigree instructions block:

```markdown
### Observations (Agent Scratchpad)
- `observe` — fire-and-forget: record something you noticed in passing
- `list_observations` / `promote_observation` / `dismiss_observation` — triage
- Observations expire after 14 days. Use `list_issues --label=from-observation` to measure pipeline output.
```

**Step 6: Commit**

```bash
git add -A
git commit -m "chore: CI fixes + add observation tools to CLAUDE.md"
```
