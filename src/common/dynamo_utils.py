"""Shared helpers for preparing Python values for DynamoDB storage.

DynamoDB's boto3 Table resource rejects native float (this bit us twice
already: ownership_percentage in Phase 2, confidence in Phase 4a - both
fixed with a one-off Decimal conversion at the call site). This recursive
helper is introduced in Phase 4b so every NEW model converts floats
correctly by construction instead of requiring another manual fix.
Existing models keep their inline fixes rather than being refactored here,
to avoid touching already-tested code.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def to_dynamo_safe(value: Any) -> Any:
    """Recursively convert every float in a dict/list structure to Decimal.
    Leaves every other type untouched. Safe to call on an already-clean
    structure (idempotent)."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_dynamo_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_dynamo_safe(v) for v in value]
    return value