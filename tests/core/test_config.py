# tests/test_config_packs.py
"""Tests for config.json enabled_packs support."""

from __future__ import annotations

from pathlib import Path

from filigree.core import FiligreeDB, read_config, write_config


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
