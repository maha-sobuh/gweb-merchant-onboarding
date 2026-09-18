"""Unit tests for GET /mcc and POST /applications/{id}/classify handlers."""

from __future__ import annotations

import json

from handlers import classify, mcc_search
from repositories.application_repo import ApplicationRepository


class FakeContext:
    aws_request_id = "test-request-id"


def _search_event(query: str | None) -> dict:
    return {"queryStringParameters": ({"query": query} if query is not None else None)}


def _classify_event(application_id: str, body: dict) -> dict:
    return {
        "pathParameters": {"id": application_id},
        "headers": {},
        "body": json.dumps(body),
    }


def test_mcc_search_returns_matching_results():
    response = mcc_search.handler(_search_event("restaurant"), FakeContext())
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    codes = {r["code"] for r in body["results"]}
    assert "5812" in codes


def test_mcc_search_with_no_query_param():
    response = mcc_search.handler(_search_event(None), FakeContext())
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert len(body["results"]) > 0


def test_classify_returns_suggestions_without_confirmed_mcc(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-1")

    response = classify.handler(
        _classify_event(
            "app-1",
            {
                "selected_industry": "Food and dining",
                "business_description": "We run a small family restaurant",
            },
        ),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert len(body["proposedMccs"]) > 0
    assert body["confirmedMcc"] is None
    assert body["version"] == 1


def test_classify_confirms_a_proposed_code(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-2")

    classify.handler(
        _classify_event(
            "app-2",
            {"selected_industry": "Food", "business_description": "family restaurant"},
        ),
        FakeContext(),
        repo=repo,
    )

    response = classify.handler(
        _classify_event(
            "app-2",
            {
                "selected_industry": "Food",
                "business_description": "family restaurant",
                "confirmed_mcc": "5812",
            },
        ),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["confirmedMcc"] == "5812"
    assert body["version"] == 2  # second call on the same application


def test_classify_rejects_unknown_confirmed_mcc(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-3")

    response = classify.handler(
        _classify_event(
            "app-3",
            {
                "selected_industry": "Food",
                "business_description": "restaurant",
                "confirmed_mcc": "9999",
            },
        ),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 400


def test_classify_flags_manual_review_for_sensitive_mcc(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(application_id="app-4")

    response = classify.handler(
        _classify_event(
            "app-4",
            {
                "selected_industry": "Financial services",
                "business_description": "We are a securities broker dealer",
                "confirmed_mcc": "6211",
            },
        ),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["manualReviewRequired"] is True


def test_classify_never_auto_clears_review_flag_just_because_confirmed(dynamodb_table):
    """Confirming a sensitive MCC must NOT clear manual_review_required —
    spec: never auto-approve solely on a classification result."""
    repo = ApplicationRepository()
    repo.create_application(application_id="app-5")

    response = classify.handler(
        _classify_event(
            "app-5",
            {
                "selected_industry": "Gambling",
                "business_description": "online casino and sportsbook",
                "confirmed_mcc": "7995",
            },
        ),
        FakeContext(),
        repo=repo,
    )

    body = json.loads(response["body"])
    assert body["manualReviewRequired"] is True


def test_classify_on_missing_application_is_404(dynamodb_table):
    repo = ApplicationRepository()

    response = classify.handler(
        _classify_event(
            "nonexistent-app",
            {"selected_industry": "Food", "business_description": "restaurant"},
        ),
        FakeContext(),
        repo=repo,
    )

    assert response["statusCode"] == 404


def test_classify_persists_both_selection_and_proposal(dynamodb_table):
    """spec: persist both applicant-selected activity and system-proposed MCC."""
    repo = ApplicationRepository()
    repo.create_application(application_id="app-6")

    classify.handler(
        _classify_event(
            "app-6",
            {"selected_industry": "Pharmacy", "business_description": "we sell prescription drugs"},
        ),
        FakeContext(),
        repo=repo,
    )

    stored = repo.get_classification("app-6")
    assert stored is not None
    assert stored.selected_industry == "Pharmacy"
    assert len(stored.proposed_mccs) > 0