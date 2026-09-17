"""Unit tests for ApplicationRepository.get_business / upsert_business (Phase 2)."""

from __future__ import annotations

from common.errors import ConflictError, NotFoundError
from models.business import Business
from repositories.application_repo import ApplicationRepository


def _repo() -> ApplicationRepository:
    return ApplicationRepository()


def test_upsert_business_creates_new_record(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-1")

    data = Business(legal_name="Acme LLC", website="https://acme.example.com")
    record, created = repo.upsert_business(application_id="app-1", data=data, expected_version=None)

    assert created is True
    assert record.version == 1
    assert record.legal_name == "Acme LLC"
    assert record.application_id == "app-1"


def test_upsert_business_masks_raw_tax_id(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-2")

    data = Business(legal_name="Acme LLC", tax_id="12-3456789")
    record, _ = repo.upsert_business(application_id="app-2", data=data, expected_version=None)

    assert record.tax_id_last_four == "6789"
    item = record.to_item()
    assert "tax_id" not in item
    assert "12-3456789" not in str(item)


def test_create_duplicate_business_without_version_is_conflict(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-3")
    data = Business(legal_name="Acme LLC")
    repo.upsert_business(application_id="app-3", data=data, expected_version=None)

    try:
        repo.upsert_business(application_id="app-3", data=data, expected_version=None)
        assert False, "expected ConflictError"
    except ConflictError:
        pass


def test_update_with_correct_version_increments_version(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-4")
    data = Business(legal_name="Acme LLC")
    created_record, _ = repo.upsert_business(application_id="app-4", data=data, expected_version=None)
    assert created_record.version == 1

    updated_data = Business(legal_name="Acme Holdings LLC")
    updated_record, created = repo.upsert_business(
        application_id="app-4", data=updated_data, expected_version=1
    )

    assert created is False
    assert updated_record.version == 2
    assert updated_record.legal_name == "Acme Holdings LLC"
    assert updated_record.created_at == created_record.created_at


def test_update_with_stale_version_is_conflict(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-5")
    data = Business(legal_name="Acme LLC")
    repo.upsert_business(application_id="app-5", data=data, expected_version=None)

    try:
        repo.upsert_business(
            application_id="app-5",
            data=Business(legal_name="Acme Holdings LLC"),
            expected_version=99,
        )
        assert False, "expected ConflictError"
    except ConflictError:
        pass


def test_update_before_any_create_is_not_found(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-6")

    try:
        repo.upsert_business(
            application_id="app-6", data=Business(legal_name="Acme LLC"), expected_version=1
        )
        assert False, "expected NotFoundError"
    except NotFoundError:
        pass


def test_get_business_returns_none_when_missing(dynamodb_table):
    repo = _repo()
    repo.create_application(application_id="app-7")
    assert repo.get_business("app-7") is None