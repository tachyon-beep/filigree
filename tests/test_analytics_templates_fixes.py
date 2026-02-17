"""Tests for bug fixes in analytics, summary, templates, and templates_data.

Covers:
- Bug 1: Analytics timestamp/metric bugs (cycle_time reopen, _parse_iso, flow_metrics date)
- Bug 2: Summary timezone .replace() handling
- Bug 3: Summary WIP cap at 100
- Bug 4: Templates enforcement validation + shape crash handling
- Bug 5: rolled_back dead-end state in release pack
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from filigree.analytics import _parse_iso as analytics_parse_iso
from filigree.analytics import cycle_time, get_flow_metrics
from filigree.core import FiligreeDB
from filigree.summary import _parse_iso as summary_parse_iso
from filigree.summary import generate_summary
from filigree.templates import TemplateRegistry
from filigree.templates_data import BUILT_IN_PACKS

# ---------------------------------------------------------------------------
# Bug 1a: cycle_time with reopen -- should use first close, not last
# ---------------------------------------------------------------------------


class TestCycleTimeReopen:
    def test_cycle_time_uses_first_close(self, db: FiligreeDB) -> None:
        """Cycle time should measure first WIP to first done, not first WIP to last done."""
        issue = db.create_issue("Reopen test")
        db.update_issue(issue.id, status="in_progress")
        db.close_issue(issue.id)

        # Record cycle_time after first close
        ct_first = cycle_time(db, issue.id)
        assert ct_first is not None

        # Reopen and close again
        db.reopen_issue(issue.id)
        db.update_issue(issue.id, status="in_progress")
        # Backdate the second close to make it clearly different
        db.close_issue(issue.id)

        ct_after_reopen = cycle_time(db, issue.id)
        assert ct_after_reopen is not None
        # Should be the same as the first close (break after first done)
        assert ct_after_reopen == ct_first


# ---------------------------------------------------------------------------
# Bug 1b: _parse_iso returns None on failure
# ---------------------------------------------------------------------------


class TestAnalyticsParseIso:
    def test_garbage_returns_none(self) -> None:
        """Garbage strings should return None, not datetime.now()."""
        result = analytics_parse_iso("not-a-date")
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = analytics_parse_iso("")
        assert result is None

    def test_valid_iso_string(self) -> None:
        """Valid ISO string should return correct datetime."""
        result = analytics_parse_iso("2026-01-15T10:00:00+00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 15

    def test_naive_iso_string_gets_utc(self) -> None:
        """Naive ISO string should get UTC timezone attached."""
        result = analytics_parse_iso("2026-01-15T10:00:00")
        assert result is not None
        assert result.tzinfo is not None
        assert result.tzinfo == UTC

    def test_result_is_not_now(self) -> None:
        """On failure, should NOT return datetime.now()."""
        before = datetime.now(UTC)
        result = analytics_parse_iso("garbage")
        assert result is None
        # Verify it's truly None, not something close to now
        assert result != before


# ---------------------------------------------------------------------------
# Bug 1c: get_flow_metrics date comparison
# ---------------------------------------------------------------------------


class TestFlowMetricsDateComparison:
    def test_metrics_correctly_filter_by_cutoff_date(self, db: FiligreeDB) -> None:
        """Verify metrics use proper datetime comparison, not string comparison."""
        issue = db.create_issue("Recent close")
        db.update_issue(issue.id, status="in_progress")
        db.close_issue(issue.id)

        data = get_flow_metrics(db, days=30)
        assert data["throughput"] >= 1

    def test_metrics_exclude_old_issues(self, db: FiligreeDB) -> None:
        """Issues closed before the cutoff should be excluded."""
        issue = db.create_issue("Old close")
        db.update_issue(issue.id, status="in_progress")
        db.close_issue(issue.id)

        # Backdate the closed_at to 60 days ago
        old_ts = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        db.conn.execute("UPDATE issues SET closed_at = ? WHERE id = ?", (old_ts, issue.id))
        db.conn.commit()

        data = get_flow_metrics(db, days=30)
        assert data["throughput"] == 0


# ---------------------------------------------------------------------------
# Bug 2: Summary timezone handling
# ---------------------------------------------------------------------------


class TestSummaryTimezoneHandling:
    def test_naive_datetime_gets_utc(self) -> None:
        """Naive datetime should get UTC attached via replace."""
        result = summary_parse_iso("2026-01-15T10:00:00")
        assert result.tzinfo is not None
        assert result.tzinfo == UTC

    def test_aware_datetime_converted_to_utc(self) -> None:
        """Aware datetime with non-UTC timezone should be converted to UTC."""
        # +05:00 timezone
        result = summary_parse_iso("2026-01-15T15:00:00+05:00")
        assert result.tzinfo is not None
        # 15:00 +05:00 should be 10:00 UTC
        assert result.hour == 10

    def test_utc_datetime_unchanged(self) -> None:
        """UTC datetime should remain unchanged."""
        result = summary_parse_iso("2026-01-15T10:00:00+00:00")
        assert result.hour == 10
        assert result.tzinfo is not None

    def test_stale_detection_with_aware_datetimes(self, db: FiligreeDB) -> None:
        """Stale detection should work correctly with timezone-aware timestamps."""
        issue = db.create_issue("Stale task")
        db.update_issue(issue.id, status="in_progress")
        # Backdate to 5 days ago using a non-UTC timezone
        old_ts = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        db.conn.execute("UPDATE issues SET updated_at = ? WHERE id = ?", (old_ts, issue.id))
        db.conn.commit()
        summary = generate_summary(db)
        assert "Stale" in summary
        assert "Stale task" in summary


# ---------------------------------------------------------------------------
# Bug 3: Summary WIP limit
# ---------------------------------------------------------------------------


class TestSummaryWipLimit:
    def test_list_issues_called_with_high_limit(self, db: FiligreeDB) -> None:
        """Verify that list_issues is called with a high limit in generate_summary."""
        # We patch list_issues to track calls with limit parameter
        original_list_issues = db.list_issues
        call_limits: list[int | None] = []

        def tracking_list_issues(**kwargs):  # type: ignore[no-untyped-def]
            call_limits.append(kwargs.get("limit"))
            return original_list_issues(**kwargs)

        with patch.object(db, "list_issues", side_effect=tracking_list_issues):
            generate_summary(db)

        # All calls should have limit=10000 (not the default 100)
        for limit in call_limits:
            assert limit == 10000, f"list_issues called with limit={limit}, expected 10000"


# ---------------------------------------------------------------------------
# Bug 4: Templates enforcement validation + shape crash handling
# ---------------------------------------------------------------------------


class TestTemplateEnforcementValidation:
    def test_invalid_enforcement_raises_error(self) -> None:
        """Template with invalid enforcement value should raise ValueError."""
        raw = {
            "type": "badtype",
            "display_name": "Bad",
            "description": "Bad type",
            "states": [
                {"name": "open", "category": "open"},
                {"name": "closed", "category": "done"},
            ],
            "initial_state": "open",
            "transitions": [
                {"from": "open", "to": "closed", "enforcement": "invalid_value"},
            ],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match="invalid enforcement"):
            TemplateRegistry.parse_type_template(raw)

    def test_valid_enforcement_values_accepted(self) -> None:
        """Templates with valid enforcement values should parse fine."""
        for enforcement in ("hard", "soft", "none"):
            raw = {
                "type": "goodtype",
                "display_name": "Good",
                "description": "Good type",
                "states": [
                    {"name": "open", "category": "open"},
                    {"name": "closed", "category": "done"},
                ],
                "initial_state": "open",
                "transitions": [
                    {"from": "open", "to": "closed", "enforcement": enforcement},
                ],
                "fields_schema": [],
            }
            tpl = TemplateRegistry.parse_type_template(raw)
            assert tpl.transitions[0].enforcement == enforcement


class TestTemplateMalformedShape:
    def test_states_none_raises_value_error(self) -> None:
        """Template with states=None should raise ValueError, not TypeError."""
        raw = {
            "type": "badshape",
            "display_name": "Bad",
            "description": "Bad shape",
            "states": None,
            "initial_state": "open",
            "transitions": [],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match="must be a list"):
            TemplateRegistry.parse_type_template(raw)

    def test_states_string_raises_value_error(self) -> None:
        """Template with states as a string should raise ValueError."""
        raw = {
            "type": "badshape",
            "display_name": "Bad",
            "description": "Bad shape",
            "states": "not a list",
            "initial_state": "open",
            "transitions": [],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match="must be a list"):
            TemplateRegistry.parse_type_template(raw)

    def test_state_missing_name_raises_value_error(self) -> None:
        """State dict without 'name' key should raise ValueError."""
        raw = {
            "type": "badshape",
            "display_name": "Bad",
            "description": "Bad shape",
            "states": [{"category": "open"}],  # missing 'name'
            "initial_state": "open",
            "transitions": [],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match="must be a dict with 'name' and 'category'"):
            TemplateRegistry.parse_type_template(raw)

    def test_state_missing_category_raises_value_error(self) -> None:
        """State dict without 'category' key should raise ValueError."""
        raw = {
            "type": "badshape",
            "display_name": "Bad",
            "description": "Bad shape",
            "states": [{"name": "open"}],  # missing 'category'
            "initial_state": "open",
            "transitions": [],
            "fields_schema": [],
        }
        with pytest.raises(ValueError, match="must be a dict with 'name' and 'category'"):
            TemplateRegistry.parse_type_template(raw)

    def test_malformed_template_in_pack_loading_does_not_crash(self, tmp_path: object) -> None:
        """Malformed template in _load_pack_data should be skipped, not crash."""
        import json
        from pathlib import Path

        filigree_dir = Path(str(tmp_path)) / ".filigree"
        filigree_dir.mkdir()
        templates_dir = filigree_dir / "templates"
        templates_dir.mkdir()

        # Write a template with states=None (would cause TypeError without fix)
        bad_template = {
            "type": "crasher",
            "display_name": "Crasher",
            "description": "Should not crash loading",
            "states": None,
            "initial_state": "open",
            "transitions": [],
            "fields_schema": [],
        }
        (templates_dir / "crasher.json").write_text(json.dumps(bad_template))

        config = {"prefix": "test", "version": 1, "enabled_packs": ["core"]}
        (filigree_dir / "config.json").write_text(json.dumps(config))

        reg = TemplateRegistry()
        reg.load(filigree_dir)  # Should not raise
        assert reg.get_type("task") is not None  # Built-ins still loaded
        assert reg.get_type("crasher") is None  # Bad template was skipped


# ---------------------------------------------------------------------------
# Bug 5: rolled_back transition in release pack
# ---------------------------------------------------------------------------


class TestRolledBackTransition:
    def test_rolled_back_has_outbound_transition(self) -> None:
        """rolled_back state should have a transition to development."""
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        tpl = TemplateRegistry.parse_type_template(raw)
        rollback_transitions = [t for t in tpl.transitions if t.from_state == "rolled_back"]
        assert len(rollback_transitions) >= 1, "rolled_back should have at least one outbound transition"

    def test_rolled_back_can_go_to_development(self) -> None:
        """rolled_back should transition to development with soft enforcement."""
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        tpl = TemplateRegistry.parse_type_template(raw)
        rb_to_dev = [t for t in tpl.transitions if t.from_state == "rolled_back" and t.to_state == "development"]
        assert len(rb_to_dev) == 1, "Should have exactly one rolled_back -> development transition"
        assert rb_to_dev[0].enforcement == "soft"

    def test_release_type_validates_with_rollback_transition(self) -> None:
        """Release type should still pass validation with the new transition."""
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        tpl = TemplateRegistry.parse_type_template(raw)
        errors = TemplateRegistry.validate_type_template(tpl)
        assert errors == [], f"Validation errors: {errors}"

    def test_rolled_back_not_dead_end(self) -> None:
        """rolled_back should not appear in dead-end quality warnings."""
        raw = BUILT_IN_PACKS["release"]["types"]["release"]
        tpl = TemplateRegistry.parse_type_template(raw)
        warnings = TemplateRegistry.check_type_template_quality(tpl)
        dead_end_warnings = [w for w in warnings if "rolled_back" in w and "dead end" in w]
        assert dead_end_warnings == [], f"rolled_back still flagged as dead end: {dead_end_warnings}"
