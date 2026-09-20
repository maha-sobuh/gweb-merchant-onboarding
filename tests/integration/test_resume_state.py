"""Resume and review: GET /applications/{id} returns the full normalized state
of a partially completed application, including what is still missing.
"""

from __future__ import annotations

import json

from handlers import applicant_update, applications_create, applications_get, business_update
from repositories.application_repo import ApplicationRepository


class FakeContext:
    aws_request_id = "resume-test-request-id"


def _event(application_id, body=None):
    event = {"headers": {}, "pathParameters": {"id": application_id}}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def test_get_returns_full_state_for_resume_and_review(dynamodb_table):
    repo = ApplicationRepository()
    ctx = FakeContext()

    created = applications_create.handler({"headers": {}, "body": "{}"}, ctx, repo=repo)
    application_id = json.loads(created["body"])["applicationId"]

    person = applicant_update.handler(
        _event(application_id, {"first_name": "Jane", "last_name": "Doe", "is_control_person": True}),
        ctx,
        repo=repo,
    )
    assert person["statusCode"] == 201, person["body"]

    business = business_update.handler(
        _event(application_id, {"legal_name": "Acme Test LLC", "entity_structure": "LLC"}),
        ctx,
        repo=repo,
    )
    assert business["statusCode"] == 201, business["body"]

    response = applications_get.handler(_event(application_id), ctx, repo=repo)
    assert response["statusCode"] == 200, response["body"]
    body = json.loads(response["body"])

    # Original metadata fields are unchanged.
    assert body["applicationId"] == application_id
    assert body["status"] == "IN_PROGRESS"

    # Everything captured so far comes back, so the client can resume.
    assert body["business"]["legalName"] == "Acme Test LLC"
    assert len(body["persons"]) == 1
    assert body["persons"][0]["firstName"] == "Jane"
    assert body["documents"] == []
    assert body["classification"] is None
    assert body["evaluation"] is None

    # And the review screen knows what is still missing.
    assert body["readyToSubmit"] is False
    assert "business.tax_id (EIN/TIN)" in body["missingItems"]
    assert "business.physical_address" in body["missingItems"]
    assert any("GOVERNMENT_ID" in item for item in body["missingItems"])
    assert any("confirmed MCC" in item for item in body["missingItems"])
