"""End-to-end journey through the real Lambda handlers (spec section 11):

create application -> applicant -> business -> pre-sign upload -> mock upload
completion -> classify -> evaluate -> submit.

Everything runs against moto (DynamoDB + S3); there are no real AWS calls.
All data is obviously fake fixture data - no real PII.
"""

from __future__ import annotations

import hashlib
import json

import boto3
import pytest

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
from models.submission import SUBMISSION_SK, submission_pk
from repositories.application_repo import ApplicationRepository
from storage.s3_client import S3Client

FAKE_TAX_ID = "987654321"

# NOTE: these keys must match the fields of models.applicant.Address.
ADDRESS = {
    "line1": "1 Test Street",
    "city": "Testville",
    "state": "CA",
    "postal_code": "90001",
    "country": "US",
}

FAKE_PDF = b"%PDF-1.4\n% fixture only\n%%EOF\n"
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"fixture only"

# (document_type, filename, content_type, bytes)
REQUIRED_DOCUMENTS = [
    ("GOVERNMENT_ID", "id-front.png", "image/png", FAKE_PNG),
    ("BUSINESS_REGISTRATION", "registration.pdf", "application/pdf", FAKE_PDF),
    ("BANK_EVIDENCE", "voided-check.pdf", "application/pdf", FAKE_PDF),
]

# 2500 + 1000 * 10 + 5_000_000 * 2.5 / 100 = 137_500 cents on 5_000_000 -> 2.75%
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
    aws_request_id = "journey-test-request-id"


def _event(application_id=None, body=None, **path_params):
    event = {"headers": {}, "body": json.dumps(body) if body is not None else None}
    params = dict(path_params)
    if application_id:
        params["id"] = application_id
    if params:
        event["pathParameters"] = params
    return event


def _expect(response, status):
    """Assert the status code (showing the body on failure) and return the parsed body."""
    assert response["statusCode"] == status, response["body"]
    return json.loads(response["body"])


def _create_application(repo, ctx):
    response = applications_create.handler({"headers": {}, "body": "{}"}, ctx, repo=repo)
    return _expect(response, 201)["applicationId"]


def test_full_merchant_onboarding_journey(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    s3_raw = boto3.client("s3")
    ctx = FakeContext()

    # 1. Create the application.
    application_id = _create_application(repo, ctx)

    # 2. Submitting an empty application is blocked with an explicit list.
    blocked = _expect(submit.handler(_event(application_id), ctx, repo=repo), 400)
    assert blocked["error"]["code"] == "SUBMISSION_INCOMPLETE"
    # business + control person + confirmed MCC + 3 required documents
    assert len(blocked["missingItems"]) == 6
    assert repo.get_metadata(application_id).status.value == "IN_PROGRESS"

    # 3. Control person.
    _expect(
        applicant_update.handler(
            _event(
                application_id,
                {"first_name": "Jane", "last_name": "Doe", "is_control_person": True},
            ),
            ctx,
            repo=repo,
        ),
        201,
    )

    # 4. Business.
    business = _expect(
        business_update.handler(
            _event(
                application_id,
                {
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
                },
            ),
            ctx,
            repo=repo,
        ),
        201,
    )
    assert business["version"] == 1

    # 5. Documents: presign -> (mock) direct upload to S3 -> complete.
    for document_type, filename, content_type, content in REQUIRED_DOCUMENTS:
        presigned = _expect(
            documents_presign.handler(
                _event(
                    application_id,
                    {
                        "document_type": document_type,
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
        )
        assert presigned["status"] == "REQUESTED"
        assert presigned["uploadUrl"].startswith("https://")
        assert "s3Key" not in presigned and "s3_key" not in presigned
        document_id = presigned["documentId"]

        # The client would PUT to uploadUrl; here we write straight to moto.
        key = repo.get_document(application_id, document_id).s3_key
        s3_raw.put_object(
            Bucket=documents_bucket, Key=key, Body=content, ContentType=content_type
        )

        completed = _expect(
            documents_complete.handler(
                _event(
                    application_id,
                    {"checksum_sha256": hashlib.sha256(content).hexdigest()},
                    documentId=document_id,
                ),
                ctx,
                repo=repo,
                s3=s3,
            ),
            200,
        )
        assert completed["status"] == "RECEIVED"
        assert completed["actualSizeBytes"] == len(content)

    # 6. Classification: propose first, then the applicant confirms a code.
    classify_body = {
        "selected_industry": "Restaurants",
        "business_description": "Restaurant serving lunch and dinner to walk-in customers",
    }
    suggested = _expect(classify.handler(_event(application_id, classify_body), ctx, repo=repo), 200)
    assert suggested["proposedMccs"], "expected at least one MCC proposal"
    assert suggested["confirmedMcc"] is None
    top_code = suggested["proposedMccs"][0]["code"]

    confirmed = _expect(
        classify.handler(
            _event(application_id, {**classify_body, "confirmed_mcc": top_code}), ctx, repo=repo
        ),
        200,
    )
    assert confirmed["confirmedMcc"] == top_code

    # 7. Evaluation: deterministic rate arithmetic + AI commentary + explainable signals.
    evaluation = _expect(
        evaluate.handler(_event(application_id, {"processing_statement": STATEMENT}), ctx, repo=repo),
        200,
    )
    effective_rate = evaluation["statementAnalysis"]["effective_rate_percent"]
    assert effective_rate == pytest.approx(2.75, abs=0.01)
    assert evaluation["businessProfile"]["summary_text"]
    for signal in evaluation["riskSignals"]:
        if signal["severity"] == "WARNING":
            assert signal["source_fields"], "every warning must cite its source fields"

    # 8. Submit: locks the application and returns the review payload.
    submit_response = submit.handler(_event(application_id), ctx, repo=repo)
    submitted = _expect(submit_response, 200)
    assert submitted["status"] == "SUBMITTED"
    snapshot = submitted["snapshot"]
    assert snapshot["business"]["taxIdLastFour"].endswith("4321")
    assert len(snapshot["persons"]) == 1
    assert len(snapshot["documents"]) == 3
    assert all(d["status"] == "RECEIVED" for d in snapshot["documents"])
    assert snapshot["classification"]["confirmedMcc"] == top_code
    assert snapshot["evaluation"]["statementAnalysis"]["effective_rate_percent"] == pytest.approx(
        2.75, abs=0.01
    )

    # Privacy: the raw tax id and the S3 key layout never leave the API.
    raw = submit_response["body"]
    assert FAKE_TAX_ID not in raw
    assert f"applications/{application_id}/documents" not in raw

    # 9. A second submit is a conflict, and the state is locked.
    _expect(submit.handler(_event(application_id), ctx, repo=repo), 409)
    assert repo.get_metadata(application_id).status.value == "SUBMITTED"

    # 10. The frozen snapshot item exists in DynamoDB.
    stored = dynamodb_table.get_item(
        Key={"PK": submission_pk(application_id), "SK": SUBMISSION_SK}
    )
    assert "Item" in stored


def test_complete_before_upload_is_rejected(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    ctx = FakeContext()
    application_id = _create_application(repo, ctx)

    presigned = _expect(
        documents_presign.handler(
            _event(
                application_id,
                {
                    "document_type": "GOVERNMENT_ID",
                    "original_filename": "id.png",
                    "content_type": "image/png",
                    "declared_size_bytes": len(FAKE_PNG),
                },
            ),
            ctx,
            repo=repo,
            s3=s3,
        ),
        201,
    )
    document_id = presigned["documentId"]

    # No object was ever PUT to S3, so /complete must refuse.
    response = documents_complete.handler(
        _event(
            application_id,
            {"checksum_sha256": hashlib.sha256(FAKE_PNG).hexdigest()},
            documentId=document_id,
        ),
        ctx,
        repo=repo,
        s3=s3,
    )
    assert response["statusCode"] == 400, response["body"]
    assert repo.get_document(application_id, document_id).status.value == "REQUESTED"