# Merchant Onboarding API — Phase 1 (Foundation)

Serverless backend for creating and retrieving merchant onboarding applications.

- Compute: AWS Lambda (Python 3.12) only
- IaC: AWS SAM (`template.yaml`)
- Validation: Pydantic v2
- Data: DynamoDB single table `MerchantOnboarding`

Phase 1 implements **POST /applications** and **GET /applications/{id}** only. Document upload, MCC classification, and AI evaluation are out of scope.

## Prerequisites

- Python 3.12
- Runtime is Python 3.13 (AWS Lambda LTS-supported since Nov 2024), matching the local development environment — avoids requiring Docker for `sam build` on machines where local Python 3.12 isn't installed.
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- AWS CLI credentials configured for deploy (not required for local unit tests)
- Docker (required by `sam local start-api`)

## Install dependencies

From this directory:

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

Runtime Lambdas install `src/requirements.txt` during `sam build` (Pydantic). `boto3` is provided by the Lambda Python runtime.

## DynamoDB access patterns (interview notes)

Single-table design: one partition per application (`PK=APP#{applicationId}`). Phase 1 stores only `SK=METADATA`. Later items (documents, MCC, AI) share that partition so a future `Query` can load the aggregate without joins or extra tables.

Idempotency for POST uses a second item type in the same table: `PK=IDEM#{sha256(key)}`, `SK=CREATE_APPLICATION`. That avoids a GSI and stores a hash instead of the raw header (possible PII).

Creates use `attribute_not_exists(PK)`. Updates in later phases must check `version` (optimistic concurrency) and increment it in the same `SET`.

## Build and run locally

```bash
sam build
sam local start-api
```

API listens on `http://127.0.0.1:3000`. Local invoke uses the table name from the template environment; for DynamoDB you can point at [DynamoDB Local](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/DynamoDBLocal.html) or deploy the stack so the table exists.

Deploy (optional):

```bash
sam deploy --guided
```

Parameters: `TableName` (default `MerchantOnboarding`), `LogLevel`, `InternalTimeoutSeconds` (must stay below the Lambda timeout of 28s).

## Timeout Budget

AWS::Serverless::Api (REST API) has a hard, non-negotiable 29-second integration timeout with Lambda — this cannot be raised without a special AWS service quota request, and even then only for Regional/private REST APIs. If Lambda runs longer than 29s, API Gateway returns a 504 before Lambda's own Timeout setting even matters.

So the timeout chain for this project is:

- API Gateway REST API hard cap: 29s (fixed, cannot be changed in this architecture)
- Lambda function Timeout: 28s (must stay under the 29s cap)
- Internal DeadlineGuard budget: 20s (leaves headroom for response serialization and cleanup, per section 7.1 of the assessment spec which recommends an internal target ≤35s under the overall 45s rule — here we're intentionally tighter because the REST API cap is 29s, not 45s)

## Example curl

Create:

```bash
curl -s -X POST http://127.0.0.1:3000/applications \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 9f3c0a2e-demo-key" \
  -d "{}"
```

Expected: HTTP 201

```json
{
  "applicationId": "<uuid>",
  "status": "IN_PROGRESS",
  "version": 1,
  "createdAt": "2026-09-16T07:00:00Z",
  "updatedAt": "2026-09-16T07:00:00Z"
}
```

Replay the same `Idempotency-Key` to receive the same `applicationId` (no duplicate row).

Get:

```bash
curl -s http://127.0.0.1:3000/applications/<applicationId>
```

Expected: HTTP 200 with the same JSON shape, or 404:

```json
{"error": {"code": "NOT_FOUND", "message": "Application not found"}}
```

## Tests

Unit tests mock DynamoDB with [moto](https://docs.getmoto.org/). No real AWS calls.

```bash
# Windows PowerShell
$env:PYTHONPATH = "src"
python -m pytest tests -q

# macOS/Linux
PYTHONPATH=src python -m pytest tests -q
```

- `tests/unit/test_application_repo.py` — PutItem conditions, get, idempotency
- `tests/unit/test_handlers.py` — handlers with a mocked repository
- `tests/integration/test_create_and_get_flow.py` — create → get round trip plus idempotency

## Error shape

All failures use:

```json
{"error": {"code": "VALIDATION_ERROR", "message": "..."}}
```

| Situation        | HTTP | code              |
|------------------|------|-------------------|
| Validation       | 400  | VALIDATION_ERROR  |
| Missing item     | 404  | NOT_FOUND         |
| Duplicate create | 409  | CONFLICT          |
| Unexpected       | 500  | INTERNAL_ERROR    |

Internal exception text and stack traces are logged as JSON to CloudWatch (PII redacted), never returned in the body.

## Known gaps (TODO)

- **Authentication / authorization** — API is unauthenticated in Phase 1. Add Amazon Cognito, IAM, or a Lambda authorizer before any production traffic.
- Document upload (S3), MCC classification, and AI evaluation endpoints are intentionally not implemented.
- `applicant` and `business` Pydantic models exist for spec §§3.1–3.2 but are not accepted or persisted yet.
