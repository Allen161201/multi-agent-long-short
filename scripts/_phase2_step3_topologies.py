"""
Phase 2 Step 3 — Batch 7 hard tests E/F/G.

Three topology smokes against the same evidence packet (AAPL,
quality_long, 2026-04-29T16:15-04:00) to verify orchestrators end-to-end
under live Haiku:

  E: multi / pipeline — sequential prelude + sleeve + risk + pm
  F: multi / solo     — single baseline_solo agent (different cache namespace)
  G: multi / flat     — 6 specialists in parallel + pm_flat aggregator

Specialists' cache keys are unchanged across topologies (same agent_name +
prompt_version + packet_hash), so once Phase 2 Step 2 has already run the
6 prelude+sleeve specialists, flat-mode reuses those records cleanly.
The only fresh provider calls are:
  E: 0 (all 8 already cached after Phase 2 Step 2)
  F: 1 (baseline_solo)
  G: 1 (pm_flat)

Hardcoded constants at the TOP per the user's "D5-reusable" clarification.
"""
from __future__ import annotations

import json
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
TIMEOUT_S = 300
TICKER = "AAPL"
CANDIDATE_TYPE = "quality_long"
CUTOFF = "2026-04-29T16:15:00-04:00"
DECISION_DIR = ROOT / "data" / "decisions" / "2026-04-29" / "16-15"
CACHE_ROOT = ROOT / "data" / "cache" / "llm_smoke_haiku_p2step2"


def _summarise(name: str, parsed: dict) -> str:
    decision = (
        parsed.get("recommended_action")
        or parsed.get("decision")
        or parsed.get("thesis_status")
        or parsed.get("verification_status")
        or parsed.get("decision_label")
        or "n/a"
    )
    confidence = parsed.get("confidence") or parsed.get("confidence_level") or "n/a"
    return f"    {name:18s} decision={decision!s:30s} conf={confidence!s}"


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ABORT  ANTHROPIC_API_KEY not set")
        return 2

    print("=== Phase 2 Step 3 — Batch 7 hard tests E/F/G ===")
    print(f"  ticker={TICKER}  candidate={CANDIDATE_TYPE}  cutoff={CUTOFF}")
    print(f"  model={HAIKU_MODEL}  timeout_s={TIMEOUT_S}")
    print(f"  cache_root={CACHE_ROOT}")

    from src.evidence_packet import generate_evidence_packet
    from src.agents.runner import run_all_agents_for_candidate
    from src.llm.cache import LLMCache
    from src.llm.anthropic_provider import (
        AnthropicProvider,
        get_cost_ledger,
    )

    ledger = get_cost_ledger()
    ledger.reset()

    packet = generate_evidence_packet(
        ticker=TICKER, decision_timestamp=CUTOFF, strict_pit_mode=True,
    )
    env = packet.get("envelope", {})
    print(f"\n  packet_hash (generator): {env.get('evidence_packet_hash')}")
    print(f"  rule_version           : {env.get('rule_version')}")

    provider = AnthropicProvider(default_model=HAIKU_MODEL, timeout_s=TIMEOUT_S)
    cache = LLMCache(root=CACHE_ROOT)

    runs: dict[str, dict] = {}

    for label, agent_mode, topology in [
        ("E_pipeline", "multi", "pipeline"),
        ("F_solo",     "solo",  "pipeline"),  # topology ignored when solo
        ("G_flat",     "multi", "flat"),
    ]:
        print(f"\n  === {label} (agent_mode={agent_mode}, topology={topology}) ===")
        cost_before = ledger.total_usd()
        t0 = time.perf_counter()
        result = run_all_agents_for_candidate(
            evidence_packet=packet,
            candidate_type=CANDIDATE_TYPE,
            provider=provider,
            cache=cache,
            agent_mode=agent_mode,
            topology=topology,
            force_refresh=False,
        )
        elapsed = time.perf_counter() - t0
        cost = ledger.total_usd() - cost_before
        print(f"    cost USD       : ${cost:.5f}")
        print(f"    wall-clock     : {elapsed:.2f} s")
        print(f"    cache_summary  : {result['cache_summary']}")
        for name, parsed in result["agent_outputs"].items():
            print(_summarise(name, parsed))
        runs[label] = {
            "agent_mode": agent_mode,
            "topology": topology,
            "cost_usd": cost,
            "wall_clock_s": elapsed,
            "cache_summary": result["cache_summary"],
            "final_decision": result.get("final_decision"),
            "ndi_compute_result": result.get("ndi_compute_result"),
            "agent_outputs": result.get("agent_outputs"),
        }

    total = ledger.total_usd()
    print(f"\n  Cumulative Step 3 cost: ${total:.5f}")

    # ── Cross-topology decision compare ──────────────────────────────
    def _final_label(r: dict | None) -> str:
        fd = (r or {}).get("final_decision") or {}
        return str(fd.get("decision") or fd.get("recommended_action") or "n/a")
    print("\n  ── Cross-topology final decisions ──")
    print(f"    E (pipeline) : {_final_label(runs['E_pipeline'])}")
    print(f"    F (solo)     : {_final_label(runs['F_solo'])}")
    print(f"    G (flat)     : {_final_label(runs['G_flat'])}")

    # ── Persist ──────────────────────────────────────────────────────
    DECISION_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DECISION_DIR / f"{TICKER}_{CANDIDATE_TYPE}_phase2_step3_results.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({
            "ticker": TICKER,
            "candidate_type": CANDIDATE_TYPE,
            "cutoff": CUTOFF,
            "model": HAIKU_MODEL,
            "runs": runs,
            "total_cost_usd": total,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    import traceback
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(99)
