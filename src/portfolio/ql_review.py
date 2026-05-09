"""
§30.2 Friday QL review (Pass 6, 2026-05-03).

PM evaluates each open quality_long position for §30.1 exit triggers using
a 4-agent abbreviated pipeline: fundamental + valuation + risk + pm.
Skipped (already established at entry): narrative_event, alt_data_verify,
network_effect.

Per §30.1 there are two entry types:
  - long_term: §10.15 network effect + reasonable valuation
  - event_triggered: earnings / event narrative with PM-decided
    profit-take timing

PM decision ∈ {hold, trim, exit}. Trim emits trim_pct ∈ (0.0, 1.0).
Exit emits exit_trigger ∈ {network_effect_degraded, fundamental_breach,
thesis_invalidation, event_playthrough_complete}.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any

from src.agents.runner import run_ql_review_pipeline
from src.evidence_packet.generator import generate_evidence_packet


def run_ql_friday_review(
    *,
    today: date,
    ql_position: dict,
    portfolio_state: dict,
    live_adapters: Any = None,
    rule_version: str = "v0.9.0_pass8_hardrule",
    provider=None,
    cache=None,
) -> dict:
    """
    Generate the QL-review packet for an open quality_long position.
    Run the abbreviated 4-agent pipeline. Return PM decision.

    Returns:
        {
          "decision": "hold" | "trim" | "exit",
          "trim_pct": float | None,
          "rationale": str,
          "exit_trigger": str | None,
          "entry_type_recognized": str | None,
          "cost_usd": float,
          "pm_decision_raw": dict,
        }
    """
    ticker = ql_position.get("ticker")
    if not ticker:
        return _hold("missing ticker on ql_position")

    decision_timestamp = today.isoformat() + "T15:30:00-04:00"

    try:
        packet = generate_evidence_packet(
            ticker=ticker,
            decision_timestamp=decision_timestamp,
            live_adapters=live_adapters,
        )
    except Exception as e:
        return _hold(f"packet_gen_failed: {type(e).__name__}: {e}")

    packet = copy.deepcopy(packet)
    packet["review_context"] = {
        "mode": "ql_review",
        "review_mode": True,
        "existing_position": _summarize_position(ql_position, today),
        "rule_version": rule_version,
    }
    packet["candidate_type"] = "quality_long_review"

    pipeline_result = run_ql_review_pipeline(
        evidence_packet=packet,
        existing_ql_position=ql_position,
        provider=provider,
        cache=cache,
    )

    pm_record = pipeline_result.get("pm") or {}
    pm_parsed = pm_record.get("parsed_output") or pm_record.get("parsed") or {}
    decision = (pm_parsed.get("decision")
                or pm_parsed.get("recommended_action")
                or "hold").lower()
    if decision not in ("hold", "trim", "exit"):
        decision = "hold"

    trim_pct = pm_parsed.get("trim_pct")
    try:
        trim_pct = float(trim_pct) if trim_pct is not None else None
    except (TypeError, ValueError):
        trim_pct = None
    if trim_pct is not None:
        trim_pct = max(0.0, min(1.0, trim_pct))

    return {
        "decision": decision,
        "trim_pct": trim_pct,
        "rationale": (pm_parsed.get("reason")
                      or pm_parsed.get("decision_rationale") or ""),
        "exit_trigger": pm_parsed.get("exit_trigger"),
        "entry_type_recognized": pm_parsed.get("entry_type_recognized"),
        "cost_usd": float(pipeline_result.get("cost_usd", 0.0) or 0.0),
        "pm_decision_raw": pm_parsed,
    }


def apply_ql_action_to_state(
    state: dict,
    ql_pos: dict,
    action: dict,
) -> None:
    """Mutate state in place per agent's hold|trim|exit decision."""
    decision = (action.get("decision") or "hold").lower()
    if decision == "exit":
        _apply_exit(state, ql_pos, action)
    elif decision == "trim":
        _apply_trim(state, ql_pos, action)
    # hold: no state change


def _apply_exit(state: dict, ql_pos: dict, action: dict) -> None:
    current_price = float(
        ql_pos.get("current_price")
        or ql_pos.get("entry_price", 0.0)
    )
    shares = float(ql_pos.get("shares", 0.0))
    proceeds = current_price * shares
    realized_pnl = proceeds - (float(ql_pos.get("entry_price", 0.0)) * shares)
    if "cash_balance" in state:
        state["cash_balance"] = float(state["cash_balance"]) + proceeds
    elif "cash" in state:
        state["cash"] = float(state["cash"]) + proceeds
    # §5.16 v2.15 (PART 4 wiring 2026-05-08): flat 15 bps tx cost on QL exit.
    from src.portfolio.cost_helpers import apply_tx_cost
    exit_tx = apply_tx_cost(proceeds)
    if "cash_balance" in state:
        state["cash_balance"] = float(state["cash_balance"]) - exit_tx
    elif "cash" in state:
        state["cash"] = float(state["cash"]) - exit_tx
    state["tx_cost_today"] = float(state.get("tx_cost_today", 0.0)) + exit_tx
    state["tx_cost_cumulative"] = float(state.get("tx_cost_cumulative", 0.0)) + exit_tx
    ql_pos["tx_cost_exit_usd"] = float(ql_pos.get("tx_cost_exit_usd", 0.0)) + exit_tx
    ql_pos["status"] = "closed"
    ql_pos["close_date"] = state.get("as_of")
    ql_pos["close_price"] = current_price
    ql_pos["realized_pnl"] = realized_pnl
    state.setdefault("realized_pnl_history", []).append({
        "ticker": ql_pos.get("ticker"),
        "side": "long",
        "entry_date": ql_pos.get("entry_date"),
        "close_date": state.get("as_of"),
        "entry_price": ql_pos.get("entry_price"),
        "close_price": current_price,
        "shares": shares,
        "realized_pnl": realized_pnl,
        "rationale": action.get("rationale", ""),
        "exit_trigger": action.get("exit_trigger"),
    })


def _apply_trim(state: dict, ql_pos: dict, action: dict) -> None:
    trim_pct = action.get("trim_pct")
    try:
        trim_pct = float(trim_pct) if trim_pct is not None else 0.0
    except (TypeError, ValueError):
        return
    if trim_pct <= 0 or trim_pct >= 1:
        return
    shares = float(ql_pos.get("shares", 0.0))
    shares_to_trim = round(shares * trim_pct, 2)
    if shares_to_trim == 0:
        return
    current_price = float(
        ql_pos.get("current_price")
        or ql_pos.get("entry_price", 0.0)
    )
    proceeds = current_price * shares_to_trim
    realized_pnl_partial = (
        (current_price - float(ql_pos.get("entry_price", 0.0))) * shares_to_trim
    )
    if "cash_balance" in state:
        state["cash_balance"] = float(state["cash_balance"]) + proceeds
    elif "cash" in state:
        state["cash"] = float(state["cash"]) + proceeds
    # §5.16 v2.15 (PART 4 wiring 2026-05-08): flat 15 bps tx cost on QL trim.
    from src.portfolio.cost_helpers import apply_tx_cost
    trim_tx = apply_tx_cost(proceeds)
    if "cash_balance" in state:
        state["cash_balance"] = float(state["cash_balance"]) - trim_tx
    elif "cash" in state:
        state["cash"] = float(state["cash"]) - trim_tx
    state["tx_cost_today"] = float(state.get("tx_cost_today", 0.0)) + trim_tx
    state["tx_cost_cumulative"] = float(state.get("tx_cost_cumulative", 0.0)) + trim_tx
    ql_pos["tx_cost_trim_usd"] = float(ql_pos.get("tx_cost_trim_usd", 0.0)) + trim_tx
    ql_pos["shares"] = shares - shares_to_trim
    ql_pos.setdefault("trim_history", []).append({
        "trim_date": state.get("as_of"),
        "shares_trimmed": shares_to_trim,
        "trim_price": current_price,
        "realized_pnl_partial": realized_pnl_partial,
        "rationale": action.get("rationale", ""),
    })
    state.setdefault("realized_pnl_history", []).append({
        "ticker": ql_pos.get("ticker"),
        "side": "long_trim",
        "trim_date": state.get("as_of"),
        "shares_trimmed": shares_to_trim,
        "trim_price": current_price,
        "realized_pnl_partial": realized_pnl_partial,
    })


def _summarize_position(pos: dict, today: date) -> dict:
    entry_date = pos.get("entry_date") or pos.get("entry_date_iso")
    days_held = None
    try:
        if entry_date:
            d0 = date.fromisoformat(str(entry_date)[:10])
            days_held = (today - d0).days
    except Exception:
        days_held = None
    return {
        "ticker": pos.get("ticker"),
        "side": pos.get("side"),
        "entry_type": pos.get("entry_type", "long_term"),
        "entry_price": pos.get("entry_price"),
        "current_price": pos.get("current_price"),
        "shares": pos.get("shares"),
        "entry_date": entry_date,
        "days_held": days_held,
        "size_pct_at_open": pos.get("size_pct_at_open"),
        "unrealized_pnl": (
            (float(pos.get("current_price") or 0.0)
             - float(pos.get("entry_price") or 0.0))
            * float(pos.get("shares") or 0.0)
            if pos.get("current_price") and pos.get("entry_price") else None
        ),
    }


def _hold(reason: str) -> dict:
    return {
        "decision": "hold",
        "trim_pct": None,
        "rationale": f"ql_review bypassed: {reason}",
        "exit_trigger": None,
        "entry_type_recognized": None,
        "cost_usd": 0.0,
        "pm_decision_raw": {},
    }


__all__ = [
    "run_ql_friday_review",
    "apply_ql_action_to_state",
]
