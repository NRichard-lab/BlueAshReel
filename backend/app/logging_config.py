from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|authorization|cookie|session|csrf|path|directory|folder|selection|filename|title|database_url)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*|(?:(?:password|token|secret)=)[^\s&]+")
_native_log_file: Path | None = None


def redact(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub(r"\1[REDACTED]", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload["fields"] = redact(fields)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging(level: str = "INFO", *, native_log_file: Path | None = None) -> None:
    global _native_log_file
    if native_log_file is not None:
        _native_log_file = native_log_file
    handler = (
        RotatingFileHandler(_native_log_file, maxBytes=1024 * 1024, backupCount=4, encoding="utf-8")
        if _native_log_file is not None else logging.StreamHandler()
    )
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for previous in root.handlers[:]:
        root.removeHandler(previous)
        previous.close()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # These loggers include raw URLs and query strings. The application middleware
    # emits a bounded request event using the matched route template instead.
    for noisy_logger in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
