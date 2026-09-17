"""Unit tests for the PATCH /applications/{id}/applicant handler (Phase 2)."""

from __future__ import annotations

import json

from handlers import applicant_update
from repositories.application_repo import ApplicationRepository


class FakeContext:
    aws_request_id = "test-request-id"


def _event(application_id: str, body: dict | None = None) -> dict:
    return {
        "pathParameters": {"id": application_id},
        "headers": {},
        "body": json.dumps(body if body is not None else {}),
    }


def test_create_new_person_returns_201(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-1")

    response = applicant_update.handler(
        _event("app-1", {"first_name": "Jane", "last_name": "Doe"}),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["personId"]
    assert body["version"] == 1
    assert body["firstName"] == "Jane"


def test_update_existing_person_returns_200_with_incremented_version(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-2")

    created = applicant_update.handler(
        _event("app-2", {"first_name": "Jane"}), FakeContext(), repo=repo
    )
    person_id = json.loads(created["body"])["personId"]

    updated = applicant_update.handler(
        _event("app-2", {"person_id": person_id, "expected_version": 1, "first_name": "Janet"}),
        FakeContext(),
        repo=repo,
    )

    assert updated["statusCode"] == 200
    body = json.loads(updated["body"])
    assert body["version"] == 2
    assert body["firstName"] == "Janet"


def test_update_without_expected_version_is_400(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-3")
    created = applicant_update.handler(
        _event("app-3", {"first_name": "Jane"}), FakeContext(), repo=repo
    )
    person_id = json.loads(created["body"])["personId"]

    response = applicant_update.handler(
        _event("app-3", {"person_id": person_id, "first_name": "Janet"}),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 400


def test_stale_version_is_409(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-4")
    created = applicant_update.handler(
        _event("app-4", {"first_name": "Jane"}), FakeContext(), repo=repo
    )
    person_id = json.loads(created["body"])["personId"]

    response = applicant_update.handler(
        _event(
            "app-4",
            {"person_id": person_id, "expected_version": 99, "first_name": "Janet"},
        ),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 409


def test_update_on_missing_application_is_404(dynamodb_table):
    repo = ApplicationRepository()
    response = applicant_update.handler(
        _event("nonexistent-app", {"first_name": "Jane"}), FakeContext(), repo=repo
    )
    assert response["statusCode"] == 404


def test_invalid_ownership_percentage_is_400_with_field_detail(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-5")

    response = applicant_update.handler(
        _event("app-5", {"first_name": "Jane", "ownership_percentage": 150}),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert "ownership_percentage" in body["error"]["message"]


def test_unknown_field_is_rejected(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-6")

    response = applicant_update.handler(
        _event("app-6", {"first_name": "Jane", "ssn_typo_field": "123456789"}),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 400


def test_response_never_contains_raw_tax_id_or_government_id(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-7")

    response = applicant_update.handler(
        _event(
            "app-7",
            {
                "first_name": "Jane",
                "tax_id": "123-45-6789",
                "government_id_number": "X99887766",
            },
        ),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 201
    raw_body = response["body"]
    assert "123-45-6789" not in raw_body
    assert "X99887766" not in raw_body
    body = json.loads(raw_body)
    assert body["taxIdLastFour"] == "6789"
    assert body["governmentIdLastFour"] == "7766"