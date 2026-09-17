"""Business legal-entity models (spec section 3.2).

Defined in Phase 1 for later KYB / MCC / underwriting phases.
POST /applications does not accept these yet.

legal_name, tax_id, addresses, and contact fields are PII/sensitive —
never log model_dump().
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from models.applicant import Address


class EntityStructure(str, Enum):
    SOLE_PROP = "SOLE_PROP"
    LLC = "LLC"
    PARTNERSHIP = "PARTNERSHIP"
    PRIVATE_CORP = "PRIVATE_CORP"
    PUBLIC_CORP = "PUBLIC_CORP"
    NON_PROFIT = "NON_PROFIT"
    GOVERNMENT = "GOVERNMENT"
    OTHER = "OTHER"


class BusinessTaxIdType(str, Enum):
    EIN = "EIN"
    SSN = "SSN"
    OTHER = "OTHER"


class Business(BaseModel):
    """Merchant legal entity (spec §3.2).

    MCC / NAICS are declared so the classification phase can fill them
    without a model change. They are unused in Phase 1.
    """

    model_config = ConfigDict(extra="forbid")

    legal_name: str | None = None
    dba_name: str | None = Field(default=None, description="Doing-business-as / trade name")
    entity_structure: EntityStructure | None = None
    tax_id: str | None = Field(default=None, description="EIN/TIN — sensitive, never log")
    tax_id_type: BusinessTaxIdType | None = None
    formation_date: date | None = None
    formation_state: str | None = None
    formation_country: str | None = Field(
        default=None, description="ISO 3166-1 alpha-2"
    )
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    industry_description: str | None = None
    naics_code: str | None = None
    mcc: str | None = Field(
        default=None,
        description="Merchant Category Code — populated in a later phase",
    )
    physical_address: Address | None = None
    mailing_address: Address | None = None
    business_description: str | None = None
    years_in_business: int | None = Field(default=None, ge=0)
    annual_processing_volume: int | None = Field(
        default=None, ge=0, description="Expected annual card volume in minor units"
    )
    average_transaction_amount: int | None = Field(
        default=None, ge=0, description="Average ticket in minor units"
    )
