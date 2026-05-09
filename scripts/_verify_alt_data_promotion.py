"""
Throwaway: verify alternative_data_features status promotion at the
packet layer with full alt-data opt-in. NO LLM CALLS — just builds the
packet and prints what the wiring layer landed.

Will be deleted after the verification task closes.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

TICKER = "AAPL"
CUTOFF = "2026-04-29T16:15:00-04:00"
ALL_LIVE = (
    "wikipedia_pageviews",
    "sec_edgar",
    "github_public",
    "fmp_sentiment",
    "sec_13f",
    "sec_form4",
    "sec_def14a",
    "sec_8k_fulltext",
    "github_commit_messages",
)


def main() -> int:
    from src.evidence_packet import generate_evidence_packet, PITViolationError

    print("=== Packet-layer verification (no LLM) ===")
    print(f"  ticker        : {TICKER}")
    print(f"  cutoff        : {CUTOFF}")
    print(f"  live_adapters : {ALL_LIVE}")
    print()

    try:
        packet = generate_evidence_packet(
            ticker=TICKER,
            decision_timestamp=CUTOFF,
            strict_pit_mode=True,
            live_adapters=ALL_LIVE,
        )
    except PITViolationError as e:
        print(f"  ABORT  PITViolationError: {e}")
        return 2

    env = packet.get("envelope", {})
    print(f"  packet hash   : {env.get('evidence_packet_hash')}")
    print()

    b = packet["alternative_data_features"]
    print(f"  alternative_data_features.status   = {b.get('status')!r}")
    print(f"  alternative_data_features.source   = {b.get('source')!r}")
    print(f"  alternative_data_features.as_of    = {b.get('as_of')!r}")
    print(f"  reason (first 140) = {(b.get('reason') or '')[:140]!r}")
    print()

    # Manifest summary
    manifest = packet.get("alt_data_manifest") or {}
    calls = manifest.get("calls") or []
    print(f"  manifest.calls : {len(calls)} entries")
    for call in calls:
        sid = call.get("source_id")
        called = call.get("called")
        rows = call.get("returned_rows")
        skip = call.get("skip_reason")
        status = call.get("extraction_status") or call.get("status")
        flag = "ok" if (called and rows) else ("skip" if not called else "0rows")
        print(f"    [{flag:5s}] {sid:25s} called={called} returned_rows={rows} status={status} skip={skip}")
    print()

    # Sub-block presence
    sub_keys = list(b.keys())
    print(f"  sub-block keys : {sub_keys}")
    if "wikipedia_pageviews" in b:
        wiki = b["wikipedia_pageviews"]
        print(f"    wikipedia_pageviews.status = {wiki.get('status') if isinstance(wiki, dict) else type(wiki).__name__}")
    if "github_public_metrics" in b:
        gh = b["github_public_metrics"]
        print(f"    github_public_metrics.status = {gh.get('status') if isinstance(gh, dict) else type(gh).__name__}")
    if "github_commit_narrative" in b:
        ghc = b["github_commit_narrative"]
        print(f"    github_commit_narrative.status = {ghc.get('status') if isinstance(ghc, dict) else type(ghc).__name__}")

    # Verdict
    print()
    if b.get("status") == "ok":
        print("  ✅ PROMOTION SUCCEEDED — alternative_data_features.status = 'ok'")
        return 0
    elif b.get("status") == "not_evaluated":
        print("  ❌ PROMOTION FAILED — block remained 'not_evaluated' despite live opt-in")
        print("     Inspect the manifest above to diagnose why no source delivered rows.")
        return 1
    else:
        print(f"  ⚠️  Block landed at status={b.get('status')!r} — neither ok nor not_evaluated")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(99)
