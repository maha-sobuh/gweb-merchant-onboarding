"""API Gateway proxy response helpers.

Every endpoint returns the same envelope: statusCode, JSON headers, JSON body.
"""

from __future__ import annotations

import json
from typing import Any

from common.errors import AppError, error_to_response_parts

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    # CORS is open because auth is not in Phase 1 (see README known gaps).
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Idempotency-Key",
}


def json_response(
    status_code: int,
    body: dict[str, Any] | list[Any],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    merged = {**DEFAULT_HEADERS, **(headers or {})}
    return {
        "statusCode": status_code,
        "headers": merged,
        "body": json.dumps(body, default=str),
    }


def error_response(exc: BaseException) -> dict[str, Any]:
    status_code, body = error_to_response_parts(exc)
    return json_response(status_code, body)


def app_error_response(error: AppError) -> dict[str, Any]:
    return json_response(error.status_code, error.to_body())
