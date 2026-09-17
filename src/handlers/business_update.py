"""PATCH /applications/{id}/business - create or update the business record.

Business is a singleton per application (one legal entity). Stateless.
Optimistic concurrency via `expected_version` in the body.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from pydantic import ValidationError

from common.errors import DeadlineGuard, NotFoundError, ValidationAppError
from common.logging_config import configure_logging
from common.responses import error_response, json_response
from models.business import Business
from repositories.application_repo import ApplicationRepository

logger = logging.getLogger("merchant_onboarding.handlers.business_update")


def handler(event: dict, context: Any, repo: ApplicationRepository | None = None) -> dict:
    correlation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    configure_logging(correlation_id)
    repo = repo or ApplicationRepository()

    try:
        with DeadlineGuard() as deadline:
            deadline.check()
            return _update(event, repo, correlation_id, deadline)
    except Exception as exc:  # noqa: BLE001
        logger.exception("business update failed")
        return error_response(exc)


def _update(
    event: dict,
    repo: ApplicationRepository,
    correlation_id: str,
    deadline: DeadlineGuard,
) -> dict:
    application_id = _extract_application_id(event)
    configure_logging(correlation_id, application_id)

    if repo.get_metadata(application_id) is None:
        raise NotFoundError("Application not found")

    data = _validate_body(event.get("body"))
    deadline.check()

    record, created = repo.upsert_business(
        application_id=application_id,
        data=data,
        expected_version=data.expected_version,
    )

    logger.info(
        "business upsert completed",
        extra={"business_created": created},
    )
    return json_response(201 if created else 200, record.to_public_dict())


def _extract_application_id(event: dict) -> str:
    path_params = event.get("pathParameters") or {}
    application_id = path_params.get("id")
    if not application_id:
        raise ValidationAppError("applicationId path parameter is required")
    return application_id


def _validate_body(body: Any) -> Business:
    if body in (None, "", b""):
        payload: Any = {}
    else:
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8")
        if isinstance(body, str):
            try:
                payload = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError as exc:
                raise ValidationAppError("Request body must be valid JSON") from exc
        elif isinstance(body, dict):
            payload = body
        else:
            raise ValidationAppError("Request body must be a JSON object")

    if not isinstance(payload, dict):
        raise ValidationAppError("Request body must be a JSON object")

    try:
        return Business.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", [])) or "body"
        logger.info("validation failed", extra={"field": field, "error_count": exc.error_count()})
        raise ValidationAppError(f"Invalid field: {field}") from exc