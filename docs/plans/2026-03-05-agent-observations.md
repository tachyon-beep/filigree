# Agent Observations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Validate whether agents produce useful observations. Minimal viable experiment: `observe` tool, triage tools, session prompting, dedup, and a `from-observation` label for measuring pipeline output. Dashboard UI, JSONL export, file_briefing, and auto-promote deferred to v2 (only if the experiment proves the concept).

**Architecture:** New `observations` + `dismissed_observations` tables, new `db_observations.py` mixin, new MCP tool module `mcp_tools/observations.py`, observation-aware session context + MCP prompt.

**Tech Stack:** SQLite (existing stack)

**Design doc:** `docs/plans/2026-03-05-agent-observations-design.md`

**Deferred to v2 (if experiment validates):**
- JSONL export/import for observations
- `file_briefing` MCP tool (read-only aggregation)
- Dashboard API + badge/popover UI
- Auto-promote P0/P1 on expiry (sweep simplified to delete-all for v1)

---

### Task 1: Schema — Add `observations` and `dismissed_observations` tables

**Files:**
- Modify: `src/filigree/db_schema.py` (SCHEMA_SQL + bump CURRENT_SCHEMA_VERSION)
- Modify: `src/filigree/migrations.py` (add migrate_v6_to_v7)
- Test: `tests/core/test_schema.py`
- Test: `tests/migrations/test_migrate.py`

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
        assert expected == cols

    def test_dismissed_observations_table_exists(self, db: FiligreeDB) -> None:
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dismissed_observations'"
        ).fetchone()
        assert row is not None
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
```

Register it: `6: migrate_v6_to_v7` in `MIGRATIONS`.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_schema.py::TestObservationsSchema -v`
Expected: PASS

**Step 5: Add migration test**

In `tests/migrations/test_migrate.py`, add a test that creates a v6 DB and migrates to v7, verifying both tables exist.

**Step 6: Commit**

```bash
git add src/filigree/db_schema.py src/filigree/migrations.py tests/core/test_schema.py tests/migrations/test_migrate.py
git commit -m "feat: add observations schema (v6→v7 migration)"
```

---

### Task 2: Core DB — ObservationsMixin CRUD

**Files:**
- Create: `src/filigree/db_observations.py`
- Modify: `src/filigree/core.py` (add mixin to FiligreeDB)
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
        db.create_observation("bug here", file_path="src/foo.py", line=10)
        db.create_observation("bug here", file_path="src/foo.py", line=10)
        assert db.observation_count() == 1

    def test_create_different_summary_same_location_allowed(self, db: FiligreeDB) -> None:
        db.create_observation("null deref", file_path="src/foo.py", line=10)
        db.create_observation("type error", file_path="src/foo.py", line=10)
        assert db.observation_count() == 2

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

    def test_list_sweeps_expired(self, db: FiligreeDB) -> None:
        """Expired observations are auto-removed on list."""
        obs = db.create_observation("Will expire")
        # Manually set expires_at to the past
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs["id"],),
        )
        db.conn.commit()
        result = db.list_observations()
        assert len(result) == 0


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

    def test_promote_nonexistent_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.promote_observation("nope-123")

    def test_promote_is_atomic_no_double_promote(self, db: FiligreeDB) -> None:
        """Second promote of same observation should fail."""
        obs = db.create_observation("Once only")
        db.promote_observation(obs["id"])
        with pytest.raises(ValueError, match="not found"):
            db.promote_observation(obs["id"])


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

        # From IssuesMixin
        def create_issue(
            self, title: str, *, type: str = "task", priority: int = 2,
            description: str = "", **kwargs: Any,
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
            self.conn.execute("RELEASE SAVEPOINT sweep_obs")
            if cursor.rowcount > 0:
                logger.info("Swept %d expired observations", cursor.rowcount)
            return cursor.rowcount
        except Exception:
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
        self, *, limit: int = 100, offset: int = 0, file_path: str = "",
    ) -> list[dict[str, Any]]:
        self._sweep_expired_observations()
        if file_path:
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
        row = self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()
        return row[0]

    def observation_stats(self) -> dict[str, Any]:
        """Return observation count + age stats for session context prompting."""
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
        # Atomic claim: DELETE...RETURNING * as a single statement.
        # If another caller already promoted/dismissed this observation,
        # the DELETE returns no rows and we raise.
        row = self.conn.execute(
            "DELETE FROM observations WHERE id = ? RETURNING *", (obs_id,)
        ).fetchone()
        if row is None:
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
            if obs["line"]:
                loc += f":{obs['line']}"
            desc_parts.append(f"Observed in: {loc}")
        if obs.get("source_issue_id"):
            desc_parts.append(f"Observed while working on: {obs['source_issue_id']}")
        description = "\n\n".join(desc_parts)

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

        # Check for matching scan findings at the same file+line
        matching_findings: list[dict[str, Any]] = []
        if obs["file_id"] and obs["line"]:
            rows = self.conn.execute(
                "SELECT id, rule_id, severity, message FROM scan_findings "
                "WHERE file_id = ? AND line_start = ? AND status = 'open'",
                (obs["file_id"], obs["line"]),
            ).fetchall()
            matching_findings = [dict(r) for r in rows]

        return {"issue": issue, "matching_findings": matching_findings}
```

**Step 4: Wire into FiligreeDB**

In `src/filigree/core.py`:
- Import `ObservationsMixin` from `db_observations`
- Add it **first** (leftmost) in the `FiligreeDB` class bases: `class FiligreeDB(ObservationsMixin, FilesMixin, IssuesMixin, EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin)`
- **Why leftmost:** `ObservationsMixin` depends on methods from `FilesMixin` (`register_file`, `add_file_association`), `IssuesMixin` (`create_issue`, `_generate_unique_id`), and `MetaMixin` (`add_label`). Placing it first in MRO means those methods resolve correctly from the downstream mixins.

In `tests/util/test_mixin_contracts.py`:
- Add `"db_observations.py"` to the `_MIXIN_FILES` list (required — `test_all_mixin_files_scanned` asserts all `db_*.py` files are enumerated)
- Update the expected MRO order to include `ObservationsMixin` first

**Step 5: Run tests**

Run: `uv run pytest tests/core/test_observations.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/filigree/db_observations.py src/filigree/core.py tests/core/test_observations.py tests/util/test_mixin_contracts.py
git commit -m "feat: add ObservationsMixin with TTL, audit trail, atomic promote"
```

---

### Task 3: MCP Tools — observe, list_observations, dismiss, promote

**Files:**
- Create: `src/filigree/mcp_tools/observations.py`
- Modify: `src/filigree/mcp_server.py` (add to module aggregation loop)
- Modify: `src/filigree/types/inputs.py` (add TypedDicts + TOOL_ARGS_MAP entries)
- Test: `tests/mcp/test_observations.py`

**Step 1: Write failing tests**

Create `tests/mcp/test_observations.py` — test each tool via the MCP handler:
- `observe` creates an observation and returns summary
- `list_observations` returns observations + stats
- `list_observations` with `file_path` filter returns subset
- `dismiss_observation` accepts optional `reason` and `actor` params
- `batch_dismiss_observations` removes multiple at once
- `promote_observation` returns issue + `matching_findings` array

**Step 2: Add TypedDicts to `types/inputs.py`**

```python
# ---------------------------------------------------------------------------
# observations.py handlers
# ---------------------------------------------------------------------------

class ObserveArgs(TypedDict):
    summary: str
    detail: NotRequired[str]
    file: NotRequired[str]
    line: NotRequired[int]
    source_issue_id: NotRequired[str]
    priority: NotRequired[int]
    actor: NotRequired[str]

class ListObservationsArgs(TypedDict):
    limit: NotRequired[int]
    offset: NotRequired[int]
    file_path: NotRequired[str]

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
- `observe` — calls `tracker.create_observation()`, passes `source_issue_id` through
- `list_observations` — passes `file_path` filter, includes `tracker.observation_stats()` in response
- `dismiss_observation` — passes `reason` and `actor` through
- `batch_dismiss_observations` — passes `reason` and `actor` through
- `promote_observation` — returns issue + `matching_findings`

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

**Step 1: Write failing tests**

Test that `generate_summary()` includes observation info when observations exist.
Test the escalation:
- 0 observations → no mention
- 1+ observations, all fresh → gentle nudge line
- 1+ observations older than 48h → stale notice with count

**Step 2: Add observation stats to `generate_summary()`**

In `summary.py`, after the existing stats section, add:

```python
# Observations
try:
    obs_stats = db.observation_stats()
    if obs_stats["count"] > 0:
        if obs_stats["stale_count"] > 0:
            lines.append("")
            lines.append(f"⚠ STALE OBSERVATIONS: {obs_stats['stale_count']} observation(s) older than 48 hours (oldest: {obs_stats['oldest_hours']:.0f}h ago)")
            lines.append(f"  Total pending: {obs_stats['count']}. Run `list_observations` to review.")
        else:
            lines.append("")
            lines.append(f"OBSERVATIONS: {obs_stats['count']} pending (oldest: {obs_stats['oldest_hours']:.0f}h ago)")
            lines.append("  Use `list_observations` to review, `promote_observation` to create issues,")
            lines.append("  or `dismiss_observation` to clear.")
        if obs_stats["expiring_soon_count"] > 0:
            lines.append(f"  ({obs_stats['expiring_soon_count']} expiring within 24h)")
except Exception:
    pass  # Observation stats are best-effort, never fatal
```

**Step 3: Add observation nudge to `_build_workflow_text()` in mcp_server.py**

After the existing type/pack listing, add a section that calls `observation_stats()` and emits the same gentle/stale notice. This ensures agents see it even if they don't read `session_context`.

**Step 4: Add to `session-context` output**

In `src/filigree/hooks.py`, add observation stats to `generate_session_context()` output (same gentle/stale formatting). The `session-context` CLI command in `cli_commands/admin.py` is a thin wrapper that calls this function — no changes needed in `admin.py`.

**Step 5: Run tests**

Run: `uv run pytest tests/analytics/test_summary.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/filigree/summary.py src/filigree/mcp_server.py src/filigree/hooks.py tests/analytics/test_summary.py
git commit -m "feat: add observation prompting to session context and MCP prompt"
```

---

### Task 5: CI check + CLAUDE.md update

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
