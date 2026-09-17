"""Unit tests for document repository methods (Phase 3a: create/get/receive)."""

from __future__ import annotations

from common.errors import ConflictError, NotFoundError
from models.document import DocumentStatus, DocumentType
from repositories.application_repo import ApplicationRepository


def _repo() -> ApplicationRepository:
    return ApplicationRepository()


def test_create_document_creates_requested_record(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-1")

    record = repo.create_document(
        application_id="app-1",
        document_id="doc-1",
        document_type=DocumentType.GOVERNMENT_ID,
        s3_key="applications/app-1/documents/doc-1.pdf",
        content_type="application/pdf",
        original_filename="license.pdf",
        declared_size_bytes=1024,
    )

    assert record.status == DocumentStatus.REQUESTED
    assert record.version == 1
    assert record.actual_size_bytes is None
    assert record.checksum_sha256 is None


def test_get_document_returns_none_when_missing(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-2")
    assert repo.get_document("app-2", "nonexistent-doc") is None


def test_mark_document_received_transitions_status(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-3")
    repo.create_document(
        application_id="app-3",
        document_id="doc-1",
        document_type=DocumentType.BANK_EVIDENCE,
        s3_key="applications/app-3/documents/doc-1.pdf",
        content_type="application/pdf",
        original_filename="statement.pdf",
        declared_size_bytes=2048,
    )

    updated = repo.mark_document_received(
        application_id="app-3",
        document_id="doc-1",
        checksum_sha256="a" * 64,
        actual_size_bytes=2048,
    )

    assert updated.status == DocumentStatus.RECEIVED
    assert updated.checksum_sha256 == "a" * 64
    assert updated.actual_size_bytes == 2048
    assert updated.version == 2


def test_mark_document_received_twice_is_conflict(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-4")
    repo.create_document(
        application_id="app-4",
        document_id="doc-1",
        document_type=DocumentType.BANK_EVIDENCE,
        s3_key="applications/app-4/documents/doc-1.pdf",
        content_type="application/pdf",
        original_filename="statement.pdf",
        declared_size_bytes=2048,
    )
    repo.mark_document_received("app-4", "doc-1", "a" * 64, 2048)

    try:
        repo.mark_document_received("app-4", "doc-1", "b" * 64, 2048)
        assert False, "expected ConflictError"
    except ConflictError:
        pass


def test_mark_document_received_on_missing_document_is_not_found(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-5")

    try:
        repo.mark_document_received("app-5", "nonexistent-doc", "a" * 64, 100)
        assert False, "expected NotFoundError"
    except NotFoundError:
        pass