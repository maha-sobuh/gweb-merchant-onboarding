"""Validate the packaged MCC catalog and risk policy.

Run from the repository root:
    python scripts/validate_mcc_catalog.py

Exits with a non-zero status if any problem is found, so it can be used in CI.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "data"
VALID_TIERS = {"STANDARD", "ENHANCED_REVIEW", "RESTRICTED"}


def main() -> int:
    errors: list[str] = []

    catalog = json.loads((DATA_DIR / "mcc_catalog.json").read_text(encoding="utf-8"))
    policy = json.loads((DATA_DIR / "risk_policy.json").read_text(encoding="utf-8"))

    codes: set[str] = set()
    for row in catalog:
        code = str(row.get("code", ""))
        if not re.fullmatch(r"\d{4}", code):
            errors.append(f"invalid code (must be 4 digits): {code!r}")
        if code in codes:
            errors.append(f"duplicate code: {code}")
        codes.add(code)
        if not row.get("description"):
            errors.append(f"{code}: missing description")
        keywords = row.get("keywords", [])
        if not keywords:
            errors.append(f"{code}: no keywords")
        for keyword in keywords:
            if keyword != keyword.lower():
                errors.append(f"{code}: keyword must be lowercase: {keyword!r}")

    def check_rule(where: str, code: str, rule: object) -> None:
        if code not in codes:
            errors.append(f"{where}: policy code {code} is not in the catalog")
        if not isinstance(rule, dict):
            errors.append(f"{where}/{code}: rule must be an object")
            return
        if rule.get("tier") not in VALID_TIERS:
            errors.append(f"{where}/{code}: invalid tier {rule.get('tier')!r}")
        if not rule.get("reason"):
            errors.append(f"{where}/{code}: missing reason")

    base_rules = {k: v for k, v in policy.get("base_policy", {}).items() if not k.startswith("_")}
    for code, rule in base_rules.items():
        check_rule("base_policy", code, rule)

    override_count = 0
    for provider, rules in policy.get("provider_overrides", {}).items():
        if provider.startswith("_"):
            continue
        if not isinstance(rules, dict):
            errors.append(f"provider_overrides/{provider}: must be an object")
            continue
        for code, rule in rules.items():
            override_count += 1
            check_rule(f"provider_overrides/{provider}", code, rule)

    print(f"Catalog entries : {len(catalog)}")
    print(f"Base policy     : {len(base_rules)} rules")
    print(f"Provider rules  : {override_count} overrides")

    if errors:
        print()
        print("PROBLEMS FOUND:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("OK: catalog and risk policy are consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
