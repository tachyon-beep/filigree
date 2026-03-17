"""Shared utilities, types, and Protocol for DB mixins."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from filigree.models import FileRecord, Issue
from filigree.types.core import AssocType, ISOTimestamp, ScanRunStatus, StatusCategory
from filigree.types.events import EventType

if TYPE_CHECKING:
    from filigree.templates import TemplateRegistry, TransitionOption
    from filigree.types.core import ObservationDict
    from filigree.types.files import ScanRunDict

logger = logging.getLogger(__name__)

# Shared internal API — used by DB mixins across modules.
__all__ = ["AGE_BUCKETS", "DBMixinProtocol", "StatusCategory", "_escape_like", "_escape_like_chars", "_now_iso", "_safe_json_loads"]

# Virtual label dispatch — explicit allowlist, no prefix matching
AGE_BUCKETS: dict[str, tuple[int, int]] = {
    "fresh": (0, 7),
    "recent": (7, 30),
    "aging": (30, 90),
    "stale": (90, 180),
    "ancient": (180, 999999),
}


def _now_iso() -> ISOTimestamp:
    return ISOTimestamp(datetime.now(UTC).isoformat())


def _safe_json_loads(raw: str | None, context: str, *, error_key: str = "_metadata_error") -> dict[str, Any]:
    """Parse JSON from a database column, returning an error marker on corrupt data.

    Used by DB mixins to safely handle corrupt JSON in issue fields, file metadata,
    and scan finding metadata.  On failure, returns ``{error_key: True}`` — callers
    can check for the sentinel key (``_metadata_error`` or ``_fields_error``) to
    detect corrupt records.
    """
    try:
        result = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("Corrupt JSON (%s): %r", context, str(raw)[:200] if raw else raw)
        return {error_key: True}
    if not isinstance(result, dict):
        logger.warning("JSON (%s) parsed but is not a dict (got %s), repairing to {{}}: %r", context, type(result).__name__, str(raw)[:200])
        return {}
    return result


def _escape_like_chars(value: str) -> str:
    """Escape LIKE wildcard characters (``%``, ``_``, ``\\``) without adding wrapping wildcards."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _escape_like(query: str) -> str:
    """Escape a string for SQL LIKE with backslash escape, wrapped in % wildcards."""
    return f"%{_escape_like_chars(query)}%"


class DBMixinProtocol(Protocol):
    """Shared attributes and methods that DB mixins access via self.

    Mixins inherit this Protocol so mypy can type-check cross-mixin calls
    without ``type: ignore`` on every call.  Individual mixins implement a
    *subset* of these methods; the full contract is satisfied by FiligreeDB
    at composition time via MRO.

    **Single source of truth** — do NOT redeclare these methods in per-mixin
    ``TYPE_CHECKING`` blocks.  Add new cross-mixin methods here instead.
    """

    # -- Shared attributes ---------------------------------------------------

    db_path: Path
    prefix: str
    _conn: sqlite3.Connection | None
    _template_registry: TemplateRegistry | None
    _enabled_packs_override: list[str] | None

    @property
    def conn(self) -> sqlite3.Connection: ...

    # -- Core (FiligreeDB) ---------------------------------------------------

    def get_issue(self, issue_id: str) -> Issue: ...

    # -- WorkflowMixin -------------------------------------------------------

    @property
    def templates(self) -> TemplateRegistry: ...

    def _validate_status(self, status: str, issue_type: str = "task") -> None: ...
    def _validate_parent_id(self, parent_id: str | None) -> None: ...
    def _validate_label_name(self, label: str) -> str: ...
    def _get_states_for_category(self, category: str) -> list[str]: ...
    def _resolve_status_category(self, issue_type: str, status: str) -> StatusCategory: ...
    def get_valid_transitions(self, issue_id: str) -> list[TransitionOption]: ...

    @staticmethod
    def _infer_status_category(status: str) -> StatusCategory: ...

    # -- EventsMixin ---------------------------------------------------------

    def _record_event(
        self,
        issue_id: str,
        event_type: EventType,
        *,
        actor: str = "",
        old_value: str | None = None,
        new_value: str | None = None,
        comment: str = "",
    ) -> None: ...

    # -- IssuesMixin ---------------------------------------------------------

    def _generate_unique_id(self, table: str, infix: str = "") -> str: ...
    def _build_issues_batch(self, issue_ids: list[str]) -> list[Issue]: ...

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
    ) -> Issue: ...

    def list_issues(
        self,
        *,
        status: str | None = None,
        type: str | None = None,
        priority: int | None = None,
        parent_id: str | None = None,
        assignee: str | None = None,
        label: str | list[str] | None = None,
        label_prefix: str | None = None,
        not_label: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Issue]: ...

    # -- MetaMixin -----------------------------------------------------------

    def add_label(self, issue_id: str, label: str) -> bool: ...
    def add_comment(self, issue_id: str, text: str, *, author: str = "") -> int: ...

    # -- PlanningMixin -------------------------------------------------------

    def get_ready(self) -> list[Issue]: ...
    def _resolve_open_done_states(self) -> tuple[list[str], list[str], str, str]: ...

    # -- FilesMixin ----------------------------------------------------------

    def register_file(
        self,
        path: str,
        *,
        language: str = "",
        file_type: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> FileRecord: ...

    def add_file_association(self, file_id: str, issue_id: str, assoc_type: AssocType) -> None: ...

    # -- ObservationsMixin ---------------------------------------------------

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
        auto_commit: bool = True,
    ) -> ObservationDict: ...

    # -- ScansMixin ----------------------------------------------------------

    def update_scan_run_status(
        self,
        scan_run_id: str,
        status: ScanRunStatus,
        *,
        exit_code: int | None = None,
        findings_count: int | None = None,
        error_message: str | None = None,
    ) -> ScanRunDict: ...
