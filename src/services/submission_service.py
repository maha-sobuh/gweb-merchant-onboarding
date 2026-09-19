"""Submission validation + normalized review payload construction
(spec section 2, steps 7-8). No boto3 - pure logic over already-loaded
records, same shape as evaluation_service.py.
"""

from __future__ import annotations

from models.application import ApplicationMetadata
from models.applicant import PersonRecord
from models.business import BusinessRecord
from models.document import DocumentRecord, DocumentStatus, DocumentType
from models.evaluation import EvaluationRecord
from models.mcc import ClassificationRecord

REQUIRED_DOCUMENT_TYPES = (
    DocumentType.GOVERNMENT_ID,
    DocumentType.BUSINESS_REGISTRATION,
    DocumentType.BANK_EVIDENCE,
)


def validate_for_submission(
    business: BusinessRecord | None,
    persons: list[PersonRecord],
    documents: list[DocumentRecord],
    classification: ClassificationRecord | None,
) -> list[str]:
    """Returns a list of human-readable missing-item descriptions. Empty
    list means the application is ready to submit. Spec: 'Submission
    blocks when required items are missing' - every item here is a
    specific, actionable gap, not a generic 'incomplete' message."""
    missing: list[str] = []

    if business is None:
        missing.append("business information not provided (PATCH /applications/{id}/business)")
    else:
        if not business.legal_name:
            missing.append("business.legal_name")
        if not business.entity_structure:
            missing.append("business.entity_structure")
        if not business.tax_id_last_four:
            missing.append("business.tax_id (EIN/TIN)")
        if not business.physical_address:
            missing.append("business.physical_address")
        if not business.business_description:
            missing.append("business.business_description")

    if not any(p.is_control_person for p in persons):
        missing.append(
            "at least one control person "
            "(PATCH /applications/{id}/applicant with is_control_person=true)"
        )

    if classification is None or not classification.confirmed_mcc:
        missing.append("confirmed MCC (POST /applications/{id}/classify with confirmed_mcc)")

    received_types = {d.document_type for d in documents if d.status == DocumentStatus.RECEIVED}
    for required in REQUIRED_DOCUMENT_TYPES:
        if required not in received_types:
            missing.append(f"document {required.value} (status RECEIVED)")

    return missing


def build_submission_snapshot(
    metadata: ApplicationMetadata,
    business: BusinessRecord | None,
    persons: list[PersonRecord],
    documents: list[DocumentRecord],
    classification: ClassificationRecord | None,
    evaluation: EvaluationRecord | None,
) -> dict:
    """The normalized internal review payload (spec step 8). A future
    processor-mapping job reads this single frozen structure rather than
    re-querying every live entity type."""
    return {
        "application": {
            "applicationId": metadata.application_id,
            "status": metadata.status.value,
            "createdAt": metadata.created_at,
        },
        "business": business.to_public_dict() if business else None,
        "persons": [p.to_public_dict() for p in persons],
        "documents": [d.to_public_dict() for d in documents],
        "classification": classification.to_public_dict() if classification else None,
        "evaluation": evaluation.to_public_dict() if evaluation else None,
    }