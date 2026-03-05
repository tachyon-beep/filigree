"""Shared utilities, types, and Protocol for DB mixins."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from filigree.types.core import ISOTimestamp, StatusCategory

if TYPE_CHECKING:
    from filigree.core import Issue
    from filigree.templates import TemplateRegistry

# Re-export for backward compat
__all__ = ["DBMixinProtocol", "StatusCategory", "_now_iso"]


def _now_iso() -> ISOTimestamp:
    return ISOTimestamp(datetime.now(UTC).isoformat())


class DBMixinProtocol(Protocol):
    """Shared attributes and methods that DB mixins access via self.

    Mixins inherit this Protocol so mypy can type-check self.conn,
    self.get_issue(), etc. without ``type: ignore`` on every call.
    Actual implementations are provided by FiligreeDB at composition time.
    """

    db_path: Path
    prefix: str
    _conn: sqlite3.Connection | None
    _template_registry: TemplateRegistry | None
    _enabled_packs_override: list[str] | None

    @property
    def conn(self) -> sqlite3.Connection: ...

    def get_issue(self, issue_id: str) -> Issue: ...
