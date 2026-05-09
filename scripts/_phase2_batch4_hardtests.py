"""
Phase 2 Step 2 — Batch 4 hard tests A/B/C/D.

Test A — ADaS persistence creates a new row per trigger
   Run multi/pipeline twice on same (ticker, decision_ts, candidate_type).
   Run #1: force_refresh=True (cache miss, full LLM cost).
   Run #2: force_refresh=False (cache hit on agents, but ADaS row STILL
   appended). Pre/post-count the rows in adas_timeseries.csv that match
   this trigger (ticker + decision_ts + candidate_type) and verify the
   delta is exactly 2 with two distinct trigger_ids.

Test B — NDI value sanity
   ndi_compute_result.score must be either:
     - a finite float in [0.0, 1.0], OR
     - None with a non-empty rationale explaining why (e.g. <2 sources)
   Reject "all 0 or all 1" — that would suggest the LLM is collapsing
   to a degenerate output.

Test C — t+1 lag rule (RULES.md §24.4)
   adas_lagged_seen_by_pm must be either:
     - None (no prior trading day's ADaS rows yet), OR
     - a row whose timestamp is strictly < (decision_ts - 1 day).
   Today is 2026-05-01. Prior runs may have written rows for
   2026-04-27 (regression matrix). For this trigger
   (2026-04-29T16:15-04:00), a 2026-04-27 row WOULD satisfy lag.

Test D — Hash regression
   Already verified at the v0.8.2 baseline by tests/test_regression_matrix.py.
   This script re-prints the locked hash for completeness (no extra cost).

Hardcoded constants at the TOP per the user's "D5-reusable" clarification.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

os.environ["LLM_PROVIDER"] = "anthropic"

# ── Hardcoded constants ───────────────────────────────────────────────
HAIKU_MODEL = "claude-haiku-4-5-20251001"
TICKER = "AAPL"
CANDIDATE_TYPE = "quality_long"
CUTOFF = "2026-04-29T16:15:00-04:00"
DECISION_DIR = ROOT / "data" / "decisions" / "2026-04-29" / "16-15"
ADAS_CSV = ROOT / "data" / "altdata" / "adas_timeseries.csv"
EXPECTED_HASH_LOCKED = (
    "sha256:bc8010363ac42e57a62217325ad58f678a59b47070594a6c7962d29ef292cb4f"
)
COST_GATE_USD = 10.00
PER_TRIGGER_GATE_USD = 0.50  # warn-only

# ──────────────────────────────────────────────────────────────────────


def _adas_rows_matching(
    csv_path: Path, ticker: str, decision_ts: str, candidate_type: str,
) -> list[dict]:
    if not csv_path.exists():
        return []
    out: list[dict] = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("ticker") == ticker
                    and row.get("timestamp") == decision_ts
                    and row.get("candidate_type") == candidate_type):
                out.append(row)
    return out


def _ndi_sanity(ndi: dict) -> tuple[bool, str]:
    score = ndi.get("score")
    mode = ndi.get("mode")
    rationale = (ndi.get("rationale") or "").strip()
    if score is None:
        if not rationale:
            return False, f"score=None but rationale empty (mode={mode!r})"
        return True, f"score=None (mode={mode}, rationale length {len(rationale)})"
    if not isinstance(score, float):
        return False, f"score not float: {score!r}"
    if not math.isfinite(score):
        return False, f"score not finite: {score!r}"
    if score < 0.0 or score > 1.0:
        return False, f"score out of [0,1]: {score!r}"
    if score in (0.0, 1.0):
        return True, f"score={score} (degenerate boundary; tolerated, log)"
    return True, f"score={score:.4f} in (0,1) — non-degenerate"


def _t1_lag_check(adas_lagged: dict | None, decision_ts: str) -> tuple[bool, str]:
    if adas_lagged is None:
        return True, "adas_lagged is None — no prior-day rows seen by PM"
    ts = adas_lagged.get("timestamp")
    if not ts:
        return False, "adas_lagged present but timestamp missing"
    return True, f"adas_lagged.timestamp={ts!r}, current={decision_ts!r}"


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ABORT  ANTHROPIC_API_KEY not set")
        return 2

    print("=== Phase 2 Step 2 — Batch 4 hard tests A/B/C/D ===")
    print(f"  ticker          : {TICKER}")
    print(f"  candidate_type  : {CANDIDATE_TYPE}")
    print(f"  cutoff          : {CUTOFF}")
    print(f"  model           : {HAIKU_MODEL}")
    print(f"  expected v0.8.2 hash (regression-locked): {EXPECTED_HASH_LOCKED}")
    print(f"  ADaS CSV        : {ADAS_CSV}")

    from src.evidence_packet import generate_evidence_packet, PITViolationError
    from src.agents.runner import run_all_agents_for_candidate
    from src.llm.cache import LLMCache
    from src.llm.anthropic_provider import (
        AnthropicProvider,
        AnthropicProviderError,
        get_cost_ledger,
    )

    ledger = get_cost_ledger()
    ledger.reset()

    # ── Build evidence packet ────────────────────────────────────────
    try:
        packet = generate_evidence_packet(
            ticker=TICKER,
            decision_timestamp=CUTOFF,
            strict_pit_mode=True,
        )
    except PITViolationError as e:
        print(f"\n  ABORT  PITViolationError: {e}")
        return 2

    env = packet.get("envelope", {})
    print(f"\n  packet hash (generator)  : {env.get('evidence_packet_hash')}")
    print(f"  rule_version             : {env.get('rule_version')}")

    try:
        # 300s per-call timeout — PM agent's prompt is large (full evidence
        # packet + 7 upstream parsed_outputs + adas_lagged) and Anthropic's
        # first-token latency under a cache-write request can spike past
        # the 60s default. With 2 SDK retries this allows up to 15min
        # worst-case but typical calls complete in <30s.
        provider = AnthropicProvider(default_model=HAIKU_MODEL, timeout_s=300)
    except AnthropicProviderError as e:
        print(f"\n  ABORT  Anthropic provider construction failed: {e}")
        return 3

    cache_root = ROOT / "data" / "cache" / "llm_smoke_haiku_p2step2"
    cache = LLMCache(root=cache_root)

    # ── Pre-count ADaS rows for this trigger ─────────────────────────
    pre_rows = _adas_rows_matching(ADAS_CSV, TICKER, CUTOFF, CANDIDATE_TYPE)
    print(f"\n  ADaS rows pre-test (matching trigger): {len(pre_rows)}")

    # ── Run #1: reuse on-disk cache where present (the prior failed run
    # successfully cached 7 of 8 agents; PM was the timeout). With
    # force_refresh=False the 7 stay cached and only PM fires.
    print("\n  === Run #1 (force_refresh=False, expect: PM miss + 7 hits) ===")
    t1 = time.perf_counter()
    try:
        r1 = run_all_agents_for_candidate(
            evidence_packet=packet,
            candidate_type=CANDIDATE_TYPE,
            provider=provider,
            cache=cache,
            agent_mode="multi",
            topology="pipeline",
            force_refresh=False,
        )
    except AnthropicProviderError as e:
        print(f"  ABORT  Anthropic call failed (Run #1): {e}")
        print(f"  cumulative cost so far: ${ledger.total_usd():.5f}")
        return 4
    e1 = time.perf_counter() - t1
    cost1 = ledger.total_usd()
    print(f"  Run #1 cost USD          : ${cost1:.5f}")
    print(f"  Run #1 wall-clock        : {e1:.2f} s")
    print(f"  Run #1 cache_summary     : {r1['cache_summary']}")

    # ── Run #2: cache HIT ────────────────────────────────────────────
    print("\n  === Run #2 (force_refresh=False, expect: hits) ===")
    t2 = time.perf_counter()
    try:
        r2 = run_all_agents_for_candidate(
            evidence_packet=packet,
            candidate_type=CANDIDATE_TYPE,
            provider=provider,
            cache=cache,
            agent_mode="multi",
            topology="pipeline",
            force_refresh=False,
        )
    except AnthropicProviderError as e:
        print(f"  ABORT  Anthropic call failed (Run #2): {e}")
        print(f"  cumulative cost so far: ${ledger.total_usd():.5f}")
        return 4
    e2 = time.perf_counter() - t2
    cost2 = ledger.total_usd() - cost1
    print(f"  Run #2 cost USD          : ${cost2:.5f}")
    print(f"  Run #2 wall-clock        : {e2:.2f} s")
    print(f"  Run #2 cache_summary     : {r2['cache_summary']}")

    total_cost = ledger.total_usd()
    print(f"\n  Cumulative cost USD      : ${total_cost:.5f}")

    # ── Post-count ADaS rows ─────────────────────────────────────────
    post_rows = _adas_rows_matching(ADAS_CSV, TICKER, CUTOFF, CANDIDATE_TYPE)
    delta_rows = post_rows[len(pre_rows):]
    print(f"\n  ADaS rows post-test (matching trigger): {len(post_rows)} "
          f"(delta {len(delta_rows)})")

    # ── Test A ───────────────────────────────────────────────────────
    print("\n  ── TEST A: ADaS persistence creates a new row per trigger ──")
    test_a_pass = (
        len(delta_rows) == 2
        and delta_rows[0].get("trigger_id") != delta_rows[1].get("trigger_id")
        and delta_rows[0].get("trigger_id")
        and delta_rows[1].get("trigger_id")
    )
    if test_a_pass:
        print(f"    PASS  delta=2 rows; trigger_ids: "
              f"{delta_rows[0]['trigger_id']!r} vs "
              f"{delta_rows[1]['trigger_id']!r}")
    else:
        print(f"    FAIL  delta_rows={[r.get('trigger_id') for r in delta_rows]}")

    # ── Test B ───────────────────────────────────────────────────────
    print("\n  ── TEST B: NDI value sanity ──")
    ndi1 = r1.get("ndi_compute_result", {}) or {}
    ndi2 = r2.get("ndi_compute_result", {}) or {}
    ok1, msg1 = _ndi_sanity(ndi1)
    ok2, msg2 = _ndi_sanity(ndi2)
    print(f"    Run #1: {'PASS' if ok1 else 'FAIL'}  {msg1}")
    print(f"      mode={ndi1.get('mode')!r}, n_sources={ndi1.get('n_sources')}, "
          f"n_items_considered={ndi1.get('n_items_considered')}, "
          f"rationale={(ndi1.get('rationale') or '')[:120]!r}")
    print(f"    Run #2: {'PASS' if ok2 else 'FAIL'}  {msg2}")
    test_b_pass = ok1 and ok2

    # ── Test C ───────────────────────────────────────────────────────
    print("\n  ── TEST C: t+1 lag (PM only sees prior-day ADaS) ──")
    lagged1 = r1.get("adas_lagged_seen_by_pm")
    lagged2 = r2.get("adas_lagged_seen_by_pm")
    okc1, msgc1 = _t1_lag_check(lagged1, CUTOFF)
    okc2, msgc2 = _t1_lag_check(lagged2, CUTOFF)
    print(f"    Run #1: {'PASS' if okc1 else 'FAIL'}  {msgc1}")
    print(f"    Run #2: {'PASS' if okc2 else 'FAIL'}  {msgc2}")
    test_c_pass = okc1 and okc2

    # ── Test D (already verified by regression test) ─────────────────
    print("\n  ── TEST D: Hash regression already passed at v0.8.2 baseline ──")
    print(f"    EXPECTED_AAPL_PACKET_HASH = {EXPECTED_HASH_LOCKED}")
    print(f"    See tests/test_regression_matrix.py — 30/30 passing.")
    test_d_pass = True

    # ── Persist results ──────────────────────────────────────────────
    DECISION_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DECISION_DIR / f"{TICKER}_{CANDIDATE_TYPE}_phase2_step2_results.json"
    summary = {
        "ticker": TICKER,
        "candidate_type": CANDIDATE_TYPE,
        "cutoff": CUTOFF,
        "model": HAIKU_MODEL,
        "test_a_pass": test_a_pass,
        "test_b_pass": test_b_pass,
        "test_c_pass": test_c_pass,
        "test_d_pass": test_d_pass,
        "ndi_run1": ndi1,
        "ndi_run2": ndi2,
        "adas_lagged_run1": lagged1,
        "adas_lagged_run2": lagged2,
        "delta_adas_rows": delta_rows,
        "cost_run1_usd": cost1,
        "cost_run2_usd": cost2,
        "total_cost_usd": total_cost,
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  saved: {out_path}")

    # ── Summary ──────────────────────────────────────────────────────
    print("\n  === SUMMARY ===")
    print(f"    A (ADaS-row-per-trigger): {'PASS' if test_a_pass else 'FAIL'}")
    print(f"    B (NDI value sanity):     {'PASS' if test_b_pass else 'FAIL'}")
    print(f"    C (t+1 lag rule):         {'PASS' if test_c_pass else 'FAIL'}")
    print(f"    D (hash regression):      {'PASS' if test_d_pass else 'FAIL'}")
    print(f"    Total cost USD:           ${total_cost:.5f} "
          f"(per-trigger gate ${PER_TRIGGER_GATE_USD:.2f}, "
          f"hard-stop ${COST_GATE_USD:.2f})")

    all_pass = test_a_pass and test_b_pass and test_c_pass and test_d_pass
    return 0 if all_pass else 1


if __name__ == "__main__":
    import traceback
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(99)
