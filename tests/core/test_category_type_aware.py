"""Regression tests for filigree-b55aa3191f: type-aware category SQL predicates.

When two enabled packs share a state name with different categories
(e.g. ``incident.resolved`` is ``wip`` while ``debt_item.resolved`` is
``done``), category filters and blocker queries must compare the
``(type, status)`` pair, not just the status name.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from tests._db_factory import make_db


@pytest.fixture
def collision_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """DB with incident + debt enabled — both define a ``resolved`` state in different categories."""
    d = make_db(tmp_path, packs=["core", "planning", "incident", "debt"])
    yield d
    d.close()


class TestListIssuesCategoryRespectsType:
    def test_incident_resolved_not_in_done_filter(self, collision_db: FiligreeDB) -> None:
        inc = collision_db.create_issue("incident", type="incident")
        # Drive the incident through its workflow into 'resolved' (a wip-category state).
        # incident: reported -> triaging requires `severity`.
        collision_db.update_issue(inc.id, status="triaging", fields={"severity": "sev3"})
        for nxt in ("investigating", "mitigating", "resolved"):
            collision_db.update_issue(inc.id, status=nxt)
        done = collision_db.list_issues(status="done")
        assert all(i.id != inc.id for i in done), "incident.resolved is wip, must not appear in done filter"

    def test_incident_resolved_in_wip_filter(self, collision_db: FiligreeDB) -> None:
        inc = collision_db.create_issue("incident", type="incident")
        # incident: reported -> triaging requires `severity`.
        collision_db.update_issue(inc.id, status="triaging", fields={"severity": "sev3"})
        for nxt in ("investigating", "mitigating", "resolved"):
            collision_db.update_issue(inc.id, status=nxt)
        wip = collision_db.list_issues(status="wip")
        assert any(i.id == inc.id for i in wip)

    def test_debt_resolved_in_done_filter(self, collision_db: FiligreeDB) -> None:
        """Sanity: debt_item.resolved is done-category and must still match status='done'."""
        debt = collision_db.create_issue(
            "debt",
            type="debt_item",
            fields={
                "debt_category": "code",
                "impact": "low",
                "remediation_plan": "rewrite",
                "rationale": "ok",
            },
        )
        # debt_item: identified -> assessed -> scheduled -> remediating -> resolved
        for nxt in ("assessed", "scheduled", "remediating", "resolved"):
            collision_db.update_issue(debt.id, status=nxt)
        done = collision_db.list_issues(status="done")
        assert any(i.id == debt.id for i in done)

    def test_debt_resolved_not_in_wip_filter(self, collision_db: FiligreeDB) -> None:
        debt = collision_db.create_issue(
            "debt",
            type="debt_item",
            fields={
                "debt_category": "code",
                "impact": "low",
                "remediation_plan": "rewrite",
                "rationale": "ok",
            },
        )
        for nxt in ("assessed", "scheduled", "remediating", "resolved"):
            collision_db.update_issue(debt.id, status=nxt)
        wip = collision_db.list_issues(status="wip")
        assert all(i.id != debt.id for i in wip), "debt_item.resolved is done, must not match wip"


class TestBlockerSemanticsRespectType:
    def test_task_blocked_by_incident_resolved_is_not_ready(self, collision_db: FiligreeDB) -> None:
        """An incident in 'resolved' is still wip — a task blocked by it must remain blocked."""
        inc = collision_db.create_issue("blocking incident", type="incident")
        task = collision_db.create_issue("dependent task")
        collision_db.add_dependency(task.id, inc.id)
        # incident: reported -> triaging requires `severity`.
        collision_db.update_issue(inc.id, status="triaging", fields={"severity": "sev3"})
        for nxt in ("investigating", "mitigating", "resolved"):
            collision_db.update_issue(inc.id, status=nxt)

        ready_ids = {i.id for i in collision_db.get_ready()}
        assert task.id not in ready_ids
        blocked_ids = {i.id for i in collision_db.get_blocked()}
        assert task.id in blocked_ids

        hydrated = collision_db.get_issue(task.id)
        assert hydrated.blocked_by == [inc.id]
        assert hydrated.is_ready is False

    def test_task_blocked_by_debt_resolved_is_ready(self, collision_db: FiligreeDB) -> None:
        """Sanity: debt_item.resolved IS done — its dependents must become ready."""
        debt = collision_db.create_issue(
            "blocking debt",
            type="debt_item",
            fields={
                "debt_category": "code",
                "impact": "low",
                "remediation_plan": "rewrite",
                "rationale": "ok",
            },
        )
        task = collision_db.create_issue("dependent task")
        collision_db.add_dependency(task.id, debt.id)
        for nxt in ("assessed", "scheduled", "remediating", "resolved"):
            collision_db.update_issue(debt.id, status=nxt)

        ready_ids = {i.id for i in collision_db.get_ready()}
        assert task.id in ready_ids
        hydrated = collision_db.get_issue(task.id)
        assert hydrated.blocked_by == []
        assert hydrated.is_ready is True

    def test_archived_blocker_still_treated_as_done(self, collision_db: FiligreeDB) -> None:
        """Regression guard: synthetic 'archived' status must continue to count as done in blocker queries."""
        blocker = collision_db.create_issue("blocker")
        task = collision_db.create_issue("dependent")
        collision_db.add_dependency(task.id, blocker.id)
        collision_db.close_issue(blocker.id)
        # archive_closed flips closed -> archived
        collision_db.archive_closed()

        ready_ids = {i.id for i in collision_db.get_ready()}
        assert task.id in ready_ids
        hydrated = collision_db.get_issue(task.id)
        assert hydrated.blocked_by == []

    def test_has_blockers_label_respects_type(self, collision_db: FiligreeDB) -> None:
        """has:blockers virtual label must agree with get_ready/get_blocked semantics."""
        inc = collision_db.create_issue("blocking incident", type="incident")
        task = collision_db.create_issue("dependent task")
        collision_db.add_dependency(task.id, inc.id)
        # incident: reported -> triaging requires `severity`.
        collision_db.update_issue(inc.id, status="triaging", fields={"severity": "sev3"})
        for nxt in ("investigating", "mitigating", "resolved"):
            collision_db.update_issue(inc.id, status=nxt)

        with_blockers = {i.id for i in collision_db.list_issues(label="has:blockers")}
        assert task.id in with_blockers
