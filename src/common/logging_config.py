"""Structured JSON logging for CloudWatch.

Rules:
- Always include correlation_id (API Gateway / Lambda request id).
- Include application_id when known.
- NEVER emit PII (names, emails, phones, tax IDs, DOB, addresses, ID numbers).
- Extra fields are redacted by key name before serialization.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

# Keys (case-insensitive, substring match) that must never appear in logs.
_PII_KEY_FRAGMENTS = (
    "email",
    "phone",
    "ssn",
    "tax_id",
    "taxid",
    "first_name",
    "last_name",
    "middle_name",
    "date_of_birth",
    "dob",
    "address",
    "government_id",
    "passport",
    "driver",
    "idempotency-key",
    "idempotency_key",
    "legal_name",
    "dba",
)

_REDACTED = "[REDACTED]"


def _is_pii_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in _PII_KEY_FRAGMENTS)


def redact(value: Any) -> Any:
    """Recursively strip PII-looking keys from dicts destined for logs."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_pii_key(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        correlation_id = getattr(record, "correlation_id", None)
        application_id = getattr(record, "application_id", None)
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if application_id:
            payload["application_id"] = application_id
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key
            not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "correlation_id",
                "application_id",
                "message",
                "taskName",
            }
        }
        if extras:
            payload["extra"] = redact(extras)
        if record.exc_info:
            # Exception type + message only — still run through a PII scrubber
            # on the text in case a library interpolated a field value.
            formatted = self.formatException(record.exc_info)
            payload["exception"] = _scrub_text(formatted)
        return json.dumps(payload, default=str)


_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_SSN_RE = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")
_PHONE_RE = re.compile(r"\b\+?\d[\d\-\s().]{8,}\d\b")


def _scrub_text(text: str) -> str:
    text = _EMAIL_RE.sub(_REDACTED, text)
    text = _SSN_RE.sub(_REDACTED, text)
    text = _PHONE_RE.sub(_REDACTED, text)
    return text


class _ContextFilter(logging.Filter):
    def __init__(self, correlation_id: str | None, application_id: str | None) -> None:
        super().__init__()
        self.correlation_id = correlation_id
        self.application_id = application_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "correlation_id", None):
            record.correlation_id = self.correlation_id
        if not getattr(record, "application_id", None):
            record.application_id = self.application_id
        return True


def configure_logging(
    correlation_id: str | None = None,
    application_id: str | None = None,
) -> logging.Logger:
    """Idempotent-safe: reconfigures the root logger for this invocation."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(level)
    logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_ContextFilter(correlation_id, application_id))
    logger.addHandler(handler)

    # Keep boto/urllib chatter out of INFO unless we opted into DEBUG.
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return logging.getLogger("merchant_onboarding")
