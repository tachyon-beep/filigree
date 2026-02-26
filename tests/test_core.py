"""Tests for filigree.core — utility functions, write_atomic, get_mode."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from filigree.core import find_filigree_command, get_mode, write_atomic


class TestGetMode:
    def test_default_mode_is_ethereal(self, tmp_path: Path) -> None:
        """Projects without a mode field default to ethereal."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == "ethereal"

    def test_explicit_ethereal(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "ethereal"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == "ethereal"

    def test_explicit_server(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "server"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == "server"

    def test_missing_config_defaults_to_ethereal(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        assert get_mode(filigree_dir) == "ethereal"

    def test_unknown_mode_falls_back_to_ethereal(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Unknown mode values fall back to ethereal with a warning."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "bogus"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        with caplog.at_level(logging.WARNING, logger="filigree.core"):
            result = get_mode(filigree_dir)
        assert result == "ethereal"
        assert "bogus" in caplog.text


class TestFindFiligreeCommand:
    def test_returns_list(self) -> None:
        """Command is always a list of strings."""
        result = find_filigree_command()
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)

    def test_at_least_one_element(self) -> None:
        result = find_filigree_command()
        assert len(result) >= 1


class TestWriteAtomic:
    def test_writes_content(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        write_atomic(target, "hello")
        assert target.read_text() == "hello"

    def test_no_tmp_file_left(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        write_atomic(target, "hello")
        assert not (tmp_path / "test.txt.tmp").exists()

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        """Overwriting an existing file works correctly."""
        target = tmp_path / "test.txt"
        target.write_text("original")
        write_atomic(target, "updated")
        assert target.read_text() == "updated"

    def test_error_cleanup_removes_tmp_and_preserves_original(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bug filigree-07485f: on os.replace failure, temp file must be removed and original preserved."""
        target = tmp_path / "test.txt"
        target.write_text("precious data")
        tmp_file = target.with_suffix(".txt.tmp")

        def failing_replace(src: object, dst: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", failing_replace)

        with pytest.raises(OSError, match="disk full"):
            write_atomic(target, "new content that should not land")

        assert target.read_text() == "precious data", "Original file must be untouched"
        assert not tmp_file.exists(), "Temp file must be cleaned up on failure"
