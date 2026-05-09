"""
Solo orchestrator wrapper (RULES.md §16.2 / Bucket A baseline of §26).

The single-agent ablation baseline. Reuses runner.run_all_agents_for_candidate
under (agent_mode='solo', topology='solo'). The actual single-agent
prompt body lives in src/agents/baseline_solo.py +
src/agents/prompts/baseline_solo_v0.1.txt.

This wrapper exists so the pnl_backtest harness and any standalone
ablation driver can swap topologies by importing from a stable
location, without coupling to runner internals (the runner's _run_solo
helper is private).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from src.agents.runner import run_all_agents_for_candidate


def run_solo(
    *,
    evidence_packet: dict,
    candidate_type: Literal["surge_short", "quality_long"],
    provider: Optional[Any] = None,
    cache: Optional[Any] = None,
    force_refresh: bool = False,
) -> dict:
    """Run the single-agent baseline (baseline_solo) on the packet.

    Returns the same run-level envelope shape as
    run_all_agents_for_candidate(agent_mode='multi', topology='pipeline'),
    so downstream pnl_backtest / dashboard code can consume both
    interchangeably (final_decision in the same place; agent_outputs
    has one key, 'baseline_solo').
    """
    return run_all_agents_for_candidate(
        evidence_packet=evidence_packet,
        candidate_type=candidate_type,
        provider=provider,
        cache=cache,
        force_refresh=force_refresh,
        agent_mode="solo",
        topology="pipeline",   # ignored when agent_mode='solo'
    )


__all__ = ["run_solo"]
