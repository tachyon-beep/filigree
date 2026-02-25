"""CLI tests for `filigree server` subcommands.

Covers: filigree-577d66 (test gap — server CLI wiring)
All server functions are mocked; these test CLI arg parsing, exit codes, and output.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from filigree.cli import cli
from filigree.server import DaemonResult, DaemonStatus


class TestServerStart:
    def test_start_success(self, cli_runner: CliRunner) -> None:
        with patch(
            "filigree.server.start_daemon",
            return_value=DaemonResult(True, "Started filigree daemon (pid 1234) on port 8377"),
        ):
            result = cli_runner.invoke(cli, ["server", "start"])
        assert result.exit_code == 0
        assert "Started" in result.output
        assert "1234" in result.output

    def test_start_with_port(self, cli_runner: CliRunner) -> None:
        with patch(
            "filigree.server.start_daemon",
            return_value=DaemonResult(True, "Started filigree daemon (pid 5678) on port 9999"),
        ) as mock_start:
            result = cli_runner.invoke(cli, ["server", "start", "--port", "9999"])
        assert result.exit_code == 0
        mock_start.assert_called_once_with(port=9999)

    def test_start_failure(self, cli_runner: CliRunner) -> None:
        with patch(
            "filigree.server.start_daemon",
            return_value=DaemonResult(False, "Daemon exited immediately (code 1): bind error"),
        ):
            result = cli_runner.invoke(cli, ["server", "start"])
        assert result.exit_code == 1
        assert "bind error" in result.output


class TestServerStop:
    def test_stop_success(self, cli_runner: CliRunner) -> None:
        with patch(
            "filigree.server.stop_daemon",
            return_value=DaemonResult(True, "Stopped filigree daemon (pid 1234)"),
        ):
            result = cli_runner.invoke(cli, ["server", "stop"])
        assert result.exit_code == 0
        assert "Stopped" in result.output

    def test_stop_not_running(self, cli_runner: CliRunner) -> None:
        with patch(
            "filigree.server.stop_daemon",
            return_value=DaemonResult(False, "No PID file found — daemon may not be running"),
        ):
            result = cli_runner.invoke(cli, ["server", "stop"])
        assert result.exit_code == 1
        assert "not be running" in result.output


class TestServerStatus:
    def test_status_running(self, cli_runner: CliRunner) -> None:
        with patch(
            "filigree.server.daemon_status",
            return_value=DaemonStatus(running=True, pid=1234, port=8377, project_count=3),
        ):
            result = cli_runner.invoke(cli, ["server", "status"])
        assert result.exit_code == 0
        assert "running" in result.output
        assert "1234" in result.output
        assert "8377" in result.output
        assert "3" in result.output

    def test_status_not_running(self, cli_runner: CliRunner) -> None:
        with patch(
            "filigree.server.daemon_status",
            return_value=DaemonStatus(running=False),
        ):
            result = cli_runner.invoke(cli, ["server", "status"])
        assert result.exit_code == 0
        assert "not running" in result.output


class TestServerRegister:
    def test_register_success(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        with (
            patch("filigree.server.register_project") as mock_reg,
            patch(
                "filigree.cli_commands.server._reload_server_daemon_if_running",
                return_value=(True, "daemon_not_running"),
            ),
        ):
            result = cli_runner.invoke(cli, ["server", "register", str(tmp_path)])
        assert result.exit_code == 0
        assert "Registered" in result.output
        mock_reg.assert_called_once_with(filigree_dir)

    def test_register_no_filigree_dir(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(cli, ["server", "register", str(tmp_path)])
        assert result.exit_code == 1
        assert "No .filigree/" in result.output

    def test_register_with_daemon_reload(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        with (
            patch("filigree.server.register_project"),
            patch(
                "filigree.cli_commands.server._reload_server_daemon_if_running",
                return_value=(True, "daemon_reloaded"),
            ),
        ):
            result = cli_runner.invoke(cli, ["server", "register", str(tmp_path)])
        assert result.exit_code == 0
        assert "Reloaded running daemon" in result.output


class TestServerUnregister:
    def test_unregister_success(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        with (
            patch("filigree.server.unregister_project") as mock_unreg,
            patch(
                "filigree.cli_commands.server._reload_server_daemon_if_running",
                return_value=(True, "daemon_not_running"),
            ),
        ):
            result = cli_runner.invoke(cli, ["server", "unregister", str(tmp_path)])
        assert result.exit_code == 0
        assert "Unregistered" in result.output
        mock_unreg.assert_called_once()

    def test_unregister_error(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        with patch(
            "filigree.server.unregister_project",
            side_effect=Exception("not registered"),
        ):
            result = cli_runner.invoke(cli, ["server", "unregister", str(tmp_path)])
        assert result.exit_code == 1
        assert "not registered" in result.output

    def test_unregister_with_daemon_reload_failure(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        with (
            patch("filigree.server.unregister_project"),
            patch(
                "filigree.cli_commands.server._reload_server_daemon_if_running",
                return_value=(False, "daemon reload failed with HTTP 500"),
            ),
        ):
            result = cli_runner.invoke(cli, ["server", "unregister", str(tmp_path)])
        assert result.exit_code == 1
        assert "daemon reload failed" in result.output
