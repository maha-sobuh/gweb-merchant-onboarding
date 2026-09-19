"""45-second rule (spec section 7.1): a hanging external dependency must not
hang the request.

A fake AI adapter blocks far longer than the internal deadline. The evaluate
handler must still return before the deadline, fall back safely
(ai_available=False), and keep the deterministic rate arithmetic intact.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from adapters.ai_adapter import AIAdapterError
from handlers import applications_create, business_update, evaluate
from repositories.application_repo import ApplicationRepository

DEADLINE_SECONDS = 3  # tiny internal budget so the test stays fast

STATEMENT = {
    "processor": "Test Processor Inc",
    "statement_period": "2026-08",
    "monthly_volume_cents": 5_000_000,
    "monthly_transaction_count": 1000,
    "discount_rate_percent": 2.5,
    "per_transaction_fee_cents": 10,
    "monthly_fee_cents": 2500,
}


class FakeContext:
    aws_request_id = "slow-dependency-test"


class HangingAIAdapter:
    """Simulates an external model API that never answers in time."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def summarize_business_profile(self, business_description, selected_industry, deadline):
        self.release.wait(timeout=30)  # far beyond the internal deadline
        raise AIAdapterError("released by test")

    def extract_statement(self, original_filename, deadline):
        self.release.wait(timeout=30)
        raise AIAdapterError("released by test")


def test_hanging_ai_dependency_falls_back_before_the_deadline(dynamodb_table, monkeypatch):
    monkeypatch.setenv("INTERNAL_TIMEOUT_SECONDS", str(DEADLINE_SECONDS))
    repo = ApplicationRepository()
    ctx = FakeContext()

    created = applications_create.handler({"headers": {}, "body": "{}"}, ctx, repo=repo)
    application_id = json.loads(created["body"])["applicationId"]

    business = business_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps(
                {
                    "legal_name": "Acme Test LLC",
                    "entity_structure": "LLC",
                    "business_description": "Restaurant serving lunch and dinner to walk-in customers",
                }
            ),
        },
        ctx,
        repo=repo,
    )
    assert business["statusCode"] == 201, business["body"]

    ai = HangingAIAdapter()
    started = time.monotonic()
    try:
        response = evaluate.handler(
            {
                "pathParameters": {"id": application_id},
                "headers": {},
                "body": json.dumps({"processing_statement": STATEMENT}),
            },
            ctx,
            repo=repo,
            ai=ai,
        )
    finally:
        ai.release.set()  # let the blocked worker thread finish
    elapsed = time.monotonic() - started

    # The request returned before the internal deadline, with a normal 200.
    assert elapsed < DEADLINE_SECONDS, f"took {elapsed:.2f}s"
    assert response["statusCode"] == 200, response["body"]
    body = json.loads(response["body"])

    # The AI part degraded safely and says so.
    assert body["businessProfile"]["ai_available"] is False

    # The deterministic arithmetic is unaffected by the AI outage.
    assert body["statementAnalysis"]["effective_rate_percent"] == pytest.approx(2.75, abs=0.01)