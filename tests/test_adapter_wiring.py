"""
D7 wiring tests — verify the new live_adapters parameter behaves
correctly without disturbing the default packet hash.

Done-criteria coverage:
  1. Default call (live_adapters=None) is byte-identical to pre-D
     output (already proven by Step A6 regression matrix; we re-check
     here so a future regression is caught at this layer).
  2. Generate AAPL with live_adapters=True and all blocks enabled →
     manifest shows 5 calls (3 alt-data + 2 OpenCLI), each with
     called=True. (Reddit excluded 2026-04-28 per RULES.md §10.13.)
  3. Generate AAPL with live_adapters=True but enabled_blocks excluding
     alternative_data_features → only adapters whose block is still
     enabled run; the rest record called=False with skip_reason
     indicating the block is disabled.
  4. Hash differs between full and toggled packets (per R-PACKET-04).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Force stub mode so no network is touched.
os.environ["STUB_MODE"] = "true"

from src.evidence_packet.generator import generate_evidence_packet  # noqa: E402
from src.evidence_packet.schema import BlockKey  # noqa: E402

DECISION_TS = "2026-04-27T16:00:00-04:00"


def test_default_call_byte_identical():
    """Without live_adapters, two consecutive runs produce the same
    hash and no alt_data_manifest is added."""
    p1 = generate_evidence_packet(ticker="AAPL", decision_timestamp=DECISION_TS)
    p2 = generate_evidence_packet(ticker="AAPL", decision_timestamp=DECISION_TS)
    assert (p1["envelope"]["evidence_packet_hash"]
            == p2["envelope"]["evidence_packet_hash"])
    assert "alt_data_manifest" not in p1


def test_full_live_adapters_five_calls_all_blocks_enabled():
    p = generate_evidence_packet(
        ticker="AAPL", decision_timestamp=DECISION_TS,
        live_adapters=True,
    )
    manifest = p.get("alt_data_manifest")
    assert isinstance(manifest, dict)
    calls_called = [c for c in manifest["calls"] if c.get("called")]
    assert len(calls_called) == 5, (
        f"expected 5 adapter calls (3 alt-data + 2 OpenCLI), got "
        f"{len(calls_called)}: {[c['source_id'] for c in calls_called]}"
    )
    sids = {c["source_id"] for c in calls_called}
    expected = {
        "wikipedia_pageviews", "sec_edgar", "github_public",
        "sec_8k_fulltext", "github_commit_messages",
    }
    assert sids == expected, f"got {sids}, expected {expected}"
    # Reddit must NOT appear in the manifest at all.
    assert "reddit_public" not in {c["source_id"] for c in manifest["calls"]}


def test_toggled_blocks_skip_relevant_adapters():
    # Disable alternative_data_features.
    enabled = {
        BlockKey.PRICE_SNAPSHOT, BlockKey.MACRO_REGIME,
        BlockKey.FUNDAMENTAL_SNAPSHOT, BlockKey.VALUATION_SNAPSHOT,
        BlockKey.NEWS_EVENT_SUMMARY, BlockKey.CORPORATE_CALENDAR,
        BlockKey.INFORMATION_INTEGRITY,
        BlockKey.SENTIMENT_OWNERSHIP,
        BlockKey.DECISION_TIME_DISCIPLINE,
        BlockKey.FILING_CONFIRMATION,
        BlockKey.NARRATIVE_PRICE_GAP,
    }
    p = generate_evidence_packet(
        ticker="AAPL", decision_timestamp=DECISION_TS,
        enabled_blocks=enabled, live_adapters=True,
    )
    manifest = p["alt_data_manifest"]
    calls = {c["source_id"]: c for c in manifest["calls"]}
    # alt-data adapters whose block is alternative_data_features:
    assert calls["wikipedia_pageviews"]["called"] is False
    assert calls["github_public"]["called"] is False
    # sec_edgar targets filing_confirmation (still enabled):
    assert calls["sec_edgar"]["called"] is True
    # OpenCLI sec_8k_fulltext targets filing_confirmation (still enabled):
    assert calls["sec_8k_fulltext"]["called"] is True
    # OpenCLI github_commit_messages targets alternative_data_features (disabled):
    assert calls["github_commit_messages"]["called"] is False
    assert "block_alternative_data_features_disabled" \
        in calls["github_commit_messages"]["skip_reason"]


def test_full_vs_toggled_hash_differs():
    p_full = generate_evidence_packet(
        ticker="AAPL", decision_timestamp=DECISION_TS, live_adapters=True,
    )
    enabled = {
        BlockKey.PRICE_SNAPSHOT, BlockKey.MACRO_REGIME,
        BlockKey.FUNDAMENTAL_SNAPSHOT, BlockKey.VALUATION_SNAPSHOT,
        BlockKey.NEWS_EVENT_SUMMARY, BlockKey.CORPORATE_CALENDAR,
        BlockKey.INFORMATION_INTEGRITY,
        BlockKey.SENTIMENT_OWNERSHIP,
        BlockKey.DECISION_TIME_DISCIPLINE,
        BlockKey.FILING_CONFIRMATION,
        BlockKey.NARRATIVE_PRICE_GAP,
    }
    p_partial = generate_evidence_packet(
        ticker="AAPL", decision_timestamp=DECISION_TS,
        enabled_blocks=enabled, live_adapters=True,
    )
    h_full = p_full["envelope"]["evidence_packet_hash"]
    h_partial = p_partial["envelope"]["evidence_packet_hash"]
    assert h_full != h_partial, (
        f"hashes must differ between full and toggled packets per R-PACKET-04; "
        f"got {h_full} == {h_partial}"
    )


def test_no_adapter_call_packet_does_not_contain_manifest():
    p_default = generate_evidence_packet(
        ticker="AAPL", decision_timestamp=DECISION_TS,
    )
    assert "alt_data_manifest" not in p_default

    p_false = generate_evidence_packet(
        ticker="AAPL", decision_timestamp=DECISION_TS, live_adapters=False,
    )
    assert "alt_data_manifest" not in p_false


def main() -> int:
    failures: list[str] = []
    print("\n=== test_adapter_wiring (D7) ===")
    for fn in (
        test_default_call_byte_identical,
        test_full_live_adapters_five_calls_all_blocks_enabled,
        test_toggled_blocks_skip_relevant_adapters,
        test_full_vs_toggled_hash_differs,
        test_no_adapter_call_packet_does_not_contain_manifest,
    ):
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failures.append(fn.__name__)
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failures.append(fn.__name__)

    print()
    if failures:
        print(f"  RESULT: {len(failures)} failure(s)")
        return 1
    print("  RESULT: 5/5 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
