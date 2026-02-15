# tests/test_e2e_workflows.py
"""End-to-end workflow tests for all workflow template packs.

These tests exercise the full FiligreeDB lifecycle with real templates loaded,
verifying that state machines, hard/soft enforcement, and field gates work
correctly through the database layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from filigree.core import FiligreeDB


@pytest.fixture
def db(tmp_path: Path) -> FiligreeDB:
    """FiligreeDB with core + risk + spike packs enabled."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "risk", "spike"]}
    (filigree_dir / "config.json").write_text(json.dumps(config))

    d = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
    d.initialize()
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Risk workflow E2E
# ---------------------------------------------------------------------------


class TestRiskWorkflowE2E:
    """Full lifecycle tests for risk type."""

    def test_full_mitigation_lifecycle(self, db: FiligreeDB) -> None:
        """identified -> assessing -> assessed -> mitigating -> mitigated."""
        risk = db.create_issue("Data loss risk", type="risk")
        assert risk.status == "identified"

        risk = db.update_issue(risk.id, status="assessing")
        assert risk.status == "assessing"

        risk = db.update_issue(
            risk.id,
            status="assessed",
            fields={"risk_score": "15", "impact": "Complete data loss for affected users"},
        )
        assert risk.status == "assessed"

        risk = db.update_issue(risk.id, status="mitigating")
        assert risk.status == "mitigating"

        risk = db.update_issue(risk.id, status="mitigated")
        assert risk.status == "mitigated"
        assert risk.closed_at is not None

    def test_assessment_hard_gate_blocks_without_fields(self, db: FiligreeDB) -> None:
        """Cannot transition assessing -> assessed without risk_score + impact."""
        risk = db.create_issue("Risk", type="risk")
        db.update_issue(risk.id, status="assessing")

        with pytest.raises(ValueError, match=r"risk_score|impact"):
            db.update_issue(risk.id, status="assessed")

    def test_assessment_hard_gate_blocks_partial_fields(self, db: FiligreeDB) -> None:
        """Only risk_score without impact should still block."""
        risk = db.create_issue("Risk", type="risk")
        db.update_issue(risk.id, status="assessing")

        with pytest.raises(ValueError, match="impact"):
            db.update_issue(risk.id, status="assessed", fields={"risk_score": "10"})

    def test_acceptance_hard_gate_blocks_without_fields(self, db: FiligreeDB) -> None:
        """Cannot accept without risk_owner + acceptance_rationale."""
        risk = db.create_issue("Risk", type="risk")
        db.update_issue(risk.id, status="assessing")
        db.update_issue(risk.id, status="assessed", fields={"risk_score": "5", "impact": "Low impact"})

        with pytest.raises(ValueError, match=r"risk_owner|acceptance_rationale"):
            db.update_issue(risk.id, status="accepted")

    def test_acceptance_with_fields_succeeds(self, db: FiligreeDB) -> None:
        """Acceptance with required fields transitions correctly."""
        risk = db.create_issue("Risk", type="risk")
        db.update_issue(risk.id, status="assessing")
        db.update_issue(risk.id, status="assessed", fields={"risk_score": "3", "impact": "Minor"})

        risk = db.update_issue(
            risk.id,
            status="accepted",
            fields={"risk_owner": "alice", "acceptance_rationale": "Cost of mitigation exceeds impact"},
        )
        assert risk.status == "accepted"
        assert risk.closed_at is not None

    def test_retire_from_identified(self, db: FiligreeDB) -> None:
        """Early exit: identified -> retired."""
        risk = db.create_issue("Obsolete risk", type="risk")
        risk = db.update_issue(risk.id, status="retired")
        assert risk.status == "retired"
        assert risk.closed_at is not None

    def test_escalate_then_mitigate(self, db: FiligreeDB) -> None:
        """assessed -> escalated -> mitigating -> mitigated."""
        risk = db.create_issue("Escalated risk", type="risk")
        db.update_issue(risk.id, status="assessing")
        db.update_issue(risk.id, status="assessed", fields={"risk_score": "20", "impact": "Critical"})
        db.update_issue(risk.id, status="escalated")
        db.update_issue(risk.id, status="mitigating")
        risk = db.update_issue(risk.id, status="mitigated")
        assert risk.status == "mitigated"


class TestMitigationWorkflowE2E:
    """Full lifecycle tests for mitigation type."""

    def test_full_mitigation_lifecycle(self, db: FiligreeDB) -> None:
        """planned -> in_progress -> completed."""
        mit = db.create_issue("Add backups", type="mitigation")
        assert mit.status == "planned"

        mit = db.update_issue(mit.id, status="in_progress")
        assert mit.status == "in_progress"

        mit = db.update_issue(mit.id, status="completed")
        assert mit.status == "completed"
        assert mit.closed_at is not None

    def test_ineffective_replan_loop(self, db: FiligreeDB) -> None:
        """planned -> in_progress -> ineffective -> planned (replan)."""
        mit = db.create_issue("First attempt", type="mitigation")
        db.update_issue(mit.id, status="in_progress")
        db.update_issue(mit.id, status="ineffective", fields={"outcome": "Did not reduce risk"})
        mit = db.update_issue(mit.id, status="planned")
        assert mit.status == "planned"
        # Can restart
        mit = db.update_issue(mit.id, status="in_progress")
        assert mit.status == "in_progress"

    def test_cancel_from_planned(self, db: FiligreeDB) -> None:
        """planned -> cancelled."""
        mit = db.create_issue("Cancelled mitigation", type="mitigation")
        mit = db.update_issue(mit.id, status="cancelled")
        assert mit.status == "cancelled"
        assert mit.closed_at is not None

    def test_cancel_from_in_progress(self, db: FiligreeDB) -> None:
        """in_progress -> cancelled."""
        mit = db.create_issue("Cancelled mitigation", type="mitigation")
        db.update_issue(mit.id, status="in_progress")
        mit = db.update_issue(mit.id, status="cancelled")
        assert mit.status == "cancelled"

    def test_mitigation_as_child_of_risk(self, db: FiligreeDB) -> None:
        """Mitigation linked to risk via parent_id."""
        risk = db.create_issue("Parent risk", type="risk")
        mit = db.create_issue("Mitigation action", type="mitigation", parent_id=risk.id)
        assert mit.parent_id == risk.id

        # Verify hierarchy
        children = db.list_issues(parent_id=risk.id)
        assert any(c.id == mit.id for c in children)


# ---------------------------------------------------------------------------
# Spike workflow E2E
# ---------------------------------------------------------------------------


class TestSpikeWorkflowE2E:
    """Full lifecycle tests for spike type."""

    def test_full_lifecycle(self, db: FiligreeDB) -> None:
        """proposed -> investigating -> concluded -> actioned."""
        spike = db.create_issue("Investigate caching options", type="spike")
        assert spike.status == "proposed"

        spike = db.update_issue(spike.id, status="investigating")
        assert spike.status == "investigating"

        spike = db.update_issue(
            spike.id,
            status="concluded",
            fields={"findings": "Redis outperforms memcached for our use case"},
        )
        assert spike.status == "concluded"
        assert spike.closed_at is not None

        spike = db.update_issue(
            spike.id,
            status="actioned",
            fields={"recommendation": "Adopt Redis with 24h TTL"},
        )
        assert spike.status == "actioned"

    def test_conclusion_hard_gate_blocks_without_findings(self, db: FiligreeDB) -> None:
        """Cannot conclude without findings."""
        spike = db.create_issue("Spike", type="spike")
        db.update_issue(spike.id, status="investigating")

        with pytest.raises(ValueError, match="findings"):
            db.update_issue(spike.id, status="concluded")

    def test_conclusion_with_findings_succeeds(self, db: FiligreeDB) -> None:
        """Conclusion with findings transitions correctly."""
        spike = db.create_issue("Spike", type="spike")
        db.update_issue(spike.id, status="investigating")

        spike = db.update_issue(
            spike.id,
            status="concluded",
            fields={"findings": "The approach is feasible with minor modifications"},
        )
        assert spike.status == "concluded"

    def test_abandon_from_investigating(self, db: FiligreeDB) -> None:
        """investigating -> abandoned (soft, no findings required)."""
        spike = db.create_issue("Abandoned spike", type="spike")
        db.update_issue(spike.id, status="investigating")

        spike = db.update_issue(spike.id, status="abandoned")
        assert spike.status == "abandoned"
        assert spike.closed_at is not None

    def test_abandon_from_proposed(self, db: FiligreeDB) -> None:
        """proposed -> abandoned."""
        spike = db.create_issue("Never started", type="spike")
        spike = db.update_issue(spike.id, status="abandoned")
        assert spike.status == "abandoned"
        assert spike.closed_at is not None

    def test_spike_with_fields(self, db: FiligreeDB) -> None:
        """Verify field storage through the lifecycle."""
        spike = db.create_issue(
            "Cache spike",
            type="spike",
            fields={"hypothesis": "Redis is faster than SQLite for hot data", "time_box": "2 days"},
        )
        assert spike.fields["hypothesis"] == "Redis is faster than SQLite for hot data"
        assert spike.fields["time_box"] == "2 days"


class TestFindingWorkflowE2E:
    """Full lifecycle tests for finding type."""

    def test_draft_to_published(self, db: FiligreeDB) -> None:
        """draft -> published."""
        finding = db.create_issue("Redis benchmark results", type="finding")
        assert finding.status == "draft"

        finding = db.update_issue(finding.id, status="published")
        assert finding.status == "published"
        assert finding.closed_at is not None

    def test_finding_as_child_of_spike(self, db: FiligreeDB) -> None:
        """Finding linked to spike via parent_id."""
        spike = db.create_issue("Cache spike", type="spike")
        finding = db.create_issue(
            "Redis latency is 2ms p99",
            type="finding",
            parent_id=spike.id,
            fields={"summary": "Redis p99 latency is 2ms under load", "evidence": "Benchmark data attached"},
        )
        assert finding.parent_id == spike.id
        assert finding.fields["summary"] == "Redis p99 latency is 2ms under load"

    def test_multiple_findings_per_spike(self, db: FiligreeDB) -> None:
        """Multiple findings can belong to the same spike."""
        spike = db.create_issue("Investigation", type="spike")
        f1 = db.create_issue("Finding 1", type="finding", parent_id=spike.id)
        f2 = db.create_issue("Finding 2", type="finding", parent_id=spike.id)
        f3 = db.create_issue("Finding 3", type="finding", parent_id=spike.id)

        children = db.list_issues(parent_id=spike.id)
        child_ids = {c.id for c in children}
        assert {f1.id, f2.id, f3.id} == child_ids


# ---------------------------------------------------------------------------
# Cross-pack E2E
# ---------------------------------------------------------------------------


class TestCrossPackE2E:
    """Test cross-pack relationships through the database layer."""

    def test_spike_investigates_risk_via_dependency(self, db: FiligreeDB) -> None:
        """Spike can block on a risk via dependency (investigation link)."""
        risk = db.create_issue("Security risk", type="risk")
        spike = db.create_issue("Investigate security risk", type="spike")
        db.add_dependency(spike.id, risk.id)

        spike_issue = db.get_issue(spike.id)
        assert risk.id in spike_issue.blocked_by

    def test_spike_spawns_task_via_dependency(self, db: FiligreeDB) -> None:
        """Spike findings can spawn core pack tasks via dependency."""
        spike = db.create_issue("Cache spike", type="spike")
        task = db.create_issue("Implement Redis cache", type="task")
        db.add_dependency(task.id, spike.id)

        task_issue = db.get_issue(task.id)
        assert spike.id in task_issue.blocked_by


# ---------------------------------------------------------------------------
# Fixtures for Tier 3 packs
# ---------------------------------------------------------------------------


@pytest.fixture
def req_db(tmp_path: Path) -> FiligreeDB:
    """FiligreeDB with core + requirements packs enabled."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "requirements"]}
    (filigree_dir / "config.json").write_text(json.dumps(config))
    d = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
    d.initialize()
    yield d
    d.close()


@pytest.fixture
def roadmap_db(tmp_path: Path) -> FiligreeDB:
    """FiligreeDB with core + planning + roadmap packs enabled."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning", "roadmap"]}
    (filigree_dir / "config.json").write_text(json.dumps(config))
    d = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
    d.initialize()
    yield d
    d.close()


@pytest.fixture
def incident_db(tmp_path: Path) -> FiligreeDB:
    """FiligreeDB with core + incident packs enabled."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "incident"]}
    (filigree_dir / "config.json").write_text(json.dumps(config))
    d = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
    d.initialize()
    yield d
    d.close()


@pytest.fixture
def debt_db(tmp_path: Path) -> FiligreeDB:
    """FiligreeDB with core + debt packs enabled."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "debt"]}
    (filigree_dir / "config.json").write_text(json.dumps(config))
    d = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
    d.initialize()
    yield d
    d.close()


@pytest.fixture
def release_db(tmp_path: Path) -> FiligreeDB:
    """FiligreeDB with core + planning + release packs enabled."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning", "release"]}
    (filigree_dir / "config.json").write_text(json.dumps(config))
    d = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
    d.initialize()
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Requirements workflow E2E
# ---------------------------------------------------------------------------


class TestRequirementWorkflowE2E:
    """Full lifecycle tests for requirement type."""

    def test_full_verification_lifecycle(self, req_db: FiligreeDB) -> None:
        """drafted -> reviewing -> approved -> implementing -> verified."""
        req = req_db.create_issue("User login must use MFA", type="requirement")
        assert req.status == "drafted"

        req = req_db.update_issue(req.id, status="reviewing")
        assert req.status == "reviewing"

        req = req_db.update_issue(req.id, status="approved")
        assert req.status == "approved"

        req = req_db.update_issue(req.id, status="implementing")
        assert req.status == "implementing"

        req = req_db.update_issue(
            req.id,
            status="verified",
            fields={"verification_method": "test"},
        )
        assert req.status == "verified"
        assert req.closed_at is not None

    def test_verification_hard_gate_blocks_without_method(self, req_db: FiligreeDB) -> None:
        """Cannot verify without verification_method."""
        req = req_db.create_issue("Req", type="requirement")
        req_db.update_issue(req.id, status="reviewing")
        req_db.update_issue(req.id, status="approved")
        req_db.update_issue(req.id, status="implementing")

        with pytest.raises(ValueError, match="verification_method"):
            req_db.update_issue(req.id, status="verified")

    def test_verification_with_method_succeeds(self, req_db: FiligreeDB) -> None:
        """Verification with method transitions correctly."""
        req = req_db.create_issue("Req", type="requirement")
        req_db.update_issue(req.id, status="reviewing")
        req_db.update_issue(req.id, status="approved")
        req_db.update_issue(req.id, status="implementing")

        req = req_db.update_issue(req.id, status="verified", fields={"verification_method": "demonstration"})
        assert req.status == "verified"

    def test_rejected_from_reviewing(self, req_db: FiligreeDB) -> None:
        """reviewing -> rejected."""
        req = req_db.create_issue("Bad requirement", type="requirement")
        req_db.update_issue(req.id, status="reviewing")
        req = req_db.update_issue(req.id, status="rejected")
        assert req.status == "rejected"
        assert req.closed_at is not None

    def test_rejected_from_drafted(self, req_db: FiligreeDB) -> None:
        """drafted -> rejected (early exit)."""
        req = req_db.create_issue("Premature req", type="requirement")
        req = req_db.update_issue(req.id, status="rejected")
        assert req.status == "rejected"

    def test_deferred_from_drafted(self, req_db: FiligreeDB) -> None:
        """drafted -> deferred."""
        req = req_db.create_issue("Later req", type="requirement")
        req = req_db.update_issue(req.id, status="deferred")
        assert req.status == "deferred"
        assert req.closed_at is not None

    def test_deferred_from_approved(self, req_db: FiligreeDB) -> None:
        """approved -> deferred."""
        req = req_db.create_issue("Deprioritized req", type="requirement")
        req_db.update_issue(req.id, status="reviewing")
        req_db.update_issue(req.id, status="approved")
        req = req_db.update_issue(req.id, status="deferred")
        assert req.status == "deferred"

    def test_send_back_to_draft_from_reviewing(self, req_db: FiligreeDB) -> None:
        """reviewing -> drafted (rework)."""
        req = req_db.create_issue("Needs rework", type="requirement")
        req_db.update_issue(req.id, status="reviewing")
        req = req_db.update_issue(req.id, status="drafted")
        assert req.status == "drafted"

    def test_send_back_to_draft_from_implementing(self, req_db: FiligreeDB) -> None:
        """implementing -> drafted (rewrite)."""
        req = req_db.create_issue("Req needing rewrite", type="requirement")
        req_db.update_issue(req.id, status="reviewing")
        req_db.update_issue(req.id, status="approved")
        req_db.update_issue(req.id, status="implementing")
        req = req_db.update_issue(req.id, status="drafted")
        assert req.status == "drafted"

    def test_requirement_with_fields(self, req_db: FiligreeDB) -> None:
        """Verify field storage."""
        req = req_db.create_issue(
            "Performance SLA",
            type="requirement",
            fields={"req_type": "non_functional", "stakeholder": "SRE team"},
        )
        assert req.fields["req_type"] == "non_functional"
        assert req.fields["stakeholder"] == "SRE team"


class TestAcceptanceCriterionWorkflowE2E:
    """Full lifecycle tests for acceptance_criterion type."""

    def test_draft_to_validated(self, req_db: FiligreeDB) -> None:
        """draft -> validated."""
        ac = req_db.create_issue("Login redirects to dashboard", type="acceptance_criterion")
        assert ac.status == "draft"

        ac = req_db.update_issue(ac.id, status="validated")
        assert ac.status == "validated"
        assert ac.closed_at is not None

    def test_criterion_as_child_of_requirement(self, req_db: FiligreeDB) -> None:
        """Acceptance criterion linked to requirement via parent_id."""
        req = req_db.create_issue("Login requirement", type="requirement")
        ac = req_db.create_issue(
            "Redirects after login",
            type="acceptance_criterion",
            parent_id=req.id,
            fields={
                "given": "User is on login page",
                "when": "Valid credentials submitted",
                "then": "Redirect to /dashboard",
            },
        )
        assert ac.parent_id == req.id
        assert ac.fields["given"] == "User is on login page"


# ---------------------------------------------------------------------------
# Roadmap workflow E2E
# ---------------------------------------------------------------------------


class TestThemeWorkflowE2E:
    """Full lifecycle tests for theme type."""

    def test_full_lifecycle(self, roadmap_db: FiligreeDB) -> None:
        """proposed -> active -> achieved."""
        theme = roadmap_db.create_issue("Platform reliability", type="theme")
        assert theme.status == "proposed"

        theme = roadmap_db.update_issue(theme.id, status="active")
        assert theme.status == "active"

        theme = roadmap_db.update_issue(theme.id, status="achieved")
        assert theme.status == "achieved"
        assert theme.closed_at is not None

    def test_sunset_from_proposed(self, roadmap_db: FiligreeDB) -> None:
        """proposed -> sunset."""
        theme = roadmap_db.create_issue("Deprecated initiative", type="theme")
        theme = roadmap_db.update_issue(theme.id, status="sunset")
        assert theme.status == "sunset"
        assert theme.closed_at is not None

    def test_sunset_from_active(self, roadmap_db: FiligreeDB) -> None:
        """active -> sunset."""
        theme = roadmap_db.create_issue("Pivoted away", type="theme")
        roadmap_db.update_issue(theme.id, status="active")
        theme = roadmap_db.update_issue(theme.id, status="sunset")
        assert theme.status == "sunset"


class TestObjectiveWorkflowE2E:
    """Full lifecycle tests for objective type."""

    def test_full_lifecycle(self, roadmap_db: FiligreeDB) -> None:
        """defined -> pursuing -> achieved."""
        obj = roadmap_db.create_issue("Reduce p99 latency below 100ms", type="objective")
        assert obj.status == "defined"

        obj = roadmap_db.update_issue(obj.id, status="pursuing")
        assert obj.status == "pursuing"

        obj = roadmap_db.update_issue(obj.id, status="achieved")
        assert obj.status == "achieved"
        assert obj.closed_at is not None

    def test_dropped_from_defined(self, roadmap_db: FiligreeDB) -> None:
        """defined -> dropped."""
        obj = roadmap_db.create_issue("Cancelled objective", type="objective")
        obj = roadmap_db.update_issue(obj.id, status="dropped")
        assert obj.status == "dropped"
        assert obj.closed_at is not None

    def test_dropped_from_pursuing(self, roadmap_db: FiligreeDB) -> None:
        """pursuing -> dropped."""
        obj = roadmap_db.create_issue("Deprioritized objective", type="objective")
        roadmap_db.update_issue(obj.id, status="pursuing")
        obj = roadmap_db.update_issue(obj.id, status="dropped")
        assert obj.status == "dropped"

    def test_objective_as_child_of_theme(self, roadmap_db: FiligreeDB) -> None:
        """Objective linked to theme via parent_id."""
        theme = roadmap_db.create_issue("Platform reliability", type="theme")
        obj = roadmap_db.create_issue("99.9% uptime", type="objective", parent_id=theme.id)
        assert obj.parent_id == theme.id


class TestKeyResultWorkflowE2E:
    """Full lifecycle tests for key_result type."""

    def test_full_lifecycle_met(self, roadmap_db: FiligreeDB) -> None:
        """defined -> tracking -> met."""
        kr = roadmap_db.create_issue(
            "p99 latency < 100ms",
            type="key_result",
            fields={"target_value": "100", "unit": "ms", "baseline": "250"},
        )
        assert kr.status == "defined"

        kr = roadmap_db.update_issue(kr.id, status="tracking")
        assert kr.status == "tracking"

        kr = roadmap_db.update_issue(kr.id, status="met", fields={"current_value": "85"})
        assert kr.status == "met"
        assert kr.closed_at is not None

    def test_met_hard_gate_blocks_without_current_value(self, roadmap_db: FiligreeDB) -> None:
        """Cannot mark met without current_value."""
        kr = roadmap_db.create_issue("KR", type="key_result")
        roadmap_db.update_issue(kr.id, status="tracking")

        with pytest.raises(ValueError, match="current_value"):
            roadmap_db.update_issue(kr.id, status="met")

    def test_missed_hard_gate_blocks_without_current_value(self, roadmap_db: FiligreeDB) -> None:
        """Cannot mark missed without current_value."""
        kr = roadmap_db.create_issue("KR", type="key_result")
        roadmap_db.update_issue(kr.id, status="tracking")

        with pytest.raises(ValueError, match="current_value"):
            roadmap_db.update_issue(kr.id, status="missed")

    def test_missed_with_current_value(self, roadmap_db: FiligreeDB) -> None:
        """tracking -> missed with current_value succeeds."""
        kr = roadmap_db.create_issue("KR", type="key_result")
        roadmap_db.update_issue(kr.id, status="tracking")

        kr = roadmap_db.update_issue(kr.id, status="missed", fields={"current_value": "150"})
        assert kr.status == "missed"
        assert kr.closed_at is not None

    def test_key_result_as_child_of_objective(self, roadmap_db: FiligreeDB) -> None:
        """Key result linked to objective via parent_id."""
        obj = roadmap_db.create_issue("Reduce latency", type="objective")
        kr = roadmap_db.create_issue("p99 < 100ms", type="key_result", parent_id=obj.id)
        assert kr.parent_id == obj.id

    def test_roadmap_hierarchy(self, roadmap_db: FiligreeDB) -> None:
        """theme -> objective -> key_result hierarchy."""
        theme = roadmap_db.create_issue("Performance", type="theme")
        obj = roadmap_db.create_issue("Fast API", type="objective", parent_id=theme.id)
        kr = roadmap_db.create_issue("p99 < 50ms", type="key_result", parent_id=obj.id)

        theme_children = roadmap_db.list_issues(parent_id=theme.id)
        assert any(c.id == obj.id for c in theme_children)

        obj_children = roadmap_db.list_issues(parent_id=obj.id)
        assert any(c.id == kr.id for c in obj_children)


# ---------------------------------------------------------------------------
# Incident workflow E2E
# ---------------------------------------------------------------------------


class TestIncidentWorkflowE2E:
    """Full lifecycle tests for incident type."""

    def test_full_lifecycle(self, incident_db: FiligreeDB) -> None:
        """reported -> triaging -> investigating -> mitigating -> resolved -> closed."""
        inc = incident_db.create_issue("API outage", type="incident")
        assert inc.status == "reported"

        inc = incident_db.update_issue(inc.id, status="triaging", fields={"severity": "sev1"})
        assert inc.status == "triaging"

        inc = incident_db.update_issue(inc.id, status="investigating")
        assert inc.status == "investigating"

        inc = incident_db.update_issue(inc.id, status="mitigating")
        assert inc.status == "mitigating"

        inc = incident_db.update_issue(inc.id, status="resolved")
        assert inc.status == "resolved"
        assert inc.closed_at is not None

        inc = incident_db.update_issue(
            inc.id, status="closed", fields={"root_cause": "Database connection pool exhaustion"}
        )
        assert inc.status == "closed"

    def test_triage_hard_gate_blocks_without_severity(self, incident_db: FiligreeDB) -> None:
        """Cannot triage without severity."""
        inc = incident_db.create_issue("Incident", type="incident")

        with pytest.raises(ValueError, match="severity"):
            incident_db.update_issue(inc.id, status="triaging")

    def test_triage_with_severity_succeeds(self, incident_db: FiligreeDB) -> None:
        """Triage with severity transitions correctly."""
        inc = incident_db.create_issue("Incident", type="incident")
        inc = incident_db.update_issue(inc.id, status="triaging", fields={"severity": "sev2"})
        assert inc.status == "triaging"

    def test_close_hard_gate_blocks_without_root_cause(self, incident_db: FiligreeDB) -> None:
        """Cannot close without root_cause."""
        inc = incident_db.create_issue("Incident", type="incident")
        incident_db.update_issue(inc.id, status="triaging", fields={"severity": "sev3"})
        incident_db.update_issue(inc.id, status="investigating")
        incident_db.update_issue(inc.id, status="resolved")

        with pytest.raises(ValueError, match="root_cause"):
            incident_db.update_issue(inc.id, status="closed")

    def test_close_with_root_cause_succeeds(self, incident_db: FiligreeDB) -> None:
        """Close with root_cause transitions correctly."""
        inc = incident_db.create_issue("Incident", type="incident")
        incident_db.update_issue(inc.id, status="triaging", fields={"severity": "sev4"})
        incident_db.update_issue(inc.id, status="investigating")
        incident_db.update_issue(inc.id, status="resolved")

        inc = incident_db.update_issue(inc.id, status="closed", fields={"root_cause": "Misconfigured timeout"})
        assert inc.status == "closed"

    def test_direct_investigating_to_resolved(self, incident_db: FiligreeDB) -> None:
        """investigating -> resolved (skip mitigation phase)."""
        inc = incident_db.create_issue("Quick fix", type="incident")
        incident_db.update_issue(inc.id, status="triaging", fields={"severity": "sev3"})
        incident_db.update_issue(inc.id, status="investigating")
        inc = incident_db.update_issue(inc.id, status="resolved")
        assert inc.status == "resolved"

    def test_incident_with_fields(self, incident_db: FiligreeDB) -> None:
        """Verify field storage through lifecycle."""
        inc = incident_db.create_issue(
            "Database outage",
            type="incident",
            fields={"impact_scope": "All EU users", "detection_method": "Automated monitoring"},
        )
        assert inc.fields["impact_scope"] == "All EU users"
        assert inc.fields["detection_method"] == "Automated monitoring"


class TestPostmortemWorkflowE2E:
    """Full lifecycle tests for postmortem type."""

    def test_full_lifecycle(self, incident_db: FiligreeDB) -> None:
        """drafting -> reviewing -> published."""
        pm = incident_db.create_issue("Q1 outage postmortem", type="postmortem")
        assert pm.status == "drafting"

        pm = incident_db.update_issue(pm.id, status="reviewing")
        assert pm.status == "reviewing"

        pm = incident_db.update_issue(
            pm.id,
            status="published",
            fields={"action_items": "1. Add circuit breaker\n2. Increase pool size"},
        )
        assert pm.status == "published"
        assert pm.closed_at is not None

    def test_publish_hard_gate_blocks_without_action_items(self, incident_db: FiligreeDB) -> None:
        """Cannot publish without action_items."""
        pm = incident_db.create_issue("PM", type="postmortem")
        incident_db.update_issue(pm.id, status="reviewing")

        with pytest.raises(ValueError, match="action_items"):
            incident_db.update_issue(pm.id, status="published")

    def test_publish_with_action_items_succeeds(self, incident_db: FiligreeDB) -> None:
        """Publish with action_items transitions correctly."""
        pm = incident_db.create_issue("PM", type="postmortem")
        incident_db.update_issue(pm.id, status="reviewing")

        pm = incident_db.update_issue(pm.id, status="published", fields={"action_items": "Fix the thing"})
        assert pm.status == "published"

    def test_send_back_to_drafting(self, incident_db: FiligreeDB) -> None:
        """reviewing -> drafting (needs more work)."""
        pm = incident_db.create_issue("PM", type="postmortem")
        incident_db.update_issue(pm.id, status="reviewing")
        pm = incident_db.update_issue(pm.id, status="drafting")
        assert pm.status == "drafting"

    def test_postmortem_as_child_of_incident(self, incident_db: FiligreeDB) -> None:
        """Postmortem linked to incident via parent_id."""
        inc = incident_db.create_issue("Outage", type="incident")
        pm = incident_db.create_issue("Outage postmortem", type="postmortem", parent_id=inc.id)
        assert pm.parent_id == inc.id

        children = incident_db.list_issues(parent_id=inc.id)
        assert any(c.id == pm.id for c in children)


# ---------------------------------------------------------------------------
# Debt workflow E2E
# ---------------------------------------------------------------------------


class TestDebtItemWorkflowE2E:
    """Full lifecycle tests for debt_item type."""

    def test_full_remediation_lifecycle(self, debt_db: FiligreeDB) -> None:
        """identified -> assessed -> scheduled -> remediating -> resolved."""
        debt = debt_db.create_issue("Monolithic auth module", type="debt_item")
        assert debt.status == "identified"

        debt = debt_db.update_issue(
            debt.id,
            status="assessed",
            fields={"debt_category": "architecture", "impact": "high"},
        )
        assert debt.status == "assessed"

        debt = debt_db.update_issue(debt.id, status="scheduled")
        assert debt.status == "scheduled"

        debt = debt_db.update_issue(debt.id, status="remediating")
        assert debt.status == "remediating"

        debt = debt_db.update_issue(debt.id, status="resolved")
        assert debt.status == "resolved"
        assert debt.closed_at is not None

    def test_assessment_hard_gate_blocks_without_fields(self, debt_db: FiligreeDB) -> None:
        """Cannot assess without debt_category and impact."""
        debt = debt_db.create_issue("Debt", type="debt_item")

        with pytest.raises(ValueError, match=r"debt_category|impact"):
            debt_db.update_issue(debt.id, status="assessed")

    def test_assessment_hard_gate_blocks_partial_fields(self, debt_db: FiligreeDB) -> None:
        """Only debt_category without impact should still block."""
        debt = debt_db.create_issue("Debt", type="debt_item")

        with pytest.raises(ValueError, match="impact"):
            debt_db.update_issue(debt.id, status="assessed", fields={"debt_category": "code"})

    def test_assessment_with_fields_succeeds(self, debt_db: FiligreeDB) -> None:
        """Assessment with required fields transitions correctly."""
        debt = debt_db.create_issue("Debt", type="debt_item")
        debt = debt_db.update_issue(debt.id, status="assessed", fields={"debt_category": "test", "impact": "medium"})
        assert debt.status == "assessed"

    def test_accepted_from_identified(self, debt_db: FiligreeDB) -> None:
        """identified -> accepted (live with it)."""
        debt = debt_db.create_issue("Acceptable debt", type="debt_item")
        debt = debt_db.update_issue(debt.id, status="accepted")
        assert debt.status == "accepted"
        assert debt.closed_at is not None

    def test_accepted_from_assessed(self, debt_db: FiligreeDB) -> None:
        """assessed -> accepted (decided not worth fixing)."""
        debt = debt_db.create_issue("Low-impact debt", type="debt_item")
        debt_db.update_issue(debt.id, status="assessed", fields={"debt_category": "documentation", "impact": "low"})
        debt = debt_db.update_issue(debt.id, status="accepted")
        assert debt.status == "accepted"

    def test_remediating_back_to_assessed(self, debt_db: FiligreeDB) -> None:
        """remediating -> assessed (approach failed, reassess)."""
        debt = debt_db.create_issue("Debt", type="debt_item")
        debt_db.update_issue(debt.id, status="assessed", fields={"debt_category": "code", "impact": "high"})
        debt_db.update_issue(debt.id, status="scheduled")
        debt_db.update_issue(debt.id, status="remediating")
        debt = debt_db.update_issue(debt.id, status="assessed")
        assert debt.status == "assessed"

    def test_debt_with_fields(self, debt_db: FiligreeDB) -> None:
        """Verify field storage."""
        debt = debt_db.create_issue(
            "Legacy auth",
            type="debt_item",
            fields={"code_location": "src/auth/legacy.py", "incurred_reason": "MVP shortcut"},
        )
        assert debt.fields["code_location"] == "src/auth/legacy.py"
        assert debt.fields["incurred_reason"] == "MVP shortcut"


class TestRemediationWorkflowE2E:
    """Full lifecycle tests for remediation type."""

    def test_full_lifecycle(self, debt_db: FiligreeDB) -> None:
        """planned -> in_progress -> completed."""
        rem = debt_db.create_issue("Refactor auth module", type="remediation")
        assert rem.status == "planned"

        rem = debt_db.update_issue(rem.id, status="in_progress")
        assert rem.status == "in_progress"

        rem = debt_db.update_issue(rem.id, status="completed")
        assert rem.status == "completed"
        assert rem.closed_at is not None

    def test_abandoned_from_planned(self, debt_db: FiligreeDB) -> None:
        """planned -> abandoned."""
        rem = debt_db.create_issue("Cancelled remediation", type="remediation")
        rem = debt_db.update_issue(rem.id, status="abandoned")
        assert rem.status == "abandoned"
        assert rem.closed_at is not None

    def test_abandoned_from_in_progress(self, debt_db: FiligreeDB) -> None:
        """in_progress -> abandoned."""
        rem = debt_db.create_issue("Stopped remediation", type="remediation")
        debt_db.update_issue(rem.id, status="in_progress")
        rem = debt_db.update_issue(rem.id, status="abandoned")
        assert rem.status == "abandoned"

    def test_remediation_as_child_of_debt(self, debt_db: FiligreeDB) -> None:
        """Remediation linked to debt_item via parent_id."""
        debt = debt_db.create_issue("Legacy code", type="debt_item")
        rem = debt_db.create_issue("Rewrite module", type="remediation", parent_id=debt.id)
        assert rem.parent_id == debt.id

        children = debt_db.list_issues(parent_id=debt.id)
        assert any(c.id == rem.id for c in children)


# ---------------------------------------------------------------------------
# Release workflow E2E
# ---------------------------------------------------------------------------


class TestReleaseWorkflowE2E:
    """Full lifecycle tests for release type."""

    def test_full_release_lifecycle(self, release_db: FiligreeDB) -> None:
        """planning -> development -> frozen -> testing -> staged -> released."""
        rel = release_db.create_issue("Q1 Release", type="release")
        assert rel.status == "planning"

        rel = release_db.update_issue(rel.id, status="development")
        assert rel.status == "development"

        rel = release_db.update_issue(rel.id, status="frozen", fields={"version": "v2.1.0"})
        assert rel.status == "frozen"

        rel = release_db.update_issue(rel.id, status="testing")
        assert rel.status == "testing"

        rel = release_db.update_issue(rel.id, status="staged")
        assert rel.status == "staged"

        rel = release_db.update_issue(rel.id, status="released")
        assert rel.status == "released"
        assert rel.closed_at is not None

    def test_freeze_hard_gate_blocks_without_version(self, release_db: FiligreeDB) -> None:
        """Cannot freeze without version."""
        rel = release_db.create_issue("Release", type="release")
        release_db.update_issue(rel.id, status="development")

        with pytest.raises(ValueError, match="version"):
            release_db.update_issue(rel.id, status="frozen")

    def test_freeze_with_version_succeeds(self, release_db: FiligreeDB) -> None:
        """Freeze with version transitions correctly."""
        rel = release_db.create_issue("Release", type="release")
        release_db.update_issue(rel.id, status="development")

        rel = release_db.update_issue(rel.id, status="frozen", fields={"version": "v1.0.0"})
        assert rel.status == "frozen"

    def test_rollback_from_released(self, release_db: FiligreeDB) -> None:
        """released -> rolled_back."""
        rel = release_db.create_issue("Bad release", type="release")
        release_db.update_issue(rel.id, status="development")
        release_db.update_issue(rel.id, status="frozen", fields={"version": "v1.2.0"})
        release_db.update_issue(rel.id, status="testing")
        release_db.update_issue(rel.id, status="staged")
        release_db.update_issue(rel.id, status="released")

        rel = release_db.update_issue(rel.id, status="rolled_back")
        assert rel.status == "rolled_back"

    def test_unfreeze_back_to_development(self, release_db: FiligreeDB) -> None:
        """frozen -> development (unfreeze)."""
        rel = release_db.create_issue("Release", type="release")
        release_db.update_issue(rel.id, status="development")
        release_db.update_issue(rel.id, status="frozen", fields={"version": "v1.0.0"})

        rel = release_db.update_issue(rel.id, status="development")
        assert rel.status == "development"

    def test_testing_back_to_development(self, release_db: FiligreeDB) -> None:
        """testing -> development (failed testing)."""
        rel = release_db.create_issue("Release", type="release")
        release_db.update_issue(rel.id, status="development")
        release_db.update_issue(rel.id, status="frozen", fields={"version": "v1.0.0"})
        release_db.update_issue(rel.id, status="testing")

        rel = release_db.update_issue(rel.id, status="development")
        assert rel.status == "development"

    def test_staged_back_to_development(self, release_db: FiligreeDB) -> None:
        """staged -> development (staging issue found)."""
        rel = release_db.create_issue("Release", type="release")
        release_db.update_issue(rel.id, status="development")
        release_db.update_issue(rel.id, status="frozen", fields={"version": "v1.0.0"})
        release_db.update_issue(rel.id, status="testing")
        release_db.update_issue(rel.id, status="staged")

        rel = release_db.update_issue(rel.id, status="development")
        assert rel.status == "development"

    def test_release_with_fields(self, release_db: FiligreeDB) -> None:
        """Verify field storage."""
        rel = release_db.create_issue(
            "v2.0",
            type="release",
            fields={"release_manager": "alice", "rollback_plan": "Revert to v1.9"},
        )
        assert rel.fields["release_manager"] == "alice"
        assert rel.fields["rollback_plan"] == "Revert to v1.9"


class TestReleaseItemWorkflowE2E:
    """Full lifecycle tests for release_item type."""

    def test_full_lifecycle(self, release_db: FiligreeDB) -> None:
        """queued -> included -> verified."""
        item = release_db.create_issue("Add user search", type="release_item")
        assert item.status == "queued"

        item = release_db.update_issue(item.id, status="included")
        assert item.status == "included"

        item = release_db.update_issue(item.id, status="verified")
        assert item.status == "verified"
        assert item.closed_at is not None

    def test_excluded_from_queued(self, release_db: FiligreeDB) -> None:
        """queued -> excluded."""
        item = release_db.create_issue("Dropped feature", type="release_item")
        item = release_db.update_issue(item.id, status="excluded")
        assert item.status == "excluded"
        assert item.closed_at is not None

    def test_excluded_from_included(self, release_db: FiligreeDB) -> None:
        """included -> excluded (pulled from release)."""
        item = release_db.create_issue("Risky feature", type="release_item")
        release_db.update_issue(item.id, status="included")
        item = release_db.update_issue(item.id, status="excluded")
        assert item.status == "excluded"

    def test_release_item_as_child_of_release(self, release_db: FiligreeDB) -> None:
        """Release item linked to release via parent_id."""
        rel = release_db.create_issue("v2.0 release", type="release")
        item = release_db.create_issue("Feature X", type="release_item", parent_id=rel.id)
        assert item.parent_id == rel.id

        children = release_db.list_issues(parent_id=rel.id)
        assert any(c.id == item.id for c in children)

    def test_multiple_items_in_release(self, release_db: FiligreeDB) -> None:
        """Multiple release items can belong to the same release."""
        rel = release_db.create_issue("v2.0", type="release")
        i1 = release_db.create_issue("Feature A", type="release_item", parent_id=rel.id)
        i2 = release_db.create_issue("Feature B", type="release_item", parent_id=rel.id)
        i3 = release_db.create_issue("Bugfix C", type="release_item", parent_id=rel.id)

        children = release_db.list_issues(parent_id=rel.id)
        child_ids = {c.id for c in children}
        assert {i1.id, i2.id, i3.id} == child_ids


# ---------------------------------------------------------------------------
# Cross-pack E2E (Tier 3)
# ---------------------------------------------------------------------------


class TestTier3CrossPackE2E:
    """Test cross-pack relationships for Tier 3 packs through the database layer."""

    def test_incident_spawns_tasks(self, incident_db: FiligreeDB) -> None:
        """Incident follow-up tasks via dependency."""
        inc = incident_db.create_issue("Outage", type="incident")
        task = incident_db.create_issue("Add monitoring", type="task")
        incident_db.add_dependency(task.id, inc.id)

        task_issue = incident_db.get_issue(task.id)
        assert inc.id in task_issue.blocked_by

    def test_postmortem_spawns_tasks(self, incident_db: FiligreeDB) -> None:
        """Postmortem action items become tasks via dependency."""
        pm = incident_db.create_issue("Outage PM", type="postmortem")
        task = incident_db.create_issue("Implement circuit breaker", type="task")
        incident_db.add_dependency(task.id, pm.id)

        task_issue = incident_db.get_issue(task.id)
        assert pm.id in task_issue.blocked_by

    def test_remediation_as_child_of_debt_item(self, debt_db: FiligreeDB) -> None:
        """Remediation belongs to debt_item via parent_id."""
        debt = debt_db.create_issue("Tech debt", type="debt_item")
        rem = debt_db.create_issue("Fix it", type="remediation", parent_id=debt.id)

        children = debt_db.list_issues(parent_id=debt.id)
        assert any(c.id == rem.id for c in children)

    def test_release_item_hierarchy(self, release_db: FiligreeDB) -> None:
        """Release -> release_item hierarchy works through FiligreeDB."""
        rel = release_db.create_issue("v3.0", type="release")
        item = release_db.create_issue("Big feature", type="release_item", parent_id=rel.id)

        children = release_db.list_issues(parent_id=rel.id)
        assert any(c.id == item.id for c in children)
