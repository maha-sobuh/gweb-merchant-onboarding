"""Unit tests for the PATCH /applications/{id}/business handler (Phase 2)."""

from __future__ import annotations

import json

from handlers import business_update
from repositories.application_repo import ApplicationRepository


class FakeContext:
    aws_request_id = "test-request-id"


def _event(application_id: str, body: dict | None = None) -> dict:
    return {
        "pathParameters": {"id": application_id},
        "headers": {},
        "body": json.dumps(body if body is not None else {}),
    }


def test_create_new_business_returns_201(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-1")

    response = business_update.handler(
        _event("app-1", {"legal_name": "Acme LLC", "website": "https://acme.example.com"}),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["version"] == 1
    assert body["legalName"] == "Acme LLC"


def test_update_existing_business_returns_200_with_incremented_version(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-2")

    business_update.handler(_event("app-2", {"legal_name": "Acme LLC"}), FakeContext(), repo=repo)

    updated = business_update.handler(
        _event("app-2", {"expected_version": 1, "legal_name": "Acme Holdings LLC"}),
        FakeContext(),
        repo=repo,
    )

    assert updated["statusCode"] == 200
    body = json.loads(updated["body"])
    assert body["version"] == 2
    assert body["legalName"] == "Acme Holdings LLC"


def test_second_create_without_expected_version_is_409(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-3")

    business_update.handler(_event("app-3", {"legal_name": "Acme LLC"}), FakeContext(), repo=repo)

    response = business_update.handler(
        _event("app-3", {"legal_name": "Acme Again LLC"}), FakeContext(), repo=repo
    )

    assert response["statusCode"] == 409


def test_stale_version_is_409(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-4")
    business_update.handler(_event("app-4", {"legal_name": "Acme LLC"}), FakeContext(), repo=repo)

    response = business_update.handler(
        _event("app-4", {"expected_version": 99, "legal_name": "Acme Holdings LLC"}),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 409


def test_update_on_missing_application_is_404(dynamodb_table):
    repo = ApplicationRepository()
    response = business_update.handler(
        _event("nonexistent-app", {"legal_name": "Acme LLC"}), FakeContext(), repo=repo
    )
    assert response["statusCode"] == 404


def test_unknown_field_is_rejected(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-5")

    response = business_update.handler(
        _event("app-5", {"legal_name": "Acme LLC", "typo_field": "oops"}),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 400


def test_negative_annual_processing_volume_is_400(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-6")

    response = business_update.handler(
        _event("app-6", {"legal_name": "Acme LLC", "annual_processing_volume": -100}),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert "annual_processing_volume" in body["error"]["message"]


def test_response_never_contains_raw_tax_id(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-7")

    response = business_update.handler(
        _event("app-7", {"legal_name": "Acme LLC", "tax_id": "12-3456789"}),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 201
    raw_body = response["body"]
    assert "12-3456789" not in raw_body
    body = json.loads(raw_body)
    assert body["taxIdLastFour"] == "6789"