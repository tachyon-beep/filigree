"""Tests for structured logging."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
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

    def test_no_duplicate_handlers_via_symlink(self, tmp_path: Path) -> None:
        """setup_logging via symlinked dir twice must not add duplicate handlers."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        os.symlink(str(real_dir), str(link_dir))
        # Call twice via the symlink — baseFilename uses abspath (keeps symlink),
        # but dedup check used resolve() (follows symlink), causing mismatch.
        logger1 = setup_logging(link_dir)
        logger2 = setup_logging(link_dir)
        assert logger1 is logger2
        assert len(logger1.handlers) == 1

    def test_no_duplicate_handlers_under_concurrency(self, tmp_path: Path) -> None:
        """Concurrent setup_logging calls must not produce duplicate handlers."""
        import threading

        results: list[logging.Logger] = []
        barrier = threading.Barrier(4)

        def call_setup() -> None:
            barrier.wait()
            results.append(setup_logging(tmp_path))

        threads = [threading.Thread(target=call_setup) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 4
        # All should be the same logger
        assert all(r is results[0] for r in results)
        # Must have exactly 1 handler for this path
        logger = logging.getLogger("filigree")
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
            and h.baseFilename == os.path.abspath(str(tmp_path / "filigree.log"))
        ]
        assert len(file_handlers) == 1, f"Expected 1 handler, got {len(file_handlers)}"

    def teardown_method(self) -> None:
        """Clean up the filigree logger handlers between tests."""
        logger = logging.getLogger("filigree")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
