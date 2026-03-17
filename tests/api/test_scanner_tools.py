"""Tests for new scanner MCP tools — get_scan_status, preview_scan, trigger_scan_batch."""

from __future__ import annotations

import pytest

from filigree.core import FiligreeDB


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
