"""Core database operations for the issue tracker.

Single source of truth for all SQLite operations. Both CLI and MCP server
import from this module. No daemon, no sync — just direct SQLite with WAL mode.

Convention-based discovery: each project has a `.filigree/` directory containing
`filigree.db` (SQLite) and `config.json` (project prefix, version).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re as _re
import shutil
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from filigree.templates import TemplateRegistry, TransitionOption, ValidationResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Undo constants
# ---------------------------------------------------------------------------

_REVERSIBLE_EVENTS = frozenset(
    {
        "status_changed",
        "title_changed",
        "priority_changed",
        "assignee_changed",
        "claimed",
        "dependency_added",
        "dependency_removed",
        "description_changed",
        "notes_changed",
    }
)
_SKIP_EVENTS = frozenset({"transition_warning"})

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


def read_config(filigree_dir: Path) -> dict[str, Any]:
    """Read .filigree/config.json. Returns defaults if missing."""
    config_path = filigree_dir / CONFIG_FILENAME
    if config_path.exists():
        result: dict[str, Any] = json.loads(config_path.read_text())
        return result
    return {"prefix": "filigree", "version": 1, "enabled_packs": ["core", "planning"]}


def write_config(filigree_dir: Path, config: dict[str, Any]) -> None:
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


def _normalize_scan_path(path: str) -> str:
    """Normalize scanner-provided paths for stable file identity."""
    normalized = os.path.normpath(path.replace("\\", "/"))
    return "" if normalized == "." else normalized


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

# V1 schema (without file tables) — kept for migration tests
SCHEMA_V1_SQL = SCHEMA_SQL.split("-- ---- File records & scan findings (v2)")[0]
if SCHEMA_V1_SQL == SCHEMA_SQL:
    raise RuntimeError("SCHEMA_V1_SQL split marker not found — check comment text in SCHEMA_SQL")

CURRENT_SCHEMA_VERSION = 4


def _seed_builtin_packs(conn: sqlite3.Connection, now: str) -> int:
    """Seed built-in packs and type templates into the database.

    Returns the number of type templates seeded.
    """
    from filigree.templates_data import BUILT_IN_PACKS

    count = 0
    default_enabled = {"core", "planning"}

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
    status_category: str = "open"

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
    severity: str = "info"
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


VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
VALID_FINDING_STATUSES = frozenset({"open", "acknowledged", "fixed", "false_positive", "unseen_in_latest"})
VALID_ASSOC_TYPES = frozenset({"bug_in", "task_for", "scan_finding", "mentioned_in"})


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def _generate_id_standalone(prefix: str) -> str:
    """Generate a short unique ID like 'myproject-a3f' (no collision check)."""
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# FiligreeDB — the core
# ---------------------------------------------------------------------------


class FiligreeDB:
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
        self.enabled_packs = self._enabled_packs_override if self._enabled_packs_override is not None else ["core", "planning"]
        self._conn: sqlite3.Connection | None = None
        self._check_same_thread = check_same_thread
        self._template_registry: TemplateRegistry | None = template_registry
        self._check_same_thread = check_same_thread

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

    @property
    def templates(self) -> TemplateRegistry:
        """Lazy-loaded TemplateRegistry — created on first access.

        Uses runtime import to avoid circular dependency (WFT-AR-001).
        Can be overridden via constructor injection for testing.
        """
        if self._template_registry is None:
            from filigree.templates import TemplateRegistry

            self._template_registry = TemplateRegistry()
            filigree_dir = self.db_path.parent
            self._template_registry.load(filigree_dir, enabled_packs=self._enabled_packs_override)
        return self._template_registry

    # -- Templates -----------------------------------------------------------

    def _seed_templates(self) -> None:
        """Seed built-in packs and type templates into the database."""
        now = _now_iso()
        _seed_builtin_packs(self.conn, now)

    def reload_templates(self) -> None:
        """Clear the cached template registry so it reloads on next access."""
        self._template_registry = None

    def get_template(self, issue_type: str) -> dict[str, Any] | None:
        """Get a template by type name from the registry."""
        tpl = self.templates.get_type(issue_type)
        if tpl is None:
            return None
        fields_schema: list[dict[str, Any]] = []
        for f in tpl.fields_schema:
            field_dict: dict[str, Any] = {"name": f.name, "type": f.type, "description": f.description}
            if f.options:
                field_dict["options"] = list(f.options)
            if f.default is not None:
                field_dict["default"] = f.default
            if f.required_at:
                field_dict["required_at"] = list(f.required_at)
            fields_schema.append(field_dict)
        return {
            "type": tpl.type,
            "display_name": tpl.display_name,
            "description": tpl.description,
            "states": [{"name": s.name, "category": s.category} for s in tpl.states],
            "initial_state": tpl.initial_state,
            "transitions": [
                {
                    "from": t.from_state,
                    "to": t.to_state,
                    "enforcement": t.enforcement,
                    "requires_fields": list(t.requires_fields),
                }
                for t in tpl.transitions
            ],
            "fields_schema": fields_schema,
        }

    def list_templates(self) -> list[dict[str, Any]]:
        """List all registered templates via the registry (respects enabled_packs)."""
        result: list[dict[str, Any]] = []
        for tpl in self.templates.list_types():
            result.append(
                {
                    "type": tpl.type,
                    "display_name": tpl.display_name,
                    "description": tpl.description,
                    "fields_schema": [{"name": f.name, "type": f.type, "description": f.description} for f in tpl.fields_schema],
                }
            )
        return sorted(result, key=lambda t: t["type"])

    def _validate_status(self, status: str, issue_type: str = "task") -> None:
        """Validate status against type-specific states from templates.

        Unknown types (no template) skip validation — permissive for custom types.
        """
        valid_states = self.templates.get_valid_states(issue_type)
        if valid_states is not None and status not in valid_states:
            msg = f"Invalid status '{status}' for type '{issue_type}'. Valid states: {', '.join(valid_states)}"
            raise ValueError(msg)

    def _validate_parent_id(self, parent_id: str | None) -> None:
        """Raise ValueError if parent_id does not reference an existing issue."""
        if parent_id is None:
            return
        exists = self.conn.execute("SELECT 1 FROM issues WHERE id = ?", (parent_id,)).fetchone()
        if exists is None:
            msg = f"parent_id '{parent_id}' does not reference an existing issue"
            raise ValueError(msg)

    def _get_states_for_category(self, category: str) -> list[str]:
        """Collect all state names that map to a category across enabled types.

        Returns deduplicated list. Empty if no types are registered.
        """
        states: list[str] = []
        for tpl in self.templates.list_types():
            for s in tpl.states:
                if s.category == category and s.name not in states:
                    states.append(s.name)
        return states

    @staticmethod
    def _infer_status_category(status: str) -> str:
        """Infer status category from status name when no template is available."""
        done_names = {"closed", "done", "resolved", "wont_fix", "cancelled", "archived"}
        wip_names = {"in_progress", "fixing", "verifying", "reviewing", "testing", "active"}
        if status in done_names:
            return "done"
        if status in wip_names:
            return "wip"
        return "open"

    def _resolve_status_category(self, issue_type: str, status: str) -> str:
        """Resolve status category via template or fallback heuristic for unknown types."""
        cat = self.templates.get_category(issue_type, status)
        if cat is not None:
            return cat
        return self._infer_status_category(status)

    def _generate_id(self) -> str:
        """Generate a unique ID using O(1) EXISTS checks against the PK index."""
        for _ in range(10):
            candidate = f"{self.prefix}-{uuid.uuid4().hex[:6]}"
            exists = self.conn.execute("SELECT 1 FROM issues WHERE id = ?", (candidate,)).fetchone()
            if exists is None:
                return candidate
        # 10 collisions in a row is astronomically unlikely; use longer suffix
        return f"{self.prefix}-{uuid.uuid4().hex[:10]}"

    def _reserved_label_names(self) -> set[str]:
        """Issue type names are reserved and cannot be used as free-form labels."""
        return {tpl.type.casefold() for tpl in self.templates.list_types()}

    def _validate_label_name(self, label: str) -> str:
        """Normalize and validate a label before writing it."""
        if not isinstance(label, str):
            msg = "Label must be a string"
            raise ValueError(msg)
        normalized = label.strip()
        if not normalized:
            msg = "Label cannot be empty"
            raise ValueError(msg)
        if normalized.casefold() in self._reserved_label_names():
            msg = (
                f"Label '{normalized}' is reserved as an issue type name; "
                "set the issue type explicitly instead."
            )
            raise ValueError(msg)
        return normalized

    # -- Issue CRUD ----------------------------------------------------------

    def create_issue(
        self,
        title: str,
        *,
        type: str = "task",
        priority: int = 2,
        parent_id: str | None = None,
        assignee: str = "",
        description: str = "",
        notes: str = "",
        fields: dict[str, Any] | None = None,
        labels: list[str] | None = None,
        deps: list[str] | None = None,
        actor: str = "",
    ) -> Issue:
        if not title or not title.strip():
            msg = "Title cannot be empty"
            raise ValueError(msg)
        if not (0 <= priority <= 4):
            msg = f"Priority must be between 0 and 4, got {priority}"
            raise ValueError(msg)
        if fields:
            for k in fields:
                if not k or not k.strip():
                    msg = "Field key cannot be empty"
                    raise ValueError(msg)
        if labels:
            labels = [self._validate_label_name(label) for label in labels]
        # Reject unknown types — don't silently fall back
        if self.templates.get_type(type) is None:
            valid_types = [t.type for t in self.templates.list_types()]
            msg = f"Unknown type '{type}'. Valid types: {', '.join(valid_types)}"
            raise ValueError(msg)

        self._validate_parent_id(parent_id)

        # Validate deps BEFORE any writes to prevent partial commits
        if deps:
            dep_ph = ",".join("?" * len(deps))
            found = {r["id"] for r in self.conn.execute(f"SELECT id FROM issues WHERE id IN ({dep_ph})", deps).fetchall()}
            missing = [d for d in deps if d not in found]
            if missing:
                msg = f"Invalid dependency IDs (not found): {', '.join(missing)}"
                raise ValueError(msg)

        issue_id = self._generate_id()
        now = _now_iso()
        fields = fields or {}

        # Determine initial state from template
        initial_state = self.templates.get_initial_state(type)

        try:
            self.conn.execute(
                "INSERT INTO issues (id, title, status, priority, type, parent_id, assignee, "
                "created_at, updated_at, description, notes, fields) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    issue_id,
                    title,
                    initial_state,
                    priority,
                    type,
                    parent_id,
                    assignee,
                    now,
                    now,
                    description,
                    notes,
                    json.dumps(fields),
                ),
            )

            self._record_event(issue_id, "created", actor=actor, new_value=title)

            if labels:
                for label in labels:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO labels (issue_id, label) VALUES (?, ?)",
                        (issue_id, label),
                    )

            if deps:
                for dep_id in deps:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, 'blocks', ?)",
                        (issue_id, dep_id, now),
                    )

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return self.get_issue(issue_id)

    def get_issue(self, issue_id: str) -> Issue:
        row = self.conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        if row is None:
            msg = f"Issue not found: {issue_id}"
            raise KeyError(msg)
        return self._build_issue(issue_id)

    def _build_issue(self, issue_id: str) -> Issue:
        """Build a single Issue with all computed fields. Internal — caller must validate existence."""
        issues = self._build_issues_batch([issue_id])
        if not issues:
            msg = f"Issue not found: {issue_id}"
            raise KeyError(msg)
        return issues[0]

    def _build_issues_batch(self, issue_ids: list[str]) -> list[Issue]:
        """Build multiple Issues efficiently with batched queries (eliminates N+1)."""
        if not issue_ids:
            return []

        placeholders = ",".join("?" * len(issue_ids))

        # 1. Fetch all issue rows
        rows_by_id: dict[str, sqlite3.Row] = {}
        for r in self.conn.execute(f"SELECT * FROM issues WHERE id IN ({placeholders})", issue_ids).fetchall():
            rows_by_id[r["id"]] = r

        # 2. Batch fetch labels
        labels_by_id: dict[str, list[str]] = {iid: [] for iid in issue_ids}
        for r in self.conn.execute(f"SELECT issue_id, label FROM labels WHERE issue_id IN ({placeholders})", issue_ids).fetchall():
            labels_by_id[r["issue_id"]].append(r["label"])

        # 3. Batch fetch "blocks" (issues that this one blocks — where depends_on_id = this)
        blocks_by_id: dict[str, list[str]] = {iid: [] for iid in issue_ids}
        for r in self.conn.execute(
            f"SELECT depends_on_id, issue_id FROM dependencies WHERE depends_on_id IN ({placeholders})",
            issue_ids,
        ).fetchall():
            blocks_by_id[r["depends_on_id"]].append(r["issue_id"])

        # 4. Batch fetch "blocked_by" — only open (non-done) blockers
        done_states = self._get_states_for_category("done") or ["closed"]
        done_ph = ",".join("?" * len(done_states))
        blocked_by_id: dict[str, list[str]] = {iid: [] for iid in issue_ids}
        for r in self.conn.execute(
            f"SELECT d.issue_id, d.depends_on_id FROM dependencies d "
            f"JOIN issues blocker ON d.depends_on_id = blocker.id "
            f"WHERE d.issue_id IN ({placeholders}) AND blocker.status NOT IN ({done_ph})",
            [*issue_ids, *done_states],
        ).fetchall():
            blocked_by_id[r["issue_id"]].append(r["depends_on_id"])

        # 5. Batch fetch children
        children_by_id: dict[str, list[str]] = {iid: [] for iid in issue_ids}
        for r in self.conn.execute(f"SELECT id, parent_id FROM issues WHERE parent_id IN ({placeholders})", issue_ids).fetchall():
            children_by_id[r["parent_id"]].append(r["id"])

        # 6. Batch compute open blocker counts (category-aware)
        open_blockers_by_id: dict[str, int] = dict.fromkeys(issue_ids, 0)
        done_states = self._get_states_for_category("done")
        if done_states:
            done_ph = ",".join("?" * len(done_states))
            for r in self.conn.execute(
                f"SELECT d.issue_id, COUNT(*) as cnt FROM dependencies d "
                f"JOIN issues i ON d.depends_on_id = i.id "
                f"WHERE d.issue_id IN ({placeholders}) AND i.status NOT IN ({done_ph}) "
                f"GROUP BY d.issue_id",
                [*issue_ids, *done_states],
            ).fetchall():
                open_blockers_by_id[r["issue_id"]] = r["cnt"]
        else:
            # No done-category states: every dependency is an active blocker
            for r in self.conn.execute(
                f"SELECT d.issue_id, COUNT(*) as cnt FROM dependencies d WHERE d.issue_id IN ({placeholders}) GROUP BY d.issue_id",
                issue_ids,
            ).fetchall():
                open_blockers_by_id[r["issue_id"]] = r["cnt"]

        # 7. Compute open states for is_ready check
        open_states_set = set(self._get_states_for_category("open")) or {"open"}

        # Build Issue objects preserving input order
        result: list[Issue] = []
        for iid in issue_ids:
            row = rows_by_id.get(iid)
            if row is None:
                continue
            result.append(
                Issue(
                    id=row["id"],
                    title=row["title"],
                    status=row["status"],
                    priority=row["priority"],
                    type=row["type"],
                    parent_id=row["parent_id"],
                    assignee=row["assignee"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    closed_at=row["closed_at"],
                    description=row["description"],
                    notes=row["notes"],
                    fields=json.loads(row["fields"]) if row["fields"] else {},
                    labels=labels_by_id.get(iid, []),
                    blocks=blocks_by_id.get(iid, []),
                    blocked_by=blocked_by_id.get(iid, []),
                    is_ready=(row["status"] in open_states_set and open_blockers_by_id.get(iid, 0) == 0),
                    children=children_by_id.get(iid, []),
                    status_category=self._resolve_status_category(row["type"], row["status"]),
                )
            )
        return result

    def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        assignee: str | None = None,
        description: str | None = None,
        notes: str | None = None,
        parent_id: str | None = None,
        fields: dict[str, Any] | None = None,
        actor: str = "",
        _skip_transition_check: bool = False,
    ) -> Issue:
        current = self.get_issue(issue_id)
        now = _now_iso()

        # --- Validate all inputs BEFORE any writes to prevent partial commits ---
        if priority is not None and priority != current.priority and not (0 <= priority <= 4):
            msg = f"Priority must be between 0 and 4, got {priority}"
            raise ValueError(msg)

        if parent_id is not None and parent_id != "":
            if parent_id == issue_id:
                msg = f"Issue {issue_id} cannot be its own parent"
                raise ValueError(msg)
            self._validate_parent_id(parent_id)
            # Check for circular parent chain
            ancestor = parent_id
            while ancestor is not None:
                row = self.conn.execute("SELECT parent_id FROM issues WHERE id = ?", (ancestor,)).fetchone()
                if row is None:
                    break
                ancestor = row["parent_id"]
                if ancestor == issue_id:
                    msg = f"Setting parent_id to '{parent_id}' would create a circular parent chain"
                    raise ValueError(msg)

        # Cache transition validation result for reuse in write phase (warnings)
        _transition_result = None
        if status is not None and status != current.status:
            self._validate_status(status, current.type)

            if not _skip_transition_check:
                # WFT-FR-069: Atomic transition-with-fields
                merged_fields = {**current.fields}
                if fields is not None:
                    merged_fields.update(fields)

                tpl = self.templates.get_type(current.type)
                if tpl is not None:
                    _transition_result = self.templates.validate_transition(current.type, current.status, status, merged_fields)
                    if not _transition_result.allowed:
                        if _transition_result.missing_fields:
                            missing_str = ", ".join(_transition_result.missing_fields)
                            msg = (
                                f"Cannot transition '{current.status}' -> '{status}' for type "
                                f"'{current.type}': missing required fields: {missing_str}"
                            )
                        else:
                            msg = (
                                f"Transition '{current.status}' -> '{status}' is not allowed for type "
                                f"'{current.type}'. Use get_valid_transitions() to see allowed transitions."
                            )
                        raise ValueError(msg)

        # --- All validation passed — now record events and apply changes ---
        updates: list[str] = []
        params: list[Any] = []

        try:
            if title is not None and title != current.title:
                self._record_event(issue_id, "title_changed", actor=actor, old_value=current.title, new_value=title)
                updates.append("title = ?")
                params.append(title)

            if status is not None and status != current.status:
                # Record soft-enforcement warnings from cached validation result
                if _transition_result is not None:
                    if _transition_result.warnings:
                        for warning in _transition_result.warnings:
                            self._record_event(
                                issue_id,
                                "transition_warning",
                                actor=actor,
                                old_value=current.status,
                                new_value=status,
                                comment=warning,
                            )
                    if _transition_result.missing_fields and _transition_result.enforcement == "soft":
                        self._record_event(
                            issue_id,
                            "transition_warning",
                            actor=actor,
                            old_value=current.status,
                            new_value=status,
                            comment=f"Missing recommended fields: {', '.join(_transition_result.missing_fields)}",
                        )

                self._record_event(issue_id, "status_changed", actor=actor, old_value=current.status, new_value=status)
                updates.append("status = ?")
                params.append(status)

                # Set closed_at when entering a done-category state
                status_cat = self.templates.get_category(current.type, status)
                is_done = (status_cat or self._infer_status_category(status)) == "done"

                if is_done:
                    updates.append("closed_at = ?")
                    params.append(now)
                else:
                    # Clear closed_at when leaving a done-category state
                    old_cat = self.templates.get_category(current.type, current.status)
                    if (old_cat or self._infer_status_category(current.status)) == "done":
                        updates.append("closed_at = NULL")

            if priority is not None and priority != current.priority:
                self._record_event(
                    issue_id,
                    "priority_changed",
                    actor=actor,
                    old_value=str(current.priority),
                    new_value=str(priority),
                )
                updates.append("priority = ?")
                params.append(priority)

            if assignee is not None and assignee != current.assignee:
                self._record_event(issue_id, "assignee_changed", actor=actor, old_value=current.assignee, new_value=assignee)
                updates.append("assignee = ?")
                params.append(assignee)

            if description is not None and description != current.description:
                self._record_event(
                    issue_id,
                    "description_changed",
                    actor=actor,
                    old_value=current.description,
                    new_value=description,
                )
                updates.append("description = ?")
                params.append(description)

            if notes is not None and notes != current.notes:
                self._record_event(
                    issue_id,
                    "notes_changed",
                    actor=actor,
                    old_value=current.notes,
                    new_value=notes,
                )
                updates.append("notes = ?")
                params.append(notes)

            if parent_id is not None:
                if parent_id == "":
                    # Clear parent
                    if current.parent_id is not None:
                        self._record_event(
                            issue_id,
                            "parent_changed",
                            actor=actor,
                            old_value=current.parent_id or "",
                            new_value="",
                        )
                        updates.append("parent_id = NULL")
                else:
                    if parent_id != current.parent_id:
                        self._record_event(
                            issue_id,
                            "parent_changed",
                            actor=actor,
                            old_value=current.parent_id or "",
                            new_value=parent_id,
                        )
                        updates.append("parent_id = ?")
                        params.append(parent_id)

            if fields is not None:
                # Merge into existing fields
                merged = {**current.fields, **fields}
                updates.append("fields = ?")
                params.append(json.dumps(merged))

            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.append(issue_id)
                sql = f"UPDATE issues SET {', '.join(updates)} WHERE id = ?"
                self.conn.execute(sql, params)
                self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return self.get_issue(issue_id)

    def close_issue(
        self,
        issue_id: str,
        *,
        reason: str = "",
        actor: str = "",
        status: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> Issue:
        if fields is not None and not isinstance(fields, dict):
            msg = "fields must be a dict"
            raise TypeError(msg)

        current = self.get_issue(issue_id)

        # Determine done state via template system
        cat: str | None = self.templates.get_category(current.type, current.status)
        if cat is None:
            cat = self._infer_status_category(current.status)
        if cat == "done":
            msg = f"Issue {issue_id} is already closed (status: '{current.status}', closed_at: {current.closed_at})"
            raise ValueError(msg)

        if status is not None:
            # Validate that the requested status is a done-category state
            target_category = self.templates.get_category(current.type, status)
            if target_category != "done":
                msg = f"Cannot close with status '{status}': it is not a done-category state for type '{current.type}'."
                raise ValueError(msg)
            done_status = status
        else:
            # Default to first done-category state
            _first_done = self.templates.get_first_state_of_category(current.type, "done")
            done_status = _first_done if _first_done is not None else "closed"

        # Enforce hard gates even though close_issue skips transition graph
        # validation. If a defined transition from current→done has hard
        # enforcement, the required fields must be satisfied.
        merged_fields = {**current.fields}
        if fields:
            merged_fields.update(fields)
        if reason:
            merged_fields["close_reason"] = reason
        result = self.templates.validate_transition(current.type, current.status, done_status, merged_fields)
        if not result.allowed and result.enforcement == "hard":
            missing_str = ", ".join(result.missing_fields)
            msg = f"Cannot close issue {issue_id}: hard-enforcement gate requires fields: {missing_str}"
            raise ValueError(msg)

        # Merge close_reason into fields for the update call
        update_fields: dict[str, Any] = {}
        if fields:
            update_fields.update(fields)
        if reason:
            update_fields["close_reason"] = reason

        return self.update_issue(
            issue_id,
            status=done_status,
            fields=update_fields or None,
            actor=actor,
            _skip_transition_check=True,
        )

    def reopen_issue(self, issue_id: str, *, actor: str = "") -> Issue:
        """Reopen a closed issue, returning it to its type's initial state.

        Clears closed_at. Only works on issues in done-category states.
        """
        current = self.get_issue(issue_id)
        cat: str | None = self.templates.get_category(current.type, current.status)
        if cat is None:
            cat = self._infer_status_category(current.status)
        if cat != "done":
            msg = f"Cannot reopen {issue_id}: status '{current.status}' is not in a done-category state"
            raise ValueError(msg)

        initial_state = self.templates.get_initial_state(current.type)
        try:
            self._record_event(issue_id, "reopened", actor=actor, old_value=current.status, new_value=initial_state)
            return self.update_issue(issue_id, status=initial_state, actor=actor, _skip_transition_check=True)
        except Exception:
            self.conn.rollback()
            raise

    def claim_issue(self, issue_id: str, *, assignee: str, actor: str = "") -> Issue:
        """Atomically claim an open-category issue with optimistic locking.

        Sets assignee only — does NOT change status. Agent uses update_issue
        to advance through the workflow after claiming.

        Uses a single atomic UPDATE with WHERE guard to prevent race conditions
        where two agents try to claim the same issue concurrently.
        """
        if not assignee or not assignee.strip():
            msg = "Assignee cannot be empty"
            raise ValueError(msg)
        # Look up the issue type so we know which states are "open"
        row = self.conn.execute("SELECT type FROM issues WHERE id = ?", (issue_id,)).fetchone()
        if row is None:
            msg = f"Issue not found: {issue_id}"
            raise KeyError(msg)
        issue_type = row["type"]

        # Get all open-category states for this type
        open_states: list[str] = []
        tpl = self.templates.get_type(issue_type)
        if tpl is not None:
            open_states = [s.name for s in tpl.states if s.category == "open"]
        if not open_states:
            open_states = ["open"]

        # Atomic UPDATE: only succeeds if issue is unassigned OR already owned by this agent
        status_ph = ",".join("?" * len(open_states))
        try:
            cursor = self.conn.execute(
                f"UPDATE issues SET assignee = ?, updated_at = ? "
                f"WHERE id = ? AND status IN ({status_ph}) "
                f"AND (assignee = '' OR assignee IS NULL OR assignee = ?)",
                [assignee, _now_iso(), issue_id, *open_states, assignee],
            )

            if cursor.rowcount == 0:
                # Figure out why it failed: wrong status or already claimed?
                current = self.conn.execute("SELECT status, assignee FROM issues WHERE id = ?", (issue_id,)).fetchone()
                if current is None:
                    msg = f"Issue not found: {issue_id}"
                    raise KeyError(msg)
                if current["assignee"] and current["assignee"] != assignee:
                    msg = f"Cannot claim {issue_id}: already assigned to '{current['assignee']}'"
                    raise ValueError(msg)
                msg = f"Cannot claim {issue_id}: status is '{current['status']}', expected open-category state"
                raise ValueError(msg)

            self._record_event(issue_id, "claimed", actor=actor, new_value=assignee)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_issue(issue_id)

    def release_claim(self, issue_id: str, *, actor: str = "") -> Issue:
        """Release a claimed issue by clearing its assignee.

        Does NOT change status. Only succeeds if issue has an assignee.
        """
        current = self.get_issue(issue_id)

        if not current.assignee:
            msg = f"Cannot release {issue_id}: no assignee set"
            raise ValueError(msg)

        try:
            self.conn.execute(
                "UPDATE issues SET assignee = '', updated_at = ? WHERE id = ?",
                [_now_iso(), issue_id],
            )

            self._record_event(issue_id, "released", actor=actor, old_value=current.assignee)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_issue(issue_id)

    def claim_next(
        self,
        assignee: str,
        *,
        type_filter: str | None = None,
        priority_min: int | None = None,
        priority_max: int | None = None,
        actor: str = "",
    ) -> Issue | None:
        """Claim the highest-priority ready issue matching filters.

        Iterates ready issues sorted by priority and attempts claim_issue()
        on each until one succeeds (handles race conditions with retry).
        Returns None if no matching ready issues exist.
        """
        if not assignee or not assignee.strip():
            msg = "Assignee cannot be empty"
            raise ValueError(msg)
        ready = self.get_ready()

        for issue in ready:
            if type_filter is not None and issue.type != type_filter:
                continue
            if priority_min is not None and issue.priority < priority_min:
                continue
            if priority_max is not None and issue.priority > priority_max:
                continue
            try:
                return self.claim_issue(issue.id, assignee=assignee, actor=actor or assignee)
            except ValueError:
                continue  # Race condition: someone else claimed it
        return None

    def batch_close(
        self,
        issue_ids: list[str],
        *,
        reason: str = "",
        actor: str = "",
    ) -> tuple[list[Issue], list[dict[str, str]]]:
        """Close multiple issues with per-item error handling. Returns (closed, errors)."""
        if not isinstance(issue_ids, list) or not all(isinstance(i, str) for i in issue_ids):
            msg = "issue_ids must be a list of strings"
            raise TypeError(msg)
        results: list[Issue] = []
        errors: list[dict[str, str]] = []
        for issue_id in issue_ids:
            try:
                results.append(self.close_issue(issue_id, reason=reason, actor=actor))
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}"})
            except ValueError as e:
                errors.append({"id": issue_id, "error": str(e)})
        return results, errors

    def batch_update(
        self,
        issue_ids: list[str],
        *,
        status: str | None = None,
        priority: int | None = None,
        assignee: str | None = None,
        fields: dict[str, Any] | None = None,
        actor: str = "",
    ) -> tuple[list[Issue], list[dict[str, str]]]:
        """Update multiple issues with the same changes. Returns (updated, errors)."""
        if not isinstance(issue_ids, list) or not all(isinstance(i, str) for i in issue_ids):
            msg = "issue_ids must be a list of strings"
            raise TypeError(msg)
        results: list[Issue] = []
        errors: list[dict[str, str]] = []
        for issue_id in issue_ids:
            try:
                results.append(
                    self.update_issue(
                        issue_id,
                        status=status,
                        priority=priority,
                        assignee=assignee,
                        fields=fields,
                        actor=actor,
                    )
                )
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}"})
            except ValueError as e:
                errors.append({"id": issue_id, "error": str(e)})
        return results, errors

    def batch_add_label(
        self,
        issue_ids: list[str],
        *,
        label: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Add the same label to multiple issues. Returns (labeled, errors)."""
        if not isinstance(issue_ids, list) or not all(isinstance(i, str) for i in issue_ids):
            msg = "issue_ids must be a list of strings"
            raise TypeError(msg)
        if not isinstance(label, str):
            msg = "label must be a string"
            raise TypeError(msg)

        results: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        for issue_id in issue_ids:
            try:
                self.get_issue(issue_id)
                added = self.add_label(issue_id, label)
                results.append({"id": issue_id, "status": "added" if added else "already_exists"})
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}", "code": "not_found"})
            except ValueError as e:
                errors.append({"id": issue_id, "error": str(e), "code": "validation_error"})
        return results, errors

    def batch_add_comment(
        self,
        issue_ids: list[str],
        *,
        text: str,
        author: str = "",
    ) -> tuple[list[dict[str, str | int]], list[dict[str, str]]]:
        """Add the same comment to multiple issues. Returns (commented, errors)."""
        if not isinstance(issue_ids, list) or not all(isinstance(i, str) for i in issue_ids):
            msg = "issue_ids must be a list of strings"
            raise TypeError(msg)
        if not isinstance(text, str):
            msg = "text must be a string"
            raise TypeError(msg)
        if not isinstance(author, str):
            msg = "author must be a string"
            raise TypeError(msg)

        results: list[dict[str, str | int]] = []
        errors: list[dict[str, str]] = []
        for issue_id in issue_ids:
            try:
                self.get_issue(issue_id)
                comment_id = self.add_comment(issue_id, text, author=author)
                results.append({"id": issue_id, "comment_id": comment_id})
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}", "code": "not_found"})
            except ValueError as e:
                errors.append({"id": issue_id, "error": str(e), "code": "validation_error"})
        return results, errors

    def list_issues(
        self,
        *,
        status: str | None = None,
        type: str | None = None,
        priority: int | None = None,
        parent_id: str | None = None,
        assignee: str | None = None,
        label: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Issue]:
        if limit < 0:
            limit = 100
        if offset < 0:
            offset = 0
        conditions: list[str] = []
        params: list[Any] = []

        if status is not None:
            # Check if status is a category name (with aliases)
            category_aliases = {"in_progress": "wip", "closed": "done"}
            category_key = category_aliases.get(status, status)
            category_states: list[str] = []
            if category_key in ("open", "wip", "done"):
                category_states = self._get_states_for_category(category_key)

            if category_states:
                placeholders = ",".join("?" * len(category_states))
                conditions.append(f"status IN ({placeholders})")
                params.extend(category_states)
            else:
                # Literal state match (either not a category, or W7 empty guard)
                conditions.append("status = ?")
                params.append(status)
        if type is not None:
            conditions.append("type = ?")
            params.append(type)
        if priority is not None:
            conditions.append("priority = ?")
            params.append(priority)
        if parent_id is not None:
            conditions.append("parent_id = ?")
            params.append(parent_id)
        if assignee is not None:
            conditions.append("assignee = ?")
            params.append(assignee)
        if label is not None:
            conditions.append("id IN (SELECT issue_id FROM labels WHERE label = ?)")
            params.append(label)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        rows = self.conn.execute(
            f"SELECT id FROM issues{where} ORDER BY priority, created_at LIMIT ? OFFSET ?",
            params,
        ).fetchall()

        return self._build_issues_batch([r["id"] for r in rows])

    def search_issues(self, query: str, *, limit: int = 100, offset: int = 0) -> list[Issue]:
        # Try FTS5 first, fall back to LIKE if FTS table doesn't exist
        try:
            # Sanitize: strip non-alphanumeric chars except * (prefix) and " (phrase)
            sanitized = _re.sub(r'[^\w\s*"]', "", query)
            # Quote each token and add * for prefix matching, then join with AND
            # Strip double quotes from tokens to prevent FTS5 syntax injection
            tokens = [t.replace('"', "") for t in sanitized.strip().split()]
            tokens = [t for t in tokens if t]  # drop empty tokens after stripping
            fts_query = " AND ".join(f'"{t}"*' for t in tokens) if tokens else '""'
            rows = self.conn.execute(
                "SELECT i.id FROM issues i "
                "JOIN issues_fts ON issues_fts.rowid = i.rowid "
                "WHERE issues_fts MATCH ? "
                "ORDER BY issues_fts.rank LIMIT ? OFFSET ?",
                (fts_query, limit, offset),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 not available — fall back to LIKE
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            rows = self.conn.execute(
                "SELECT id FROM issues WHERE title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
                "ORDER BY priority, created_at LIMIT ? OFFSET ?",
                (pattern, pattern, limit, offset),
            ).fetchall()
        return self._build_issues_batch([r["id"] for r in rows])

    # -- Template-aware queries ----------------------------------------------

    def get_valid_transitions(self, issue_id: str) -> list[TransitionOption]:
        """Return valid next states for an issue with readiness info.

        Delegates to TemplateRegistry.get_valid_transitions() with the issue's
        current state and fields. Returns an empty list for unknown types.
        """
        issue = self.get_issue(issue_id)
        return self.templates.get_valid_transitions(issue.type, issue.status, issue.fields)

    def validate_issue(self, issue_id: str) -> ValidationResult:
        """Validate an issue against its template.

        Checks whether all fields required at the current state are populated.
        Also checks fields needed for next reachable transitions (upcoming requirements).
        Returns a ValidationResult with warnings for missing recommended fields.
        Unknown types validate as valid (no template to check against).
        """
        from filigree.templates import ValidationResult

        issue = self.get_issue(issue_id)
        tpl = self.templates.get_type(issue.type)
        if tpl is None:
            return ValidationResult(valid=True, warnings=(), errors=())

        warnings: list[str] = []

        # Check required_at fields for current state
        missing = self.templates.validate_fields_for_state(issue.type, issue.status, issue.fields)
        for field_name in missing:
            warnings.append(f"Field '{field_name}' is recommended at state '{issue.status}' for type '{issue.type}' but is not populated.")

        # Check upcoming requirements: fields needed for next transitions
        transitions = self.templates.get_valid_transitions(issue.type, issue.status, issue.fields)
        for t in transitions:
            if t.missing_fields:
                fields_str = ", ".join(t.missing_fields)
                warnings.append(f"Transition to '{t.to}' requires: {fields_str}")

        return ValidationResult(valid=True, warnings=tuple(warnings), errors=())

    # -- Dependencies --------------------------------------------------------

    def add_dependency(self, issue_id: str, depends_on_id: str, *, dep_type: str = "blocks", actor: str = "") -> bool:
        # Validate both issues exist
        self.get_issue(issue_id)  # raises KeyError if not found
        self.get_issue(depends_on_id)  # raises KeyError if not found

        if issue_id == depends_on_id:
            msg = f"Cannot add self-dependency: {issue_id}"
            raise ValueError(msg)

        # Check for cycles: would depends_on_id transitively reach issue_id?
        if self._would_create_cycle(issue_id, depends_on_id):
            msg = f"Dependency {issue_id} -> {depends_on_id} would create a cycle"
            raise ValueError(msg)

        now = _now_iso()
        try:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, ?, ?)",
                (issue_id, depends_on_id, dep_type, now),
            )
            if cursor.rowcount == 0:
                return False  # Already exists — no-op, no event
            self._record_event(issue_id, "dependency_added", actor=actor, new_value=f"{dep_type}:{depends_on_id}")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return True

    def _would_create_cycle(self, issue_id: str, depends_on_id: str) -> bool:
        """Check if adding issue_id -> depends_on_id would create a cycle.

        Uses BFS from depends_on_id following existing dependency edges.
        If issue_id is reachable, adding the new edge would close a cycle.
        """
        visited: set[str] = set()
        queue = [depends_on_id]
        while queue:
            current = queue.pop(0)
            if current == issue_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            # Follow existing dependencies: current depends_on X means current -> X
            for r in self.conn.execute("SELECT depends_on_id FROM dependencies WHERE issue_id = ?", (current,)).fetchall():
                queue.append(r["depends_on_id"])
        return False

    def remove_dependency(self, issue_id: str, depends_on_id: str, *, actor: str = "") -> bool:
        cursor = self.conn.execute(
            "DELETE FROM dependencies WHERE issue_id = ? AND depends_on_id = ?",
            (issue_id, depends_on_id),
        )
        if cursor.rowcount == 0:
            return False  # Nothing to remove
        self._record_event(issue_id, "dependency_removed", actor=actor, old_value=depends_on_id)
        self.conn.commit()
        return True

    def get_all_dependencies(self) -> list[dict[str, str]]:
        rows = self.conn.execute("SELECT issue_id, depends_on_id, type FROM dependencies").fetchall()
        return [{"from": r["issue_id"], "to": r["depends_on_id"], "type": r["type"]} for r in rows]

    # -- Ready / Blocked -----------------------------------------------------

    def get_ready(self) -> list[Issue]:
        """Issues in open-category states with no open blockers."""
        open_states = self._get_states_for_category("open")
        done_states = self._get_states_for_category("done")

        if not open_states:
            return []

        open_ph = ",".join("?" * len(open_states))
        if done_states:
            done_ph = ",".join("?" * len(done_states))
            rows = self.conn.execute(
                f"SELECT i.id FROM issues i "
                f"WHERE i.status IN ({open_ph}) "
                f"AND NOT EXISTS ("
                f"  SELECT 1 FROM dependencies d "
                f"  JOIN issues blocker ON d.depends_on_id = blocker.id "
                f"  WHERE d.issue_id = i.id AND blocker.status NOT IN ({done_ph})"
                f") ORDER BY i.priority, i.created_at",
                [*open_states, *done_states],
            ).fetchall()
        else:
            # No done states configured means every dependency is an open blocker.
            rows = self.conn.execute(
                f"SELECT i.id FROM issues i "
                f"WHERE i.status IN ({open_ph}) "
                f"AND NOT EXISTS ("
                f"  SELECT 1 FROM dependencies d "
                f"  WHERE d.issue_id = i.id"
                f") ORDER BY i.priority, i.created_at",
                open_states,
            ).fetchall()

        return self._build_issues_batch([r["id"] for r in rows])

    def get_blocked(self) -> list[Issue]:
        """Issues in open-category states that have at least one non-done blocker."""
        open_states = self._get_states_for_category("open")
        done_states = self._get_states_for_category("done")

        if not open_states:
            return []

        open_ph = ",".join("?" * len(open_states))
        if done_states:
            done_ph = ",".join("?" * len(done_states))
            rows = self.conn.execute(
                f"SELECT DISTINCT i.id FROM issues i "
                f"JOIN dependencies d ON d.issue_id = i.id "
                f"JOIN issues blocker ON d.depends_on_id = blocker.id "
                f"WHERE i.status IN ({open_ph}) AND blocker.status NOT IN ({done_ph}) "
                f"ORDER BY i.priority, i.created_at",
                [*open_states, *done_states],
            ).fetchall()
        else:
            # No done states defined — all blockers count
            rows = self.conn.execute(
                f"SELECT DISTINCT i.id FROM issues i "
                f"JOIN dependencies d ON d.issue_id = i.id "
                f"JOIN issues blocker ON d.depends_on_id = blocker.id "
                f"WHERE i.status IN ({open_ph}) "
                f"ORDER BY i.priority, i.created_at",
                open_states,
            ).fetchall()

        return self._build_issues_batch([r["id"] for r in rows])

    # -- Critical path -------------------------------------------------------

    def get_critical_path(self) -> list[dict[str, Any]]:
        """Compute the longest dependency chain among non-done issues.

        Uses topological-order dynamic programming on the open-issue dependency DAG.
        Returns the chain as a list of {id, title, priority, type} dicts, ordered
        from the root blocker to the final blocked issue.
        """
        done_states = self._get_states_for_category("done")

        done_ph = ",".join("?" * len(done_states)) if done_states else "'__none__'"
        open_rows = self.conn.execute(
            f"SELECT id, title, priority, type FROM issues WHERE status NOT IN ({done_ph})",
            done_states if done_states else [],
        ).fetchall()
        open_ids = {r["id"] for r in open_rows}
        info = {r["id"]: {"id": r["id"], "title": r["title"], "priority": r["priority"], "type": r["type"]} for r in open_rows}

        # edges: blocker -> list of issues it blocks (forward edges)
        forward: dict[str, list[str]] = {nid: [] for nid in open_ids}
        in_degree: dict[str, int] = dict.fromkeys(open_ids, 0)
        dep_rows = self.conn.execute("SELECT issue_id, depends_on_id FROM dependencies").fetchall()
        for dep in dep_rows:
            from_id, to_id = dep["issue_id"], dep["depends_on_id"]
            if from_id in open_ids and to_id in open_ids:
                forward[to_id].append(from_id)  # to_id blocks from_id
                in_degree[from_id] = in_degree.get(from_id, 0) + 1

        if not open_ids:
            return []

        # Topological sort (Kahn's algorithm) + longest path DP
        queue = [nid for nid in open_ids if in_degree[nid] == 0]
        dist: dict[str, int] = dict.fromkeys(open_ids, 0)
        pred: dict[str, str | None] = dict.fromkeys(open_ids, None)

        while queue:
            node = queue.pop(0)
            for neighbor in forward[node]:
                if dist[node] + 1 > dist[neighbor]:
                    dist[neighbor] = dist[node] + 1
                    pred[neighbor] = node
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if not dist:
            return []

        # Find the node with the longest path
        end_node = max(dist, key=lambda n: dist[n])
        if dist[end_node] == 0:
            return []  # No chains at all

        # Reconstruct path
        path: list[str] = []
        current: str | None = end_node
        while current is not None:
            path.append(current)
            current = pred[current]
        path.reverse()

        return [info[nid] for nid in path]

    # -- Plan tree -----------------------------------------------------------

    def get_plan(self, milestone_id: str) -> dict[str, Any]:
        """Get milestone->phase->step tree with progress stats."""
        milestone = self.get_issue(milestone_id)

        phases = self.list_issues(parent_id=milestone_id)
        phases.sort(key=lambda p: p.fields.get("sequence", 999))

        result: dict[str, Any] = {
            "milestone": milestone.to_dict(),
            "phases": [],
            "total_steps": 0,
            "completed_steps": 0,
        }

        for phase in phases:
            steps = self.list_issues(parent_id=phase.id)
            steps.sort(key=lambda s: s.fields.get("sequence", 999))

            completed = sum(1 for s in steps if s.status_category == "done")
            ready = sum(1 for s in steps if s.is_ready)

            result["phases"].append(
                {
                    "phase": phase.to_dict(),
                    "steps": [s.to_dict() for s in steps],
                    "total": len(steps),
                    "completed": completed,
                    "ready": ready,
                }
            )
            result["total_steps"] += len(steps)
            result["completed_steps"] += completed

        return result

    def create_plan(
        self,
        milestone: dict[str, Any],
        phases: list[dict[str, Any]],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        """Create a full milestone → phase → step hierarchy in one transaction.

        Args:
            milestone: {title, priority?, description?, fields?}
            phases: [{title, priority?, description?, steps: [{title, priority?, description?, deps?: [step_index]}]}]
            actor: Who created the plan

        Step deps use integer indices (0-based within the phase's steps list)
        or cross-phase references as "phase_idx.step_idx" strings.

        Returns the full plan tree (same format as get_plan).
        """
        # Validate inputs — specific error messages for each level
        if not milestone.get("title", "").strip():
            msg = "Milestone 'title' is required and cannot be empty"
            raise ValueError(msg)
        for phase_idx, phase_data in enumerate(phases):
            if not phase_data.get("title", "").strip():
                msg = f"Phase {phase_idx + 1} 'title' is required and cannot be empty"
                raise ValueError(msg)
            for step_idx, step_data in enumerate(phase_data.get("steps", [])):
                if not step_data.get("title", "").strip():
                    msg = f"Phase {phase_idx + 1}, Step {step_idx + 1} 'title' is required and cannot be empty"
                    raise ValueError(msg)

        now = _now_iso()
        milestone_initial = self.templates.get_initial_state("milestone")
        phase_initial = self.templates.get_initial_state("phase")
        step_initial = self.templates.get_initial_state("step")

        try:
            # Create milestone
            ms_id = self._generate_id()
            ms_fields = milestone.get("fields") or {}
            self.conn.execute(
                "INSERT INTO issues (id, title, status, priority, type, parent_id, assignee, "
                "created_at, updated_at, description, notes, fields) "
                "VALUES (?, ?, ?, ?, 'milestone', NULL, '', ?, ?, ?, '', ?)",
                (
                    ms_id,
                    milestone["title"],
                    milestone_initial,
                    milestone.get("priority", 2),
                    now,
                    now,
                    milestone.get("description", ""),
                    json.dumps(ms_fields),
                ),
            )
            self._record_event(ms_id, "created", actor=actor, new_value=milestone["title"])

            # Track all created step IDs for cross-phase dependency resolution
            # step_ids[phase_idx][step_idx] = issue_id
            step_ids: list[list[str]] = []

            for phase_idx, phase_data in enumerate(phases):
                # Create phase
                phase_id = self._generate_id()
                phase_fields = phase_data.get("fields") or {}
                phase_fields["sequence"] = phase_idx + 1
                self.conn.execute(
                    "INSERT INTO issues (id, title, status, priority, type, parent_id, assignee, "
                    "created_at, updated_at, description, notes, fields) "
                    "VALUES (?, ?, ?, ?, 'phase', ?, '', ?, ?, ?, '', ?)",
                    (
                        phase_id,
                        phase_data["title"],
                        phase_initial,
                        phase_data.get("priority", 2),
                        ms_id,
                        now,
                        now,
                        phase_data.get("description", ""),
                        json.dumps(phase_fields),
                    ),
                )
                self._record_event(phase_id, "created", actor=actor, new_value=phase_data["title"])

                # Create steps
                phase_step_ids: list[str] = []
                steps = phase_data.get("steps") or []
                for step_idx, step_data in enumerate(steps):
                    step_id = self._generate_id()
                    step_fields = step_data.get("fields") or {}
                    step_fields["sequence"] = step_idx + 1
                    self.conn.execute(
                        "INSERT INTO issues (id, title, status, priority, type, parent_id, assignee, "
                        "created_at, updated_at, description, notes, fields) "
                        "VALUES (?, ?, ?, ?, 'step', ?, '', ?, ?, ?, '', ?)",
                        (
                            step_id,
                            step_data["title"],
                            step_initial,
                            step_data.get("priority", 2),
                            phase_id,
                            now,
                            now,
                            step_data.get("description", ""),
                            json.dumps(step_fields),
                        ),
                    )
                    self._record_event(step_id, "created", actor=actor, new_value=step_data["title"])
                    phase_step_ids.append(step_id)
                step_ids.append(phase_step_ids)

            # Wire up dependencies after all steps exist
            for phase_idx, phase_data in enumerate(phases):
                steps = phase_data.get("steps") or []
                for step_idx, step_data in enumerate(steps):
                    for dep_ref in step_data.get("deps", []):
                        dep_ref_str = str(dep_ref)
                        if "." in dep_ref_str:
                            # Cross-phase: "phase_idx.step_idx"
                            p_idx_str, s_idx_str = dep_ref_str.split(".", 1)
                            p_idx_int, s_idx_int = int(p_idx_str), int(s_idx_str)
                            if p_idx_int < 0 or s_idx_int < 0:
                                msg = f"Negative dep index not allowed: {dep_ref_str}"
                                raise ValueError(msg)
                            dep_issue_id = step_ids[p_idx_int][s_idx_int]
                        else:
                            # Same phase: step index
                            same_idx = int(dep_ref_str)
                            if same_idx < 0:
                                msg = f"Negative dep index not allowed: {dep_ref_str}"
                                raise ValueError(msg)
                            dep_issue_id = step_ids[phase_idx][same_idx]

                        issue_id = step_ids[phase_idx][step_idx]
                        if issue_id == dep_issue_id:
                            msg = f"Cannot add self-dependency: {issue_id}"
                            raise ValueError(msg)
                        if self._would_create_cycle(issue_id, dep_issue_id):
                            msg = f"Dependency {issue_id} -> {dep_issue_id} would create a cycle"
                            raise ValueError(msg)

                        self.conn.execute(
                            "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, 'blocks', ?)",
                            (issue_id, dep_issue_id, now),
                        )
                        self._record_event(issue_id, "dependency_added", actor=actor, new_value=f"blocks:{dep_issue_id}")

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return self.get_plan(ms_id)

    # -- Comments ------------------------------------------------------------

    def add_comment(self, issue_id: str, text: str, *, author: str = "") -> int:
        if not text or not text.strip():
            msg = "Comment text cannot be empty"
            raise ValueError(msg)
        now = _now_iso()
        cursor = self.conn.execute(
            "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
            (issue_id, author, text, now),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_comments(self, issue_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, author, text, created_at FROM comments WHERE issue_id = ? ORDER BY created_at",
            (issue_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Labels --------------------------------------------------------------

    def add_label(self, issue_id: str, label: str) -> bool:
        normalized = self._validate_label_name(label)
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO labels (issue_id, label) VALUES (?, ?)",
            (issue_id, normalized),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def remove_label(self, issue_id: str, label: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM labels WHERE issue_id = ? AND label = ?",
            (issue_id, label),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # -- Stats ---------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        by_status = {}
        for row in self.conn.execute("SELECT status, COUNT(*) as cnt FROM issues GROUP BY status").fetchall():
            by_status[row["status"]] = row["cnt"]

        by_type = {}
        for row in self.conn.execute("SELECT type, COUNT(*) as cnt FROM issues GROUP BY type").fetchall():
            by_type[row["type"]] = row["cnt"]

        open_states = self._get_states_for_category("open")
        done_states = self._get_states_for_category("done")
        if not open_states:
            ready_count = 0
            blocked_count = 0
        else:
            open_ph = ",".join("?" * len(open_states))
            if done_states:
                done_ph = ",".join("?" * len(done_states))
                ready_count = self.conn.execute(
                    f"SELECT COUNT(*) as cnt FROM issues i "
                    f"WHERE i.status IN ({open_ph}) "
                    f"AND NOT EXISTS ("
                    f"  SELECT 1 FROM dependencies d "
                    f"  JOIN issues blocker ON d.depends_on_id = blocker.id "
                    f"  WHERE d.issue_id = i.id AND blocker.status NOT IN ({done_ph})"
                    f")",
                    [*open_states, *done_states],
                ).fetchone()["cnt"]
                blocked_count = self.conn.execute(
                    f"SELECT COUNT(DISTINCT i.id) as cnt FROM issues i "
                    f"JOIN dependencies d ON d.issue_id = i.id "
                    f"JOIN issues blocker ON d.depends_on_id = blocker.id "
                    f"WHERE i.status IN ({open_ph}) AND blocker.status NOT IN ({done_ph})",
                    [*open_states, *done_states],
                ).fetchone()["cnt"]
            else:
                # No done states configured — every dependency is an active blocker
                ready_count = self.conn.execute(
                    f"SELECT COUNT(*) as cnt FROM issues i "
                    f"WHERE i.status IN ({open_ph}) "
                    f"AND NOT EXISTS ("
                    f"  SELECT 1 FROM dependencies d "
                    f"  WHERE d.issue_id = i.id"
                    f")",
                    open_states,
                ).fetchone()["cnt"]
                blocked_count = self.conn.execute(
                    f"SELECT COUNT(DISTINCT i.id) as cnt FROM issues i "
                    f"JOIN dependencies d ON d.issue_id = i.id "
                    f"WHERE i.status IN ({open_ph})",
                    open_states,
                ).fetchone()["cnt"]

        dep_count = self.conn.execute("SELECT COUNT(*) as cnt FROM dependencies").fetchone()["cnt"]

        # Category-level counts (open/wip/done) via template-aware resolution
        by_category: dict[str, int] = {"open": 0, "wip": 0, "done": 0}
        for row in self.conn.execute("SELECT type, status, COUNT(*) as cnt FROM issues GROUP BY type, status").fetchall():
            cat = self._resolve_status_category(row["type"], row["status"])
            by_category[cat] = by_category.get(cat, 0) + row["cnt"]

        return {
            "by_status": by_status,
            "by_category": by_category,
            "by_type": by_type,
            "ready_count": ready_count,
            "blocked_count": blocked_count,
            "total_dependencies": dep_count,
        }

    # -- Events (private) ----------------------------------------------------

    def _record_event(
        self,
        issue_id: str,
        event_type: str,
        *,
        actor: str = "",
        old_value: str | None = None,
        new_value: str | None = None,
        comment: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, actor, old_value, new_value, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (issue_id, event_type, actor, old_value, new_value, comment, _now_iso()),
        )

    def get_recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT e.*, i.title as issue_title FROM events e JOIN issues i ON e.issue_id = i.id ORDER BY e.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_events_since(self, since: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Get events since a given ISO timestamp, ordered chronologically."""
        rows = self.conn.execute(
            "SELECT e.*, i.title as issue_title FROM events e "
            "JOIN issues i ON e.issue_id = i.id "
            "WHERE e.created_at > ? "
            "ORDER BY e.created_at ASC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_issue_events(self, issue_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Get events for a specific issue, newest first."""
        self.get_issue(issue_id)  # raises KeyError if not found
        rows = self.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (issue_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def undo_last(self, issue_id: str, *, actor: str = "") -> dict[str, Any]:
        """Undo the most recent reversible event for an issue.

        Returns dict with 'undone' bool and details. Only reverses the single
        most recent reversible event — 'undone' events are not themselves
        undoable, preventing undo chains.
        """
        current = self.get_issue(issue_id)
        now = _now_iso()

        # Find the most recent reversible event directly (skips non-reversible events
        # like 'created', 'released', 'archived' so undo can reach earlier reversible ones)
        rev_ph = ",".join("?" * len(_REVERSIBLE_EVENTS))
        row = self.conn.execute(
            f"SELECT * FROM events WHERE issue_id = ? AND event_type IN ({rev_ph}) ORDER BY created_at DESC, id DESC LIMIT 1",
            (issue_id, *_REVERSIBLE_EVENTS),
        ).fetchone()

        if row is None:
            return {"undone": False, "reason": "No reversible events to undo"}

        event_type = row["event_type"]
        event_id = row["id"]

        # Check if this event was already undone (a newer 'undone' event exists)
        already_undone = self.conn.execute(
            "SELECT 1 FROM events WHERE issue_id = ? AND event_type = 'undone' AND (created_at > ? OR (created_at = ? AND id > ?))",
            (issue_id, row["created_at"], row["created_at"], event_id),
        ).fetchone()
        if already_undone:
            return {"undone": False, "reason": "Most recent reversible event already undone"}

        # Apply reverse action
        match event_type:
            case "status_changed":
                old_status = row["old_value"]
                # Direct SQL update — bypasses transition validation for undo
                self.conn.execute(
                    "UPDATE issues SET status = ?, updated_at = ? WHERE id = ?",
                    (old_status, now, issue_id),
                )
                # Maintain closed_at consistency with the restored status
                old_cat = self._resolve_status_category(current.type, old_status)
                if old_cat == "done":
                    # Restoring to a done state — set closed_at
                    self.conn.execute(
                        "UPDATE issues SET closed_at = ? WHERE id = ?",
                        (now, issue_id),
                    )
                else:
                    # Restoring to a non-done state — clear closed_at
                    self.conn.execute(
                        "UPDATE issues SET closed_at = NULL WHERE id = ?",
                        (issue_id,),
                    )

            case "title_changed":
                self.conn.execute(
                    "UPDATE issues SET title = ?, updated_at = ? WHERE id = ?",
                    (row["old_value"], now, issue_id),
                )

            case "priority_changed":
                if row["old_value"] is None:
                    return {"undone": False, "reason": "Cannot undo: event has no old_value"}
                self.conn.execute(
                    "UPDATE issues SET priority = ?, updated_at = ? WHERE id = ?",
                    (int(row["old_value"]), now, issue_id),
                )

            case "assignee_changed":
                self.conn.execute(
                    "UPDATE issues SET assignee = ?, updated_at = ? WHERE id = ?",
                    (row["old_value"] or "", now, issue_id),
                )

            case "claimed":
                # Restore: clear the assignee that was set by claim
                self.conn.execute(
                    "UPDATE issues SET assignee = '', updated_at = ? WHERE id = ?",
                    (now, issue_id),
                )

            case "dependency_added":
                # Event: issue_id=from_id, new_value="type:depends_on_id"
                if row["new_value"] is None:
                    return {"undone": False, "reason": "Cannot undo: event has no new_value"}
                dep_target = row["new_value"].split(":", 1)[-1] if ":" in row["new_value"] else row["new_value"]
                self.conn.execute(
                    "DELETE FROM dependencies WHERE issue_id = ? AND depends_on_id = ?",
                    (issue_id, dep_target),
                )

            case "dependency_removed":
                # Event: issue_id=from_id, old_value=depends_on_id
                self.conn.execute(
                    "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, 'blocks', ?)",
                    (issue_id, row["old_value"], now),
                )

            case "description_changed":
                self.conn.execute(
                    "UPDATE issues SET description = ?, updated_at = ? WHERE id = ?",
                    (row["old_value"] or "", now, issue_id),
                )

            case "notes_changed":
                self.conn.execute(
                    "UPDATE issues SET notes = ?, updated_at = ? WHERE id = ?",
                    (row["old_value"] or "", now, issue_id),
                )

        # Record the undo event
        self._record_event(
            issue_id,
            "undone",
            actor=actor,
            old_value=event_type,
            new_value=str(event_id),
        )
        self.conn.commit()

        return {
            "undone": True,
            "event_type": event_type,
            "event_id": event_id,
            "issue": self.get_issue(issue_id).to_dict(),
        }

    # -- Bulk import (for migration) -----------------------------------------

    def bulk_insert_issue(self, issue_data: dict[str, Any], *, validate: bool = True) -> None:
        """Insert a pre-formed issue dict directly. For migration use only."""
        if validate:
            self._validate_parent_id(issue_data.get("parent_id"))
        self.conn.execute(
            "INSERT OR IGNORE INTO issues "
            "(id, title, status, priority, type, parent_id, assignee, "
            "created_at, updated_at, closed_at, description, notes, fields) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                issue_data["id"],
                issue_data["title"],
                issue_data.get("status", "open"),
                issue_data.get("priority", 2),
                issue_data.get("type", "task"),
                issue_data.get("parent_id"),
                issue_data.get("assignee", ""),
                issue_data.get("created_at", _now_iso()),
                issue_data.get("updated_at", _now_iso()),
                issue_data.get("closed_at"),
                issue_data.get("description", ""),
                issue_data.get("notes", ""),
                json.dumps(issue_data.get("fields", {})),
            ),
        )

    def bulk_insert_dependency(self, issue_id: str, depends_on_id: str, dep_type: str = "blocks") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, ?, ?)",
            (issue_id, depends_on_id, dep_type, _now_iso()),
        )

    def bulk_insert_event(self, event_data: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO events (issue_id, event_type, actor, old_value, new_value, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_data["issue_id"],
                event_data["event_type"],
                event_data.get("actor", ""),
                event_data.get("old_value"),
                event_data.get("new_value"),
                event_data.get("comment", ""),
                event_data.get("created_at", _now_iso()),
            ),
        )

    def bulk_commit(self) -> None:
        self.conn.commit()

    # -- Export / Import (JSONL) -----------------------------------------------

    def export_jsonl(self, output_path: str | Path) -> int:
        """Export all issues, dependencies, labels, comments, and events to JSONL.

        Each line is a JSON object with a "type" field indicating the record type.
        Returns the total number of records written.
        """
        count = 0
        with Path(output_path).open("w") as f:
            # Issues
            for row in self.conn.execute("SELECT * FROM issues ORDER BY created_at").fetchall():
                record = dict(row)
                record["_type"] = "issue"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            # Dependencies
            for row in self.conn.execute("SELECT * FROM dependencies ORDER BY issue_id").fetchall():
                record = dict(row)
                record["_type"] = "dependency"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            # Labels
            for row in self.conn.execute("SELECT * FROM labels ORDER BY issue_id").fetchall():
                record = dict(row)
                record["_type"] = "label"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            # Comments
            for row in self.conn.execute("SELECT * FROM comments ORDER BY created_at").fetchall():
                record = dict(row)
                record["_type"] = "comment"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            # Events
            for row in self.conn.execute("SELECT * FROM events ORDER BY created_at").fetchall():
                record = dict(row)
                record["_type"] = "event"
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

        return count

    def import_jsonl(self, input_path: str | Path, *, merge: bool = False) -> int:
        """Import issues from JSONL file.

        Args:
            input_path: Path to JSONL file
            merge: If True, skip existing records (OR IGNORE). If False, raise on conflict.

        Returns the number of records imported.
        """
        count = 0
        conflict = "OR IGNORE" if merge else "OR ABORT"

        with Path(input_path).open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record_type = record.pop("_type", None)

                if record_type == "issue":
                    self.conn.execute(
                        f"INSERT {conflict} INTO issues "
                        "(id, title, status, priority, type, parent_id, assignee, "
                        "created_at, updated_at, closed_at, description, notes, fields) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            record["id"],
                            record["title"],
                            record.get("status", "open"),
                            record.get("priority", 2),
                            record.get("type", "task"),
                            record.get("parent_id"),
                            record.get("assignee", ""),
                            record.get("created_at", _now_iso()),
                            record.get("updated_at", _now_iso()),
                            record.get("closed_at"),
                            record.get("description", ""),
                            record.get("notes", ""),
                            record.get("fields", "{}"),
                        ),
                    )
                elif record_type == "dependency":
                    self.conn.execute(
                        f"INSERT {conflict} INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, ?, ?)",
                        (
                            record["issue_id"],
                            record["depends_on_id"],
                            record.get("type", "blocks"),
                            record.get("created_at", _now_iso()),
                        ),
                    )
                elif record_type == "label":
                    self.conn.execute(
                        f"INSERT {conflict} INTO labels (issue_id, label) VALUES (?, ?)",
                        (record["issue_id"], record["label"]),
                    )
                elif record_type == "comment":
                    self.conn.execute(
                        f"INSERT {conflict} INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
                        (
                            record.get("issue_id", ""),
                            record.get("author", ""),
                            record.get("text", ""),
                            record.get("created_at", _now_iso()),
                        ),
                    )
                elif record_type == "event":
                    self.conn.execute(
                        "INSERT OR IGNORE INTO events "
                        "(issue_id, event_type, actor, old_value, new_value, comment, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            record.get("issue_id", ""),
                            record.get("event_type", ""),
                            record.get("actor", ""),
                            record.get("old_value"),
                            record.get("new_value"),
                            record.get("comment", ""),
                            record.get("created_at", _now_iso()),
                        ),
                    )
                else:
                    continue  # Unknown record type — skip

                count += 1

        self.conn.commit()
        return count

    # -- File records & scan findings ----------------------------------------

    def _generate_file_id(self) -> str:
        """Generate a unique file record ID like 'prefix-f-abc123'."""
        for _ in range(10):
            candidate = f"{self.prefix}-f-{uuid.uuid4().hex[:6]}"
            exists = self.conn.execute("SELECT 1 FROM file_records WHERE id = ?", (candidate,)).fetchone()
            if exists is None:
                return candidate
        return f"{self.prefix}-f-{uuid.uuid4().hex[:10]}"

    def _generate_finding_id(self) -> str:
        """Generate a unique scan finding ID like 'prefix-sf-abc123'."""
        for _ in range(10):
            candidate = f"{self.prefix}-sf-{uuid.uuid4().hex[:6]}"
            exists = self.conn.execute("SELECT 1 FROM scan_findings WHERE id = ?", (candidate,)).fetchone()
            if exists is None:
                return candidate
        return f"{self.prefix}-sf-{uuid.uuid4().hex[:10]}"

    def _build_file_record(self, row: sqlite3.Row) -> FileRecord:
        """Build a FileRecord from a database row."""
        meta_raw = row["metadata"]
        meta = json.loads(meta_raw) if meta_raw else {}
        return FileRecord(
            id=row["id"],
            path=row["path"],
            language=row["language"] or "",
            file_type=row["file_type"] or "",
            first_seen=row["first_seen"],
            updated_at=row["updated_at"],
            metadata=meta,
        )

    def _build_scan_finding(self, row: sqlite3.Row) -> ScanFinding:
        """Build a ScanFinding from a database row."""
        meta_raw = row["metadata"]
        try:
            parsed_meta = json.loads(meta_raw) if meta_raw else {}
        except (json.JSONDecodeError, TypeError):
            parsed_meta = {}
        meta = parsed_meta if isinstance(parsed_meta, dict) else {}
        return ScanFinding(
            id=row["id"],
            file_id=row["file_id"],
            severity=row["severity"],
            status=row["status"],
            scan_source=row["scan_source"] or "",
            rule_id=row["rule_id"] or "",
            message=row["message"] or "",
            suggestion=row["suggestion"] or "",
            scan_run_id=row["scan_run_id"] or "",
            line_start=row["line_start"],
            line_end=row["line_end"],
            issue_id=row["issue_id"],
            seen_count=row["seen_count"] or 1,
            first_seen=row["first_seen"],
            updated_at=row["updated_at"],
            last_seen_at=row["last_seen_at"],
            metadata=meta,
        )

    def register_file(
        self,
        path: str,
        *,
        language: str = "",
        file_type: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> FileRecord:
        """Register a file or update it if already registered (upsert by path).

        Returns the FileRecord (created or updated).
        """
        now = _now_iso()
        existing = self.conn.execute("SELECT * FROM file_records WHERE path = ?", (path,)).fetchone()

        if existing is not None:
            updates: list[str] = []
            params: list[Any] = []
            # Detect field changes and emit events
            changes: list[tuple[str, str, str]] = []  # (field, old, new)
            if language and language != (existing["language"] or ""):
                updates.append("language = ?")
                params.append(language)
                changes.append(("language", existing["language"] or "", language))
            if file_type and file_type != (existing["file_type"] or ""):
                updates.append("file_type = ?")
                params.append(file_type)
                changes.append(("file_type", existing["file_type"] or "", file_type))
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
            updates.append("updated_at = ?")
            params.append(now)
            params.append(existing["id"])
            self.conn.execute(
                f"UPDATE file_records SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            for field, old_val, new_val in changes:
                self.conn.execute(
                    "INSERT INTO file_events "
                    "(file_id, event_type, field, old_value, new_value, created_at) "
                    "VALUES (?, 'file_metadata_update', ?, ?, ?, ?)",
                    (existing["id"], field, old_val, new_val, now),
                )
            self.conn.commit()
            return self.get_file(existing["id"])

        file_id = self._generate_file_id()
        self.conn.execute(
            "INSERT INTO file_records (id, path, language, file_type, first_seen, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_id, path, language, file_type, now, now, json.dumps(metadata or {})),
        )
        self.conn.commit()
        return self.get_file(file_id)

    def get_file(self, file_id: str) -> FileRecord:
        """Get a file record by ID. Raises KeyError if not found."""
        row = self.conn.execute("SELECT * FROM file_records WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise KeyError(file_id)
        return self._build_file_record(row)

    def get_file_by_path(self, path: str) -> FileRecord | None:
        """Get a file record by path. Returns None if not found."""
        row = self.conn.execute("SELECT * FROM file_records WHERE path = ?", (path,)).fetchone()
        if row is None:
            return None
        return self._build_file_record(row)

    def list_files(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        language: str | None = None,
        path_prefix: str | None = None,
        sort: str = "updated_at",
    ) -> list[FileRecord]:
        """List file records with optional filtering and sorting."""
        clauses: list[str] = []
        params: list[Any] = []

        if language is not None:
            clauses.append("language = ?")
            params.append(language)
        if path_prefix is not None:
            clauses.append("path LIKE ?")
            params.append(f"%{path_prefix}%")

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        valid_sorts = {"updated_at", "first_seen", "path", "language"}
        sort_col = sort if sort in valid_sorts else "updated_at"
        order = "ASC" if sort_col == "path" else "DESC"

        rows = self.conn.execute(
            f"SELECT * FROM file_records{where} ORDER BY {sort_col} {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [self._build_file_record(r) for r in rows]

    _VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})

    def list_files_paginated(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        language: str | None = None,
        path_prefix: str | None = None,
        min_findings: int | None = None,
        has_severity: str | None = None,
        scan_source: str | None = None,
        sort: str = "updated_at",
        direction: str | None = None,
    ) -> dict[str, Any]:
        """List file records with pagination metadata.

        Returns ``{results, total, limit, offset, has_more}``.

        When *min_findings* is provided, only files with at least that many
        open findings are returned (uses a correlated subquery).

        When *has_severity* is provided (e.g. ``"critical"``), only files
        with at least one open finding of that severity are returned.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if language is not None:
            clauses.append("language = ?")
            params.append(language)
        if path_prefix is not None:
            clauses.append("path LIKE ?")
            params.append(f"%{path_prefix}%")
        if min_findings is not None and min_findings > 0:
            clauses.append(
                "(SELECT COUNT(*) FROM scan_findings sf"
                " WHERE sf.file_id = file_records.id"
                " AND sf.status NOT IN ('fixed', 'false_positive')) >= ?"
            )
            params.append(min_findings)
        if has_severity and has_severity in self._VALID_SEVERITIES:
            clauses.append(
                "(SELECT COUNT(*) FROM scan_findings sf"
                " WHERE sf.file_id = file_records.id"
                " AND sf.status NOT IN ('fixed', 'false_positive')"
                " AND sf.severity = ?) > 0"
            )
            params.append(has_severity)
        if scan_source:
            clauses.append("EXISTS (SELECT 1 FROM scan_findings sf WHERE sf.file_id = file_records.id AND sf.scan_source = ?)")
            params.append(scan_source)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        total: int = self.conn.execute(
            f"SELECT COUNT(*) FROM file_records{where}",
            params,
        ).fetchone()[0]

        valid_sorts = {"updated_at", "first_seen", "path", "language"}
        sort_col = sort if sort in valid_sorts else "updated_at"
        default_order = "ASC" if sort_col == "path" else "DESC"
        order = direction.upper() if direction and direction.upper() in ("ASC", "DESC") else default_order

        _open = "sf.status NOT IN ('fixed', 'false_positive')"
        _sev_cols = " ".join(
            f"(SELECT COUNT(*) FROM scan_findings sf WHERE sf.file_id = fr.id AND {_open} AND sf.severity='{s}') AS cnt_{s},"
            for s in ("critical", "high", "medium", "low", "info")
        )
        fr_where = where.replace("file_records.id", "fr.id")
        enriched_sql = (
            f"SELECT fr.*, "
            f"(SELECT COUNT(*) FROM scan_findings sf"
            f" WHERE sf.file_id = fr.id AND {_open}"
            f") AS open_findings, "
            f"(SELECT COUNT(*) FROM scan_findings sf"
            f" WHERE sf.file_id = fr.id"
            f") AS total_findings, "
            f"{_sev_cols} "
            f"(SELECT COUNT(*) FROM file_associations fa"
            f" WHERE fa.file_id = fr.id"
            f") AS associations_count"
            f" FROM file_records fr{fr_where}"
            f" ORDER BY {sort_col} {order}"
            f" LIMIT ? OFFSET ?"
        )
        rows = self.conn.execute(enriched_sql, [*params, limit, offset]).fetchall()

        results = []
        for r in rows:
            d = self._build_file_record(r).to_dict()
            d["summary"] = {
                "total_findings": r["total_findings"],
                "open_findings": r["open_findings"],
                "critical": r["cnt_critical"],
                "high": r["cnt_high"],
                "medium": r["cnt_medium"],
                "low": r["cnt_low"],
                "info": r["cnt_info"],
            }
            d["associations_count"] = r["associations_count"]
            results.append(d)
        return {
            "results": results,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }

    def process_scan_results(
        self,
        *,
        scan_source: str,
        findings: list[dict[str, Any]],
        scan_run_id: str = "",
        mark_unseen: bool = False,
    ) -> dict[str, Any]:
        """Ingest scan results: create/update file records and findings.

        Each finding dict must have at minimum: path, rule_id, severity, message.
        Optional: language, line_start, line_end, metadata.

        When *mark_unseen* is ``True``, findings in the same (file, scan_source)
        that are NOT in this batch are set to ``unseen_in_latest`` status.
        Only findings with a non-terminal status are affected (``fixed`` and
        ``false_positive`` are left alone).

        Returns summary stats including ``new_finding_ids``.
        """
        # Validate all findings upfront before any writes, so a bad entry
        # at index N cannot leave writes from 0..N-1 pending.
        warnings: list[str] = []
        for i, f in enumerate(findings):
            if not isinstance(f, dict):
                raise ValueError(f"findings[{i}] must be a dict, got {type(f).__name__}")
            if "path" not in f:
                raise ValueError(f"findings[{i}] is missing required key 'path'")
            if "rule_id" not in f:
                raise ValueError(f"findings[{i}] is missing required key 'rule_id'")
            if "message" not in f:
                raise ValueError(f"findings[{i}] is missing required key 'message'")
            rule_id = f["rule_id"]
            if not isinstance(rule_id, str):
                raise ValueError(f"findings[{i}] rule_id must be a string, got {type(rule_id).__name__}")
            if not rule_id.strip():
                raise ValueError(f"findings[{i}] rule_id must be a non-empty string")
            message = f["message"]
            if not isinstance(message, str):
                raise ValueError(f"findings[{i}] message must be a string, got {type(message).__name__}")
            if not message.strip():
                raise ValueError(f"findings[{i}] message must be a non-empty string")
            severity = f.get("severity", "info")
            if not isinstance(severity, str):
                msg = f"findings[{i}] severity must be a string, got {type(severity).__name__}"
                raise ValueError(msg)
            if isinstance(f["path"], str):
                f["path"] = _normalize_scan_path(f["path"])
            # Normalize: strip whitespace and lowercase
            normalized = severity.strip().lower()
            if normalized in VALID_SEVERITIES:
                f["severity"] = normalized
            else:
                path = f["path"]
                rule_id = f.get("rule_id", "")
                warn_msg = f"Unknown severity {severity!r} for finding at {path} (rule_id={rule_id!r}), mapped to 'info'"
                warnings.append(warn_msg)
                logger.warning(
                    "Severity fallback: %r → 'info' for %s (rule_id=%s, scan_source=%s)",
                    severity,
                    path,
                    rule_id,
                    scan_source,
                )
                f["severity"] = "info"

        now = _now_iso()
        stats: dict[str, Any] = {
            "files_created": 0,
            "files_updated": 0,
            "findings_created": 0,
            "findings_updated": 0,
            "new_finding_ids": [],
            "warnings": warnings,
        }

        # Track which finding IDs were seen, keyed by file_id, for mark_unseen
        seen_finding_ids: dict[str, list[str]] = {}

        for f in findings:
            severity = f.get("severity", "info")
            path = f["path"]
            language = f.get("language", "")

            # Upsert file record
            existing_file = self.conn.execute("SELECT id FROM file_records WHERE path = ?", (path,)).fetchone()
            if existing_file is not None:
                file_id = existing_file["id"]
                update_parts = ["updated_at = ?"]
                update_params: list[Any] = [now]
                if language:
                    update_parts.append("language = ?")
                    update_params.append(language)
                update_params.append(file_id)
                self.conn.execute(
                    f"UPDATE file_records SET {', '.join(update_parts)} WHERE id = ?",
                    update_params,
                )
                stats["files_updated"] += 1
            else:
                file_id = self._generate_file_id()
                self.conn.execute(
                    "INSERT INTO file_records (id, path, language, first_seen, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (file_id, path, language, now, now),
                )
                stats["files_created"] += 1

            # Upsert finding (dedup on file_id + scan_source + rule_id + line_start)
            rule_id = f.get("rule_id", "")
            line_start = f.get("line_start")
            dedup_line = line_start if line_start is not None else -1

            # Suggestion size cap (10,000 chars)
            suggestion = f.get("suggestion", "")
            if len(suggestion) > 10_000:
                logger.warning(
                    "Suggestion truncated for %s (rule_id=%s): %d chars → 10000",
                    path,
                    rule_id,
                    len(suggestion),
                )
                suggestion = suggestion[:10_000] + "\n[truncated]"

            existing_finding = self.conn.execute(
                "SELECT id, seen_count, scan_run_id FROM scan_findings "
                "WHERE file_id = ? AND scan_source = ? AND rule_id = ? AND coalesce(line_start, -1) = ?",
                (file_id, scan_source, rule_id, dedup_line),
            ).fetchone()

            if existing_finding is not None:
                # scan_run_id attribution: keep original if non-empty, allow
                # late attribution for previously-unattributed findings
                existing_run_id = existing_finding["scan_run_id"] or ""
                run_id_update = existing_run_id
                if scan_run_id and not existing_run_id:
                    run_id_update = scan_run_id

                self.conn.execute(
                    "UPDATE scan_findings SET message = ?, severity = ?, line_end = ?, "
                    "suggestion = ?, scan_run_id = ?, metadata = ?, "
                    "seen_count = seen_count + 1, updated_at = ?, last_seen_at = ?, "
                    "status = CASE WHEN status IN ('fixed', 'unseen_in_latest') THEN 'open' ELSE status END "
                    "WHERE id = ?",
                    (
                        f.get("message", ""),
                        severity,
                        f.get("line_end"),
                        suggestion,
                        run_id_update,
                        json.dumps(f.get("metadata") or {}),
                        now,
                        now,
                        existing_finding["id"],
                    ),
                )
                stats["findings_updated"] += 1
                seen_finding_ids.setdefault(file_id, []).append(existing_finding["id"])
            else:
                finding_id = self._generate_finding_id()
                self.conn.execute(
                    "INSERT INTO scan_findings "
                    "(id, file_id, scan_source, rule_id, severity, status, message, "
                    "suggestion, scan_run_id, "
                    "line_start, line_end, first_seen, updated_at, last_seen_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        finding_id,
                        file_id,
                        scan_source,
                        rule_id,
                        severity,
                        f.get("message", ""),
                        suggestion,
                        scan_run_id,
                        line_start,
                        f.get("line_end"),
                        now,
                        now,
                        now,
                        json.dumps(f.get("metadata") or {}),
                    ),
                )
                stats["findings_created"] += 1
                stats["new_finding_ids"].append(finding_id)
                seen_finding_ids.setdefault(file_id, []).append(finding_id)

        # Mark unseen findings as unseen_in_latest (atomic per file+source)
        if mark_unseen:
            terminal = ("fixed", "false_positive")
            for fid, fids in seen_finding_ids.items():
                placeholders = ",".join("?" * len(fids))
                self.conn.execute(
                    f"UPDATE scan_findings SET status = 'unseen_in_latest', updated_at = ? "
                    f"WHERE file_id = ? AND scan_source = ? "
                    f"AND status NOT IN (?, ?) "
                    f"AND id NOT IN ({placeholders})",
                    [now, fid, scan_source, *terminal, *fids],
                )

        self.conn.commit()
        return stats

    def get_scan_runs(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Query scan run history from scan_findings grouped by scan_run_id.

        Returns a list of scan run summaries, ordered by most recent activity.
        Findings with empty scan_run_id are excluded.
        """
        rows = self.conn.execute(
            "SELECT scan_run_id, scan_source, "
            "MIN(first_seen) AS started_at, "
            "MAX(updated_at) AS completed_at, "
            "COUNT(*) AS total_findings, "
            "COUNT(DISTINCT file_id) AS files_scanned "
            "FROM scan_findings "
            "WHERE scan_run_id != '' "
            "GROUP BY scan_run_id, scan_source "
            "ORDER BY MAX(updated_at) DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "scan_run_id": row["scan_run_id"],
                "scan_source": row["scan_source"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "total_findings": row["total_findings"],
                "files_scanned": row["files_scanned"],
            }
            for row in rows
        ]

    def clean_stale_findings(
        self,
        *,
        days: int = 30,
        scan_source: str | None = None,
        actor: str = "",
    ) -> dict[str, Any]:
        """Move ``unseen_in_latest`` findings older than *days* to ``fixed``.

        Only affects findings whose ``last_seen_at`` (or ``updated_at`` as
        fallback) is older than the cutoff.  Returns stats about what changed.
        """
        from datetime import timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

        clauses = [
            "status = 'unseen_in_latest'",
            "coalesce(last_seen_at, updated_at) < ?",
        ]
        params: list[Any] = [cutoff]

        if scan_source is not None:
            clauses.append("scan_source = ?")
            params.append(scan_source)

        now = _now_iso()
        where = " AND ".join(clauses)
        cursor = self.conn.execute(
            f"UPDATE scan_findings SET status = 'fixed', updated_at = ? WHERE {where}",
            [now, *params],
        )
        self.conn.commit()
        return {"findings_fixed": cursor.rowcount}

    # Severity ordering for SQL sort: lower number = more severe.
    _SEVERITY_ORDER_SQL = (
        "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 WHEN 'info' THEN 4 ELSE 5 END"
    )

    _VALID_FINDING_SORTS = frozenset({"updated_at", "severity"})

    def get_findings(
        self,
        file_id: str,
        *,
        severity: str | None = None,
        status: str | None = None,
        sort: str = "updated_at",
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScanFinding]:
        """Get scan findings for a file with optional filters."""
        if sort not in self._VALID_FINDING_SORTS:
            valid = ", ".join(sorted(self._VALID_FINDING_SORTS))
            raise ValueError(f'Invalid sort field "{sort}". Must be one of: {valid}')

        clauses = ["file_id = ?"]
        params: list[Any] = [file_id]

        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        where = " AND ".join(clauses)
        order_clause = f"{self._SEVERITY_ORDER_SQL} ASC, updated_at DESC" if sort == "severity" else "updated_at DESC"

        rows = self.conn.execute(
            f"SELECT * FROM scan_findings WHERE {where} ORDER BY {order_clause} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [self._build_scan_finding(r) for r in rows]

    def get_findings_paginated(
        self,
        file_id: str,
        *,
        severity: str | None = None,
        status: str | None = None,
        sort: str = "updated_at",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get scan findings with pagination metadata.

        Returns ``{results, total, limit, offset, has_more}``.
        """
        if sort not in self._VALID_FINDING_SORTS:
            valid = ", ".join(sorted(self._VALID_FINDING_SORTS))
            raise ValueError(f'Invalid sort field "{sort}". Must be one of: {valid}')

        clauses = ["file_id = ?"]
        params: list[Any] = [file_id]

        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        where = " AND ".join(clauses)

        total: int = self.conn.execute(
            f"SELECT COUNT(*) FROM scan_findings WHERE {where}",
            params,
        ).fetchone()[0]

        order_clause = f"{self._SEVERITY_ORDER_SQL} ASC, updated_at DESC" if sort == "severity" else "updated_at DESC"

        rows = self.conn.execute(
            f"SELECT * FROM scan_findings WHERE {where} ORDER BY {order_clause} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

        results = [self._build_scan_finding(r).to_dict() for r in rows]
        return {
            "results": results,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }

    def get_file_findings_summary(self, file_id: str) -> dict[str, Any]:
        """Get a severity-bucketed summary of findings for a file.

        Returns a dict like::

            {"total_findings": 5, "open_findings": 3,
             "critical": 0, "high": 2, "medium": 1, "low": 0, "info": 0}

        Only findings with ``status`` not in ('fixed', 'false_positive') are
        counted towards ``open_findings`` and severity buckets.
        """
        # "open" = not fixed/false_positive; build SUM(CASE …) per severity
        _open = "status NOT IN ('fixed', 'false_positive')"
        _sev = " ".join(
            f"SUM(CASE WHEN severity='{s}' AND {_open} THEN 1 ELSE 0 END) AS {s}," for s in ("critical", "high", "medium", "low")
        )
        row = self.conn.execute(
            f"SELECT COUNT(*) AS total_findings, "
            f"SUM(CASE WHEN {_open} THEN 1 ELSE 0 END) AS open_findings, "
            f"{_sev} "
            f"SUM(CASE WHEN severity='info' AND {_open} THEN 1 ELSE 0 END) AS info "
            f"FROM scan_findings WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        return {
            "total_findings": row["total_findings"],
            "open_findings": row["open_findings"] or 0,
            "critical": row["critical"] or 0,
            "high": row["high"] or 0,
            "medium": row["medium"] or 0,
            "low": row["low"] or 0,
            "info": row["info"] or 0,
        }

    def get_global_findings_stats(self) -> dict[str, Any]:
        """Get project-wide severity-bucketed findings stats.

        Returns::

            {"total_findings": 20, "open_findings": 15,
             "files_with_findings": 8,
             "critical": 2, "high": 3, "medium": 5, "low": 4, "info": 1}
        """
        _open = "status NOT IN ('fixed', 'false_positive')"
        _sev = " ".join(
            f"SUM(CASE WHEN severity='{s}' AND {_open} THEN 1 ELSE 0 END) AS {s}," for s in ("critical", "high", "medium", "low")
        )
        row = self.conn.execute(
            f"SELECT COUNT(*) AS total_findings, "
            f"SUM(CASE WHEN {_open} THEN 1 ELSE 0 END) AS open_findings, "
            f"COUNT(DISTINCT CASE WHEN {_open} THEN file_id END) AS files_with_findings, "
            f"{_sev} "
            f"SUM(CASE WHEN severity='info' AND {_open} THEN 1 ELSE 0 END) AS info "
            f"FROM scan_findings",
        ).fetchone()
        return {
            "total_findings": row["total_findings"],
            "open_findings": row["open_findings"] or 0,
            "files_with_findings": row["files_with_findings"],
            "critical": row["critical"] or 0,
            "high": row["high"] or 0,
            "medium": row["medium"] or 0,
            "low": row["low"] or 0,
            "info": row["info"] or 0,
        }

    def get_file_detail(self, file_id: str) -> dict[str, Any]:
        """Get a structured file detail response with separated data layers.

        Returns::

            {
              "file": { ...file fields... },
              "associations": [ ...linked issues... ],
              "recent_findings": [ ...latest findings... ],
              "summary": { ...severity bucketed counts... }
            }

        Raises ``KeyError`` if the file does not exist.
        """
        f = self.get_file(file_id)
        associations = self.get_file_associations(file_id)
        recent = self.get_findings(file_id, limit=10)
        summary = self.get_file_findings_summary(file_id)
        return {
            "file": f.to_dict(),
            "associations": associations,
            "recent_findings": [r.to_dict() for r in recent],
            "summary": summary,
        }

    def add_file_association(
        self,
        file_id: str,
        issue_id: str,
        assoc_type: str,
    ) -> None:
        """Link a file to an issue. Idempotent (duplicates ignored)."""
        if assoc_type not in VALID_ASSOC_TYPES:
            msg = f'Invalid assoc_type "{assoc_type}". Must be one of: {", ".join(sorted(VALID_ASSOC_TYPES))}'
            raise ValueError(msg)
        # Validate issue exists before creating the association
        row = self.conn.execute("SELECT 1 FROM issues WHERE id = ?", (issue_id,)).fetchone()
        if row is None:
            msg = f'Issue not found: "{issue_id}". Verify the issue exists before creating an association.'
            raise ValueError(msg)
        now = _now_iso()
        self.conn.execute(
            "INSERT OR IGNORE INTO file_associations (file_id, issue_id, assoc_type, created_at) VALUES (?, ?, ?, ?)",
            (file_id, issue_id, assoc_type, now),
        )
        self.conn.commit()

    def get_file_associations(self, file_id: str) -> list[dict[str, Any]]:
        """Get all issue associations for a file."""
        rows = self.conn.execute(
            "SELECT fa.*, i.title as issue_title, i.status as issue_status "
            "FROM file_associations fa "
            "LEFT JOIN issues i ON fa.issue_id = i.id "
            "WHERE fa.file_id = ? ORDER BY fa.created_at DESC",
            (file_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "file_id": r["file_id"],
                "issue_id": r["issue_id"],
                "assoc_type": r["assoc_type"],
                "created_at": r["created_at"],
                "issue_title": r["issue_title"],
                "issue_status": r["issue_status"],
            }
            for r in rows
        ]

    def get_issue_files(self, issue_id: str) -> list[dict[str, Any]]:
        """Get all files associated with an issue (issue → files direction)."""
        rows = self.conn.execute(
            "SELECT fa.*, fr.path as file_path, fr.language as file_language "
            "FROM file_associations fa "
            "JOIN file_records fr ON fa.file_id = fr.id "
            "WHERE fa.issue_id = ? ORDER BY fa.created_at DESC",
            (issue_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "file_id": r["file_id"],
                "issue_id": r["issue_id"],
                "assoc_type": r["assoc_type"],
                "created_at": r["created_at"],
                "file_path": r["file_path"],
                "file_language": r["file_language"],
            }
            for r in rows
        ]

    def get_issue_findings(self, issue_id: str) -> list[ScanFinding]:
        """Get all scan findings related to an issue.

        Finds findings via two paths:
        1. scan_findings.issue_id FK (directly linked)
        2. file_associations with assoc_type='scan_finding' (linked via file)
        """
        rows = self.conn.execute(
            "SELECT sf.* FROM scan_findings sf WHERE sf.issue_id = ? "
            "UNION "
            "SELECT sf.* FROM scan_findings sf "
            "JOIN file_associations fa ON sf.file_id = fa.file_id "
            "WHERE fa.issue_id = ? AND fa.assoc_type = 'scan_finding'",
            (issue_id, issue_id),
        ).fetchall()
        return [self._build_scan_finding(r) for r in rows]

    def get_file_hotspots(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Get files ranked by weighted finding severity score.

        Only counts open findings. Severity weights:
        critical=10, high=5, medium=2, low=1, info=0.
        """
        rows = self.conn.execute(
            """
            SELECT
                fr.id, fr.path, fr.language,
                SUM(CASE WHEN sf.severity = 'critical' THEN 1 ELSE 0 END) as cnt_critical,
                SUM(CASE WHEN sf.severity = 'high' THEN 1 ELSE 0 END) as cnt_high,
                SUM(CASE WHEN sf.severity = 'medium' THEN 1 ELSE 0 END) as cnt_medium,
                SUM(CASE WHEN sf.severity = 'low' THEN 1 ELSE 0 END) as cnt_low,
                SUM(CASE WHEN sf.severity = 'info' THEN 1 ELSE 0 END) as cnt_info,
                SUM(
                    CASE sf.severity
                        WHEN 'critical' THEN 10
                        WHEN 'high' THEN 5
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 1
                        ELSE 0
                    END
                ) as score
            FROM file_records fr
            JOIN scan_findings sf ON sf.file_id = fr.id
            WHERE sf.status = 'open'
            GROUP BY fr.id
            HAVING score > 0
            ORDER BY score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [
            {
                "file": {"id": r["id"], "path": r["path"], "language": r["language"]},
                "score": r["score"],
                "findings_breakdown": {
                    "critical": r["cnt_critical"],
                    "high": r["cnt_high"],
                    "medium": r["cnt_medium"],
                    "low": r["cnt_low"],
                    "info": r["cnt_info"],
                },
            }
            for r in rows
        ]

    # -- File Timeline --------------------------------------------------------

    def get_file_timeline(
        self,
        file_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        """Build a merged timeline of events for a file.

        Assembles entries from scan findings and file associations, sorted
        newest-first.  Each entry carries a deterministic ``id`` derived from
        ``sha256(type + timestamp + source_id)[:12]`` so clients can
        cache/deduplicate without server coordination.

        When *event_type* is ``"finding"`` only finding events are returned;
        when ``"association"`` only association events.

        Raises ``KeyError`` if the file does not exist.
        """
        self.get_file(file_id)  # validate existence

        entries: list[dict[str, Any]] = []

        # 1. Finding events (created + status changes inferred from updated_at)
        findings = self.conn.execute(
            "SELECT id, scan_source, rule_id, severity, status, message, "
            "first_seen, updated_at FROM scan_findings WHERE file_id = ? "
            "ORDER BY first_seen DESC",
            (file_id,),
        ).fetchall()
        for f in findings:
            entries.append(
                {
                    "type": "finding_created",
                    "timestamp": f["first_seen"],
                    "source_id": f["id"],
                    "data": {
                        "scan_source": f["scan_source"],
                        "rule_id": f["rule_id"],
                        "severity": f["severity"],
                        "message": f["message"],
                    },
                }
            )
            if f["updated_at"] != f["first_seen"]:
                entries.append(
                    {
                        "type": "finding_updated",
                        "timestamp": f["updated_at"],
                        "source_id": f["id"],
                        "data": {
                            "scan_source": f["scan_source"],
                            "rule_id": f["rule_id"],
                            "severity": f["severity"],
                            "status": f["status"],
                        },
                    }
                )

        # 2. Association events
        assocs = self.conn.execute(
            "SELECT fa.id, fa.issue_id, fa.assoc_type, fa.created_at, "
            "i.title as issue_title "
            "FROM file_associations fa "
            "LEFT JOIN issues i ON fa.issue_id = i.id "
            "WHERE fa.file_id = ? ORDER BY fa.created_at DESC",
            (file_id,),
        ).fetchall()
        for a in assocs:
            entries.append(
                {
                    "type": "association_created",
                    "timestamp": a["created_at"],
                    "source_id": str(a["id"]),
                    "data": {
                        "issue_id": a["issue_id"],
                        "issue_title": a["issue_title"],
                        "assoc_type": a["assoc_type"],
                    },
                }
            )

        # 3. File metadata events
        meta_events = self.conn.execute(
            "SELECT id, field, old_value, new_value, created_at FROM file_events WHERE file_id = ? ORDER BY created_at DESC",
            (file_id,),
        ).fetchall()
        for m in meta_events:
            entries.append(
                {
                    "type": "file_metadata_update",
                    "timestamp": m["created_at"],
                    "source_id": str(m["id"]),
                    "data": {
                        "field": m["field"],
                        "old_value": m["old_value"],
                        "new_value": m["new_value"],
                    },
                }
            )

        # Filter by event type before sorting/paginating
        if event_type == "finding":
            entries = [e for e in entries if e["type"].startswith("finding_")]
        elif event_type == "association":
            entries = [e for e in entries if e["type"].startswith("association_")]
        elif event_type == "file_metadata_update":
            entries = [e for e in entries if e["type"] == "file_metadata_update"]
        elif event_type is not None:
            entries = []  # Unknown filter type -> empty results

        # Add deterministic IDs and sort newest-first
        for entry in entries:
            raw = f"{entry['type']}:{entry['timestamp']}:{entry['source_id']}"
            entry["id"] = hashlib.sha256(raw.encode()).hexdigest()[:12]

        entries.sort(key=lambda e: e["timestamp"], reverse=True)

        total = len(entries)
        page = entries[offset : offset + limit]
        return {
            "results": page,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        }

    # -- Archival / Compaction ------------------------------------------------

    def archive_closed(self, *, days_old: int = 30, actor: str = "") -> list[str]:
        """Archive done-category issues older than `days_old` days.

        Sets their status to 'archived' (preserving closed_at).
        Returns list of archived issue IDs.
        """
        from datetime import timedelta

        cutoff_dt = datetime.now(UTC) - timedelta(days=days_old)
        cutoff = cutoff_dt.isoformat()

        done_states = self._get_states_for_category("done") or ["closed"]
        done_ph = ",".join("?" * len(done_states))
        rows = self.conn.execute(
            f"SELECT id FROM issues WHERE status IN ({done_ph}) AND closed_at < ? AND closed_at IS NOT NULL",
            [*done_states, cutoff],
        ).fetchall()

        archived_ids = [r["id"] for r in rows]
        if not archived_ids:
            return []

        now = _now_iso()
        for issue_id in archived_ids:
            self.conn.execute(
                "UPDATE issues SET status = 'archived', updated_at = ? WHERE id = ?",
                (now, issue_id),
            )
            self._record_event(issue_id, "archived", actor=actor)

        self.conn.commit()
        return archived_ids

    def compact_events(self, *, keep_recent: int = 50, actor: str = "") -> int:
        """Remove old events for archived issues, keeping only the most recent ones.

        Returns the number of events deleted.
        """
        archived = self.conn.execute("SELECT id FROM issues WHERE status = 'archived'").fetchall()
        if not archived:
            return 0

        total_deleted = 0
        for row in archived:
            issue_id = row["id"]
            event_count = self.conn.execute("SELECT COUNT(*) as cnt FROM events WHERE issue_id = ?", (issue_id,)).fetchone()["cnt"]

            if event_count <= keep_recent:
                continue

            self.conn.execute(
                "DELETE FROM events WHERE id IN (  SELECT id FROM events WHERE issue_id = ? ORDER BY created_at ASC LIMIT ?)",
                (issue_id, event_count - keep_recent),
            )
            total_deleted += event_count - keep_recent

        if total_deleted > 0:
            self.conn.commit()

        return total_deleted

    def vacuum(self) -> None:
        """Run VACUUM to reclaim space after compaction."""
        self.conn.execute("VACUUM")

    def analyze(self) -> None:
        """Update query planner statistics."""
        self.conn.execute("ANALYZE")
