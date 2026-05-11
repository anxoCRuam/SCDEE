"""
Structured JSON log formatter for production logging.

Outputs log records as single-line JSON objects suitable for
log aggregation pipelines (ELK, CloudWatch, Datadog, etc.).

In development, settings/dev.py switches to the 'simple' text
formatter for human readability.

Example output:
    {"timestamp": "2025-01-15T10:30:00Z", "level": "ERROR",
     "logger": "apps.accounts.services", "message": "Login failed",
     "exc_type": "AuthenticationError", "exc_message": "Invalid credentials"}

References: RNF-11
"""

import json
import logging
import traceback
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON strings."""

    def format(self, record: logging.LogRecord) -> str:
        """Convert a LogRecord into a JSON string.

        Args:
            record: The log record to format.

        Returns:
            A single-line JSON string with structured log data.
        """
        log_entry: dict = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present.
        if record.exc_info and record.exc_info[1]:
            log_entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            log_entry["exc_message"] = str(record.exc_info[1])
            log_entry["traceback"] = traceback.format_exception(*record.exc_info)

        # Add extra fields passed via logger.info("msg", extra={...})
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "exc_info",
            "exc_text",
            "message",
            "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_entry[key] = value

        return json.dumps(log_entry, default=str, ensure_ascii=False)
