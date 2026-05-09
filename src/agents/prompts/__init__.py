"""Frozen prompt templates per agent.

Each module exports:
    PROMPT_VERSION       — bump this string to invalidate this agent's cache.
    SYSTEM_PROMPT        — role + invariants + anti-hindsight + format.
    USER_PROMPT_TEMPLATE — Python str.format() template; placeholders use
                           double-brace literals like {{evidence_packet_json}}.
    OUTPUT_SCHEMA_NAME   — the pydantic model name in src.agents.schemas.

The runner imports these by agent name and never modifies them.
"""
from . import (
    alt_data_verification_agent,
    fund_net_val_agent,         # DEPRECATED — see fundamental/network_effect/valuation
    fundamental_agent,
    narrative_event_agent,
    network_effect_agent,
    pm_agent,
    pm_flat_agent,
    quality_long_agent,
    risk_agent,
    risk_pm_agent,              # DEPRECATED — see risk_agent / pm_agent
    surge_short_agent,
    valuation_agent,
)

# Step A2: baseline_solo lives at src/agents/baseline_solo.py rather than
# src/agents/prompts/, so it is imported by absolute path.
from .. import baseline_solo as baseline_solo_agent

# Map each agent_name → its prompt module + schema name.
#
# §10.14 split (2026-04-29): the legacy unified `fund_net_val` and
# `risk_pm` entries are retained for backward compatibility but are
# DEPRECATED. The default pipeline now uses the split agents (see
# runner.PIPELINE_PRELUDE / RISK_AGENT / PM_AGENT). Separate cache
# namespaces follow naturally from the new agent_name strings.
AGENT_PROMPTS = {
    "narrative_event":      narrative_event_agent,
    "alt_data_verify":      alt_data_verification_agent,
    # §10.14 split — Fund/Net/Val
    "fundamental":          fundamental_agent,
    "network_effect":       network_effect_agent,
    "valuation":            valuation_agent,
    # Legacy unified Fund/Net/Val (deprecated; retained for backward compat)
    "fund_net_val":         fund_net_val_agent,
    "surge_short":          surge_short_agent,
    "quality_long":         quality_long_agent,
    # §10.14 split — Risk + PM
    "risk":                 risk_agent,
    "pm":                   pm_agent,
    # Legacy unified Risk/PM (deprecated; retained for backward compat)
    "risk_pm":              risk_pm_agent,
    "baseline_solo":        baseline_solo_agent,
    "pm_flat":              pm_flat_agent,
}

__all__ = ["AGENT_PROMPTS"]
