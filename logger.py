"""Structured logging configuration.

Provides a single `get_logger` function that returns a logger with
consistent formatting, context enrichment, and both file and console output.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any

from config import DATA_DIR

_LOG_DIR = DATA_DIR / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = str(_LOG_DIR / "app.log")

_STANDARD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
})


class ExtraFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        extra = {
            k: v for k, v in record.__dict__.items()
            if k not in _STANDARD_ATTRS and not k.startswith("_")
        }
        if extra:
            record.extra = extra
        return True


class JsonFormatter(logging.Formatter):
    """Formats log records as JSON lines for machine parsability."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            log_entry.update(extra)
        return json.dumps(log_entry, default=str)


_handlers_configured = False


def get_logger(name: str) -> logging.Logger:
    """Return a structured logger for the given *name*.

    Handlers are added once (module-level) so repeated calls for the same
    name return the same logger without duplicate handlers.
    """
    global _handlers_configured

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not _handlers_configured:
        extra_filter = ExtraFilter()

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(JsonFormatter())
        console_handler.addFilter(extra_filter)

        file_handler = logging.FileHandler(_LOG_FILE)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(extra_filter)

        logger.root.addHandler(console_handler)
        logger.root.addHandler(file_handler)
        _handlers_configured = True

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        if not root_logger.handlers:
            root_logger.addHandler(console_handler)
            root_logger.addHandler(file_handler)

    return logger
