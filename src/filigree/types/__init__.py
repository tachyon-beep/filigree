# IMPORT CONSTRAINT: types/ modules must only import from typing, stdlib, and each other.
# NEVER import from core.py, db_base.py, or any mixin — this prevents circular imports.
"""Typed return-value contracts for filigree core and API layers.

Import directly from submodules (e.g., ``from filigree.types.api import IssueDict``).
"""
