from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from common.errors import ConflictError
from models.application import METADATA_SK, application_pk
from repositories.application_repo import ApplicationRepository, hash_idempotency_key


def test_create_writes_metadata_with_required_attributes(dynamodb_table):
    repo = ApplicationRepository()
    meta, created = repo.create_application("11111111-1111-1111-1111-111111111111")

    assert created is True
    assert meta.application_id == "11111111-1111-1111-1111-111111111111"
    assert meta.status.value == "IN_PROGRESS"
    assert meta.version == 1
    assert meta.entity_type == "APPLICATION_METADATA"
    assert meta.created_at
    assert meta.updated_at

    item = dynamodb_table.get_item(
        Key={"PK": application_pk(meta.application_id), "SK": METADATA_SK}
    )["Item"]
    for field in ("entity_type", "created_at", "updated_at", "version", "status"):
        assert field in item


def test_get_metadata_returns_none_when_missing(dynamodb_table):
    repo = ApplicationRepository()
    assert repo.get_metadata("00000000-0000-0000-0000-000000000000") is None


def test_duplicate_application_id_raises_conflict(dynamodb_table):
    repo = ApplicationRepository()
    app_id = "22222222-2222-2222-2222-222222222222"
    repo.create_application(app_id)
    with pytest.raises(ConflictError):
        repo.create_application(app_id)


def test_idempotency_key_replays_same_application(dynamodb_table):
    repo = ApplicationRepository()
    key = "client-retry-1"
    first, created_first = repo.create_application("aaaa1111-1111-1111-1111-111111111111", key)
    second, created_second = repo.create_application("bbbb2222-2222-2222-2222-222222222222", key)

    assert created_first is True
    assert created_second is False
    assert first.application_id == second.application_id
    assert first.application_id == "aaaa1111-1111-1111-1111-111111111111"

    # Only one METADATA item exists.
    scan = dynamodb_table.scan(
        FilterExpression="entity_type = :t",
        ExpressionAttributeValues={":t": "APPLICATION_METADATA"},
    )
    assert scan["Count"] == 1


def test_idempotency_hash_is_stable():
    assert hash_idempotency_key("abc") == hash_idempotency_key("abc")
    assert hash_idempotency_key("abc") != hash_idempotency_key("abd")


def test_conditional_write_does_not_overwrite(dynamodb_table):
    repo = ApplicationRepository()
    app_id = "cccc3333-3333-3333-3333-333333333333"
    repo.create_application(app_id)
    # Direct PutItem without condition would overwrite; repo must refuse.
    with pytest.raises(ClientError):
        dynamodb_table.put_item(
            Item={
                "PK": application_pk(app_id),
                "SK": METADATA_SK,
                "entity_type": "APPLICATION_METADATA",
                "application_id": app_id,
                "status": "IN_PROGRESS",
                "version": 99,
                "created_at": "x",
                "updated_at": "x",
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
    item = dynamodb_table.get_item(
        Key={"PK": application_pk(app_id), "SK": METADATA_SK}
    )["Item"]
    assert int(item["version"]) == 1
