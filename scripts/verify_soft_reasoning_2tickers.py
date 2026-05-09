"""Single-ticker LLM verify (Step 6, 2026-05-02 v0.8.6 SOFT_REASONING).

Runs ONE quality_long pipeline on AVB @ 2026-04-27 09:30 ET and ONE
surge_short pipeline on AKAN @ 2026-04-28 09:30 ET, both with the
full live_adapters tuple. Prints PM decision + reasoning_summary
verbatim and the cost. Total expected cost ~$0.55.

Hard cap: $1.00 — protects against runaway. Aborts cleanly if exceeded.
"""
from __future__ import annotations

import json
import os
import sys
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
os.environ["ANTHROPIC_HARD_STOP_USD"] = "1.00"

from src.evidence_packet.generator import generate_evidence_packet  # noqa
from src.agents.runner import run_all_agents_for_candidate  # noqa
from src.llm.factory import get_provider  # noqa
from src.llm.cache import LLMCache  # noqa
from src.llm.anthropic_provider import get_cost_ledger  # noqa

# Per task brief: "live_adapters=True". This is the default tuple
# (3 alt-data + 2 OpenCLI = 5 sources), keeps packet under the 50k
# input-token cap. The replay script uses the wider 9-source tuple.
LIVE_ADAPTERS: bool | tuple = True


def _ledger_total() -> float:
    try:
        return float(get_cost_ledger().total_usd)
    except Exception:
        return 0.0


def _extract_pm(out: dict) -> dict:
    aos = out.get("agent_outputs", {}) or {}
    for k in ("pm", "pm_agent", "pm_flat", "risk_pm"):
        if k in aos:
            v = aos[k]
            if isinstance(v, dict):
                p = v.get("parsed") or v.get("parsed_output") or v
                if isinstance(p, dict):
                    return p
    return {}


def run_one(*, ticker: str, candidate_type: str, decision_ts: str,
            agent_mode: str, topology: str, label: str) -> dict:
    print(f"\n=== {label} ===")
    print(f"  ticker={ticker}  candidate={candidate_type}  decision={decision_ts}")
    cost_before = _ledger_total()

    packet = generate_evidence_packet(
        ticker=ticker, decision_timestamp=decision_ts,
        live_adapters=LIVE_ADAPTERS,
    )
    print(f"  packet built; rule_version={packet['envelope']['rule_version']}")
    print(f"  packet_hash={packet['envelope']['evidence_packet_hash']}")

    provider = get_provider()
    cache = LLMCache()
    out = run_all_agents_for_candidate(
        evidence_packet=packet,
        candidate_type=candidate_type,
        provider=provider,
        cache=cache,
        agent_mode=agent_mode,
        topology=topology,
    )

    cost_after = _ledger_total()
    pm = _extract_pm(out)

    decision = pm.get("decision_or_assessment") or pm.get("decision") or "(unknown)"
    plan = pm.get("execution_plan") or {}
    reasoning = pm.get("reasoning_summary") or pm.get("rationale") or "(none)"

    return {
        "label": label,
        "ticker": ticker,
        "candidate_type": candidate_type,
        "decision_or_assessment": decision,
        "execution_plan": plan,
        "side": plan.get("side"),
        "size_pct": plan.get("size_pct_of_portfolio"),
        "reasoning_summary": reasoning,
        "evidence_used_count": len(pm.get("evidence_used", []) or []),
        "evidence_missing_count": len(pm.get("evidence_missing", []) or []),
        "pm_prompt_version": pm.get("prompt_version"),
        "pm_rule_version": pm.get("rule_version"),
        "cost_usd": cost_after - cost_before,
    }


def main() -> int:
    results = []
    print(f"[verify] starting; cumulative_cost=${_ledger_total():.4f}",
          flush=True)

    # 1. AVB quality_long @ 2026-04-27 09:30 ET — 7-agent pipeline
    results.append(run_one(
        ticker="AVB", candidate_type="quality_long",
        decision_ts="2026-04-27T09:30:00-04:00",
        agent_mode="multi", topology="pipeline",
        label="AVB quality_long",
    ))

    # 2. AKAN surge_short @ 2026-04-28 09:30 ET — 6-agent pipeline
    results.append(run_one(
        ticker="AKAN", candidate_type="surge_short",
        decision_ts="2026-04-28T09:30:00-04:00",
        agent_mode="multi", topology="pipeline",
        label="AKAN surge_short",
    ))

    print()
    print("=" * 88)
    print("RESULTS")
    print("=" * 88)
    total_cost = 0.0
    for r in results:
        print(f"\n[{r['label']}]")
        print(f"  pm_prompt_version: {r['pm_prompt_version']}")
        print(f"  pm_rule_version:   {r['pm_rule_version']}")
        print(f"  decision: {r['decision_or_assessment'][:200]}")
        print(f"  side={r['side']}  size_pct={r['size_pct']}")
        print(f"  evidence_used={r['evidence_used_count']} "
              f"evidence_missing={r['evidence_missing_count']}")
        print(f"  cost_usd: ${r['cost_usd']:.4f}")
        print(f"  reasoning_summary:")
        for line in (r["reasoning_summary"] or "").split("\n"):
            print(f"    {line}")
        total_cost += r["cost_usd"]

    # Persist results next to the script for the final report.
    out_path = ROOT / "data" / "decisions" / "soft_reasoning_verify_2026_05_02.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\n[verify] results saved to {out_path}")
    print(f"[verify] total_verification_cost: ${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
