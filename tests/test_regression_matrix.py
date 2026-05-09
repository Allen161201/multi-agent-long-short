"""
D1 Step A6 — full regression matrix.

Runs 3 tickers (AAPL, NVDA, UBER) × 2 candidate types (surge_short,
quality_long) × 5 mode combinations = 30 cells. For each cell:
  - generates the evidence packet
  - runs the configured agent setup once (expect: misses)
  - runs it again on the same cache (expect: 100% hits)
  - asserts schema_pass on every parsed_output
  - records the result row

Plus an extra ORIGINAL-PACKET regression check: with no toggle and no
new envelope changes, the packet hash must be byte-identical across
reruns. This guards the hash-stability invariant.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.agents.runner import run_all_agents_for_candidate  # noqa: E402
from src.llm.cache import LLMCache  # noqa: E402
from src.llm.factory import get_provider  # noqa: E402
from evidence_packet.generator import generate_evidence_packet  # noqa: E402

TICKERS = ("AAPL", "NVDA", "UBER")
CANDIDATES = ("surge_short", "quality_long")
DECISION_TS = "2026-04-27T16:00:00-04:00"

# §22 frozen baseline hash for the AAPL packet at DECISION_TS, computed
# under the stub provider with default envelope (no enabled_blocks toggle).
# Bumped 2026-05-02 when rule_version moved
# v0.8.6_soft_reasoning_framework -> v0.8.7_ust_fixed_income
# (RULES.md §27.10–§27.18 direct-UST fixed-income universe + dividend
# accrual + ETF expense ratio + Risk soft-veto extension — additive new
# rules, user-approved per Standing Rule 4 in chat 2026-05-02). Hash drift
# driven by the rule_version literals in src/evidence_packet/generator.py:351
# (flows into envelope.rule_version) and src/evidence_packet/blocks/macro.py:29
# REGIME_RULE_VERSION (flows into macro_regime.rule_version). Verified at
# bump time that exactly these 2 packet paths contain the new string and
# zero packet paths still contain "v0.8.6".
# Predecessors:
#   v0.7:                          sha256:1a24b87e9dbb46e520aebf5423c8532541e92bc4238577f045a6d3db363694a2
#   v0.8 (transient):              sha256:63a7d89a6cb69baf267ce8668485590406a22ce96b606fd29299830390b7cdaf
#   v0.8.1_ndi_adas_advisory:      sha256:b776aa8676354b07168a73c28d42df566ddb4851a6ea6015ea33d6cb64253142
#   v0.8.2_ndi_runtime_wired:      sha256:bc8010363ac42e57a62217325ad58f678a59b47070594a6c7962d29ef292cb4f
#   v0.8.3_13f_cadence:            sha256:7aa26c88e1961c4848ada5b920d0d9a67fd94d5e381e0db96a46cd59c9972e95
#   v0.8.4_borrow_cost_cover_cadence: sha256:ea828dc9f964e4841404791648612804f1ed3847a38591e46df36bfedaa0bd11
#   v0.8.5_surge_short_integrity_exception: sha256:64f34919b91ac686b66fa071cb18f39322cffe53313be00fc5ef4e3fd0381d61
#   v0.8.6_soft_reasoning_framework: sha256:760d0764c6b1816df32a5a42f2b69508ad05382cffb57180a4fe288cd903bcdc
#   v0.8.7_ust_fixed_income (initial): sha256:6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095
#   v0.8.7_ust_fixed_income (cosmetic relock 2026-05-03): sha256:04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc
# 2026-05-04 Pass 8 relock: rule_version moved
#   v0.8.7_ust_fixed_income -> v0.9.0_pass8_hardrule (Pass 8 hard-rule
#   architecture overhaul: §2.1/§2.2/§2.6 rewrites + §2.14/§2.15 +
#   §3.6 resolved + §4.7/§4.8 modified + §4.11 SPY drawdown side-input +
#   §5.13 hard-veto-only + §5.14 REMOVED + §10.14 R-COVER-08/09/10 +
#   §16.7 + §27.14/§27.15/§30.2 floor subordination + §31/§32/§33 NEW;
#   user explicit authorization). Hash drift driven by the rule_version
#   literals at all 9 authoritative src/ sites; verified hash flips after
#   bump and is reproducible across reruns under stub provider.
# 2026-05-06 doc v2.10 relock: Bug 2 fix in src/evidence_packet/generator.py
#   moved build_telemetry placeholder to BEFORE compute_evidence_packet_hash
#   so canonical-at-hash bytes match canonical-from-disk bytes. Bug 1
#   (cross-process drift) reframed as phantom — fully explained by Bug 2;
#   5-fresh-process probe under stub provider produced byte-identical
#   hashes. Prior 2026-05-04 baseline sha256:a1538e90... is no longer
#   reproducible (its environment state is gone). Intermediate observed
#   value sha256:7c3f590d... matches gen-time and recanon-from-disk under
#   the fix and is the new locked baseline. User explicit authorization
#   2026-05-06 in chat for §22 changelog + §0 binding-state hash relock.
# 2026-05-06 doc v2.11 relock (Scope 4): added price_snapshot.trigger_day_open
#   field in src/evidence_packet/blocks/price.py to expose the trigger-day
#   9:30 ET open price (PIT-guarded; only populated when cutoff is in
#   [09:30, 16:15) ET on the bar's date). This unblocks §32 mandatory-short
#   bypass which was silently no-op'ing because _compute_surge_pct_from_packet
#   was reading non-existent field "today_open" (Scope 3 fix renamed to
#   "open" but the EOD-bar-derived open is the prior trading day's open
#   under the §6.6 16:00 ET cutoff; Scope 4 adds the trigger-day intraday
#   9:30 ET open as a separate field). _compute_surge_pct_from_packet now
#   reads ps.trigger_day_open (numerator) and ps.last_eod_close (denominator
#   = prior trading day's close under the §6.6 standard cutoff). Verified
#   §32 fires for ACON (+114.9%) and BTOG (+421.8%) with full §32.5 audit
#   trail. AAPL canonical packet hash sha256:7c3f590d... SUPERSEDED by
#   sha256:6b3758bd... (5-fresh-process probe byte-identical). User explicit
#   authorization 2026-05-06 in chat for §22 changelog + §0 binding-state
#   hash field + Version line bump 2.10→2.11 ONLY.
EXPECTED_AAPL_PACKET_HASH = (
    # Relocked 2026-05-07 after V7 FRED PIT anchor fix. The prior 5/6
    # baseline `sha256:98088d08...` was based on macro_regime reading
    # today's FRED cache (silent PIT leak via get_macro_indicators_for_dashboard
    # in src/evidence_packet/blocks/macro.py:45 — fixed under V7 to call the
    # PIT-correct date-keyed get_macro_indicators(cutoff_date_str) entrypoint
    # at src/data_adapters/fred_adapter.py:519). New hash content reflects
    # the PIT-correct FRED cache for the canonical 2026-04-27 cutoff date
    # (fred_2026-04-27.json: live FRED data, treasury_1m=3.69, fed_funds=3.64).
    # Verified byte-identical across 5 fresh processes 2026-05-07. User
    # explicit authorization 2026-05-07 for §22 changelog + §0 binding-state
    # hash field + Version line bump 2.13→2.14 + V7 anchor.
    "sha256:626266c71956d0baec9252d6c9845388a3c324b193d707d2e0e0d75ae2d979bb"
)

# Five mode combinations per the Step A6 spec.
TEXT_ONLY_BLOCKS = {
    "news_event_summary",
    "filing_confirmation",
    "narrative_price_gap_assessment",
    "decision_time_discipline",
}

MODE_COMBINATIONS = [
    # (label, agent_mode, topology, enabled_blocks_or_None)
    ("multi/pipeline/all",       "multi", "pipeline", None),
    ("multi/flat/all",           "multi", "flat",     None),
    ("solo/-/all",               "solo",  "pipeline", None),
    ("multi/pipeline/text-only", "multi", "pipeline", TEXT_ONLY_BLOCKS),
    ("multi/flat/text-only",     "multi", "flat",     TEXT_ONLY_BLOCKS),
]


def _all_validations_ok(parsed_outputs: dict) -> tuple[bool, list[str]]:
    """Return (all_ok, fail_names). A `validation_status` of "ok" or
    absent (the stub-skeletons mostly do not include this field) is
    considered a pass; any explicit failure status fails the cell."""
    failed: list[str] = []
    for agent_name, parsed in parsed_outputs.items():
        if not isinstance(parsed, dict):
            failed.append(f"{agent_name}:not-a-dict")
            continue
        status = parsed.get("validation_status")
        if status and status != "ok":
            failed.append(f"{agent_name}:{status}")
    return (not failed), failed


def main() -> int:
    failures: list[str] = []
    rows: list[dict] = []

    print("\n=== Step A6 regression matrix ===\n")
    print(
        f"  {'ticker':6s}  {'candidate':14s}  {'mode':28s}  "
        f"{'schema_pass':12s}  {'cache_hit_2nd':14s}"
    )
    print("  " + "-" * 80)

    provider = get_provider()
    # One shared cache root so all cells share the disk layout (and the
    # second run can hit the first run's records).
    cache_root = tempfile.mkdtemp(prefix="d1_step_a6_")
    cache = LLMCache(root=cache_root)

    for ticker in TICKERS:
        for candidate in CANDIDATES:
            for label, agent_mode, topology, enabled_blocks in MODE_COMBINATIONS:
                packet = generate_evidence_packet(
                    ticker=ticker, decision_timestamp=DECISION_TS,
                    enabled_blocks=enabled_blocks,
                )

                first = run_all_agents_for_candidate(
                    evidence_packet=packet,
                    candidate_type=candidate,
                    provider=provider,
                    cache=cache,
                    agent_mode=agent_mode,
                    topology=topology,
                )
                schema_ok, fail_names = _all_validations_ok(
                    first["agent_outputs"]
                )

                second = run_all_agents_for_candidate(
                    evidence_packet=packet,
                    candidate_type=candidate,
                    provider=provider,
                    cache=cache,
                    agent_mode=agent_mode,
                    topology=topology,
                )
                summ2 = second["cache_summary"]
                total_agents = summ2["hits"] + summ2["misses"]
                full_hit = total_agents > 0 and summ2["misses"] == 0

                row_label = (
                    f"{ticker:6s}  {candidate:14s}  {label:28s}  "
                    f"{('PASS' if schema_ok else 'FAIL'):12s}  "
                    f"{('YES' if full_hit else 'NO'):14s}"
                )
                print(f"  {row_label}")
                rows.append({
                    "ticker": ticker, "candidate": candidate,
                    "mode": label, "schema_pass": schema_ok,
                    "cache_hit_2nd": full_hit,
                    "schema_failures": fail_names,
                    "second_run_summary": summ2,
                })
                if not schema_ok:
                    failures.append(f"{ticker}/{candidate}/{label}: {fail_names}")
                if not full_hit:
                    failures.append(
                        f"{ticker}/{candidate}/{label}: cache miss on 2nd run "
                        f"({summ2})"
                    )

    print()
    print(f"  Total cells: {len(rows)}")
    print(f"  Schema-pass cells: {sum(1 for r in rows if r['schema_pass'])}")
    print(f"  Cache-hit-on-2nd cells: {sum(1 for r in rows if r['cache_hit_2nd'])}")

    # ── Byte-identical regression on the original packet ──
    print("\n=== Byte-identical packet hash regression ===\n")
    p_a = generate_evidence_packet(ticker="AAPL", decision_timestamp=DECISION_TS)
    p_b = generate_evidence_packet(ticker="AAPL", decision_timestamp=DECISION_TS)
    same_hash = (p_a["envelope"]["evidence_packet_hash"]
                 == p_b["envelope"]["evidence_packet_hash"])
    no_eb_field = "enabled_blocks" not in p_a["envelope"]
    print(f"  hash stable across reruns:        {same_hash}")
    print(f"  default envelope omits enabled_blocks: {no_eb_field}")
    print(f"  hash A: {p_a['envelope']['evidence_packet_hash']}")
    print(f"  expected (v0.8 baseline): {EXPECTED_AAPL_PACKET_HASH}")
    if not same_hash:
        failures.append("byte-identical packet hash regression failed")
    if not no_eb_field:
        failures.append("default envelope unexpectedly contains enabled_blocks")
    # §22 frozen-baseline assertion. The hash is bumped through the §22
    # change procedure whenever rule_version changes or any block schema
    # changes (e.g. adding a new field to a block). This guards against
    # accidental drift.
    if p_a["envelope"]["evidence_packet_hash"] != EXPECTED_AAPL_PACKET_HASH:
        failures.append(
            f"AAPL baseline hash drift: expected "
            f"{EXPECTED_AAPL_PACKET_HASH}, got "
            f"{p_a['envelope']['evidence_packet_hash']}"
        )

    print()
    if failures:
        print(f"  RESULT: {len(failures)} failure(s)")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("  RESULT: 30/30 schema-pass, 30/30 cache-hit-on-2nd-run, "
          "byte-identical hash regression passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
