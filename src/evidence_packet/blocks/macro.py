"""
macro_regime block — schema §3.

Reuses the existing rule-based regime classifier in
`src/agents/macro_regime.py` and the FRED adapter — no reimplementation.
"""
from __future__ import annotations

from datetime import datetime

from agents.macro_regime import classify_regime
from data_adapters import fred_adapter

from ..schema import BlockKey, BlockStatus, Source

# Bumped 2026-05-02 — runtime envelope now emits
# v0.8.4_borrow_cost_cover_cadence. v0.8.4 modifies §5.16 (uniform
# borrow cost 15% → 100% annualized, user-approved per Standing
# Rule 4) and adds §10.14 R-COVER-07 (cover evaluation cadence —
# 12:30 ET daily 3-agent abbreviated pipeline). v0.8.3 added
# §11.14.1/.2/.3 13F cadence sub-rules; v0.8.2 wired compute_ndi();
# v0.8.1 reverted hardcoded NDI/ADaS thresholds; v0.8 added
# §23-§28 NDI/ADaS framework, BIL-default fixed-income sleeve, and
# agent isolation rules.
# Intentional regression-baseline rebase: prior v0.8.3 hash
# (sha256:7aa26c88e1961c4848ada5b920d0d9a67fd94d5e381e0db96a46cd59c9972e95)
# is INVALIDATED; new v0.8.4 hash captured per §22 procedure on
# this commit.
REGIME_RULE_VERSION = "v0.9.0_pass8_hardrule"


def build(*, allowed_data_cutoff: datetime) -> dict:
    """Build macro_regime. The FRED adapter is keyed by date, so we use
    the cutoff's calendar date as the cache key."""
    source_list_entries: list[dict] = []
    quality_flags: list[dict] = []
    pit_flags: list[dict] = []
    agent_notes: list[str] = []

    cutoff_date_str = allowed_data_cutoff.strftime("%Y-%m-%d")

    # PIT-correct path: per-date FRED cache keyed by allowed_data_cutoff.
    # Fallback to dashboard mode only on hard failure (e.g. cache file
    # missing AND FRED API down). Pre-V7 (2026-05-07) this called
    # get_macro_indicators_for_dashboard() unconditionally, which was
    # hard-keyed to today_ET and produced a silent PIT leak in replay
    # mode (today's mock fed_funds=5.33 served instead of March 2025's
    # live 4.33). V7 anchor in tests/test_pit_replay_mode.py guards this.
    try:
        indicators = fred_adapter.get_macro_indicators(cutoff_date_str)
    except Exception as e:  # pragma: no cover — defensive
        quality_flags.append({
            "kind": "fred_date_keyed_call_failed",
            "severity": "warn",
            "detail": f"falling back to dashboard get_macro_indicators: {e}",
        })
        indicators = fred_adapter.get_macro_indicators_for_dashboard()

    regime_payload = classify_regime(indicators)

    # Source detection: did FRED come back live?
    # The dashboard entrypoint writes `_data_mode`; the legacy date-keyed
    # entrypoint writes `_cache_data_mode`. Prefer the modern key, fall
    # back to the legacy one for the date-keyed code path.
    came_from_cache = bool(indicators.get("_from_cache"))
    data_mode = indicators.get("_data_mode") or indicators.get("_cache_data_mode")
    if data_mode == "live" or any(
        isinstance(v, dict) and v.get("source") == "live_fred"
        for v in indicators.values()
    ):
        primary_source = Source.LIVE_FRED
    elif data_mode == "mock":
        primary_source = Source.MOCK_FALLBACK
    else:
        primary_source = Source.LIVE_FRED if not came_from_cache else Source.CACHE

    # Block payload
    block = {
        "status": BlockStatus.OK,
        "source": primary_source,
        "as_of": cutoff_date_str,
        "available_as_of": cutoff_date_str,
        "rule_version": REGIME_RULE_VERSION,

        "macro_regime": regime_payload.get("macro_regime"),
        "macro_condition": regime_payload.get("macro_condition"),
        "regime_label": regime_payload.get("regime_label"),
        "stress_score": regime_payload.get("stress_score"),
        "stress_triggers": regime_payload.get("stress_triggers")
            or regime_payload.get("triggers"),
        "equity_allocation_cap": regime_payload.get("equity_allocation_cap"),
        "fixed_income_base_allocation": regime_payload.get("fixed_income_base_allocation"),
        "equity_discipline": regime_payload.get("equity_discipline"),
        "equity_restriction": regime_payload.get("equity_restriction"),
        "confidence": regime_payload.get("confidence"),
        "evidence": regime_payload.get("evidence"),
        "missing_warnings": regime_payload.get("missing_warnings"),
        "fred_values_used": {
            k: v.get("value") for k, v in indicators.items()
            if isinstance(v, dict) and not k.startswith("_")
        },
        # v0.7 credit-stress side-signals (RULES.md §4.12). Descriptors
        # only — agents read these for contextual reasoning; they are
        # NOT independent trading rules.
        "credit_signal_inputs": regime_payload.get("credit_signal_inputs"),
        "lookahead_safe": True,
        "served_from_cache": came_from_cache,
    }

    source_list_entries.append({
        "label": "fred_macro_indicators",
        "source": primary_source,
        "url": "https://api.stlouisfed.org/fred/series/observations",
        "as_of": cutoff_date_str,
        "served_from_cache": came_from_cache,
    })

    pit_flags.append({
        "field": "macro_regime.fred_values_used",
        "PIT_safe": True,
        "note": "FRED observations cached by date; classification rule-based",
    })

    if primary_source == Source.MOCK_FALLBACK:
        quality_flags.append({
            "kind": "macro_regime_mock_fallback",
            "severity": "warn",
            "detail": "FRED unavailable / DATA_MODE=mock; macro regime is mocked",
        })
    if regime_payload.get("missing_warnings"):
        quality_flags.append({
            "kind": "macro_partial_indicator_set",
            "severity": "info",
            "detail": f"some FRED indicators missing: "
                       f"{regime_payload.get('missing_warnings')}",
        })

    if regime_payload.get("regime_label"):
        agent_notes.append(
            f"Macro regime: {regime_payload.get('regime_label')} "
            f"(stress_score={regime_payload.get('stress_score')}); "
            f"equity_discipline={regime_payload.get('equity_discipline')}"
        )

    return {
        "block": block,
        "source_list_entries": source_list_entries,
        "quality_flags": quality_flags,
        "pit_flags": pit_flags,
        "agent_notes": agent_notes,
        "api_calls_made": 0,  # FRED adapter does not currently expose a per-call counter
    }


def block_key() -> str:
    return BlockKey.MACRO_REGIME
