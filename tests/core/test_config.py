# tests/core/test_config.py
"""Tests for filigree.core — config, utility functions, write_atomic, get_mode."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from filigree.core import (
    FILIGREE_DIR_NAME,
    FiligreeDB,
    find_filigree_command,
    find_filigree_root,
    get_mode,
    read_config,
    write_atomic,
    write_config,
)


class TestConfigEnabledPacks:
    """Verify enabled_packs default and passthrough."""

    def test_read_config_missing_enabled_packs_not_injected(self, tmp_path: Path) -> None:
        """Config without enabled_packs should not auto-inject — FiligreeDB constructor applies default."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        write_config(filigree_dir, {"prefix": "test", "version": 1})

        config = read_config(filigree_dir)
        assert "enabled_packs" not in config
        # FiligreeDB constructor applies the default when enabled_packs=None
        db = FiligreeDB(filigree_dir / "filigree.db", prefix="test", enabled_packs=config.get("enabled_packs"))
        db.initialize()
        assert db.enabled_packs == ["core", "planning", "release"]
        db.close()

    def test_read_config_preserves_explicit_enabled_packs(self, tmp_path: Path) -> None:
        """Config with explicit enabled_packs should be preserved as-is."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        write_config(filigree_dir, {"prefix": "test", "version": 1, "enabled_packs": ["core"]})

        config = read_config(filigree_dir)
        assert config["enabled_packs"] == ["core"]

    def test_read_config_empty_enabled_packs_preserved(self, tmp_path: Path) -> None:
        """Config with empty enabled_packs (feature flag off) should stay empty."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        write_config(filigree_dir, {"prefix": "test", "version": 1, "enabled_packs": []})

        config = read_config(filigree_dir)
        assert config["enabled_packs"] == []

    def test_read_config_no_file_gets_defaults(self, tmp_path: Path) -> None:
        """Missing config.json should return defaults including enabled_packs."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        # No config file written

        config = read_config(filigree_dir)
        assert config["prefix"] == "filigree"
        assert config["enabled_packs"] == ["core", "planning", "release"]

    def test_from_project_passes_enabled_packs(self, tmp_path: Path) -> None:
        """FiligreeDB.from_project() should read enabled_packs from config."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        write_config(
            filigree_dir,
            {
                "prefix": "proj",
                "version": 1,
                "enabled_packs": ["core", "planning", "risk"],
            },
        )

        # Initialize the database so from_project can open it
        init_db = FiligreeDB(filigree_dir / "filigree.db", prefix="proj")
        init_db.initialize()
        init_db.close()

        db = FiligreeDB.from_project(tmp_path)
        assert db.enabled_packs == ["core", "planning", "risk"]
        db.close()

    def test_from_project_default_enabled_packs(self, tmp_path: Path) -> None:
        """FiligreeDB.from_project() with no enabled_packs in config gets default."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        write_config(filigree_dir, {"prefix": "proj", "version": 1})

        init_db = FiligreeDB(filigree_dir / "filigree.db", prefix="proj")
        init_db.initialize()
        init_db.close()

        db = FiligreeDB.from_project(tmp_path)
        assert db.enabled_packs == ["core", "planning", "release"]
        db.close()


# ===========================================================================
# Utility functions (from test_core.py)
# ===========================================================================


class TestGetMode:
    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            ({"prefix": "test", "version": 1}, "ethereal"),
            ({"prefix": "test", "version": 1, "mode": "ethereal"}, "ethereal"),
            ({"prefix": "test", "version": 1, "mode": "server"}, "server"),
        ],
        ids=["no-mode-field", "explicit-ethereal", "explicit-server"],
    )
    def test_mode_from_config(self, tmp_path: Path, config: dict, expected: str) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == expected

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


# ===========================================================================
# Config and root discovery (from test_core_gaps.py)
# ===========================================================================


class TestFindFiligreeRoot:
    def test_finds_in_current_dir(self, tmp_path: Path) -> None:
        (tmp_path / FILIGREE_DIR_NAME).mkdir()
        result = find_filigree_root(tmp_path)
        assert result == tmp_path / FILIGREE_DIR_NAME

    def test_finds_in_parent_dir(self, tmp_path: Path) -> None:
        (tmp_path / FILIGREE_DIR_NAME).mkdir()
        child = tmp_path / "subdir"
        child.mkdir()
        result = find_filigree_root(child)
        assert result == tmp_path / FILIGREE_DIR_NAME

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            find_filigree_root(tmp_path)


class TestConfig:
    def test_read_write_roundtrip(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / FILIGREE_DIR_NAME
        filigree_dir.mkdir()
        write_config(filigree_dir, {"prefix": "myproj", "version": 1})
        config = read_config(filigree_dir)
        assert config["prefix"] == "myproj"
        assert config["version"] == 1

    def test_read_missing_returns_defaults(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / FILIGREE_DIR_NAME
        filigree_dir.mkdir()
        config = read_config(filigree_dir)
        assert config["prefix"] == "filigree"
