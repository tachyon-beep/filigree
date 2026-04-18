"""Filigree — agent-native issue tracker with convention-based project discovery."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

try:
    __version__ = version("filigree")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

__all__ = ["FiligreeDB", "Issue", "__version__"]

if TYPE_CHECKING:
    from filigree.core import FiligreeDB
    from filigree.models import Issue


def __getattr__(name: str) -> Any:
    # Deferred re-exports (PEP 562): keep the public ``filigree.FiligreeDB``
    # / ``filigree.Issue`` aliases without paying the import cost of the
    # full DB mixin stack when callers only want a lightweight submodule
    # like ``filigree.migrations``.
    if name == "FiligreeDB":
        from filigree.core import FiligreeDB

        return FiligreeDB
    if name == "Issue":
        from filigree.models import Issue

        return Issue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
