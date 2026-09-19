"""GET /applications/{id}/evaluation - retrieve the most recent evaluation result."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from common.errors import DeadlineGuard, NotFoundError, ValidationAppError
from common.logging_config import configure_logging
from common.responses import error_response, json_response
from repositories.application_repo import ApplicationRepository

logger = logging.getLogger("merchant_onboarding.handlers.get_evaluation")


def handler(event: dict, context: Any, repo: ApplicationRepository | None = None) -> dict:
    correlation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    configure_logging(correlation_id)
    repo = repo or ApplicationRepository()

    try:
        with DeadlineGuard() as deadline:
            deadline.check()
            return _get(event, repo, correlation_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("get evaluation failed")
        return error_response(exc)


def _get(event: dict, repo: ApplicationRepository, correlation_id: str) -> dict:
    application_id = _extract_application_id(event)
    configure_logging(correlation_id, application_id)

    if repo.get_metadata(application_id) is None:
        raise NotFoundError("Application not found")

    evaluation = repo.get_evaluation(application_id)
    if evaluation is None:
        raise NotFoundError("No evaluation has been run yet for this application")

    return json_response(200, evaluation.to_public_dict())


def _extract_application_id(event: dict) -> str:
    path_params = event.get("pathParameters") or {}
    application_id = path_params.get("id")
    if not application_id:
        raise ValidationAppError("applicationId path parameter is required")
    return application_id