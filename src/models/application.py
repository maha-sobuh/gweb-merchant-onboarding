"""Application aggregate and DynamoDB METADATA item (Phase 1).

Access pattern this model supports:
  PK = APP#{applicationId}
  SK = METADATA

We store one METADATA item per application so GET /applications/{id} is a
single-item GetItem (1 RCU, strongly consistent if needed). Later phases
will add sibling items under the SAME partition (documents, MCC result,
AI evaluation) without breaking this lookup.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from models.applicant import Applicant
from models.business import Business

METADATA_SK = "METADATA"
ENTITY_TYPE_APPLICATION = "APPLICATION_METADATA"
ENTITY_TYPE_IDEMPOTENCY = "IDEMPOTENCY_RECORD"


class ApplicationStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    # Later phases: SUBMITTED, UNDER_REVIEW, APPROVED, REJECTED, NEEDS_INFO


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def application_pk(application_id: str) -> str:
    return f"APP#{application_id}"


def idempotency_pk(key_hash: str) -> str:
    return f"IDEM#{key_hash}"


class ApplicationMetadata(BaseModel):
    """On-disk METADATA item. Every DynamoDB item in this table carries
    entity_type, created_at, updated_at, version, and status.
    """

    model_config = ConfigDict(populate_by_name=True)

    pk: str = Field(alias="PK")
    sk: str = Field(default=METADATA_SK, alias="SK")
    entity_type: str = ENTITY_TYPE_APPLICATION
    application_id: str
    status: ApplicationStatus = ApplicationStatus.IN_PROGRESS
    version: int = 1
    created_at: str
    updated_at: str
    idempotency_key_hash: str | None = None

    def to_item(self) -> dict:
        return {
            "PK": self.pk,
            "SK": self.sk,
            "entity_type": self.entity_type,
            "application_id": self.application_id,
            "status": self.status.value,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            **(
                {"idempotency_key_hash": self.idempotency_key_hash}
                if self.idempotency_key_hash
                else {}
            ),
        }

    @classmethod
    def from_item(cls, item: dict) -> ApplicationMetadata:
        return cls(
            PK=item["PK"],
            SK=item["SK"],
            entity_type=item["entity_type"],
            application_id=item["application_id"],
            status=item["status"],
            version=int(item["version"]),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            idempotency_key_hash=item.get("idempotency_key_hash"),
        )


class Application(BaseModel):
    """API-facing application state (normalized, no DynamoDB keys).

    applicant/business are modeled for later phases and omitted from
    Phase 1 responses until those items exist.
    """

    model_config = ConfigDict(populate_by_name=True)

    application_id: str = Field(serialization_alias="applicationId")
    status: ApplicationStatus
    version: int
    created_at: str = Field(serialization_alias="createdAt")
    updated_at: str = Field(serialization_alias="updatedAt")
    applicant: Applicant | None = None
    business: Business | None = None

    def to_public_dict(self) -> dict:
        data = self.model_dump(by_alias=True, exclude_none=True)
        # Nested unused models stay out of Phase 1 payloads.
        data.pop("applicant", None)
        data.pop("business", None)
        return data

    @classmethod
    def from_metadata(cls, meta: ApplicationMetadata) -> Application:
        return cls(
            application_id=meta.application_id,
            status=meta.status,
            version=meta.version,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
        )
