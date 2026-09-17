"""Integration test: create application -> add applicant -> add business -> update business.

Exercises applications_create, applicant_update, and business_update together
against the same moto-backed table, confirming a person record and a business
record coexist correctly under the same application partition.
"""

from __future__ import annotations

import json

from handlers import applicant_update, applications_create, applications_get, business_update
from repositories.application_repo import ApplicationRepository


class FakeContext:
    aws_request_id = "integration-test-request-id"


def test_application_with_applicant_and_business_coexist(dynamodb_table):
    repo = ApplicationRepository()

    # 1. Create the application.
    create_response = applications_create.handler({"headers": {}, "body": "{}"}, FakeContext(), repo=repo)
    application_id = json.loads(create_response["body"])["applicationId"]

    # 2. Add a person.
    person_response = applicant_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps({"first_name": "Jane", "last_name": "Doe", "is_control_person": True}),
        },
        FakeContext(),
        repo=repo,
    )
    assert person_response["statusCode"] == 201
    person_id = json.loads(person_response["body"])["personId"]

    # 3. Add the business.
    business_response = business_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps({"legal_name": "Acme LLC", "entity_structure": "LLC"}),
        },
        FakeContext(),
        repo=repo,
    )
    assert business_response["statusCode"] == 201
    business_body = json.loads(business_response["body"])
    assert business_body["version"] == 1

    # 4. Update the business.
    update_response = business_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps({"expected_version": 1, "legal_name": "Acme Holdings LLC"}),
        },
        FakeContext(),
        repo=repo,
    )
    assert update_response["statusCode"] == 200
    assert json.loads(update_response["body"])["version"] == 2

    # 5. Application METADATA is still reachable and untouched.
    get_response = applications_get.handler(
        {"pathParameters": {"id": application_id}, "headers": {}}, FakeContext(), repo=repo
    )
    assert get_response["statusCode"] == 200

    # 6. Both the person and the business live under the same application
    #    partition and are independently retrievable — proof the single-table
    #    design (PK=APP#{id}, different SK per entity type) works as intended.
    stored_person = repo.get_person(application_id, person_id)
    stored_business = repo.get_business(application_id)
    assert stored_person is not None
    assert stored_business is not None
    assert stored_business.legal_name == "Acme Holdings LLC"
    assert stored_person.first_name == "Jane"


def test_business_update_does_not_affect_applicant_or_metadata_versions(dynamodb_table):
    """Different entity types under the same PK must have independent version counters."""
    repo = ApplicationRepository()
    create_response = applications_create.handler({"headers": {}, "body": "{}"}, FakeContext(), repo=repo)
    application_id = json.loads(create_response["body"])["applicationId"]

    person_response = applicant_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps({"first_name": "Jane"}),
        },
        FakeContext(),
        repo=repo,
    )
    person_id = json.loads(person_response["body"])["personId"]

    business_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps({"legal_name": "Acme LLC"}),
        },
        FakeContext(),
        repo=repo,
    )
    # One update on top of the create above: version should go 1 -> 2.
    business_update.handler(
        {
            "pathParameters": {"id": application_id},
            "headers": {},
            "body": json.dumps({"expected_version": 1, "legal_name": "Acme Holdings LLC"}),
        },
        FakeContext(),
        repo=repo,
    )

    metadata = repo.get_metadata(application_id)
    person = repo.get_person(application_id, person_id)
    business = repo.get_business(application_id)

    assert metadata.version == 1  # never touched by person/business writes
    assert person.version == 1  # never touched by business writes
    assert business.version == 2  # create (v1) -> one update (v2)