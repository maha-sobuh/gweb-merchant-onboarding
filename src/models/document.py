"""Document upload models (spec section 4).

Document lifecycle (spec §4): REQUESTED -> UPLOADING -> RECEIVED ->
PROCESSING -> ACCEPTED / NEEDS_REVIEW / REJECTED.

Phase 3 implements REQUESTED (created by /documents/presign) and RECEIVED
(confirmed by /documents/{id}/complete). UPLOADING is the client's direct
PUT to S3 — never persisted, since we cannot observe it. PROCESSING /
ACCEPTED / NEEDS_REVIEW / REJECTED belong to the AI evaluation phase.

Trust boundary (documented, not hidden): checksum_sha256 is CLIENT-REPORTED
at /complete time. We verify the object landed in S3 (HeadObject) and that
its size matches, but we do not independently recompute the hash server-side
in this prototype — Lambda would have to download the full object to do so,
which risks the 45-second budget on large files. Production hardening would
use S3's native x-amz-checksum-sha256 upload-time verification (S3 rejects
the PUT itself if the hash doesn't match) instead of trusting the client's
reported value. This tradeoff is called out again in SECURITY notes.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

DOC_SK_PREFIX = "DOC#"
ENTITY_TYPE_DOCUMENT = "DOCUMENT"

MAX_DECLARED_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB — generous for ID photos / PDF statements
ALLOWED_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}


class DocumentType(str, Enum):
    GOVERNMENT_ID = "GOVERNMENT_ID"
    BUSINESS_REGISTRATION = "BUSINESS_REGISTRATION"
    BUSINESS_LICENSE = "BUSINESS_LICENSE"
    BANK_EVIDENCE = "BANK_EVIDENCE"
    PROCESSING_STATEMENT = "PROCESSING_STATEMENT"
    ADDITIONAL_EVIDENCE = "ADDITIONAL_EVIDENCE"


class DocumentStatus(str, Enum):
    REQUESTED = "REQUESTED"
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"  # later phase
    ACCEPTED = "ACCEPTED"  # later phase
    NEEDS_REVIEW = "NEEDS_REVIEW"  # later phase
    REJECTED = "REJECTED"  # later phase


class PresignRequest(BaseModel):
    """Body for POST /applications/{id}/documents/presign."""

    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType
    original_filename: str = Field(min_length=1, max_length=255)
    content_type: str
    declared_size_bytes: int = Field(gt=0, le=MAX_DECLARED_SIZE_BYTES)

    def validate_content_type(self) -> None:
        # Kept as an explicit method (not a Pydantic validator) so the
        # handler can raise our own ValidationAppError with a clear message
        # instead of a generic Pydantic error.
        if self.content_type not in ALLOWED_CONTENT_TYPES:
            allowed = ", ".join(sorted(ALLOWED_CONTENT_TYPES))
            raise ValueError(f"content_type must be one of: {allowed}")


class CompleteRequest(BaseModel):
    """Body for POST /applications/{id}/documents/{documentId}/complete."""

    model_config = ConfigDict(extra="forbid")

    checksum_sha256: str = Field(
        min_length=64,
        max_length=64,
        description="Client-computed SHA-256 hex digest of the uploaded file",
    )


def document_pk(application_id: str) -> str:
    return f"APP#{application_id}"


def document_sk(document_id: str) -> str:
    return f"{DOC_SK_PREFIX}{document_id}"


def build_s3_key(application_id: str, document_id: str, original_filename: str) -> str:
    """Non-guessable, namespaced key. Never derived from user-controlled path segments."""
    safe_suffix = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "bin"
    safe_suffix = "".join(c for c in safe_suffix if c.isalnum())[:10] or "bin"
    return f"applications/{application_id}/documents/{document_id}.{safe_suffix}"


class DocumentRecord(BaseModel):
    """On-disk DOCUMENT item.

    PK = APP#{applicationId}, SK = DOC#{documentId}
    Same single table as METADATA / PERSON / BUSINESS.
    """

    model_config = ConfigDict(populate_by_name=True)

    pk: str = Field(alias="PK")
    sk: str = Field(alias="SK")
    entity_type: str = ENTITY_TYPE_DOCUMENT
    application_id: str
    document_id: str
    document_type: DocumentType
    status: DocumentStatus
    s3_key: str
    content_type: str
    original_filename: str
    declared_size_bytes: int
    actual_size_bytes: int | None = None
    checksum_sha256: str | None = None
    version: int = 1
    created_at: str
    updated_at: str

    def to_item(self) -> dict:
        return {
            "PK": self.pk,
            "SK": self.sk,
            "entity_type": self.entity_type,
            "application_id": self.application_id,
            "document_id": self.document_id,
            "document_type": self.document_type.value,
            "status": self.status.value,
            "s3_key": self.s3_key,
            "content_type": self.content_type,
            "original_filename": self.original_filename,
            "declared_size_bytes": self.declared_size_bytes,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            **({"actual_size_bytes": self.actual_size_bytes} if self.actual_size_bytes is not None else {}),
            **({"checksum_sha256": self.checksum_sha256} if self.checksum_sha256 else {}),
        }

    @classmethod
    def from_item(cls, item: dict) -> "DocumentRecord":
        return cls(
            PK=item["PK"],
            SK=item["SK"],
            entity_type=item.get("entity_type", ENTITY_TYPE_DOCUMENT),
            application_id=item["application_id"],
            document_id=item["document_id"],
            document_type=item["document_type"],
            status=item["status"],
            s3_key=item["s3_key"],
            content_type=item["content_type"],
            original_filename=item["original_filename"],
            declared_size_bytes=int(item["declared_size_bytes"]),
            actual_size_bytes=(
                int(item["actual_size_bytes"]) if "actual_size_bytes" in item else None
            ),
            checksum_sha256=item.get("checksum_sha256"),
            version=int(item["version"]),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        )

    def to_public_dict(self) -> dict:
        """API response shape. s3_key is intentionally NOT exposed — the
        client only ever needs the presigned URL (one-time, expiring),
        never the raw key (spec §4.1: never expose an unrestricted bucket;
        we go further and don't expose the key layout at all)."""
        return {
            "documentId": self.document_id,
            "applicationId": self.application_id,
            "documentType": self.document_type.value,
            "status": self.status.value,
            "contentType": self.content_type,
            "originalFilename": self.original_filename,
            "declaredSizeBytes": self.declared_size_bytes,
            "actualSizeBytes": self.actual_size_bytes,
            "version": self.version,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }