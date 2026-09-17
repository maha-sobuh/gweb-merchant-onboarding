"""Unit tests for ApplicationRepository.get_person / upsert_person (Phase 2)."""

from __future__ import annotations

from common.errors import ConflictError, NotFoundError
from models.applicant import Applicant
from repositories.application_repo import ApplicationRepository


def _repo() -> ApplicationRepository:
    return ApplicationRepository()


def test_upsert_person_creates_new_record(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-1")

    data = Applicant(first_name="Jane", last_name="Doe", email="jane@example.com")
    record, created = repo.upsert_person(
        application_id="app-1", data=data, person_id="person-1", expected_version=None
    )

    assert created is True
    assert record.version == 1
    assert record.first_name == "Jane"
    assert record.person_id == "person-1"


def test_upsert_person_masks_raw_identifiers(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-2")

    data = Applicant(
        first_name="Jane",
        tax_id="123-45-6789",
        government_id_number="X99887766",
    )
    record, _ = repo.upsert_person(
        application_id="app-2", data=data, person_id="person-1", expected_version=None
    )

    # Only last four survive into the stored record.
    assert record.tax_id_last_four == "6789"
    assert record.government_id_last_four == "7766"
    # Raw values must never appear anywhere on the stored item.
    item = record.to_item()
    assert "tax_id" not in item
    assert "government_id_number" not in item
    assert "123-45-6789" not in str(item)
    assert "X99887766" not in str(item)


def test_create_duplicate_person_id_without_version_is_conflict(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-3")
    data = Applicant(first_name="Jane")
    repo.upsert_person(application_id="app-3", data=data, person_id="person-1", expected_version=None)

    try:
        repo.upsert_person(
            application_id="app-3", data=data, person_id="person-1", expected_version=None
        )
        assert False, "expected ConflictError"
    except ConflictError:
        pass


def test_update_with_correct_version_increments_version(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-4")
    data = Applicant(first_name="Jane")
    created_record, _ = repo.upsert_person(
        application_id="app-4", data=data, person_id="person-1", expected_version=None
    )
    assert created_record.version == 1

    updated_data = Applicant(first_name="Janet")
    updated_record, created = repo.upsert_person(
        application_id="app-4",
        data=updated_data,
        person_id="person-1",
        expected_version=1,
    )

    assert created is False
    assert updated_record.version == 2
    assert updated_record.first_name == "Janet"
    # created_at must be preserved across updates.
    assert updated_record.created_at == created_record.created_at


def test_update_with_stale_version_is_conflict(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-5")
    data = Applicant(first_name="Jane")
    repo.upsert_person(application_id="app-5", data=data, person_id="person-1", expected_version=None)

    try:
        repo.upsert_person(
            application_id="app-5",
            data=Applicant(first_name="Janet"),
            person_id="person-1",
            expected_version=99,
        )
        assert False, "expected ConflictError"
    except ConflictError:
        pass


def test_update_nonexistent_person_is_not_found(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-6")

    try:
        repo.upsert_person(
            application_id="app-6",
            data=Applicant(first_name="Jane"),
            person_id="does-not-exist",
            expected_version=1,
        )
        assert False, "expected NotFoundError"
    except NotFoundError:
        pass


def test_get_person_returns_none_when_missing(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-7")
    assert repo.get_person("app-7", "nobody") is None