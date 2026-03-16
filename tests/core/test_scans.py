"""Tests for ScansMixin — scan run lifecycle tracking."""

from __future__ import annotations

import pytest

from filigree.core import FiligreeDB


class TestCreateScanRun:
    def test_create_returns_dict(self, db: FiligreeDB) -> None:
        run = db.create_scan_run(
            scan_run_id="test-run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        assert run["id"] == "test-run-1"
        assert run["scanner_name"] == "codex"
        assert run["status"] == "pending"
        assert run["file_paths"] == ["src/main.py"]

    def test_create_duplicate_raises(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="test-run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        with pytest.raises(ValueError, match="already exists"):
            db.create_scan_run(
                scan_run_id="test-run-1",
                scanner_name="codex",
                scan_source="codex",
                file_paths=["src/main.py"],
                file_ids=["f-1"],
            )


class TestUpdateScanRunStatus:
    def test_transition_pending_to_running(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
            pid=1234,
        )
        db.update_scan_run_status("run-1", "running")
        run = db.get_scan_run("run-1")
        assert run["status"] == "running"

    def test_transition_running_to_completed(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "completed", exit_code=0, findings_count=5)
        run = db.get_scan_run("run-1")
        assert run["status"] == "completed"
        assert run["exit_code"] == 0
        assert run["findings_count"] == 5
        assert run["completed_at"] is not None

    def test_transition_running_to_failed(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=[],
            file_ids=[],
        )
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "failed", error_message="crash")
        run = db.get_scan_run("run-1")
        assert run["status"] == "failed"
        assert run["error_message"] == "crash"

    def test_invalid_transition_raises(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=[],
            file_ids=[],
        )
        with pytest.raises(ValueError, match="Invalid transition"):
            db.update_scan_run_status("run-1", "completed")

    def test_not_found_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.update_scan_run_status("no-such-run", "running")


class TestGetScanRun:
    def test_get_returns_dict(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["a.py", "b.py"],
            file_ids=["f-1", "f-2"],
        )
        run = db.get_scan_run("run-1")
        assert run["id"] == "run-1"
        assert run["file_paths"] == ["a.py", "b.py"]

    def test_get_not_found_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_scan_run("no-such-run")


class TestCooldownCheck:
    def test_no_recent_run_allows_trigger(self, db: FiligreeDB) -> None:
        assert db.check_scan_cooldown("codex", "src/main.py") is None

    def test_running_scan_blocks(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        db.update_scan_run_status("run-1", "running")
        result = db.check_scan_cooldown("codex", "src/main.py")
        assert result is not None  # returns blocking run info

    def test_failed_scan_does_not_block(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "failed")
        assert db.check_scan_cooldown("codex", "src/main.py") is None


class TestGetScanStatus:
    def test_returns_status_with_process_info(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        status = db.get_scan_status("run-1")
        assert status["id"] == "run-1"
        assert status["process_alive"] is False
        assert isinstance(status["log_tail"], list)

    def test_not_found_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_scan_status("no-such-run")

    def test_batch_scan_warns_about_partial_pid_monitoring(self, db: FiligreeDB) -> None:
        """Batch scans (multiple file_paths) include a data_warnings note."""
        db.create_scan_run(
            scan_run_id="batch-1",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["a.py", "b.py", "c.py"],
            file_ids=["f-1", "f-2", "f-3"],
            pid=99999,
        )
        status = db.get_scan_status("batch-1")
        assert any("1 of 3" in w for w in status["data_warnings"])


class TestCorruptScanRunJson:
    """Corrupt JSON in scan_runs is handled gracefully with data_warnings."""

    def test_corrupt_file_paths_returns_empty_with_warning(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-corrupt",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["a.py"],
            file_ids=["f-1"],
        )
        # Corrupt the file_paths JSON directly
        db.conn.execute(
            "UPDATE scan_runs SET file_paths = 'not-valid-json' WHERE id = ?",
            ("run-corrupt",),
        )
        db.conn.commit()
        run = db.get_scan_run("run-corrupt")
        assert run["file_paths"] == []
        assert any("file_paths" in w for w in run["data_warnings"])

    def test_corrupt_file_ids_returns_empty_with_warning(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-corrupt-ids",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["a.py"],
            file_ids=["f-1"],
        )
        db.conn.execute(
            "UPDATE scan_runs SET file_ids = '{broken' WHERE id = ?",
            ("run-corrupt-ids",),
        )
        db.conn.commit()
        run = db.get_scan_run("run-corrupt-ids")
        assert run["file_ids"] == []
        assert any("file_ids" in w for w in run["data_warnings"])

    def test_valid_json_has_no_warnings(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-ok",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["a.py"],
            file_ids=["f-1"],
        )
        run = db.get_scan_run("run-ok")
        assert run["data_warnings"] == []


class TestScanRunTimeout:
    """The running -> timeout transition is valid."""

    def test_transition_running_to_timeout(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="run-t",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["x.py"],
            file_ids=["f-1"],
        )
        db.update_scan_run_status("run-t", "running")
        db.update_scan_run_status("run-t", "timeout", error_message="Exceeded 300s limit")
        run = db.get_scan_run("run-t")
        assert run["status"] == "timeout"
        assert run["completed_at"] is not None
        assert run["error_message"] == "Exceeded 300s limit"


class TestCooldownMultiFile:
    """json_each cooldown correctly matches individual files in arrays."""

    def test_cooldown_matches_specific_file_in_batch(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="batch-cd",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py", "src/other.py", "src/utils.py"],
            file_ids=["f-1", "f-2", "f-3"],
        )
        db.update_scan_run_status("batch-cd", "running")
        # Should block for a file that's in the array
        assert db.check_scan_cooldown("codex", "src/other.py") is not None
        # Should not block for a file not in the array
        assert db.check_scan_cooldown("codex", "src/different.py") is None

    def test_cooldown_no_prefix_false_positive(self, db: FiligreeDB) -> None:
        """json_each matches exactly, not as prefix like LIKE would."""
        db.create_scan_run(
            scan_run_id="batch-prefix",
            scanner_name="codex",
            scan_source="codex",
            file_paths=["src/main.py"],
            file_ids=["f-1"],
        )
        db.update_scan_run_status("batch-prefix", "running")
        # "src/main.py.bak" should NOT match "src/main.py"
        assert db.check_scan_cooldown("codex", "src/main.py.bak") is None
