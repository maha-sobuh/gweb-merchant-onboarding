"""Submission / internal review payload model (spec section 2, step 8).

"Internal review payload: Produce a normalized record that GWEB can later
map to one or more processor/provider onboarding APIs."

This is intentionally a flat snapshot of everything already validated and
stored elsewhere (business, persons, documents, classification,
evaluation) at the moment of submission - it is the frozen record a human
reviewer (or a future processor-mapping job) reads, independent of
whatever the applicant does to their in-progress data afterward.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from common.dynamo_utils import to_dynamo_safe

SUBMISSION_SK = "SUBMISSION"
ENTITY_TYPE_SUBMISSION = "SUBMISSION"


def submission_pk(application_id: str) -> str:
    return f"APP#{application_id}"


class SubmissionRecord(BaseModel):
    """On-disk SUBMISSION item. PK = APP#{applicationId}, SK = SUBMISSION.

    Created exactly once per application, at the moment /submit succeeds
    (see ApplicationRepository.submit_application - guarded by the same
    IN_PROGRESS -> SUBMITTED state-machine condition as METADATA status).
    """

    model_config = ConfigDict(populate_by_name=True)

    pk: str = Field(alias="PK")
    sk: str = Field(default=SUBMISSION_SK, alias="SK")
    entity_type: str = ENTITY_TYPE_SUBMISSION
    application_id: str
    submitted_at: str
    snapshot: dict

    def to_item(self) -> dict:
        item = {
            "PK": self.pk,
            "SK": self.sk,
            "entity_type": self.entity_type,
            "application_id": self.application_id,
            "submitted_at": self.submitted_at,
            "snapshot": self.snapshot,
        }
        return to_dynamo_safe(item)

    @classmethod
    def from_item(cls, item: dict) -> "SubmissionRecord":
        return cls(
            PK=item["PK"],
            SK=item["SK"],
            entity_type=item.get("entity_type", ENTITY_TYPE_SUBMISSION),
            application_id=item["application_id"],
            submitted_at=item["submitted_at"],
            snapshot=item["snapshot"],
        )

    def to_public_dict(self) -> dict:
        return {
            "applicationId": self.application_id,
            "submittedAt": self.submitted_at,
            "snapshot": self.snapshot,
        }