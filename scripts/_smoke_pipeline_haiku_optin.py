"""
Throwaway: Haiku-pinned single-ticker pipeline with FULL alt-data opt-in.
Tests whether PM agent stops citing 'Alternative-data gap' once
alternative_data_features.status is promoted to 'ok'.

Will be deleted after the verification task closes.
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

os.environ["LLM_PROVIDER"] = "anthropic"

HAIKU_MODEL = "claude-haiku-4-5-20251001"
TICKER = "AAPL"
CANDIDATE_TYPE = "quality_long"
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
DECISION_DIR = ROOT / "data" / "decisions" / "2026-04-29" / "16-15"
CACHE_DIR_NAME = "llm_smoke_haiku_v9_placeholderfix"


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ABORT  ANTHROPIC_API_KEY not set")
        return 2

    print("=== Haiku full-opt-in pipeline (alt-data status promotion smoke) ===")
    print(f"  ticker        : {TICKER}")
    print(f"  cutoff        : {CUTOFF}")
    print(f"  live_adapters : {ALL_LIVE}")
    print(f"  cache         : {CACHE_DIR_NAME}")

    from src.evidence_packet import generate_evidence_packet, PITViolationError
    from src.agents.runner import run_all_agents_for_candidate
    from src.llm.cache import LLMCache
    from src.llm.anthropic_provider import (
        AnthropicProvider, AnthropicProviderError, get_cost_ledger,
    )

    ledger = get_cost_ledger()
    ledger.reset()
    t_wall = time.perf_counter()

    try:
        packet = generate_evidence_packet(
            ticker=TICKER,
            decision_timestamp=CUTOFF,
            strict_pit_mode=True,
            live_adapters=ALL_LIVE,
        )
    except PITViolationError as e:
        print(f"\n  ABORT  PITViolationError: {e}")
        return 2

    b = packet["alternative_data_features"]
    print(f"\n  packet hash : {packet['envelope'].get('evidence_packet_hash')}")
    print(f"  alt_data.status = {b.get('status')!r}  source = {b.get('source')!r}")

    if b.get("status") != "ok":
        print(f"\n  ABORT  alt_data.status = {b.get('status')!r} — fix not applied")
        return 3

    try:
        provider = AnthropicProvider(default_model=HAIKU_MODEL)
    except AnthropicProviderError as e:
        print(f"\n  ABORT  Anthropic provider construction failed: {e}")
        return 3

    cache = LLMCache(root=ROOT / "data" / "cache" / CACHE_DIR_NAME)

    try:
        result = run_all_agents_for_candidate(
            evidence_packet=packet,
            candidate_type=CANDIDATE_TYPE,
            provider=provider,
            cache=cache,
            agent_mode="multi",
            topology="pipeline",
            force_refresh=False,
        )
    except AnthropicProviderError as e:
        elapsed = time.perf_counter() - t_wall
        print(f"\n  ABORT  Anthropic call failed: {e}")
        print(f"  cumulative cost : ${ledger.total_usd():.5f}")
        print(f"  wall-clock      : {elapsed:.2f} s")
        # Show which agents completed (cache check)
        cache_root = ROOT / "data" / "cache" / CACHE_DIR_NAME
        if cache_root.exists():
            print(f"  cache contents:")
            for sub in sorted(cache_root.iterdir()):
                if sub.is_dir():
                    n = sum(1 for _ in sub.glob("*.json"))
                    print(f"    {sub.name}: {n} entries")
        return 4

    elapsed = time.perf_counter() - t_wall

    # Save artifacts
    DECISION_DIR.mkdir(parents=True, exist_ok=True)
    pkt_path = DECISION_DIR / f"{TICKER}_evidence_packet_optin.json"
    dec_path = DECISION_DIR / f"{TICKER}_{CANDIDATE_TYPE}_decision_optin.json"
    pkt_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    dec_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Per-agent decision summary
    print("\n  ── Per-agent outputs ──")
    for agent_name, parsed in result["agent_outputs"].items():
        decision = (parsed.get("recommended_action") or parsed.get("decision")
                    or parsed.get("thesis_status") or parsed.get("verification_status")
                    or "n/a")
        print(f"    {agent_name:24s} | {str(decision)[:50]}")

    # PM agent risk_flags inspection — KEY TEST
    pm_out = result["agent_outputs"].get("PortfolioManagerAgentOutput") or \
             result.get("final_decision") or {}
    risk_flags = pm_out.get("risk_flags") or pm_out.get("risk_concerns") or []
    print(f"\n  ── PM risk_flags ({len(risk_flags)} items) ──")
    for rf in (risk_flags if isinstance(risk_flags, list) else [risk_flags]):
        s = str(rf)[:200]
        print(f"    • {s}")
        if "Alternative-data gap" in s or "alternative-data gap" in s.lower() or "alt-data gap" in s.lower() or "all adapters not_connected" in s.lower():
            print(f"      ⚠️  PM STILL CITES ALT-DATA GAP — prompt-template issue, not packet issue")

    # alt_data_verify agent verdict — KEY TEST
    av = result["agent_outputs"].get("AltDataVerifyAgentOutput") or {}
    print(f"\n  ── alt_data_verify ──")
    print(f"    verification_status : {av.get('verification_status')!r}")
    print(f"    confidence          : {av.get('confidence')!r}")
    notes = av.get('audit_rationale') or av.get('notes') or ''
    print(f"    rationale (first 280): {str(notes)[:280]}")

    # Cost
    print(f"\n  ── Cost / wall-clock ──")
    print(f"    total cost USD : ${ledger.total_usd():.5f}")
    print(f"    wall-clock (s) : {elapsed:.2f}")
    cs = result.get('cache_summary') or {}
    print(f"    cache hits/misses : {cs.get('hits')}/{cs.get('misses')}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(99)
