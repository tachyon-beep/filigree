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
