"""GET /mcc?query=... - search the static MCC catalog.

No DynamoDB access at all (read-only reference data). No Application
lookup needed either - this endpoint is independent of any application.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from common.errors import DeadlineGuard
from common.logging_config import configure_logging
from common.responses import error_response, json_response
from services import mcc_service

logger = logging.getLogger("merchant_onboarding.handlers.mcc_search")


def handler(event: dict, context: Any) -> dict:
    correlation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    configure_logging(correlation_id)

    try:
        with DeadlineGuard() as deadline:
            deadline.check()
            return _search(event)
    except Exception as exc:  # noqa: BLE001
        logger.exception("mcc search failed")
        return error_response(exc)


def _search(event: dict) -> dict:
    query_params = event.get("queryStringParameters") or {}
    query = (query_params.get("query") or "").strip()

    results = mcc_service.search(query)
    logger.info("mcc search completed", extra={"query": query, "result_count": len(results)})

    body = {
        "query": query,
        "results": [
            {"code": r.code, "description": r.description, "keywords": r.keywords} for r in results
        ],
    }
    return json_response(200, body)