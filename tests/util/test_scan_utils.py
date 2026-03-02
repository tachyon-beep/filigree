"""Tests for scripts/scan_utils.py — shared scanner utilities."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# scripts/ is not a package — add it to sys.path so we can import scan_utils [B3]
_scripts_dir = str(Path(__file__).resolve().parents[2] / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from scan_utils import (  # type: ignore[import-not-found]  # noqa: E402, I001
    _infer_rule_id,
    estimate_tokens,
    find_files,
    load_context,
    parse_findings,
    post_to_api,
    severity_map,
)


# ── severity_map ───────────────────────────────────────────────────────


class TestSeverityMap:
    """severity_map() maps scanner-native severities to filigree severities."""

    def test_major_maps_to_high(self) -> None:
        assert severity_map("major") == "high"

    def test_minor_maps_to_medium(self) -> None:
        assert severity_map("minor") == "medium"

    def test_trivial_maps_to_low(self) -> None:
        assert severity_map("trivial") == "low"

    def test_critical_passes_through(self) -> None:
        assert severity_map("critical") == "critical"

    def test_high_passes_through(self) -> None:
        assert severity_map("high") == "high"

    def test_medium_passes_through(self) -> None:
        assert severity_map("medium") == "medium"

    def test_low_passes_through(self) -> None:
        assert severity_map("low") == "low"

    def test_info_passes_through(self) -> None:
        assert severity_map("info") == "info"

    def test_unknown_maps_to_info(self) -> None:
        assert severity_map("extreme") == "info"

    def test_case_insensitive(self) -> None:
        assert severity_map("MAJOR") == "high"
        assert severity_map("Minor") == "medium"

    def test_whitespace_stripped(self) -> None:
        assert severity_map("  major  ") == "high"


# ── parse_findings ─────────────────────────────────────────────────────


SINGLE_FINDING_MD = """\
## Summary
Off-by-one error in loop boundary

## Severity
- Severity: major
- Priority: P1

## Evidence
src/filigree/core.py:42 — `for i in range(len(items))` should be `range(len(items) - 1)`

## Root Cause Hypothesis
Classic fencepost error when iterating pairs.

## Suggested Fix
Change `range(len(items))` to `range(len(items) - 1)`.
"""

TWO_FINDINGS_MD = """\
## Summary
SQL injection in query builder

## Severity
- Severity: critical
- Priority: P0

## Evidence
src/filigree/dashboard.py:99 — f-string concatenation in SQL

## Root Cause Hypothesis
Direct string interpolation instead of parameterised query.

## Suggested Fix
Use `?` placeholders.

---

## Summary
Unclosed file handle in export

## Severity
- Severity: minor
- Priority: P2

## Evidence
src/filigree/core.py:200 — `open()` without context manager

## Root Cause Hypothesis
Missing `with` statement.

## Suggested Fix
Wrap in `with open(...) as f:`.
"""

NO_BUG_MD = "No concrete bug found in src/filigree/core.py"


class TestParseFindings:
    """parse_findings() extracts structured findings from markdown."""

    def test_empty_text_returns_empty(self) -> None:
        assert parse_findings("") == []
        assert parse_findings("   ") == []

    def test_no_bug_sentinel_returns_empty(self) -> None:
        assert parse_findings(NO_BUG_MD) == []

    def test_single_finding(self) -> None:
        findings = parse_findings(SINGLE_FINDING_MD, file_path="src/filigree/core.py")
        assert len(findings) == 1
        f = findings[0]
        assert f["path"] == "src/filigree/core.py"
        assert f["severity"] == "high"  # major → high via severity_map
        assert f["rule_id"] == "logic-error"  # "off-by-one" keyword
        assert "Off-by-one" in f["message"]
        assert f["suggestion"] == "Change `range(len(items))` to `range(len(items) - 1)`."
        assert f["line_start"] == 42

    def test_two_findings_separated_by_dashes(self) -> None:
        findings = parse_findings(TWO_FINDINGS_MD, file_path="test.py")
        assert len(findings) == 2
        assert findings[0]["severity"] == "critical"
        assert findings[0]["rule_id"] == "injection"  # "SQL" keyword
        assert findings[1]["severity"] == "medium"  # minor → medium
        assert findings[1]["rule_id"] == "resource-leak"  # "unclosed" keyword

    def test_root_cause_appended_to_message(self) -> None:
        findings = parse_findings(SINGLE_FINDING_MD, file_path="x.py")
        assert "Root cause:" in findings[0]["message"]

    def test_line_number_extracted_from_evidence(self) -> None:
        findings = parse_findings(SINGLE_FINDING_MD, file_path="x.py")
        assert findings[0]["line_start"] == 42

    def test_missing_evidence_no_line_start(self) -> None:
        md = "## Summary\nSome bug\n\n## Severity\n- Severity: minor\n"
        findings = parse_findings(md, file_path="x.py")
        assert len(findings) == 1
        assert "line_start" not in findings[0]

    def test_default_severity_when_missing(self) -> None:
        md = "## Summary\nSome bug without severity section\n"
        findings = parse_findings(md, file_path="x.py")
        assert len(findings) == 1
        assert findings[0]["severity"] == "info"


# ── _infer_rule_id ─────────────────────────────────────────────────────


class TestInferRuleId:
    """_infer_rule_id() maps free-text summaries to canonical rule IDs."""

    def test_logic_keywords(self) -> None:
        assert _infer_rule_id("Off-by-one logic error") == "logic-error"
        assert _infer_rule_id("Unreachable branch") == "logic-error"

    def test_resource_keywords(self) -> None:
        assert _infer_rule_id("Unclosed file handle") == "resource-leak"
        assert _infer_rule_id("Memory leak in pool") == "resource-leak"

    def test_race_keywords(self) -> None:
        assert _infer_rule_id("Race condition in async handler") == "race-condition"

    def test_injection_keywords(self) -> None:
        assert _infer_rule_id("SQL injection vulnerability") == "injection"
        assert _infer_rule_id("XSS in template") == "injection"

    def test_performance_keywords(self) -> None:
        assert _infer_rule_id("O(n² performance issue") == "performance"
        # "Blocking" contains "lock" which matches race-condition first
        # in keyword iteration order; use explicit "performance" keyword
        assert _infer_rule_id("Performance bottleneck in loop") == "performance"

    def test_api_keywords(self) -> None:
        assert _infer_rule_id("Deprecated API usage") == "api-misuse"

    def test_error_handling_keywords(self) -> None:
        assert _infer_rule_id("Bare exception handler") == "error-handling"

    def test_unknown_maps_to_other(self) -> None:
        assert _infer_rule_id("Something completely different") == "other"


# ── post_to_api ────────────────────────────────────────────────────────


class TestPostToApi:
    """post_to_api() POSTs findings to the filigree scan API."""

    def _mock_urlopen(self, *, status: int = 200, body: dict[str, Any] | None = None) -> MagicMock:
        """Create a mock for urllib.request.urlopen."""
        resp = MagicMock()
        resp.read.return_value = json.dumps(body or {"status": "ok"}).encode("utf-8")
        resp.status = status
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_success_returns_true(self) -> None:
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen()):
            result = post_to_api(
                api_url="http://localhost:8377",
                scan_source="test",
                scan_run_id="run-1",
                findings=[{"path": "x.py", "rule_id": "other", "severity": "info", "message": "test"}],
            )
        assert result is True

    def test_sends_correct_payload(self) -> None:
        findings = [{"path": "x.py", "rule_id": "other", "severity": "info", "message": "test"}]
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen()) as mock_open:
            post_to_api(
                api_url="http://localhost:8377",
                scan_source="codex",
                scan_run_id="run-42",
                findings=findings,
            )
            req = mock_open.call_args[0][0]
            payload = json.loads(req.data.decode("utf-8"))
            assert payload["scan_source"] == "codex"
            assert payload["scan_run_id"] == "run-42"
            assert payload["findings"] == findings
            assert req.get_header("Content-type") == "application/json"

    def test_http_error_returns_false(self) -> None:
        import urllib.error

        err = urllib.error.HTTPError(
            url="http://localhost:8377/api/v1/scan-results",
            code=500,
            msg="Internal Server Error",
            hdrs={},  # type: ignore[arg-type]
            fp=MagicMock(read=MagicMock(return_value=b"error")),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = post_to_api(
                api_url="http://localhost:8377",
                scan_source="test",
                scan_run_id="run-1",
                findings=[{"path": "x.py", "rule_id": "other", "severity": "info", "message": "test"}],
            )
        assert result is False

    def test_url_error_returns_false(self) -> None:
        import urllib.error

        err = urllib.error.URLError("Connection refused")
        with patch("urllib.request.urlopen", side_effect=err):
            result = post_to_api(
                api_url="http://localhost:8377",
                scan_source="test",
                scan_run_id="run-1",
                findings=[{"path": "x.py", "rule_id": "other", "severity": "info", "message": "test"}],
            )
        assert result is False

    def test_api_warnings_logged(self) -> None:
        body = {"status": "ok", "warnings": ["severity coerced: extreme → info"]}
        with (
            patch("urllib.request.urlopen", return_value=self._mock_urlopen(body=body)),
            patch("scan_utils.logger") as mock_logger,
        ):
            post_to_api(
                api_url="http://localhost:8377",
                scan_source="test",
                scan_run_id="run-1",
                findings=[{"path": "x.py", "rule_id": "other", "severity": "info", "message": "test"}],
            )
            mock_logger.warning.assert_called()
            warn_call = mock_logger.warning.call_args
            assert "severity coerced" in str(warn_call)

    def test_http_error_body_read_failure_still_logs(self) -> None:
        """Bug filigree-4876d3: network failure reading error body should not crash."""
        import urllib.error

        err = urllib.error.HTTPError(
            url="http://localhost:8377/api/v1/scan-results",
            code=500,
            msg="Internal Server Error",
            hdrs={},  # type: ignore[arg-type]
            fp=MagicMock(read=MagicMock(side_effect=OSError("connection reset"))),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = post_to_api(
                api_url="http://localhost:8377",
                scan_source="test",
                scan_run_id="run-1",
                findings=[{"path": "x.py", "rule_id": "other", "severity": "info", "message": "test"}],
            )
        assert result is False  # Graceful degradation: returns False, doesn't crash


# ── find_files ─────────────────────────────────────────────────────────


class TestFindFiles:
    """find_files() discovers source files with filtering and truncation."""

    def _make_tree(self, tmp_path: Path) -> Path:
        """Create a small file tree for testing."""
        root = tmp_path / "src"
        root.mkdir()
        (root / "a.py").write_text("# a")
        (root / "b.py").write_text("# b")
        (root / "c.py").write_text("# c")
        (root / "d.txt").write_text("data")
        (root / "test_e.py").write_text("# test")
        sub = root / "sub"
        sub.mkdir()
        (sub / "f.py").write_text("# f")
        # Excluded dir
        pycache = root / "__pycache__"
        pycache.mkdir()
        (pycache / "g.pyc").write_text("bytecode")
        return root

    def test_python_filter(self, tmp_path: Path) -> None:
        root = self._make_tree(tmp_path)
        files = find_files(root, file_type="python")
        names = {f.name for f in files}
        assert "a.py" in names
        assert "b.py" in names
        assert "d.txt" not in names
        assert "test_e.py" not in names  # test_ files excluded

    def test_all_filter(self, tmp_path: Path) -> None:
        root = self._make_tree(tmp_path)
        files = find_files(root, file_type="all")
        names = {f.name for f in files}
        assert "a.py" in names
        assert "d.txt" in names
        assert "test_e.py" in names
        assert "g.pyc" not in names  # binary excluded

    def test_max_files_truncation(self, tmp_path: Path) -> None:
        root = self._make_tree(tmp_path)
        files = find_files(root, file_type="python", max_files=2)
        assert len(files) == 2

    def test_max_files_zero_means_no_limit(self, tmp_path: Path) -> None:
        root = self._make_tree(tmp_path)
        all_files = find_files(root, file_type="python")
        limited = find_files(root, file_type="python", max_files=0)
        assert len(limited) == len(all_files)

    def test_exclude_dirs(self, tmp_path: Path) -> None:
        root = self._make_tree(tmp_path)
        sub = root / "sub"
        files = find_files(root, file_type="python", exclude_dirs={sub})
        names = {f.name for f in files}
        assert "f.py" not in names
        assert "a.py" in names

    def test_pycache_always_excluded(self, tmp_path: Path) -> None:
        root = self._make_tree(tmp_path)
        files = find_files(root, file_type="all")
        names = {f.name for f in files}
        assert "g.pyc" not in names

    def test_results_sorted(self, tmp_path: Path) -> None:
        root = self._make_tree(tmp_path)
        files = find_files(root, file_type="python")
        assert files == sorted(files)


# ── load_context ───────────────────────────────────────────────────────


class TestLoadContext:
    """load_context() loads CLAUDE.md and ARCHITECTURE.md from repo root."""

    def test_loads_existing_files(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("# Claude instructions")
        (tmp_path / "ARCHITECTURE.md").write_text("# Architecture")
        ctx = load_context(tmp_path)
        assert "Claude instructions" in ctx
        assert "Architecture" in ctx

    def test_missing_files_returns_empty(self, tmp_path: Path) -> None:
        ctx = load_context(tmp_path)
        assert ctx == ""

    def test_partial_files(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("# Only claude")
        ctx = load_context(tmp_path)
        assert "Only claude" in ctx
        assert "ARCHITECTURE" not in ctx


# ── estimate_tokens ────────────────────────────────────────────────────


class TestEstimateTokens:
    """estimate_tokens() provides rough token counts for cost estimation."""

    def test_basic_estimation(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("x" * 400)  # 400 chars → ~100 tokens
        tokens = estimate_tokens([f])
        assert tokens == (400 // 4) + 2000  # chars/4 + overhead

    def test_missing_file_uses_overhead(self, tmp_path: Path) -> None:
        f = tmp_path / "nonexistent.py"
        tokens = estimate_tokens([f])
        assert tokens == 2000  # just overhead

    def test_multiple_files(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("x" * 800)
        f2.write_text("y" * 400)
        tokens = estimate_tokens([f1, f2])
        expected = (800 // 4 + 2000) + (400 // 4 + 2000)
        assert tokens == expected
