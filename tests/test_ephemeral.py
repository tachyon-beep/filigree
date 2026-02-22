from __future__ import annotations

import os
import socket
from pathlib import Path

from filigree.ephemeral import (
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

    def test_is_pid_alive_for_self(self) -> None:
        assert is_pid_alive(os.getpid()) is True

    def test_is_pid_alive_for_dead(self) -> None:
        assert is_pid_alive(99999999) is False

    def test_verify_pid_ownership_for_self(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="python")
        assert verify_pid_ownership(pid_file, expected_cmd="python") is True

    def test_verify_pid_ownership_wrong_cmd(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid_file(pid_file, os.getpid(), cmd="filigree")
        assert verify_pid_ownership(pid_file, expected_cmd="nginx") is False

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
