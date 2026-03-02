from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

from filigree.ephemeral import (
    _read_os_command_line,
    cleanup_legacy_tmp_files,
    cleanup_stale_pid,
    compute_port,
    find_available_port,
    is_pid_alive,
    read_pid_file,
    read_port_file,
    verify_pid_ownership,
    write_pid_file,
    write_port_file,
)


class TestComputePort:
    def test_deterministic_for_same_path(self) -> None:
        """Same path always produces same port."""
        p = Path("/home/john/myproject/.filigree")
        assert compute_port(p) == compute_port(p)

    def test_in_valid_range(self) -> None:
        """Port is between 8400 and 9399."""
        p = Path("/home/john/myproject/.filigree")
        port = compute_port(p)
        assert 8400 <= port <= 9399

    def test_different_paths_likely_different_ports(self) -> None:
        """Different paths are unlikely to produce the same port."""
        ports = {compute_port(Path(f"/project-{i}/.filigree")) for i in range(20)}
        # With 1000-slot range and 20 samples, collisions are possible but
        # getting fewer than 15 unique ports would be suspicious
        assert len(ports) >= 15


class TestFindAvailablePort:
    def test_returns_deterministic_port_when_free(self) -> None:
        """When the deterministic port is free, use it."""
        p = Path("/home/john/myproject/.filigree")
        expected = compute_port(p)
        port = find_available_port(p)
        assert port == expected

    def test_skips_occupied_port(self) -> None:
        """When deterministic port is occupied, tries next ones."""
        p = Path("/home/john/myproject/.filigree")
        base = compute_port(p)
        # Occupy the base port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", base))
        sock.listen(1)
        try:
            port = find_available_port(p)
            assert port != base
            assert port > base  # should try sequential ports next
        finally:
            sock.close()

    def test_tries_base_plus_retries_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loop tries 1 base + PORT_RETRIES sequential ports before OS fallback."""
        from filigree import ephemeral

        checked: list[int] = []

        def fake_is_port_free(port: int) -> bool:
            checked.append(port)
            return False  # all ports "occupied"

        monkeypatch.setattr(ephemeral, "_is_port_free", fake_is_port_free)
        port = find_available_port(Path("/some/project/.filigree"))

        # Should check base + PORT_RETRIES sequential candidates
        assert len(checked) == ephemeral.PORT_RETRIES + 1
        # OS fallback must return a valid port
        assert 1 <= port <= 65535


class TestPidLifecycle:
    def test_write_and_read_pid(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, 12345, cmd="filigree")
        info = read_pid_file(pid_file)
        assert info is not None
        assert info["pid"] == 12345
        assert info["cmd"] == "filigree"

    def test_read_missing_pid_returns_none(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        assert read_pid_file(pid_file) is None

    def test_read_corrupt_pid_returns_none(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        pid_file.write_text("not-json")
        assert read_pid_file(pid_file) is None

    def test_read_legacy_plain_pid(self, tmp_path: Path) -> None:
        """Backward compat: plain integer PID files still work."""
        pid_file = tmp_path / "ephemeral.pid"
        pid_file.write_text("12345")
        info = read_pid_file(pid_file)
        assert info is not None
        assert info["pid"] == 12345
        assert info["cmd"] == "unknown"

    def test_read_non_positive_pid_returns_none(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        pid_file.write_text("0")
        assert read_pid_file(pid_file) is None

    def test_read_corrupt_pid_logs_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Bug filigree-0c570e: corrupt PID file must log a warning, not silently return None."""
        import logging

        pid_file = tmp_path / "ephemeral.pid"
        pid_file.write_text("not-a-number-or-json!!!")
        with caplog.at_level(logging.WARNING):
            result = read_pid_file(pid_file)
        assert result is None
        assert any("corrupt" in r.message.lower() or "pid" in r.message.lower() for r in caplog.records), (
            "read_pid_file must log a warning when PID file exists but can't be parsed"
        )

    def test_is_pid_alive_for_self(self) -> None:
        assert is_pid_alive(os.getpid()) is True

    def test_is_pid_alive_for_dead(self) -> None:
        assert is_pid_alive(99999999) is False

    def test_is_pid_alive_non_positive_false(self) -> None:
        assert is_pid_alive(0) is False
        assert is_pid_alive(-1) is False

    def test_verify_pid_ownership_for_self(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="python")
        expected = Path(sys.executable).name
        assert verify_pid_ownership(pid_file, expected_cmd=expected) is True

    def test_verify_pid_ownership_wrong_cmd(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="filigree")
        assert verify_pid_ownership(pid_file, expected_cmd="definitely-not-real") is False

    def test_verify_pid_ownership_ignores_stale_pid_file_cmd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="filigree")
        monkeypatch.setattr("filigree.ephemeral._read_os_command_line", lambda _pid: ["python", "worker.py"])
        assert verify_pid_ownership(pid_file, expected_cmd="filigree") is False

    def test_verify_pid_ownership_accepts_python_module_invocation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="filigree")
        monkeypatch.setattr(
            "filigree.ephemeral._read_os_command_line",
            lambda _pid: [sys.executable, "-m", "filigree", "dashboard", "--server-mode"],
        )
        assert verify_pid_ownership(pid_file, expected_cmd="filigree") is True

    def test_verify_pid_ownership_rejects_unrelated_python_module(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="filigree")
        monkeypatch.setattr(
            "filigree.ephemeral._read_os_command_line",
            lambda _pid: [sys.executable, "-m", "othermodule", "serve"],
        )
        assert verify_pid_ownership(pid_file, expected_cmd="filigree") is False

    def test_verify_pid_ownership_falls_back_to_pid_file_cmd_when_os_lookup_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="filigree")
        monkeypatch.setattr("filigree.ephemeral._read_os_command_line", lambda _pid: None)
        assert verify_pid_ownership(pid_file, expected_cmd="filigree") is True

    def test_verify_pid_ownership_fallback_rejects_mismatched_pid_file_cmd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="not-filigree")
        monkeypatch.setattr("filigree.ephemeral._read_os_command_line", lambda _pid: None)
        assert verify_pid_ownership(pid_file, expected_cmd="filigree") is False

    def test_verify_pid_ownership_returns_false_for_legacy_unknown_cmd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Task filigree-a26e15: legacy plain-integer PID file sets cmd='unknown'.

        verify_pid_ownership should return False because 'unknown' is not a
        trustworthy process identity.
        """
        pid_file = tmp_path / "ephemeral.pid"
        # Simulate a legacy plain-integer PID file
        pid_file.write_text(str(os.getpid()))
        info = read_pid_file(pid_file)
        assert info is not None
        assert info["cmd"] == "unknown"

        # OS command line lookup is unavailable
        monkeypatch.setattr("filigree.ephemeral._read_os_command_line", lambda _pid: None)

        # Should return False because cmd="unknown" is not trustworthy
        assert verify_pid_ownership(pid_file, expected_cmd="filigree") is False

    def test_cleanup_stale_pid_removes_dead(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, 99999999, cmd="filigree")
        cleanup_stale_pid(pid_file)
        assert not pid_file.exists()

    def test_cleanup_stale_pid_keeps_alive(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="python")
        cleanup_stale_pid(pid_file)
        assert pid_file.exists()


class TestPortFile:
    def test_write_and_read_port(self, tmp_path: Path) -> None:
        port_file = tmp_path / "ephemeral.port"
        write_port_file(port_file, 9173)
        assert read_port_file(port_file) == 9173

    def test_read_missing_port_returns_none(self, tmp_path: Path) -> None:
        port_file = tmp_path / "ephemeral.port"
        assert read_port_file(port_file) is None

    def test_read_corrupt_port_returns_none(self, tmp_path: Path) -> None:
        port_file = tmp_path / "ephemeral.port"
        port_file.write_text("not-a-number\ngarbage")
        assert read_port_file(port_file) is None


class TestReadOsCommandLine:
    """Task filigree-e43f60: tests for _read_os_command_line /proc and ps fallback."""

    @staticmethod
    def _make_path_factory(tmp_path: Path) -> object:
        """Return a callable that redirects Path("/proc") to tmp_path/proc."""
        _orig_path = Path

        def _factory(*args: object, **kwargs: object) -> Path:
            if args == ("/proc",):
                return _orig_path(str(tmp_path / "proc"))
            return _orig_path(*args, **kwargs)  # type: ignore[arg-type]

        return _factory

    @staticmethod
    def _make_noproc_path_factory(tmp_path: Path) -> object:
        """Return a callable that redirects Path("/proc") to a nonexistent dir."""
        _orig_path = Path

        def _factory(*args: object, **kwargs: object) -> Path:
            if args == ("/proc",):
                return _orig_path(str(tmp_path / "no-proc"))
            return _orig_path(*args, **kwargs)  # type: ignore[arg-type]

        return _factory

    def test_proc_cmdline_nul_separated_parsing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """NUL-separated tokens from /proc/{pid}/cmdline are split correctly."""
        fake_proc_dir = tmp_path / "proc" / "12345"
        fake_proc_dir.mkdir(parents=True)
        (fake_proc_dir / "cmdline").write_bytes(b"/usr/bin/filigree\x00dashboard\x00--port\x008400\x00")

        monkeypatch.setattr("filigree.ephemeral.Path", self._make_path_factory(tmp_path))

        result = _read_os_command_line(12345)
        assert result == ["/usr/bin/filigree", "dashboard", "--port", "8400"]

    def test_proc_cmdline_trailing_nuls_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty tokens from trailing NUL bytes are filtered out."""
        fake_proc_dir = tmp_path / "proc" / "99"
        fake_proc_dir.mkdir(parents=True)
        (fake_proc_dir / "cmdline").write_bytes(b"python\x00-m\x00filigree\x00\x00\x00")

        monkeypatch.setattr("filigree.ephemeral.Path", self._make_path_factory(tmp_path))

        result = _read_os_command_line(99)
        assert result == ["python", "-m", "filigree"]

    def test_ps_fallback_when_proc_unavailable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When /proc is not available, falls back to ps and uses shlex.split."""
        import subprocess as _subprocess

        monkeypatch.setattr("filigree.ephemeral.Path", self._make_noproc_path_factory(tmp_path))

        fake_result = _subprocess.CompletedProcess(
            args=["ps", "-p", "12345", "-o", "command="],
            returncode=0,
            stdout="/usr/bin/filigree dashboard --port 8400\n",
            stderr="",
        )
        monkeypatch.setattr("filigree.ephemeral.subprocess.run", lambda *a, **kw: fake_result)

        result = _read_os_command_line(12345)
        assert result == ["/usr/bin/filigree", "dashboard", "--port", "8400"]

    def test_ps_fallback_with_module_invocation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ps output with python -m invocation is parsed correctly via shlex.split."""
        import subprocess as _subprocess

        monkeypatch.setattr("filigree.ephemeral.Path", self._make_noproc_path_factory(tmp_path))

        fake_result = _subprocess.CompletedProcess(
            args=["ps", "-p", "12345", "-o", "command="],
            returncode=0,
            stdout="/usr/bin/python -m filigree dashboard\n",
            stderr="",
        )
        monkeypatch.setattr("filigree.ephemeral.subprocess.run", lambda *a, **kw: fake_result)

        result = _read_os_command_line(12345)
        assert result == ["/usr/bin/python", "-m", "filigree", "dashboard"]

    def test_ps_fallback_empty_output_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ps returning empty stdout results in None."""
        import subprocess as _subprocess

        monkeypatch.setattr("filigree.ephemeral.Path", self._make_noproc_path_factory(tmp_path))

        fake_result = _subprocess.CompletedProcess(
            args=["ps", "-p", "12345", "-o", "command="],
            returncode=0,
            stdout="",
            stderr="",
        )
        monkeypatch.setattr("filigree.ephemeral.subprocess.run", lambda *a, **kw: fake_result)

        result = _read_os_command_line(12345)
        assert result is None

    def test_both_proc_and_ps_fail_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both /proc and ps are unavailable, returns None."""
        monkeypatch.setattr("filigree.ephemeral.Path", self._make_noproc_path_factory(tmp_path))

        def _raise_oserror(*args: object, **kwargs: object) -> None:
            raise OSError("ps not found")

        monkeypatch.setattr("filigree.ephemeral.subprocess.run", _raise_oserror)

        result = _read_os_command_line(12345)
        assert result is None


class TestLegacyCleanup:
    def test_cleanup_legacy_tmp_files_ignores_permission_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def fake_unlink(self: Path, *, missing_ok: bool = False) -> None:
            calls.append(self.name)
            if self.name == "filigree-dashboard.pid":
                raise PermissionError("denied")

        monkeypatch.setattr(Path, "unlink", fake_unlink)

        cleanup_legacy_tmp_files()

        assert calls == [
            "filigree-dashboard.pid",
            "filigree-dashboard.lock",
            "filigree-dashboard.log",
        ]
