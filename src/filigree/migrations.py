"""Schema migration framework for filigree.

Migrations are version-keyed functions that transform the database schema
from one version to the next. Each migration receives a raw sqlite3.Connection
and must be idempotent (safe to re-run, using IF NOT EXISTS / IF EXISTS).

The migration runner:
  1. Reads the current schema version via PRAGMA user_version
  2. Applies each pending migration in order
  3. Bumps user_version after each successful migration
  4. Wraps each migration in a transaction (rollback on failure)

Usage — adding a new migration:
  1. Increment CURRENT_SCHEMA_VERSION in core.py
  2. Add a function here: def migrate_v<N>_to_v<N+1>(conn) -> None
  3. Register it in MIGRATIONS: N: migrate_v<N>_to_v<N+1>
  4. Update SCHEMA_SQL in core.py to match the post-migration state
  5. Add a test in tests/test_migrations.py

SQLite ALTER TABLE limitations (why helpers exist):
  - ADD COLUMN: supported (but only with constant defaults, no NOT NULL without DEFAULT)
  - DROP COLUMN: supported only in SQLite >= 3.35.0
  - RENAME COLUMN: supported since SQLite 3.25.0
  - ALTER column type/constraints: NOT supported — use rebuild_table()
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Migration function protocol
# ---------------------------------------------------------------------------


class MigrationFn(Protocol):
    """Protocol for migration functions."""

    def __call__(self, conn: sqlite3.Connection) -> None: ...


# ---------------------------------------------------------------------------
# Migration registry
#
# Keys are the version being migrated FROM (i.e., the current user_version).
# Values are functions that transform the schema to the next version.
#
# Example: {1: migrate_v1_to_v2} means "if user_version == 1, run this to get to 2"
# ---------------------------------------------------------------------------


def migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 → v2: Add file records, scan findings, and file associations tables.

    Changes:
      - new table 'file_records' for tracking source code files
      - new table 'scan_findings' for security/code quality scan results
      - new table 'file_associations' for linking files to issues
      - indexes for efficient querying
    """
    conn.execute("""\
        CREATE TABLE IF NOT EXISTS file_records (
            id          TEXT PRIMARY KEY,
            path        TEXT NOT NULL UNIQUE,
            language    TEXT DEFAULT '',
            file_type   TEXT DEFAULT '',
            first_seen  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            metadata    TEXT DEFAULT '{}'
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_records_path ON file_records(path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_records_language ON file_records(language)")

    conn.execute("""\
        CREATE TABLE IF NOT EXISTS scan_findings (
            id            TEXT PRIMARY KEY,
            file_id       TEXT NOT NULL REFERENCES file_records(id),
            issue_id      TEXT REFERENCES issues(id) ON DELETE SET NULL,
            scan_source   TEXT NOT NULL DEFAULT '',
            rule_id       TEXT DEFAULT '',
            severity      TEXT NOT NULL DEFAULT 'info',
            status        TEXT NOT NULL DEFAULT 'open',
            message       TEXT DEFAULT '',
            line_start    INTEGER,
            line_end      INTEGER,
            seen_count    INTEGER DEFAULT 1,
            first_seen    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            last_seen_at  TEXT,
            metadata      TEXT DEFAULT '{}',
            CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
            CHECK (status IN ('open', 'acknowledged', 'fixed', 'false_positive', 'unseen_in_latest'))
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_findings_file ON scan_findings(file_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_findings_issue ON scan_findings(issue_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_findings_severity ON scan_findings(severity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_findings_status ON scan_findings(status)")
    conn.execute("""\
        CREATE UNIQUE INDEX IF NOT EXISTS idx_scan_findings_dedup
          ON scan_findings(file_id, scan_source, rule_id, coalesce(line_start, -1))""")

    conn.execute("""\
        CREATE TABLE IF NOT EXISTS file_associations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id     TEXT NOT NULL REFERENCES file_records(id),
            issue_id    TEXT NOT NULL REFERENCES issues(id),
            assoc_type  TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            UNIQUE(file_id, issue_id, assoc_type),
            CHECK (assoc_type IN ('bug_in', 'task_for', 'scan_finding', 'mentioned_in'))
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_assoc_file ON file_associations(file_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_assoc_issue ON file_associations(issue_id)")


MIGRATIONS: dict[int, MigrationFn] = {
    1: migrate_v1_to_v2,
    # 2: migrate_v2_to_v3,
}


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------


class MigrationError(Exception):
    """Raised when a migration fails."""

    def __init__(self, from_version: int, to_version: int, cause: Exception) -> None:
        self.from_version = from_version
        self.to_version = to_version
        self.cause = cause
        super().__init__(f"Migration v{from_version} → v{to_version} failed: {cause}")


def apply_pending_migrations(conn: sqlite3.Connection, target_version: int) -> int:
    """Apply all pending migrations from current version up to target_version.

    Args:
        conn: Open SQLite connection (must have row_factory and PRAGMAs already set).
        target_version: The CURRENT_SCHEMA_VERSION from core.py.

    Returns:
        Number of migrations applied (0 if already up to date).

    Raises:
        MigrationError: If any individual migration fails (DB rolled back to
            the last successful migration).
        ValueError: If current version > target (downgrade not supported).
    """
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]

    if current == target_version:
        return 0

    if current > target_version:
        msg = f"Database schema v{current} is newer than this version of filigree (expects v{target_version}). Downgrade is not supported."
        raise ValueError(msg)

    applied = 0
    for version in range(current, target_version):
        migration = MIGRATIONS.get(version)
        if migration is None:
            msg = (
                f"No migration registered for v{version} → v{version + 1}. "
                f"Database is at v{version}, target is v{target_version}. "
                f"Register the migration in filigree.migrations.MIGRATIONS."
            )
            raise MigrationError(version, version + 1, KeyError(msg))

        logger.info("Applying migration v%d → v%d ...", version, version + 1)
        try:
            conn.execute("BEGIN IMMEDIATE")
            migration(conn)
            conn.execute(f"PRAGMA user_version = {version + 1}")
            conn.commit()
            applied += 1
            logger.info("Migration v%d → v%d complete.", version, version + 1)
        except Exception as exc:
            conn.rollback()
            raise MigrationError(version, version + 1, exc) from exc

    return applied


# ---------------------------------------------------------------------------
# SQLite migration helpers
#
# These handle the quirks of SQLite's limited ALTER TABLE support.
# ---------------------------------------------------------------------------


def add_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str = "TEXT",
    default: str | None = "''",
) -> None:
    """Add a column to a table (idempotent).

    Args:
        conn: SQLite connection.
        table: Table name.
        column: New column name.
        col_type: SQL type (TEXT, INTEGER, REAL, BLOB).
        default: DEFAULT value as a SQL literal (e.g., "''" or "0" or "NULL").
                 If None, no DEFAULT clause is added.

    Note: SQLite requires a DEFAULT for ADD COLUMN with NOT NULL.
    """
    # Check if column already exists (idempotent)
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in existing:
        return

    default_clause = f" DEFAULT {default}" if default is not None else ""
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{default_clause}")


def add_index(
    conn: sqlite3.Connection,
    index_name: str,
    table: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    """Create an index (idempotent via IF NOT EXISTS).

    Args:
        conn: SQLite connection.
        index_name: Name for the index.
        table: Table to index.
        columns: Column names to include.
        unique: Whether to create a UNIQUE index.
    """
    unique_kw = "UNIQUE " if unique else ""
    cols = ", ".join(columns)
    conn.execute(f"CREATE {unique_kw}INDEX IF NOT EXISTS {index_name} ON {table}({cols})")


def drop_index(conn: sqlite3.Connection, index_name: str) -> None:
    """Drop an index (idempotent via IF EXISTS)."""
    conn.execute(f"DROP INDEX IF EXISTS {index_name}")


def rename_column(conn: sqlite3.Connection, table: str, old_name: str, new_name: str) -> None:
    """Rename a column (SQLite >= 3.25.0, idempotent).

    Checks column existence before attempting rename.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if new_name in existing:
        return  # Already renamed
    if old_name not in existing:
        msg = f"Column {old_name!r} not found in table {table!r}"
        raise ValueError(msg)
    conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}")


def rebuild_table(
    conn: sqlite3.Connection,
    table: str,
    new_schema_sql: str,
    column_mapping: dict[str, str] | None = None,
) -> None:
    """Recreate a table with a new schema, preserving data.

    This is the "12-step" pattern for SQLite schema changes that ALTER TABLE
    can't handle (changing types, constraints, removing columns, etc.):
      1. Create new table with temp name
      2. Copy data from old table
      3. Drop old table
      4. Rename new table to original name

    Args:
        conn: SQLite connection.
        table: Existing table name.
        new_schema_sql: Full CREATE TABLE statement for the new schema.
                        Must use the table name directly (not a temp name —
                        this function handles the rename dance).
        column_mapping: Optional mapping of {new_col: old_col_or_expr}.
                        If None, copies all columns that exist in both schemas.
                        Use SQL expressions as values for transformations, e.g.:
                        {"priority": "CASE WHEN priority > 4 THEN 4 ELSE priority END"}

    Warning: This drops all indexes, triggers, and views that reference the table.
             Recreate them after calling this function.
    """
    temp_table = f"_filigree_migrate_{table}"

    # Create temp table with new schema (case-insensitive replace of table name)
    pattern = re.compile(
        rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(table)}\b",
        re.IGNORECASE,
    )
    temp_schema = pattern.sub(f"CREATE TABLE {temp_table}", new_schema_sql, count=1)

    conn.execute(f"DROP TABLE IF EXISTS {temp_table}")  # Clean up any leftover from failed run
    conn.execute(temp_schema)

    if column_mapping is None:
        # Auto-detect shared columns
        old_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        new_cols = [row[1] for row in conn.execute(f"PRAGMA table_info({temp_table})").fetchall()]
        shared = [c for c in new_cols if c in old_cols]
        if not shared:
            conn.execute(f"DROP TABLE IF EXISTS {temp_table}")
            msg = f"No shared columns between old and new schema for table '{table}'"
            raise ValueError(msg)
        select_cols = ", ".join(shared)
        insert_cols = select_cols
    else:
        insert_cols = ", ".join(column_mapping.keys())
        select_cols = ", ".join(column_mapping.values())

    # S608: table/column names are from internal schema, not user input
    insert_sql = f"INSERT INTO {temp_table} ({insert_cols}) SELECT {select_cols} FROM {table}"  # noqa: S608
    conn.execute(insert_sql)

    try:
        conn.execute(f"DROP TABLE {table}")
    except sqlite3.IntegrityError:
        # FK constraint prevents direct drop — must temporarily disable FK
        # enforcement.  PRAGMA foreign_keys=OFF only takes effect outside a
        # transaction, so we commit any active transaction first.
        #
        # IMPORTANT: This creates a "point of no return" for the caller's
        # transaction.  If the caller (e.g. migration runner) later fails,
        # the rebuild itself CANNOT be rolled back.  This is an inherent
        # SQLite limitation — there is no way to atomically rebuild an
        # FK-referenced table within a single transaction.  Alternatives
        # (defer_foreign_keys, RENAME dance) do not work because SQLite's
        # commit-time FK check and RENAME-time FK reference updates prevent
        # them from succeeding.
        #
        # Migrations that rebuild FK-referenced tables should place the
        # rebuild as the LAST operation to minimize post-rebuild failure risk.
        in_txn = conn.in_transaction
        if in_txn:
            conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table}")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(f"Foreign key violations after rebuilding '{table}': {violations}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        if in_txn:
            conn.execute("BEGIN IMMEDIATE")
        return

    conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table}")


# ---------------------------------------------------------------------------
# Migration templates
#
# Copy one of these as a starting point for a new migration. Keep the
# docstring — it serves as the migration's changelog entry.
# ---------------------------------------------------------------------------


def _template_simple_migration(conn: sqlite3.Connection) -> None:
    """v1 → v2: <describe what changes and why>.

    Changes:
      - issues: add 'foo' column (TEXT, default '')
      - new index idx_issues_foo on issues(foo)
    """
    add_column(conn, "issues", "foo", "TEXT", "''")
    add_index(conn, "idx_issues_foo", "issues", ["foo"])


def _template_table_rebuild_migration(conn: sqlite3.Connection) -> None:
    """v2 → v3: <describe what changes and why>.

    Changes:
      - issues: change priority CHECK constraint from 0-4 to 0-5
      - issues: drop legacy 'foo' column

    Uses rebuild_table because ALTER TABLE can't modify constraints.

    NOTE: If the rebuilt table is referenced by FK from other tables,
    place the rebuild_table() call LAST — see rebuild_table() docstring
    for details on the non-atomic FK rebuild limitation.
    """
    new_schema = """\
    CREATE TABLE issues (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'open',
        priority    INTEGER NOT NULL DEFAULT 2,
        -- ... full table definition with new constraints ...
        CHECK (priority BETWEEN 0 AND 5)
    )"""

    rebuild_table(
        conn,
        "issues",
        new_schema,
        # Explicit column mapping — omit 'foo' to drop it, transform priority
        column_mapping={
            "id": "id",
            "title": "title",
            "status": "status",
            "priority": "MIN(priority, 5)",  # clamp old values to new range
        },
    )

    # Recreate indexes (rebuild_table drops them)
    add_index(conn, "idx_issues_status", "issues", ["status"])
    add_index(conn, "idx_issues_priority", "issues", ["priority"])


def _template_new_table_migration(conn: sqlite3.Connection) -> None:
    """v3 → v4: <describe what changes and why>.

    Changes:
      - new table 'attachments' for file references
      - new index on attachments(issue_id)
    """
    # Use execute() not executescript() — executescript implicitly commits,
    # breaking the migration runner's per-migration transaction guarantees.
    conn.execute("""\
        CREATE TABLE IF NOT EXISTS attachments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id   TEXT NOT NULL REFERENCES issues(id),
            filename   TEXT NOT NULL,
            mime_type  TEXT DEFAULT '',
            size_bytes INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attachments_issue ON attachments(issue_id)")
