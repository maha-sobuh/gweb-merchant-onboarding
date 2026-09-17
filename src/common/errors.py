"""Custom exceptions and mapping to the public JSON error shape.

Public bodies NEVER include exception type names, args, or stack traces.
Those belong in structured CloudWatch logs only (see logging_config).
"""

from __future__ import annotations

import os
import time
from typing import Any


class AppError(Exception):
    """Base error that is safe to convert into an HTTP response."""

    status_code = 500
    code = "INTERNAL_ERROR"
    message = "An unexpected error occurred"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        self.message = message or self.message
        if code:
            self.code = code
        super().__init__(self.message)

    def to_body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


class ValidationAppError(AppError):
    status_code = 400
    code = "VALIDATION_ERROR"
    message = "The request is invalid"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "The requested resource was not found"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "The resource already exists"


class HandlerTimeoutError(AppError):
    """Raised by DeadlineGuard when the internal budget is exhausted."""

    status_code = 500
    code = "INTERNAL_TIMEOUT"
    message = "The request exceeded the internal processing deadline"


class DeadlineGuard:
    """Fail-fast budget that is SHORTER than the Lambda timeout.

    Why this exists (reuse in later phases):
    - Lambda's configured Timeout is a hard freeze. If we wait until the
      runtime is killed, we cannot return a structured 500 or flush logs.
    - AI calls, S3 handshakes, and retries in later phases must check
      remaining() / check() before starting another remote call so we
      stop while we still have time to respond.
    - INTERNAL_TIMEOUT_SECONDS is injected by SAM so the guard tracks
      the function timeout without hardcoding it.

    Usage:
        with DeadlineGuard() as deadline:
            deadline.check()
            # do work
            if deadline.remaining() < 2:
                raise HandlerTimeoutError()
    """

    def __init__(self, seconds: float | None = None) -> None:
        raw = os.environ.get("INTERNAL_TIMEOUT_SECONDS", "25")
        self.limit_seconds = float(seconds if seconds is not None else raw)
        self._started = time.monotonic()

    def remaining(self) -> float:
        return self.limit_seconds - (time.monotonic() - self._started)

    def check(self) -> None:
        if self.remaining() <= 0:
            raise HandlerTimeoutError()

    def __enter__(self) -> DeadlineGuard:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            self.check()
        return False


def error_to_response_parts(exc: BaseException) -> tuple[int, dict[str, Any]]:
    """Map any exception to (status_code, body). Unknown errors become 500."""
    if isinstance(exc, AppError):
        return exc.status_code, exc.to_body()
    return 500, AppError().to_body()
