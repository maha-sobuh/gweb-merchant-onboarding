"""Applicant / control-person models (spec section 3.1).

These fields are defined in Phase 1 so later document/KYC phases can persist
them without changing the schema shape. They are NOT collected by
POST /applications yet.

PII: every field here except role flags and ownership_percentage is PII.
logging_config redacts them; never print model_dump() in logs.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


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

    Spec §3.1 — FinCEN CDD "ownership prong" (typically ≥25%) and
    "control prong" (one individual with significant managerial authority).
    """

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = Field(default=None, description="SSN/ITIN — PII, never log")
    tax_id_type: TaxIdType | None = None
    citizenship_country: str | None = Field(
        default=None, description="ISO 3166-1 alpha-2"
    )
    title: str | None = Field(
        default=None, description="Corporate title, e.g. CEO, Managing Member"
    )
    ownership_percentage: float | None = Field(default=None, ge=0, le=100)
    is_control_person: bool = False
    is_beneficial_owner: bool = False
    residential_address: Address | None = None
    government_id_type: GovernmentIdType | None = None
    government_id_number: str | None = Field(
        default=None, description="For non-US persons when SSN is unavailable"
    )
    government_id_country: str | None = None


class ControlPerson(Applicant):
    """Alias type for the CDD control-prong individual (spec §3.1)."""

    is_control_person: bool = True
