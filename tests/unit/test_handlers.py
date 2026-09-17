from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from common.errors import ConflictError, NotFoundError
from handlers import applications_create, applications_get
from models.application import ApplicationMetadata, application_pk, utc_now_iso


def _context():
    return SimpleNamespace(aws_request_id="corr-123")


def _meta(app_id: str = "11111111-1111-1111-1111-111111111111") -> ApplicationMetadata:
    now = utc_now_iso()
    return ApplicationMetadata(
        PK=application_pk(app_id),
        application_id=app_id,
        created_at=now,
        updated_at=now,
    )


def test_create_returns_201_and_initial_state():
    repo = MagicMock()
    repo.create_application.return_value = (_meta(), True)

    result = applications_create.handler(
        {"headers": {}, "body": "{}"},
        _context(),
        repo=repo,
    )

    assert result["statusCode"] == 201
    body = json.loads(result["body"])
    assert body["status"] == "IN_PROGRESS"
    assert body["version"] == 1
    assert "applicationId" in body
    repo.create_application.assert_called_once()


def test_create_rejects_unknown_fields_with_400():
    repo = MagicMock()
    result = applications_create.handler(
        {"headers": {}, "body": json.dumps({"legalName": "Acme"})},
        _context(),
        repo=repo,
    )
    assert result["statusCode"] == 400
    body = json.loads(result["body"])
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "error" in body and "message" in body["error"]
    repo.create_application.assert_not_called()


def test_create_invalid_json_is_400():
    result = applications_create.handler(
        {"headers": {}, "body": "{not-json"},
        _context(),
        repo=MagicMock(),
    )
    assert result["statusCode"] == 400
    assert json.loads(result["body"])["error"]["code"] == "VALIDATION_ERROR"


def test_create_conflict_is_409():
    repo = MagicMock()
    repo.create_application.side_effect = ConflictError("An application with this id already exists")
    result = applications_create.handler({"headers": {}, "body": "{}"}, _context(), repo=repo)
    assert result["statusCode"] == 409
    assert json.loads(result["body"])["error"]["code"] == "CONFLICT"


def test_create_unexpected_error_is_500_without_internals():
    repo = MagicMock()
    repo.create_application.side_effect = RuntimeError("secret stack boom 123-45-6789")
    result = applications_create.handler({"headers": {}, "body": "{}"}, _context(), repo=repo)
    assert result["statusCode"] == 500
    body = json.loads(result["body"])
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "boom" not in result["body"]
    assert "123-45-6789" not in result["body"]


def test_create_passes_idempotency_key_header():
    repo = MagicMock()
    repo.create_application.return_value = (_meta(), True)
    applications_create.handler(
        {"headers": {"Idempotency-Key": "abc"}, "body": None},
        _context(),
        repo=repo,
    )
    kwargs = repo.create_application.call_args.kwargs
    assert kwargs["idempotency_key"] == "abc"


def test_get_returns_200():
    repo = MagicMock()
    meta = _meta()
    repo.get_metadata.return_value = meta
    result = applications_get.handler(
        {"pathParameters": {"id": meta.application_id}},
        _context(),
        repo=repo,
    )
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["applicationId"] == meta.application_id
    assert body["status"] == "IN_PROGRESS"


def test_get_not_found_is_404():
    repo = MagicMock()
    repo.get_metadata.return_value = None
    result = applications_get.handler(
        {"pathParameters": {"id": "11111111-1111-1111-1111-111111111111"}},
        _context(),
        repo=repo,
    )
    assert result["statusCode"] == 404
    body = json.loads(result["body"])
    assert body["error"]["code"] == "NOT_FOUND"


def test_get_missing_id_is_400():
    result = applications_get.handler({"pathParameters": {}}, _context(), repo=MagicMock())
    assert result["statusCode"] == 400
