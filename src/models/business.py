"""Business legal-entity models (spec section 3.2).

Two representations, same split as models/applicant.py:
- `Business` — request/response shape used by the API. May carry a raw
  tax_id (EIN) that must NEVER be persisted or logged as-is.
- `BusinessRecord` — on-disk DynamoDB item. Stores only a masked tax_id
  (last four) per spec section 10.

Unlike Applicant, a Business is a SINGLETON per application (one legal
entity per merchant application) so there is no business_id — the item
is addressed purely by PK=APP#{applicationId}, SK=BUSINESS.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from models.applicant import Address, mask_last_four

BUSINESS_SK = "BUSINESS"
ENTITY_TYPE_BUSINESS = "BUSINESS"


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
    without a model change. They are unused in Phase 1/2.

    This is the REQUEST/RESPONSE shape. Raw tax_id is accepted here but
    is masked to last-four before it ever reaches DynamoDB (see
    BusinessRecord.from_business).
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(
        default=None,
        description=(
            "Required when updating an existing business record. "
            "Omit only on the very first PATCH for this application."
        ),
    )

    legal_name: str | None = None
    dba_name: str | None = Field(default=None, description="Doing-business-as / trade name")
    entity_structure: EntityStructure | None = None
    tax_id: str | None = Field(default=None, description="EIN/TIN — sensitive, never log or store raw")
    tax_id_type: BusinessTaxIdType | None = None
    formation_date: date | None = None
    formation_state: str | None = None
    formation_country: str | None = Field(default=None, description="ISO 3166-1 alpha-2")
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    industry_description: str | None = None
    naics_code: str | None = None
    mcc: str | None = Field(
        default=None, description="Merchant Category Code — populated in a later phase"
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


def business_pk(application_id: str) -> str:
    return f"APP#{application_id}"


class BusinessRecord(BaseModel):
    """On-disk BUSINESS item.

    PK = APP#{applicationId}, SK = BUSINESS (singleton — one per application).
    Lives in the same single table as METADATA and PERSON items, so a
    future Query(PK=APP#{id}) returns the whole aggregate in one round trip.

    Only a masked tax_id is stored. The raw EIN/TIN never reaches this class.
    """

    model_config = ConfigDict(populate_by_name=True)

    pk: str = Field(alias="PK")
    sk: str = Field(default=BUSINESS_SK, alias="SK")
    entity_type: str = ENTITY_TYPE_BUSINESS
    application_id: str
    version: int = 1
    created_at: str
    updated_at: str

    legal_name: str | None = None
    dba_name: str | None = None
    entity_structure: EntityStructure | None = None
    tax_id_last_four: str | None = None
    tax_id_type: BusinessTaxIdType | None = None
    formation_date: str | None = None
    formation_state: str | None = None
    formation_country: str | None = None
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    industry_description: str | None = None
    naics_code: str | None = None
    mcc: str | None = None
    physical_address: Address | None = None
    mailing_address: Address | None = None
    business_description: str | None = None
    years_in_business: int | None = None
    annual_processing_volume: int | None = None
    average_transaction_amount: int | None = None

    def to_item(self) -> dict:
        item: dict = {
            "PK": self.pk,
            "SK": self.sk,
            "entity_type": self.entity_type,
            "application_id": self.application_id,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        skip = set(item.keys())
        optional = self.model_dump(exclude=skip, exclude_none=True)

        if "physical_address" in optional:
            addr = {k: v for k, v in optional["physical_address"].items() if v is not None}
            if addr:
                optional["physical_address"] = addr
            else:
                optional.pop("physical_address")
        if "mailing_address" in optional:
            addr = {k: v for k, v in optional["mailing_address"].items() if v is not None}
            if addr:
                optional["mailing_address"] = addr
            else:
                optional.pop("mailing_address")
        if "entity_structure" in optional:
            optional["entity_structure"] = (
                self.entity_structure.value if self.entity_structure else None
            )
        if "tax_id_type" in optional:
            optional["tax_id_type"] = self.tax_id_type.value if self.tax_id_type else None

        # DynamoDB rejects native float; nothing here is float today, but
        # this keeps the write path safe if a future field is added as one
        # (see the same fix already required in models/applicant.py).
        item.update(
            {
                k: (Decimal(str(v)) if isinstance(v, float) else v)
                for k, v in optional.items()
                if v is not None
            }
        )
        return item

    @classmethod
    def from_item(cls, item: dict) -> "BusinessRecord":
        return cls(
            PK=item["PK"],
            SK=item["SK"],
            entity_type=item.get("entity_type", ENTITY_TYPE_BUSINESS),
            application_id=item["application_id"],
            version=int(item["version"]),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            legal_name=item.get("legal_name"),
            dba_name=item.get("dba_name"),
            entity_structure=item.get("entity_structure"),
            tax_id_last_four=item.get("tax_id_last_four"),
            tax_id_type=item.get("tax_id_type"),
            formation_date=item.get("formation_date"),
            formation_state=item.get("formation_state"),
            formation_country=item.get("formation_country"),
            website=item.get("website"),
            phone=item.get("phone"),
            email=item.get("email"),
            industry_description=item.get("industry_description"),
            naics_code=item.get("naics_code"),
            mcc=item.get("mcc"),
            physical_address=item.get("physical_address"),
            mailing_address=item.get("mailing_address"),
            business_description=item.get("business_description"),
            years_in_business=item.get("years_in_business"),
            annual_processing_volume=item.get("annual_processing_volume"),
            average_transaction_amount=item.get("average_transaction_amount"),
        )

    def to_public_dict(self) -> dict:
        """API response shape — camelCase, masked identifiers only."""
        data = self.model_dump(exclude={"pk", "sk", "entity_type"}, exclude_none=True)
        rename = {
            "application_id": "applicationId",
            "created_at": "createdAt",
            "updated_at": "updatedAt",
            "legal_name": "legalName",
            "dba_name": "dbaName",
            "entity_structure": "entityStructure",
            "tax_id_last_four": "taxIdLastFour",
            "tax_id_type": "taxIdType",
            "formation_date": "formationDate",
            "formation_state": "formationState",
            "formation_country": "formationCountry",
            "industry_description": "industryDescription",
            "naics_code": "naicsCode",
            "physical_address": "physicalAddress",
            "mailing_address": "mailingAddress",
            "business_description": "businessDescription",
            "years_in_business": "yearsInBusiness",
            "annual_processing_volume": "annualProcessingVolume",
            "average_transaction_amount": "averageTransactionAmount",
        }
        return {rename.get(k, k): v for k, v in data.items()}

    @classmethod
    def from_business(
        cls,
        application_id: str,
        data: Business,
        now: str,
        version: int = 1,
    ) -> "BusinessRecord":
        """Build a storage record from validated request data, masking the tax id."""
        return cls(
            PK=business_pk(application_id),
            SK=BUSINESS_SK,
            application_id=application_id,
            version=version,
            created_at=now,
            updated_at=now,
            legal_name=data.legal_name,
            dba_name=data.dba_name,
            entity_structure=data.entity_structure,
            tax_id_last_four=mask_last_four(data.tax_id),
            tax_id_type=data.tax_id_type,
            formation_date=data.formation_date.isoformat() if data.formation_date else None,
            formation_state=data.formation_state,
            formation_country=data.formation_country,
            website=data.website,
            phone=data.phone,
            email=data.email,
            industry_description=data.industry_description,
            naics_code=data.naics_code,
            mcc=data.mcc,
            physical_address=data.physical_address,
            mailing_address=data.mailing_address,
            business_description=data.business_description,
            years_in_business=data.years_in_business,
            annual_processing_volume=data.annual_processing_volume,
            average_transaction_amount=data.average_transaction_amount,
        )