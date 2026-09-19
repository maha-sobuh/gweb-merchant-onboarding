"""Demo walkthrough (spec deliverable: runnable demo / local Lambda simulation).

Runs the whole merchant onboarding journey through the REAL Lambda handlers,
in-process, against moto (DynamoDB + S3). No AWS account, Docker or network is
needed, and all data is obviously fake fixture data.

Run from the repository root:
    python scripts/demo_flow.py

Save the transcript (PowerShell):
    python scripts/demo_flow.py | Out-File -Encoding utf8 docs/demo-output.txt
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TABLE_NAME = "MerchantOnboarding"
BUCKET_NAME = "demo-documents-bucket"
REGION = "us-east-1"

os.environ.update(
    {
        "TABLE_NAME": TABLE_NAME,
        "DOCUMENTS_BUCKET": BUCKET_NAME,
        "LOG_LEVEL": "WARNING",  # keep the transcript readable; INFO logs are JSON on stderr
        "INTERNAL_TIMEOUT_SECONDS": "20",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
    }
)

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

FAKE_TAX_ID = "987654321"
FAKE_PDF = b"%PDF-1.4\n% fixture only\n%%EOF\n"
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"fixture only"

ADDRESS = {
    "line1": "1 Test Street",
    "city": "Testville",
    "state": "CA",
    "postal_code": "90001",
    "country": "US",
}

DOCUMENTS = [
    ("GOVERNMENT_ID", "id-front.png", "image/png", FAKE_PNG),
    ("BUSINESS_REGISTRATION", "registration.pdf", "application/pdf", FAKE_PDF),
    ("BANK_EVIDENCE", "voided-check.pdf", "application/pdf", FAKE_PDF),
]

STATEMENT = {
    "processor": "Test Processor Inc",
    "statement_period": "2026-08",
    "monthly_volume_cents": 5_000_000,
    "monthly_transaction_count": 1000,
    "discount_rate_percent": 2.5,
    "per_transaction_fee_cents": 10,
    "monthly_fee_cents": 2500,
}

BUSINESS = {
    "legal_name": "Acme Test LLC",
    "entity_structure": "LLC",
    "tax_id": FAKE_TAX_ID,
    "tax_id_type": "EIN",
    "formation_date": "2020-01-15",
    "formation_state": "CA",
    "formation_country": "US",
    "website": "https://acme-test.example",
    "email": "billing@acme-test.example",
    "industry_description": "Restaurants",
    "business_description": "Restaurant serving lunch and dinner to walk-in customers",
    "physical_address": ADDRESS,
    "annual_processing_volume": 60_000_000,
    "average_transaction_amount": 4500,
}


class Ctx:
    aws_request_id = "demo-request-id"


def event(application_id=None, body=None, **path_params):
    ev = {"headers": {}, "body": json.dumps(body) if body is not None else None}
    params = dict(path_params)
    if application_id:
        params["id"] = application_id
    if params:
        ev["pathParameters"] = params
    return ev


def heading(text):
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def show(label, response, expected, summarize=None):
    status = response["statusCode"]
    body = json.loads(response["body"]) if response.get("body") else None
    flag = "OK " if status == expected else "!! "
    print(f"{flag}{label} -> HTTP {status}")
    shown = summarize(body) if (summarize and isinstance(body, dict)) else body
    if shown is not None:
        for line in json.dumps(shown, indent=2).splitlines():
            print("    " + line)
    if status != expected:
        raise SystemExit(f"Unexpected status {status}, expected {expected}")
    return body


def setup_aws():
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    dynamodb.create_table(
        TableName=TABLE_NAME,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
    )
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET_NAME)


def journey():
    from handlers import (
        applicant_update,
        applications_create,
        business_update,
        classify,
        documents_complete,
        documents_presign,
        evaluate,
        submit,
    )
    from repositories.application_repo import ApplicationRepository
    from storage.s3_client import S3Client

    repo = ApplicationRepository()
    s3 = S3Client()
    s3_raw = boto3.client("s3", region_name=REGION)
    ctx = Ctx()

    heading("1. Create the application  (POST /applications)")
    created = show(
        "create",
        applications_create.handler({"headers": {"Idempotency-Key": "demo-key-1"}, "body": "{}"}, ctx, repo=repo),
        201,
    )
    app_id = created["applicationId"]

    heading("2. Try to submit too early  (POST /applications/{id}/submit)")
    show("submit (empty application)", submit.handler(event(app_id), ctx, repo=repo), 400)

    heading("3. Add the control person  (PATCH /applications/{id}/applicant)")
    show(
        "applicant",
        applicant_update.handler(
            event(app_id, {"first_name": "Jane", "last_name": "Doe", "is_control_person": True}),
            ctx,
            repo=repo,
        ),
        201,
    )

    heading("4. Add the business  (PATCH /applications/{id}/business)")
    show("business", business_update.handler(event(app_id, BUSINESS), ctx, repo=repo), 201)

    heading("5. Documents: presign -> direct upload to S3 -> complete")
    for doc_type, filename, content_type, content in DOCUMENTS:
        presigned = show(
            f"presign {doc_type}",
            documents_presign.handler(
                event(
                    app_id,
                    {
                        "document_type": doc_type,
                        "original_filename": filename,
                        "content_type": content_type,
                        "declared_size_bytes": len(content),
                    },
                ),
                ctx,
                repo=repo,
                s3=s3,
            ),
            201,
            summarize=lambda b: {
                "documentId": b["documentId"],
                "status": b["status"],
                "uploadMethod": b["uploadMethod"],
                "expiresInSeconds": b["expiresInSeconds"],
                "uploadUrl": b["uploadUrl"][:60] + "...",
            },
        )
        document_id = presigned["documentId"]
        key = repo.get_document(app_id, document_id).s3_key
        s3_raw.put_object(Bucket=BUCKET_NAME, Key=key, Body=content, ContentType=content_type)
        print(f"    (client PUTs {len(content)} bytes straight to S3; Lambda never sees the file)")
        show(
            f"complete {doc_type}",
            documents_complete.handler(
                event(app_id, {"checksum_sha256": hashlib.sha256(content).hexdigest()}, documentId=document_id),
                ctx,
                repo=repo,
                s3=s3,
            ),
            200,
            summarize=lambda b: {
                "documentId": b["documentId"],
                "status": b["status"],
                "actualSizeBytes": b["actualSizeBytes"],
            },
        )

    heading("6. Classify: propose MCC(s), then the applicant confirms  (POST .../classify)")
    classify_body = {
        "selected_industry": "Restaurants",
        "business_description": BUSINESS["business_description"],
    }
    short = lambda b: {  # noqa: E731
        "proposedMccs": [
            {k: p[k] for k in ("code", "description", "confidence", "riskTier")}
            for p in b["proposedMccs"][:3]
        ],
        "confirmedMcc": b["confirmedMcc"],
        "manualReviewRequired": b["manualReviewRequired"],
    }
    suggested = show("suggest", classify.handler(event(app_id, classify_body), ctx, repo=repo), 200, short)
    top_code = suggested["proposedMccs"][0]["code"]
    show(
        f"confirm {top_code}",
        classify.handler(event(app_id, {**classify_body, "confirmed_mcc": top_code}), ctx, repo=repo),
        200,
        short,
    )

    heading("7. Evaluate: deterministic rate math + AI commentary + risk signals  (POST .../evaluate)")
    show(
        "evaluate",
        evaluate.handler(event(app_id, {"processing_statement": STATEMENT}), ctx, repo=repo),
        200,
    )

    heading("8. Submit: lock the application and produce the review payload  (POST .../submit)")
    show("submit", submit.handler(event(app_id), ctx, repo=repo), 200)

    heading("9. Submit again: the application is locked  (expect 409)")
    show("submit (second call)", submit.handler(event(app_id), ctx, repo=repo), 409)


def slow_dependency_demo():
    """45-second rule: a hanging AI dependency must not hang the request."""
    from adapters.ai_adapter import AIAdapterError
    from handlers import applications_create, business_update, evaluate
    from repositories.application_repo import ApplicationRepository

    class HangingAI:
        def __init__(self):
            self.release = threading.Event()

        def summarize_business_profile(self, description, industry, deadline):
            self.release.wait(timeout=30)
            raise AIAdapterError("released")

        def extract_statement(self, filename, deadline):
            self.release.wait(timeout=30)
            raise AIAdapterError("released")

    heading("10. Slow dependency: the AI provider hangs, the request still returns in time")
    previous = os.environ["INTERNAL_TIMEOUT_SECONDS"]
    os.environ["INTERNAL_TIMEOUT_SECONDS"] = "3"
    print("    internal deadline for this demo: 3 seconds (production default: 20; API Gateway hard cap: 29)")
    try:
        repo = ApplicationRepository()
        ctx = Ctx()
        app_id = json.loads(
            applications_create.handler({"headers": {}, "body": "{}"}, ctx, repo=repo)["body"]
        )["applicationId"]
        business_update.handler(event(app_id, BUSINESS), ctx, repo=repo)

        ai = HangingAI()
        started = time.monotonic()
        try:
            response = evaluate.handler(event(app_id, {"processing_statement": STATEMENT}), ctx, repo=repo, ai=ai)
        finally:
            ai.release.set()
        elapsed = time.monotonic() - started
    finally:
        os.environ["INTERNAL_TIMEOUT_SECONDS"] = previous

    body = json.loads(response["body"])
    print(f"    elapsed: {elapsed:.2f}s  (HTTP {response['statusCode']})")
    print(f"    ai_available: {body['businessProfile']['ai_available']}  (safe fallback)")
    print(
        "    effective_rate_percent: "
        f"{body['statementAnalysis']['effective_rate_percent']}  (deterministic, unaffected by the AI outage)"
    )


def main():
    with mock_aws():
        setup_aws()
        journey()
        slow_dependency_demo()
    heading("Demo complete")


if __name__ == "__main__":
    main()