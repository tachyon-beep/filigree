"""Tests for the schema migration framework.

Testing strategy:
  1. Framework tests — verify the migration runner handles edge cases
  2. Schema equivalence — migrated DB must match a fresh DB at the same version
  3. Per-migration tests — each migration gets its own test class (add as needed)

To add tests for a new migration (e.g., v2 → v3):
  1. Add a TestMigrateV2ToV3 class below
  2. Create a v2 database fixture (snapshot of SCHEMA_SQL before the migration)
  3. Run the migration and verify:
     - Schema matches fresh initialization
     - Data is preserved and transformed correctly
     - Indexes exist
     - Constraints are enforced
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from filigree.core import CURRENT_SCHEMA_VERSION, SCHEMA_SQL, FiligreeDB
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
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _get_table_names(conn: sqlite3.Connection) -> set[str]:
    """Return all table names (excluding FTS shadow tables)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _get_schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


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

        migrations.MIGRATIONS[1] = fake_v1_to_v2
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

        migrations.MIGRATIONS[1] = bad_migration
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

        migrations.MIGRATIONS[1] = m1
        migrations.MIGRATIONS[2] = m2
        migrations.MIGRATIONS[3] = m3
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
        """If migration 2→3 fails, v1→v2 is still committed."""
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

        migrations.MIGRATIONS[1] = m1
        migrations.MIGRATIONS[2] = m2_fail
        try:
            with pytest.raises(MigrationError, match="Boom"):
                apply_pending_migrations(conn, 3)
            # v1→v2 succeeded and was committed
            assert _get_schema_version(conn) == 2
            assert "a" in _get_table_columns(conn, "t")
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
    # Uncomment and adapt when adding migration v1 → v2:
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
#     """Tests for migration v1 → v2.
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
