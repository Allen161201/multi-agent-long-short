"""Phase 2 Step 5 — PM rationale spot-check.

Reads the 5 PM decision JSONs written by _phase2_mini_smoke.py and
checks whether each rationale mentions at least one of the 11 alt-data
sources by name. The match is keyword-based: a hit on any keyword
counts as that source's name being referenced.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAYS = ["2026-04-23", "2026-04-24", "2026-04-27", "2026-04-28", "2026-04-29"]
DECISION_ROOT = ROOT / "data" / "decisions" / "phase2_mini_smoke"

ALT_DATA_KEYWORDS = {
    "macro_regime": [
        "macro regime", "macro_regime", "regime",
        "overheat", "normal", "restrictive", "crisis", "poor",
        "strengthening", "fred",
    ],
    "fmp_news / news_event_summary": [
        "news", "press release", "article", "headline", "publication",
        "narrative", "catalyst",
    ],
    "corporate_calendar": [
        "corporate calendar", "earnings date", "ex-dividend",
        "corporate event", "upcoming earnings",
    ],
    "sec_8k_fulltext": [
        "8-k", "8k", "sec filing", "sec_8k", "form 8", "filing",
    ],
    "alternative_data_features": [
        "alternative data", "alt-data", "alt_data", "alt data",
    ],
    "network_effect": [
        "network effect", "network_effect", "network-effect",
        "platform", "two-sided", "ecosystem",
    ],
    "sentiment_community_ownership": [
        "sentiment", "community", "ownership", "institutional",
        "reddit", "twitter",
    ],
    "fundamental_snapshot (anchor)": [
        "eps", "fcf", "operating margin", "operating_margin",
        "debt-to-equity", "d/e", "fundamental",
    ],
    "valuation_snapshot (anchor)": [
        "pe ", "p/e", "pb ", "p/b", "ev/ebitda", "valuation",
    ],
}


def main() -> int:
    print("=== Phase 2 Step 5 — PM rationale alt-data spot-check ===\n")
    any_alt_data_per_day: list[bool] = []
    for d in DAYS:
        p = DECISION_ROOT / d / "16-15" / "AAPL_quality_long_decision.json"
        if not p.exists():
            print(f"  {d}: NO DECISION FILE")
            any_alt_data_per_day.append(False)
            continue
        dec = json.loads(p.read_text(encoding="utf-8"))
        final = dec.get("final_decision") or {}
        # Try every plausible rationale field
        rat_pieces = []
        for k in (
            "reason", "audit_rationale", "rationale", "reasoning_summary",
            "commentary", "pm_summary", "decision_rationale",
            "final_rationale",
        ):
            v = final.get(k)
            if v:
                rat_pieces.append(str(v))
        rat = " ".join(rat_pieces).lower()
        decision = (
            final.get("decision")
            or final.get("recommended_action")
            or "n/a"
        )
        print(f"  {d} (decision={decision}, rationale {len(rat)} chars)")
        if not rat:
            print("    <no rationale text>")
            any_alt_data_per_day.append(False)
            continue
        hits: list[tuple[str, str]] = []
        for src, kws in ALT_DATA_KEYWORDS.items():
            for k in kws:
                if k in rat:
                    hits.append((src, k))
                    break
        # Distinguish anchor sources from true alt-data
        anchor_sources = {
            "fundamental_snapshot (anchor)",
            "valuation_snapshot (anchor)",
        }
        true_alt_hits = [h for h in hits if h[0] not in anchor_sources]
        any_alt_data_per_day.append(bool(true_alt_hits))
        if hits:
            for src, k in hits:
                marker = "  (anchor)" if src in anchor_sources else "  (alt-data)"
                print(f"    {marker}  {src} matched on {k!r}")
        else:
            print("    <no source keyword matched>")

    total_days = len(DAYS)
    days_with_alt_data = sum(any_alt_data_per_day)
    print(f"\n  Days with at least one true alt-data source mention: "
          f"{days_with_alt_data}/{total_days}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
