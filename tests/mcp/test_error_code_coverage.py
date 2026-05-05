"""Coverage for ErrorCode members that would otherwise be asserted by no test.

Stage 2a review finding #11: ``PERMISSION`` and ``NOT_INITIALIZED`` were
emitted by production code but had zero direct test assertions. A silent
re-route of either legacy code (e.g. ``permission_error → VALIDATION``)
would have broken no test. These tests exercise the emit paths end-to-end
so a re-route lights up red.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from filigree.core import FiligreeDB
from filigree.mcp_tools.meta import _handle_restart_dashboard
from filigree.mcp_tools.scanners import _handle_list_scanners, _handle_trigger_scan_batch
from filigree.types.api import ErrorCode
from tests.mcp._helpers import _parse


class TestNotInitializedCoverage:
    """When filigree_dir is None, MCP tools emit ErrorCode.NOT_INITIALIZED."""

    async def test_list_scanners_without_project(self, mcp_db: FiligreeDB, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_scanners emits NOT_INITIALIZED when no project is active."""
        import filigree.mcp_server as mcp_mod

        monkeypatch.setattr(mcp_mod, "_filigree_dir", None)
        data = _parse(await _handle_list_scanners({}))
        assert data["code"] == ErrorCode.NOT_INITIALIZED

    async def test_restart_dashboard_without_project(self) -> None:
        """restart_dashboard emits NOT_INITIALIZED when find_filigree_root fails."""
        with patch("filigree.core.find_filigree_root", side_effect=FileNotFoundError):
            data = _parse(await _handle_restart_dashboard({}))
        assert data["code"] == ErrorCode.NOT_INITIALIZED

    async def test_trigger_scan_batch_without_project(self, mcp_db: FiligreeDB, monkeypatch: pytest.MonkeyPatch) -> None:
        """trigger_scan_batch emits NOT_INITIALIZED when no project is active."""
        import filigree.mcp_server as mcp_mod

        monkeypatch.setattr(mcp_mod, "_filigree_dir", None)
        data = _parse(await _handle_trigger_scan_batch({"scanner": "s", "file_paths": ["x.py"]}))
        assert data["code"] == ErrorCode.NOT_INITIALIZED


class TestPermissionCoverage:
    """restart_dashboard emits ErrorCode.PERMISSION when os.kill is blocked."""

    async def test_restart_dashboard_permission_denied_on_sigterm(self, mcp_db: FiligreeDB, tmp_path: Path) -> None:
        """os.kill raising PermissionError (EPERM) surfaces as ErrorCode.PERMISSION.

        Exercises the outer ``except PermissionError`` at meta.py:675 — the
        initial SIGTERM is blocked because the PID belongs to another user.
        """
        import filigree.mcp_server as mcp_mod

        assert mcp_mod._filigree_dir is not None
        pid_file = mcp_mod._filigree_dir / "ephemeral.pid"
        pid_file.write_text('{"pid": 42, "cmd": "filigree dashboard", "port": 8501}')

        with (
            patch("filigree.core.find_filigree_root", return_value=mcp_mod._filigree_dir),
            patch("filigree.ephemeral.read_pid_file", return_value={"pid": 42}),
            patch("filigree.ephemeral.verify_pid_ownership", return_value=True),
            patch("filigree.ephemeral.is_pid_alive", return_value=True),
            patch("os.kill", side_effect=PermissionError("EPERM")),
            patch("time.sleep"),
            patch("filigree.hooks.ensure_dashboard_running"),
        ):
            data = _parse(await _handle_restart_dashboard({}))

        assert data["code"] == ErrorCode.PERMISSION
        assert "42" in data["error"]
