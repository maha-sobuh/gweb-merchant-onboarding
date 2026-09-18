"""MCC catalog search + suggestion + risk policy lookup.

Deterministic, rule-based (keyword matching) — NOT the AI evaluation layer
(that is a separate adapter, spec section 6). This module never imports
boto3; it only reads the two static JSON files bundled under src/data/.

Catalog source: Visa Merchant Data Standards Manual / Mastercard Quick
Reference Booklet code definitions (see README "MCC Data Source" section
for the refresh process, per spec §5: "Include a script or documented
process to refresh the dataset").
"""

from __future__ import annotations

import json
from pathlib import Path

from models.mcc import MccEntry, ProposedMcc, RiskTier

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_MAX_SUGGESTIONS = 3


def _load_catalog() -> list[MccEntry]:
    raw = json.loads((_DATA_DIR / "mcc_catalog.json").read_text(encoding="utf-8"))
    return [MccEntry(**row) for row in raw]


def _load_risk_policy() -> dict:
    return json.loads((_DATA_DIR / "risk_policy.json").read_text(encoding="utf-8"))


# Loaded once per Lambda cold start (module-level cache), not per invocation —
# these are small static files, re-reading them on every request would be
# wasted I/O inside the 45-second budget for no benefit.
_CATALOG: list[MccEntry] = _load_catalog()
_RISK_POLICY: dict = _load_risk_policy()
_CATALOG_BY_CODE: dict[str, MccEntry] = {entry.code: entry for entry in _CATALOG}


def search(query: str, limit: int = 20) -> list[MccEntry]:
    """GET /mcc?query=... — matches on code prefix or description/keyword substring."""
    if not query:
        return _CATALOG[:limit]
    needle = query.strip().lower()
    if needle.isdigit():
        results = [e for e in _CATALOG if e.code.startswith(needle)]
    else:
        results = [
            e
            for e in _CATALOG
            if needle in e.description.lower() or any(needle in kw for kw in e.keywords)
        ]
    return results[:limit]


def get_entry(code: str) -> MccEntry | None:
    return _CATALOG_BY_CODE.get(code)


def risk_tier_for(code: str, provider: str | None = None) -> tuple[RiskTier, str | None]:
    """Provider override wins over the base policy when present (spec §5:
    "Support provider-specific overrides, because acquirers/processors can
    apply different underwriting policies to the same MCC")."""
    if provider:
        override = _RISK_POLICY.get("provider_overrides", {}).get(provider, {}).get(code)
        if override:
            return RiskTier(override["tier"]), override["reason"]

    base = _RISK_POLICY.get("base_policy", {}).get(code)
    if base:
        return RiskTier(base["tier"]), base["reason"]

    return RiskTier.STANDARD, None


def suggest(
    business_description: str, selected_industry: str, provider: str | None = None
) -> list[ProposedMcc]:
    """Score every catalog entry by keyword overlap with the applicant's
    free-text description + selected industry. Deterministic and fully
    explainable — every suggestion states which words matched.
    """
    haystack = f"{selected_industry} {business_description}".lower()

    scored: list[tuple[int, MccEntry, list[str]]] = []
    for entry in _CATALOG:
        matched = [kw for kw in entry.keywords if kw in haystack]
        # Description words count too, but weighted less than curated keywords.
        description_hit = 1 if entry.description.lower() in haystack else 0
        score = len(matched) * 2 + description_hit
        if score > 0:
            scored.append((score, entry, matched))

    scored.sort(key=lambda row: (-row[0], row[1].code))
    top = scored[:_MAX_SUGGESTIONS]

    if not top:
        # No keyword match at all — do not fabricate a guess. Return the
        # single most generic catalog entry as a low-confidence placeholder
        # and let the ambiguity flag force manual review.
        fallback = _CATALOG_BY_CODE.get("5999")
        if fallback:
            tier, reason = risk_tier_for(fallback.code, provider)
            return [
                ProposedMcc(
                    code=fallback.code,
                    description=fallback.description,
                    confidence=0.0,
                    reason="No keyword match against the business description or industry; manual classification recommended",
                    risk_tier=tier,
                    risk_reason=reason,
                )
            ]
        return []

    max_score = top[0][0]
    results = []
    for score, entry, matched in top:
        tier, reason = risk_tier_for(entry.code, provider)
        confidence = round(min(1.0, score / max(max_score, 1)), 2)
        results.append(
            ProposedMcc(
                code=entry.code,
                description=entry.description,
                confidence=confidence,
                reason=(
                    f"Matched keywords: {', '.join(matched)}"
                    if matched
                    else "Matched business description text"
                ),
                risk_tier=tier,
                risk_reason=reason,
            )
        )
    return results


def is_ambiguous(proposals: list[ProposedMcc]) -> bool:
    """Flags for manual review per spec §5: "ambiguous or sensitive
    categories must be marked for manual review." Ambiguous = top
    suggestion has low confidence, or the top two are close enough that
    picking one over the other isn't clearly justified by keyword overlap.
    """
    if not proposals:
        return True
    if proposals[0].confidence < 0.5:
        return True
    if len(proposals) > 1 and (proposals[0].confidence - proposals[1].confidence) < 0.15:
        return True
    return False


def is_sensitive(risk_tier: RiskTier) -> bool:
    return risk_tier in (RiskTier.ENHANCED_REVIEW, RiskTier.RESTRICTED)