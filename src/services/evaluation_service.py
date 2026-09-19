"""Orchestrates one full evaluation: gathers everything already known about
an application, runs the deterministic rate calculation, runs the risk
signal checks, and calls the AI adapter for the business profile summary.

No boto3 here - all DynamoDB reads go through ApplicationRepository, which
is passed in. This module is pure business logic and is the piece that
would be unit-testable without any AWS mocking at all if it didn't need
the repo for input data.
"""

from __future__ import annotations
import concurrent.futures


from common.errors import DeadlineGuard
from adapters.ai_adapter import AIAdapter, AIAdapterError
from models.applicant import PersonRecord
from models.business import BusinessRecord
from models.document import DocumentStatus, DocumentType
from models.evaluation import (
    BusinessProfileSummary,
    EvaluateRequest,
    RiskSignal,
    StatementAnalysis,
    StatementExtraction,
)
from models.mcc import ClassificationRecord
from repositories.application_repo import ApplicationRepository

REQUIRED_DOCUMENT_TYPES = (
    DocumentType.GOVERNMENT_ID,
    DocumentType.BUSINESS_REGISTRATION,
    DocumentType.BANK_EVIDENCE,
)
HIGH_TICKET_THRESHOLD_CENTS = 10_000 * 100  # $10,000 — configurable, documented, not hardcoded logic

AI_CALL_RESERVE_SECONDS = 1.0  # headroom kept for saving the result and building the response


def _call_ai(fn, deadline: DeadlineGuard, *args):
    """Run one AI adapter call under a HARD timeout derived from the remaining
    deadline (spec 7.1: never wait indefinitely for AI). A hanging adapter is
    abandoned and reported as AIAdapterError, so callers fall back safely."""
    timeout = deadline.remaining() - AI_CALL_RESERVE_SECONDS
    if timeout <= 0:
        raise AIAdapterError("No time budget left for the AI call")
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn, *args)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        raise AIAdapterError("AI call timed out") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def run_evaluation(
    application_id: str,
    request: EvaluateRequest,
    repo: ApplicationRepository,
    ai: AIAdapter,
    deadline: DeadlineGuard,
) -> tuple[BusinessProfileSummary, StatementAnalysis | None, list[RiskSignal]]:
    business = repo.get_business(application_id)
    classification = repo.get_classification(application_id)
    persons = _list_all_persons(repo, application_id)
    documents = _list_all_documents(repo, application_id)

    business_profile = _summarize_business_profile(business, ai, deadline)
    statement_analysis = _analyze_statement(request, documents, ai, deadline)
    risk_signals = _compute_risk_signals(business, classification, persons, documents)

    return business_profile, statement_analysis, risk_signals


def _summarize_business_profile(
    business: BusinessRecord | None, ai: AIAdapter, deadline: DeadlineGuard
) -> BusinessProfileSummary:
    description = business.business_description if business else None
    industry = business.industry_description if business else None

    if not description:
        return BusinessProfileSummary(
            sales_channel="unknown",
            fulfillment_model="unknown",
            recurring_behavior="unknown",
            customer_type="unknown",
            summary_text="No business description on file yet; AI summary skipped.",
            ai_confidence=0.0,
            ai_available=False,
        )

    try:
        return _call_ai(ai.summarize_business_profile, deadline, description, industry or "", deadline)
    except AIAdapterError:
        # Fail safely, per spec: AI failure must never break the whole
        # evaluation - the rest of the response (statement analysis, risk
        # signals) is still fully computed and returned.
        return BusinessProfileSummary(
            sales_channel="unknown",
            fulfillment_model="unknown",
            recurring_behavior="unknown",
            customer_type="unknown",
            summary_text="AI business-profile summary was unavailable for this request (timeout or adapter failure).",
            ai_confidence=0.0,
            ai_available=False,
        )


def _analyze_statement(
    request: EvaluateRequest,
    documents: list,
    ai: AIAdapter,
    deadline: DeadlineGuard,
) -> StatementAnalysis | None:
    if request.processing_statement is not None:
        s = request.processing_statement
        extraction = StatementExtraction(
            processor=s.processor,
            statement_period=s.statement_period,
            monthly_volume_cents=s.monthly_volume_cents,
            monthly_transaction_count=s.monthly_transaction_count,
            discount_rate_percent=s.discount_rate_percent,
            per_transaction_fee_cents=s.per_transaction_fee_cents,
            monthly_fee_cents=s.monthly_fee_cents,
            source="client_provided",
            extraction_confidence=1.0,
        )
        return _compute_effective_rate(extraction)

    statement_doc = next(
        (
            d
            for d in documents
            if d.document_type == DocumentType.PROCESSING_STATEMENT
            and d.status == DocumentStatus.RECEIVED
        ),
        None,
    )
    if statement_doc is None:
        return None  # nothing to analyze - no statement provided or uploaded

    try:
        extraction = _call_ai(ai.extract_statement, deadline, statement_doc.original_filename, deadline)
    except AIAdapterError:
        extraction = StatementExtraction(
            source="ai_mock_extraction",
            extraction_confidence=0.0,
        )
    return _compute_effective_rate(extraction)


def _compute_effective_rate(extraction: StatementExtraction) -> StatementAnalysis:
    """Pure arithmetic. No AI involvement past this point - every number
    here is reproducible by hand from the extraction fields."""
    required = (
        extraction.monthly_volume_cents,
        extraction.monthly_transaction_count,
        extraction.discount_rate_percent,
        extraction.per_transaction_fee_cents,
        extraction.monthly_fee_cents,
    )
    if any(v is None for v in required) or extraction.monthly_volume_cents == 0:
        return StatementAnalysis(
            extraction=extraction,
            effective_rate_percent=None,
            calculation_note=(
                "Insufficient statement data to compute an effective rate "
                "(missing volume, transaction count, discount rate, or fees)."
            ),
        )

    total_fees_cents = (
        extraction.monthly_fee_cents
        + (extraction.monthly_transaction_count * extraction.per_transaction_fee_cents)
        + (extraction.monthly_volume_cents * extraction.discount_rate_percent / 100)
    )
    effective_rate_percent = round((total_fees_cents / extraction.monthly_volume_cents) * 100, 4)

    return StatementAnalysis(
        extraction=extraction,
        effective_rate_percent=effective_rate_percent,
        calculation_note=(
            "effective_rate_percent = (monthly_fee_cents + "
            "monthly_transaction_count * per_transaction_fee_cents + "
            "monthly_volume_cents * discount_rate_percent / 100) / "
            "monthly_volume_cents * 100"
        ),
    )


def _compute_risk_signals(
    business: BusinessRecord | None,
    classification: ClassificationRecord | None,
    persons: list[PersonRecord],
    documents: list,
) -> list[RiskSignal]:
    signals: list[RiskSignal] = []

    # 1. Missing required documents.
    received_types = {
        d.document_type for d in documents if d.status == DocumentStatus.RECEIVED
    }
    for required_type in REQUIRED_DOCUMENT_TYPES:
        if required_type not in received_types:
            signals.append(
                RiskSignal(
                    code="MISSING_REQUIRED_DOCUMENT",
                    message=f"No RECEIVED document of type {required_type.value} on file",
                    source_fields=["documents"],
                    severity="WARNING",
                )
            )

    # 2. Ownership percentages should sum close to 100% when any are declared.
    owners = [p for p in persons if p.is_beneficial_owner and p.ownership_percentage is not None]
    if owners:
        total = sum(p.ownership_percentage for p in owners)
        if total < 75 or total > 100:
            signals.append(
                RiskSignal(
                    code="INCOMPLETE_OWNERSHIP_DATA",
                    message=f"Declared beneficial-owner percentages sum to {total}%, expected close to 100%",
                    source_fields=[f"person.{p.person_id}.ownership_percentage" for p in owners],
                    severity="WARNING",
                )
            )

    # 3. Business description / confirmed MCC mismatch.
    if classification and classification.confirmed_mcc and classification.proposed_mccs:
        top_proposed = classification.proposed_mccs[0].code
        if classification.confirmed_mcc != top_proposed:
            signals.append(
                RiskSignal(
                    code="MCC_DESCRIPTION_MISMATCH",
                    message=(
                        f"Applicant confirmed MCC {classification.confirmed_mcc}, but the "
                        f"business description most closely matches {top_proposed}"
                    ),
                    source_fields=["classification.confirmed_mcc", "business.business_description"],
                    severity="WARNING",
                )
            )

    # 4. Unusually high average ticket size.
    if business and business.average_transaction_amount:
        if business.average_transaction_amount > HIGH_TICKET_THRESHOLD_CENTS:
            signals.append(
                RiskSignal(
                    code="UNUSUALLY_HIGH_TICKET_SIZE",
                    message=(
                        f"Average transaction amount {business.average_transaction_amount} cents "
                        f"exceeds the {HIGH_TICKET_THRESHOLD_CENTS} cent review threshold"
                    ),
                    source_fields=["business.average_transaction_amount"],
                    severity="WARNING",
                )
            )

    # 5. Sensitive/licensed MCC confirmed without a BUSINESS_LICENSE document.
    if (
        classification
        and classification.confirmed_mcc
        and classification.manual_review_required
        and DocumentType.BUSINESS_LICENSE not in received_types
    ):
        signals.append(
            RiskSignal(
                code="REGULATED_MCC_WITHOUT_LICENSE_EVIDENCE",
                message=(
                    f"Confirmed MCC {classification.confirmed_mcc} requires enhanced review, "
                    "but no BUSINESS_LICENSE document has been received"
                ),
                source_fields=["classification.confirmed_mcc", "documents"],
                severity="WARNING",
            )
        )

    return signals


def _list_all_persons(repo: ApplicationRepository, application_id: str) -> list[PersonRecord]:
    return repo.list_persons(application_id)


def _list_all_documents(repo: ApplicationRepository, application_id: str) -> list:
    return repo.list_documents(application_id)