"""
End-to-end smoke driver — Task 7 (2026-04-29 PM).

Runs ONE pipeline invocation against a single ticker against the real
Anthropic API. Reports cost, cache hit rate, per-agent model, the
final PM decision, and wall-clock duration.

Usage:
    python scripts/smoke_test_anthropic_e2e.py [--ticker AAPL]
                                                 [--cutoff 2026-04-29T09:30:00-04:00]
                                                 [--candidate quality_long|surge_short]

Defaults: AAPL, 2026-04-29 09:30 ET, quality_long.

Replay-mode metadata is enforced when --cutoff is supplied AND
LLM_PROVIDER=anthropic (which this script forces explicitly). The
generator runs in strict_pit_mode=True and raises PITViolationError on
any lookahead violation — no silent fallback.

Cost guard: the provider's $5.00 cumulative hard-stop applies. A clean
single-ticker run is well under $0.50 with prompt caching engaged.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Load .env BEFORE the provider imports run.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

# Force Anthropic provider for the smoke run.
os.environ["LLM_PROVIDER"] = "anthropic"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--cutoff", default="2026-04-29T09:30:00-04:00")
    parser.add_argument(
        "--candidate", default="quality_long",
        choices=("quality_long", "surge_short"),
    )
    args = parser.parse_args()

    from src.evidence_packet import (
        generate_evidence_packet, PITViolationError,
    )
    from src.agents.runner import run_all_agents_for_candidate
    from src.llm.factory import get_provider
    from src.llm.cache import LLMCache
    from src.llm.anthropic_provider import (
        get_cost_ledger, AnthropicProviderError,
    )

    print(f"\n=== Anthropic E2E smoke run ===")
    print(f"  ticker      : {args.ticker}")
    print(f"  cutoff      : {args.cutoff}")
    print(f"  candidate   : {args.candidate}")

    ledger = get_cost_ledger()
    ledger.reset()

    t_wall = time.perf_counter()

    try:
        packet = generate_evidence_packet(
            ticker=args.ticker,
            decision_timestamp=args.cutoff,
            strict_pit_mode=True,
        )
    except PITViolationError as e:
        print(f"\n  ABORT  PITViolationError: {e}")
        for v in e.violations[:5]:
            print(f"           {v}")
        return 2

    print(
        f"  packet hash : {packet['envelope'].get('evidence_packet_hash')}"
    )
    print(f"  pit_mode    : {packet['envelope'].get('pit_mode')}")

    try:
        provider = get_provider()
    except AnthropicProviderError as e:
        print(f"\n  ABORT  Anthropic provider construction failed: {e}")
        return 3

    cache_root = ROOT / "data" / "cache" / "llm_smoke_anthropic"
    cache = LLMCache(root=cache_root)

    try:
        result = run_all_agents_for_candidate(
            evidence_packet=packet,
            candidate_type=args.candidate,
            provider=provider,
            cache=cache,
            agent_mode="multi",
            topology="pipeline",
            force_refresh=True,   # smoke = no stale cache hits
        )
    except AnthropicProviderError as e:
        print(f"\n  ABORT  Anthropic call failed: {e}")
        elapsed = time.perf_counter() - t_wall
        ledger_total = ledger.total_usd()
        print(f"  cumulative cost so far: ${ledger_total:.5f}")
        print(f"  wall-clock so far     : {elapsed:.2f} s")
        return 4

    elapsed = time.perf_counter() - t_wall

    # Per-agent model used (every call recorded in the ledger).
    models_used: dict[str, str] = {}
    for call in ledger.calls():
        agent = call.get("agent_schema_name") or "unknown"
        models_used[agent] = call["model"]
    cache_summary = result["cache_summary"]
    n_agents = sum(1 for _ in ledger.calls())
    cached_read_total = sum(
        c["input_tokens_cached_read"] for c in ledger.calls()
    )
    cached_write_total = sum(
        c["input_tokens_cached_write"] for c in ledger.calls()
    )
    input_uncached_total = sum(
        c["input_tokens_uncached"] for c in ledger.calls()
    )
    output_total = sum(c["output_tokens"] for c in ledger.calls())

    final = result.get("final_decision") or {}
    decision = final.get("decision") or final.get("recommended_action")

    print(f"\n  ── Smoke run summary ──")
    print(f"  total cost USD          : ${ledger.total_usd():.5f}")
    print(f"  per-agent model         : {json.dumps(models_used, indent=2)}")
    print(f"  agent calls (total)     : {n_agents}")
    print(f"  cache hits (on-disk)    : {cache_summary['hits']}")
    print(f"  cache misses (on-disk)  : {cache_summary['misses']}")
    print(f"  prompt-cache write tok  : {cached_write_total}")
    print(f"  prompt-cache read tok   : {cached_read_total}")
    print(f"  input uncached tok      : {input_uncached_total}")
    print(f"  output tok              : {output_total}")
    print(f"  final decision          : {decision!r}")
    print(f"  wall-clock              : {elapsed:.2f} s")

    if ledger.total_usd() > 0.50:
        print(f"\n  WARN  cost ${ledger.total_usd():.4f} exceeds $0.50 target")

    return 0


if __name__ == "__main__":
    sys.exit(main())
