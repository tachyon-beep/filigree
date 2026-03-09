"""Tests for observation CRUD operations."""

from __future__ import annotations

from typing import Any

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

    def test_create_dedup_raises_when_existing_vanishes(self, db: FiligreeDB) -> None:
        """If INSERT OR IGNORE fires but SELECT-back returns None, raise instead of returning a ghost ID.

        Simulates a race condition: between INSERT OR IGNORE (dedup conflict) and
        the SELECT-back, a concurrent process deletes the matching row. Uses a
        connection wrapper to inject the deletion after the INSERT.
        """
        import sqlite3

        # First, create the observation that will trigger dedup
        db.create_observation("race condition", file_path="src/bar.py", line=5)

        # Wrap the connection to intercept and inject a delete after INSERT OR IGNORE
        real_conn = db._conn

        class InterceptingConnection:
            """Thin wrapper that deletes the matching obs after INSERT OR IGNORE."""

            def __init__(self, real: sqlite3.Connection):
                self._real = real

            def execute(self, sql: str, params: tuple = ()):
                result = self._real.execute(sql, params)
                if "INSERT OR IGNORE INTO observations" in sql and result.rowcount == 0:
                    # Simulate race: delete the row before SELECT can find it
                    self._real.execute("DELETE FROM observations WHERE summary = ?", (params[1],))
                return result

            def __getattr__(self, name: str):
                return getattr(self._real, name)

        db._conn = InterceptingConnection(real_conn)  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError, match="could not be located"):
                db.create_observation("race condition", file_path="src/bar.py", line=5)
        finally:
            db._conn = real_conn

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

    def test_list_with_offset(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.create_observation(f"Obs {i}", priority=i % 5)
        all_obs = db.list_observations()
        offset_obs = db.list_observations(offset=2)
        assert len(offset_obs) == 3
        assert offset_obs[0]["id"] == all_obs[2]["id"]

    def test_list_with_limit_and_offset(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.create_observation(f"Obs {i}", priority=i % 5)
        all_obs = db.list_observations()
        page = db.list_observations(limit=2, offset=1)
        assert len(page) == 2
        assert page[0]["id"] == all_obs[1]["id"]
        assert page[1]["id"] == all_obs[2]["id"]

    def test_list_offset_beyond_results(self, db: FiligreeDB) -> None:
        db.create_observation("Only one")
        assert db.list_observations(offset=10) == []

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
        row = db.conn.execute("SELECT * FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)).fetchone()
        assert row is not None
        assert row["reason"] == "expired (TTL)"
        assert row["actor"] == "system"


class _InterceptingConn:
    """Thin wrapper around a sqlite3.Connection that intercepts execute calls."""

    def __init__(self, real: Any, intercept: Any) -> None:
        self._real = real
        self._intercept = intercept

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self._intercept(self._real, sql, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class TestSweepExceptionNarrowing:
    """Verify _sweep_expired_observations catches sqlite3.Error but not non-DB errors."""

    def test_sweep_catches_sqlite_error(self, db: FiligreeDB) -> None:
        """sqlite3.Error during sweep is caught — list_observations still returns results."""
        import sqlite3

        db.create_observation("survives sweep failure")

        real_conn = db._conn

        def _fail_on_delete(real: Any, sql: str, params: Any = ()) -> Any:
            if "DELETE FROM observations WHERE expires_at" in sql:
                raise sqlite3.OperationalError("disk I/O error")
            return real.execute(sql, params)

        db._conn = _InterceptingConn(real_conn, _fail_on_delete)  # type: ignore[assignment]
        try:
            result = db.list_observations()
        finally:
            db._conn = real_conn
        assert len(result) == 1

    def test_sweep_propagates_non_sqlite_error(self, db: FiligreeDB) -> None:
        """Non-sqlite3.Error (e.g. RuntimeError) propagates through sweep."""
        db.create_observation("irrelevant")

        real_conn = db._conn

        def _fail_with_runtime(real: Any, sql: str, params: Any = ()) -> Any:
            if "DELETE FROM observations WHERE expires_at" in sql:
                raise RuntimeError("thread safety violation")
            return real.execute(sql, params)

        db._conn = _InterceptingConn(real_conn, _fail_with_runtime)  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError, match="thread safety"):
                db.list_observations()
        finally:
            db._conn = real_conn


class TestDismissObservation:
    def test_dismiss_deletes_and_logs(self, db: FiligreeDB) -> None:
        obs = db.create_observation("To dismiss")
        db.dismiss_observation(obs["id"], actor="tester", reason="not a real bug")
        assert db.list_observations() == []
        # Check audit trail
        row = db.conn.execute("SELECT * FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)).fetchone()
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
        result = db.batch_dismiss_observations([o1["id"], o2["id"]])
        assert result["dismissed"] == 2
        assert result["not_found"] == []
        remaining = db.list_observations()
        assert len(remaining) == 1
        assert remaining[0]["summary"] == "Three"
        # Both logged in audit trail
        count = db.conn.execute("SELECT COUNT(*) FROM dismissed_observations").fetchone()[0]
        assert count == 2

    def test_batch_dismiss_empty_list(self, db: FiligreeDB) -> None:
        result = db.batch_dismiss_observations([])
        assert result == {"dismissed": 0, "not_found": []}

    def test_batch_dismiss_duplicate_ids(self, db: FiligreeDB) -> None:
        o1 = db.create_observation("Only one")
        result = db.batch_dismiss_observations([o1["id"], o1["id"]])
        assert result["dismissed"] == 1
        assert result["not_found"] == []
        assert db.observation_count() == 0
        # Audit trail should have exactly one entry (SQL IN deduplicates)
        count = db.conn.execute("SELECT COUNT(*) FROM dismissed_observations WHERE obs_id = ?", (o1["id"],)).fetchone()[0]
        assert count == 1

    def test_batch_dismiss_partial_invalid_ids_reports_not_found(self, db: FiligreeDB) -> None:
        """Non-existent IDs reported in not_found; dismissed count reflects actual deletes."""
        o1 = db.create_observation("Real one")
        result = db.batch_dismiss_observations([o1["id"], "does-not-exist"])
        assert result["dismissed"] == 1
        assert result["not_found"] == ["does-not-exist"]
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
        labels = db.conn.execute("SELECT label FROM labels WHERE issue_id = ?", (result["issue"].id,)).fetchall()
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
        row = db.conn.execute("SELECT * FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)).fetchone()
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

    def test_promote_preserves_observation_on_create_issue_failure(self, db: FiligreeDB) -> None:
        """If create_issue raises, the observation is NOT deleted — no data loss."""
        from unittest.mock import patch

        obs = db.create_observation("will fail promote")
        with patch.object(db, "create_issue", side_effect=RuntimeError("boom")), pytest.raises(RuntimeError, match="boom"):
            db.promote_observation(obs["id"])
        # Observation must still exist
        assert db.observation_count() == 1
        remaining = db.list_observations()
        assert remaining[0]["id"] == obs["id"]
        # No audit trail entry — nothing was dismissed
        row = db.conn.execute("SELECT * FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)).fetchone()
        assert row is None

    def test_promote_label_failure_still_creates_issue(self, db: FiligreeDB) -> None:
        """Enrichment failures are best-effort: issue is still returned even if
        add_label raises."""
        from unittest.mock import patch

        count_before = db.conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        obs = db.create_observation("will partially fail")
        with patch.object(db, "add_label", side_effect=RuntimeError("label boom")):
            result = db.promote_observation(obs["id"])
        # Issue was created and returned successfully
        assert result["issue"] is not None
        assert db.observation_count() == 0
        count_after = db.conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        assert count_after == count_before + 1

    def test_promote_label_failure_returns_warnings(self, db: FiligreeDB) -> None:
        """Enrichment failures surface in warnings list."""
        from unittest.mock import patch

        obs = db.create_observation("will warn")
        with patch.object(db, "add_label", side_effect=RuntimeError("label boom")):
            result = db.promote_observation(obs["id"])
        assert "warnings" in result
        assert any("label" in w for w in result["warnings"])

    def test_promote_cleanup_failure_returns_warnings(self, db: FiligreeDB) -> None:
        """If audit trail + observation delete fails, warning is surfaced."""
        import sqlite3

        obs = db.create_observation("cleanup will fail")
        real_conn = db._conn

        def _fail_on_dismissed_insert(real: Any, sql: str, params: Any = ()) -> Any:
            if "INSERT INTO dismissed_observations" in sql:
                raise sqlite3.OperationalError("disk I/O error")
            return real.execute(sql, params)

        db._conn = _InterceptingConn(real_conn, _fail_on_dismissed_insert)  # type: ignore[assignment]
        try:
            result = db.promote_observation(obs["id"])
        finally:
            db._conn = real_conn
        assert result["issue"] is not None
        assert "warnings" in result
        assert any("clean up" in w.lower() for w in result["warnings"])

    def test_promote_no_warnings_on_success(self, db: FiligreeDB) -> None:
        """No warnings key when everything succeeds."""
        obs = db.create_observation("all good")
        result = db.promote_observation(obs["id"])
        assert "warnings" not in result

    def test_promote_expired_observation_raises(self, db: FiligreeDB) -> None:
        """Promoting an expired observation should fail, not create a stale issue."""
        obs = db.create_observation("stale finding")
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs["id"],),
        )
        db.conn.commit()
        with pytest.raises(ValueError, match="expired"):
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


class TestObservationStatsNoSweep:
    """Verify observation_stats(sweep=False) excludes expired rows via WHERE filter."""

    def test_stats_sweep_false_excludes_expired(self, db: FiligreeDB) -> None:
        db.create_observation("active obs")
        expired = db.create_observation("expired obs", file_path="src/old.py")
        # Backdate expiry to the past
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (expired["id"],),
        )
        db.conn.commit()

        stats = db.observation_stats(sweep=False)
        assert stats["count"] == 1, "sweep=False should exclude expired rows"
        # Verify the expired row is still in the database (no sweep happened)
        raw_count = db.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        assert raw_count == 2, "Expired row should still exist in DB"

    def test_stats_sweep_false_stale_excludes_expired(self, db: FiligreeDB) -> None:
        """Stale count should also exclude expired rows when sweep=False."""
        stale = db.create_observation("stale but alive")
        expired_stale = db.create_observation("stale and expired", file_path="src/x.py")
        # Backdate both to 3 days ago (stale), but only expire one
        db.conn.execute(
            "UPDATE observations SET created_at = '2020-01-01T00:00:00+00:00' WHERE id IN (?, ?)",
            (stale["id"], expired_stale["id"]),
        )
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-02T00:00:00+00:00' WHERE id = ?",
            (expired_stale["id"],),
        )
        db.conn.commit()

        stats = db.observation_stats(sweep=False)
        assert stats["stale_count"] == 1, "Only non-expired stale obs should be counted"

    def test_stats_sweep_false_empty_when_all_expired(self, db: FiligreeDB) -> None:
        obs = db.create_observation("will expire")
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs["id"],),
        )
        db.conn.commit()

        stats = db.observation_stats(sweep=False)
        assert stats["count"] == 0
        assert stats["stale_count"] == 0
        assert stats["expiring_soon_count"] == 0


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

    def test_list_files_excludes_expired_observations(self, db: FiligreeDB) -> None:
        """list_files_paginated should exclude expired observations from the count."""
        db.create_observation("active obs", file_path="src/listed.py")
        obs2 = db.create_observation("will expire", file_path="src/listed.py", line=1)
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs2["id"],),
        )
        db.conn.commit()
        result = db.list_files_paginated(path_prefix="src/listed")
        assert len(result["results"]) == 1
        assert result["results"][0]["observation_count"] == 1

    def test_file_detail_excludes_expired_observations(self, db: FiligreeDB) -> None:
        """get_file_detail should exclude expired observations from the count."""
        db.create_observation("active obs", file_path="src/temp.py")
        obs2 = db.create_observation("will expire", file_path="src/temp.py", line=1)
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (obs2["id"],),
        )
        db.conn.commit()
        detail = db.get_file_detail(obs2["file_id"])
        # Expired observations should not be counted
        assert detail["observation_count"] == 1


# ── M13: observation_stats sweep=False oldest_hours ────────────────────


class TestObservationStatsNoSweepOldest:
    """Verify sweep=False correctly filters the oldest_hours query."""

    def test_oldest_hours_excludes_expired(self, db: FiligreeDB) -> None:
        """oldest_hours should reflect only non-expired observations when sweep=False."""
        # Create a very old observation that is expired
        old_expired = db.create_observation("ancient expired")
        db.conn.execute(
            "UPDATE observations SET created_at = '2020-01-01T00:00:00+00:00', expires_at = '2020-02-01T00:00:00+00:00' WHERE id = ?",
            (old_expired["id"],),
        )
        # Create a recent observation that is alive
        db.create_observation("recent alive")
        db.conn.commit()

        stats = db.observation_stats(sweep=False)
        assert stats["count"] == 1
        # oldest_hours should reflect the recent obs, not the ancient expired one
        assert stats["oldest_hours"] < 24  # created just now

    def test_expiring_soon_excludes_already_expired(self, db: FiligreeDB) -> None:
        """expiring_soon_count should not include already-expired observations."""
        from datetime import UTC, datetime, timedelta

        # Create an obs that expires in 12 hours (expiring soon, alive)
        soon = db.create_observation("expires soon")
        soon_expiry = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
        db.conn.execute(
            "UPDATE observations SET expires_at = ? WHERE id = ?",
            (soon_expiry, soon["id"]),
        )
        # Create an obs that already expired (should NOT count)
        expired = db.create_observation("already expired")
        db.conn.execute(
            "UPDATE observations SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (expired["id"],),
        )
        db.conn.commit()

        stats = db.observation_stats(sweep=False)
        assert stats["expiring_soon_count"] == 1  # only the soon-expiring one
