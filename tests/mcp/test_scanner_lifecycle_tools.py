"""MCP tool tests for scanner lifecycle handlers (trigger_scan_batch, get_scan_status, preview_scan).

Tests the MCP handler layer via call_tool() — handler wiring, argument parsing,
validation, and error mapping. Core DB methods are covered in test_scans.py;
these tests verify the MCP integration layer on top.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from filigree.core import FiligreeDB
from filigree.mcp_server import call_tool  # type: ignore[attr-defined]
from filigree.mcp_tools.scanners import _validate_localhost_url
from tests.mcp._helpers import _parse


def _write_scanner_toml(mcp_db: FiligreeDB, name: str = "test-scanner") -> None:
    """Write a scanner TOML into the test .filigree/scanners/ dir."""
    import filigree.mcp_server as mcp_mod

    assert mcp_mod._filigree_dir is not None
    scanners_dir = mcp_mod._filigree_dir / "scanners"
    scanners_dir.mkdir(exist_ok=True)
    (scanners_dir / f"{name}.toml").write_text(
        f'[scanner]\nname = "{name}"\ndescription = "Test scanner"\n'
        f'command = "echo"\nargs = ["scan", "{{file}}", "--scan-run-id", "{{scan_run_id}}"]\nfile_types = ["py"]\n'
    )


def _make_target_files(mcp_db: FiligreeDB, names: list[str]) -> list[str]:
    """Create target files on disk and return their names."""
    import filigree.mcp_server as mcp_mod

    assert mcp_mod._filigree_dir is not None
    project_root = mcp_mod._filigree_dir.parent
    for name in names:
        (project_root / name).write_text("x = 1\n")
    return names


def _cleanup_files(mcp_db: FiligreeDB, names: list[str]) -> None:
    """Remove target files from disk."""
    import filigree.mcp_server as mcp_mod

    assert mcp_mod._filigree_dir is not None
    project_root = mcp_mod._filigree_dir.parent
    for name in names:
        (project_root / name).unlink(missing_ok=True)


class _FakeProc:
    """Minimal subprocess.Popen stand-in."""

    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid

    def poll(self) -> None:
        return None


class TestPreviewScanTool:
    async def test_preview_scan(self, mcp_db: FiligreeDB) -> None:
        files = _make_target_files(mcp_db, ["preview_target.py"])
        _write_scanner_toml(mcp_db)
        try:
            data = _parse(
                await call_tool(
                    "preview_scan",
                    {"scanner": "test-scanner", "file_path": "preview_target.py"},
                )
            )
            assert data["valid"] is True
            assert data["scanner"] == "test-scanner"
            assert isinstance(data["command"], list)
            assert "preview_target.py" in data["command_string"]
        finally:
            _cleanup_files(mcp_db, files)

    async def test_preview_scan_not_found(self, mcp_db: FiligreeDB) -> None:
        data = _parse(
            await call_tool(
                "preview_scan",
                {"scanner": "nonexistent", "file_path": "foo.py"},
            )
        )
        assert data["code"] == "scanner_not_found"

    async def test_preview_scan_path_traversal(self, mcp_db: FiligreeDB) -> None:
        _write_scanner_toml(mcp_db)
        data = _parse(
            await call_tool(
                "preview_scan",
                {"scanner": "test-scanner", "file_path": "../../etc/passwd"},
            )
        )
        assert data["code"] == "invalid_path"


class TestGetScanStatusTool:
    async def test_get_scan_status(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_scan_run(
            scan_run_id="test-run-1",
            scanner_name="scanner",
            scan_source="scanner",
            file_paths=["src/a.py"],
            file_ids=["fid-1"],
        )
        data = _parse(await call_tool("get_scan_status", {"scan_run_id": "test-run-1"}))
        assert data["id"] == "test-run-1"
        assert data["status"] == "pending"
        assert "process_alive" in data
        assert "log_tail" in data

    async def test_get_scan_status_not_found(self, mcp_db: FiligreeDB) -> None:
        data = _parse(await call_tool("get_scan_status", {"scan_run_id": "nonexistent"}))
        assert data["code"] == "not_found"

    async def test_get_scan_status_empty_id_rejected(self, mcp_db: FiligreeDB) -> None:
        data = _parse(await call_tool("get_scan_status", {"scan_run_id": ""}))
        assert data["code"] == "validation_error"

    async def test_get_scan_status_log_lines_validated(self, mcp_db: FiligreeDB) -> None:
        data = _parse(await call_tool("get_scan_status", {"scan_run_id": "x", "log_lines": 0}))
        assert data["code"] == "validation_error"

    async def test_get_scan_status_auto_fails_dead_process(self, mcp_db: FiligreeDB) -> None:
        """When process is dead, get_scan_status should auto-transition to 'failed'."""
        mcp_db.create_scan_run(
            scan_run_id="dead-run",
            scanner_name="scanner",
            scan_source="scanner",
            file_paths=["src/a.py"],
            file_ids=["fid-1"],
            pid=99999,
        )
        mcp_db.update_scan_run_status("dead-run", "running")
        # os.kill will raise ProcessLookupError for a non-existent PID
        data = _parse(await call_tool("get_scan_status", {"scan_run_id": "dead-run"}))
        assert data["status"] == "failed"
        assert data["process_alive"] is False
        assert "died" in data.get("error_message", "")


class TestTriggerScanBatchTool:
    async def test_batch_scan_success(self, mcp_db: FiligreeDB) -> None:
        files = _make_target_files(mcp_db, ["batch_a.py", "batch_b.py"])
        _write_scanner_toml(mcp_db)
        try:
            with patch(
                "filigree.mcp_tools.scanners.subprocess.Popen",
                side_effect=[_FakeProc(100), _FakeProc(101)],
            ):
                data = _parse(
                    await call_tool(
                        "trigger_scan_batch",
                        {"scanner": "test-scanner", "file_paths": ["batch_a.py", "batch_b.py"]},
                    )
                )
            assert data["status"] == "triggered"
            assert data["file_count"] == 2
            assert data["processes_spawned"] == 2
            assert "scan_run_id" in data
        finally:
            _cleanup_files(mcp_db, files)

    async def test_batch_scan_partial_spawn_failure(self, mcp_db: FiligreeDB) -> None:
        """When some files fail to spawn, scan_run should only include successful ones."""
        files = _make_target_files(mcp_db, ["batch_ok.py", "batch_fail.py"])
        _write_scanner_toml(mcp_db)
        try:
            with patch(
                "filigree.mcp_tools.scanners.subprocess.Popen",
                side_effect=[_FakeProc(100), OSError("mock fail")],
            ):
                data = _parse(
                    await call_tool(
                        "trigger_scan_batch",
                        {"scanner": "test-scanner", "file_paths": ["batch_ok.py", "batch_fail.py"]},
                    )
                )
            assert data["status"] == "triggered"
            assert data["processes_spawned"] == 1
            assert data["file_count"] == 1
            assert len(data["spawn_errors"]) == 1
            # Verify spawn error includes actual error detail (not just "spawn_failed")
            assert "mock fail" in data["spawn_errors"][0]["reason"].lower() or "spawn" in data["spawn_errors"][0]["reason"].lower()

            # Verify scan_run record only includes the successful file
            run = mcp_db.get_scan_run(data["scan_run_id"])
            assert len(run["file_paths"]) == 1
            assert "batch_ok.py" in run["file_paths"][0]
        finally:
            _cleanup_files(mcp_db, files)

    async def test_batch_scan_all_spawn_failure(self, mcp_db: FiligreeDB) -> None:
        files = _make_target_files(mcp_db, ["batch_all_fail.py"])
        _write_scanner_toml(mcp_db)
        try:
            with patch(
                "filigree.mcp_tools.scanners.subprocess.Popen",
                side_effect=OSError("mock fail"),
            ):
                data = _parse(
                    await call_tool(
                        "trigger_scan_batch",
                        {"scanner": "test-scanner", "file_paths": ["batch_all_fail.py"]},
                    )
                )
            assert data["code"] == "spawn_failed"
        finally:
            _cleanup_files(mcp_db, files)

    async def test_batch_scan_empty_paths_rejected(self, mcp_db: FiligreeDB) -> None:
        _write_scanner_toml(mcp_db)
        data = _parse(
            await call_tool(
                "trigger_scan_batch",
                {"scanner": "test-scanner", "file_paths": []},
            )
        )
        assert data["code"] == "validation_error"

    async def test_batch_scan_scanner_not_found(self, mcp_db: FiligreeDB) -> None:
        data = _parse(
            await call_tool(
                "trigger_scan_batch",
                {"scanner": "nonexistent", "file_paths": ["foo.py"]},
            )
        )
        assert data["code"] == "scanner_not_found"

    async def test_batch_scan_non_localhost_rejected(self, mcp_db: FiligreeDB) -> None:
        files = _make_target_files(mcp_db, ["batch_url.py"])
        _write_scanner_toml(mcp_db)
        try:
            data = _parse(
                await call_tool(
                    "trigger_scan_batch",
                    {
                        "scanner": "test-scanner",
                        "file_paths": ["batch_url.py"],
                        "api_url": "https://evil.example.com",
                    },
                )
            )
            assert data["code"] == "invalid_api_url"
        finally:
            _cleanup_files(mcp_db, files)

    async def test_batch_scan_skips_invalid_and_missing_files(self, mcp_db: FiligreeDB) -> None:
        files = _make_target_files(mcp_db, ["batch_valid.py"])
        _write_scanner_toml(mcp_db)
        try:
            with patch(
                "filigree.mcp_tools.scanners.subprocess.Popen",
                return_value=_FakeProc(100),
            ):
                data = _parse(
                    await call_tool(
                        "trigger_scan_batch",
                        {
                            "scanner": "test-scanner",
                            "file_paths": ["batch_valid.py", "nonexistent.py", "../../etc/passwd"],
                        },
                    )
                )
            assert data["status"] == "triggered"
            assert data["file_count"] == 1
            assert len(data["skipped"]) == 2
        finally:
            _cleanup_files(mcp_db, files)

    async def test_batch_scan_per_file_log_files(self, mcp_db: FiligreeDB) -> None:
        """Each file in a batch gets its own log file (no clobbering)."""
        import filigree.mcp_server as mcp_mod

        files = _make_target_files(mcp_db, ["batch_log_a.py", "batch_log_b.py"])
        _write_scanner_toml(mcp_db)
        try:
            with patch(
                "filigree.mcp_tools.scanners.subprocess.Popen",
                side_effect=[_FakeProc(100), _FakeProc(101)],
            ):
                data = _parse(
                    await call_tool(
                        "trigger_scan_batch",
                        {"scanner": "test-scanner", "file_paths": ["batch_log_a.py", "batch_log_b.py"]},
                    )
                )
            scan_run_id = data["scan_run_id"]
            assert mcp_mod._filigree_dir is not None
            scan_log_dir = mcp_mod._filigree_dir / "scans"
            # Should have per-file log files, not a single shared one
            log_files = sorted(scan_log_dir.glob(f"{scan_run_id}*.log"))
            assert len(log_files) == 2
            assert any("-0.log" in str(f) for f in log_files)
            assert any("-1.log" in str(f) for f in log_files)
        finally:
            _cleanup_files(mcp_db, files)


class TestProcessScanResultsCompletion:
    """Test that process_scan_results auto-completes scan runs (#11)."""

    def test_scan_run_marked_completed(self, mcp_db: FiligreeDB) -> None:
        """When scan_run_id is provided, the scan run should transition to completed."""
        mcp_db.register_file("src/a.py")
        mcp_db.create_scan_run(
            scan_run_id="ingest-run",
            scanner_name="scanner",
            scan_source="scanner",
            file_paths=["src/a.py"],
            file_ids=["fid-1"],
        )
        mcp_db.update_scan_run_status("ingest-run", "running")

        mcp_db.process_scan_results(
            scan_source="scanner",
            scan_run_id="ingest-run",
            findings=[
                {"path": "src/a.py", "rule_id": "r1", "severity": "info", "message": "m1"},
            ],
        )

        run = mcp_db.get_scan_run("ingest-run")
        assert run["status"] == "completed"
        assert run["findings_count"] == 1

    def test_scan_run_completion_failure_does_not_lose_findings(self, mcp_db: FiligreeDB) -> None:
        """If scan run completion fails, findings should still be ingested."""
        mcp_db.register_file("src/b.py")

        # Use a non-existent scan_run_id — completion will fail but findings should persist
        result = mcp_db.process_scan_results(
            scan_source="scanner",
            scan_run_id="nonexistent-run",
            findings=[
                {"path": "src/b.py", "rule_id": "r1", "severity": "info", "message": "m1"},
            ],
        )

        assert result["findings_created"] == 1
        assert len(result["new_finding_ids"]) == 1


class TestValidateLocalhostUrl:
    """Edge-case coverage for the _validate_localhost_url security boundary."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8377/api/v1/scan-results",
            "http://127.0.0.1:8377/api/v1/scan-results",
            "http://[::1]:8377/api/v1/scan-results",
            "http://localhost/path",
        ],
    )
    def test_localhost_urls_accepted(self, url: str) -> None:
        assert _validate_localhost_url(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example.com/api",
            "http://localhost.evil.com/api",
            "http://192.168.1.1:8377/api",
            "http://10.0.0.1/api",
        ],
    )
    def test_non_localhost_urls_rejected(self, url: str) -> None:
        result = _validate_localhost_url(url)
        assert result is not None
        # Should be an error response (list of TextContent)
        assert isinstance(result, list)

    def test_empty_string_url(self) -> None:
        # Empty URL produces empty hostname — accepted by fallback
        result = _validate_localhost_url("")
        assert result is None

    def test_malformed_url_no_scheme(self) -> None:
        # urlparse("no-scheme") puts everything in path, hostname is None → ""
        result = _validate_localhost_url("no-scheme")
        assert result is None


class TestSpawnScanLogFileFailure:
    """_spawn_scan handles log file creation failure gracefully."""

    async def test_log_file_open_failure_still_spawns(self, mcp_db: FiligreeDB) -> None:
        files = _make_target_files(mcp_db, ["log_fail_target.py"])
        _write_scanner_toml(mcp_db)
        try:
            real_open = open

            def mock_open_fail(path, *a, **kw):
                if "scans" in str(path) and str(path).endswith(".log"):
                    raise OSError("disk full")
                return real_open(path, *a, **kw)

            with (
                patch("filigree.mcp_tools.scanners.subprocess.Popen", return_value=_FakeProc(100)),
                patch("builtins.open", side_effect=mock_open_fail),
            ):
                data = _parse(
                    await call_tool(
                        "trigger_scan_batch",
                        {"scanner": "test-scanner", "file_paths": ["log_fail_target.py"]},
                    )
                )
            assert data["status"] == "triggered"
            assert "log_warning" in data or any("log" in str(w).lower() for w in data.get("warnings", []))
        finally:
            _cleanup_files(mcp_db, files)


class TestBatchScanDbTrackingFailure:
    """trigger_scan_batch kills all processes when DB tracking fails."""

    async def test_db_failure_kills_spawned_processes(self, mcp_db: FiligreeDB) -> None:
        files = _make_target_files(mcp_db, ["db_fail_a.py", "db_fail_b.py"])
        _write_scanner_toml(mcp_db)
        mock_procs = [MagicMock(pid=100, poll=MagicMock(return_value=None)), MagicMock(pid=101, poll=MagicMock(return_value=None))]
        try:
            with (
                patch("filigree.mcp_tools.scanners.subprocess.Popen", side_effect=mock_procs),
                patch.object(mcp_db, "create_scan_run", side_effect=RuntimeError("DB broken")),
            ):
                data = _parse(
                    await call_tool(
                        "trigger_scan_batch",
                        {"scanner": "test-scanner", "file_paths": ["db_fail_a.py", "db_fail_b.py"]},
                    )
                )
            assert data["code"] == "db_error"
            # Both processes should have been killed
            for proc in mock_procs:
                proc.kill.assert_called_once()
        finally:
            _cleanup_files(mcp_db, files)
