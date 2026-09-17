"""GET /applications/{id} — fetch application METADATA.

Stateless: read-only GetItem. Repeating the call with the same id is safe.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from common.errors import DeadlineGuard, NotFoundError, ValidationAppError
from common.logging_config import configure_logging
from common.responses import error_response, json_response
from models.application import Application
from repositories.application_repo import ApplicationRepository

logger = logging.getLogger("merchant_onboarding.handlers.get")

_UUID_CHARS = set("0123456789abcdefABCDEF-")


def handler(event: dict, context: Any, repo: ApplicationRepository | None = None) -> dict:
    correlation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    configure_logging(correlation_id)
    repo = repo or ApplicationRepository()

    try:
        with DeadlineGuard() as deadline:
            deadline.check()
            return _get(event, repo, correlation_id, deadline)
    except Exception as exc:  # noqa: BLE001 — mapped to a safe 4xx/5xx body
        logger.exception("get application failed")
        return error_response(exc)


def _get(
    event: dict,
    repo: ApplicationRepository,
    correlation_id: str,
    deadline: DeadlineGuard,
) -> dict:
    path = event.get("pathParameters") or {}
    application_id = (path.get("id") or path.get("applicationId") or "").strip()
    if not application_id:
        raise ValidationAppError("application id is required")
    if not _looks_like_id(application_id):
        raise ValidationAppError("application id is invalid")

    configure_logging(correlation_id, application_id)
    deadline.check()

    metadata = repo.get_metadata(application_id)
    if metadata is None:
        raise NotFoundError("Application not found")

    application = Application.from_metadata(metadata)
    logger.info(
        "application retrieved", extra={"application_status": metadata.status.value}
    )
    return json_response(200, application.to_public_dict())


def _looks_like_id(value: str) -> bool:
    if len(value) > 64 or len(value) < 8:
        return False
    return all(ch in _UUID_CHARS for ch in value)
