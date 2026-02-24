"""Structured JSON logging for filigree.

Writes JSONL to .filigree/filigree.log with rotation (5MB, 3 backups).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOG_FILENAME = "filigree.log"
_setup_lock = threading.Lock()
_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_BACKUP_COUNT = 3


class _JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if hasattr(record, "tool"):
            entry["tool"] = record.tool
        if hasattr(record, "args_data"):
            entry["args"] = record.args_data
        if hasattr(record, "duration_ms"):
            entry["duration_ms"] = record.duration_ms
        if hasattr(record, "error"):
            entry["error"] = record.error
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, default=str)


def setup_logging(filigree_dir: Path) -> logging.Logger:
    """Set up structured JSON logging to .filigree/filigree.log.

    Returns a logger that writes JSONL with rotation.
    """
    logger = logging.getLogger("filigree")
    log_path = filigree_dir / _LOG_FILENAME
    target_filename = os.path.abspath(str(log_path))

    with _setup_lock:
        # Check existing RotatingFileHandlers on the global logger.
        for h in logger.handlers[:]:
            if not isinstance(h, RotatingFileHandler):
                continue
            if h.baseFilename == target_filename:
                return logger
            # Different path — remove stale handler to avoid leaks / duplicates.
            logger.removeHandler(h)
            h.close()

        handler = RotatingFileHandler(
            str(log_path),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
        )
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
