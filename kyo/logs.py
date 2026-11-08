"""Structured logs, one JSON object per line. Request bodies are never logged."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime

from fastapi import Request

LOGGER = "kyo"
_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}
_CARD_LIKE = re.compile(r"\b(?:\d[ -]?){12,19}\b")

log = logging.getLogger(LOGGER)


def scrub(text: object) -> str:
    """Mask anything that looks like a card number, in case a shopper typed one into a request."""
    return _CARD_LIKE.sub("[number removed]", str(text))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD:
                entry[key] = scrub(value) if isinstance(value, str) else value
        if record.exc_info:
            entry["error"] = scrub(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


def configure_logging() -> None:
    """Send the application's logs to stderr as JSON. Safe to call more than once."""
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.setLevel(os.environ.get("KYO_LOG_LEVEL", "INFO").upper())
    log.propagate = False


async def log_requests(request: Request, call_next):
    """Record each request's method, path, status and duration. Query strings are left out."""
    started = time.perf_counter()
    response = await call_next(request)
    if not request.url.path.startswith("/static"):
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
    return response
