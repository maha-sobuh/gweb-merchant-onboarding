"""Single-table DynamoDB access for Merchant Onboarding.

WHY single-table:
- One application is a cluster of related records that will grow:
  METADATA now; later documents, MCC classification, AI evaluation.
- Querying PK=APP#{id} returns the whole aggregate with one Query in
  future phases. Phase 1 only needs GetItem on SK=METADATA.
- One table = one capacity pool, one backup policy, no cross-table
  transactions for a single application.

WHY these keys:
- PK / SK generic names so different entity types coexist.
- APP#{applicationId} / METADATA isolates the lookup used by GET.
- IDEM#{sha256(key)} / CREATE_APPLICATION implements POST idempotency
  as a second item type in the SAME table (no GSI required in Phase 1).
- APP#{applicationId} / PERSON#{personId} (Phase 2) keeps every person
  attached to an application in the same partition as METADATA, so a
  future Query(PK=APP#{id}) returns the whole aggregate in one round trip.

Concurrency:
- Create uses ConditionExpression attribute_not_exists(PK) so a duplicate
  applicationId cannot overwrite an existing item (409).
- Updates use optimistic concurrency on `version`:
      ConditionExpression = "version = :expected"
      SET version = version + 1, updated_at = :now, ...
  A mismatch means another writer won; the caller retries after a GetItem.
  Never blind-PutItem an existing row. upsert_person (Phase 2) is the
  first real implementation of this pattern.

This is the ONLY module that may import boto3.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from common.errors import ConflictError, NotFoundError
from models.application import (
    ENTITY_TYPE_IDEMPOTENCY,
    METADATA_SK,
    ApplicationMetadata,
    ApplicationStatus,
    application_pk,
    idempotency_pk,
    utc_now_iso,
)
from models.applicant import Applicant, PersonRecord, person_pk, person_sk

from models.business import Business, BusinessRecord, BUSINESS_SK, business_pk

from models.document import DocumentRecord, DocumentStatus, DocumentType, document_pk, document_sk

from models.mcc import ClassificationRecord, CLASSIFICATION_SK, classification_pk

logger = logging.getLogger("merchant_onboarding.repo")

IDEMPOTENCY_SK = "CREATE_APPLICATION"


def hash_idempotency_key(raw_key: str) -> str:
    """Store/lookup a digest so the raw header value (possible PII) is not kept."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class ApplicationRepository:
    def __init__(self, table_name: str | None = None, dynamodb_resource: Any = None) -> None:
        self._table_name = table_name or os.environ["TABLE_NAME"]
        if dynamodb_resource is None:
            kwargs: dict[str, Any] = {}
            region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
            if region:
                kwargs["region_name"] = region
            dynamodb_resource = boto3.resource("dynamodb", **kwargs)
        self._table = dynamodb_resource.Table(self._table_name)

    def create_application(
        self,
        application_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[ApplicationMetadata, bool]:
        """Create METADATA (and optional idempotency record).

        Returns (metadata, created) where created is False on an idempotent replay.
        """
        now = utc_now_iso()
        key_hash = hash_idempotency_key(idempotency_key) if idempotency_key else None

        if key_hash:
            claimed = self._claim_idempotency(key_hash, application_id, now)
            if not claimed:
                existing = self.get_idempotency(key_hash)
                if existing is None:
                    # Extremely unlikely: conditional fail then missing item.
                    raise ConflictError("Idempotency key conflict")
                replay_id = existing["application_id"]
                meta = self.get_metadata(replay_id)
                if meta is None:
                    # Recover from a crash between the two PutItems.
                    logger.warning(
                        "Idempotency claimed without METADATA; completing write",
                        extra={"application_id": replay_id},
                    )
                    meta = self._put_metadata(replay_id, now, key_hash)
                    return meta, True
                return meta, False

        meta = self._put_metadata(application_id, now, key_hash)
        return meta, True

    def get_metadata(self, application_id: str) -> ApplicationMetadata | None:
        response = self._table.get_item(
            Key={"PK": application_pk(application_id), "SK": METADATA_SK},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return ApplicationMetadata.from_item(item)

    def get_idempotency(self, key_hash: str) -> dict | None:
        response = self._table.get_item(
            Key={"PK": idempotency_pk(key_hash), "SK": IDEMPOTENCY_SK},
            ConsistentRead=True,
        )
        return response.get("Item")

    def _claim_idempotency(self, key_hash: str, application_id: str, now: str) -> bool:
        item = {
            "PK": idempotency_pk(key_hash),
            "SK": IDEMPOTENCY_SK,
            "entity_type": ENTITY_TYPE_IDEMPOTENCY,
            "application_id": application_id,
            "status": ApplicationStatus.IN_PROGRESS.value,
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(PK)",
            )
            return True
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                return False
            logger.exception("DynamoDB error claiming idempotency key")
            raise

    def _put_metadata(
        self,
        application_id: str,
        now: str,
        key_hash: str | None,
    ) -> ApplicationMetadata:
        meta = ApplicationMetadata(
            PK=application_pk(application_id),
            SK=METADATA_SK,
            application_id=application_id,
            status=ApplicationStatus.IN_PROGRESS,
            version=1,
            created_at=now,
            updated_at=now,
            idempotency_key_hash=key_hash,
        )
        try:
            # Guard against UUID collision / replay of the same applicationId.
            self._table.put_item(
                Item=meta.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                raise ConflictError("An application with this id already exists") from exc
            logger.exception("DynamoDB error writing application metadata")
            raise
        return meta

    def update_metadata_optimistic(self, meta: ApplicationMetadata, expected_version: int) -> None:
        """Pattern for later phases — not used by create/get.

        ConditionExpression version = :expected_version prevents lost updates.
        Increment version in the same SET so two writers cannot both succeed.
        """
        raise NotImplementedError(
            "Phase 1 has no updates. Future writers must ConditionExpression "
            "on version and SET version = version + 1."
        )

    # --- Phase 2: applicant / person records -------------------------------

    def get_person(self, application_id: str, person_id: str) -> PersonRecord | None:
        response = self._table.get_item(
            Key={"PK": person_pk(application_id), "SK": person_sk(person_id)},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return PersonRecord.from_item(item)

    def upsert_person(
        self,
        application_id: str,
        data: Applicant,
        person_id: str,
        expected_version: int | None,
    ) -> tuple[PersonRecord, bool]:
        """Create a new person or update an existing one.

        - expected_version is None -> create. Guarded by attribute_not_exists
          so a reused person_id cannot silently overwrite an existing record.
        - expected_version is provided -> optimistic-concurrency update, the
          real implementation of the pattern documented in
          update_metadata_optimistic above.

        Returns (record, created).
        """
        now = utc_now_iso()

        if expected_version is None:
            record = PersonRecord.from_applicant(application_id, person_id, data, now, version=1)
            try:
                self._table.put_item(
                    Item=record.to_item(),
                    ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
                )
            except ClientError as exc:
                if _is_conditional_check_failed(exc):
                    raise ConflictError(
                        "A person with this id already exists; supply expected_version to update it"
                    ) from exc
                logger.exception("DynamoDB error creating person")
                raise
            return record, True

        existing = self.get_person(application_id, person_id)
        if existing is None:
            raise NotFoundError("Person not found on this application")

        record = PersonRecord.from_applicant(
            application_id, person_id, data, existing.created_at, version=expected_version + 1
        )
        try:
            self._table.put_item(
                Item=record.to_item(),
                ConditionExpression="attribute_exists(PK) AND version = :expected",
                ExpressionAttributeValues={":expected": expected_version},
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                raise ConflictError(
                    "Person was modified concurrently; refetch and retry with the current version"
                ) from exc
            logger.exception("DynamoDB error updating person")
            raise
        return record, False
            # --- Phase 2: business record (singleton per application) --------------

    def get_business(self, application_id: str) -> BusinessRecord | None:
        response = self._table.get_item(
            Key={"PK": business_pk(application_id), "SK": BUSINESS_SK},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return BusinessRecord.from_item(item)

    def upsert_business(
        self,
        application_id: str,
        data: Business,
        expected_version: int | None,
    ) -> tuple[BusinessRecord, bool]:
        """Create or update the single business record for an application.

        - expected_version is None -> create. Guarded by attribute_not_exists
          so a second unversioned PATCH cannot silently overwrite an existing
          business record.
        - expected_version is provided -> optimistic-concurrency update,
          same pattern as upsert_person.

        Returns (record, created).
        """
        now = utc_now_iso()

        if expected_version is None:
            record = BusinessRecord.from_business(application_id, data, now, version=1)
            try:
                self._table.put_item(
                    Item=record.to_item(),
                    ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
                )
            except ClientError as exc:
                if _is_conditional_check_failed(exc):
                    raise ConflictError(
                        "A business record already exists for this application; "
                        "supply expected_version to update it"
                    ) from exc
                logger.exception("DynamoDB error creating business record")
                raise
            return record, True

        existing = self.get_business(application_id)
        if existing is None:
            raise NotFoundError("No business record exists yet for this application")

        record = BusinessRecord.from_business(
            application_id, data, existing.created_at, version=expected_version + 1
        )
        try:
            self._table.put_item(
                Item=record.to_item(),
                ConditionExpression="attribute_exists(PK) AND version = :expected",
                ExpressionAttributeValues={":expected": expected_version},
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                raise ConflictError(
                    "Business record was modified concurrently; refetch and retry with the current version"
                ) from exc
            logger.exception("DynamoDB error updating business record")
            raise
        return record, False

    # --- Phase 3: document records -----------------------------------------

    def create_document(
        self,
        application_id: str,
        document_id: str,
        document_type: DocumentType,
        s3_key: str,
        content_type: str,
        original_filename: str,
        declared_size_bytes: int,
    ) -> DocumentRecord:
        """Create a REQUESTED document record. Always a fresh document_id
        (UUID, generated by the handler), so a plain attribute_not_exists
        guard is sufficient — there is no legitimate case where the same
        document_id is reused.
        """
        now = utc_now_iso()
        record = DocumentRecord(
            PK=document_pk(application_id),
            SK=document_sk(document_id),
            application_id=application_id,
            document_id=document_id,
            document_type=document_type,
            status=DocumentStatus.REQUESTED,
            s3_key=s3_key,
            content_type=content_type,
            original_filename=original_filename,
            declared_size_bytes=declared_size_bytes,
            version=1,
            created_at=now,
            updated_at=now,
        )
        try:
            self._table.put_item(
                Item=record.to_item(),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                raise ConflictError("A document with this id already exists") from exc
            logger.exception("DynamoDB error creating document record")
            raise
        return record

    def get_document(self, application_id: str, document_id: str) -> DocumentRecord | None:
        response = self._table.get_item(
            Key={"PK": document_pk(application_id), "SK": document_sk(document_id)},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return DocumentRecord.from_item(item)

    def mark_document_received(
        self,
        application_id: str,
        document_id: str,
        checksum_sha256: str,
        actual_size_bytes: int,
    ) -> DocumentRecord:
        """Transition REQUESTED -> RECEIVED.

        The status itself is the concurrency guard here (state-machine
        pattern), rather than a separate expected_version field: this
        write can only succeed once, from REQUESTED. A second /complete
        call on the same document (status already RECEIVED) is rejected
        as a conflict, not silently reapplied.
        """
        existing = self.get_document(application_id, document_id)
        if existing is None:
            raise NotFoundError("Document not found on this application")

        now = utc_now_iso()
        try:
            self._table.update_item(
                Key={"PK": document_pk(application_id), "SK": document_sk(document_id)},
                UpdateExpression=(
                    "SET #status = :received, checksum_sha256 = :checksum, "
                    "actual_size_bytes = :size, updated_at = :now, "
                    "version = version + :one"
                ),
                ConditionExpression="#status = :requested",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":received": DocumentStatus.RECEIVED.value,
                    ":requested": DocumentStatus.REQUESTED.value,
                    ":checksum": checksum_sha256,
                    ":size": actual_size_bytes,
                    ":now": now,
                    ":one": 1,
                },
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                raise ConflictError(
                    f"Document is not in REQUESTED state (currently {existing.status.value}); "
                    "it may already have been completed"
                ) from exc
            logger.exception("DynamoDB error marking document received")
            raise

        return self.get_document(application_id, document_id)  # re-read for the updated item

    # --- Phase 4a: MCC classification record (singleton per application) ---

    def get_classification(self, application_id: str) -> ClassificationRecord | None:
        response = self._table.get_item(
            Key={"PK": classification_pk(application_id), "SK": CLASSIFICATION_SK},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return ClassificationRecord.from_item(item)

    def upsert_classification(self, record: ClassificationRecord) -> ClassificationRecord:
        """Create or overwrite the single classification record for an
        application. Unlike person/business, this is intentionally NOT
        version-guarded: every /classify call (whether it's a fresh
        suggestion or a confirm/correct) is meant to simply record the
        latest state, and the spec asks us to persist both the applicant's
        selection and the system's proposal every time, not to protect
        against concurrent editors the way person/business records do.
        """
        existing = self.get_classification(application_id=record.application_id)
        now = utc_now_iso()
        record.version = (existing.version + 1) if existing else 1
        record.created_at = existing.created_at if existing else now
        record.updated_at = now

        self._table.put_item(Item=record.to_item())
        return record


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"