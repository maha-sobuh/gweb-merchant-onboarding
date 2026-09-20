# Security Notes

This is a take-home assessment implementation, not a production service. This document records the security controls that are implemented, the threats they address, and what would be required before real traffic.

## Data handled

The API collects business details, personal details of owners and control persons, tax identifiers, and uploaded documents (government ID, business registration, bank evidence). All of this is sensitive.

## Controls implemented

**Input validation.** Every request body is validated with Pydantic v2 models before it touches storage. Invalid input returns a 400 with a generic message.

**Sensitive identifiers are masked before storage.** Tax IDs and government ID numbers are reduced to their last four characters when the record is built (`tax_id_last_four`, `government_id_last_four`), so the full value is never stored or returned.

**No PII in logs.** Logging is structured JSON with a correlation ID and application ID, and PII is redacted. Internal exceptions and stack traces are logged, never returned to the client.

**Generic error responses.** All failures use one error envelope (`{"error": {"code": ..., "message": ...}}`). Unexpected errors return `INTERNAL_ERROR` with no internal detail.

**Private, encrypted document storage.** The S3 bucket has server-side encryption and a public access block configured in the template. Documents are uploaded directly with expiring presigned URLs bound to the declared content type, and object keys are non-guessable, so a document cannot be found by enumerating identifiers. File bytes never pass through Lambda. When an upload is confirmed, the object's existence and actual size are read back from S3 (HeadObject) instead of trusting the client.

**Least-privilege IAM.** Each Lambda function has its own policy listing only the DynamoDB and S3 actions it needs. For example, the GET endpoints have `GetItem` only, and the MCC search function has no data-access policy at all.

**Idempotency keys are hashed.** The `Idempotency-Key` header is stored as a SHA-256 hash rather than raw, since clients may put identifying values in it.

**Concurrency and state integrity.** State transitions (`IN_PROGRESS` -> `SUBMITTED`, document `REQUESTED` -> `RECEIVED`) are DynamoDB conditional writes, and updates use optimistic concurrency through a `version` attribute. Double submits, stale updates, and races return 409 instead of overwriting data.

**Bounded execution.** A `DeadlineGuard` (20s internal budget under a 28s Lambda timeout) stops slow work before API Gateway's 29s limit.

**Configuration through the environment.** Table name, bucket name, log level and timeouts come from environment variables set by the SAM template. Nothing sensitive is hardcoded in the repository.

**Constrained AI boundary.** The evaluation step goes through an `AIAdapter` interface. Adapter output is schema-validated, size-capped and timeout-guarded, and the adapter fails safe. The effective rate is computed deterministically and never comes from a model. MCC classification is deterministic, and nothing in the system auto-approves an application.

## Threats considered

| Threat | Status |
|--------|--------|
| Duplicate or concurrent submission | Mitigated: conditional writes, 409 on conflict |
| PII exposure through logs or error bodies | Mitigated: redacted structured logs, generic errors |
| Guessing or enumerating uploaded documents | Mitigated: private bucket, non-guessable keys, presigned URLs |
| Reuse of an idempotency key leaking data | Mitigated: hashed keys, replay returns the original result only |
| Tampered document metadata or oversized uploads | Partly mitigated: the 25 MB cap and the content type allowlist apply to the declared values, and the actual size is read from S3 on completion but is not compared with the declared size. `checksum_sha256` is client-reported and not re-verified, and there is no file-signature check |
| Untrusted merchant text reaching an AI provider (prompt injection) | Not applicable today, since the adapter is a mock. With a real provider, treat merchant-supplied text as untrusted, keep the output schema-validated, and keep the deterministic calculations separate |
| Unauthorized access to any endpoint | **Not mitigated**, see below |

## Known gaps

- **No authentication or authorization.** Any caller who knows an `applicationId` can read or modify that application. This is the main blocker for production.

## Recommended before production

- Add authentication (Amazon Cognito, IAM auth, or a Lambda authorizer) and check that the caller owns the application on every endpoint.
- Use non-sequential, high-entropy application IDs (already UUIDs) together with ownership checks, not as the only protection.
- Add API Gateway throttling and usage plans, and consider AWS WAF.
- Move to customer-managed KMS keys for S3 and DynamoDB, and enable S3 versioning and access logging.
- On completion, compare the actual size and content type with the declared values and the size cap, and check the file signature. Enforce the size cap at upload time as well (presigned URLs alone cannot; a presigned POST with a `content-length-range` condition can), and scan uploaded files for malware.
- Verify document integrity server-side (recompute the checksum after upload instead of trusting the client value).
- Make `submit_application` atomic with a single `transact_write_items` call.
- Retention and deletion policy for PII and documents, plus audit logging of reads and writes.
- Dependency scanning in CI.

## Reporting a vulnerability

Please report security issues privately to the repository owner (for example through GitHub's private vulnerability reporting on the repository) rather than opening a public issue.
