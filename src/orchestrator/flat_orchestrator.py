"""
Flat-ensemble orchestrator wrapper (RULES.md §16.4).

Reuses runner.run_all_agents_for_candidate under
(agent_mode='multi', topology='flat'). Specialists run independently
against the same evidence packet (no upstream synthesis, no cross-talk).
Their parsed outputs are aggregated by `pm_flat` (separate prompt +
cache namespace from pipeline-mode `pm`).

Per RULES.md §25 (agent isolation): specialist agents in flat mode see
the raw evidence packet — they do NOT see portfolio state or ADaS
history. Only the pm_flat aggregator combines verdicts; ADaS is
pipeline-mode-only (the t+1 lag is a sequential-pipeline construct).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from src.agents.runner import run_all_agents_for_candidate


def run_flat_ensemble(
    *,
    evidence_packet: dict,
    candidate_type: Literal["surge_short", "quality_long"],
    provider: Optional[Any] = None,
    cache: Optional[Any] = None,
    force_refresh: bool = False,
) -> dict:
    """Run the flat ensemble: 6 specialists in parallel + pm_flat
    aggregator. Returns the same run-level envelope shape as the
    pipeline orchestrator. final_decision is the pm_flat output.
    """
    return run_all_agents_for_candidate(
        evidence_packet=evidence_packet,
        candidate_type=candidate_type,
        provider=provider,
        cache=cache,
        force_refresh=force_refresh,
        agent_mode="multi",
        topology="flat",
    )


__all__ = ["run_flat_ensemble"]
