"""Unit tests for POST /applications/{id}/documents/{documentId}/complete."""

from __future__ import annotations

import json

import boto3

from handlers import documents_complete, documents_presign
from repositories.application_repo import ApplicationRepository
from storage.s3_client import S3Client


class FakeContext:
    aws_request_id = "test-request-id"


def _presign_event(application_id: str, body: dict | None = None) -> dict:
    default_body = {
        "document_type": "GOVERNMENT_ID",
        "original_filename": "license.pdf",
        "content_type": "application/pdf",
        "declared_size_bytes": 1024,
    }
    if body:
        default_body.update(body)
    return {
        "pathParameters": {"id": application_id},
        "headers": {},
        "body": json.dumps(default_body),
    }


def _complete_event(application_id: str, document_id: str, checksum: str | None = None) -> dict:
    return {
        "pathParameters": {"id": application_id, "documentId": document_id},
        "headers": {},
        "body": json.dumps({"checksum_sha256": checksum or ("a" * 64)}),
    }


def _presign_document(repo, s3, application_id: str) -> str:
    """Helper: run the presign step and return the resulting documentId."""
    response = documents_presign.handler(_presign_event(application_id), FakeContext(), repo=repo, s3=s3)
    return json.loads(response["body"])["documentId"]


def _upload_bytes_to_s3(bucket: str, key: str, content: bytes = b"fake-pdf-bytes") -> None:
    """Simulate the client's direct-to-S3 PUT that would normally happen
    against the presigned URL (moto intercepts real HTTP calls to real S3,
    but the presigned URL itself points at AWS's real endpoint, which moto
    cannot intercept over the network in this test environment — so we
    upload directly via the mocked S3 client instead, which is equivalent
    from the server's point of view: an object exists at that key)."""
    client = boto3.client("s3", region_name="us-east-1")
    client.put_object(Bucket=bucket, Key=key, Body=content)


def test_complete_transitions_document_to_received(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-1")
    document_id = _presign_document(repo, s3, "app-1")

    stored = repo.get_document("app-1", document_id)
    _upload_bytes_to_s3(documents_bucket, stored.s3_key)

    response = documents_complete.handler(
        _complete_event("app-1", document_id), FakeContext(), repo=repo, s3=s3
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "RECEIVED"
    assert body["actualSizeBytes"] == len(b"fake-pdf-bytes")


def test_complete_before_upload_is_400(dynamodb_table, documents_bucket):
    """Client calls /complete without ever PUTting the file to S3."""
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-2")
    document_id = _presign_document(repo, s3, "app-2")

    response = documents_complete.handler(
        _complete_event("app-2", document_id), FakeContext(), repo=repo, s3=s3
    )

    assert response["statusCode"] == 400


def test_complete_twice_is_conflict(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-3")
    document_id = _presign_document(repo, s3, "app-3")
    stored = repo.get_document("app-3", document_id)
    _upload_bytes_to_s3(documents_bucket, stored.s3_key)

    first = documents_complete.handler(
        _complete_event("app-3", document_id), FakeContext(), repo=repo, s3=s3
    )
    assert first["statusCode"] == 200

    second = documents_complete.handler(
        _complete_event("app-3", document_id), FakeContext(), repo=repo, s3=s3
    )
    assert second["statusCode"] == 409


def test_complete_on_missing_document_is_404(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-4")

    response = documents_complete.handler(
        _complete_event("app-4", "nonexistent-doc"), FakeContext(), repo=repo, s3=s3
    )

    assert response["statusCode"] == 404


def test_complete_on_missing_application_is_404(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()

    response = documents_complete.handler(
        _complete_event("nonexistent-app", "some-doc"), FakeContext(), repo=repo, s3=s3
    )

    assert response["statusCode"] == 404


def test_complete_rejects_malformed_checksum(dynamodb_table, documents_bucket):
    repo = ApplicationRepository()
    s3 = S3Client()
    repo.create_application(application_id="app-5")
    document_id = _presign_document(repo, s3, "app-5")
    stored = repo.get_document("app-5", document_id)
    _upload_bytes_to_s3(documents_bucket, stored.s3_key)

    response = documents_complete.handler(
        _complete_event("app-5", document_id, checksum="too-short"),
        FakeContext(),
        repo=repo,
        s3=s3,
    )

    assert response["statusCode"] == 400