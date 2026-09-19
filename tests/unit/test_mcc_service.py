"""Unit tests for services/mcc_service.py (deterministic MCC search/suggest/risk)."""

from __future__ import annotations

from models.mcc import RiskTier
from services import mcc_service


def test_search_by_code_prefix():
    results = mcc_service.search("601")
    codes = {r.code for r in results}
    assert "6012" in codes
    assert "6051" not in codes  # doesn't start with 601


def test_search_by_keyword():
    results = mcc_service.search("restaurant")
    codes = {r.code for r in results}
    assert "5812" in codes


def test_search_empty_query_returns_catalog_slice():
    results = mcc_service.search("")
    assert len(results) > 0


def test_get_entry_returns_none_for_unknown_code():
    assert mcc_service.get_entry("0000") is None


def test_get_entry_returns_entry_for_known_code():
    entry = mcc_service.get_entry("5812")
    assert entry is not None
    assert "restaurant" in entry.description.lower() or "eating" in entry.description.lower()


def test_suggest_matches_restaurant_description():
    proposals = mcc_service.suggest(
        business_description="We run a small family restaurant serving dinner",
        selected_industry="Food and dining",
    )
    assert len(proposals) > 0
    assert proposals[0].code == "5812"
    assert proposals[0].confidence > 0


def test_suggest_returns_low_confidence_fallback_when_no_match():
    proposals = mcc_service.suggest(
        business_description="xzxzxzxz qqqqq nonsense text",
        selected_industry="zzzzz",
    )
    assert len(proposals) == 1
    assert proposals[0].confidence == 0.0


def test_risk_tier_for_enhanced_review_code():
    tier, reason = mcc_service.risk_tier_for("6211")
    assert tier == RiskTier.ENHANCED_REVIEW
    assert reason is not None


def test_risk_tier_for_standard_code():
    tier, reason = mcc_service.risk_tier_for("5812")
    assert tier == RiskTier.STANDARD
    assert reason is None


def test_risk_tier_provider_override_takes_precedence():
    base_tier, _ = mcc_service.risk_tier_for("5933")
    override_tier, override_reason = mcc_service.risk_tier_for("5933", provider="acme_processor")

    assert base_tier == RiskTier.ENHANCED_REVIEW
    assert override_tier == RiskTier.RESTRICTED  # acme_processor is stricter
    assert "acme_processor" in override_reason or "pawn" in override_reason.lower()


def test_is_ambiguous_true_for_low_confidence():
    proposals = mcc_service.suggest(business_description="xzxzxzxz", selected_industry="zzzzz")
    assert mcc_service.is_ambiguous(proposals) is True


def test_is_ambiguous_false_for_clear_match():
    proposals = mcc_service.suggest(
        business_description="We are a securities broker dealer trading platform",
        selected_industry="Financial services",
    )
    assert mcc_service.is_ambiguous(proposals) is False


def test_is_sensitive_true_for_enhanced_review_and_restricted():
    assert mcc_service.is_sensitive(RiskTier.ENHANCED_REVIEW) is True
    assert mcc_service.is_sensitive(RiskTier.RESTRICTED) is True
    assert mcc_service.is_sensitive(RiskTier.STANDARD) is False



def test_provider_named_like_a_comment_key_does_not_crash():
    from services import mcc_service

    tier, _reason = mcc_service.risk_tier_for("5812", provider="_comment")
    assert tier.value == "STANDARD"