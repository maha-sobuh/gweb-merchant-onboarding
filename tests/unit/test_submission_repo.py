from __future__ import annotations

from decimal import Decimal

import pytest

from common.errors import ConflictError
from models.application import METADATA_SK, application_pk
from models.submission import SUBMISSION_SK, SubmissionRecord, submission_pk
from repositories.application_repo import ApplicationRepository

APP_ID = "33333333-3333-3333-3333-333333333333"


def _snapshot(**overrides) -> dict:
    data = {
        "application": {"applicationId": APP_ID, "status": "IN_PROGRESS"},
        "business": {"legalName": "Acme LLC"},
        "persons": [{"name": "A"}],
        "documents": [],
        "classification": {"confirmedMcc": "5814"},
        "evaluation": {"riskSignals": []},
    }
    data.update(overrides)
    return data


def test_submit_moves_status_to_submitted_and_bumps_version(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(APP_ID)

    updated, submission = repo.submit_application(APP_ID, _snapshot())

    assert updated.status.value == "SUBMITTED"
    assert updated.version == 2
    assert submission.submitted_at == updated.updated_at

    item = dynamodb_table.get_item(
        Key={"PK": application_pk(APP_ID), "SK": METADATA_SK}
    )["Item"]
    assert item["status"] == "SUBMITTED"
    assert item["version"] == 2


def test_submit_persists_submission_item(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(APP_ID)

    _, submission = repo.submit_application(APP_ID, _snapshot())

    item = dynamodb_table.get_item(
        Key={"PK": submission_pk(APP_ID), "SK": SUBMISSION_SK}
    )["Item"]
    assert item["entity_type"] == "SUBMISSION"
    assert item["application_id"] == APP_ID
    assert item["submitted_at"] == submission.submitted_at
    assert item["snapshot"]["business"] == {"legalName": "Acme LLC"}

    stored = SubmissionRecord.from_item(item)
    assert stored.to_public_dict()["applicationId"] == APP_ID


def test_second_submit_raises_conflict_and_keeps_first_snapshot(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(APP_ID)
    repo.submit_application(APP_ID, _snapshot())

    with pytest.raises(ConflictError):
        repo.submit_application(
            APP_ID, _snapshot(business={"legalName": "CHANGED"})
        )

    item = dynamodb_table.get_item(
        Key={"PK": submission_pk(APP_ID), "SK": SUBMISSION_SK}
    )["Item"]
    assert item["snapshot"]["business"] == {"legalName": "Acme LLC"}

    meta = repo.get_metadata(APP_ID)
    assert meta.version == 2  # the failed attempt did not bump it again


def test_submit_unknown_application_raises_conflict_and_writes_nothing(dynamodb_table):
    repo = ApplicationRepository()

    with pytest.raises(ConflictError):
        repo.submit_application("00000000-0000-0000-0000-000000000000", _snapshot())

    assert "Item" not in dynamodb_table.get_item(
        Key={
            "PK": submission_pk("00000000-0000-0000-0000-000000000000"),
            "SK": SUBMISSION_SK,
        }
    )


def test_submit_handles_float_values_in_snapshot(dynamodb_table):
    repo = ApplicationRepository()
    repo.create_application(APP_ID)

    snapshot = _snapshot(evaluation={"effectiveRatePercent": 0.85, "riskSignals": []})
    repo.submit_application(APP_ID, snapshot)

    item = dynamodb_table.get_item(
        Key={"PK": submission_pk(APP_ID), "SK": SUBMISSION_SK}
    )["Item"]
    stored = item["snapshot"]["evaluation"]["effectiveRatePercent"]
    assert isinstance(stored, Decimal)
    assert float(stored) == pytest.approx(0.85)