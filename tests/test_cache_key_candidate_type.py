"""
Unit test for D1 Step A1: Risk/PM cache-key candidate_type extension.

Same ticker, packet, timestamp — different candidate_type → Risk/PM and
baseline_solo cache keys MUST differ; the four specialists' keys MUST
remain identical. The test directly exercises the runner's key-derivation
path by stubbing the provider and cache so no I/O happens.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.agents import runner  # noqa: E402


PACKET = {
    "envelope": {
        "ticker": "AAPL",
        "decision_timestamp": "2026-04-27T16:00:00-04:00",
        "evidence_packet_hash": "sha256:deadbeef",
    },
}


class _CapturingCache:
    """Cache stand-in that captures every (agent, key) lookup and never
    serves a hit, so each run_agent call follows the MISS path through
    key derivation. We only care about the cache_key the runner builds."""

    def __init__(self) -> None:
        self.lookups: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str, dict]] = []

    def get(self, agent_name, cache_key):
        self.lookups.append((agent_name, cache_key))
        return None

    def put(self, agent_name, cache_key, record):
        self.writes.append((agent_name, cache_key, record))
        return Path(f"/tmp/{agent_name}/{cache_key}.json")


class _SkeletonProvider:
    """Stub provider that delegates to the real DeterministicStubProvider
    skeleton builder, but skips the latency timing — keeps the test fast
    and deterministic."""
    name = "deterministic_stub"

    def complete(self, *, system_prompt, user_prompt, model_id,
                 max_tokens, temperature, response_format,
                 agent_schema_name=None):
        from src.llm.deterministic_stub import build_stub_skeleton
        import json as _json
        skeleton = build_stub_skeleton(agent_schema_name)
        return {
            "raw_text": _json.dumps(skeleton, ensure_ascii=False),
            "model_id": "stub-v1",
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": 0,
            "stop_reason": "stub",
            "provider": "deterministic_stub",
            "cache_used": False,
        }


def _key_for(agent_name, candidate_type):
    cache = _CapturingCache()
    provider = _SkeletonProvider()
    runner.run_agent(
        agent_name=agent_name,
        evidence_packet=PACKET,
        candidate_type=candidate_type,
        provider=provider,
        cache=cache,
        force_refresh=True,
    )
    assert cache.writes, "expected runner to put one record"
    return cache.writes[-1][1]


def main() -> int:
    failures: list[str] = []

    def expect(name, ok, detail=""):
        mark = "OK" if ok else "FAIL"
        print(f"  [{mark}] {name}  {detail}")
        if not ok:
            failures.append(name)

    print("\n=== Step A1 cache-key test ===\n")

    # 1) Risk/PM keys diverge on candidate_type.
    k_risk_short = _key_for("risk_pm", "surge_short")
    k_risk_long = _key_for("risk_pm", "quality_long")
    expect("risk_pm key changes with candidate_type",
           k_risk_short != k_risk_long,
           f"\n     surge_short={k_risk_short}\n     quality_long={k_risk_long}")

    # 2) The four specialists' keys are identical across candidate_type.
    for agent in ("narrative_event", "alt_data_verify", "fund_net_val"):
        k_a = _key_for(agent, "surge_short")
        k_b = _key_for(agent, "quality_long")
        expect(f"{agent} key independent of candidate_type",
               k_a == k_b,
               f"\n     {k_a}")

    # 3) Sleeve agents are mutually exclusive — they only ever run for
    # one candidate_type. Run each in its native mode, then re-run with
    # the OPPOSITE candidate_type and confirm the key still does not
    # change (the agent itself is candidate-type-agnostic by namespace).
    k_surge_a = _key_for("surge_short", "surge_short")
    k_surge_b = _key_for("surge_short", "quality_long")
    expect("surge_short key independent of candidate_type",
           k_surge_a == k_surge_b,
           f"\n     {k_surge_a}")
    k_qual_a = _key_for("quality_long", "quality_long")
    k_qual_b = _key_for("quality_long", "surge_short")
    expect("quality_long key independent of candidate_type",
           k_qual_a == k_qual_b,
           f"\n     {k_qual_a}")

    # 4) baseline_solo (added in Step A2) should also include candidate_type.
    if "baseline_solo" in runner.AGENT_PROMPTS \
            if hasattr(runner, "AGENT_PROMPTS") else \
            "baseline_solo" in __import__("src.agents.prompts", fromlist=["AGENT_PROMPTS"]).AGENT_PROMPTS:
        k_solo_short = _key_for("baseline_solo", "surge_short")
        k_solo_long = _key_for("baseline_solo", "quality_long")
        expect("baseline_solo key changes with candidate_type",
               k_solo_short != k_solo_long,
               f"\n     surge_short={k_solo_short}\n     quality_long={k_solo_long}")

    print()
    if failures:
        print(f"  RESULT: {len(failures)} failure(s): {failures}")
        return 1
    print("  RESULT: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
