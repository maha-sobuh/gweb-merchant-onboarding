"""Unit tests for POST /applications/{id}/documents/presign."""

from __future__ import annotations

import json

from handlers import documents_presign
from repositories.application_repo import ApplicationRepository
from storage.s3_client import S3Client


class FakeContext:
    aws_request_id = "test-request-id"


def _event(application_id: str, body: dict) -> dict:
    return {
        "pathParameters": {"id": application_id},
        "headers": {},
        "body": json.dumps(body),
    }


def _valid_body(**overrides) -> dict:
    body = {
        "document_type": "GOVERNMENT_ID",
        "original_filename": "license.pdf",
        "content_type": "application/pdf",
        "declared_size_bytes": 1024,
    }
    body.update(overrides)
    return body


def test_presign_creates_document_and_returns_upload_url(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-1")

    response = documents_presign.handler(
        _event("app-1", _valid_body()), FakeContext(), repo=repo, s3=s3
    )

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["documentId"]
    assert body["status"] == "REQUESTED"
    assert body["uploadUrl"].startswith("https://")
    assert body["uploadMethod"] == "PUT"
    assert body["requiredHeaders"] == {"Content-Type": "application/pdf"}


def test_presign_response_never_exposes_raw_s3_key(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-2")

    response = documents_presign.handler(
        _event("app-2", _valid_body()), FakeContext(), repo=repo, s3=s3
    )

    raw_body = response["body"]
    assert "s3Key" not in raw_body
    assert "s3_key" not in raw_body


def test_presign_rejects_disallowed_content_type(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-3")

    response = documents_presign.handler(
        _event("app-3", _valid_body(content_type="application/zip")),
        FakeContext(),
        repo=repo,
        s3=s3,
    )

    assert response["statusCode"] == 400


def test_presign_rejects_oversized_declared_size(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-4")

    response = documents_presign.handler(
        _event("app-4", _valid_body(declared_size_bytes=999_999_999)),
        FakeContext(),
        repo=repo,
        s3=s3,
    )

    assert response["statusCode"] == 400


def test_presign_on_missing_application_is_404(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()

    response = documents_presign.handler(
        _event("nonexistent-app", _valid_body()), FakeContext(), repo=repo, s3=s3
    )

    assert response["statusCode"] == 404


def test_presign_rejects_unknown_document_type(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-5")

    response = documents_presign.handler(
        _event("app-5", _valid_body(document_type="NOT_A_REAL_TYPE")),
        FakeContext(),
        repo=repo,
        s3=s3,
    )

    assert response["statusCode"] == 400


def test_presign_persists_document_record_in_requested_state(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-6")

    response = documents_presign.handler(
        _event("app-6", _valid_body()), FakeContext(), repo=repo, s3=s3
    )
    document_id = json.loads(response["body"])["documentId"]

    stored = repo.get_document("app-6", document_id)
    assert stored is not None
    assert stored.status.value == "REQUESTED"
    assert stored.declared_size_bytes == 1024