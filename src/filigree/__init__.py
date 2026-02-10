"""Filigree — agent-native issue tracker with convention-based project discovery."""

from importlib.metadata import version

__version__ = version("filigree")

from filigree.core import FiligreeDB, Issue

__all__ = ["FiligreeDB", "Issue", "__version__"]
