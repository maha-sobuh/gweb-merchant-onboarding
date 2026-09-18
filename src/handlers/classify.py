"""POST /applications/{id}/classify - MCC suggestion, confirmation, or correction.

Spec section 5 user journey: applicant self-selects an industry, system
proposes MCC(s) with risk tags, applicant confirms or corrects. Both the
applicant's selection and the system's proposal are persisted every call
so reviewers can inspect mismatches (spec: "Persist both applicant-selected
activity and system-proposed MCC so reviewers can inspect mismatches").

Never auto-approves: manual_review_required is computed independently of
what the applicant confirms (spec: "Never auto-approve a merchant solely
because an AI model labels it low risk" - this endpoint isn't even AI, and
still never auto-approves).
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
from models.mcc import ClassificationRecord, ClassifyRequest, classification_pk
from repositories.application_repo import ApplicationRepository
from services import mcc_service

logger = logging.getLogger("merchant_onboarding.handlers.classify")


def handler(event: dict, context: Any, repo: ApplicationRepository | None = None) -> dict:
    correlation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    configure_logging(correlation_id)
    repo = repo or ApplicationRepository()

    try:
        with DeadlineGuard() as deadline:
            deadline.check()
            return _classify(event, repo, correlation_id, deadline)
    except Exception as exc:  # noqa: BLE001
        logger.exception("classify failed")
        return error_response(exc)


def _classify(
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

    proposals = mcc_service.suggest(
        business_description=data.business_description,
        selected_industry=data.selected_industry,
        provider=data.provider,
    )

    confirmed_code = data.confirmed_mcc
    if confirmed_code and mcc_service.get_entry(confirmed_code) is None:
        raise ValidationAppError(f"confirmed_mcc '{confirmed_code}' is not a known MCC code")

    # Manual review is driven by the CONFIRMED code's risk tier when the
    # applicant has confirmed one; otherwise by the top proposal, plus
    # ambiguity in the suggestions regardless of confirmation.
    review_tier_code = confirmed_code or (proposals[0].code if proposals else None)
    sensitive = False
    if review_tier_code:
        tier, _ = mcc_service.risk_tier_for(review_tier_code, data.provider)
        sensitive = mcc_service.is_sensitive(tier)
    manual_review_required = sensitive or mcc_service.is_ambiguous(proposals)

    record = ClassificationRecord(
        PK=classification_pk(application_id),
        application_id=application_id,
        created_at="",  # filled by upsert_classification
        updated_at="",
        selected_industry=data.selected_industry,
        business_description=data.business_description,
        proposed_mccs=proposals,
        confirmed_mcc=confirmed_code,
        manual_review_required=manual_review_required,
    )
    saved = repo.upsert_classification(record)

    logger.info(
        "classification completed",
        extra={
            "confirmed": bool(confirmed_code),
            "top_proposal": proposals[0].code if proposals else None,
            "manual_review_required": manual_review_required,
        },
    )
    return json_response(200, saved.to_public_dict())


def _extract_application_id(event: dict) -> str:
    path_params = event.get("pathParameters") or {}
    application_id = path_params.get("id")
    if not application_id:
        raise ValidationAppError("applicationId path parameter is required")
    return application_id


def _validate_body(body: Any) -> ClassifyRequest:
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
        return ClassifyRequest.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", [])) or "body"
        logger.info("validation failed", extra={"field": field, "error_count": exc.error_count()})
        raise ValidationAppError(f"Invalid field: {field}") from exc