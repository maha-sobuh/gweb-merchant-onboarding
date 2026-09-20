# Test Evidence

## Summary

The full captured output of `pytest -q` is saved in `docs/pytest-output.txt`, and every test passes.

Environment: Python 3.13.1, pytest 9.1.1. Tests run against moto (mocked DynamoDB and S3); no real AWS calls, no credentials, and only fixture data (no real PII).

Run them from the repository root:

```bash
pytest
```

## What the tests cover

| Spec requirement (section 11) | Where |
|-------------------------------|-------|
| Validation | `tests/unit/test_applicant_handler.py`, `test_business_handler.py`, `test_documents_presign_handler.py`, `test_documents_complete_handler.py`, `test_submission_service.py` |
| MCC search and risk mapping | `tests/unit/test_mcc_service.py`, `test_mcc_handlers.py` |
| Rate arithmetic | `tests/unit/test_evaluation_service.py`, `test_evaluate_handlers.py` |
| Lambda handlers and service logic | `tests/unit/test_handlers.py`, `test_submit_handler.py`, `test_ai_adapter.py` |
| Repositories (conditional writes, versions, idempotency) | `tests/unit/test_application_repo.py`, `test_business_repo.py`, `test_person_repo.py`, `test_document_repo.py`, `test_evaluation_repo.py`, `test_submission_repo.py` |
| Slow dependency and the deadline | `tests/unit/test_slow_dependency.py` |
| End-to-end flow and resume | `tests/integration/test_full_journey.py`, `tests/integration/test_resume_state.py` |

### End-to-end test

`test_full_merchant_onboarding_journey` drives the real Lambda handlers through the whole journey: create application, an early submit that is blocked with 6 missing items, control person, business, three documents (presign, direct upload to S3, complete), MCC suggestion and confirmation, evaluation with a processing statement, submit, and a second submit that returns 409. It also asserts that the raw tax ID and the S3 key layout never appear in a response, and that the `SUBMISSION` item exists in DynamoDB.

`test_complete_before_upload_is_rejected` checks that `/complete` returns 400 and leaves the document in `REQUESTED` when nothing was uploaded.

## 45-second reliability

Requirement: a hanging external dependency must not hang the request, and the function must exit safely before the hard limit.

**Mechanism.** Every AI adapter call goes through `_call_ai` in `services/evaluation_service.py`. It runs the call in a worker thread and waits at most (remaining deadline minus a 1-second reserve). On timeout it raises `AIAdapterError`, and the caller falls back to a safe result with `ai_available: false`. The rest of the evaluation, including the deterministic rate arithmetic, is still computed and returned.

**Test.** `test_hanging_ai_dependency_falls_back_before_the_deadline` uses an adapter that blocks for up to 30 seconds, with the internal deadline set to 3 seconds. It asserts that:

- the handler returns before the 3-second deadline,
- the response is HTTP 200,
- `businessProfile.ai_available` is `false`,
- `statementAnalysis.effective_rate_percent` is still `2.75` (the arithmetic is unaffected by the AI outage).

**Observed run** (`scripts/demo_flow.py`, step 10):

```
internal deadline for this demo: 3 seconds
elapsed: 2.02s  (HTTP 200)
ai_available: False  (safe fallback)
effective_rate_percent: 2.75
```

In production the deadline is 20 seconds (Lambda timeout 28 s, API Gateway hard cap 29 s), so a hanging provider is abandoned after about 19 seconds and the request still completes well inside the 45-second rule.

**Limitations.** A Python thread cannot be killed: the abandoned worker keeps running until it finishes or the Lambda environment is frozen, and its result is discarded. A real provider client should also set its own connect and read timeouts. There is no automatic retry of the AI call; bounded retries with backoff are only sensible when the remaining deadline permits, and the design falls back instead.

## Bugs the tests caught

1. `ApplicationStatus` had no `SUBMITTED` value, so every real submit would have failed. The handler tests used a fake repository and passed; the moto-backed repository tests failed immediately.
2. The submit response returned numeric values (for example the effective rate) as strings, because floats were converted to `Decimal` before serialization. The end-to-end test caught it.
3. The review snapshot recorded `IN_PROGRESS` as the application status instead of `SUBMITTED`. The demo transcript exposed it.

## Demo transcript

`docs/demo-output.txt` is the captured output of `python scripts/demo_flow.py`: the complete journey from creation to the review payload, the 409 on a second submit, and the slow-dependency demonstration. Regenerate it with:

```powershell
python scripts/demo_flow.py | Out-File -Encoding utf8 docs/demo-output.txt
```

## Not covered

- No test against a real AWS deployment; behavior is verified with moto and `sam validate`.
- `sam local start-api` was not run.
- No load or concurrency stress test. Concurrent submits are protected by the conditional write, and the second call returning 409 is tested sequentially.
