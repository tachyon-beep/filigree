"""Tests for schema migrations, versioning, and database evolution."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from filigree.db_schema import CURRENT_SCHEMA_VERSION, SCHEMA_SQL, SCHEMA_V1_SQL
from filigree.migrations import (
    MigrationError,
    add_column,
    add_index,
    apply_pending_migrations,
    drop_index,
    rebuild_table,
    rename_column,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path, name: str = "test.db") -> sqlite3.Connection:
    """Create a raw SQLite connection with filigree PRAGMAs."""
    conn = sqlite3.connect(str(tmp_path / name))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _get_table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Return {column_name: column_type} for a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1]: row[2] for row in rows}


def _get_index_names(conn: sqlite3.Connection) -> set[str]:
    """Return all user-created index names."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'").fetchall()
    return {row[0] for row in rows}


def _get_table_names(conn: sqlite3.Connection) -> set[str]:
    """Return all table names (excluding FTS shadow tables)."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return {row[0] for row in rows}


def _get_schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


# ---------------------------------------------------------------------------
# Schema constant integrity
# ---------------------------------------------------------------------------


class TestSchemaV1Constant:
    """Verify SCHEMA_V1_SQL is a proper subset of SCHEMA_SQL."""

    def test_v1_is_subset_of_full_schema(self) -> None:
        assert SCHEMA_V1_SQL != SCHEMA_SQL, "SCHEMA_V1_SQL should not equal SCHEMA_SQL (missing file tables)"

    def test_v1_contains_core_tables(self) -> None:
        for table in ("issues", "dependencies", "events", "comments", "labels", "type_templates", "packs"):
            assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_V1_SQL

    def test_v1_excludes_file_tables(self) -> None:
        for table in ("file_records", "scan_findings", "file_associations", "file_events"):
            assert table not in SCHEMA_V1_SQL


# ---------------------------------------------------------------------------
# Migration runner tests
# ---------------------------------------------------------------------------


class TestMigrationRunner:
    """Test the apply_pending_migrations framework itself."""

    def test_no_op_when_current(self, tmp_path: Path) -> None:
        """No migrations applied when already at target version."""
        conn = _make_db(tmp_path)
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        conn.commit()

        applied = apply_pending_migrations(conn, CURRENT_SCHEMA_VERSION)
        assert applied == 0
        conn.close()

    def test_error_on_downgrade(self, tmp_path: Path) -> None:
        """Raises ValueError if DB is newer than target."""
        conn = _make_db(tmp_path)
        conn.execute("PRAGMA user_version = 99")
        conn.commit()

        with pytest.raises(ValueError, match="newer than this version"):
            apply_pending_migrations(conn, CURRENT_SCHEMA_VERSION)
        conn.close()

    def test_error_on_missing_migration(self, tmp_path: Path) -> None:
        """Raises MigrationError if a migration function isn't registered."""
        conn = _make_db(tmp_path)
        conn.executescript(SCHEMA_SQL)
        # Set version to one less than current to trigger migration lookup
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION - 1}")
        conn.commit()

        # Only run this test if there's actually a gap in the registry
        from filigree.migrations import MIGRATIONS

        if (CURRENT_SCHEMA_VERSION - 1) not in MIGRATIONS:
            with pytest.raises(MigrationError, match="No migration registered"):
                apply_pending_migrations(conn, CURRENT_SCHEMA_VERSION)
        conn.close()

    def test_migration_applies_and_bumps_version(self, tmp_path: Path) -> None:
        """A registered migration runs and increments user_version."""
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        # Register a temporary migration
        from filigree import migrations

        original = migrations.MIGRATIONS.copy()
        did_run = []

        def fake_v1_to_v2(c: sqlite3.Connection) -> None:
            c.execute("ALTER TABLE test_table ADD COLUMN name TEXT DEFAULT ''")
            did_run.append(True)

        migrations.MIGRATIONS[1] = fake_v1_to_v2  # type: ignore[assignment]
        try:
            applied = apply_pending_migrations(conn, 2)
            assert applied == 1
            assert _get_schema_version(conn) == 2
            assert did_run == [True]
            # Verify column was added
            cols = _get_table_columns(conn, "test_table")
            assert "name" in cols
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()

    def test_migration_rollback_on_failure(self, tmp_path: Path) -> None:
        """Failed migration rolls back and preserves the pre-migration version."""
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO test_table VALUES (1, 'original')")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def bad_migration(c: sqlite3.Connection) -> None:
            c.execute("UPDATE test_table SET val = 'modified'")
            raise RuntimeError("Intentional failure")

        migrations.MIGRATIONS[1] = bad_migration  # type: ignore[assignment]
        try:
            with pytest.raises(MigrationError, match="Intentional failure"):
                apply_pending_migrations(conn, 2)
            # Version unchanged
            assert _get_schema_version(conn) == 1
            # Data rolled back
            val = conn.execute("SELECT val FROM test_table WHERE id = 1").fetchone()[0]
            assert val == "original"
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()

    def test_multi_step_migration(self, tmp_path: Path) -> None:
        """Multiple migrations run in sequence."""
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()
        order: list[int] = []

        def m1(c: sqlite3.Connection) -> None:
            c.execute("ALTER TABLE t ADD COLUMN a TEXT DEFAULT ''")
            order.append(1)

        def m2(c: sqlite3.Connection) -> None:
            c.execute("ALTER TABLE t ADD COLUMN b TEXT DEFAULT ''")
            order.append(2)

        def m3(c: sqlite3.Connection) -> None:
            c.execute("ALTER TABLE t ADD COLUMN c TEXT DEFAULT ''")
            order.append(3)

        migrations.MIGRATIONS[1] = m1  # type: ignore[assignment]
        migrations.MIGRATIONS[2] = m2  # type: ignore[assignment]
        migrations.MIGRATIONS[3] = m3  # type: ignore[assignment]
        try:
            applied = apply_pending_migrations(conn, 4)
            assert applied == 3
            assert order == [1, 2, 3]
            assert _get_schema_version(conn) == 4
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()

    def test_partial_failure_preserves_successful_steps(self, tmp_path: Path) -> None:
        """If migration 2->3 fails, v1->v2 is still committed."""
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def m1(c: sqlite3.Connection) -> None:
            c.execute("ALTER TABLE t ADD COLUMN a TEXT DEFAULT ''")

        def m2_fail(c: sqlite3.Connection) -> None:
            raise RuntimeError("Boom")

        migrations.MIGRATIONS[1] = m1  # type: ignore[assignment]
        migrations.MIGRATIONS[2] = m2_fail  # type: ignore[assignment]
        try:
            with pytest.raises(MigrationError, match="Boom"):
                apply_pending_migrations(conn, 3)
            # v1->v2 succeeded and was committed
            assert _get_schema_version(conn) == 2
            assert "a" in _get_table_columns(conn, "t")
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()


class TestMigrationRunnerTransactionGuard:
    """Bug filigree-8b0f07: rollback must not discard caller-owned transaction work."""

    def test_raises_when_called_inside_existing_transaction(self, tmp_path: Path) -> None:
        """Calling apply_pending_migrations inside an open transaction must fail fast."""
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def noop(c: sqlite3.Connection) -> None:
            pass

        migrations.MIGRATIONS[1] = noop  # type: ignore[assignment]
        try:
            # Start a caller-owned transaction
            conn.execute("INSERT INTO t VALUES (1)")
            assert conn.in_transaction  # precondition

            with pytest.raises(RuntimeError, match="existing transaction"):
                apply_pending_migrations(conn, 2)

            # Caller's transaction must still be intact (not rolled back)
            assert conn.in_transaction
            conn.commit()
            row = conn.execute("SELECT id FROM t").fetchone()
            assert row[0] == 1  # caller data preserved
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()


class TestMigrationRunnerFKPreservation:
    """Bug filigree-3831c4: FK enforcement setting must be preserved."""

    def test_fk_off_preserved_after_migration(self, tmp_path: Path) -> None:
        """If caller had FK=OFF, it must still be OFF after migration."""
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def noop(c: sqlite3.Connection) -> None:
            pass

        migrations.MIGRATIONS[1] = noop  # type: ignore[assignment]
        try:
            # Caller explicitly disables FKs
            conn.execute("PRAGMA foreign_keys=OFF")
            fk_before = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk_before == 0  # precondition: OFF

            apply_pending_migrations(conn, 2)

            fk_after = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk_after == 0  # must be restored to OFF
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()

    def test_fk_on_preserved_after_migration(self, tmp_path: Path) -> None:
        """If caller had FK=ON, it must still be ON after migration."""
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def noop(c: sqlite3.Connection) -> None:
            pass

        migrations.MIGRATIONS[1] = noop  # type: ignore[assignment]
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            fk_before = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk_before == 1  # precondition: ON

            apply_pending_migrations(conn, 2)

            fk_after = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk_after == 1
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()

    def test_fk_restored_after_failed_migration(self, tmp_path: Path) -> None:
        """FK setting must be restored even when a migration fails."""
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def bad_migration(c: sqlite3.Connection) -> None:
            raise RuntimeError("boom")

        migrations.MIGRATIONS[1] = bad_migration  # type: ignore[assignment]
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            with pytest.raises(MigrationError):
                apply_pending_migrations(conn, 2)

            fk_after = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk_after == 0  # must be restored to OFF
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()


# ---------------------------------------------------------------------------
# SQLite helper tests
# ---------------------------------------------------------------------------


class TestAddColumn:
    def test_adds_column(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        add_column(conn, "t", "name", "TEXT", "''")
        assert "name" in _get_table_columns(conn, "t")
        conn.close()

    def test_idempotent(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        add_column(conn, "t", "name", "TEXT", "''")
        add_column(conn, "t", "name", "TEXT", "''")  # no error
        assert "name" in _get_table_columns(conn, "t")
        conn.close()

    def test_integer_with_default(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        add_column(conn, "t", "count", "INTEGER", "0")
        conn.execute("INSERT INTO t (id) VALUES (1)")
        val = conn.execute("SELECT count FROM t WHERE id = 1").fetchone()[0]
        assert val == 0
        conn.close()

    def test_nullable_no_default(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        add_column(conn, "t", "optional", "TEXT", None)
        conn.execute("INSERT INTO t (id) VALUES (1)")
        val = conn.execute("SELECT optional FROM t WHERE id = 1").fetchone()[0]
        assert val is None
        conn.close()


class TestAddIndex:
    def test_creates_index(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        add_index(conn, "idx_t_name", "t", ["name"])
        assert "idx_t_name" in _get_index_names(conn)
        conn.close()

    def test_idempotent(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        add_index(conn, "idx_t_name", "t", ["name"])
        add_index(conn, "idx_t_name", "t", ["name"])  # no error
        assert "idx_t_name" in _get_index_names(conn)
        conn.close()

    def test_composite_index(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, a TEXT, b TEXT)")
        add_index(conn, "idx_t_ab", "t", ["a", "b"])
        assert "idx_t_ab" in _get_index_names(conn)
        conn.close()

    def test_unique_index(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, code TEXT)")
        add_index(conn, "idx_t_code", "t", ["code"], unique=True)
        conn.execute("INSERT INTO t VALUES (1, 'a')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO t VALUES (2, 'a')")
        conn.close()


class TestDropIndex:
    def test_drops_index(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE INDEX idx_t_name ON t(name)")
        drop_index(conn, "idx_t_name")
        assert "idx_t_name" not in _get_index_names(conn)
        conn.close()

    def test_idempotent(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        drop_index(conn, "nonexistent_index")  # no error
        # Confirm no indexes were accidentally created
        assert "nonexistent_index" not in _get_index_names(conn)
        conn.close()


class TestRenameColumn:
    def test_renames(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, old_name TEXT)")
        rename_column(conn, "t", "old_name", "new_name")
        cols = _get_table_columns(conn, "t")
        assert "new_name" in cols
        assert "old_name" not in cols
        conn.close()

    def test_idempotent(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, old_name TEXT)")
        rename_column(conn, "t", "old_name", "new_name")
        rename_column(conn, "t", "old_name", "new_name")  # no error
        assert "new_name" in _get_table_columns(conn, "t")
        assert "old_name" not in _get_table_columns(conn, "t")
        conn.close()

    def test_error_on_missing_source(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        with pytest.raises(ValueError, match="not found"):
            rename_column(conn, "t", "nonexistent", "new_name")
        conn.close()


class TestRebuildTable:
    def test_preserves_data(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
        conn.execute("INSERT INTO t VALUES (1, 'Alice', 30)")
        conn.execute("INSERT INTO t VALUES (2, 'Bob', 25)")
        conn.commit()

        # Rebuild with same schema
        rebuild_table(conn, "t", "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
        conn.commit()

        rows = conn.execute("SELECT * FROM t ORDER BY id").fetchall()
        assert len(rows) == 2
        assert rows[0][1] == "Alice"
        assert rows[1][1] == "Bob"
        conn.close()

    def test_drops_column(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, legacy TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'Alice', 'old')")
        conn.commit()

        rebuild_table(
            conn,
            "t",
            "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)",
            column_mapping={"id": "id", "name": "name"},
        )
        conn.commit()

        cols = _get_table_columns(conn, "t")
        assert "legacy" not in cols
        row = conn.execute("SELECT * FROM t").fetchone()
        assert row[1] == "Alice"
        conn.close()

    def test_transforms_data(self, tmp_path: Path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, priority INTEGER)")
        conn.execute("INSERT INTO t VALUES (1, 10)")
        conn.commit()

        rebuild_table(
            conn,
            "t",
            "CREATE TABLE t (id INTEGER PRIMARY KEY, priority INTEGER CHECK(priority BETWEEN 0 AND 4))",
            column_mapping={"id": "id", "priority": "MIN(priority, 4)"},
        )
        conn.commit()

        val = conn.execute("SELECT priority FROM t WHERE id = 1").fetchone()[0]
        assert val == 4
        conn.close()

    def test_rebuild_fk_referenced_table(self, tmp_path: Path) -> None:
        """rebuild_table works on FK-referenced tables when FK enforcement is off.

        The migration runner disables FK enforcement before each migration
        and validates integrity before commit. This test mirrors that pattern.
        """
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(id))")
        conn.execute("INSERT INTO parent VALUES ('p1', 'Parent 1')")
        conn.execute("INSERT INTO child VALUES ('c1', 'p1')")
        conn.commit()

        # Migration runner disables FKs before the transaction
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        rebuild_table(
            conn,
            "parent",
            "CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT, extra TEXT DEFAULT '')",
        )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert not violations
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

        row = conn.execute("SELECT name FROM parent WHERE id = 'p1'").fetchone()
        assert row[0] == "Parent 1"
        cols = _get_table_columns(conn, "parent")
        assert "extra" in cols
        conn.close()

    def test_cleans_up_leftover_temp_table(self, tmp_path: Path) -> None:
        """If a previous migration failed mid-rebuild, temp table is cleaned up."""
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE _filigree_migrate_t (id INTEGER PRIMARY KEY, junk TEXT)")
        conn.commit()

        rebuild_table(conn, "t", "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT DEFAULT '')")
        conn.commit()

        tables = _get_table_names(conn)
        assert "_filigree_migrate_t" not in tables
        assert "t" in tables
        conn.close()

    def test_lowercase_create_table(self, tmp_path: Path) -> None:
        """rebuild_table must handle lowercase SQL keywords."""
        conn = _make_db(tmp_path)
        conn.execute("create table t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'Alice')")
        conn.commit()

        rebuild_table(conn, "t", "create table t (id INTEGER PRIMARY KEY, name TEXT, age INTEGER DEFAULT 0)")
        conn.commit()

        rows = conn.execute("SELECT * FROM t ORDER BY id").fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "Alice"
        conn.close()


# ---------------------------------------------------------------------------
# Migration atomicity tests (BEGIN IMMEDIATE + execute vs executescript)
# ---------------------------------------------------------------------------


class TestMigrationAtomicity:
    """Test that migrations are properly wrapped in transactions.

    Verifies that BEGIN IMMEDIATE is issued before each migration and that
    rebuild_table() using execute() (not executescript()) preserves the
    active transaction so rollback works correctly.
    """

    def test_rebuild_table_failure_rolls_back(self, tmp_path: Path) -> None:
        """A migration that calls rebuild_table() then fails should roll back completely.

        Temp tables should be cleaned up and version should remain unchanged.
        """
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, priority INTEGER DEFAULT 2)")
        conn.execute("INSERT INTO t VALUES (1, 'Alice', 2)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def bad_rebuild_migration(c: sqlite3.Connection) -> None:
            """Migration that calls rebuild_table then raises."""
            rebuild_table(
                c,
                "t",
                "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, priority INTEGER CHECK(priority BETWEEN 0 AND 4))",
            )
            # Now fail after rebuild_table succeeded
            raise RuntimeError("Intentional post-rebuild failure")

        migrations.MIGRATIONS[1] = bad_rebuild_migration  # type: ignore[assignment]
        try:
            with pytest.raises(MigrationError, match="Intentional post-rebuild failure"):
                apply_pending_migrations(conn, 2)

            # Version should NOT have been bumped
            assert _get_schema_version(conn) == 1

            # The temp table should not exist
            tables = _get_table_names(conn)
            assert "_filigree_migrate_t" not in tables

            # The original table should still exist with original data
            assert "t" in tables
            row = conn.execute("SELECT name FROM t WHERE id = 1").fetchone()
            assert row[0] == "Alice"
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()

    def test_rebuild_fk_table_failure_version_not_bumped(self, tmp_path: Path) -> None:
        """Migration that rebuilds FK-referenced table then fails rolls back completely.

        With defer_foreign_keys, the rebuild stays within the caller's
        transaction and can be fully rolled back on failure -- including
        the schema change itself.
        """
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE child (id TEXT PRIMARY KEY, pid TEXT REFERENCES parent(id))")
        conn.execute("INSERT INTO parent VALUES ('p1', 'Alice')")
        conn.execute("INSERT INTO child VALUES ('c1', 'p1')")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def bad_fk_rebuild_migration(c: sqlite3.Connection) -> None:
            """Rebuild FK-referenced table then raise."""
            rebuild_table(
                c,
                "parent",
                "CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT, extra TEXT DEFAULT '')",
            )
            # Post-rebuild DML that should be rolled back
            c.execute("INSERT INTO parent VALUES ('p2', 'Bob', '')")
            raise RuntimeError("Intentional post-rebuild failure")

        migrations.MIGRATIONS[1] = bad_fk_rebuild_migration  # type: ignore[assignment]
        try:
            with pytest.raises(MigrationError, match="Intentional post-rebuild failure"):
                apply_pending_migrations(conn, 2)

            # Version must NOT have been bumped
            assert _get_schema_version(conn) == 1

            # Entire rebuild was rolled back (defer_foreign_keys keeps
            # the rebuild inside the caller's transaction)
            tables = _get_table_names(conn)
            assert "parent" in tables

            # Original schema restored -- no 'extra' column
            cols = _get_table_columns(conn, "parent")
            assert "extra" not in cols, "rebuild should have been rolled back"

            # Original data intact
            count = conn.execute("SELECT COUNT(*) FROM parent").fetchone()[0]
            assert count == 1, "post-rebuild DML should have been rolled back"
            row = conn.execute("SELECT name FROM parent WHERE id = 'p1'").fetchone()
            assert row[0] == "Alice"

            # Child table and FK data intact
            assert "child" in tables
            row = conn.execute("SELECT pid FROM child WHERE id = 'c1'").fetchone()
            assert row[0] == "p1"

            # FK enforcement still works
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO child VALUES ('c2', 'nonexistent')")
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()

    def test_rebuild_fk_table_pre_rebuild_ops_rolled_back(self, tmp_path: Path) -> None:
        """Pre-rebuild DML in the same migration is rolled back on failure.

        This is the key atomicity test: operations BEFORE the rebuild are
        also rolled back when a post-rebuild failure occurs.
        """
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE child (id TEXT PRIMARY KEY, pid TEXT REFERENCES parent(id))")
        conn.execute("INSERT INTO parent VALUES ('p1', 'Alice')")
        conn.execute("INSERT INTO child VALUES ('c1', 'p1')")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def migration_with_pre_rebuild_dml(c: sqlite3.Connection) -> None:
            """DML before rebuild, then rebuild FK table, then fail."""
            c.execute("UPDATE parent SET name = 'MODIFIED' WHERE id = 'p1'")
            rebuild_table(
                c,
                "parent",
                "CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT, extra TEXT DEFAULT '')",
            )
            raise RuntimeError("Post-rebuild failure")

        migrations.MIGRATIONS[1] = migration_with_pre_rebuild_dml  # type: ignore[assignment]
        try:
            with pytest.raises(MigrationError, match="Post-rebuild failure"):
                apply_pending_migrations(conn, 2)

            # Version not bumped
            assert _get_schema_version(conn) == 1

            # Pre-rebuild UPDATE was rolled back
            row = conn.execute("SELECT name FROM parent WHERE id = 'p1'").fetchone()
            assert row[0] == "Alice", "pre-rebuild DML should have been rolled back"

            # Rebuild itself was rolled back
            cols = _get_table_columns(conn, "parent")
            assert "extra" not in cols, "rebuild should have been rolled back"
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()

    def test_rebuild_fk_table_validates_fk_check(self, tmp_path: Path) -> None:
        """FK violations after rebuild are caught by foreign_key_check before commit.

        The migration runner checks PRAGMA foreign_key_check after each
        migration. This test verifies that pattern catches violations.
        """
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE child (id TEXT PRIMARY KEY, pid TEXT REFERENCES parent(id))")
        conn.execute("INSERT INTO parent VALUES ('p1', 'Alice')")
        conn.execute("INSERT INTO child VALUES ('c1', 'p1')")
        conn.commit()

        # Rebuild parent with column_mapping that changes the PK value,
        # breaking the FK from child
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        rebuild_table(
            conn,
            "parent",
            "CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT)",
            column_mapping={"id": "'CHANGED'", "name": "name"},
        )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert violations, "Should detect FK violations when PK values change"
        conn.rollback()
        conn.execute("PRAGMA foreign_keys=ON")

    def test_template_new_table_uses_execute_not_executescript(self, tmp_path: Path) -> None:
        """_template_new_table_migration must use execute(), not executescript().

        executescript() implicitly commits, breaking transaction rollback.
        """
        from filigree.migrations import _template_new_table_migration

        conn = _make_db(tmp_path)
        # Create the issues table that the template references
        conn.execute("CREATE TABLE issues (id TEXT PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()

        # Run inside a transaction and then rollback
        conn.execute("BEGIN IMMEDIATE")
        _template_new_table_migration(conn)
        conn.rollback()

        # After rollback, the attachments table should NOT exist
        tables = _get_table_names(conn)
        assert "attachments" not in tables
        conn.close()

    def test_begin_immediate_is_used(self, tmp_path: Path) -> None:
        """Verify that BEGIN IMMEDIATE is issued before each migration.

        We test this indirectly: if BEGIN IMMEDIATE is properly used, then
        DML changes within a migration are rolled back on failure.
        """
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'original')")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        from filigree import migrations

        original = migrations.MIGRATIONS.copy()

        def migration_with_dml_then_fail(c: sqlite3.Connection) -> None:
            c.execute("UPDATE t SET val = 'modified' WHERE id = 1")
            c.execute("INSERT INTO t VALUES (2, 'new_row')")
            raise RuntimeError("Fail after DML")

        migrations.MIGRATIONS[1] = migration_with_dml_then_fail  # type: ignore[assignment]
        try:
            with pytest.raises(MigrationError, match="Fail after DML"):
                apply_pending_migrations(conn, 2)

            # Version unchanged
            assert _get_schema_version(conn) == 1
            # DML rolled back
            val = conn.execute("SELECT val FROM t WHERE id = 1").fetchone()[0]
            assert val == "original"
            # Inserted row rolled back
            count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            assert count == 1
        finally:
            migrations.MIGRATIONS.clear()
            migrations.MIGRATIONS.update(original)
            conn.close()

    def test_rebuild_table_uses_execute_not_executescript(self, tmp_path: Path) -> None:
        """Verify rebuild_table works within a transaction (execute, not executescript).

        executescript() implicitly commits, but execute() does not. We verify
        this by running rebuild_table inside a manually started transaction and
        checking that rollback undoes the rebuild.
        """
        conn = _make_db(tmp_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'Alice')")
        conn.commit()

        # Start a transaction, run rebuild_table, then rollback
        conn.execute("BEGIN IMMEDIATE")
        rebuild_table(
            conn,
            "t",
            "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, extra TEXT DEFAULT '')",
        )
        conn.rollback()

        # After rollback, the original table should be intact
        cols = _get_table_columns(conn, "t")
        assert "extra" not in cols
        assert "name" in cols

        row = conn.execute("SELECT name FROM t WHERE id = 1").fetchone()
        assert row[0] == "Alice"
        conn.close()


# ---------------------------------------------------------------------------
# v2 -> v3 migration tests
# ---------------------------------------------------------------------------


class TestMigrateV2ToV3:
    """Tests for migration v2 -> v3: scan_run_id + suggestion columns + index."""

    @pytest.fixture
    def v2_db(self, tmp_path: Path) -> sqlite3.Connection:
        """Create a v2 database with scan_findings table (no suggestion/scan_run_id)."""
        from filigree.migrations import migrate_v1_to_v2

        conn = _make_db(tmp_path)
        conn.executescript(SCHEMA_V1_SQL)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        # Apply v1->v2 to get the scan_findings table
        conn.execute("BEGIN IMMEDIATE")
        migrate_v1_to_v2(conn)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()

        # Insert a representative finding
        conn.execute("INSERT INTO file_records (id, path, first_seen, updated_at) VALUES ('f1', 'src/main.py', '2026-01-01', '2026-01-01')")
        conn.execute(
            "INSERT INTO scan_findings (id, file_id, scan_source, rule_id, severity, "
            "status, message, first_seen, updated_at) "
            "VALUES ('sf1', 'f1', 'ruff', 'E501', 'low', 'open', 'line too long', "
            "'2026-01-01', '2026-01-01')"
        )
        conn.commit()
        return conn

    def test_migration_runs(self, v2_db: sqlite3.Connection) -> None:
        applied = apply_pending_migrations(v2_db, 3)
        assert applied == 1
        assert _get_schema_version(v2_db) == 3

    def test_columns_added(self, v2_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v2_db, 3)
        cols = _get_table_columns(v2_db, "scan_findings")
        assert "suggestion" in cols
        assert "scan_run_id" in cols

    def test_index_created(self, v2_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v2_db, 3)
        indexes = _get_index_names(v2_db)
        assert "idx_scan_findings_run" in indexes

    def test_data_preserved(self, v2_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v2_db, 3)
        row = v2_db.execute("SELECT message FROM scan_findings WHERE id = 'sf1'").fetchone()
        assert row[0] == "line too long"

    def test_new_columns_have_defaults(self, v2_db: sqlite3.Connection) -> None:
        apply_pending_migrations(v2_db, 3)
        row = v2_db.execute("SELECT suggestion, scan_run_id FROM scan_findings WHERE id = 'sf1'").fetchone()
        assert row[0] == ""
        assert row[1] == ""

    def test_schema_matches_fresh(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        """Migrated schema matches fresh SCHEMA_SQL for scan_findings table."""
        apply_pending_migrations(v2_db, 3)

        fresh = _make_db(tmp_path, "fresh.db")
        fresh.executescript(SCHEMA_SQL)
        fresh.commit()

        migrated_cols = _get_table_columns(v2_db, "scan_findings")
        fresh_cols = _get_table_columns(fresh, "scan_findings")
        assert migrated_cols == fresh_cols, f"Column mismatch: {migrated_cols} != {fresh_cols}"

        # Check that the new index exists in both
        fresh_indexes = _get_index_names(fresh)
        migrated_indexes = _get_index_names(v2_db)
        assert "idx_scan_findings_run" in fresh_indexes
        assert "idx_scan_findings_run" in migrated_indexes

        fresh.close()


# ---------------------------------------------------------------------------
# v3 -> v4 migration tests
# ---------------------------------------------------------------------------


class TestMigrateV3ToV4:
    """Tests for migration v3 -> v4: file_events table + index."""

    @pytest.fixture
    def v3_db(self, tmp_path: Path) -> sqlite3.Connection:
        """Create a v3 database (file tables present, no file_events)."""
        from filigree.db_schema import SCHEMA_V1_SQL
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
        applied = apply_pending_migrations(v3_db, 4)
        assert applied == 0


class TestMigrateV4ToV5:
    """Tests for migration v4 -> v5: normalize release version fields."""

    @pytest.fixture
    def v4_db(self, tmp_path: Path) -> sqlite3.Connection:
        """Create a v4 database using the full schema (stamped as v4)."""
        conn = _make_db(tmp_path)
        conn.executescript(SCHEMA_SQL)
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        return conn

    def _insert_release(self, conn: sqlite3.Connection, issue_id: str, title: str, version: str) -> None:
        import json

        now = "2026-01-01T00:00:00Z"
        fields = json.dumps({"version": version}) if version else "{}"
        conn.execute(
            "INSERT INTO issues (id, title, status, priority, type, assignee, created_at, updated_at, fields) "
            "VALUES (?, ?, 'open', 2, 'release', '', ?, ?, ?)",
            (issue_id, title, now, now, fields),
        )
        conn.commit()

    def test_normalizes_no_v_prefix(self, v4_db: sqlite3.Connection) -> None:
        """'1.2.3' should become 'v1.2.3'."""
        import json

        self._insert_release(v4_db, "r1", "R1", "1.2.3")
        apply_pending_migrations(v4_db, 5)
        row = v4_db.execute("SELECT fields FROM issues WHERE id = 'r1'").fetchone()
        assert json.loads(row["fields"])["version"] == "v1.2.3"
        # Check migration comment
        comment = v4_db.execute("SELECT text FROM comments WHERE issue_id = 'r1'").fetchone()
        assert "normalized" in comment["text"].lower() or "1.2.3" in comment["text"]

    def test_normalizes_two_part_version(self, v4_db: sqlite3.Connection) -> None:
        """'v1.2' should become 'v1.2.0'."""
        import json

        self._insert_release(v4_db, "r2", "R2", "v1.2")
        apply_pending_migrations(v4_db, 5)
        row = v4_db.execute("SELECT fields FROM issues WHERE id = 'r2'").fetchone()
        assert json.loads(row["fields"])["version"] == "v1.2.0"

    def test_clears_unnormalizable_version(self, v4_db: sqlite3.Connection) -> None:
        """'TBD' should be cleared with a comment."""
        import json

        self._insert_release(v4_db, "r3", "R3", "TBD")
        apply_pending_migrations(v4_db, 5)
        row = v4_db.execute("SELECT fields FROM issues WHERE id = 'r3'").fetchone()
        fields = json.loads(row["fields"])
        assert "version" not in fields
        comment = v4_db.execute("SELECT text FROM comments WHERE issue_id = 'r3'").fetchone()
        assert "cleared" in comment["text"].lower()

    def test_leaves_compliant_versions_untouched(self, v4_db: sqlite3.Connection) -> None:
        """'v1.2.3' and 'Future' should remain unchanged."""
        import json

        self._insert_release(v4_db, "r4", "R4", "v1.2.3")
        self._insert_release(v4_db, "r5", "Future", "Future")
        apply_pending_migrations(v4_db, 5)

        row4 = v4_db.execute("SELECT fields FROM issues WHERE id = 'r4'").fetchone()
        assert json.loads(row4["fields"])["version"] == "v1.2.3"
        row5 = v4_db.execute("SELECT fields FROM issues WHERE id = 'r5'").fetchone()
        assert json.loads(row5["fields"])["version"] == "Future"

        # No comments added for compliant versions
        comments4 = v4_db.execute("SELECT text FROM comments WHERE issue_id = 'r4'").fetchall()
        assert len(comments4) == 0
        comments5 = v4_db.execute("SELECT text FROM comments WHERE issue_id = 'r5'").fetchall()
        assert len(comments5) == 0

    def test_handles_empty_version_field(self, v4_db: sqlite3.Connection) -> None:
        """Empty version should be left alone (no change, no comment)."""
        self._insert_release(v4_db, "r6", "R6", "")
        apply_pending_migrations(v4_db, 5)
        comments = v4_db.execute("SELECT text FROM comments WHERE issue_id = 'r6'").fetchall()
        assert len(comments) == 0


# ---------------------------------------------------------------------------
# Schema equivalence test
# ---------------------------------------------------------------------------


class TestSchemaEquivalence:
    """Verify that migrating from v1 matches a fresh database.

    This is the most important migration test. When you add a migration:
    1. Capture the SCHEMA_SQL from BEFORE your change as V<N>_SCHEMA_SQL
    2. Update SCHEMA_SQL in core.py for the new version
    3. This test creates both a fresh DB and a migrated DB, then compares schemas.
    """

    def test_fresh_db_at_current_version(self, db: FiligreeDB) -> None:
        """Fresh initialization sets the correct schema version."""
        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION

    def test_fresh_db_has_all_tables(self, db: FiligreeDB) -> None:
        """Fresh initialization creates all expected tables."""
        tables = _get_table_names(db.conn)
        expected = {"issues", "dependencies", "events", "comments", "labels", "type_templates", "packs"}
        assert expected.issubset(tables)

    def test_fresh_db_has_all_indexes(self, db: FiligreeDB) -> None:
        """Fresh initialization creates all expected indexes."""
        indexes = _get_index_names(db.conn)
        expected = {
            "idx_issues_status",
            "idx_issues_type",
            "idx_issues_parent",
            "idx_issues_priority",
            "idx_issues_status_priority",
            "idx_deps_depends_on",
            "idx_deps_issue_depends",
            "idx_events_issue",
            "idx_events_created",
            "idx_events_issue_time",
            "idx_comments_issue",
        }
        assert expected.issubset(indexes)

    # -- Template for per-version equivalence tests --------------------------
    #
    # Uncomment and adapt when adding migration v1 -> v2:
    #
    # # Snapshot of SCHEMA_SQL before v2 changes (copy from git history)
    # V1_SCHEMA_SQL = """..."""
    #
    # def test_v1_to_v2_schema_matches_fresh(self, tmp_path: Path) -> None:
    #     """Migrated v1 DB has same schema as fresh v2 DB."""
    #     # Create a v1 database
    #     migrated = _make_db(tmp_path, "migrated.db")
    #     migrated.executescript(self.V1_SCHEMA_SQL)
    #     migrated.execute("PRAGMA user_version = 1")
    #     migrated.commit()
    #
    #     # Run migration
    #     applied = apply_pending_migrations(migrated, 2)
    #     assert applied == 1
    #
    #     # Create a fresh v2 database
    #     fresh = _make_db(tmp_path, "fresh.db")
    #     fresh.executescript(SCHEMA_SQL)
    #     fresh.execute("PRAGMA user_version = 2")
    #     fresh.commit()
    #
    #     # Compare schemas (table-by-table column comparison)
    #     for table in _get_table_names(fresh):
    #         if table.startswith("issues_fts"):
    #             continue  # FTS shadow tables vary
    #         fresh_cols = _get_table_columns(fresh, table)
    #         migrated_cols = _get_table_columns(migrated, table)
    #         assert fresh_cols == migrated_cols, f"Schema mismatch in table {table}"
    #
    #     # Compare indexes
    #     fresh_indexes = _get_index_names(fresh)
    #     migrated_indexes = _get_index_names(migrated)
    #     assert fresh_indexes == migrated_indexes
    #
    #     migrated.close()
    #     fresh.close()


# ---------------------------------------------------------------------------
# FiligreeDB integration tests
# ---------------------------------------------------------------------------


class TestFiligreeDBMigration:
    """Test that FiligreeDB.initialize() handles migration scenarios."""

    def test_fresh_init_creates_schema(self, tmp_path: Path) -> None:
        """Fresh database gets full schema from SCHEMA_SQL."""
        d = FiligreeDB(tmp_path / "fresh.db", prefix="test")
        d.initialize()
        assert d.get_schema_version() == CURRENT_SCHEMA_VERSION
        # Can create and query issues
        issue = d.create_issue("Test issue")
        assert d.get_issue(issue.id).title == "Test issue"
        d.close()

    def test_reinitialize_is_idempotent(self, tmp_path: Path) -> None:
        """Calling initialize() twice on the same DB is safe."""
        d = FiligreeDB(tmp_path / "idem.db", prefix="test")
        d.initialize()
        issue = d.create_issue("Before reinit")
        d.initialize()  # Should be a no-op (already at current version)
        assert d.get_issue(issue.id).title == "Before reinit"
        assert d.get_schema_version() == CURRENT_SCHEMA_VERSION
        d.close()


# ---------------------------------------------------------------------------
# Per-migration test template
# ---------------------------------------------------------------------------

# class TestMigrateV1ToV2:
#     """Tests for migration v1 -> v2.
#
#     Describe what the migration does and why.
#     """
#
#     # Snapshot: SCHEMA_SQL at v1 (copy from git before making changes)
#     V1_SCHEMA = """..."""
#
#     @pytest.fixture
#     def v1_db(self, tmp_path: Path) -> sqlite3.Connection:
#         """Create a v1 database with representative test data."""
#         conn = _make_db(tmp_path)
#         conn.executescript(self.V1_SCHEMA)
#         conn.execute("PRAGMA user_version = 1")
#         # Insert representative data that exercises the migration
#         conn.execute(
#             "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at) "
#             "VALUES ('test-1', 'Issue 1', 'open', 2, 'task', '2026-01-01', '2026-01-01')"
#         )
#         conn.commit()
#         return conn
#
#     def test_migration_runs(self, v1_db: sqlite3.Connection) -> None:
#         applied = apply_pending_migrations(v1_db, 2)
#         assert applied == 1
#         assert _get_schema_version(v1_db) == 2
#
#     def test_data_preserved(self, v1_db: sqlite3.Connection) -> None:
#         apply_pending_migrations(v1_db, 2)
#         row = v1_db.execute("SELECT title FROM issues WHERE id = 'test-1'").fetchone()
#         assert row[0] == "Issue 1"
#
#     def test_new_column_has_default(self, v1_db: sqlite3.Connection) -> None:
#         apply_pending_migrations(v1_db, 2)
#         row = v1_db.execute("SELECT new_col FROM issues WHERE id = 'test-1'").fetchone()
#         assert row[0] == ""  # default value
#
#     def test_schema_matches_fresh(self, v1_db: sqlite3.Connection, tmp_path: Path) -> None:
#         """Migrated schema matches fresh SCHEMA_SQL."""
#         apply_pending_migrations(v1_db, 2)
#
#         fresh = _make_db(tmp_path, "fresh.db")
#         fresh.executescript(SCHEMA_SQL)
#         fresh.commit()
#
#         for table in ["issues", "dependencies", "events", "comments", "labels"]:
#             assert _get_table_columns(v1_db, table) == _get_table_columns(fresh, table), \
#                 f"Column mismatch in {table}"
#         fresh.close()


# ---------------------------------------------------------------------------
# File migration tests (from test_files.py)
# ---------------------------------------------------------------------------


class TestFileMigration:
    """Verify v1->v2 migration adds file tables to existing databases."""

    def test_migration_creates_tables(self, tmp_path: Path) -> None:
        # Create a fresh database
        d = FiligreeDB(tmp_path / "filigree.db", prefix="test")
        d.initialize()
        # Should be at current version (fresh DB gets latest schema)
        assert d.get_schema_version() == CURRENT_SCHEMA_VERSION
        d.close()

    def test_migration_from_v1(self, tmp_path: Path) -> None:
        """Simulate an existing v1 database that needs migration."""
        db_path = tmp_path / "filigree.db"
        conn = sqlite3.connect(str(db_path))
        # Manually create only v1 tables (without file tables)
        conn.executescript(SCHEMA_V1_SQL)
        # SCHEMA_V1_SQL predates the 'fields' column, but the v4->v5 migration
        # (release version normalization) expects it. Add it to simulate a
        # real v1 database that already had the fields column in its schema.
        conn.execute("ALTER TABLE issues ADD COLUMN fields TEXT DEFAULT '{}'")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

        # Opening with FiligreeDB should run migration
        d = FiligreeDB(db_path, prefix="test")
        d.initialize()
        assert d.get_schema_version() == CURRENT_SCHEMA_VERSION
        # File tables should now exist
        row = d.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_records'").fetchone()
        assert row is not None
        d.close()


# ---------------------------------------------------------------------------
# Schema versioning tests (from test_core_gaps.py)
# ---------------------------------------------------------------------------


class TestSchemaVersioning:
    def test_version_set_after_init(self, db: FiligreeDB) -> None:
        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION

    def test_fresh_db_gets_current_version(self, tmp_path: Path) -> None:
        """A fresh database should get CURRENT_SCHEMA_VERSION."""
        d = FiligreeDB(tmp_path / "filigree.db", prefix="test")
        d.initialize()
        assert d.get_schema_version() == CURRENT_SCHEMA_VERSION
        d.close()

    def test_custom_status_after_migration(self, db: FiligreeDB) -> None:
        """After v3 migration removes CHECK constraint, custom status values are accepted by SQLite."""
        db.conn.execute(
            "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at) "
            "VALUES ('test-custom1', 'Custom status', 'review', 2, 'task', '2026-01-01', '2026-01-01')",
        )
        db.conn.commit()
        issue = db.get_issue("test-custom1")
        assert issue.status == "review"
