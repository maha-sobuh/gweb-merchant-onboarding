"""AI-assisted evaluation adapter (spec section 6).

MockAIAdapter is used because no external model/API credentials are
supplied in this environment (spec: "It may use a mock adapter if no
external model/API credentials are supplied, but the interface and
failure behavior must be production-minded"). Swapping in a real LLM
provider means writing a new class that implements AIAdapter - nothing
in evaluation_service.py or the handlers would need to change.

Production-minded means, even in mock form:
- Output is validated against a Pydantic schema before it leaves this
  module (spec: "AI output must be treated as untrusted structured
  input. Validate JSON/schema...").
- Input size is capped (spec: "...impose token/size limits...").
- Every call checks the shared DeadlineGuard before "calling out" (spec:
  "...handle timeouts...") - reusing the same pattern from
  common/errors.py that every other external-dependency call in this
  project uses (S3, DynamoDB).
- A failure never propagates as a 500 for the whole evaluation - the
  caller gets a safe fallback with ai_available=False (spec: "...and
  fall back safely").
"""

from __future__ import annotations

import logging
from typing import Protocol

from common.errors import DeadlineGuard, HandlerTimeoutError
from models.evaluation import BusinessProfileSummary, StatementExtraction

logger = logging.getLogger("merchant_onboarding.adapters.ai")

MAX_DESCRIPTION_CHARS = 2000  # matches Business.business_description's own cap
MIN_SECONDS_REQUIRED_FOR_AI_CALL = 3.0  # safety margin under the DeadlineGuard budget


class AIAdapterError(Exception):
    """Raised when the AI adapter cannot produce a valid, timely result.
    Callers MUST catch this and fall back - never let it become a 500."""


class AIAdapter(Protocol):
    def summarize_business_profile(
        self, business_description: str, selected_industry: str, deadline: DeadlineGuard
    ) -> BusinessProfileSummary: ...

    def extract_statement(
        self, original_filename: str, deadline: DeadlineGuard
    ) -> StatementExtraction: ...


class MockAIAdapter:
    """Deterministic, keyword-based stand-in for a real LLM call. Every
    method still goes through the same timeout/validation/fallback
    discipline a real network-calling adapter would need, so swapping in
    a real provider later is a drop-in replacement, not a rewrite.
    """

    def summarize_business_profile(
        self, business_description: str, selected_industry: str, deadline: DeadlineGuard
    ) -> BusinessProfileSummary:
        if deadline.remaining() < MIN_SECONDS_REQUIRED_FOR_AI_CALL:
            raise AIAdapterError("Insufficient time budget remaining for AI business-profile call")

        text = (business_description or "")[:MAX_DESCRIPTION_CHARS].lower()

        try:
            sales_channel = self._infer_sales_channel(text)
            fulfillment_model = self._infer_fulfillment_model(text)
            recurring_behavior = self._infer_recurring_behavior(text)
            customer_type = self._infer_customer_type(text)

            summary_text = (
                f"Business self-describes as operating in '{selected_industry}'. "
                f"Sales channel appears to be {sales_channel}; fulfillment looks "
                f"{fulfillment_model}; billing pattern looks {recurring_behavior}; "
                f"customer base appears {customer_type}. This summary is generated "
                f"by keyword heuristics (mock adapter), not a real language model."
            )

            # Validate against the schema before returning - this is the
            # "treat AI output as untrusted structured input" check. A real
            # adapter parsing free-text LLM JSON output would fail here on
            # malformed responses; the mock cannot fail this in practice,
            # but the check stays in the code path so the pattern is real.
            return BusinessProfileSummary(
                sales_channel=sales_channel,
                fulfillment_model=fulfillment_model,
                recurring_behavior=recurring_behavior,
                customer_type=customer_type,
                summary_text=summary_text,
                ai_confidence=0.4,  # mock heuristics are deliberately low-confidence
                ai_available=True,
            )
        except Exception as exc:  # noqa: BLE001 - any adapter failure -> safe fallback
            logger.warning("AI business-profile summary failed, falling back", exc_info=exc)
            raise AIAdapterError(str(exc)) from exc

    def extract_statement(
        self, original_filename: str, deadline: DeadlineGuard
    ) -> StatementExtraction:
        """No real OCR/parsing available in this prototype. Returns a
        clearly-labeled low-confidence placeholder so the rate-analysis
        code path can be demonstrated end-to-end. A production adapter
        would replace this with real document AI / OCR extraction."""
        if deadline.remaining() < MIN_SECONDS_REQUIRED_FOR_AI_CALL:
            raise AIAdapterError("Insufficient time budget remaining for AI statement extraction")

        return StatementExtraction(
            processor="unknown (mock extraction - no OCR configured)",
            statement_period=None,
            monthly_volume_cents=None,
            monthly_transaction_count=None,
            discount_rate_percent=None,
            per_transaction_fee_cents=None,
            monthly_fee_cents=None,
            source="ai_mock_extraction",
            extraction_confidence=0.0,
        )

    @staticmethod
    def _infer_sales_channel(text: str) -> str:
        if any(w in text for w in ("online", "e-commerce", "ecommerce", "website", "app")):
            return "primarily online / card-not-present"
        if any(w in text for w in ("storefront", "in-store", "retail location", "walk-in")):
            return "primarily in-person / card-present"
        return "unspecified — not enough signal in the description"

    @staticmethod
    def _infer_fulfillment_model(text: str) -> str:
        if any(w in text for w in ("ship", "shipping", "delivery", "warehouse")):
            return "ship/deliver goods to customer"
        if any(w in text for w in ("service", "consulting", "appointment", "booking")):
            return "service-based, no physical goods shipped"
        return "unspecified — not enough signal in the description"

    @staticmethod
    def _infer_recurring_behavior(text: str) -> str:
        if any(w in text for w in ("subscription", "recurring", "membership", "monthly plan")):
            return "recurring/subscription billing indicated"
        return "one-time/transactional, no recurring billing indicated"

    @staticmethod
    def _infer_customer_type(text: str) -> str:
        if any(w in text for w in ("b2b", "business to business", "wholesale", "enterprise clients")):
            return "primarily business customers (B2B)"
        if any(w in text for w in ("consumer", "b2c", "retail customer", "shoppers")):
            return "primarily individual consumers (B2C)"
        return "unspecified — not enough signal in the description"