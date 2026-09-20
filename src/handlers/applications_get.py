"""GET /applications/{id} - retrieve the normalized application state.

Returns the application metadata plus everything captured so far (business,
persons, documents, MCC classification, evaluation) and the list of items still
missing for submission. This is what a client needs to resume a partially
completed application and to render the final review screen.

Stateless and read-only: GetItem / Query calls only. Repeating the call with the
same id is safe.
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
from services.submission_service import validate_for_submission

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
    except Exception as exc:  # noqa: BLE001 - mapped to a safe 4xx/5xx body
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

    business = repo.get_business(application_id)
    persons = repo.list_persons(application_id)
    documents = repo.list_documents(application_id)
    classification = repo.get_classification(application_id)
    evaluation = repo.get_evaluation(application_id)
    deadline.check()

    # Same completeness rules the submit endpoint enforces, so the client can
    # show exactly what is still missing before it tries to submit.
    missing_items = validate_for_submission(business, persons, documents, classification)

    body = Application.from_metadata(metadata).to_public_dict()
    body.update(
        {
            "business": business.to_public_dict() if business else None,
            "persons": [p.to_public_dict() for p in persons],
            "documents": [d.to_public_dict() for d in documents],
            "classification": classification.to_public_dict() if classification else None,
            "evaluation": evaluation.to_public_dict() if evaluation else None,
            "missingItems": missing_items,
            "readyToSubmit": not missing_items and metadata.status.value == "IN_PROGRESS",
        }
    )

    logger.info(
        "application retrieved",
        extra={
            "application_status": metadata.status.value,
            "missing_count": len(missing_items),
        },
    )
    return json_response(200, body)


def _looks_like_id(value: str) -> bool:
    if len(value) > 64 or len(value) < 8:
        return False
    return all(ch in _UUID_CHARS for ch in value)
