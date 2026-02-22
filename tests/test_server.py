from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from filigree.server import (
    ServerConfig,
    read_server_config,
    register_project,
    unregister_project,
    write_server_config,
)


class TestServerConfig:
    def test_default_config(self) -> None:
        config = ServerConfig()
        assert config.port == 8377
        assert config.projects == {}

    def test_write_and_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        config = ServerConfig(port=9000)
        write_server_config(config)
        loaded = read_server_config()
        assert loaded.port == 9000

    def test_read_missing_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        config = read_server_config()
        assert config.port == 8377

    def test_roundtrip_with_special_chars_in_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Paths with quotes, spaces, and unicode survive serialization."""
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        weird_path = '/home/alice/my "quoted" project/.filigree'
        config = ServerConfig(projects={weird_path: {"prefix": 'weird"prefix'}})
        write_server_config(config)
        loaded = read_server_config()
        assert weird_path in loaded.projects
        assert loaded.projects[weird_path]["prefix"] == 'weird"prefix'


class TestProjectRegistration:
    def test_register_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        filigree_dir = tmp_path / "myproject" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text('{"prefix": "myproject"}')

        register_project(filigree_dir)
        config = read_server_config()
        assert str(filigree_dir.resolve()) in config.projects

    def test_unregister_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        filigree_dir = tmp_path / "myproject" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text('{"prefix": "myproject"}')

        register_project(filigree_dir)
        unregister_project(filigree_dir)
        config = read_server_config()
        assert str(filigree_dir.resolve()) not in config.projects


class TestVersionEnforcement:
    def test_register_rejects_incompatible_schema(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Projects with newer schema versions are rejected."""
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        filigree_dir = tmp_path / "future-project" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text(
            json.dumps(
                {
                    "prefix": "future",
                    "version": 999,
                }
            )
        )

        with pytest.raises(ValueError, match="schema version"):
            register_project(filigree_dir)


class TestDaemonLifecycle:
    def test_start_writes_pid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        spawned: list = []

        def mock_popen(cmd, **kwargs):  # type: ignore[no-untyped-def]
            mock = MagicMock()
            mock.pid = 54321
            mock.poll.return_value = None
            spawned.append(cmd)
            return mock

        monkeypatch.setattr("filigree.server.subprocess.Popen", mock_popen)
        from filigree.server import start_daemon

        result = start_daemon()
        assert result.success
        pid_data = json.loads((config_dir / "server.pid").read_text())
        assert pid_data["pid"] == 54321
        assert pid_data["cmd"] == "filigree"

    def test_stop_kills_process(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        killed: list[int] = []
        monkeypatch.setattr("os.kill", lambda pid, sig: killed.append(pid))

        # Mock is_pid_alive: first call returns True (so stop proceeds to kill),
        # subsequent calls return False (process exited after SIGTERM)
        alive_calls: list[int] = []

        def mock_is_pid_alive(pid: int) -> bool:
            alive_calls.append(pid)
            return len(alive_calls) == 1  # True first time only

        monkeypatch.setattr("filigree.server.is_pid_alive", mock_is_pid_alive)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)

        from filigree.server import stop_daemon

        result = stop_daemon()
        assert result.success
        assert 54321 in killed

    def test_start_daemon_passes_server_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """start_daemon() must include --server-mode in the spawned command."""
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        spawned: list[list[str]] = []

        def mock_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            mock = MagicMock()
            mock.pid = 99999
            mock.poll.return_value = None
            spawned.append(cmd)
            return mock

        monkeypatch.setattr("filigree.server.subprocess.Popen", mock_popen)
        from filigree.server import start_daemon

        result = start_daemon()
        assert result.success
        assert len(spawned) == 1
        assert "--server-mode" in spawned[0]

    def test_start_daemon_persists_port_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        def mock_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            mock = MagicMock()
            mock.pid = 88888
            mock.poll.return_value = None
            return mock

        monkeypatch.setattr("filigree.server.subprocess.Popen", mock_popen)
        from filigree.server import start_daemon

        result = start_daemon(port=9911)
        assert result.success
        cfg = read_server_config()
        assert cfg.port == 9911

    def test_status_reports_not_running(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        from filigree.server import daemon_status

        status = daemon_status()
        assert not status.running
