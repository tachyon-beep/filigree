"""Tests for core file records, scan findings, and associations."""

from __future__ import annotations

import pytest

from filigree.core import FiligreeDB

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestFileSchema:
    """Verify file/finding tables are created."""

    def test_file_records_table_exists(self, db: FiligreeDB) -> None:
        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_records'").fetchone()
        assert row is not None

    def test_scan_findings_table_exists(self, db: FiligreeDB) -> None:
        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_findings'").fetchone()
        assert row is not None

    def test_file_associations_table_exists(self, db: FiligreeDB) -> None:
        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_associations'").fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# FileRecord CRUD tests
# ---------------------------------------------------------------------------


class TestRegisterFile:
    """Tests for registering and retrieving file records."""

    def test_register_new_file(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py", language="python")
        assert f.path == "src/main.py"
        assert f.language == "python"
        assert f.id.startswith("test-f-")

    def test_register_duplicate_path_returns_existing(self, db: FiligreeDB) -> None:
        f1 = db.register_file("src/main.py")
        f2 = db.register_file("src/main.py")
        assert f1.id == f2.id

    def test_register_updates_language(self, db: FiligreeDB) -> None:
        db.register_file("src/main.py", language="")
        f2 = db.register_file("src/main.py", language="python")
        assert f2.language == "python"

    def test_get_file_by_id(self, db: FiligreeDB) -> None:
        created = db.register_file("src/main.py", language="python")
        fetched = db.get_file(created.id)
        assert fetched.path == "src/main.py"
        assert fetched.language == "python"

    def test_get_file_not_found(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError, match="test-f-nope"):
            db.get_file("test-f-nope")

    def test_get_file_by_path(self, db: FiligreeDB) -> None:
        created = db.register_file("src/main.py")
        fetched = db.get_file_by_path("src/main.py")
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_file_by_path_not_found(self, db: FiligreeDB) -> None:
        result = db.get_file_by_path("nonexistent.py")
        assert result is None

    def test_register_normalizes_path(self, db: FiligreeDB) -> None:
        """Bug filigree-b78901: register_file must normalize paths for consistent identity."""
        f1 = db.register_file("src/foo/../bar.py")
        f2 = db.register_file("src/bar.py")
        assert f1.id == f2.id  # Same file, same record
        assert f1.path == "src/bar.py"  # Stored normalized

    def test_register_normalizes_backslashes(self, db: FiligreeDB) -> None:
        """Windows-style paths should be normalized to forward slashes."""
        f1 = db.register_file("src\\utils\\helper.py")
        f2 = db.register_file("src/utils/helper.py")
        assert f1.id == f2.id

    def test_register_empty_path_after_normalization_raises(self, db: FiligreeDB) -> None:
        """Path that normalizes to empty (e.g. '.') should be rejected."""
        with pytest.raises(ValueError, match="empty after normalization"):
            db.register_file(".")


class TestListFiles:
    """Tests for listing file records."""

    def test_list_empty(self, db: FiligreeDB) -> None:
        files = db.list_files()
        assert files == []

    def test_list_returns_all(self, db: FiligreeDB) -> None:
        db.register_file("a.py")
        db.register_file("b.py")
        files = db.list_files()
        assert len(files) == 2

    def test_list_with_limit(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.register_file(f"file{i}.py")
        files = db.list_files(limit=3)
        assert len(files) == 3

    def test_list_with_offset(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.register_file(f"file{i}.py")
        files = db.list_files(offset=3)
        assert len(files) == 2

    def test_list_with_language_filter(self, db: FiligreeDB) -> None:
        db.register_file("a.py", language="python")
        db.register_file("b.js", language="javascript")
        files = db.list_files(language="python")
        assert len(files) == 1
        assert files[0].language == "python"

    def test_list_with_path_prefix(self, db: FiligreeDB) -> None:
        db.register_file("src/core/a.py")
        db.register_file("src/core/b.py")
        db.register_file("tests/test_a.py")
        files = db.list_files(path_prefix="src/core/")
        assert len(files) == 2

    def test_list_with_path_prefix_escapes_like_wildcards(self, db: FiligreeDB) -> None:
        """path_prefix containing SQL LIKE wildcards (% and _) must match literally."""
        db.register_file("src/file_test.py")
        db.register_file("src/filextest.py")  # _ wildcard would match this
        db.register_file("src/file%test.py")
        db.register_file("src/fileABCtest.py")  # % wildcard would match this

        # Underscore must be literal — only file_test.py should match
        files = db.list_files(path_prefix="file_test")
        assert len(files) == 1
        assert files[0].path == "src/file_test.py"

        # Percent must be literal — only file%test.py should match
        files = db.list_files(path_prefix="file%test")
        assert len(files) == 1
        assert files[0].path == "src/file%test.py"

    def test_list_sorted_by_path(self, db: FiligreeDB) -> None:
        db.register_file("z.py")
        db.register_file("a.py")
        files = db.list_files(sort="path")
        assert files[0].path == "a.py"
        assert files[1].path == "z.py"


# ---------------------------------------------------------------------------
# Scan findings tests
# ---------------------------------------------------------------------------


class TestProcessScanResults:
    """Tests for ingesting scan findings."""

    def test_ingest_creates_file_and_findings(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(
            scan_source="ruff",
            findings=[
                {
                    "path": "src/main.py",
                    "rule_id": "E501",
                    "severity": "low",
                    "message": "Line too long",
                    "line_start": 10,
                    "line_end": 10,
                },
            ],
        )
        assert result["files_created"] >= 1
        assert result["findings_created"] >= 1

    def test_ingest_upserts_existing_finding(self, db: FiligreeDB) -> None:
        finding = {
            "path": "src/main.py",
            "rule_id": "E501",
            "severity": "low",
            "message": "Line too long",
            "line_start": 10,
        }
        db.process_scan_results(scan_source="ruff", findings=[finding])
        result = db.process_scan_results(scan_source="ruff", findings=[finding])
        # Second ingest should update, not create
        assert result["findings_created"] == 0
        assert result["findings_updated"] >= 1

    def test_ingest_normalizes_scan_path_separators(self, db: FiligreeDB) -> None:
        """Equivalent Windows/POSIX path forms should map to one file record."""
        first = {
            "path": r".\src\main.py",
            "rule_id": "E501",
            "severity": "low",
            "message": "Line too long",
            "line_start": 10,
        }
        second = {
            "path": "src/main.py",
            "rule_id": "E501",
            "severity": "low",
            "message": "Line too long",
            "line_start": 10,
        }
        db.process_scan_results(scan_source="ruff", findings=[first])
        result = db.process_scan_results(scan_source="ruff", findings=[second])

        assert result["files_created"] == 0
        assert result["files_updated"] == 1
        row = db.conn.execute("SELECT path FROM file_records").fetchall()
        assert len(row) == 1
        assert row[0]["path"] == "src/main.py"

    def test_ingest_with_language(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {
                    "path": "src/main.py",
                    "language": "python",
                    "rule_id": "E501",
                    "severity": "low",
                    "message": "Line too long",
                },
            ],
        )
        f = db.get_file_by_path("src/main.py")
        assert f is not None
        assert f.language == "python"

    def test_ingest_unknown_severity_maps_to_info(self, db: FiligreeDB) -> None:
        """Unknown severity strings are mapped to 'info' with a warning."""
        result = db.process_scan_results(
            scan_source="ruff",
            findings=[
                {
                    "path": "src/main.py",
                    "rule_id": "E501",
                    "severity": "extreme",
                    "message": "Bad",
                },
            ],
        )
        assert result["findings_created"] == 1
        assert any("extreme" in w for w in result["warnings"])
        finding = db.conn.execute("SELECT severity FROM scan_findings").fetchone()
        assert finding["severity"] == "info"

    def test_ingest_empty_findings(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(scan_source="ruff", findings=[])
        assert result["files_created"] == 0
        assert result["findings_created"] == 0
        assert result["new_finding_ids"] == []
        assert result["issues_created"] == 0
        assert result["issue_ids"] == []

    def test_create_issues_promotes_finding_to_bug_and_links_file(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(
            scan_source="codex",
            create_issues=True,
            findings=[
                {
                    "path": "src/main.py",
                    "rule_id": "logic-error",
                    "severity": "high",
                    "message": "Off-by-one in pagination loop",
                    "line_start": 42,
                },
            ],
        )
        assert result["issues_created"] == 1
        assert len(result["issue_ids"]) == 1

        issue_id = result["issue_ids"][0]
        issue = db.get_issue(issue_id)
        assert issue.type == "bug"
        assert "candidate" in issue.labels
        assert "scan_finding" in issue.labels

        file_record = db.get_file_by_path("src/main.py")
        assert file_record is not None
        finding = db.get_findings(file_record.id)[0]
        assert finding.issue_id == issue_id

        associations = db.get_file_associations(file_record.id)
        assert any(a["issue_id"] == issue_id and a["assoc_type"] == "bug_in" for a in associations)

    def test_create_issues_backfills_existing_unlinked_finding(self, db: FiligreeDB) -> None:
        finding = {
            "path": "src/main.py",
            "rule_id": "logic-error",
            "severity": "high",
            "message": "Off-by-one in pagination loop",
            "line_start": 42,
        }
        db.process_scan_results(scan_source="codex", create_issues=False, findings=[finding])
        result = db.process_scan_results(scan_source="codex", create_issues=True, findings=[finding])

        assert result["issues_created"] == 1
        issue_id = result["issue_ids"][0]
        file_record = db.get_file_by_path("src/main.py")
        assert file_record is not None
        updated_finding = db.get_findings(file_record.id)[0]
        assert updated_finding.issue_id == issue_id

    def test_update_finding_marks_it_fixed(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        file_record = db.get_file_by_path("a.py")
        assert file_record is not None
        finding = db.get_findings(file_record.id)[0]

        updated = db.update_finding(file_record.id, finding.id, status="fixed")
        assert updated.status == "fixed"
        summary = db.get_file_findings_summary(file_record.id)
        assert summary["open_findings"] == 0

    def test_update_finding_links_issue_and_creates_association(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        issue = db.create_issue("Fix lint finding", type="bug")
        file_record = db.get_file_by_path("a.py")
        assert file_record is not None
        finding = db.get_findings(file_record.id)[0]

        updated = db.update_finding(file_record.id, finding.id, status="fixed", issue_id=issue.id)
        assert updated.status == "fixed"
        assert updated.issue_id == issue.id
        associations = db.get_file_associations(file_record.id)
        assert any(a["issue_id"] == issue.id and a["assoc_type"] == "bug_in" for a in associations)

    def test_ingest_finding_missing_path(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="path"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"severity": "low", "message": "No path key"}],
            )

    def test_ingest_finding_missing_rule_id(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="rule_id"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"path": "a.py", "severity": "low", "message": "No rule_id key"}],
            )

    def test_ingest_finding_missing_message(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="message"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"path": "a.py", "rule_id": "E1", "severity": "low"}],
            )

    def test_ingest_finding_blank_rule_id_rejected(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"path": "a.py", "rule_id": "  ", "severity": "low", "message": "m"}],
            )

    def test_ingest_finding_blank_message_rejected(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"path": "a.py", "rule_id": "E1", "severity": "low", "message": "  "}],
            )

    def test_ingest_finding_is_string(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="dict"):
            db.process_scan_results(scan_source="ruff", findings=["not-a-dict"])

    def test_ingest_finding_is_number(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="dict"):
            db.process_scan_results(scan_source="ruff", findings=[42])

    def test_bad_finding_at_end_does_not_persist_earlier_writes(self, db: FiligreeDB) -> None:
        """A bad finding later in the list must not leave earlier writes pending."""
        with pytest.raises(ValueError, match="severity must be a string"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[
                    {"path": "good.py", "rule_id": "E501", "severity": "low", "message": "ok"},
                    {"path": "bad.py", "rule_id": "E999", "severity": 42, "message": "bad"},
                ],
            )
        # Neither file nor finding should have been persisted
        assert db.conn.execute("SELECT COUNT(*) FROM file_records").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM scan_findings").fetchone()[0] == 0

    def test_runtime_exception_rolls_back_pending_scan_writes(self, db: FiligreeDB) -> None:
        """Mid-batch runtime exceptions must rollback partial writes."""
        with pytest.raises(ValueError, match="suggestion must be a string"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[
                    {"path": "good.py", "rule_id": "E501", "severity": "low", "message": "ok"},
                    {
                        "path": "bad.py",
                        "rule_id": "E999",
                        "severity": "low",
                        "message": "bad",
                        "suggestion": 123,  # upfront validation catches non-string
                    },
                ],
            )

        # Force a separate successful write+commit and confirm dirty scan writes
        # were not accidentally committed.
        db.create_issue("post-error commit probe")
        assert db.conn.execute("SELECT COUNT(*) FROM file_records").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM scan_findings").fetchone()[0] == 0

    def test_non_string_path_rejected(self, db: FiligreeDB) -> None:
        """Bug filigree-0dbe1a: non-string path must raise ValueError, not crash."""
        with pytest.raises(ValueError, match="path must be a string"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"path": 123, "rule_id": "E1", "severity": "low", "message": "m"}],
            )

    def test_non_integer_line_start_rejected(self, db: FiligreeDB) -> None:
        """Bug filigree-0dbe1a: non-integer line_start must raise ValueError."""
        with pytest.raises(ValueError, match="line_start must be"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[
                    {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m", "line_start": "ten"},
                ],
            )

    def test_non_integer_line_end_rejected(self, db: FiligreeDB) -> None:
        """Bug filigree-0dbe1a: non-integer line_end must raise ValueError."""
        with pytest.raises(ValueError, match="line_end must be"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[
                    {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m", "line_end": "twenty"},
                ],
            )

    def test_non_string_suggestion_rejected(self, db: FiligreeDB) -> None:
        """Bug filigree-0dbe1a: non-string suggestion must raise ValueError."""
        with pytest.raises(ValueError, match="suggestion must be a string"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[
                    {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m", "suggestion": 42},
                ],
            )

    def test_scan_metadata_persisted_on_create(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {
                    "path": "a.py",
                    "rule_id": "E1",
                    "severity": "low",
                    "message": "m",
                    "metadata": {"url": "https://example.com", "tags": ["style"]},
                },
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        assert findings[0].metadata == {"url": "https://example.com", "tags": ["style"]}

    def test_scan_metadata_persisted_on_update(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m", "metadata": {"v": 1}},
            ],
        )
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m2", "metadata": {"v": 2}},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        assert findings[0].metadata == {"v": 2}

    def test_scan_metadata_defaults_empty_dict(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        assert findings[0].metadata == {}


class TestSeverityFallback:
    """Tests for severity normalization and fallback behavior."""

    def test_severity_fallback_maps_unknown_to_info(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(
            scan_source="ai",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "major", "message": "m"}],
        )
        assert result["findings_created"] == 1
        assert any("major" in w for w in result["warnings"])
        row = db.conn.execute("SELECT severity FROM scan_findings").fetchone()
        assert row["severity"] == "info"

    def test_severity_fallback_normalizes_case(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(
            scan_source="ai",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "High", "message": "m"}],
        )
        assert result["findings_created"] == 1
        assert result["warnings"] == []
        row = db.conn.execute("SELECT severity FROM scan_findings").fetchone()
        assert row["severity"] == "high"

    def test_severity_fallback_strips_whitespace(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(
            scan_source="ai",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": " low ", "message": "m"}],
        )
        assert result["findings_created"] == 1
        assert result["warnings"] == []
        row = db.conn.execute("SELECT severity FROM scan_findings").fetchone()
        assert row["severity"] == "low"

    def test_severity_fallback_empty_string(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(
            scan_source="ai",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "", "message": "m"}],
        )
        assert result["findings_created"] == 1
        assert any("''" in w for w in result["warnings"])
        row = db.conn.execute("SELECT severity FROM scan_findings").fetchone()
        assert row["severity"] == "info"

    def test_severity_fallback_none_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="severity must be a string"):
            db.process_scan_results(
                scan_source="ai",
                findings=[{"path": "a.py", "rule_id": "R1", "severity": None, "message": "m"}],
            )

    def test_severity_fallback_numeric_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="severity must be a string"):
            db.process_scan_results(
                scan_source="ai",
                findings=[{"path": "a.py", "rule_id": "R1", "severity": 42, "message": "m"}],
            )


class TestScanRunId:
    """Tests for scan_run_id storage and attribution semantics."""

    def test_scan_run_id_stored_on_insert(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="codex",
            scan_run_id="run-001",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        row = db.conn.execute("SELECT scan_run_id FROM scan_findings").fetchone()
        assert row["scan_run_id"] == "run-001"

    def test_scan_run_id_preserved_on_update(self, db: FiligreeDB) -> None:
        """Existing non-empty scan_run_id is kept when re-ingested with a different run."""
        db.process_scan_results(
            scan_source="codex",
            scan_run_id="run-001",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        db.process_scan_results(
            scan_source="codex",
            scan_run_id="run-002",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m2"}],
        )
        row = db.conn.execute("SELECT scan_run_id FROM scan_findings").fetchone()
        assert row["scan_run_id"] == "run-001"

    def test_scan_run_id_late_attribution(self, db: FiligreeDB) -> None:
        """Empty scan_run_id can be updated to non-empty on re-ingest."""
        db.process_scan_results(
            scan_source="codex",
            scan_run_id="",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        db.process_scan_results(
            scan_source="codex",
            scan_run_id="run-001",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m2"}],
        )
        row = db.conn.execute("SELECT scan_run_id FROM scan_findings").fetchone()
        assert row["scan_run_id"] == "run-001"

    def test_scan_run_id_empty_does_not_overwrite(self, db: FiligreeDB) -> None:
        """Re-ingesting with empty scan_run_id never clears existing attribution."""
        db.process_scan_results(
            scan_source="codex",
            scan_run_id="run-001",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        db.process_scan_results(
            scan_source="codex",
            scan_run_id="",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m2"}],
        )
        row = db.conn.execute("SELECT scan_run_id FROM scan_findings").fetchone()
        assert row["scan_run_id"] == "run-001"


class TestSuggestionField:
    """Tests for suggestion storage and size cap."""

    def test_suggestion_stored(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ai",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m", "suggestion": "Fix it"}],
        )
        row = db.conn.execute("SELECT suggestion FROM scan_findings").fetchone()
        assert row["suggestion"] == "Fix it"

    def test_suggestion_defaults_to_empty(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ai",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        row = db.conn.execute("SELECT suggestion FROM scan_findings").fetchone()
        assert row["suggestion"] == ""

    def test_suggestion_truncated_at_10000(self, db: FiligreeDB) -> None:
        long_suggestion = "x" * 15_000
        db.process_scan_results(
            scan_source="ai",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m", "suggestion": long_suggestion}],
        )
        row = db.conn.execute("SELECT suggestion FROM scan_findings").fetchone()
        assert len(row["suggestion"]) == 10_000 + len("\n[truncated]")
        assert row["suggestion"].endswith("\n[truncated]")

    def test_suggestion_updated_on_re_ingest(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ai",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m", "suggestion": "Fix v1"}],
        )
        db.process_scan_results(
            scan_source="ai",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m", "suggestion": "Fix v2"}],
        )
        row = db.conn.execute("SELECT suggestion FROM scan_findings").fetchone()
        assert row["suggestion"] == "Fix v2"


class TestNewFindingIds:
    """Tests for new_finding_ids in process_scan_results return."""

    def test_new_finding_ids_on_create(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "msg1"},
                {"path": "a.py", "rule_id": "E502", "severity": "low", "message": "msg2"},
            ],
        )
        assert len(result["new_finding_ids"]) == 2
        assert result["findings_created"] == 2

    def test_new_finding_ids_empty_on_update(self, db: FiligreeDB) -> None:
        finding = {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "msg"}
        db.process_scan_results(scan_source="ruff", findings=[finding])
        result = db.process_scan_results(scan_source="ruff", findings=[finding])
        assert result["new_finding_ids"] == []
        assert result["findings_updated"] == 1

    def test_new_finding_ids_mixed(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "old"}],
        )
        result = db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "old"},  # update
                {"path": "a.py", "rule_id": "E502", "severity": "low", "message": "new"},  # create
            ],
        )
        assert len(result["new_finding_ids"]) == 1
        assert result["findings_created"] == 1
        assert result["findings_updated"] == 1


class TestFindingReopenOnRescan:
    """Reappearing findings must reopen if previously fixed or unseen."""

    def test_fixed_finding_reopens_on_rescan(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE file_id = ?", (f.id,))
        db.conn.commit()
        # Re-scan with the same finding
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        finding = db.get_findings(f.id)[0]
        assert finding.status == "open"

    def test_unseen_in_latest_reopens_on_rescan(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        db.conn.execute("UPDATE scan_findings SET status = 'unseen_in_latest' WHERE file_id = ?", (f.id,))
        db.conn.commit()
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        finding = db.get_findings(f.id)[0]
        assert finding.status == "open"

    def test_acknowledged_stays_acknowledged_on_rescan(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        db.conn.execute("UPDATE scan_findings SET status = 'acknowledged' WHERE file_id = ?", (f.id,))
        db.conn.commit()
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        finding = db.get_findings(f.id)[0]
        assert finding.status == "acknowledged"

    def test_false_positive_stays_false_positive_on_rescan(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        db.conn.execute("UPDATE scan_findings SET status = 'false_positive' WHERE file_id = ?", (f.id,))
        db.conn.commit()
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        finding = db.get_findings(f.id)[0]
        assert finding.status == "false_positive"


class TestMarkUnseen:
    """Tests for mark_unseen soft status behavior."""

    def test_mark_unseen_flags_missing_findings(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m1"},
                {"path": "a.py", "rule_id": "E502", "severity": "low", "message": "m2"},
            ],
        )
        # Second scan only includes E501 — E502 should become unseen_in_latest
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m1"}],
            mark_unseen=True,
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        statuses = {finding.rule_id: finding.status for finding in findings}
        assert statuses["E501"] == "open"
        assert statuses["E502"] == "unseen_in_latest"

    def test_mark_unseen_does_not_affect_other_files(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m1"},
                {"path": "b.py", "rule_id": "E501", "severity": "low", "message": "m1"},
            ],
        )
        # Scan only a.py — b.py findings should NOT be affected
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m1"}],
            mark_unseen=True,
        )
        fb = db.get_file_by_path("b.py")
        findings_b = db.get_findings(fb.id)
        assert all(f.status == "open" for f in findings_b)

    def test_mark_unseen_does_not_affect_other_sources(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        db.process_scan_results(
            scan_source="eslint",
            findings=[{"path": "a.py", "rule_id": "no-unused-vars", "severity": "high", "message": "m"}],
        )
        # Scan ruff only — eslint findings should NOT be affected
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
            mark_unseen=True,
        )
        fa = db.get_file_by_path("a.py")
        findings = db.get_findings(fa.id)
        eslint_finding = next(f for f in findings if f.scan_source == "eslint")
        assert eslint_finding.status == "open"

    def test_mark_unseen_preserves_fixed(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m1"},
                {"path": "a.py", "rule_id": "E502", "severity": "low", "message": "m2"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        # Manually mark E502 as fixed
        e502 = next(fi for fi in findings if fi.rule_id == "E502")
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (e502.id,))
        db.conn.commit()

        # Scan only E501 — E502 should stay fixed, not become unseen_in_latest
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m1"}],
            mark_unseen=True,
        )
        findings = db.get_findings(f.id)
        statuses = {fi.rule_id: fi.status for fi in findings}
        assert statuses["E502"] == "fixed"

    def test_mark_unseen_false_does_nothing(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m1"},
                {"path": "a.py", "rule_id": "E502", "severity": "low", "message": "m2"},
            ],
        )
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m1"}],
            mark_unseen=False,
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        assert all(fi.status == "open" for fi in findings)

    def test_last_seen_at_updated_on_rescan(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        first_last_seen = db.get_findings(f.id)[0].last_seen_at

        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        second_last_seen = db.get_findings(f.id)[0].last_seen_at
        assert second_last_seen is not None
        assert second_last_seen >= (first_last_seen or "")


class TestCleanStaleFindings:
    """Tests for the clean_stale_findings command."""

    def test_cleans_old_unseen_findings(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        finding = db.get_findings(f.id)[0]
        # Mark as unseen with an old last_seen_at
        db.conn.execute(
            "UPDATE scan_findings SET status = 'unseen_in_latest', last_seen_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (finding.id,),
        )
        db.conn.commit()

        result = db.clean_stale_findings(days=30)
        assert result["findings_fixed"] == 1
        updated = db.get_findings(f.id)[0]
        assert updated.status == "fixed"

    def test_does_not_clean_recent_unseen(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        finding = db.get_findings(f.id)[0]
        # Mark as unseen but with recent last_seen_at
        db.conn.execute(
            "UPDATE scan_findings SET status = 'unseen_in_latest' WHERE id = ?",
            (finding.id,),
        )
        db.conn.commit()

        result = db.clean_stale_findings(days=30)
        assert result["findings_fixed"] == 0

    def test_filters_by_scan_source(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "m"}],
        )
        db.process_scan_results(
            scan_source="eslint",
            findings=[{"path": "a.py", "rule_id": "no-unused-vars", "severity": "high", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        for fi in findings:
            db.conn.execute(
                "UPDATE scan_findings SET status = 'unseen_in_latest', last_seen_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                (fi.id,),
            )
        db.conn.commit()

        result = db.clean_stale_findings(days=30, scan_source="ruff")
        assert result["findings_fixed"] == 1
        # eslint finding should still be unseen
        findings = db.get_findings(f.id)
        eslint = next(fi for fi in findings if fi.scan_source == "eslint")
        assert eslint.status == "unseen_in_latest"


class TestGetFindings:
    """Tests for retrieving findings for a file."""

    def test_get_findings_for_file(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "Too long"},
                {"path": "a.py", "rule_id": "E502", "severity": "high", "message": "Bad import"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        assert len(findings) == 2

    def test_get_findings_with_severity_filter(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "Low"},
                {"path": "a.py", "rule_id": "E502", "severity": "high", "message": "High"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id, severity="high")
        assert len(findings) == 1
        assert findings[0].severity == "high"

    def test_get_findings_with_status_filter(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "A"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id, status="open")
        assert len(findings) == 1
        findings = db.get_findings(f.id, status="fixed")
        assert len(findings) == 0

    def test_get_findings_pagination(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": f"E{i}", "severity": "low", "message": f"msg{i}"} for i in range(10)],
        )
        f = db.get_file_by_path("a.py")
        page1 = db.get_findings(f.id, limit=5)
        assert len(page1) == 5
        page2 = db.get_findings(f.id, limit=5, offset=5)
        assert len(page2) == 5


# ---------------------------------------------------------------------------
# File association tests
# ---------------------------------------------------------------------------


class TestFileAssociations:
    """Tests for linking files to issues."""

    def test_add_association(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py")
        issue = db.create_issue("Fix bug")
        db.add_file_association(f.id, issue.id, "bug_in")
        assocs = db.get_file_associations(f.id)
        assert len(assocs) == 1
        assert assocs[0]["issue_id"] == issue.id
        assert assocs[0]["assoc_type"] == "bug_in"

    def test_add_duplicate_association_is_idempotent(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py")
        issue = db.create_issue("Fix bug")
        db.add_file_association(f.id, issue.id, "bug_in")
        db.add_file_association(f.id, issue.id, "bug_in")
        assocs = db.get_file_associations(f.id)
        assert len(assocs) == 1

    def test_invalid_assoc_type(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py")
        issue = db.create_issue("Fix bug")
        with pytest.raises(ValueError, match="assoc_type"):
            db.add_file_association(f.id, issue.id, "invalid_type")

    def test_multiple_association_types(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py")
        issue = db.create_issue("Fix bug")
        db.add_file_association(f.id, issue.id, "bug_in")
        db.add_file_association(f.id, issue.id, "task_for")
        assocs = db.get_file_associations(f.id)
        assert len(assocs) == 2

    def test_nonexistent_issue_id_raises_valueerror(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py")
        with pytest.raises(ValueError, match="Issue not found"):
            db.add_file_association(f.id, "nonexistent-issue-id", "bug_in")


# ---------------------------------------------------------------------------
# Bidirectional navigation tests (issue -> files/findings)
# ---------------------------------------------------------------------------


class TestIssueFiles:
    """Tests for getting files associated with an issue."""

    def test_get_issue_files(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        f = db.register_file("src/main.py")
        db.add_file_association(f.id, issue.id, "bug_in")
        files = db.get_issue_files(issue.id)
        assert len(files) == 1
        assert files[0]["file_id"] == f.id
        assert files[0]["assoc_type"] == "bug_in"

    def test_get_issue_files_empty(self, db: FiligreeDB) -> None:
        issue = db.create_issue("No files")
        files = db.get_issue_files(issue.id)
        assert files == []

    def test_get_issue_files_includes_file_path(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        f = db.register_file("src/main.py", language="python")
        db.add_file_association(f.id, issue.id, "bug_in")
        files = db.get_issue_files(issue.id)
        assert files[0]["file_path"] == "src/main.py"
        assert files[0]["file_language"] == "python"


class TestIssueFindings:
    """Tests for getting scan findings linked to an issue."""

    def test_get_issue_findings_via_association(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        f = db.register_file("src/main.py")
        db.add_file_association(f.id, issue.id, "scan_finding")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "src/main.py", "rule_id": "E501", "severity": "low", "message": "Too long"}],
        )
        findings = db.get_issue_findings(issue.id)
        assert len(findings) >= 1

    def test_get_issue_findings_via_direct_link(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "src/main.py", "rule_id": "E501", "severity": "low", "message": "Too long"}],
        )
        f = db.get_file_by_path("src/main.py")
        # Directly link a finding to the issue
        finding = db.get_findings(f.id)[0]
        db.conn.execute("UPDATE scan_findings SET issue_id = ? WHERE id = ?", (issue.id, finding.id))
        db.conn.commit()
        findings = db.get_issue_findings(issue.id)
        assert len(findings) >= 1

    def test_get_issue_findings_empty(self, db: FiligreeDB) -> None:
        issue = db.create_issue("No findings")
        findings = db.get_issue_findings(issue.id)
        assert findings == []


class TestFileDetailCore:
    """Tests for get_file_findings_summary() and get_file_detail()."""

    def test_summary_empty_file(self, db: FiligreeDB) -> None:
        f = db.register_file("empty.py")
        summary = db.get_file_findings_summary(f.id)
        assert summary["total_findings"] == 0
        assert summary["open_findings"] == 0
        for sev in ("critical", "high", "medium", "low", "info"):
            assert summary[sev] == 0

    def test_summary_counts_by_severity(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "S101", "severity": "critical", "message": "Assert"},
                {"path": "a.py", "rule_id": "E501", "severity": "high", "message": "Long"},
                {"path": "a.py", "rule_id": "E302", "severity": "high", "message": "Space"},
                {"path": "a.py", "rule_id": "W291", "severity": "low", "message": "Trail"},
            ],
        )
        f = db.get_file_by_path("a.py")
        summary = db.get_file_findings_summary(f.id)
        assert summary["total_findings"] == 4
        assert summary["open_findings"] == 4
        assert summary["critical"] == 1
        assert summary["high"] == 2
        assert summary["low"] == 1

    def test_summary_excludes_fixed_and_false_positive(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "high", "message": "Long"},
                {"path": "a.py", "rule_id": "E302", "severity": "medium", "message": "Space"},
            ],
        )
        f = db.get_file_by_path("a.py")
        # Mark one as fixed
        findings = db.get_findings(f.id)
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (findings[0].id,))
        db.conn.commit()
        summary = db.get_file_findings_summary(f.id)
        assert summary["total_findings"] == 2  # total includes all
        assert summary["open_findings"] == 1  # open excludes fixed

    def test_get_file_detail_structure(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py", language="python")
        detail = db.get_file_detail(f.id)
        assert set(detail.keys()) == {"file", "associations", "recent_findings", "summary"}
        assert detail["file"]["path"] == "src/main.py"
        assert detail["associations"] == []
        assert detail["recent_findings"] == []
        assert detail["summary"]["total_findings"] == 0

    def test_get_file_detail_with_data(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "src/main.py", "rule_id": "E501", "severity": "high", "message": "Long"},
            ],
        )
        f = db.get_file_by_path("src/main.py")
        db.add_file_association(f.id, issue.id, "bug_in")
        detail = db.get_file_detail(f.id)
        assert len(detail["associations"]) == 1
        assert detail["associations"][0]["issue_title"] == "Fix bug"
        assert len(detail["recent_findings"]) == 1
        assert detail["recent_findings"][0]["severity"] == "high"
        assert detail["summary"]["high"] == 1

    def test_get_file_detail_raises_for_missing(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_file_detail("nonexistent")

    def test_recent_findings_capped_at_10(self, db: FiligreeDB) -> None:
        findings = [{"path": "big.py", "rule_id": f"E{i:03d}", "severity": "low", "message": f"Finding {i}"} for i in range(15)]
        db.process_scan_results(scan_source="ruff", findings=findings)
        f = db.get_file_by_path("big.py")
        detail = db.get_file_detail(f.id)
        assert len(detail["recent_findings"]) == 10
        assert detail["summary"]["total_findings"] == 15


class TestCreateIssuesExistingUnlinkedFailure:
    """Test that create_issues=True propagates exceptions on existing unlinked findings."""

    def test_create_issue_failure_propagates_on_existing_finding(self, db: FiligreeDB) -> None:
        """When create_issues=True and creating a bug issue fails for an existing
        unlinked finding, the exception propagates and scan writes are rolled back."""
        from unittest.mock import patch

        # First, ingest a finding without creating issues
        db.process_scan_results(
            scan_source="codex",
            create_issues=False,
            findings=[
                {
                    "path": "src/bad.py",
                    "rule_id": "logic-error",
                    "severity": "high",
                    "message": "Off-by-one in loop",
                    "line_start": 10,
                },
            ],
        )

        # Now re-ingest with create_issues=True, but patch create_issue to fail
        with (
            patch.object(db, "create_issue", side_effect=RuntimeError("DB write failed")),
            pytest.raises(RuntimeError, match="DB write failed"),
        ):
            db.process_scan_results(
                scan_source="codex",
                create_issues=True,
                findings=[
                    {
                        "path": "src/bad.py",
                        "rule_id": "logic-error",
                        "severity": "high",
                        "message": "Off-by-one in loop",
                        "line_start": 10,
                    },
                ],
            )


class TestHotspots:
    """Tests for the hotspots (triage prioritization) feature."""

    def test_hotspots_empty(self, db: FiligreeDB) -> None:
        result = db.get_file_hotspots()
        assert result == []

    def test_hotspots_ranks_by_severity_score(self, db: FiligreeDB) -> None:
        # File with critical findings should rank higher
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "critical.py", "rule_id": "S001", "severity": "critical", "message": "Security"},
                {"path": "low.py", "rule_id": "E501", "severity": "low", "message": "Style"},
                {"path": "low.py", "rule_id": "E502", "severity": "low", "message": "Style 2"},
                {"path": "low.py", "rule_id": "E503", "severity": "low", "message": "Style 3"},
            ],
        )
        result = db.get_file_hotspots()
        assert len(result) == 2
        # critical=10 > 3*low=3
        assert result[0]["file"]["path"] == "critical.py"
        assert result[0]["score"] > result[1]["score"]

    def test_hotspots_with_limit(self, db: FiligreeDB) -> None:
        for i in range(10):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"path": f"file{i}.py", "rule_id": "E501", "severity": "low", "message": "msg"}],
            )
        result = db.get_file_hotspots(limit=5)
        assert len(result) == 5

    def test_hotspots_includes_breakdown(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "S001", "severity": "critical", "message": "Critical"},
                {"path": "a.py", "rule_id": "S002", "severity": "high", "message": "High"},
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "Low"},
            ],
        )
        result = db.get_file_hotspots()
        assert result[0]["findings_breakdown"]["critical"] == 1
        assert result[0]["findings_breakdown"]["high"] == 1
        assert result[0]["findings_breakdown"]["low"] == 1

    def test_hotspots_only_counts_open_findings(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "high", "message": "High"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        # Mark finding as fixed
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (findings[0].id,))
        db.conn.commit()
        result = db.get_file_hotspots()
        assert result == []


class TestSortBySeverity:
    """Tests for sort=severity on findings."""

    def test_sort_findings_by_severity(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E001", "severity": "low", "message": "Low"},
                {"path": "a.py", "rule_id": "S001", "severity": "critical", "message": "Critical"},
                {"path": "a.py", "rule_id": "E501", "severity": "medium", "message": "Medium"},
                {"path": "a.py", "rule_id": "H001", "severity": "high", "message": "High"},
            ],
        )
        f = db.get_file_by_path("a.py")
        results = db.get_findings(f.id, sort="severity")
        severities = [r.severity for r in results]
        assert severities == ["critical", "high", "medium", "low"]

    def test_sort_findings_by_severity_paginated(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E001", "severity": "info", "message": "Info"},
                {"path": "a.py", "rule_id": "S001", "severity": "critical", "message": "Critical"},
                {"path": "a.py", "rule_id": "H001", "severity": "high", "message": "High"},
            ],
        )
        f = db.get_file_by_path("a.py")
        result = db.get_findings_paginated(f.id, sort="severity")
        severities = [r["severity"] for r in result["results"]]
        assert severities == ["critical", "high", "info"]

    def test_sort_findings_invalid_raises(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E001", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        with pytest.raises(ValueError, match="Invalid sort field"):
            db.get_findings(f.id, sort="bogus")
        with pytest.raises(ValueError, match="Invalid sort field"):
            db.get_findings_paginated(f.id, sort="bogus")

    def test_sort_findings_default_is_updated_at(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E001", "severity": "low", "message": "Low"},
                {"path": "a.py", "rule_id": "S001", "severity": "critical", "message": "Critical"},
            ],
        )
        f = db.get_file_by_path("a.py")
        # Default sort should not be by severity
        results = db.get_findings(f.id)
        # Should not crash, just returns in updated_at order
        assert len(results) == 2


class TestMinFindingsFilter:
    """Tests for min_findings filter on list_files."""

    def test_min_findings_filters_files(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "many.py", "rule_id": f"E{i}", "severity": "low", "message": f"msg{i}"} for i in range(5)],
        )
        db.register_file("empty.py")
        result = db.list_files_paginated(min_findings=3)
        assert result["total"] == 1
        assert result["results"][0]["path"] == "many.py"

    def test_min_findings_only_counts_open(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m1"},
                {"path": "a.py", "rule_id": "E2", "severity": "low", "message": "m2"},
                {"path": "a.py", "rule_id": "E3", "severity": "low", "message": "m3"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        # Mark 2 as fixed, leaving only 1 open
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (findings[0].id,))
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (findings[1].id,))
        db.conn.commit()
        result = db.list_files_paginated(min_findings=2)
        assert result["total"] == 0

    def test_min_findings_counts_acknowledged(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m1"},
                {"path": "a.py", "rule_id": "E2", "severity": "low", "message": "m2"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        # Mark one as acknowledged — should still count as active
        db.conn.execute("UPDATE scan_findings SET status = 'acknowledged' WHERE id = ?", (findings[0].id,))
        db.conn.commit()
        result = db.list_files_paginated(min_findings=2)
        assert result["total"] == 1  # Both findings are non-terminal

    def test_min_findings_counts_unseen_in_latest(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m1"},
                {"path": "a.py", "rule_id": "E2", "severity": "low", "message": "m2"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        # Mark one as unseen_in_latest — should still count as active
        db.conn.execute(
            "UPDATE scan_findings SET status = 'unseen_in_latest' WHERE id = ?",
            (findings[0].id,),
        )
        db.conn.commit()
        result = db.list_files_paginated(min_findings=2)
        assert result["total"] == 1  # Both findings are non-terminal

    def test_min_findings_zero_returns_all(self, db: FiligreeDB) -> None:
        db.register_file("a.py")
        db.register_file("b.py")
        result = db.list_files_paginated(min_findings=0)
        assert result["total"] == 2


class TestHasSeverityFilter:
    """Tests for has_severity filter on list_files."""

    def test_has_severity_critical_only_returns_critical_files(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "critical.py", "rule_id": "S1", "severity": "critical", "message": "bad"},
                {"path": "lowonly.py", "rule_id": "E1", "severity": "low", "message": "minor"},
                {"path": "highonly.py", "rule_id": "H1", "severity": "high", "message": "warn"},
            ],
        )
        result = db.list_files_paginated(has_severity="critical")
        assert result["total"] == 1
        assert result["results"][0]["path"] == "critical.py"

    def test_has_severity_high_returns_high_and_not_low(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "H1", "severity": "high", "message": "warn"},
                {"path": "b.py", "rule_id": "E1", "severity": "low", "message": "minor"},
            ],
        )
        result = db.list_files_paginated(has_severity="high")
        assert result["total"] == 1
        assert result["results"][0]["path"] == "a.py"

    def test_has_severity_ignores_fixed_findings(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "S1", "severity": "critical", "message": "bad"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (findings[0].id,))
        db.conn.commit()
        result = db.list_files_paginated(has_severity="critical")
        assert result["total"] == 0

    def test_has_severity_none_returns_all(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "S1", "severity": "critical", "message": "bad"},
                {"path": "b.py", "rule_id": "E1", "severity": "low", "message": "minor"},
            ],
        )
        db.register_file("empty.py")
        result = db.list_files_paginated(has_severity=None)
        assert result["total"] == 3

    def test_has_severity_invalid_is_ignored(self, db: FiligreeDB) -> None:
        db.register_file("a.py")
        result = db.list_files_paginated(has_severity="bogus")
        assert result["total"] == 1


class TestListFilesScanSourceFilter:
    """list_files_paginated scan_source filter shows only files with findings from that source."""

    def test_scan_source_filters_to_matching_files(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="codex",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "b.py", "rule_id": "R2", "severity": "low", "message": "m"}],
        )
        result = db.list_files_paginated(scan_source="codex")
        assert result["total"] == 1
        assert result["results"][0]["path"] == "a.py"

    def test_scan_source_none_returns_all(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="codex",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "b.py", "rule_id": "R2", "severity": "low", "message": "m"}],
        )
        result = db.list_files_paginated(scan_source=None)
        assert result["total"] == 2

    def test_scan_source_no_match_returns_empty(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        result = db.list_files_paginated(scan_source="codex")
        assert result["total"] == 0


class TestListFilesEnrichment:
    """list_files_paginated should return summary + associations_count per file."""

    def test_list_includes_summary_with_severity_counts(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E1", "severity": "critical", "message": "m1"},
                {"path": "a.py", "rule_id": "E2", "severity": "high", "message": "m2"},
                {"path": "a.py", "rule_id": "E3", "severity": "medium", "message": "m3"},
                {"path": "b.py", "rule_id": "E4", "severity": "low", "message": "m4"},
            ],
        )
        result = db.list_files_paginated()
        by_path = {r["path"]: r for r in result["results"]}
        a = by_path["a.py"]
        assert "summary" in a
        assert a["summary"]["critical"] == 1
        assert a["summary"]["high"] == 1
        assert a["summary"]["medium"] == 1
        assert a["summary"]["low"] == 0
        assert a["summary"]["open_findings"] == 3
        b = by_path["b.py"]
        assert b["summary"]["low"] == 1
        assert b["summary"]["critical"] == 0

    def test_list_includes_associations_count(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m"}],
        )
        f = db.get_file_by_path("a.py")
        issue = db.create_issue("Bug in a.py")
        db.add_file_association(f.id, issue.id, "bug_in")
        result = db.list_files_paginated()
        assert result["results"][0]["associations_count"] == 1

    def test_list_summary_excludes_fixed_findings(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E1", "severity": "critical", "message": "m1"},
                {"path": "a.py", "rule_id": "E2", "severity": "high", "message": "m2"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (findings[0].id,))
        db.conn.commit()
        result = db.list_files_paginated()
        s = result["results"][0]["summary"]
        assert s["open_findings"] == 1
        # The fixed finding should not be counted in severity buckets
        assert s["critical"] == 0
        assert s["high"] == 1

    def test_list_summary_empty_file(self, db: FiligreeDB) -> None:
        db.register_file("empty.py")
        result = db.list_files_paginated()
        s = result["results"][0]["summary"]
        assert s["open_findings"] == 0
        assert s["critical"] == 0
        assert result["results"][0]["associations_count"] == 0


class TestFileTimeline:
    """Tests for get_file_timeline() in core."""

    def test_timeline_empty_file(self, db: FiligreeDB) -> None:
        f = db.register_file("empty.py")
        result = db.get_file_timeline(f.id)
        assert result["results"] == []
        assert result["total"] == 0
        assert result["has_more"] is False

    def test_timeline_includes_finding_created(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "high", "message": "Long"}],
        )
        f = db.get_file_by_path("a.py")
        result = db.get_file_timeline(f.id)
        assert result["total"] >= 1
        types = [e["type"] for e in result["results"]]
        assert "finding_created" in types

    def test_timeline_includes_finding_updated(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "high", "message": "Long"}],
        )
        f = db.get_file_by_path("a.py")
        # Trigger an update by changing status
        findings = db.get_findings(f.id)
        db.conn.execute(
            "UPDATE scan_findings SET status = 'acknowledged', updated_at = '2099-01-01T00:00:00+00:00' WHERE id = ?",
            (findings[0].id,),
        )
        db.conn.commit()
        result = db.get_file_timeline(f.id)
        types = [e["type"] for e in result["results"]]
        assert "finding_updated" in types

    def test_timeline_includes_association(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py")
        issue = db.create_issue("Fix bug")
        db.add_file_association(f.id, issue.id, "bug_in")
        result = db.get_file_timeline(f.id)
        types = [e["type"] for e in result["results"]]
        assert "association_created" in types
        assoc_entry = next(e for e in result["results"] if e["type"] == "association_created")
        assert assoc_entry["data"]["issue_title"] == "Fix bug"

    def test_timeline_entries_have_deterministic_ids(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "msg"}],
        )
        f = db.get_file_by_path("a.py")
        r1 = db.get_file_timeline(f.id)
        r2 = db.get_file_timeline(f.id)
        ids1 = [e["id"] for e in r1["results"]]
        ids2 = [e["id"] for e in r2["results"]]
        assert ids1 == ids2
        # IDs should be 12-char hex strings
        for eid in ids1:
            assert len(eid) == 12
            int(eid, 16)  # must be valid hex

    def test_timeline_sorted_newest_first(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py")
        issue = db.create_issue("First")
        db.add_file_association(f.id, issue.id, "bug_in")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "msg"}],
        )
        result = db.get_file_timeline(f.id)
        timestamps = [e["timestamp"] for e in result["results"]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_timeline_pagination(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": f"E{i:03d}", "severity": "low", "message": f"msg{i}"} for i in range(10)],
        )
        f = db.get_file_by_path("a.py")
        result = db.get_file_timeline(f.id, limit=3)
        assert len(result["results"]) == 3
        assert result["total"] == 10
        assert result["has_more"] is True

        page2 = db.get_file_timeline(f.id, limit=3, offset=3)
        assert len(page2["results"]) == 3
        # No duplicate IDs across pages
        ids1 = {e["id"] for e in result["results"]}
        ids2 = {e["id"] for e in page2["results"]}
        assert ids1.isdisjoint(ids2)

    def test_timeline_raises_for_missing_file(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_file_timeline("nonexistent")

    def test_timeline_event_type_filter(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py")
        issue = db.create_issue("Fix it")
        db.add_file_association(f.id, issue.id, "bug_in")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m"}],
        )
        # Unfiltered should have both types
        all_events = db.get_file_timeline(f.id)
        types = {e["type"] for e in all_events["results"]}
        assert "finding_created" in types
        assert "association_created" in types

        # Filter to associations only
        assoc_only = db.get_file_timeline(f.id, event_type="association")
        for e in assoc_only["results"]:
            assert e["type"] == "association_created"
        assert assoc_only["total"] < all_events["total"]

        # Filter to findings only
        findings_only = db.get_file_timeline(f.id, event_type="finding")
        for e in findings_only["results"]:
            assert e["type"].startswith("finding_")
        assert findings_only["total"] < all_events["total"]


class TestFileMetadataEvents:
    """register_file should emit file_metadata_update events on field changes."""

    def test_metadata_event_on_language_change(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", language="python")
        db.register_file("a.py", language="python3")
        tl = db.get_file_timeline(f.id)
        meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
        assert len(meta_events) == 1
        assert meta_events[0]["data"]["field"] == "language"
        assert meta_events[0]["data"]["old_value"] == "python"
        assert meta_events[0]["data"]["new_value"] == "python3"

    def test_metadata_event_on_metadata_change(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", metadata={"k": "v1"})
        db.register_file("a.py", metadata={"k": "v2"})
        tl = db.get_file_timeline(f.id)
        meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
        assert len(meta_events) == 1
        assert meta_events[0]["data"]["field"] == "metadata"

    def test_no_event_when_no_change(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", language="python")
        db.register_file("a.py", language="python")
        tl = db.get_file_timeline(f.id)
        meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
        assert len(meta_events) == 0

    def test_no_event_when_metadata_key_order_differs(self, db: FiligreeDB) -> None:
        """JSON key ordering should not cause spurious metadata events."""
        f = db.register_file("a.py", metadata={"a": 1, "b": 2})
        db.register_file("a.py", metadata={"b": 2, "a": 1})
        tl = db.get_file_timeline(f.id)
        meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
        assert len(meta_events) == 0

    def test_no_event_on_first_registration(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", language="python")
        tl = db.get_file_timeline(f.id)
        meta_events = [e for e in tl["results"] if e["type"] == "file_metadata_update"]
        assert len(meta_events) == 0

    def test_timeline_filter_file_metadata_update(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py", language="python")
        db.register_file("a.py", language="python3")
        issue = db.create_issue("Fix it")
        db.add_file_association(f.id, issue.id, "bug_in")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m"}],
        )
        # All events
        all_tl = db.get_file_timeline(f.id)
        assert all_tl["total"] >= 3  # finding + association + metadata

        # Filter to metadata only
        meta_tl = db.get_file_timeline(f.id, event_type="file_metadata_update")
        assert meta_tl["total"] == 1
        assert all(e["type"] == "file_metadata_update" for e in meta_tl["results"])

    def test_unknown_event_type_returns_empty(self, db: FiligreeDB) -> None:
        f = db.register_file("a.py")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m"}],
        )
        result = db.get_file_timeline(f.id, event_type="bogus_type")
        assert result["total"] == 0
        assert result["results"] == []


class TestGlobalFindingsStats:
    """Tests for get_global_findings_stats()."""

    def test_global_stats_empty(self, db: FiligreeDB) -> None:
        stats = db.get_global_findings_stats()
        assert stats["total_findings"] == 0
        assert stats["open_findings"] == 0
        assert stats["files_with_findings"] == 0
        assert stats["critical"] == 0

    def test_global_stats_counts_all_files(self, db: FiligreeDB) -> None:
        # Create 15 files with findings — more than hotspot limit of 10
        for i in range(15):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"path": f"file{i}.py", "rule_id": "E1", "severity": "low", "message": "m"}],
            )
        stats = db.get_global_findings_stats()
        assert stats["files_with_findings"] == 15
        assert stats["total_findings"] == 15
        assert stats["open_findings"] == 15
        assert stats["low"] == 15

    def test_global_stats_severity_breakdown(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "S1", "severity": "critical", "message": "m"},
                {"path": "a.py", "rule_id": "S2", "severity": "high", "message": "m"},
                {"path": "b.py", "rule_id": "E1", "severity": "medium", "message": "m"},
                {"path": "c.py", "rule_id": "E2", "severity": "low", "message": "m"},
            ],
        )
        stats = db.get_global_findings_stats()
        assert stats["critical"] == 1
        assert stats["high"] == 1
        assert stats["medium"] == 1
        assert stats["low"] == 1
        assert stats["files_with_findings"] == 3

    def test_global_stats_excludes_fixed(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "S1", "severity": "critical", "message": "m"},
                {"path": "a.py", "rule_id": "E1", "severity": "low", "message": "m"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (findings[0].id,))
        db.conn.commit()
        stats = db.get_global_findings_stats()
        assert stats["open_findings"] == 1
        assert stats["critical"] == 0
        assert stats["low"] == 1


class TestPaginationMetadata:
    """Tests for paginated response format."""

    def test_list_files_returns_total(self, db: FiligreeDB) -> None:
        for i in range(10):
            db.register_file(f"file{i}.py")
        result = db.list_files_paginated(limit=3)
        assert result["total"] == 10
        assert result["limit"] == 3
        assert result["offset"] == 0
        assert result["has_more"] is True
        assert len(result["results"]) == 3

    def test_list_files_last_page(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.register_file(f"file{i}.py")
        result = db.list_files_paginated(limit=3, offset=3)
        assert result["total"] == 5
        assert result["has_more"] is False
        assert len(result["results"]) == 2

    def test_list_files_empty(self, db: FiligreeDB) -> None:
        result = db.list_files_paginated()
        assert result["total"] == 0
        assert result["has_more"] is False
        assert result["results"] == []

    def test_get_findings_returns_total(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": f"E{i}", "severity": "low", "message": f"msg{i}"} for i in range(10)],
        )
        f = db.get_file_by_path("a.py")
        result = db.get_findings_paginated(f.id, limit=5)
        assert result["total"] == 10
        assert result["has_more"] is True
        assert len(result["results"]) == 5

    def test_list_files_with_filter_and_total(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.register_file(f"src/file{i}.py", language="python")
        for i in range(3):
            db.register_file(f"lib/file{i}.js", language="javascript")
        result = db.list_files_paginated(language="python")
        assert result["total"] == 5
        assert len(result["results"]) == 5

    def test_list_files_paginated_escapes_like_wildcards(self, db: FiligreeDB) -> None:
        """path_prefix LIKE wildcards must be escaped in paginated variant too."""
        db.register_file("src/file_test.py")
        db.register_file("src/filextest.py")
        db.register_file("src/file%test.py")
        db.register_file("src/fileABCtest.py")

        result = db.list_files_paginated(path_prefix="file_test")
        assert result["total"] == 1
        assert result["results"][0]["path"] == "src/file_test.py"

        result = db.list_files_paginated(path_prefix="file%test")
        assert result["total"] == 1
        assert result["results"][0]["path"] == "src/file%test.py"


# ---------------------------------------------------------------------------
# _normalize_scan_path edge cases (filigree-7bff85)
# ---------------------------------------------------------------------------


class TestNormalizeScanPath:
    """Direct unit tests for _normalize_scan_path edge cases."""

    def test_empty_string(self) -> None:
        from filigree.core import _normalize_scan_path

        assert _normalize_scan_path("") == ""

    def test_dot_path(self) -> None:
        from filigree.core import _normalize_scan_path

        assert _normalize_scan_path(".") == ""

    def test_trailing_slash(self) -> None:
        from filigree.core import _normalize_scan_path

        assert _normalize_scan_path("src/main.py/") == "src/main.py"

    def test_double_slash(self) -> None:
        from filigree.core import _normalize_scan_path

        result = _normalize_scan_path("src//main.py")
        assert result == "src/main.py"

    def test_backslash_path(self) -> None:
        from filigree.core import _normalize_scan_path

        assert _normalize_scan_path(r"src\main.py") == "src/main.py"

    def test_dot_backslash_prefix(self) -> None:
        from filigree.core import _normalize_scan_path

        assert _normalize_scan_path(r".\src\main.py") == "src/main.py"

    def test_parent_traversal(self) -> None:
        from filigree.core import _normalize_scan_path

        assert _normalize_scan_path("src/../main.py") == "main.py"

    def test_normal_path_unchanged(self) -> None:
        from filigree.core import _normalize_scan_path

        assert _normalize_scan_path("src/main.py") == "src/main.py"


# ---------------------------------------------------------------------------
# get_scan_runs core-level tests (filigree-694a75)
# ---------------------------------------------------------------------------


class TestGetScanRunsCore:
    """Core-level unit tests for FiligreeDB.get_scan_runs()."""

    def test_empty_table(self, db: FiligreeDB) -> None:
        assert db.get_scan_runs() == []

    def test_excludes_empty_scan_run_id(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            scan_run_id="",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        assert db.get_scan_runs() == []

    def test_single_run(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="codex",
            scan_run_id="run-1",
            findings=[{"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"}],
        )
        runs = db.get_scan_runs()
        assert len(runs) == 1
        assert runs[0]["scan_run_id"] == "run-1"
        assert runs[0]["scan_source"] == "codex"
        assert runs[0]["total_findings"] == 1
        assert runs[0]["files_scanned"] == 1

    def test_multi_file_scan_run(self, db: FiligreeDB) -> None:
        """files_scanned should count distinct files, not total findings."""
        db.process_scan_results(
            scan_source="codex",
            scan_run_id="run-multi",
            findings=[
                {"path": "a.py", "rule_id": "R1", "severity": "low", "message": "m"},
                {"path": "a.py", "rule_id": "R2", "severity": "high", "message": "n"},
                {"path": "b.py", "rule_id": "R1", "severity": "low", "message": "m"},
            ],
        )
        runs = db.get_scan_runs()
        assert len(runs) == 1
        assert runs[0]["total_findings"] == 3
        assert runs[0]["files_scanned"] == 2

    def test_limit_parameter(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.process_scan_results(
                scan_source="ruff",
                scan_run_id=f"run-{i}",
                findings=[{"path": f"f{i}.py", "rule_id": "R1", "severity": "low", "message": "m"}],
            )
        runs = db.get_scan_runs(limit=2)
        assert len(runs) == 2


class TestCreateIssuesPartialFailure:
    """Test that create_issues=True handles mid-batch failures correctly.

    When create_issues=True and the inner create_issue() fails mid-way
    through a batch, process_scan_results raises the exception and rolls
    back uncommitted scan data. Because create_issue() commits its own
    transaction internally, earlier successful issues are already persisted
    -- this is inherent to the current architecture.
    """

    def test_create_issue_failure_raises_and_rolls_back_uncommitted(self, db: FiligreeDB) -> None:
        """When create_issues=True and bug creation fails for the second finding,
        the exception propagates and uncommitted scan writes are rolled back.

        The first finding's issue (committed by create_issue) persists, but
        the second finding's file/finding data (uncommitted) does not.
        """
        from unittest.mock import patch

        original_create = db.create_issue
        call_count = 0

        def failing_create(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise RuntimeError("Simulated issue creation failure")
            return original_create(*args, **kwargs)

        with (
            patch.object(db, "create_issue", side_effect=failing_create),
            pytest.raises(RuntimeError, match="Simulated issue creation failure"),
        ):
            db.process_scan_results(
                scan_source="codex",
                create_issues=True,
                findings=[
                    {
                        "path": "src/a.py",
                        "rule_id": "logic-error-1",
                        "severity": "high",
                        "message": "Off by one",
                        "line_start": 10,
                    },
                    {
                        "path": "src/b.py",
                        "rule_id": "logic-error-2",
                        "severity": "critical",
                        "message": "Null deref",
                        "line_start": 20,
                    },
                ],
            )

        # First finding's issue was committed by create_issue() -- it persists
        issues = db.list_issues()
        assert len(issues) == 1
        assert "Off by one" in issues[0].title

        # First finding's file/finding also committed (same txn as issue)
        file_a = db.get_file_by_path("src/a.py")
        assert file_a is not None

        # Second finding's file was never committed -- rolled back
        file_b = db.get_file_by_path("src/b.py")
        assert file_b is None

    def test_create_issues_succeeds_for_two_findings(self, db: FiligreeDB) -> None:
        """Baseline: create_issues=True for multiple findings creates all issues."""
        result = db.process_scan_results(
            scan_source="codex",
            create_issues=True,
            findings=[
                {
                    "path": "src/a.py",
                    "rule_id": "logic-error-1",
                    "severity": "high",
                    "message": "Off by one",
                    "line_start": 10,
                },
                {
                    "path": "src/b.py",
                    "rule_id": "logic-error-2",
                    "severity": "critical",
                    "message": "Null deref",
                    "line_start": 20,
                },
            ],
        )
        assert result["issues_created"] == 2
        assert len(result["issue_ids"]) == 2
        assert result["findings_created"] == 2
