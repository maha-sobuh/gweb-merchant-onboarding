from types import SimpleNamespace

import pytest

from models.document import DocumentStatus, DocumentType
from services.submission_service import (
    REQUIRED_DOCUMENT_TYPES,
    build_submission_snapshot,
    validate_for_submission,
)


def _business(**overrides):
    data = dict(
        legal_name="Acme LLC",
        entity_structure="LLC",
        tax_id_last_four="1234",
        physical_address={"line1": "1 Main St"},
        business_description="Sells coffee",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _person(is_control_person=True):
    return SimpleNamespace(is_control_person=is_control_person)


def _doc(doc_type, status=DocumentStatus.RECEIVED):
    return SimpleNamespace(document_type=doc_type, status=status)


def _all_docs():
    return [_doc(t) for t in REQUIRED_DOCUMENT_TYPES]


def _classification(confirmed="5814"):
    return SimpleNamespace(confirmed_mcc=confirmed)


def test_complete_application_has_no_missing_items():
    missing = validate_for_submission(
        _business(), [_person()], _all_docs(), _classification()
    )
    assert missing == []


def test_missing_business_reports_single_actionable_item():
    missing = validate_for_submission(
        None, [_person()], _all_docs(), _classification()
    )
    assert len(missing) == 1
    assert "business information" in missing[0]


@pytest.mark.parametrize(
    "field, expected",
    [
        ("legal_name", "business.legal_name"),
        ("entity_structure", "business.entity_structure"),
        ("tax_id_last_four", "business.tax_id"),
        ("physical_address", "business.physical_address"),
        ("business_description", "business.business_description"),
    ],
)
def test_each_missing_business_field_is_reported(field, expected):
    missing = validate_for_submission(
        _business(**{field: None}), [_person()], _all_docs(), _classification()
    )
    assert len(missing) == 1
    assert expected in missing[0]


@pytest.mark.parametrize(
    "persons",
    [[], [_person(is_control_person=False)]],
    ids=["no-persons", "no-control-person"],
)
def test_control_person_required(persons):
    missing = validate_for_submission(
        _business(), persons, _all_docs(), _classification()
    )
    assert len(missing) == 1
    assert "control person" in missing[0]


@pytest.mark.parametrize(
    "classification",
    [None, _classification(confirmed=None), _classification(confirmed="")],
    ids=["none", "unconfirmed", "empty"],
)
def test_confirmed_mcc_required(classification):
    missing = validate_for_submission(
        _business(), [_person()], _all_docs(), classification
    )
    assert len(missing) == 1
    assert "confirmed MCC" in missing[0]


@pytest.mark.parametrize("required", list(REQUIRED_DOCUMENT_TYPES))
def test_each_required_document_type_is_enforced(required):
    docs = [_doc(t) for t in REQUIRED_DOCUMENT_TYPES if t != required]
    missing = validate_for_submission(
        _business(), [_person()], docs, _classification()
    )
    assert len(missing) == 1
    assert required.value in missing[0]


def test_document_not_yet_received_does_not_count():
    docs = [
        _doc(t, status=DocumentStatus.REQUESTED if t == DocumentType.GOVERNMENT_ID
             else DocumentStatus.RECEIVED)
        for t in REQUIRED_DOCUMENT_TYPES
    ]
    missing = validate_for_submission(
        _business(), [_person()], docs, _classification()
    )
    assert len(missing) == 1
    assert DocumentType.GOVERNMENT_ID.value in missing[0]


def test_all_gaps_are_reported_together():
    missing = validate_for_submission(None, [], [], None)
    # business + control person + MCC + 3 required documents
    assert len(missing) == 6


def _public(payload):
    return SimpleNamespace(to_public_dict=lambda: payload)


def test_snapshot_bundles_all_entities():
    metadata = SimpleNamespace(
        application_id="app-1",
        status=SimpleNamespace(value="IN_PROGRESS"),
        created_at="2026-09-01T00:00:00Z",
    )
    snapshot = build_submission_snapshot(
        metadata,
        _public({"legalName": "Acme"}),
        [_public({"name": "A"}), _public({"name": "B"})],
        [_public({"documentType": "GOVERNMENT_ID"})],
        _public({"confirmedMcc": "5814"}),
        _public({"riskSignals": []}),
    )
    assert snapshot["application"] == {
        "applicationId": "app-1",
        "status": "IN_PROGRESS",
        "createdAt": "2026-09-01T00:00:00Z",
    }
    assert snapshot["business"] == {"legalName": "Acme"}
    assert [p["name"] for p in snapshot["persons"]] == ["A", "B"]
    assert snapshot["documents"] == [{"documentType": "GOVERNMENT_ID"}]
    assert snapshot["classification"] == {"confirmedMcc": "5814"}
    assert snapshot["evaluation"] == {"riskSignals": []}


def test_snapshot_allows_missing_optional_entities():
    metadata = SimpleNamespace(
        application_id="app-1",
        status=SimpleNamespace(value="IN_PROGRESS"),
        created_at="t",
    )
    snapshot = build_submission_snapshot(metadata, None, [], [], None, None)
    assert snapshot["business"] is None
    assert snapshot["classification"] is None
    assert snapshot["evaluation"] is None
    assert snapshot["persons"] == []
    assert snapshot["documents"] == []