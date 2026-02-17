"""Filigree — agent-native issue tracker with convention-based project discovery."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("filigree")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

from filigree.core import FiligreeDB, Issue

__all__ = ["FiligreeDB", "Issue", "__version__"]
