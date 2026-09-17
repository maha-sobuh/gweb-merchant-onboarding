"""Integration test: create application -> add applicant -> update -> get.

Exercises applications_create, applicant_update, and applications_get
together against the same moto-backed table, end to end.
"""

from __future__ import annotations

import json

from handlers import applicant_update, applications_create, applications_get
from repositories.application_repo import ApplicationRepository


class FakeContext:
    aws_request_id = "integration-test-request-id"


def test_create_application_add_applicant_update_and_verify_version(dynamodb_table):
    repo = ApplicationRepository()

    # 1. Create the application.
    create_response = applications_create.handler({"headers": {}, "body": "{}"}, FakeContext(), repo=repo)
    assert create_response["statusCode"] == 201
    application_id = json.loads(create_response["body"])["applicationId"]

    # 2. Add a person (control person / owner).
    add_person_response = applicant_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps(
                {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "email": "jane@example.com",
                    "is_control_person": True,
                    "ownership_percentage": 60,
                }
            ),
        },
        FakeContext(),
        repo=repo,
    )
    assert add_person_response["statusCode"] == 201
    person_body = json.loads(add_person_response["body"])
    person_id = person_body["personId"]
    assert person_body["version"] == 1

    # 3. Update the same person's data using the version we just received.
    update_response = applicant_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps(
                {
                    "person_id": person_id,
                    "expected_version": 1,
                    "first_name": "Jane",
                    "last_name": "Doe-Smith",
                    "ownership_percentage": 60,
                }
            ),
        },
        FakeContext(),
        repo=repo,
    )
    assert update_response["statusCode"] == 200
    updated_body = json.loads(update_response["body"])
    assert updated_body["version"] == 2
    assert updated_body["lastName"] == "Doe-Smith"

    # 4. The application itself is untouched by adding/updating a person —
    #    GET /applications/{id} still returns METADATA only in Phase 1/2.
    get_response = applications_get.handler(
        {"pathParameters": {"id": application_id}, "headers": {}},
        FakeContext(),
        repo=repo,
    )
    assert get_response["statusCode"] == 200
    app_body = json.loads(get_response["body"])
    assert app_body["applicationId"] == application_id

    # 5. Directly confirm the person record via the repository (no GET
    #    endpoint for individual persons yet — that's a later phase).
    stored_person = repo.get_person(application_id, person_id)
    assert stored_person is not None
    assert stored_person.version == 2
    assert stored_person.last_name == "Doe-Smith"


def test_stale_update_after_a_successful_update_is_rejected(dynamodb_table):
    """Simulates two clients racing to update the same person."""
    repo = ApplicationRepository()
    create_response = applications_create.handler({"headers": {}, "body": "{}"}, FakeContext(), repo=repo)
    application_id = json.loads(create_response["body"])["applicationId"]

    created = applicant_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps({"first_name": "Jane"}),
        },
        FakeContext(),
        repo=repo,
    )
    person_id = json.loads(created["body"])["personId"]

    # Client A updates successfully (version 1 -> 2).
    client_a = applicant_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps({"person_id": person_id, "expected_version": 1, "first_name": "Jane A"}),
        },
        FakeContext(),
        repo=repo,
    )
    assert client_a["statusCode"] == 200

    # Client B still thinks the version is 1 -> must be rejected with 409.
    client_b = applicant_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps({"person_id": person_id, "expected_version": 1, "first_name": "Jane B"}),
        },
        FakeContext(),
        repo=repo,
    )
    assert client_b["statusCode"] == 409