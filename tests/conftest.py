"""Shared pytest fixtures for filigree tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.core import (
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
    write_config,
)


@pytest.fixture
def db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """Fresh FiligreeDB for each test."""
    d = FiligreeDB(tmp_path / "filigree.db", prefix="test")
    d.initialize()
    yield d
    d.close()


@pytest.fixture
def populated_db(db: FiligreeDB) -> FiligreeDB:
    """FiligreeDB pre-populated with a representative issue set.

    Creates:
    - 3 issues (A=open P1, B=open P2, C=closed P3)
    - Dependency: A depends on B
    - Labels on A: ["bug", "urgent"]
    - Comment on B
    - Epic E with child A
    """
    epic = db.create_issue("Epic E", type="epic", priority=1)
    a = db.create_issue("Issue A", priority=1, labels=["bug", "urgent"], parent_id=epic.id)
    b = db.create_issue("Issue B", priority=2)
    c = db.create_issue("Issue C", priority=3)
    db.close_issue(c.id, reason="done")
    db.add_dependency(a.id, b.id)
    db.add_comment(b.id, "Test comment", author="tester")
    # Store IDs for easy access in tests
    db._test_ids: dict[str, str] = {"epic": epic.id, "a": a.id, "b": b.id, "c": c.id}  # type: ignore[attr-defined]
    return db


@pytest.fixture
def filigree_project(tmp_path: Path) -> Path:
    """A tmp directory set up as a filigree project (.filigree/ with config + db).

    Returns the project root (parent of .filigree/).
    """
    filigree_dir = tmp_path / FILIGREE_DIR_NAME
    filigree_dir.mkdir()
    write_config(filigree_dir, {"prefix": "proj", "version": 1})

    d = FiligreeDB(filigree_dir / DB_FILENAME, prefix="proj")
    d.initialize()
    d.close()

    # Create empty context.md
    (filigree_dir / SUMMARY_FILENAME).write_text("# summary\n")

    return tmp_path


@pytest.fixture
def cli_runner() -> CliRunner:
    """Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def beads_db(tmp_path: Path) -> Path:
    """Create a minimal beads-schema SQLite DB for migration tests.

    Schema mirrors the beads issue tracker with representative data.
    """
    db_path = tmp_path / "beads.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE issues (
            id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT DEFAULT 'open',
            priority INTEGER DEFAULT 2,
            issue_type TEXT DEFAULT 'task',
            parent_id TEXT,
            parent_epic TEXT,
            assignee TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT,
            closed_at TEXT,
            deleted_at TEXT,
            description TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            metadata TEXT DEFAULT 'null',
            design TEXT DEFAULT '',
            acceptance_criteria TEXT DEFAULT '',
            estimated_minutes INTEGER DEFAULT 0,
            close_reason TEXT DEFAULT '',
            external_ref TEXT DEFAULT '',
            mol_type TEXT DEFAULT '',
            work_type TEXT DEFAULT '',
            quality_score TEXT DEFAULT '',
            source_system TEXT DEFAULT '',
            event_kind TEXT DEFAULT '',
            actor TEXT DEFAULT '',
            target TEXT DEFAULT '',
            payload TEXT DEFAULT '',
            source_repo TEXT DEFAULT '',
            await_type TEXT DEFAULT '',
            await_id TEXT DEFAULT '',
            role_type TEXT DEFAULT '',
            rig TEXT DEFAULT '',
            spec_id TEXT DEFAULT '',
            wisp_type TEXT DEFAULT '',
            sender TEXT DEFAULT ''
        );

        CREATE TABLE dependencies (
            issue_id TEXT NOT NULL,
            depends_on_id TEXT NOT NULL,
            type TEXT DEFAULT 'blocks',
            PRIMARY KEY (issue_id, depends_on_id)
        );

        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id TEXT NOT NULL,
            event_type TEXT,
            actor TEXT DEFAULT '',
            old_value TEXT,
            new_value TEXT,
            comment TEXT DEFAULT '',
            created_at TEXT
        );

        CREATE TABLE labels (
            issue_id TEXT NOT NULL,
            label TEXT NOT NULL,
            PRIMARY KEY (issue_id, label)
        );

        CREATE TABLE comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id TEXT NOT NULL,
            author TEXT DEFAULT '',
            text TEXT NOT NULL,
            created_at TEXT
        );
    """)

    # Insert test data
    now = "2026-01-15T10:00:00+00:00"
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, issue_type, parent_id, parent_epic, "
        "assignee, created_at, updated_at, description, metadata, design) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("bd-aaa111", "Epic one", "open", 1, "epic", None, None, "", now, now, "An epic", "null", ""),
    )
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, issue_type, parent_id, parent_epic, "
        "assignee, created_at, updated_at, description, metadata, design) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("bd-bbb222", "Task under epic", "open", 2, "task", None, "bd-aaa111", "alice", now, now, "A task", "null", ""),
    )
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, issue_type, parent_id, parent_epic, "
        "assignee, created_at, updated_at, closed_at, description, metadata, design) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "bd-ccc333",
            "Closed bug",
            "closed",
            0,
            "bug",
            "bd-aaa111",
            None,
            "bob",
            now,
            now,
            now,
            "A bug",
            json.dumps({"source": "import"}),
            "fix the thing",
        ),
    )
    # Deleted issue (should NOT be migrated)
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, issue_type, deleted_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("bd-del999", "Deleted issue", "open", 2, "task", now, now, now),
    )
    # Issue with unknown status
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, issue_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("bd-ddd444", "Weird status", "review", 2, "task", now, now),
    )

    # Dependencies
    conn.execute(
        "INSERT INTO dependencies (issue_id, depends_on_id, type) VALUES (?, ?, ?)",
        ("bd-bbb222", "bd-ccc333", "blocks"),
    )
    # Dangling dependency (references deleted issue)
    conn.execute(
        "INSERT INTO dependencies (issue_id, depends_on_id, type) VALUES (?, ?, ?)",
        ("bd-bbb222", "bd-del999", "blocks"),
    )

    # Events
    conn.execute(
        "INSERT INTO events (issue_id, event_type, actor, new_value, created_at) VALUES (?, ?, ?, ?, ?)",
        ("bd-aaa111", "created", "system", "Epic one", now),
    )

    # Labels
    conn.execute("INSERT INTO labels (issue_id, label) VALUES (?, ?)", ("bd-aaa111", "important"))
    conn.execute("INSERT INTO labels (issue_id, label) VALUES (?, ?)", ("bd-bbb222", "backend"))

    # Comments
    conn.execute(
        "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
        ("bd-bbb222", "alice", "working on this", now),
    )

    conn.commit()
    conn.close()
    return db_path
