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
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from filigree.db_base import StatusCategory, _now_iso
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
from filigree.db_schema import CURRENT_SCHEMA_VERSION, SCHEMA_SQL
from filigree.db_workflow import WorkflowMixin
from filigree.types.core import (
    FileRecordDict,
    ISOTimestamp,
    IssueDict,
    PaginatedResult,
    ProjectConfig,
    ScanFindingDict,
)

if TYPE_CHECKING:
    from filigree.templates import TemplateRegistry

logger = logging.getLogger(__name__)

# Re-exported names from db_files (canonical definitions moved during mixin split)
# and from types.core (TypedDict re-exports for backward compat — see line 40)
__all__ = [
    "VALID_ASSOC_TYPES",
    "VALID_FINDING_STATUSES",
    "VALID_SEVERITIES",
    "FileRecordDict",
    "ISOTimestamp",
    "IssueDict",
    "PaginatedResult",
    "ProjectConfig",
    "ScanFindingDict",
    "_normalize_scan_path",
]

# ---------------------------------------------------------------------------
# Constrained-string Literal types
# ---------------------------------------------------------------------------

Severity = Literal["critical", "high", "medium", "low", "info"]
FindingStatus = Literal["open", "acknowledged", "fixed", "false_positive", "unseen_in_latest"]


# ProjectConfig and PaginatedResult moved to filigree.types.core (re-exported above)


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

# ISOTimestamp moved to filigree.types.core (re-exported above)
_EMPTY_TS: ISOTimestamp = ISOTimestamp("")


@dataclass
class Issue:
    id: str
    title: str
    status: str = "open"
    priority: int = 2
    type: str = "task"
    parent_id: str | None = None
    assignee: str = ""
    created_at: ISOTimestamp = _EMPTY_TS
    updated_at: ISOTimestamp = _EMPTY_TS
    closed_at: ISOTimestamp | None = None
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

    def to_dict(self) -> IssueDict:
        return IssueDict(
            id=self.id,
            title=self.title,
            status=self.status,
            status_category=self.status_category,
            priority=self.priority,
            type=self.type,
            parent_id=self.parent_id,
            assignee=self.assignee,
            created_at=self.created_at,
            updated_at=self.updated_at,
            closed_at=self.closed_at,
            description=self.description,
            notes=self.notes,
            fields=self.fields,
            labels=self.labels,
            blocks=self.blocks,
            blocked_by=self.blocked_by,
            is_ready=self.is_ready,
            children=self.children,
        )


@dataclass
class FileRecord:
    id: str
    path: str
    language: str = ""
    file_type: str = ""
    first_seen: ISOTimestamp = _EMPTY_TS
    updated_at: ISOTimestamp = _EMPTY_TS
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> FileRecordDict:
        return FileRecordDict(
            id=self.id,
            path=self.path,
            language=self.language,
            file_type=self.file_type,
            first_seen=self.first_seen,
            updated_at=self.updated_at,
            metadata=self.metadata,
        )


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
    first_seen: ISOTimestamp = _EMPTY_TS
    updated_at: ISOTimestamp = _EMPTY_TS
    last_seen_at: ISOTimestamp | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> ScanFindingDict:
        return ScanFindingDict(
            id=self.id,
            file_id=self.file_id,
            severity=self.severity,
            status=self.status,
            scan_source=self.scan_source,
            rule_id=self.rule_id,
            message=self.message,
            suggestion=self.suggestion,
            scan_run_id=self.scan_run_id,
            line_start=self.line_start,
            line_end=self.line_end,
            issue_id=self.issue_id,
            seen_count=self.seen_count,
            first_seen=self.first_seen,
            updated_at=self.updated_at,
            last_seen_at=self.last_seen_at,
            metadata=self.metadata,
        )


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
        if enabled_packs is not None and isinstance(enabled_packs, str):
            msg = f"enabled_packs must be a list of strings, not a bare string: {enabled_packs!r}"
            raise TypeError(msg)
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
        self._seed_future_release()
        self.conn.commit()

    def _seed_future_release(self) -> None:
        """Create the "Future" release singleton if it doesn't exist.

        Only runs when the ``release`` pack is enabled. Uses raw SQL to
        avoid circular validation during init. Idempotent — skips if a
        release with ``version == "Future"`` already exists.
        """
        if "release" not in self.enabled_packs:
            return

        existing = self.conn.execute(
            "SELECT id FROM issues WHERE type = 'release' AND json_extract(fields, '$.version') = 'Future'"
        ).fetchone()
        if existing is not None:
            return

        initial_state = self.templates.get_initial_state("release")
        issue_id = f"{self.prefix}-{_uuid.uuid4().hex[:10]}"
        now = _now_iso()
        self.conn.execute(
            "INSERT INTO issues (id, title, status, priority, type, assignee, "
            "created_at, updated_at, description, notes, fields) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (issue_id, "Future", initial_state, 4, "release", "", now, now, "", "", '{"version": "Future"}'),
        )
        logger.info("Seeded Future release singleton: %s", issue_id)

    def get_schema_version(self) -> int:
        """Return the current schema version from PRAGMA user_version."""
        result: int = self.conn.execute("PRAGMA user_version").fetchone()[0]
        return result

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
