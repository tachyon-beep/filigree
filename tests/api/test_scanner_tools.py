"""Tests for new scanner MCP tools — get_scan_status, preview_scan, trigger_scan_batch."""

from __future__ import annotations

from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from filigree.db_scans import SCAN_COOLDOWN_SECONDS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_run(
    db: FiligreeDB,
    *,
    run_id: str = "run-1",
    scanner: str = "codex",
    file_paths: list[str] | None = None,
    file_ids: list[str] | None = None,
    pid: int | None = None,
    log_path: str = "",
) -> None:
    db.create_scan_run(
        scan_run_id=run_id,
        scanner_name=scanner,
        scan_source=scanner,
        file_paths=file_paths or ["src/main.py"],
        file_ids=file_ids or ["f-1"],
        pid=pid,
        log_path=log_path,
    )


# ---------------------------------------------------------------------------
# TestGetScanStatus (original 2 methods + extensions)
# ---------------------------------------------------------------------------


class TestGetScanStatus:
    def test_returns_status_with_process_info(self, db: FiligreeDB) -> None:
        _create_run(db)
        status = db.get_scan_status("run-1")
        assert status["id"] == "run-1"
        assert status["process_alive"] is False
        assert isinstance(status["log_tail"], list)

    def test_not_found_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_scan_status("no-such-run")

    def test_pending_run_has_no_process_alive(self, db: FiligreeDB) -> None:
        _create_run(db, pid=99999)
        # Status is 'pending', so process_alive check is skipped regardless of PID
        status = db.get_scan_status("run-1")
        assert status["status"] == "pending"
        assert status["process_alive"] is False

    def test_running_run_with_dead_pid_auto_fails(self, db: FiligreeDB) -> None:
        # Use a PID extremely unlikely to exist
        _create_run(db, pid=9999991)
        db.update_scan_run_status("run-1", "running")
        status = db.get_scan_status("run-1")
        # ProcessLookupError → auto-transition to failed
        assert status["status"] == "failed"
        assert status["process_alive"] is False
        assert "died" in (status["error_message"] or "")

    def test_log_tail_reads_from_log_file(self, tmp_path: Path) -> None:
        # Use a packs-based DB so db_path is .filigree/filigree.db and
        # db_path.parent.parent (project root) == tmp_path — a stable anchor
        # for constructing log-path fixtures.
        from tests._db_factory import make_db

        project_db = make_db(tmp_path, packs=["core"])
        try:
            log_dir = tmp_path / ".filigree" / "scans"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "run-log.log"
            log_file.write_text("line1\nline2\nline3\n")

            # log_path is stored relative to project root (tmp_path)
            log_rel = str(log_file.relative_to(tmp_path))
            _create_run(project_db, log_path=log_rel)

            status = project_db.get_scan_status("run-1")
            assert status["log_tail"] == ["line1", "line2", "line3"]
        finally:
            project_db.close()

    def test_log_tail_truncated_to_log_lines(self, tmp_path: Path) -> None:
        from tests._db_factory import make_db

        project_db = make_db(tmp_path, packs=["core"])
        try:
            log_dir = tmp_path / ".filigree" / "scans"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "run-many.log"
            log_file.write_text("\n".join(f"line{i}" for i in range(100)))

            log_rel = str(log_file.relative_to(tmp_path))
            _create_run(project_db, log_path=log_rel)

            status = project_db.get_scan_status("run-1", log_lines=5)
            assert len(status["log_tail"]) == 5
            # Should be the last 5 lines
            assert status["log_tail"][0] == "line95"
        finally:
            project_db.close()

    def test_missing_log_file_returns_empty_tail(self, db: FiligreeDB) -> None:
        _create_run(db, log_path=".filigree/scans/nonexistent.log")
        status = db.get_scan_status("run-1")
        assert status["log_tail"] == []

    def test_batch_run_data_warning_included(self, db: FiligreeDB) -> None:
        _create_run(
            db,
            file_paths=["src/a.py", "src/b.py"],
            file_ids=["f-1", "f-2"],
        )
        status = db.get_scan_status("run-1")
        assert any("Batch scan" in w for w in status["data_warnings"])

    def test_completed_run_has_no_process_alive(self, db: FiligreeDB) -> None:
        _create_run(db)
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "completed")
        status = db.get_scan_status("run-1")
        assert status["status"] == "completed"
        assert status["process_alive"] is False


# ---------------------------------------------------------------------------
# TestCreateScanRun
# ---------------------------------------------------------------------------


class TestCreateScanRun:
    def test_creates_pending_run(self, db: FiligreeDB) -> None:
        run = db.create_scan_run(
            scan_run_id="run-2",
            scanner_name="mysc",
            scan_source="mysc",
            file_paths=["a.py"],
            file_ids=["fid-a"],
        )
        assert run["id"] == "run-2"
        assert run["status"] == "pending"
        assert run["scanner_name"] == "mysc"

    def test_duplicate_id_raises_value_error(self, db: FiligreeDB) -> None:
        _create_run(db)
        with pytest.raises(ValueError, match="already exists"):
            _create_run(db)

    def test_multiple_file_paths_stored(self, db: FiligreeDB) -> None:
        run = db.create_scan_run(
            scan_run_id="batch-run",
            scanner_name="sc",
            scan_source="sc",
            file_paths=["src/a.py", "src/b.py", "src/c.py"],
            file_ids=["f1", "f2", "f3"],
        )
        assert run["file_paths"] == ["src/a.py", "src/b.py", "src/c.py"]
        assert run["file_ids"] == ["f1", "f2", "f3"]

    def test_optional_fields_stored(self, db: FiligreeDB) -> None:
        run = db.create_scan_run(
            scan_run_id="run-opts",
            scanner_name="sc",
            scan_source="sc",
            file_paths=["a.py"],
            file_ids=["f1"],
            pid=1234,
            api_url="http://localhost:8377",
            log_path=".filigree/scans/run-opts.log",
        )
        assert run["pid"] == 1234
        assert run["api_url"] == "http://localhost:8377"
        assert run["log_path"] == ".filigree/scans/run-opts.log"


# ---------------------------------------------------------------------------
# TestUpdateScanRunStatus
# ---------------------------------------------------------------------------


class TestUpdateScanRunStatus:
    def test_pending_to_running(self, db: FiligreeDB) -> None:
        _create_run(db)
        run = db.update_scan_run_status("run-1", "running")
        assert run["status"] == "running"

    def test_running_to_completed(self, db: FiligreeDB) -> None:
        _create_run(db)
        db.update_scan_run_status("run-1", "running")
        run = db.update_scan_run_status("run-1", "completed", findings_count=5)
        assert run["status"] == "completed"
        assert run["findings_count"] == 5
        assert run["completed_at"] is not None

    def test_running_to_failed_with_error(self, db: FiligreeDB) -> None:
        _create_run(db)
        db.update_scan_run_status("run-1", "running")
        run = db.update_scan_run_status(
            "run-1",
            "failed",
            exit_code=1,
            error_message="scanner crashed",
        )
        assert run["status"] == "failed"
        assert run["exit_code"] == 1
        assert run["error_message"] == "scanner crashed"

    def test_pending_to_failed_directly(self, db: FiligreeDB) -> None:
        _create_run(db)
        run = db.update_scan_run_status("run-1", "failed")
        assert run["status"] == "failed"

    def test_invalid_transition_raises(self, db: FiligreeDB) -> None:
        _create_run(db)
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "completed")
        with pytest.raises(ValueError, match="Invalid transition"):
            db.update_scan_run_status("run-1", "running")

    def test_invalid_status_string_raises(self, db: FiligreeDB) -> None:
        _create_run(db)
        with pytest.raises(ValueError, match="Invalid scan run status"):
            db.update_scan_run_status("run-1", "unknown-status")  # type: ignore[arg-type]

    def test_missing_run_raises_key_error(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.update_scan_run_status("no-such-run", "running")

    def test_running_to_timeout(self, db: FiligreeDB) -> None:
        _create_run(db)
        db.update_scan_run_status("run-1", "running")
        run = db.update_scan_run_status("run-1", "timeout")
        assert run["status"] == "timeout"
        assert run["completed_at"] is not None


# ---------------------------------------------------------------------------
# TestScanCooldown
# ---------------------------------------------------------------------------


class TestScanCooldown:
    def test_no_cooldown_when_no_prior_run(self, db: FiligreeDB) -> None:
        result = db.check_scan_cooldown("mysc", "src/main.py")
        assert result is None

    def test_cooldown_blocked_by_pending_run(self, db: FiligreeDB) -> None:
        _create_run(db)  # status=pending, just created → within cooldown window
        blocking = db.check_scan_cooldown("codex", "src/main.py")
        assert blocking is not None
        assert blocking["id"] == "run-1"

    def test_cooldown_blocked_by_running_run(self, db: FiligreeDB) -> None:
        _create_run(db)
        db.update_scan_run_status("run-1", "running")
        blocking = db.check_scan_cooldown("codex", "src/main.py")
        assert blocking is not None

    def test_cooldown_blocked_by_completed_run(self, db: FiligreeDB) -> None:
        _create_run(db)
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "completed")
        blocking = db.check_scan_cooldown("codex", "src/main.py")
        assert blocking is not None

    def test_failed_run_does_not_block(self, db: FiligreeDB) -> None:
        _create_run(db)
        db.update_scan_run_status("run-1", "failed")
        result = db.check_scan_cooldown("codex", "src/main.py")
        assert result is None

    def test_different_scanner_does_not_block(self, db: FiligreeDB) -> None:
        _create_run(db, scanner="scanner-a")
        result = db.check_scan_cooldown("scanner-b", "src/main.py")
        assert result is None

    def test_different_file_does_not_block(self, db: FiligreeDB) -> None:
        _create_run(db, file_paths=["src/other.py"])
        result = db.check_scan_cooldown("codex", "src/main.py")
        assert result is None

    def test_cooldown_constant_is_30_seconds(self) -> None:
        assert SCAN_COOLDOWN_SECONDS == 30


# ---------------------------------------------------------------------------
# TestGetScanRun
# ---------------------------------------------------------------------------


class TestGetScanRun:
    def test_round_trips_all_fields(self, db: FiligreeDB) -> None:
        db.create_scan_run(
            scan_run_id="full-run",
            scanner_name="sc",
            scan_source="sc",
            file_paths=["x.py", "y.py"],
            file_ids=["f1", "f2"],
            pid=42,
            api_url="http://localhost:8377",
            log_path=".filigree/scans/full-run.log",
        )
        run = db.get_scan_run("full-run")
        assert run["id"] == "full-run"
        assert run["scanner_name"] == "sc"
        assert run["file_paths"] == ["x.py", "y.py"]
        assert run["file_ids"] == ["f1", "f2"]
        assert run["pid"] == 42
        assert run["api_url"] == "http://localhost:8377"
        assert run["status"] == "pending"
        assert run["exit_code"] is None
        assert run["findings_count"] == 0
        assert run["error_message"] == ""
        assert run["data_warnings"] == []

    def test_not_found_raises_key_error(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_scan_run("missing")

    def test_empty_log_path_defaults_to_empty_string(self, db: FiligreeDB) -> None:
        _create_run(db)
        run = db.get_scan_run("run-1")
        assert run["log_path"] == ""

    def test_pid_none_stored_and_retrieved(self, db: FiligreeDB) -> None:
        _create_run(db, pid=None)
        run = db.get_scan_run("run-1")
        assert run["pid"] is None


# ---------------------------------------------------------------------------
# TestScanRunStatusEdgeCases
# ---------------------------------------------------------------------------


class TestScanRunStatusEdgeCases:
    def test_get_scan_status_returns_status_dict_keys(self, db: FiligreeDB) -> None:
        _create_run(db)
        status = db.get_scan_status("run-1")
        required_keys = {
            "id",
            "status",
            "scanner_name",
            "file_paths",
            "file_ids",
            "process_alive",
            "log_tail",
            "started_at",
            "updated_at",
        }
        for key in required_keys:
            assert key in status, f"Missing key: {key}"

    def test_empty_log_path_gives_empty_tail(self, db: FiligreeDB) -> None:
        _create_run(db, log_path="")
        status = db.get_scan_status("run-1")
        assert status["log_tail"] == []

    def test_single_file_no_batch_warning(self, db: FiligreeDB) -> None:
        _create_run(db, file_paths=["src/single.py"], file_ids=["f-1"])
        status = db.get_scan_status("run-1")
        assert not any("Batch scan" in w for w in status["data_warnings"])

    def test_status_reflects_failed_transition(self, db: FiligreeDB) -> None:
        _create_run(db)
        db.update_scan_run_status("run-1", "running")
        db.update_scan_run_status("run-1", "failed", exit_code=2, error_message="timeout")
        status = db.get_scan_status("run-1")
        assert status["status"] == "failed"
        assert status["exit_code"] == 2
        assert status["error_message"] == "timeout"

    def test_multiple_batch_files_status(self, db: FiligreeDB) -> None:
        paths = [f"src/file{i}.py" for i in range(5)]
        ids = [f"f-{i}" for i in range(5)]
        db.create_scan_run(
            scan_run_id="batch-5",
            scanner_name="sc",
            scan_source="sc",
            file_paths=paths,
            file_ids=ids,
        )
        status = db.get_scan_status("batch-5")
        assert status["file_paths"] == paths
        # Batch warning mentions remaining count
        warnings = status["data_warnings"]
        assert any("4" in w for w in warnings)
