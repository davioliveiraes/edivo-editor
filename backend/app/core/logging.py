"""Structured logging without exception details, connection strings or private paths."""

import json
import logging
import re
from datetime import UTC, datetime

_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+")
_PATH = re.compile(r"(?<!\w)/(?:[^\s/,;:]+/)*[^\s,;:]*")
_SECRET = re.compile(r"(?i)(password|token|secret|authorization)\s*[=:]\s*[^\s,;]+")
_SAFE_FIELDS = (
    "request_id",
    "method",
    "status_code",
    "duration_ms",
    "diagnostic",
    "signal",
    "job_id",
    "project_id",
    "stage",
    "attempts",
    "error_code",
)


def sanitize(message: str) -> str:
    return _PATH.sub("[path]", _SECRET.sub(r"\1=[redacted]", _URL.sub("[url]", message)))


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitize(record.getMessage()),
        }
        for key in _SAFE_FIELDS:
            if hasattr(record, key):
                value = getattr(record, key)
                payload[key] = sanitize(value) if isinstance(value, str) else value
        # Deliberately omit exc_info/stack_info; third-party traces may contain secrets.
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy", "alembic"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
