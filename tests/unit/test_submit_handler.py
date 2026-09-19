import json
from types import SimpleNamespace

from common.errors import ConflictError
from handlers.submit import handler
from models.document import DocumentStatus, DocumentType


def _public(payload):
    return SimpleNamespace(to_public_dict=lambda: payload)


class FakeRepo:
    def __init__(self, *, complete=True, metadata="default", submit_error=None):
        self.metadata = (
            SimpleNamespace(
                application_id="app-1",
                status=SimpleNamespace(value="IN_PROGRESS"),
                created_at="2026-09-01T00:00:00Z",
            )
            if metadata == "default"
            else metadata
        )
        self.complete = complete
        self.submit_error = submit_error
        self.submit_calls = []

    def get_metadata(self, application_id):
        return self.metadata

    def get_business(self, application_id):
        if not self.complete:
            return None
        biz = _public({"legalName": "Acme"})
        biz.legal_name = "Acme LLC"
        biz.entity_structure = "LLC"
        biz.tax_id_last_four = "1234"
        biz.physical_address = {"line1": "1 Main St"}
        biz.business_description = "Sells coffee"
        return biz

    def list_persons(self, application_id):
        if not self.complete:
            return []
        person = _public({"name": "A"})
        person.is_control_person = True
        return [person]

    def list_documents(self, application_id):
        if not self.complete:
            return []
        docs = []
        for doc_type in (
            DocumentType.GOVERNMENT_ID,
            DocumentType.BUSINESS_REGISTRATION,
            DocumentType.BANK_EVIDENCE,
        ):
            d = _public({"documentType": doc_type.value})
            d.document_type = doc_type
            d.status = DocumentStatus.RECEIVED
            docs.append(d)
        return docs

    def get_classification(self, application_id):
        if not self.complete:
            return None
        c = _public({"confirmedMcc": "5814"})
        c.confirmed_mcc = "5814"
        return c

    def get_evaluation(self, application_id):
        return _public({"riskSignals": []}) if self.complete else None

    def submit_application(self, application_id, snapshot):
        self.submit_calls.append((application_id, snapshot))
        if self.submit_error:
            raise self.submit_error
        updated = SimpleNamespace(status=SimpleNamespace(value="SUBMITTED"))
        submission = SimpleNamespace(
            submitted_at="2026-09-19T00:00:00Z", snapshot=snapshot
        )
        return updated, submission


CONTEXT = SimpleNamespace(aws_request_id="req-1")


def _event(application_id="app-1"):
    return {"pathParameters": {"id": application_id}}


def test_submit_success_locks_and_returns_snapshot():
    repo = FakeRepo()
    resp = handler(_event(), CONTEXT, repo=repo)
    body = json.loads(resp["body"])

    assert resp["statusCode"] == 200
    assert body["applicationId"] == "app-1"
    assert body["status"] == "SUBMITTED"
    assert body["submittedAt"] == "2026-09-19T00:00:00Z"
    assert body["snapshot"]["business"] == {"legalName": "Acme"}
    assert len(repo.submit_calls) == 1


def test_incomplete_application_returns_400_and_does_not_lock():
    repo = FakeRepo(complete=False)
    resp = handler(_event(), CONTEXT, repo=repo)
    body = json.loads(resp["body"])

    assert resp["statusCode"] == 400
    assert body["error"]["code"] == "SUBMISSION_INCOMPLETE"
    assert len(body["missingItems"]) == 6
    assert repo.submit_calls == []


def test_unknown_application_returns_404():
    repo = FakeRepo(metadata=None)
    resp = handler(_event(), CONTEXT, repo=repo)

    assert resp["statusCode"] == 404
    assert repo.submit_calls == []


def test_double_submit_returns_409():
    repo = FakeRepo(submit_error=ConflictError("already submitted"))
    resp = handler(_event(), CONTEXT, repo=repo)

    assert resp["statusCode"] == 409


def test_missing_path_parameter_returns_400():
    repo = FakeRepo()
    resp = handler({"pathParameters": None}, CONTEXT, repo=repo)

    assert resp["statusCode"] == 400
    assert repo.submit_calls == []