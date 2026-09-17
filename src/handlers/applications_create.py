"""POST /applications — create a merchant onboarding application.

Stateless: all durable state lives in DynamoDB.
Idempotent-safe: optional Idempotency-Key header maps to at most one application.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from common.errors import DeadlineGuard, ValidationAppError
from common.logging_config import configure_logging
from common.responses import error_response, json_response
from models.application import Application
from repositories.application_repo import ApplicationRepository

logger = logging.getLogger("merchant_onboarding.handlers.create")

# Reject oversized keys so they cannot bloat the table or log lines.
_MAX_IDEMPOTENCY_KEY_LEN = 256


class CreateApplicationRequest(BaseModel):
    """Phase 1 body is empty (or {}). Extra fields are rejected."""

    model_config = ConfigDict(extra="forbid")


def handler(event: dict, context: Any, repo: ApplicationRepository | None = None) -> dict:
    correlation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    configure_logging(correlation_id)
    repo = repo or ApplicationRepository()

    try:
        with DeadlineGuard() as deadline:
            deadline.check()
            return _create(event, repo, correlation_id, deadline)
    except Exception as exc:  # noqa: BLE001 — mapped to a safe 4xx/5xx body
        logger.exception("create application failed")
        return error_response(exc)


def _create(
    event: dict,
    repo: ApplicationRepository,
    correlation_id: str,
    deadline: DeadlineGuard,
) -> dict:
    _validate_body(event.get("body"))
    idempotency_key = _extract_idempotency_key(event.get("headers") or {})
    application_id = str(uuid.uuid4())

    configure_logging(correlation_id, application_id)
    deadline.check()

    metadata, created = repo.create_application(
        application_id=application_id,
        idempotency_key=idempotency_key,
    )
    application = Application.from_metadata(metadata)
    configure_logging(correlation_id, metadata.application_id)

    logger.info(
        "application create completed",
        extra={"was_created": created, "application_status": metadata.status.value},
    )
    return json_response(201, application.to_public_dict())


def _validate_body(body: Any) -> None:
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

    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValidationAppError("Request body must be a JSON object")

    try:
        CreateApplicationRequest.model_validate(payload)
    except ValidationError as exc:
        # Field names only — never include input values (could be PII later).
        logger.info("validation failed", extra={"error_count": exc.error_count()})
        raise ValidationAppError("Request body contains unknown or invalid fields") from exc


def _extract_idempotency_key(headers: dict) -> str | None:
    raw = None
    for key, value in headers.items():
        if key.lower() == "idempotency-key":
            raw = value
            break
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise ValidationAppError("Idempotency-Key must be a string")
    key = raw.strip()
    if not key:
        return None
    if len(key) > _MAX_IDEMPOTENCY_KEY_LEN:
        raise ValidationAppError("Idempotency-Key is too long")
    return key
