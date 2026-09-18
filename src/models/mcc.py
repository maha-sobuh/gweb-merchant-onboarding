"""MCC classification models (spec section 5).

Deliberately split from risk policy: MccEntry/ProposedMcc describe WHAT a
business is (the taxonomy); RiskTier/risk reasoning describe WHAT GWEB does
about it (policy). See src/data/risk_policy.json â€” risk rules can change
without touching the MCC catalog, per spec آ§5.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

CLASSIFICATION_SK = "MCC_CLASSIFICATION"
ENTITY_TYPE_CLASSIFICATION = "MCC_CLASSIFICATION"


class RiskTier(str, Enum):
    STANDARD = "STANDARD"
    ENHANCED_REVIEW = "ENHANCED_REVIEW"
    RESTRICTED = "RESTRICTED"


class MccEntry(BaseModel):
    """One row of the static MCC catalog (src/data/mcc_catalog.json)."""

    model_config = ConfigDict(extra="forbid")

    code: str
    description: str
    keywords: list[str] = Field(default_factory=list)


class ProposedMcc(BaseModel):
    """One system-suggested MCC with its confidence and risk tier."""

    model_config = ConfigDict(extra="forbid")

    code: str
    description: str
    confidence: float = Field(ge=0, le=1)
    reason: str
    risk_tier: RiskTier
    risk_reason: str | None = None


class ClassifyRequest(BaseModel):
    """Body for POST /applications/{id}/classify.

    Two uses of the same endpoint (spec آ§5 user journey: applicant
    self-selects, system proposes, applicant confirms or corrects):
    - confirmed_mcc omitted -> "give me suggestions" (also persisted as
      the latest proposal, for audit â€” spec: "Persist both
      applicant-selected activity and system-proposed MCC").
    - confirmed_mcc provided -> "the applicant is confirming/correcting
      to this code" (must be a real catalog code; ambiguous/sensitive
      tiers are still flagged for manual review regardless of confirmation
      â€” spec: "Never auto-approve a merchant solely because an AI model
      labels it low risk").
    """

    model_config = ConfigDict(extra="forbid")

    selected_industry: str = Field(min_length=1, max_length=200)
    business_description: str = Field(min_length=1, max_length=2000)
    confirmed_mcc: str | None = Field(default=None, min_length=4, max_length=4)
    provider: str | None = Field(
        default=None, description="Optional acquirer/processor name for policy overrides"
    )


def classification_pk(application_id: str) -> str:
    return f"APP#{application_id}"


class ClassificationRecord(BaseModel):
    """On-disk MCC_CLASSIFICATION item.

    PK = APP#{applicationId}, SK = MCC_CLASSIFICATION (singleton per
    application, same pattern as BusinessRecord).
    """

    model_config = ConfigDict(populate_by_name=True)

    pk: str = Field(alias="PK")
    sk: str = Field(default=CLASSIFICATION_SK, alias="SK")
    entity_type: str = ENTITY_TYPE_CLASSIFICATION
    application_id: str
    version: int = 1
    created_at: str
    updated_at: str

    selected_industry: str
    business_description: str
    proposed_mccs: list[ProposedMcc] = Field(default_factory=list)
    confirmed_mcc: str | None = None
    manual_review_required: bool = False

    def to_item(self) -> dict:
        return {
            "PK": self.pk,
            "SK": self.sk,
            "entity_type": self.entity_type,
            "application_id": self.application_id,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "selected_industry": self.selected_industry,
            "business_description": self.business_description,
            "proposed_mccs": [
                {**p.model_dump(), "confidence": Decimal(str(p.confidence))}
                for p in self.proposed_mccs
            ],
            "manual_review_required": self.manual_review_required,
            **({"confirmed_mcc": self.confirmed_mcc} if self.confirmed_mcc else {}),
        }

    @classmethod
    def from_item(cls, item: dict) -> "ClassificationRecord":
        return cls(
            PK=item["PK"],
            SK=item["SK"],
            entity_type=item.get("entity_type", ENTITY_TYPE_CLASSIFICATION),
            application_id=item["application_id"],
            version=int(item["version"]),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            selected_industry=item["selected_industry"],
            business_description=item["business_description"],
            proposed_mccs=[ProposedMcc(**p) for p in item.get("proposed_mccs", [])],
            confirmed_mcc=item.get("confirmed_mcc"),
            manual_review_required=item.get("manual_review_required", False),
        )

    def to_public_dict(self) -> dict:
        return {
            "applicationId": self.application_id,
            "version": self.version,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "selectedIndustry": self.selected_industry,
            "businessDescription": self.business_description,
            "proposedMccs": [
                {
                    "code": p.code,
                    "description": p.description,
                    "confidence": p.confidence,
                    "reason": p.reason,
                    "riskTier": p.risk_tier.value,
                    "riskReason": p.risk_reason,
                }
                for p in self.proposed_mccs
            ],
            "confirmedMcc": self.confirmed_mcc,
            "manualReviewRequired": self.manual_review_required,
        }
        