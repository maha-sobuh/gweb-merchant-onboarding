"""Applicant / control-person models (spec section 3.1).

Two representations:
- `Applicant` — request/response shape used by the API. May carry raw
  identifiers (tax_id, government_id_number) that must NEVER be persisted
  or logged as-is.
- `PersonRecord` — on-disk DynamoDB item. Stores only masked identifiers
  (last four digits) per spec section 10 ("Mask tax IDs, personal
  identifiers... in responses where the full value is no longer needed").

PII: every field here except the *_person flags and ownership_percentage
is PII. logging_config redacts them; never print model_dump() in logs.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

PERSON_SK_PREFIX = "PERSON#"
ENTITY_TYPE_PERSON = "PERSON"


class TaxIdType(str, Enum):
    SSN = "SSN"
    ITIN = "ITIN"
    OTHER = "OTHER"


class GovernmentIdType(str, Enum):
    PASSPORT = "PASSPORT"
    DRIVERS_LICENSE = "DRIVERS_LICENSE"
    NATIONAL_ID = "NATIONAL_ID"
    OTHER = "OTHER"


class Address(BaseModel):
    """Residential or mailing address for a natural person."""

    model_config = ConfigDict(extra="forbid")

    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2")


class Applicant(BaseModel):
    """Natural person on the application (beneficial owner and/or control person).

    Spec §3.1 — FinCEN CDD "ownership prong" (typically >=25%) and
    "control prong" (one individual with significant managerial authority).

    This is the REQUEST/RESPONSE shape. Raw tax_id / government_id_number
    are accepted here but are masked down to last-four before they ever
    reach DynamoDB (see PersonRecord.from_applicant) — the raw value is
    used only transiently inside a single Lambda invocation.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: str | None = Field(default=None, description="Omit to create a new person")
    expected_version: int | None = Field(
        default=None,
        description="Required when person_id refers to an existing person (optimistic concurrency)",
    )

    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = Field(default=None, description="SSN/ITIN — PII, never log or store raw")
    tax_id_type: TaxIdType | None = None
    citizenship_country: str | None = Field(default=None, description="ISO 3166-1 alpha-2")
    title: str | None = Field(default=None, description="Corporate title, e.g. CEO, Managing Member")
    ownership_percentage: float | None = Field(default=None, ge=0, le=100)
    is_control_person: bool = False
    is_beneficial_owner: bool = False
    residential_address: Address | None = None
    government_id_type: GovernmentIdType | None = None
    government_id_number: str | None = Field(
        default=None, description="PII — masked to last four before storage, never stored raw"
    )
    government_id_country: str | None = None
    consent_accepted_at: str | None = Field(
        default=None, description="ISO-8601 timestamp of terms acceptance"
    )
    consent_terms_version: str | None = None


class ControlPerson(Applicant):
    """Alias type for the CDD control-prong individual (spec §3.1)."""

    is_control_person: bool = True


def mask_last_four(raw: str | None) -> str | None:
    """Reduce a raw identifier to its last four characters for storage/display.

    Spec §10: "Mask tax IDs, personal identifiers, and bank account data
    in responses where the full value is no longer needed." We apply the
    same rule at storage time — the full value is never needed after intake,
    so it is never written to DynamoDB or logged in the first place.
    """
    if not raw:
        return None
    cleaned = raw.strip()
    if len(cleaned) <= 4:
        return cleaned
    return cleaned[-4:]


def person_pk(application_id: str) -> str:
    return f"APP#{application_id}"


def person_sk(person_id: str) -> str:
    return f"{PERSON_SK_PREFIX}{person_id}"


class PersonRecord(BaseModel):
    """On-disk PERSON item.

    PK = APP#{applicationId}, SK = PERSON#{personId}
    Lives in the same single table as APPLICATION_METADATA (see the WHY
    comment in application_repo.py) so a future Query(PK=APP#{id}) returns
    the whole application aggregate in one round trip.

    Only masked identifiers are stored. Raw tax_id / government_id_number
    never reach this class.
    """

    model_config = ConfigDict(populate_by_name=True)

    pk: str = Field(alias="PK")
    sk: str = Field(alias="SK")
    entity_type: str = ENTITY_TYPE_PERSON
    application_id: str
    person_id: str
    version: int = 1
    created_at: str
    updated_at: str

    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    date_of_birth: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_id_last_four: str | None = None
    tax_id_type: TaxIdType | None = None
    citizenship_country: str | None = None
    title: str | None = None
    ownership_percentage: float | None = None
    is_control_person: bool = False
    is_beneficial_owner: bool = False
    residential_address: Address | None = None
    government_id_type: GovernmentIdType | None = None
    government_id_last_four: str | None = None
    government_id_country: str | None = None
    consent_accepted_at: str | None = None
    consent_terms_version: str | None = None

    def to_item(self) -> dict:
        item: dict = {
            "PK": self.pk,
            "SK": self.sk,
            "entity_type": self.entity_type,
            "application_id": self.application_id,
            "person_id": self.person_id,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        skip = set(item.keys())
        optional = self.model_dump(exclude=skip, exclude_none=True)

        if "residential_address" in optional:
            addr = {k: v for k, v in optional["residential_address"].items() if v is not None}
            if addr:
                optional["residential_address"] = addr
            else:
                optional.pop("residential_address")
        if "tax_id_type" in optional:
            optional["tax_id_type"] = self.tax_id_type.value if self.tax_id_type else None
        if "government_id_type" in optional:
            optional["government_id_type"] = (
                self.government_id_type.value if self.government_id_type else None
            )
        item.update({
            k: (Decimal(str(v)) if isinstance(v, float) else v)
            for k, v in optional.items() if v is not None
        })
        return item

    @classmethod
    def from_item(cls, item: dict) -> "PersonRecord":
        return cls(
            PK=item["PK"],
            SK=item["SK"],
            entity_type=item.get("entity_type", ENTITY_TYPE_PERSON),
            application_id=item["application_id"],
            person_id=item["person_id"],
            version=int(item["version"]),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            first_name=item.get("first_name"),
            middle_name=item.get("middle_name"),
            last_name=item.get("last_name"),
            date_of_birth=item.get("date_of_birth"),
            email=item.get("email"),
            phone=item.get("phone"),
            tax_id_last_four=item.get("tax_id_last_four"),
            tax_id_type=item.get("tax_id_type"),
            citizenship_country=item.get("citizenship_country"),
            title=item.get("title"),
            ownership_percentage=item.get("ownership_percentage"),
            is_control_person=item.get("is_control_person", False),
            is_beneficial_owner=item.get("is_beneficial_owner", False),
            residential_address=item.get("residential_address"),
            government_id_type=item.get("government_id_type"),
            government_id_last_four=item.get("government_id_last_four"),
            government_id_country=item.get("government_id_country"),
            consent_accepted_at=item.get("consent_accepted_at"),
            consent_terms_version=item.get("consent_terms_version"),
        )

    def to_public_dict(self) -> dict:
        """API response shape — camelCase, masked identifiers only."""
        data = self.model_dump(exclude={"pk", "sk", "entity_type"}, exclude_none=True)
        rename = {
            "application_id": "applicationId",
            "person_id": "personId",
            "created_at": "createdAt",
            "updated_at": "updatedAt",
            "first_name": "firstName",
            "middle_name": "middleName",
            "last_name": "lastName",
            "date_of_birth": "dateOfBirth",
            "tax_id_last_four": "taxIdLastFour",
            "tax_id_type": "taxIdType",
            "citizenship_country": "citizenshipCountry",
            "ownership_percentage": "ownershipPercentage",
            "is_control_person": "isControlPerson",
            "is_beneficial_owner": "isBeneficialOwner",
            "residential_address": "residentialAddress",
            "government_id_type": "governmentIdType",
            "government_id_last_four": "governmentIdLastFour",
            "government_id_country": "governmentIdCountry",
            "consent_accepted_at": "consentAcceptedAt",
            "consent_terms_version": "consentTermsVersion",
        }
        return {rename.get(k, k): v for k, v in data.items()}

    @classmethod
    def from_applicant(
        cls,
        application_id: str,
        person_id: str,
        data: Applicant,
        now: str,
        version: int = 1,
    ) -> "PersonRecord":
        """Build a storage record from validated request data, masking PII."""
        return cls(
            PK=person_pk(application_id),
            SK=person_sk(person_id),
            application_id=application_id,
            person_id=person_id,
            version=version,
            created_at=now,
            updated_at=now,
            first_name=data.first_name,
            middle_name=data.middle_name,
            last_name=data.last_name,
            date_of_birth=data.date_of_birth.isoformat() if data.date_of_birth else None,
            email=data.email,
            phone=data.phone,
            tax_id_last_four=mask_last_four(data.tax_id),
            tax_id_type=data.tax_id_type,
            citizenship_country=data.citizenship_country,
            title=data.title,
            ownership_percentage=data.ownership_percentage,
            is_control_person=data.is_control_person,
            is_beneficial_owner=data.is_beneficial_owner,
            residential_address=data.residential_address,
            government_id_type=data.government_id_type,
            government_id_last_four=mask_last_four(data.government_id_number),
            government_id_country=data.government_id_country,
            consent_accepted_at=data.consent_accepted_at,
            consent_terms_version=data.consent_terms_version,
        )

