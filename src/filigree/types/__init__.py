# IMPORT CONSTRAINT: types/ modules must only import from typing, stdlib, and each other.
# NEVER import from core.py, db_base.py, or any mixin — this prevents circular imports.
"""Typed return-value contracts for filigree core and API layers."""

from __future__ import annotations

from filigree.types.core import (
    FileRecordDict,
    ISOTimestamp,
    IssueDict,
    PaginatedResult,
    ProjectConfig,
    ScanFindingDict,
)

# Future domain modules — uncomment as Tasks 1B/1C populate them:
# from filigree.types.files import ...
# from filigree.types.events import ...
# from filigree.types.planning import ...
# from filigree.types.workflow import ...

__all__ = [
    "FileRecordDict",
    "ISOTimestamp",
    "IssueDict",
    "PaginatedResult",
    "ProjectConfig",
    "ScanFindingDict",
]
