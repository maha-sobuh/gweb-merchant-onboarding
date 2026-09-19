"""Unit tests for POST /applications/{id}/evaluate and GET /applications/{id}/evaluation."""

from __future__ import annotations

import json

from adapters.ai_adapter import MockAIAdapter
from handlers import evaluate, get_evaluation
from repositories.application_repo import ApplicationRepository


class FakeContext:
    aws_request_id = "test-request-id"


def _evaluate_event(application_id: str, body: dict | None = None) -> dict:
    return {
        "pathParameters": {"id": application_id},
        "headers": {},
        "body": json.dumps(body if body is not None else {}),
    }


def _get_event(application_id: str) -> dict:
    return {"pathParameters": {"id": application_id}, "headers": {}}


def test_evaluate_returns_200_with_full_structure(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-1")

    response = evaluate.handler(_evaluate_event("app-1"), FakeContext(), repo=repo, ai=MockAIAdapter())

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "businessProfile" in body
    assert "riskSignals" in body
    assert body["version"] == 1


def test_evaluate_with_processing_statement_computes_rate(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-2")

    response = evaluate.handler(
        _evaluate_event(
            "app-2",
            {
                "processing_statement": {
                    "processor": "Stripe",
                    "statement_period": "2026-08",
                    "monthly_volume_cents": 1000000,
                    "monthly_transaction_count": 100,
                    "discount_rate_percent": 2.9,
                    "per_transaction_fee_cents": 30,
                    "monthly_fee_cents": 2500,
                }
            },
        ),
        FakeContext(),
        repo=repo,
        ai=MockAIAdapter(),
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["statementAnalysis"]["effective_rate_percent"] == 3.45


def test_evaluate_re_run_increments_version(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-3")

    first = evaluate.handler(_evaluate_event("app-3"), FakeContext(), repo=repo, ai=MockAIAdapter())
    second = evaluate.handler(_evaluate_event("app-3"), FakeContext(), repo=repo, ai=MockAIAdapter())

    assert json.loads(first["body"])["version"] == 1
    assert json.loads(second["body"])["version"] == 2


def test_evaluate_on_missing_application_is_404(dynamodb_table):
    repo = ApplicationRepository()

    response = evaluate.handler(
        _evaluate_event("nonexistent-app"), FakeContext(), repo=repo, ai=MockAIAdapter()
    )

    assert response["statusCode"] == 404


def test_evaluate_rejects_invalid_statement_fields(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-4")

    response = evaluate.handler(
        _evaluate_event(
            "app-4",
            {
                "processing_statement": {
                    "processor": "Stripe",
                    "statement_period": "2026-08",
                    "monthly_volume_cents": -100,  # invalid: must be > 0
                    "monthly_transaction_count": 100,
                    "discount_rate_percent": 2.9,
                    "per_transaction_fee_cents": 30,
                    "monthly_fee_cents": 2500,
                }
            },
        ),
        FakeContext(),
        repo=repo,
        ai=MockAIAdapter(),
    )

    assert response["statusCode"] == 400


def test_get_evaluation_before_any_evaluate_is_404(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-5")

    response = get_evaluation.handler(_get_event("app-5"), FakeContext(), repo=repo)

    assert response["statusCode"] == 404


def test_get_evaluation_returns_latest_result(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-6")
    evaluate.handler(_evaluate_event("app-6"), FakeContext(), repo=repo, ai=MockAIAdapter())

    response = get_evaluation.handler(_get_event("app-6"), FakeContext(), repo=repo)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["applicationId"] == "app-6"


def test_get_evaluation_on_missing_application_is_404(dynamodb_table):
    repo = ApplicationRepository()

    response = get_evaluation.handler(_get_event("nonexistent-app"), FakeContext(), repo=repo)

    assert response["statusCode"] == 404