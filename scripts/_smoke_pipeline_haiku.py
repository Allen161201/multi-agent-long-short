"""
Throwaway: Haiku-pinned single-ticker pipeline (Step 2, Option A).

Constructs AnthropicProvider(default_model="claude-haiku-4-5-20251001")
explicitly so the runner picks Haiku via runner._effective_model_id().
Mirrors scripts/smoke_test_anthropic_e2e.py logic but does NOT modify
that script (preserves its default-Sonnet behaviour for production
smoke runs).

Will be deleted after the verification task closes.

P1.5 workaround: cutoff is yesterday-close (16:15 ET), not today-open
(09:30 ET), because the price_snapshot block always emits as_of=16:15
regardless of cutoff and an intraday cutoff would trigger
PITViolationError. Bug logged in deferred tracker.

Note on path: the user-requested layout `data/decisions/2026-04-29/16:15/`
contains a colon, which is invalid on NTFS. Using `16-15` instead.
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

# Force the Anthropic provider for this driver. Even though we construct
# it directly, setting LLM_PROVIDER avoids any default-provider drift in
# nested calls (e.g. an agent that calls get_provider() defensively).
os.environ["LLM_PROVIDER"] = "anthropic"

HAIKU_MODEL = "claude-haiku-4-5-20251001"
TICKER = "AAPL"
CANDIDATE_TYPE = "quality_long"
CUTOFF = "2026-04-29T16:15:00-04:00"
DECISION_DIR = ROOT / "data" / "decisions" / "2026-04-29" / "16-15"


def _fingerprint(api_key: str) -> str:
    prefix = "sk-ant-api03-"
    body = api_key[len(prefix):] if api_key.startswith(prefix) else api_key
    return body[:8]


def _summarise_agent_output(agent_name: str, parsed: dict) -> str:
    """One-line decision label + confidence + 1-sentence rationale."""
    decision = (
        parsed.get("recommended_action")
        or parsed.get("decision")
        or parsed.get("thesis_status")
        or parsed.get("verification_status")
        or parsed.get("decision_label")
        or "n/a"
    )
    confidence = parsed.get("confidence") or parsed.get("confidence_level") or "n/a"
    rationale = (
        parsed.get("audit_rationale")
        or parsed.get("reasoning_summary")
        or parsed.get("rationale")
        or parsed.get("notes")
        or ""
    )
    if isinstance(rationale, list):
        rationale = "; ".join(str(r) for r in rationale)
    rationale = str(rationale).replace("\n", " ").strip()
    if len(rationale) > 220:
        rationale = rationale[:217] + "..."
    return f"  {agent_name:18s} | decision={decision!s:30s} | conf={confidence!s:8s} | {rationale}"


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ABORT  ANTHROPIC_API_KEY not set")
        return 2

    print("=== Step 2: Haiku-pinned single-ticker pipeline ===")
    print(f"  ticker          : {TICKER}")
    print(f"  candidate_type  : {CANDIDATE_TYPE}")
    print(f"  cutoff          : {CUTOFF}  (P1.5 workaround: 16:15 ET, not 09:30)")
    print(f"  model           : {HAIKU_MODEL}")
    print(f"  strict_pit_mode : True")
    print(f"  live_adapters   : None  (default: no extras)")
    print(f"  key fp (8)      : {_fingerprint(api_key)}")
    print(f"  decision_dir    : {DECISION_DIR}")

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

    t_wall = time.perf_counter()

    # ── Build evidence packet ─────────────────────────────────────────
    try:
        packet = generate_evidence_packet(
            ticker=TICKER,
            decision_timestamp=CUTOFF,
            strict_pit_mode=True,
            # live_adapters=None ⇒ default behaviour (no alt-data adapters
            # invoked, byte-identical to pre-Step-D packets).
        )
    except PITViolationError as e:
        print(f"\n  ABORT  PITViolationError: {e}")
        for v in e.violations[:8]:
            print(f"           {v}")
        return 2

    envelope = packet.get("envelope", {})
    print(f"\n  packet hash     : {envelope.get('evidence_packet_hash')}")
    print(f"  pit_mode        : {envelope.get('pit_mode')}")
    print(f"  decision_mode   : {envelope.get('decision_mode')}")
    print(f"  decision_ts     : {envelope.get('decision_timestamp')}")
    print(f"  cutoff          : {envelope.get('allowed_data_cutoff')}")
    print(f"  lookahead_safe  : {envelope.get('lookahead_safe')}")
    print(f"  data_after_cut  : {envelope.get('data_after_cutoff_used')}")

    # ── Construct Haiku-pinned provider ────────────────────────────────
    try:
        provider = AnthropicProvider(default_model=HAIKU_MODEL)
    except AnthropicProviderError as e:
        print(f"\n  ABORT  Anthropic provider construction failed: {e}")
        return 3

    # Use a separate cache root so we don't collide with the production
    # smoke driver's cache.
    cache_root = ROOT / "data" / "cache" / "llm_smoke_haiku"
    cache = LLMCache(root=cache_root)

    # ── Run multi/pipeline ─────────────────────────────────────────────
    try:
        result = run_all_agents_for_candidate(
            evidence_packet=packet,
            candidate_type=CANDIDATE_TYPE,
            provider=provider,
            cache=cache,
            agent_mode="multi",
            topology="pipeline",
            force_refresh=True,
        )
    except AnthropicProviderError as e:
        elapsed = time.perf_counter() - t_wall
        print(f"\n  ABORT  Anthropic call failed: {e}")
        print(f"  cumulative cost so far: ${ledger.total_usd():.5f}")
        print(f"  wall-clock so far     : {elapsed:.2f} s")
        return 4

    elapsed = time.perf_counter() - t_wall

    # ── Persist evidence packet + decision result ──────────────────────
    DECISION_DIR.mkdir(parents=True, exist_ok=True)
    packet_path = DECISION_DIR / f"{TICKER}_evidence_packet.json"
    decision_path = DECISION_DIR / f"{TICKER}_{CANDIDATE_TYPE}_decision.json"
    with packet_path.open("w", encoding="utf-8") as f:
        json.dump(packet, f, ensure_ascii=False, indent=2, default=str)
    with decision_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    # ── Per-agent decision summary ─────────────────────────────────────
    print("\n  ── Per-agent decision summary ──")
    for agent_name, parsed in result["agent_outputs"].items():
        try:
            print(_summarise_agent_output(agent_name, parsed))
        except Exception as e:  # pragma: no cover
            print(f"  {agent_name:18s} | <failed-to-summarise: {type(e).__name__}: {e}>")

    # ── Final PM decision ──────────────────────────────────────────────
    final = result.get("final_decision") or {}
    pm_decision = (
        final.get("decision")
        or final.get("recommended_action")
        or final.get("decision_label")
    )
    pm_sizing = (
        final.get("recommended_sizing")
        or final.get("position_sizing")
        or final.get("initial_position_pct")
        or final.get("sizing")
    )
    print("\n  ── Final PM decision ──")
    print(f"    decision : {pm_decision!r}")
    print(f"    sizing   : {pm_sizing!r}")

    # ── Cost / wall-clock ─────────────────────────────────────────────
    ledger_calls = list(ledger.calls())
    n_agents = len(ledger_calls)
    in_uncached = sum(c["input_tokens_uncached"] for c in ledger_calls)
    in_read = sum(c["input_tokens_cached_read"] for c in ledger_calls)
    in_write = sum(c["input_tokens_cached_write"] for c in ledger_calls)
    out_tok = sum(c["output_tokens"] for c in ledger_calls)
    total_usd = ledger.total_usd()

    models_used: dict[str, str] = {}
    for call in ledger_calls:
        agent = call.get("agent_schema_name") or "unknown"
        models_used[agent] = call["model"]

    cache_summary = result["cache_summary"]

    print("\n  ── Cost / wall-clock / cache ──")
    print(f"    total cost USD          : ${total_usd:.5f}")
    print(f"    agent calls (provider)  : {n_agents}")
    print(f"    input uncached tok      : {in_uncached}")
    print(f"    input cached_read tok   : {in_read}")
    print(f"    input cached_write tok  : {in_write}")
    print(f"    output tok              : {out_tok}")
    print(f"    on-disk cache hits      : {cache_summary['hits']}")
    print(f"    on-disk cache misses    : {cache_summary['misses']}")
    print(f"    wall-clock (s)          : {elapsed:.2f}")
    print(f"    per-agent model         : {json.dumps(models_used)}")

    print("\n  ── Artifacts ──")
    print(f"    evidence packet : {packet_path}")
    print(f"    decision result : {decision_path}")

    if total_usd > 0.50:
        print(f"\n  WARN cost ${total_usd:.4f} exceeds $0.50 internal threshold")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(99)
