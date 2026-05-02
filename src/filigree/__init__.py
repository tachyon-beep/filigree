"""Filigree — agent-native issue tracker with convention-based project discovery."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any


def _read_source_version() -> str | None:
    # Fallback for source-only execution (no installed dist-info): read the
    # checkout's pyproject.toml so vendored / unbuilt deploys don't advertise
    # "0.0.0-dev" via --version and /api/health. Gated on [project].name
    # matching this package so a parent project's pyproject can't shadow ours.
    import tomllib
    from pathlib import Path

    try:
        candidate = Path(__file__).resolve().parents[2] / "pyproject.toml"
    except (OSError, IndexError):
        return None
    try:
        data = tomllib.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None
    project = data.get("project")
    if not isinstance(project, dict) or project.get("name") != "filigree":
        return None
    declared = project.get("version")
    return declared if isinstance(declared, str) else None


try:
    __version__ = version("filigree")
except PackageNotFoundError:
    __version__ = _read_source_version() or "0.0.0-dev"

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
