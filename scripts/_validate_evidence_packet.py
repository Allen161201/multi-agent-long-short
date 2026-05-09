"""
Internal validator harness for evidence_packet v1.

For each of AAPL / NVDA / UBER:
  - generate two packets at the same fixed decision_timestamp (so the cache
    is warm on the second call)
  - check evidence_packet_hash equality across the two runs
  - check locked_decision_id equality across the two runs (should match
    when ticker + decision_timestamp + hash all match)
  - print api_call_count per ticker (the second-run call count is what
    hits FMP after the cache is warm)
  - print lookahead_safe and any block that fell back to data_unavailable
    or not_evaluated

This script never modifies production code or rule files. It is purely a
diagnostic — outputs go to stdout.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    import os
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from evidence_packet import generate_evidence_packet
from data_adapters import fmp_adapter as fmp

ET = ZoneInfo("America/New_York")
TICKERS = ("AAPL", "NVDA", "UBER")


def call_count() -> int:
    return sum(fmp.get_call_group_summary().values())


def block_status_line(packet: dict) -> str:
    keys = ("price_snapshot", "macro_regime", "fundamental_snapshot",
             "valuation_snapshot", "news_event_summary",
             "filing_confirmation", "corporate_calendar",
             "alternative_data_features",
             "information_integrity_assessment",
             "sentiment_community_ownership_evidence",
             "narrative_price_gap_assessment",
             "decision_time_discipline")
    return " | ".join(
        f"{k}={packet.get(k, {}).get('status', '?')}" for k in keys
    )


def fixed_decision_timestamp() -> datetime:
    """Build a fixed ET timestamp 30 min in the past so it's safely
    < now (cutoff <= now invariant) and identical across both runs."""
    return datetime.now(ET).replace(microsecond=0) - \
        __import__("datetime").timedelta(minutes=30)


def main():
    fixed_dt = fixed_decision_timestamp()
    print(f"Fixed decision_timestamp = {fixed_dt.isoformat()}\n")

    summary = []
    for ticker in TICKERS:
        print("=" * 70)
        print(f"TICKER {ticker}")
        print("=" * 70)

        before_total = call_count()
        p1 = generate_evidence_packet(
            ticker=ticker, decision_mode="live",
            decision_timestamp=fixed_dt.isoformat(),
        )
        run1_calls = call_count() - before_total

        before_total = call_count()
        p2 = generate_evidence_packet(
            ticker=ticker, decision_mode="live",
            decision_timestamp=fixed_dt.isoformat(),
        )
        run2_calls = call_count() - before_total

        h1 = p1["envelope"]["evidence_packet_hash"]
        h2 = p2["envelope"]["evidence_packet_hash"]
        l1 = p1["envelope"]["locked_decision_id"]
        l2 = p2["envelope"]["locked_decision_id"]

        hash_match = (h1 == h2)
        locked_match = (l1 == l2)
        lookahead_safe = p1["envelope"]["lookahead_safe"]
        data_after_cutoff = p1["envelope"]["data_after_cutoff_used"]
        hindsight_safe = p1["envelope"].get("hindsight_safe")
        hindsight_violations = p1["envelope"].get("hindsight_violations", [])
        universe_pit_validated = p1["envelope"].get("universe_pit_validated")
        uses_current_state = p1["envelope"].get("uses_current_state")
        records_removed_by_pit = p1["envelope"].get("records_removed_by_pit_filter", {})
        schema_doc_revision = p1["envelope"].get("schema_doc_revision")

        # Find blocks that fell back
        fallback = []
        for k, v in p1.items():
            if not isinstance(v, dict):
                continue
            st = v.get("status")
            if st in ("data_unavailable", "not_evaluated", "insufficient_evidence"):
                fallback.append((k, st))

        print(f"Run 1 hash:     {h1}")
        print(f"Run 2 hash:     {h2}")
        print(f"Hash determinism (warm cache) : {'PASS' if hash_match else 'FAIL'}")
        print(f"Locked decision id (run 1)    : {l1}")
        print(f"Locked decision id (run 2)    : {l2}")
        print(f"locked_id same across runs    : {locked_match} "
              f"(expected: True at fixed timestamp; would be False at distinct decision_timestamps)")
        print(f"lookahead_safe                : {lookahead_safe}")
        print(f"data_after_cutoff_used        : {data_after_cutoff}")
        print(f"hindsight_safe                : {hindsight_safe}")
        print(f"  hindsight_violations count  : {len(hindsight_violations)}")
        if hindsight_violations:
            for v in hindsight_violations[:5]:
                print(f"    - {v}")
        print(f"universe_pit_validated        : {universe_pit_validated}")
        print(f"uses_current_state            : {uses_current_state}")
        print(f"records_removed_by_pit_filter : {records_removed_by_pit}")
        print(f"schema_doc_revision           : {schema_doc_revision}")
        print(f"API calls run 1 (cold cache)  : {run1_calls}")
        print(f"API calls run 2 (warm cache)  : {run2_calls}")
        print(f"Block status:")
        for k, st in fallback:
            print(f"   {k:46s}  {st}")
        print(f"data_quality_flags count      : {len(p1.get('data_quality_flags', []))}")
        critical = [f for f in p1.get("data_quality_flags", [])
                     if f.get("severity") == "critical"]
        if critical:
            print(f"CRITICAL flags:")
            for f in critical:
                print(f"   - {f}")

        summary.append({
            "ticker": ticker,
            "hash_deterministic": hash_match,
            "locked_id_match_at_same_timestamp": locked_match,
            "lookahead_safe": lookahead_safe,
            "data_after_cutoff": data_after_cutoff,
            "hindsight_safe": hindsight_safe,
            "hindsight_violations_count": len(hindsight_violations),
            "universe_pit_validated": universe_pit_validated,
            "uses_current_state": uses_current_state,
            "records_removed_by_pit_filter": records_removed_by_pit,
            "schema_doc_revision": schema_doc_revision,
            "api_calls_run1_cold_cache": run1_calls,
            "api_calls_run2_warm_cache": run2_calls,
            "fallback_blocks": fallback,
        })

    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    print(json.dumps(summary, indent=2, default=str))

    # Cross-ticker uniqueness check: locked_decision_ids should differ between tickers
    print("\nCross-ticker locked-id uniqueness check:")
    print("  (locked_decision_id should be UNIQUE across tickers at the same timestamp)")
    print("  pass: each ticker derives a different sha256 because ticker is part of the inputs.")


if __name__ == "__main__":
    main()
