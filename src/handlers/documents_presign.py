"""POST /applications/{id}/documents/presign - create a restricted S3 upload URL.

Spec 4.1: pre-signed S3 URLs so files upload directly client -> S3;
Lambda never proxies document bodies. Non-guessable key, private bucket.
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
from models.document import PresignRequest, build_s3_key
from repositories.application_repo import ApplicationRepository
from storage.s3_client import PRESIGN_EXPIRY_SECONDS, S3Client

logger = logging.getLogger("merchant_onboarding.handlers.documents_presign")


def handler(
    event: dict,
    context: Any,
    repo: ApplicationRepository | None = None,
    s3: S3Client | None = None,
) -> dict:
    correlation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    configure_logging(correlation_id)
    repo = repo or ApplicationRepository()
    s3 = s3 or S3Client()

    try:
        with DeadlineGuard() as deadline:
            deadline.check()
            return _presign(event, repo, s3, correlation_id, deadline)
    except Exception as exc:  # noqa: BLE001
        logger.exception("document presign failed")
        return error_response(exc)


def _presign(
    event: dict,
    repo: ApplicationRepository,
    s3: S3Client,
    correlation_id: str,
    deadline: DeadlineGuard,
) -> dict:
    application_id = _extract_application_id(event)
    configure_logging(correlation_id, application_id)

    if repo.get_metadata(application_id) is None:
        raise NotFoundError("Application not found")

    data = _validate_body(event.get("body"))
    deadline.check()

    document_id = str(uuid.uuid4())
    s3_key = build_s3_key(application_id, document_id, data.original_filename)

    record = repo.create_document(
        application_id=application_id,
        document_id=document_id,
        document_type=data.document_type,
        s3_key=s3_key,
        content_type=data.content_type,
        original_filename=data.original_filename,
        declared_size_bytes=data.declared_size_bytes,
    )

    deadline.check()  # S3 call is next; fail fast rather than start it late
    upload_url = s3.generate_presigned_put_url(s3_key, data.content_type)

    logger.info(
        "document presign completed",
        extra={"document_id": document_id, "document_type": data.document_type.value},
    )

    body = record.to_public_dict()
    body["uploadUrl"] = upload_url
    body["uploadMethod"] = "PUT"
    body["requiredHeaders"] = {"Content-Type": data.content_type}
    body["expiresInSeconds"] = PRESIGN_EXPIRY_SECONDS
    return json_response(201, body)


def _extract_application_id(event: dict) -> str:
    path_params = event.get("pathParameters") or {}
    application_id = path_params.get("id")
    if not application_id:
        raise ValidationAppError("applicationId path parameter is required")
    return application_id


def _validate_body(body: Any) -> PresignRequest:
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
        data = PresignRequest.model_validate(payload)
        data.validate_content_type()
        return data
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", [])) or "body"
        logger.info("validation failed", extra={"field": field, "error_count": exc.error_count()})
        raise ValidationAppError(f"Invalid field: {field}") from exc
    except ValueError as exc:
        raise ValidationAppError(str(exc)) from exc