"""Fixtures for end-to-end workflow scenario tests.

Pack-specific FiligreeDB fixtures used by tests in this directory.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import MappingProxyType

import pytest

from filigree.core import FiligreeDB
from filigree.templates import (
    StateDefinition,
    TransitionDefinition,
    TypeTemplate,
    WorkflowPack,
)
from tests._db_factory import make_db


@pytest.fixture
def db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + risk + spike packs enabled."""
    d = make_db(tmp_path, packs=["core", "risk", "spike"])
    yield d
    d.close()


@pytest.fixture
def req_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + requirements packs enabled."""
    d = make_db(tmp_path, packs=["core", "requirements"])
    yield d
    d.close()


@pytest.fixture
def roadmap_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + planning + roadmap packs enabled."""
    d = make_db(tmp_path, packs=["core", "planning", "roadmap"])
    yield d
    d.close()


@pytest.fixture
def incident_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + incident packs enabled.

    Intentionally omits the planning pack — workflow e2e tests only exercise
    incident, postmortem, and task types (task is in core). Adding planning
    would mask issues where incident workflows inadvertently depend on
    planning-pack types.
    """
    d = make_db(tmp_path, packs=["core", "incident"])
    yield d
    d.close()


@pytest.fixture
def debt_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + debt packs enabled."""
    d = make_db(tmp_path, packs=["core", "debt"])
    yield d
    d.close()


# ---------------------------------------------------------------------------
# builtin_* pack fixtures — appended for 2.0 plan Stage 2a.0
# Do NOT remove the existing `db` / `req_db` / `roadmap_db` fixtures above,
# which other workflow tests already depend on.
# ---------------------------------------------------------------------------


def _make_pack(name: str, types: dict[str, TypeTemplate]) -> WorkflowPack:
    return WorkflowPack(
        pack=name,
        version="test",
        display_name=name,
        description="test pack",
        types=MappingProxyType(types),
        requires_packs=(),
        relationships=(),
        cross_pack_relationships=(),
        guide=None,
    )


@pytest.fixture
def builtin_bug_pack() -> WorkflowPack:
    """Pack where 'bug' has open → confirmed (wip) → fixed (done)."""
    tpl = TypeTemplate(
        type="bug",
        display_name="Bug",
        description="bug type",
        pack="test_bug",
        states=(
            StateDefinition(name="open", category="open"),
            StateDefinition(name="confirmed", category="wip"),
            StateDefinition(name="fixed", category="done"),
        ),
        initial_state="open",
        transitions=(
            TransitionDefinition(from_state="open", to_state="confirmed", enforcement="hard"),
            TransitionDefinition(from_state="confirmed", to_state="fixed", enforcement="hard"),
        ),
        fields_schema=(),
    )
    return _make_pack("test_bug", {"bug": tpl})


@pytest.fixture
def builtin_pack_with_two_wip() -> WorkflowPack:
    """Pack where 'task' has open → (doing | reviewing) both wip."""
    tpl = TypeTemplate(
        type="task",
        display_name="Task",
        description="task type",
        pack="test_ambiguous",
        states=(
            StateDefinition(name="open", category="open"),
            StateDefinition(name="doing", category="wip"),
            StateDefinition(name="reviewing", category="wip"),
            StateDefinition(name="done", category="done"),
        ),
        initial_state="open",
        transitions=(
            TransitionDefinition(from_state="open", to_state="doing", enforcement="hard"),
            TransitionDefinition(from_state="open", to_state="reviewing", enforcement="hard"),
            TransitionDefinition(from_state="doing", to_state="done", enforcement="hard"),
            TransitionDefinition(from_state="reviewing", to_state="done", enforcement="hard"),
        ),
        fields_schema=(),
    )
    return _make_pack("test_ambiguous", {"task": tpl})


@pytest.fixture
def builtin_pack_with_only_done() -> WorkflowPack:
    """Pack where 'note' has draft (open) → published (done). No wip target."""
    tpl = TypeTemplate(
        type="note",
        display_name="Note",
        description="note type",
        pack="test_no_wip",
        states=(
            StateDefinition(name="draft", category="open"),
            StateDefinition(name="published", category="done"),
        ),
        initial_state="draft",
        transitions=(TransitionDefinition(from_state="draft", to_state="published", enforcement="hard"),),
        fields_schema=(),
    )
    return _make_pack("test_no_wip", {"note": tpl})
