"""Regression tests for MCP ``restart_dashboard`` tool.

Covers filigree-2298877675: restart_dashboard must not report ``restarted``
when the old dashboard process never exits. The pre-fix implementation set
``stopped = True`` unconditionally after the SIGTERM + 2-second grace wait,
so a wedged dashboard produced a spurious success that ``ensure_dashboard_running``
then "resolved" by reusing the exact same still-alive process.

``_handle_restart_dashboard`` does its imports inside the function body, so
patches must target the source modules (``filigree.ephemeral``,
``filigree.core``, ``filigree.hooks``), not ``filigree.mcp_tools.meta``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from filigree.core import FiligreeDB
from filigree.mcp_tools.meta import _handle_restart_dashboard
from filigree.types.api import ErrorCode
from tests.mcp._helpers import _parse


@pytest.fixture
def mcp_root(tmp_path: Path, mcp_db: FiligreeDB) -> Path:
    """Reuse the MCP fixture and expose the filigree dir for find_filigree_root."""
    import filigree.mcp_server as mcp_mod

    return mcp_mod._filigree_dir  # type: ignore[return-value]


class TestRestartDashboardStopValidation:
    async def test_unresponsive_old_pid_aborts_with_error(self, mcp_root: Path) -> None:
        """filigree-2298877675: if the old dashboard ignores SIGTERM+SIGKILL, return an error
        — never claim ``restarted`` while the same process is still alive."""
        pid_file = mcp_root / "ephemeral.pid"
        pid_file.write_text('{"pid": 99999, "cmd": "filigree dashboard", "port": 8501}')

        # Process stays alive throughout every kill attempt.
        with (
            patch("filigree.core.find_filigree_root", return_value=mcp_root),
            patch("filigree.ephemeral.read_pid_file", return_value={"pid": 99999}),
            patch("filigree.ephemeral.verify_pid_ownership", return_value=True),
            patch("filigree.ephemeral.is_pid_alive", return_value=True),
            patch("os.kill"),
            patch("time.sleep"),
            patch("filigree.hooks.ensure_dashboard_running") as mock_ensure,
        ):
            result = await _handle_restart_dashboard({})

        data = _parse(result)
        assert data.get("code") == ErrorCode.STOP_FAILED, data
        assert "99999" in data.get("error", "")
        assert data.get("status") != "restarted", "must not claim success while old PID is alive"
        # Must not proceed to respawn when we couldn't stop the old one.
        mock_ensure.assert_not_called()

    async def test_sigterm_graceful_shutdown_restarts(self, mcp_root: Path) -> None:
        """Happy path: SIGTERM takes effect → old PID dies → restart succeeds."""
        pid_file = mcp_root / "ephemeral.pid"
        pid_file.write_text('{"pid": 99999, "cmd": "filigree dashboard", "port": 8501}')

        state = {"alive": True}

        def fake_is_pid_alive(_pid: int) -> bool:
            return state["alive"]

        import signal as _signal

        def fake_kill(_pid: int, sig: int) -> None:
            # SIGTERM quickly takes effect — process terminates during grace loop.
            if sig == _signal.SIGTERM:
                state["alive"] = False

        with (
            patch("filigree.core.find_filigree_root", return_value=mcp_root),
            patch("filigree.ephemeral.read_pid_file", return_value={"pid": 99999}),
            patch("filigree.ephemeral.verify_pid_ownership", return_value=True),
            patch("filigree.ephemeral.is_pid_alive", side_effect=fake_is_pid_alive),
            patch("os.kill", side_effect=fake_kill),
            patch("time.sleep"),
            patch(
                "filigree.hooks.ensure_dashboard_running",
                return_value="Filigree dashboard starting on http://localhost:8501 (initializing)",
            ),
        ):
            result = await _handle_restart_dashboard({})

        data = _parse(result)
        assert data.get("status") == "restarted", data

    async def test_sigkill_escalation_when_sigterm_ignored(self, mcp_root: Path) -> None:
        """SIGTERM alone does nothing, but SIGKILL finishes the job → report restarted."""
        pid_file = mcp_root / "ephemeral.pid"
        pid_file.write_text('{"pid": 99999, "cmd": "filigree dashboard", "port": 8501}')

        state = {"alive": True}

        def fake_is_pid_alive(_pid: int) -> bool:
            return state["alive"]

        signals_sent: list[int] = []

        import signal as _signal

        def fake_kill(_pid: int, sig: int) -> None:
            signals_sent.append(sig)
            # Only SIGKILL actually kills the stubborn process.
            if sig == _signal.SIGKILL:
                state["alive"] = False

        with (
            patch("filigree.core.find_filigree_root", return_value=mcp_root),
            patch("filigree.ephemeral.read_pid_file", return_value={"pid": 99999}),
            patch("filigree.ephemeral.verify_pid_ownership", return_value=True),
            patch("filigree.ephemeral.is_pid_alive", side_effect=fake_is_pid_alive),
            patch("os.kill", side_effect=fake_kill),
            patch("time.sleep"),
            patch(
                "filigree.hooks.ensure_dashboard_running",
                return_value="Started Filigree dashboard on http://localhost:8501",
            ),
        ):
            result = await _handle_restart_dashboard({})

        assert _signal.SIGTERM in signals_sent
        assert _signal.SIGKILL in signals_sent
        data = _parse(result)
        assert data.get("status") == "restarted", data

    async def test_no_existing_dashboard_reports_started(self, mcp_root: Path) -> None:
        """No PID file on disk → status is ``started`` (not ``restarted``)."""
        with (
            patch("filigree.core.find_filigree_root", return_value=mcp_root),
            patch("filigree.ephemeral.read_pid_file", return_value=None),
            patch(
                "filigree.hooks.ensure_dashboard_running",
                return_value="Started Filigree dashboard on http://localhost:8501",
            ),
        ):
            result = await _handle_restart_dashboard({})

        data = _parse(result)
        assert data.get("status") == "started", data
