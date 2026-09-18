"""POST /applications/{id}/documents/{documentId}/complete - confirm an upload.

Verifies the object actually landed in S3 (HeadObject) before transitioning
the document record REQUESTED -> RECEIVED. See models/document.py module
docstring for the documented trust boundary around checksum_sha256 (it is
client-reported, not server-recomputed, in this prototype).
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
from models.document import CompleteRequest
from repositories.application_repo import ApplicationRepository
from storage.s3_client import S3Client

logger = logging.getLogger("merchant_onboarding.handlers.documents_complete")


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
            return _complete(event, repo, s3, correlation_id, deadline)
    except Exception as exc:  # noqa: BLE001
        logger.exception("document complete failed")
        return error_response(exc)


def _complete(
    event: dict,
    repo: ApplicationRepository,
    s3: S3Client,
    correlation_id: str,
    deadline: DeadlineGuard,
) -> dict:
    application_id, document_id = _extract_path_params(event)
    configure_logging(correlation_id, application_id)

    if repo.get_metadata(application_id) is None:
        raise NotFoundError("Application not found")

    document = repo.get_document(application_id, document_id)
    if document is None:
        raise NotFoundError("Document not found on this application")

    data = _validate_body(event.get("body"))
    deadline.check()  # about to make the S3 HeadObject call

    s3_info = s3.head_object(document.s3_key)
    if s3_info is None:
        # The client called /complete before actually PUTting the file to
        # the presigned URL (or used the wrong key). This is a client
        # ordering error, not a server error.
        raise ValidationAppError(
            "No object found in storage for this document — upload it to the "
            "presigned URL before calling /complete"
        )

    updated = repo.mark_document_received(
        application_id=application_id,
        document_id=document_id,
        checksum_sha256=data.checksum_sha256,
        actual_size_bytes=s3_info["size_bytes"],
    )

    logger.info(
        "document complete succeeded",
        extra={"document_id": document_id, "actual_size_bytes": s3_info["size_bytes"]},
    )
    return json_response(200, updated.to_public_dict())


def _extract_path_params(event: dict) -> tuple[str, str]:
    path_params = event.get("pathParameters") or {}
    application_id = path_params.get("id")
    document_id = path_params.get("documentId")
    if not application_id:
        raise ValidationAppError("applicationId path parameter is required")
    if not document_id:
        raise ValidationAppError("documentId path parameter is required")
    return application_id, document_id


def _validate_body(body: Any) -> CompleteRequest:
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
        return CompleteRequest.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", [])) or "body"
        logger.info("validation failed", extra={"field": field, "error_count": exc.error_count()})
        raise ValidationAppError(f"Invalid field: {field}") from exc