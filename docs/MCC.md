# MCC Classification and Risk Policy

The system keeps two things apart on purpose:

- **Taxonomy** (what a business is): the MCC catalog, `src/data/mcc_catalog.json`.
- **Policy** (what GWEB or a provider does about it): the risk policy, `src/data/risk_policy.json`.

Changing a risk rule never requires touching the catalog, and the other way round. Neither file makes an approval decision: MCC classification only produces suggestions and review flags, and nothing in the system auto-approves a merchant.

## Data source

- The catalog has **35 entries** in the shape `{"code", "description", "keywords"}`.
- Codes and descriptions follow the published Visa Merchant Data Standards Manual and Mastercard code definitions (the reference the assessment points to; `mcc_service.py` states the same). Some descriptions are abridged relative to the manual's exact wording.
- The catalog is a **curated subset**, not the full list of roughly 800 codes.
- It was created with AI assistance and entered as a static file. **There is no import script, and per-entry provenance was not recorded.** The `keywords` are hand-written search aids; they are not part of any network manual.
- Treat the catalog as seed data. Verify entries against the current manual before production use (steps below).

## Search: `GET /mcc?query=`

The catalog is loaded once per Lambda cold start and searched in memory.

| Query | Behavior |
|-------|----------|
| empty | first 20 entries |
| digits only | entries whose code starts with those digits |
| text | entries whose description or any keyword contains the text (case-insensitive) |

Results are capped at 20.

## Proposal workflow: `POST /applications/{id}/classify`

1. The applicant **self-selects an industry** (`selected_industry`) and describes the business (`business_description`).
2. The system builds a lowercase search text from both and scores every catalog entry:
   `score = 2 x (number of the entry's keywords found in the text) + 1 (if the full description string appears in the text)`.
3. Entries with a score above zero are sorted by score (then by code), and the **top 3** are returned, each with:
   - `confidence`: the entry's score divided by the best score, rounded to 2 decimals. It is a **relative ranking, not a calibrated probability**, so the best match always shows 1.0 when anything matched.
   - `reason`: which keywords matched (`Matched keywords: restaurant`).
   - `riskTier` and `riskReason` from the risk policy.
4. If nothing matches, the system does **not** guess. It returns the generic code 5999 with confidence 0.0 and a note recommending manual classification.
5. The applicant **confirms or corrects** by calling the endpoint again with `confirmed_mcc`, which must be a code in the catalog.
6. Both the applicant's selection and the system proposals are stored in the application's `MCC_CLASSIFICATION` item (`selectedIndustry`, `businessDescription`, `proposedMccs`, `confirmedMcc`), so a reviewer can inspect mismatches.

### Manual review

`manualReviewRequired` is true when either condition holds:

- **Sensitive tier:** the confirmed code (or the top proposal, if none is confirmed yet) is `ENHANCED_REVIEW` or `RESTRICTED`.
- **Ambiguous proposals:** the top confidence is below 0.5, or the top two confidences are within 0.15 of each other, or there are no proposals.

This is computed independently of what the applicant confirms.

### Mismatch signal

During evaluation, if the confirmed MCC differs from the top proposal, a `MCC_DESCRIPTION_MISMATCH` risk signal is raised, citing `classification.confirmed_mcc` and `business.business_description`. If a sensitive MCC is confirmed without a `BUSINESS_LICENSE` document, `REGULATED_MCC_WITHOUT_LICENSE_EVIDENCE` is raised.

## Risk policy

Tiers are GWEB's own language (`STANDARD`, `ENHANCED_REVIEW`, `RESTRICTED`); they are not a claim that an MCC is universally high risk. Any code not listed defaults to `STANDARD`.

`src/data/risk_policy.json` has two sections:

**`base_policy`** (GWEB's rules):

| MCC | Tier | Reason |
|-----|------|--------|
| 6012 | ENHANCED_REVIEW | Financial institution merchandise and services (called out by Mastercard for specialized handling) |
| 6051 | ENHANCED_REVIEW | Quasi-cash (currency exchange, money orders, crypto) |
| 6211 | ENHANCED_REVIEW | Securities brokers and dealers |
| 6300 | ENHANCED_REVIEW | Insurance sales and underwriting |
| 5933 | ENHANCED_REVIEW | Pawn shops |
| 7995 | RESTRICTED | Betting and gambling |

**`provider_overrides`** (per acquirer or processor): the same MCC can be treated differently by different providers. Pass `provider` in the classify request and the override wins over the base policy. Shipped example:

```json
"provider_overrides": {
  "acme_processor": {
    "5933": {"tier": "RESTRICTED", "reason": "acme_processor does not underwrite pawn/salvage merchants"}
  }
}
```

So `POST /classify` for a pawn shop returns `ENHANCED_REVIEW` by default and `RESTRICTED` with `"provider": "acme_processor"`.

A policy entry only takes effect if its code exists in the catalog, because `/classify` rejects codes that are not in it. `scripts/validate_mcc_catalog.py` checks this.

### Changing a rule

Edit `risk_policy.json` (for example change a tier, or add a provider block), run the validator and the tests, and redeploy. The catalog is untouched.

## Refreshing the dataset

The files are packaged with the Lambda code, so a refresh is a code change plus a redeploy.

1. Get the current Visa Merchant Data Standards Manual (linked in the assessment) and the current Mastercard documentation.
2. Extract the MCC code and description table (for example with `pdftotext`, or by hand for a small subset).
3. Update `src/data/mcc_catalog.json`: add or correct `code` and `description`. Keep the existing `keywords` for codes that stay, and write new lowercase keywords for new codes.
4. Update `src/data/risk_policy.json` if policy changed.
5. Run `python scripts/validate_mcc_catalog.py`.
6. Run `pytest`.
7. Commit and redeploy.

## Validation script

```bash
python scripts/validate_mcc_catalog.py
```

It checks that codes are unique four-digit strings, every entry has a description and lowercase keywords (matching is done against lowercased text, so an uppercase keyword would never match), and that every code in the risk policy exists in the catalog with a valid tier.

## Limitations

- 35-code subset, and the import was not scripted.
- Keyword substring matching only: no synonyms, stemming or fuzzy matching, so a description with unusual wording can miss.
- Confidence is relative to the best match, not a probability.
- The applicant chooses `selected_industry` as free text; it is used for matching, not validated against a fixed list.
