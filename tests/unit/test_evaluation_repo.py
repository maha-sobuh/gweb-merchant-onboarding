"""Unit tests for the Phase 4b repository additions: list_persons,
list_documents, get_evaluation, upsert_evaluation."""

from __future__ import annotations

from models.applicant import Applicant
from models.document import DocumentType
from models.evaluation import BusinessProfileSummary, EvaluationRecord, evaluation_pk
from repositories.application_repo import ApplicationRepository


def _repo() -> ApplicationRepository:
    return ApplicationRepository()


def test_list_persons_returns_all_people(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-1")
    repo.upsert_person(
        application_id="app-1", data=Applicant(first_name="A"), person_id="p1", expected_version=None
    )
    repo.upsert_person(
        application_id="app-1", data=Applicant(first_name="B"), person_id="p2", expected_version=None
    )

    persons = repo.list_persons("app-1")
    assert len(persons) == 2
    assert {p.first_name for p in persons} == {"A", "B"}


def test_list_persons_empty_when_none(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-2")
    assert repo.list_persons("app-2") == []


def test_list_documents_returns_all_documents(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-3")
    repo.create_document(
        application_id="app-3",
        document_id="doc-1",
        document_type=DocumentType.GOVERNMENT_ID,
        s3_key="k1",
        content_type="application/pdf",
        original_filename="a.pdf",
        declared_size_bytes=100,
    )
    repo.create_document(
        application_id="app-3",
        document_id="doc-2",
        document_type=DocumentType.BANK_EVIDENCE,
        s3_key="k2",
        content_type="application/pdf",
        original_filename="b.pdf",
        declared_size_bytes=200,
    )

    docs = repo.list_documents("app-3")
    assert len(docs) == 2


def test_list_documents_empty_when_none(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-4")
    assert repo.list_documents("app-4") == []


def test_get_evaluation_returns_none_when_missing(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-5")
    assert repo.get_evaluation("app-5") is None


def _fallback_profile() -> BusinessProfileSummary:
    return BusinessProfileSummary(
        sales_channel="unknown",
        fulfillment_model="unknown",
        recurring_behavior="unknown",
        customer_type="unknown",
        summary_text="x",
        ai_confidence=0.0,
        ai_available=False,
    )


def test_upsert_evaluation_creates_then_increments_version(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-6")

    record1 = EvaluationRecord(
        PK=evaluation_pk("app-6"),
        application_id="app-6",
        created_at="",
        updated_at="",
        business_profile=_fallback_profile(),
    )
    saved1 = repo.upsert_evaluation(record1)
    assert saved1.version == 1

    record2 = EvaluationRecord(
        PK=evaluation_pk("app-6"),
        application_id="app-6",
        created_at="",
        updated_at="",
        business_profile=_fallback_profile(),
    )
    saved2 = repo.upsert_evaluation(record2)
    assert saved2.version == 2
    assert saved2.created_at == saved1.created_at  # created_at preserved across re-evaluations