"""Round-trip create -> get against mocked DynamoDB (moto)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from handlers.applications_create import handler as create_handler
from handlers.applications_get import handler as get_handler


def _ctx():
    return SimpleNamespace(aws_request_id="integration-1")


def test_create_then_get_round_trip(dynamodb_table):
    create_result = create_handler({"headers": {}, "body": "{}"}, _ctx())
    assert create_result["statusCode"] == 201
    created = json.loads(create_result["body"])
    application_id = created["applicationId"]
    assert created["status"] == "IN_PROGRESS"
    assert created["version"] == 1

    get_result = get_handler({"pathParameters": {"id": application_id}}, _ctx())
    assert get_result["statusCode"] == 200
    fetched = json.loads(get_result["body"])
    assert fetched == created


def test_get_unknown_application_is_404(dynamodb_table):
    result = get_handler(
        {"pathParameters": {"id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}},
        _ctx(),
    )
    assert result["statusCode"] == 404
    body = json.loads(result["body"])
    assert body["error"]["code"] == "NOT_FOUND"


def test_idempotency_key_does_not_duplicate(dynamodb_table):
    event = {"headers": {"Idempotency-Key": "same-client-key"}, "body": "{}"}
    first = create_handler(event, _ctx())
    second = create_handler(event, _ctx())

    assert first["statusCode"] == 201
    assert second["statusCode"] == 201
    first_body = json.loads(first["body"])
    second_body = json.loads(second["body"])
    assert first_body["applicationId"] == second_body["applicationId"]

    # Distinct keys still create distinct applications.
    other = create_handler(
        {"headers": {"Idempotency-Key": "other-client-key"}, "body": "{}"},
        _ctx(),
    )
    assert json.loads(other["body"])["applicationId"] != first_body["applicationId"]
