"""TEMP — Layer 2 AKAN single-ticker LLM verify (2026-05-03).

Runs the SAME pipeline path used by the 5-day replay
(scripts/portfolio_5day_2026_04_27_to_05_01.py) for a single ticker,
exercising all post-2026-05-03 prompt updates:
  - PM v0.6_2026_05_03_universal_softveto
  - surge_short v1.2_2026_05_03_universal_softveto
  - narrative_event v0.5_2026_05_03_universal_softveto
  - alt_data_verification v0.5_2026_05_03_universal_softveto
  - fundamental v0.5_2026_05_03_universal_softveto
  - risk v0.7_2026_05_03_universal_softveto
  - (network_effect / valuation v0.3 / v0.2 not in the SS prelude)

Cost: $0.30 expected; $0.50 hard cap enforced via ANTHROPIC_HARD_STOP_USD.
Will be archived to data/decisions/llm_verify_script_archive_20260503.py
after run.
"""
from __future__ import annotations
import json
import os
import sys
import time
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

# Imports first (the replay-script import sets ANTHROPIC_HARD_STOP_USD=15
# at import time; we override it AFTER the import below).
from src.llm.cache import LLMCache
from src.llm.anthropic_provider import (
    AnthropicProvider, HAIKU_MODEL, get_cost_ledger,
)
from src.agents.runner import run_all_agents_for_candidate
from src.evidence_packet.generator import generate_evidence_packet
from scripts.portfolio_5day_2026_04_27_to_05_01 import LIVE_ADAPTERS_TUPLE

# Now force the env that the replay-script import overrode.
os.environ["LLM_PROVIDER"] = "anthropic"
os.environ["ANTHROPIC_HARD_STOP_USD"] = "0.50"

ticker = "AKAN"
decision_timestamp = "2026-04-28T09:30:00-04:00"
candidate_type = "surge_short"

print("=" * 70)
print("AKAN SINGLE-TICKER LLM VERIFY (Layer 2)")
print("=" * 70)
print(f"Ticker:              {ticker}")
print(f"decision_timestamp:  {decision_timestamp}")
print(f"candidate_type:      {candidate_type}")
print(f"LIVE_ADAPTERS_TUPLE: {len(LIVE_ADAPTERS_TUPLE)} adapters")
print(f"Budget:              $0.30 expected, $0.50 hard cap")
print(f"Hard cap env:        ANTHROPIC_HARD_STOP_USD={os.environ['ANTHROPIC_HARD_STOP_USD']}")
print(f"Token cap env:       ANTHROPIC_MAX_INPUT_TOKENS={os.environ.get('ANTHROPIC_MAX_INPUT_TOKENS', '<unset>')}")
print()

t_packet_start = time.time()
try:
    packet = generate_evidence_packet(
        ticker=ticker,
        decision_timestamp=decision_timestamp,
        live_adapters=LIVE_ADAPTERS_TUPLE,
    )
except Exception:
    print("HARD ERROR during generate_evidence_packet():")
    traceback.print_exc()
    sys.exit(1)
t_packet = time.time() - t_packet_start
print(f"Packet generated in {t_packet:.2f}s")
print(f"Packet hash: {packet.get('envelope', {}).get('evidence_packet_hash', '<none>')}")
print(f"Packet size: ~{len(json.dumps(packet, default=str)):,} chars")
print()

# Construct AnthropicProvider directly with extended per-call timeout so
# the PM agent (final, longest reasoning chain) doesn't trip the default
# 60s timeout. Anthropic API can occasionally take 2-3 min on long
# context PM calls; raise to 300s.
provider = AnthropicProvider(default_model=HAIKU_MODEL, timeout_s=300)
cache = LLMCache()
ledger = get_cost_ledger()
ledger.reset()

t_run_start = time.time()
try:
    out = run_all_agents_for_candidate(
        evidence_packet=packet,
        candidate_type=candidate_type,
        provider=provider,
        cache=cache,
        agent_mode="multi",
        topology="pipeline",
    )
except Exception:
    print("HARD ERROR during run_all_agents_for_candidate():")
    traceback.print_exc()
    print(f"Cost so far: ${ledger.total_usd():.4f}")
    sys.exit(1)
t_run = time.time() - t_run_start

cost_total = ledger.total_usd()
calls = ledger.calls()

# Dump full result + packet
out_path = ROOT / "data" / "decisions" / "llm_verify_akan_20260428_rerun.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
dump = {
    "ticker": ticker,
    "decision_timestamp": decision_timestamp,
    "candidate_type": candidate_type,
    "packet_hash": packet.get("envelope", {}).get("evidence_packet_hash"),
    "elapsed_packet_s": round(t_packet, 2),
    "elapsed_run_s": round(t_run, 2),
    "cost_usd_total": round(cost_total, 6),
    "ledger_calls": calls,
    "evidence_packet": packet,
    "agent_run_envelope": out,
}
out_path.write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")
print(f"Dumped result to {out_path}")
print()

# ── Per-agent summary ──
print("=" * 70)
print("PER-AGENT OUTPUTS")
print("=" * 70)
ao = out.get("agent_outputs", {}) or {}
agent_keys_in_order = list(ao.keys())
print(f"Agents in envelope: {agent_keys_in_order}")
print()

for k in agent_keys_in_order:
    v = ao[k]
    if not isinstance(v, dict):
        print(f"--- {k}: not a dict ---")
        continue
    parsed = v.get("parsed") or v.get("parsed_output") or {}
    print(f"--- {k} ---")
    # Try to surface a primary decision-like field
    decision_keys = (
        "recommended_action", "decision", "verdict",
        "value_creation_assessment", "decision_or_assessment",
        "hard_gates_pass", "classification", "valuation_assessment",
    )
    for dk in decision_keys:
        if dk in parsed:
            print(f"  {dk}: {parsed[dk]}")
    # Veto conditions evaluated (Risk + PM)
    vetoes = parsed.get("veto_conditions_evaluated") or []
    if vetoes:
        for vt in vetoes:
            cond = vt.get("condition", "<?>") if isinstance(vt, dict) else str(vt)
            tripped = vt.get("tripped", False) if isinstance(vt, dict) else False
            mark = "TRIPPED" if tripped else "ok"
            print(f"    veto[{cond}]: {mark}")
    # Rationale fields
    rationale = (parsed.get("audit_rationale")
                 or parsed.get("rationale")
                 or parsed.get("decision_rationale")
                 or parsed.get("reasoning_summary")
                 or parsed.get("recommendation_to_pm")
                 or parsed.get("advisory_notes")
                 or "<no rationale>")
    if isinstance(rationale, (dict, list)):
        rationale = json.dumps(rationale, default=str)[:500]
    rstr = str(rationale)
    if len(rstr) > 500:
        rstr = rstr[:500] + "...[truncated]"
    print(f"  rationale: {rstr}")
    # Per-call cost
    raw = v.get("raw_response") or v.get("usage") or {}
    if isinstance(raw, dict):
        cost = raw.get("cost_usd") or raw.get("total_cost_usd")
        in_tok = raw.get("input_tokens") or raw.get("input_uncached")
        out_tok = raw.get("output_tokens")
        if cost or in_tok:
            print(f"  cost: ${cost} in_tok={in_tok} out_tok={out_tok}")
    print()

# ── PM final ──
print("=" * 70)
print("PM FINAL DECISION")
print("=" * 70)
pm_parsed = (ao.get("pm") or ao.get("pm_agent") or ao.get("risk_pm")
             or {}).get("parsed") or {}
final_decision = pm_parsed.get("decision") or pm_parsed.get("recommended_action") or "<unspecified>"
position = pm_parsed.get("initial_position_pct") or pm_parsed.get("position_size_pct") or 0.0
print(f"PM decision: {final_decision}")
print(f"PM position size: {position}")
pm_rat = pm_parsed.get("audit_rationale") or pm_parsed.get("rationale") or "<none>"
print(f"PM rationale (full):\n  {pm_rat}")
print()

# ── Cost / token summary ──
print("=" * 70)
print("COST + TIMING")
print("=" * 70)
print(f"Total LLM cost:     ${cost_total:.4f}")
print(f"Wall time (run):    {t_run:.1f}s")
print(f"Wall time (packet): {t_packet:.1f}s")
total_in = sum(c.get("input_tokens_uncached", 0)
               + c.get("input_tokens_cached_read", 0)
               + c.get("input_tokens_cached_write", 0) for c in calls)
total_out = sum(c.get("output_tokens", 0) for c in calls)
print(f"Total input tokens:  {total_in:,}")
print(f"Total output tokens: {total_out:,}")
print(f"LLM calls made:      {len(calls)}")
print()
print("Per-call breakdown:")
for c in calls:
    print(f"  {c.get('agent_schema_name', '<?>'):40s}  "
          f"in_unc={c.get('input_tokens_uncached', 0):6d}  "
          f"in_read={c.get('input_tokens_cached_read', 0):6d}  "
          f"out={c.get('output_tokens', 0):5d}  "
          f"cost=${c.get('total_cost_usd', 0):.4f}  "
          f"lat={c.get('latency_ms', 0)}ms")
print()

# Budget check
if cost_total > 0.50:
    print(f"!!! BUDGET BREACH: ${cost_total:.4f} > $0.50 hard cap !!!")
    sys.exit(2)
elif cost_total > 0.30:
    print(f"NOTE: cost ${cost_total:.4f} above $0.30 expected, under $0.50 cap")
else:
    print(f"Cost ${cost_total:.4f} within $0.30 expected.")
print()
print("Done.")
