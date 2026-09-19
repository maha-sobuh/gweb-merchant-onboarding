"""Unit tests for services/evaluation_service.py orchestration logic."""

from __future__ import annotations

from adapters.ai_adapter import MockAIAdapter
from common.errors import DeadlineGuard
from models.applicant import Applicant
from models.business import Business
from models.evaluation import EvaluateRequest, ProcessingStatementInput
from repositories.application_repo import ApplicationRepository
from services.evaluation_service import run_evaluation


def _repo() -> ApplicationRepository:
    return ApplicationRepository()


def _deadline() -> DeadlineGuard:
    return DeadlineGuard(seconds=20)


def test_effective_rate_computed_from_client_provided_statement(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-1")
    request = EvaluateRequest(
        processing_statement=ProcessingStatementInput(
            processor="Stripe",
            statement_period="2026-08",
            monthly_volume_cents=1_000_000,
            monthly_transaction_count=100,
            discount_rate_percent=2.9,
            per_transaction_fee_cents=30,
            monthly_fee_cents=2500,
        )
    )

    _, statement, _ = run_evaluation("app-1", request, repo, MockAIAdapter(), _deadline())

    assert statement is not None
    assert statement.extraction.source == "client_provided"
    # total_fees = 2500 + 100*30 + 1_000_000*2.9/100 = 2500 + 3000 + 29000 = 34500
    # effective_rate = 34500 / 1_000_000 * 100 = 3.45
    assert statement.effective_rate_percent == 3.45


def test_no_statement_data_returns_none_analysis(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-2")

    _, statement, _ = run_evaluation("app-2", EvaluateRequest(), repo, MockAIAdapter(), _deadline())

    assert statement is None


def test_flags_missing_required_documents(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-3")

    _, _, signals = run_evaluation("app-3", EvaluateRequest(), repo, MockAIAdapter(), _deadline())

    codes = {s.code for s in signals}
    assert "MISSING_REQUIRED_DOCUMENT" in codes


def test_flags_incomplete_ownership_data(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-4")
    repo.upsert_person(
        application_id="app-4",
        data=Applicant(first_name="A", is_beneficial_owner=True, ownership_percentage=40),
        person_id="p1",
        expected_version=None,
    )

    _, _, signals = run_evaluation("app-4", EvaluateRequest(), repo, MockAIAdapter(), _deadline())

    codes = {s.code for s in signals}
    assert "INCOMPLETE_OWNERSHIP_DATA" in codes


def test_no_ownership_signal_when_percentages_sum_to_100(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-5")
    repo.upsert_person(
        application_id="app-5",
        data=Applicant(first_name="A", is_beneficial_owner=True, ownership_percentage=60),
        person_id="p1",
        expected_version=None,
    )
    repo.upsert_person(
        application_id="app-5",
        data=Applicant(first_name="B", is_beneficial_owner=True, ownership_percentage=40),
        person_id="p2",
        expected_version=None,
    )

    _, _, signals = run_evaluation("app-5", EvaluateRequest(), repo, MockAIAdapter(), _deadline())

    codes = {s.code for s in signals}
    assert "INCOMPLETE_OWNERSHIP_DATA" not in codes


def test_flags_unusually_high_ticket_size(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-6")
    repo.upsert_business(
        application_id="app-6",
        data=Business(legal_name="Acme LLC", average_transaction_amount=2_000_000),
        expected_version=None,
    )

    _, _, signals = run_evaluation("app-6", EvaluateRequest(), repo, MockAIAdapter(), _deadline())

    codes = {s.code for s in signals}
    assert "UNUSUALLY_HIGH_TICKET_SIZE" in codes


def test_no_high_ticket_signal_for_normal_amount(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-7")
    repo.upsert_business(
        application_id="app-7",
        data=Business(legal_name="Acme LLC", average_transaction_amount=5_000),
        expected_version=None,
    )

    _, _, signals = run_evaluation("app-7", EvaluateRequest(), repo, MockAIAdapter(), _deadline())

    codes = {s.code for s in signals}
    assert "UNUSUALLY_HIGH_TICKET_SIZE" not in codes


def test_business_profile_falls_back_without_description(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-8")

    profile, _, _ = run_evaluation("app-8", EvaluateRequest(), repo, MockAIAdapter(), _deadline())

    assert profile.ai_available is False


def test_business_profile_succeeds_with_description(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-9")
    repo.upsert_business(
        application_id="app-9",
        data=Business(
            legal_name="Acme LLC",
            business_description="We run an online subscription box service",
        ),
        expected_version=None,
    )

    profile, _, _ = run_evaluation("app-9", EvaluateRequest(), repo, MockAIAdapter(), _deadline())

    assert profile.ai_available is True
    assert "recurring" in profile.recurring_behavior