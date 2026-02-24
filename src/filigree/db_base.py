"""Protocol declaring shared attributes that all DB mixins access via self."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from filigree.templates import TemplateRegistry


class DBMixinProtocol(Protocol):
    """Attributes provided by FiligreeDB.__init__ that mixins may access."""

    db_path: Path
    prefix: str
    enabled_packs: list[str]
    _conn: sqlite3.Connection | None
    _template_registry: TemplateRegistry | None

    @property
    def conn(self) -> sqlite3.Connection: ...
