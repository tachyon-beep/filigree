"""Install support subpackage — shared constants and re-exports."""

# Shared constants used by multiple install_support modules.
# Defined here (rather than in install.py) to avoid circular imports,
# since install.py re-exports symbols from submodules that need these.

FILIGREE_INSTRUCTIONS_MARKER = "<!-- filigree:instructions"
"""Detection prefix for filigree instruction blocks in markdown files."""

SKILL_NAME = "filigree-workflow"
"""Name of the filigree skill pack directory."""

SKILL_MARKER = "SKILL.md"
"""Sentinel file indicating a valid skill pack installation."""
