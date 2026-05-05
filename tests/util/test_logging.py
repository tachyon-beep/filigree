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
            if isinstance(h, logging.handlers.RotatingFileHandler) and h.baseFilename == os.path.abspath(str(tmp_path / "filigree.log"))
        ]
        assert len(file_handlers) == 1, f"Expected 1 handler, got {len(file_handlers)}"

    def test_different_dir_replaces_handler(self, tmp_path: Path) -> None:
        """Calling setup_logging with a new dir must close the old handler."""
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        dir_b = tmp_path / "b"
        dir_b.mkdir()

        logger = setup_logging(dir_a)
        assert len(logger.handlers) == 1
        old_handler = logger.handlers[0]

        setup_logging(dir_b)
        # Must replace, not accumulate
        assert len(logger.handlers) == 1
        new_handler = logger.handlers[0]
        assert new_handler is not old_handler
        expected_path = os.path.abspath(str(dir_b / "filigree.log"))
        assert new_handler.baseFilename == expected_path  # type: ignore[attr-defined]
        # Old handler stream must be closed (FileHandler.close() sets stream to None)
        assert old_handler.stream is None  # type: ignore[attr-defined]

    def test_exc_info_includes_traceback_and_type(self, tmp_path: Path) -> None:
        """exc_info=True must capture traceback and exception class, not just str()."""
        logger = setup_logging(tmp_path)
        try:
            {}["missing-key"]  # type: ignore[index]
        except KeyError:
            logger.error("boom", exc_info=True)
        for handler in logger.handlers:
            handler.flush()
        log_path = tmp_path / "filigree.log"
        record = json.loads(log_path.read_text().strip().split("\n")[-1])
        # Exception type must be present so consumers can filter/classify.
        assert record.get("exception_type") == "KeyError", record
        # Traceback text must be present so consumers can diagnose.
        traceback_text = record.get("traceback", "")
        assert "Traceback" in traceback_text, record
        assert "KeyError" in traceback_text, record
        # Retain human-readable message.
        assert "missing-key" in record.get("exception", ""), record

    def test_stack_info_included(self, tmp_path: Path) -> None:
        """stack_info=True must include the stack trace text."""
        logger = setup_logging(tmp_path)
        logger.warning("context-needed", stack_info=True)
        for handler in logger.handlers:
            handler.flush()
        log_path = tmp_path / "filigree.log"
        record = json.loads(log_path.read_text().strip().split("\n")[-1])
        stack_text = record.get("stack", "")
        assert "Stack (most recent call last)" in stack_text, record

    def test_dedupes_multiple_matching_handlers(self, tmp_path: Path) -> None:
        """If the logger already has two handlers for the target path, collapse to one."""
        target = tmp_path / "filigree.log"
        logger = logging.getLogger("filigree")
        # Pre-populate with two matching rotating handlers (simulating a prior leak).
        h1 = logging.handlers.RotatingFileHandler(str(target))
        h2 = logging.handlers.RotatingFileHandler(str(target))
        logger.addHandler(h1)
        logger.addHandler(h2)
        assert len(logger.handlers) == 2

        setup_logging(tmp_path)

        expected = os.path.abspath(str(target))
        matching = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler) and h.baseFilename == expected]
        assert len(matching) == 1, f"Expected 1 matching handler, got {len(matching)}"
        # The surviving handler must still be open; removed duplicates must be closed.
        assert matching[0].stream is not None
        removed = [h for h in (h1, h2) if h is not matching[0]]
        for h in removed:
            assert h.stream is None, "Removed duplicate handler must be closed"

    def test_removes_stale_handler_following_matching(self, tmp_path: Path) -> None:
        """Stale handler positioned AFTER a matching one must still be removed."""
        matching_path = tmp_path / "filigree.log"
        stale_path = tmp_path / "other.log"
        logger = logging.getLogger("filigree")
        # Order matters: matching handler first, stale handler second.
        matching = logging.handlers.RotatingFileHandler(str(matching_path))
        stale = logging.handlers.RotatingFileHandler(str(stale_path))
        logger.addHandler(matching)
        logger.addHandler(stale)

        setup_logging(tmp_path)

        remaining = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert len(remaining) == 1, f"Expected 1 handler, got {len(remaining)}"
        assert remaining[0] is matching
        # Stale must be closed.
        assert stale.stream is None

    def test_configures_pre_existing_matching_handler(self, tmp_path: Path) -> None:
        """A pre-attached but unconfigured matching handler must end up configured."""
        target = tmp_path / "filigree.log"
        logger = logging.getLogger("filigree")
        # Pre-attach an unconfigured matching handler (no formatter, logger level NOTSET).
        pre = logging.handlers.RotatingFileHandler(str(target))
        logger.addHandler(pre)

        setup_logging(tmp_path)

        # Logger must be at INFO level so info events are not dropped.
        assert logger.level == logging.INFO
        # The surviving handler must have the JSON formatter applied.
        surviving = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert len(surviving) == 1
        formatter = surviving[0].formatter
        assert formatter is not None
        # End-to-end: an INFO emit must land as a single JSON line.
        logger.info("info_event", extra={"tool": "t"})
        for h in logger.handlers:
            h.flush()
        line = target.read_text().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["msg"] == "info_event"
        assert record["level"] == "INFO"

    def teardown_method(self) -> None:
        """Clean up the filigree logger handlers between tests."""
        logger = logging.getLogger("filigree")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
