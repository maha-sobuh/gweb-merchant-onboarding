"""Unit tests for adapters/ai_adapter.py (MockAIAdapter)."""

from __future__ import annotations

from adapters.ai_adapter import AIAdapterError, MockAIAdapter
from common.errors import DeadlineGuard


def test_summarize_infers_online_sales_channel():
    ai = MockAIAdapter()
    deadline = DeadlineGuard(seconds=20)
    result = ai.summarize_business_profile(
        "We sell products through our website and mobile app", "Retail", deadline
    )
    assert result.ai_available is True
    assert "online" in result.sales_channel


def test_summarize_infers_recurring_billing():
    ai = MockAIAdapter()
    deadline = DeadlineGuard(seconds=20)
    result = ai.summarize_business_profile(
        "Customers pay a monthly subscription fee for access", "SaaS", deadline
    )
    assert "recurring" in result.recurring_behavior


def test_summarize_infers_b2b_customer_type():
    ai = MockAIAdapter()
    deadline = DeadlineGuard(seconds=20)
    result = ai.summarize_business_profile(
        "We provide wholesale supplies to enterprise clients", "Wholesale", deadline
    )
    assert "B2B" in result.customer_type


def test_summarize_returns_unspecified_when_no_signal():
    ai = MockAIAdapter()
    deadline = DeadlineGuard(seconds=20)
    result = ai.summarize_business_profile("We do things and stuff", "General", deadline)
    assert "unspecified" in result.sales_channel


def test_summarize_confidence_is_deliberately_low():
    """Mock heuristics should never claim high confidence - real AI adapter
    would report its own confidence, but this mock must not overstate it."""
    ai = MockAIAdapter()
    deadline = DeadlineGuard(seconds=20)
    result = ai.summarize_business_profile("online store", "Retail", deadline)
    assert result.ai_confidence <= 0.5


def test_summarize_raises_when_insufficient_time_budget():
    ai = MockAIAdapter()
    deadline = DeadlineGuard(seconds=1)  # below MIN_SECONDS_REQUIRED_FOR_AI_CALL
    try:
        ai.summarize_business_profile("test description", "test", deadline)
        assert False, "expected AIAdapterError"
    except AIAdapterError:
        pass


def test_extract_statement_returns_labeled_low_confidence_placeholder():
    ai = MockAIAdapter()
    deadline = DeadlineGuard(seconds=20)
    result = ai.extract_statement("statement.pdf", deadline)
    assert result.source == "ai_mock_extraction"
    assert result.extraction_confidence == 0.0
    assert result.monthly_volume_cents is None  # no real OCR — honestly empty


def test_extract_statement_raises_when_insufficient_time_budget():
    ai = MockAIAdapter()
    deadline = DeadlineGuard(seconds=1)
    try:
        ai.extract_statement("statement.pdf", deadline)
        assert False, "expected AIAdapterError"
    except AIAdapterError:
        pass