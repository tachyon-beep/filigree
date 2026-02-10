"""Tests for structured logging."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from filigree.logging import setup_logging


class TestSetupLogging:
    def test_creates_log_file(self, tmp_path: Path) -> None:
        logger = setup_logging(tmp_path)
        logger.info("test_message", extra={"tool": "test", "args_data": {"key": "val"}})
        # Flush handlers
        for handler in logger.handlers:
            handler.flush()
        log_path = tmp_path / "filigree.log"
        assert log_path.exists()
        content = log_path.read_text()
        record = json.loads(content.strip())
        assert record["msg"] == "test_message"
        assert record["tool"] == "test"
        assert record["args"]["key"] == "val"

    def test_json_format(self, tmp_path: Path) -> None:
        logger = setup_logging(tmp_path)
        logger.info("formatted", extra={"tool": "get_issue", "duration_ms": 42.5})
        for handler in logger.handlers:
            handler.flush()
        log_path = tmp_path / "filigree.log"
        record = json.loads(log_path.read_text().strip().split("\n")[-1])
        assert record["duration_ms"] == 42.5

    def test_idempotent_setup(self, tmp_path: Path) -> None:
        logger1 = setup_logging(tmp_path)
        logger2 = setup_logging(tmp_path)
        assert logger1 is logger2
        assert len(logger1.handlers) == 1

    def teardown_method(self) -> None:
        """Clean up the filigree logger handlers between tests."""
        logger = logging.getLogger("filigree")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
