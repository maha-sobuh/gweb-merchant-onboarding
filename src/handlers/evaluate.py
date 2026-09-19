"""POST /applications/{id}/evaluate - run business profile + rate + risk evaluation.

Orchestration lives in services/evaluation_service.py (pure logic, no
boto3). This handler's job is just: validate input, load the AI adapter,
call the service, persist the result, return it.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from pydantic import ValidationError

from adapters.ai_adapter import MockAIAdapter
from common.errors import DeadlineGuard, NotFoundError, ValidationAppError
from common.logging_config import configure_logging
from common.responses import error_response, json_response
from models.evaluation import EvaluateRequest, EvaluationRecord, evaluation_pk
from repositories.application_repo import ApplicationRepository
from services.evaluation_service import run_evaluation

logger = logging.getLogger("merchant_onboarding.handlers.evaluate")


def handler(
    event: dict,
    context: Any,
    repo: ApplicationRepository | None = None,
    ai: Any = None,
) -> dict:
    correlation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    configure_logging(correlation_id)
    repo = repo or ApplicationRepository()
    ai = ai or MockAIAdapter()

    try:
        with DeadlineGuard() as deadline:
            deadline.check()
            return _evaluate(event, repo, ai, correlation_id, deadline)
    except Exception as exc:  # noqa: BLE001
        logger.exception("evaluate failed")
        return error_response(exc)


def _evaluate(
    event: dict,
    repo: ApplicationRepository,
    ai: Any,
    correlation_id: str,
    deadline: DeadlineGuard,
) -> dict:
    application_id = _extract_application_id(event)
    configure_logging(correlation_id, application_id)

    if repo.get_metadata(application_id) is None:
        raise NotFoundError("Application not found")

    request = _validate_body(event.get("body"))
    deadline.check()

    business_profile, statement_analysis, risk_signals = run_evaluation(
        application_id=application_id,
        request=request,
        repo=repo,
        ai=ai,
        deadline=deadline,
    )

    record = EvaluationRecord(
        PK=evaluation_pk(application_id),
        application_id=application_id,
        created_at="",
        updated_at="",
        business_profile=business_profile,
        statement_analysis=statement_analysis,
        risk_signals=risk_signals,
    )
    saved = repo.upsert_evaluation(record)

    logger.info(
        "evaluation completed",
        extra={
            "ai_available": business_profile.ai_available,
            "risk_signal_count": len(risk_signals),
            "has_statement_analysis": statement_analysis is not None,
        },
    )
    return json_response(200, saved.to_public_dict())


def _extract_application_id(event: dict) -> str:
    path_params = event.get("pathParameters") or {}
    application_id = path_params.get("id")
    if not application_id:
        raise ValidationAppError("applicationId path parameter is required")
    return application_id


def _validate_body(body: Any) -> EvaluateRequest:
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
        return EvaluateRequest.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", [])) or "body"
        logger.info("validation failed", extra={"field": field, "error_count": exc.error_count()})
        raise ValidationAppError(f"Invalid field: {field}") from exc
        