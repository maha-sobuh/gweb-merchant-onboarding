"""POST /applications/{id}/submit - validate completeness, lock, produce
the normalized internal review payload (spec section 2, steps 7-8).

Blocks with 400 + an explicit missing-items list when required data is
absent. Locks the application (IN_PROGRESS -> SUBMITTED) exactly once via
a state-machine-guarded DynamoDB write; a second call is a 409.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from common.errors import DeadlineGuard, NotFoundError, ValidationAppError
from common.logging_config import configure_logging
from common.responses import error_response, json_response
from repositories.application_repo import ApplicationRepository
from services.submission_service import build_submission_snapshot, validate_for_submission

logger = logging.getLogger("merchant_onboarding.handlers.submit")


def handler(event: dict, context: Any, repo: ApplicationRepository | None = None) -> dict:
    correlation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    configure_logging(correlation_id)
    repo = repo or ApplicationRepository()

    try:
        with DeadlineGuard() as deadline:
            deadline.check()
            return _submit(event, repo, correlation_id, deadline)
    except Exception as exc:  # noqa: BLE001
        logger.exception("submit failed")
        return error_response(exc)


def _submit(
    event: dict,
    repo: ApplicationRepository,
    correlation_id: str,
    deadline: DeadlineGuard,
) -> dict:
    application_id = _extract_application_id(event)
    configure_logging(correlation_id, application_id)

    metadata = repo.get_metadata(application_id)
    if metadata is None:
        raise NotFoundError("Application not found")

    business = repo.get_business(application_id)
    persons = repo.list_persons(application_id)
    documents = repo.list_documents(application_id)
    classification = repo.get_classification(application_id)
    evaluation = repo.get_evaluation(application_id)
    deadline.check()

    missing_items = validate_for_submission(business, persons, documents, classification)
    if missing_items:
        logger.info(
            "submission blocked - missing items",
            extra={"missing_count": len(missing_items)},
        )
        return json_response(
            400,
            {
                "error": {
                    "code": "SUBMISSION_INCOMPLETE",
                    "message": "Application is missing required items for submission",
                },
                "missingItems": missing_items,
            },
        )

    snapshot = build_submission_snapshot(
        metadata, business, persons, documents, classification, evaluation
    )
    deadline.check()
    updated_metadata, submission = repo.submit_application(application_id, snapshot)

    logger.info(
        "application submitted",
        extra={"application_status": updated_metadata.status.value},
    )
    return json_response(
        200,
        {
            "applicationId": application_id,
            "status": updated_metadata.status.value,
            "submittedAt": submission.submitted_at,
            "snapshot": submission.snapshot,
        },
    )


def _extract_application_id(event: dict) -> str:
    path_params = event.get("pathParameters") or {}
    application_id = path_params.get("id")
    if not application_id:
        raise ValidationAppError("applicationId path parameter is required")
    return application_id
    