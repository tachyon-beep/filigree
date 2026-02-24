"""Core database operations for the issue tracker.

Single source of truth for all SQLite operations. Both CLI and MCP server
import from this module. No daemon, no sync — just direct SQLite with WAL mode.

Covers issue CRUD, dependencies, events, comments, labels, workflow templates,
file records, scan findings, file associations, and file event timelines.

Convention-based discovery: each project has a `.filigree/` directory containing
`filigree.db` (SQLite) and `config.json` (project prefix, version).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from filigree.db_base import StatusCategory
from filigree.db_events import EventsMixin
from filigree.db_files import (
    VALID_ASSOC_TYPES,
    VALID_FINDING_STATUSES,
    VALID_SEVERITIES,
    FilesMixin,
    _normalize_scan_path,
)
from filigree.db_issues import IssuesMixin
from filigree.db_meta import MetaMixin
from filigree.db_planning import PlanningMixin
from filigree.db_workflow import WorkflowMixin

if TYPE_CHECKING:
    from filigree.templates import TemplateRegistry

logger = logging.getLogger(__name__)

# Re-exported names (canonical definitions moved to db_files during mixin split)
__all__ = [
    "VALID_ASSOC_TYPES",
    "VALID_FINDING_STATUSES",
    "VALID_SEVERITIES",
    "_normalize_scan_path",
]

# ---------------------------------------------------------------------------
# Constrained-string Literal types
# ---------------------------------------------------------------------------

Severity = Literal["critical", "high", "medium", "low", "info"]
FindingStatus = Literal["open", "acknowledged", "fixed", "false_positive", "unseen_in_latest"]


class ProjectConfig(TypedDict, total=False):
    """Shape of .filigree/config.json."""

    prefix: str
    version: int
    enabled_packs: list[str]
    mode: str


class PaginatedResult(TypedDict):
    """Envelope returned by paginated query methods."""

    results: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    has_more: bool


# ---------------------------------------------------------------------------
# Convention-based discovery
# ---------------------------------------------------------------------------

FILIGREE_DIR_NAME = ".filigree"
DB_FILENAME = "filigree.db"
CONFIG_FILENAME = "config.json"
SUMMARY_FILENAME = "context.md"


def find_filigree_root(start: Path | None = None) -> Path:
    """Walk up from start (default cwd) looking for .filigree/ directory.

    Returns the .filigree/ directory path (not the project root).
    """
    current = (start or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / FILIGREE_DIR_NAME
        if candidate.is_dir():
            return candidate
    msg = f"No {FILIGREE_DIR_NAME}/ directory found in {current} or any parent"
    raise FileNotFoundError(msg)


def read_config(filigree_dir: Path) -> ProjectConfig:
    """Read .filigree/config.json. Returns defaults if missing or corrupt."""
    defaults = ProjectConfig(prefix="filigree", version=1, enabled_packs=["core", "planning", "release"])
    config_path = filigree_dir / CONFIG_FILENAME
    if not config_path.exists():
        return defaults
    try:
        result: ProjectConfig = json.loads(config_path.read_text())
        return result
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s, using defaults: %s", config_path, exc)
        return defaults


def write_config(filigree_dir: Path, config: dict[str, Any] | ProjectConfig) -> None:
    """Write .filigree/config.json."""
    config_path = filigree_dir / CONFIG_FILENAME
    config_path.write_text(json.dumps(config, indent=2) + "\n")


VALID_MODES: frozenset[str] = frozenset({"ethereal", "server"})


def get_mode(filigree_dir: Path) -> str:
    """Return the installation mode for a project. Defaults to 'ethereal'."""
    config = read_config(filigree_dir)
    mode: str = config.get("mode", "ethereal")
    if mode not in VALID_MODES:
        logger.warning("Unknown mode '%s' in config, falling back to 'ethereal'", mode)
        return "ethereal"
    return mode


# ---------------------------------------------------------------------------
# Shared CLI / file helpers
# ---------------------------------------------------------------------------


def find_filigree_command() -> list[str]:
    """Locate the filigree CLI command as a list of argument tokens.

    Resolution order:
    1. shutil.which("filigree") -- absolute path if on PATH
    2. Sibling of running Python interpreter (covers venv case)
    3. sys.executable -m filigree -- module invocation fallback
    """
    which = shutil.which("filigree")
    if which:
        return [which]

    # Check sibling of Python interpreter (common in venvs)
    python_dir = Path(sys.executable).parent
    candidate = python_dir / "filigree"
    if candidate.is_file():
        return [str(candidate)]

    return [sys.executable, "-m", "filigree"]


def write_atomic(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + os.replace()."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS issues (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    priority    INTEGER NOT NULL DEFAULT 2,
    type        TEXT NOT NULL DEFAULT 'task',
    parent_id   TEXT REFERENCES issues(id) ON DELETE SET NULL,
    assignee    TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT,
    description TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    fields      TEXT DEFAULT '{}',

    CHECK (priority BETWEEN 0 AND 4)
);

CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
CREATE INDEX IF NOT EXISTS idx_issues_type ON issues(type);
CREATE INDEX IF NOT EXISTS idx_issues_parent ON issues(parent_id);
CREATE INDEX IF NOT EXISTS idx_issues_priority ON issues(priority);
CREATE INDEX IF NOT EXISTS idx_issues_status_priority ON issues(status, priority, created_at);

CREATE TABLE IF NOT EXISTS dependencies (
    issue_id       TEXT NOT NULL REFERENCES issues(id),
    depends_on_id  TEXT NOT NULL REFERENCES issues(id),
    type           TEXT NOT NULL DEFAULT 'blocks',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (issue_id, depends_on_id)
);

CREATE INDEX IF NOT EXISTS idx_deps_depends_on ON dependencies(depends_on_id);
CREATE INDEX IF NOT EXISTS idx_deps_issue_depends ON dependencies(issue_id, depends_on_id);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    event_type TEXT NOT NULL,
    actor      TEXT DEFAULT '',
    old_value  TEXT,
    new_value  TEXT,
    comment    TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_issue ON events(issue_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_issue_time ON events(issue_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedup
  ON events(issue_id, event_type, actor,
    coalesce(old_value,''), coalesce(new_value,''), created_at);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    author     TEXT DEFAULT '',
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_id, created_at);

CREATE TABLE IF NOT EXISTS labels (
    issue_id TEXT NOT NULL REFERENCES issues(id),
    label    TEXT NOT NULL,
    PRIMARY KEY (issue_id, label)
);

CREATE TABLE IF NOT EXISTS type_templates (
    type          TEXT PRIMARY KEY,
    pack          TEXT NOT NULL DEFAULT 'core',
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packs (
    name          TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    enabled       BOOLEAN NOT NULL DEFAULT 1
);

-- FTS5 full-text search with sync triggers
CREATE VIRTUAL TABLE IF NOT EXISTS issues_fts USING fts5(
    title, description, content='issues', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS issues_fts_insert AFTER INSERT ON issues BEGIN
    INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS issues_fts_update AFTER UPDATE OF title, description ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, description)
        VALUES('delete', old.rowid, old.title, old.description);
    INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS issues_fts_delete AFTER DELETE ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, description)
        VALUES('delete', old.rowid, old.title, old.description);
END;

-- ---- File records & scan findings (v2) -----------------------------------

CREATE TABLE IF NOT EXISTS file_records (
    id          TEXT PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    language    TEXT DEFAULT '',
    file_type   TEXT DEFAULT '',
    first_seen  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    metadata    TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_file_records_path ON file_records(path);
CREATE INDEX IF NOT EXISTS idx_file_records_language ON file_records(language);

CREATE TABLE IF NOT EXISTS scan_findings (
    id            TEXT PRIMARY KEY,
    file_id       TEXT NOT NULL REFERENCES file_records(id),
    issue_id      TEXT REFERENCES issues(id) ON DELETE SET NULL,
    scan_source   TEXT NOT NULL DEFAULT '',
    rule_id       TEXT DEFAULT '',
    severity      TEXT NOT NULL DEFAULT 'info',
    status        TEXT NOT NULL DEFAULT 'open',
    message       TEXT DEFAULT '',
    suggestion    TEXT DEFAULT '',
    scan_run_id   TEXT DEFAULT '',
    line_start    INTEGER,
    line_end      INTEGER,
    seen_count    INTEGER DEFAULT 1,
    first_seen    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_seen_at  TEXT,
    metadata      TEXT DEFAULT '{}',
    CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    CHECK (status IN ('open', 'acknowledged', 'fixed', 'false_positive', 'unseen_in_latest'))
);

CREATE INDEX IF NOT EXISTS idx_scan_findings_file ON scan_findings(file_id);
CREATE INDEX IF NOT EXISTS idx_scan_findings_issue ON scan_findings(issue_id);
CREATE INDEX IF NOT EXISTS idx_scan_findings_severity ON scan_findings(severity);
CREATE INDEX IF NOT EXISTS idx_scan_findings_status ON scan_findings(status);
CREATE INDEX IF NOT EXISTS idx_scan_findings_run ON scan_findings(scan_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scan_findings_dedup
  ON scan_findings(file_id, scan_source, rule_id, coalesce(line_start, -1));

CREATE TABLE IF NOT EXISTS file_associations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     TEXT NOT NULL REFERENCES file_records(id),
    issue_id    TEXT NOT NULL REFERENCES issues(id),
    assoc_type  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(file_id, issue_id, assoc_type),
    CHECK (assoc_type IN ('bug_in', 'task_for', 'scan_finding', 'mentioned_in'))
);

CREATE INDEX IF NOT EXISTS idx_file_assoc_file ON file_associations(file_id);
CREATE INDEX IF NOT EXISTS idx_file_assoc_issue ON file_associations(issue_id);

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
"""

# V1 schema (without file tables) — kept for migration tests.
# Defined as a standalone constant to avoid brittle string-split coupling.
SCHEMA_V1_SQL = """\
CREATE TABLE IF NOT EXISTS issues (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    priority    INTEGER NOT NULL DEFAULT 2,
    type        TEXT NOT NULL DEFAULT 'task',
    parent_id   TEXT REFERENCES issues(id) ON DELETE SET NULL,
    assignee    TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT,
    description TEXT DEFAULT '',
    notes       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS dependencies (
    issue_id       TEXT NOT NULL REFERENCES issues(id),
    depends_on_id  TEXT NOT NULL REFERENCES issues(id),
    type           TEXT NOT NULL DEFAULT 'blocks',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (issue_id, depends_on_id)
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    event_type TEXT NOT NULL,
    actor      TEXT DEFAULT '',
    old_value  TEXT,
    new_value  TEXT,
    comment    TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_issue ON events(issue_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_issue_time ON events(issue_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedup
  ON events(issue_id, event_type, actor,
    coalesce(old_value,''), coalesce(new_value,''), created_at);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id   TEXT NOT NULL REFERENCES issues(id),
    author     TEXT DEFAULT '',
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_id, created_at);

CREATE TABLE IF NOT EXISTS labels (
    issue_id TEXT NOT NULL REFERENCES issues(id),
    label    TEXT NOT NULL,
    PRIMARY KEY (issue_id, label)
);

CREATE TABLE IF NOT EXISTS type_templates (
    type          TEXT PRIMARY KEY,
    pack          TEXT NOT NULL DEFAULT 'core',
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packs (
    name          TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    enabled       BOOLEAN NOT NULL DEFAULT 1
);

-- FTS5 full-text search with sync triggers
CREATE VIRTUAL TABLE IF NOT EXISTS issues_fts USING fts5(
    title, description, content='issues', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS issues_fts_insert AFTER INSERT ON issues BEGIN
    INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS issues_fts_update AFTER UPDATE OF title, description ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, description)
        VALUES('delete', old.rowid, old.title, old.description);
    INSERT INTO issues_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS issues_fts_delete AFTER DELETE ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, description)
        VALUES('delete', old.rowid, old.title, old.description);
END;
"""

CURRENT_SCHEMA_VERSION = 4


def _seed_builtin_packs(conn: sqlite3.Connection, now: str) -> int:
    """Seed built-in packs and type templates into the database.

    Returns the number of type templates seeded.
    """
    from filigree.templates_data import BUILT_IN_PACKS

    count = 0
    default_enabled = {"core", "planning", "release"}

    for pack_name, pack_data in BUILT_IN_PACKS.items():
        enabled = 1 if pack_name in default_enabled else 0
        conn.execute(
            "INSERT OR IGNORE INTO packs (name, version, definition, is_builtin, enabled) VALUES (?, ?, ?, 1, ?)",
            (pack_name, pack_data.get("version", "1.0"), json.dumps(pack_data), enabled),
        )
        logger.debug("Seeded pack: %s (enabled=%d)", pack_name, enabled)

        for type_name, type_data in pack_data.get("types", {}).items():
            conn.execute(
                "INSERT OR REPLACE INTO type_templates (type, pack, definition, is_builtin, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (type_name, pack_name, json.dumps(type_data), now, now),
            )
            count += 1
            logger.debug("Seeded type template: %s (pack=%s)", type_name, pack_name)

    return count


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    id: str
    title: str
    status: str = "open"
    priority: int = 2
    type: str = "task"
    parent_id: str | None = None
    assignee: str = ""
    created_at: str = ""
    updated_at: str = ""
    closed_at: str | None = None
    description: str = ""
    notes: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    # Computed (not stored directly)
    labels: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    is_ready: bool = False
    children: list[str] = field(default_factory=list)
    status_category: StatusCategory = "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "status_category": self.status_category,
            "priority": self.priority,
            "type": self.type,
            "parent_id": self.parent_id,
            "assignee": self.assignee,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "description": self.description,
            "notes": self.notes,
            "fields": self.fields,
            "labels": self.labels,
            "blocks": self.blocks,
            "blocked_by": self.blocked_by,
            "is_ready": self.is_ready,
            "children": self.children,
        }


@dataclass
class FileRecord:
    id: str
    path: str
    language: str = ""
    file_type: str = ""
    first_seen: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "language": self.language,
            "file_type": self.file_type,
            "first_seen": self.first_seen,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass
class ScanFinding:
    id: str
    file_id: str
    severity: Severity = "info"
    status: str = "open"
    scan_source: str = ""
    rule_id: str = ""
    message: str = ""
    suggestion: str = ""
    scan_run_id: str = ""
    line_start: int | None = None
    line_end: int | None = None
    issue_id: str | None = None
    seen_count: int = 1
    first_seen: str = ""
    updated_at: str = ""
    last_seen_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_id": self.file_id,
            "severity": self.severity,
            "status": self.status,
            "scan_source": self.scan_source,
            "rule_id": self.rule_id,
            "message": self.message,
            "suggestion": self.suggestion,
            "scan_run_id": self.scan_run_id,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "issue_id": self.issue_id,
            "seen_count": self.seen_count,
            "first_seen": self.first_seen,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# FiligreeDB — the core
# ---------------------------------------------------------------------------


class FiligreeDB(FilesMixin, IssuesMixin, EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin):
    """Direct SQLite operations. No daemon, no sync. Importable by CLI and MCP."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        prefix: str = "filigree",
        enabled_packs: list[str] | None = None,
        template_registry: TemplateRegistry | None = None,
        check_same_thread: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.prefix = prefix
        self._enabled_packs_override = list(enabled_packs) if enabled_packs is not None else None
        self.enabled_packs = self._enabled_packs_override if self._enabled_packs_override is not None else ["core", "planning", "release"]
        self._conn: sqlite3.Connection | None = None
        self._check_same_thread = check_same_thread
        self._template_registry: TemplateRegistry | None = template_registry

    @classmethod
    def from_project(cls, project_path: Path | None = None) -> FiligreeDB:
        """Create a FiligreeDB by discovering .filigree/ from project_path (or cwd)."""
        filigree_dir = find_filigree_root(project_path)
        config = read_config(filigree_dir)
        db = cls(
            filigree_dir / DB_FILENAME,
            prefix=config.get("prefix", "filigree"),
            enabled_packs=config.get("enabled_packs"),
        )
        db.initialize()
        return db

    def __enter__(self) -> FiligreeDB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                isolation_level="DEFERRED",
                check_same_thread=self._check_same_thread,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def initialize(self) -> None:
        """Create tables (if new) or migrate (if existing), then seed templates.

        For a fresh database (user_version == 0), creates all tables from
        SCHEMA_SQL and stamps the current version. For an existing database,
        applies any pending migrations to bring it up to CURRENT_SCHEMA_VERSION.
        """
        current_version = self.get_schema_version()

        if current_version == 0:
            # Fresh database — create everything from scratch
            self.conn.executescript(SCHEMA_SQL)
            self.conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        elif current_version < CURRENT_SCHEMA_VERSION:
            # Existing database — apply pending migrations
            from filigree.migrations import apply_pending_migrations

            apply_pending_migrations(self.conn, CURRENT_SCHEMA_VERSION)

        self._seed_templates()
        self.conn.commit()

    def get_schema_version(self) -> int:
        """Return the current schema version from PRAGMA user_version."""
        result: int = self.conn.execute("PRAGMA user_version").fetchone()[0]
        return result

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
