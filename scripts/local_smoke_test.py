"""
Local smoke test for the merchant onboarding Lambda handlers.

This script exercises the handlers with realistic API-Gateway-shaped
events WITHOUT requiring `sam local start-api` (which needs Docker/WSL2).
It validates handler logic and event parsing only - it does NOT replace
`sam local start-api` for full fidelity testing (API Gateway request
validation, CORS, timeout enforcement).

Run with:
    $env:PYTHONPATH="src"; python scripts/local_smoke_test.py
"""

import json
import os
import uuid

os.environ["TABLE_NAME"] = "MerchantOnboarding"
os.environ["LOG_LEVEL"] = "INFO"
os.environ["INTERNAL_TIMEOUT_SECONDS"] = "20"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

import boto3
from moto import mock_aws


class FakeLambdaContext:
    function_name = "local-smoke-test"
    memory_limit_in_mb = 256
    invoked_function_arn = (
        "arn:aws:lambda:us-east-1:123456789012:function:local-smoke-test"
    )
    aws_request_id = str(uuid.uuid4())

    def get_remaining_time_in_millis(self):
        return 28000


def make_table():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=os.environ["TABLE_NAME"],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def make_post_event(idempotency_key=None, body=None):
    headers = {"Content-Type": "application/json"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return {
        "httpMethod": "POST",
        "path": "/applications",
        "headers": headers,
        "body": json.dumps(body if body is not None else {}),
        "isBase64Encoded": False,
        "pathParameters": None,
        "queryStringParameters": None,
        "requestContext": {
            "requestId": str(uuid.uuid4()),
            "httpMethod": "POST",
            "resourcePath": "/applications",
        },
    }


def make_get_event(application_id):
    return {
        "httpMethod": "GET",
        "path": f"/applications/{application_id}",
        "headers": {},
        "body": None,
        "isBase64Encoded": False,
        "pathParameters": {"id": application_id},
        "queryStringParameters": None,
        "requestContext": {
            "requestId": str(uuid.uuid4()),
            "httpMethod": "GET",
            "resourcePath": "/applications/{id}",
        },
    }


def pretty(label, response):
    print(f"\n{'=' * 70}")
    print(label)
    print("=" * 70)
    status = response.get("statusCode")
    print(f"statusCode: {status}")
    raw_body = response.get("body")
    try:
        parsed = json.loads(raw_body) if raw_body else None
    except (TypeError, ValueError):
        parsed = raw_body
    print("body:")
    print(json.dumps(parsed, indent=2, ensure_ascii=False))
    return status, parsed


@mock_aws
def main():
    make_table()

    from handlers import applications_create, applications_get

    context = FakeLambdaContext()

    event = make_post_event()
    response = applications_create.handler(event, context)
    status, body = pretty("TEST 1: POST /applications", response)
    application_id = body.get("applicationId") if isinstance(body, dict) else None

    if application_id:
        event = make_get_event(application_id)
        response = applications_get.handler(event, context)
        pretty("TEST 2: GET /applications/{id}", response)
    else:
        print("\nTEST 2 skipped - no applicationId returned from TEST 1")

    key = f"idem-{uuid.uuid4()}"
    event1 = make_post_event(idempotency_key=key)
    response1 = applications_create.handler(event1, context)
    _, body1 = pretty("TEST 3a: POST with Idempotency-Key (first call)", response1)
    id1 = body1.get("applicationId") if isinstance(body1, dict) else None

    event2 = make_post_event(idempotency_key=key)
    response2 = applications_create.handler(event2, context)
    _, body2 = pretty("TEST 3b: POST with Idempotency-Key (second call, same key)", response2)
    id2 = body2.get("applicationId") if isinstance(body2, dict) else None

    print(f"\n{'=' * 70}")
    print("TEST 3: Idempotency - both IDs below must match")
    print("=" * 70)
    print(f"First call applicationId:  {id1}")
    print(f"Second call applicationId: {id2}")
    print(f"MATCH: {'YES' if id1 == id2 and id1 is not None else 'NO - PROBLEM'}")

    fake_id = str(uuid.uuid4())
    event = make_get_event(fake_id)
    response = applications_get.handler(event, context)
    status, _ = pretty("TEST 4: GET nonexistent id - expect 404", response)
    print(f"\nExpected 404, got {status}: {'PASS' if status == 404 else 'CHECK THIS'}")

    print(f"\n{'=' * 70}")
    print("Smoke test complete. Review each block above for correctness.")
    print("=" * 70)


if __name__ == "__main__":
    main()
