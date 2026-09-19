"""AI evaluation models (spec section 6).

Split into three independent pieces, matching the spec's own separation:
- BusinessProfileSummary: AI-generated commentary (mock adapter output)
- StatementAnalysis: deterministic arithmetic (never AI-generated) plus
  the raw statement values it was computed from
- RiskSignal: explainable flags, each citing the field(s) that caused it

EvaluationRecord bundles all three into one storage record, singleton
per application (same pattern as BusinessRecord / ClassificationRecord).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from common.dynamo_utils import to_dynamo_safe

EVALUATION_SK = "EVALUATION"
ENTITY_TYPE_EVALUATION = "EVALUATION"


class ProcessingStatementInput(BaseModel):
    """Client-provided statement values (spec: "extract or accept current
    rates/fees" - this is the "accept" path; see StatementExtraction for
    the "extract" path when no values are supplied directly)."""

    model_config = ConfigDict(extra="forbid")

    processor: str = Field(min_length=1, max_length=200)
    statement_period: str = Field(min_length=1, max_length=50, description="e.g. '2026-08'")
    monthly_volume_cents: int = Field(gt=0)
    monthly_transaction_count: int = Field(gt=0)
    discount_rate_percent: float = Field(ge=0, le=100)
    per_transaction_fee_cents: int = Field(ge=0)
    monthly_fee_cents: int = Field(ge=0)


class StatementExtraction(BaseModel):
    """Either the client-provided values (source=client_provided) or a
    mock/AI-extracted placeholder (source=ai_mock_extraction)."""

    model_config = ConfigDict(extra="forbid")

    processor: str | None = None
    statement_period: str | None = None
    monthly_volume_cents: int | None = None
    monthly_transaction_count: int | None = None
    discount_rate_percent: float | None = None
    per_transaction_fee_cents: int | None = None
    monthly_fee_cents: int | None = None
    source: Literal["client_provided", "ai_mock_extraction"]
    extraction_confidence: float = Field(ge=0, le=1)


class StatementAnalysis(BaseModel):
    """Deterministic arithmetic ONLY - never touched by the AI adapter.
    effective_rate_percent is computed in evaluation_service.py with plain
    arithmetic so an engineer can verify it by hand from the inputs below.
    """

    model_config = ConfigDict(extra="forbid")

    extraction: StatementExtraction
    effective_rate_percent: float | None = Field(
        default=None,
        description="(monthly_fee + txn_count*per_txn_fee + volume*discount_rate/100) / volume * 100",
    )
    calculation_note: str


class BusinessProfileSummary(BaseModel):
    """AI-generated commentary. Explicitly separate from StatementAnalysis
    per spec: "Separate deterministic arithmetic from AI-generated
    commentary." ai_available=False means the adapter failed/timed out and
    this is a safe fallback, not a real result."""

    model_config = ConfigDict(extra="forbid")

    sales_channel: str
    fulfillment_model: str
    recurring_behavior: str
    customer_type: str
    summary_text: str
    ai_confidence: float = Field(ge=0, le=1)
    ai_available: bool


class RiskSignal(BaseModel):
    """One explainable flag. source_fields makes every warning traceable
    to the input that caused it (spec: "every warning should cite which
    input field/document caused it")."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    source_fields: list[str]
    severity: Literal["INFO", "WARNING"]


class EvaluateRequest(BaseModel):
    """Body for POST /applications/{id}/evaluate."""

    model_config = ConfigDict(extra="forbid")

    processing_statement: ProcessingStatementInput | None = None


def evaluation_pk(application_id: str) -> str:
    return f"APP#{application_id}"


class EvaluationRecord(BaseModel):
    """On-disk EVALUATION item. PK = APP#{applicationId}, SK = EVALUATION."""

    model_config = ConfigDict(populate_by_name=True)

    pk: str = Field(alias="PK")
    sk: str = Field(default=EVALUATION_SK, alias="SK")
    entity_type: str = ENTITY_TYPE_EVALUATION
    application_id: str
    version: int = 1
    created_at: str
    updated_at: str

    business_profile: BusinessProfileSummary
    statement_analysis: StatementAnalysis | None = None
    risk_signals: list[RiskSignal] = Field(default_factory=list)

    def to_item(self) -> dict:
        item = {
            "PK": self.pk,
            "SK": self.sk,
            "entity_type": self.entity_type,
            "application_id": self.application_id,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "business_profile": self.business_profile.model_dump(),
            "risk_signals": [s.model_dump() for s in self.risk_signals],
            **(
                {"statement_analysis": self.statement_analysis.model_dump()}
                if self.statement_analysis
                else {}
            ),
        }
        return to_dynamo_safe(item)

    @classmethod
    def from_item(cls, item: dict) -> "EvaluationRecord":
        return cls(
            PK=item["PK"],
            SK=item["SK"],
            entity_type=item.get("entity_type", ENTITY_TYPE_EVALUATION),
            application_id=item["application_id"],
            version=int(item["version"]),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            business_profile=BusinessProfileSummary(**_decimal_to_float(item["business_profile"])),
            statement_analysis=(
                StatementAnalysis(**_decimal_to_float(item["statement_analysis"]))
                if "statement_analysis" in item
                else None
            ),
            risk_signals=[RiskSignal(**s) for s in item.get("risk_signals", [])],
        )

    def to_public_dict(self) -> dict:
        return {
            "applicationId": self.application_id,
            "version": self.version,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "businessProfile": self.business_profile.model_dump(),
            "statementAnalysis": (
                self.statement_analysis.model_dump() if self.statement_analysis else None
            ),
            "riskSignals": [s.model_dump() for s in self.risk_signals],
        }


def _decimal_to_float(value):
    """Reverse of to_dynamo_safe for re-hydrating a stored item back into
    Pydantic models that expect plain float."""
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _decimal_to_float(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimal_to_float(v) for v in value]
    return value