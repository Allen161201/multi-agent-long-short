"""
§10.14.R-COVER-07 / R-COVER-08 / R-COVER-09 cover evaluation.

Pass 8 Step B2-mini-D (2026-05-05): two pre-LLM mechanical bypasses
(R-COVER-08 ≥35% mandatory profit, R-COVER-09 30-trading-day +
loss ≤10%) fire BEFORE any LLM call so cost is $0 for those branches.
The non-bypass path delegates to §33.5 default 2-agent pipeline
(risk + pm; see runner.run_cover_pipeline) or — when the harness
detects a new corporate_calendar event for the ticker — to the
4-agent narrative-refresh variant (run_cover_pipeline_with_narrative_refresh).

Excluded per §10.14.R-COVER-01 + R-COVER-06 + user 2026-05-03:
  - Fundamental (R-COVER-01: bear bias against covering)
  - Macro (R-COVER-06: cover decision independent of regime)
  - Valuation (user 2026-05-03: poor fundamentals → permanently
    "expensive" → would prevent cover)
  - Network_effect / Narrative_event (entry-time verdict persists
    unless corporate_calendar event triggers narrative-refresh)

PM owns the cover decision per R-COVER-02 and emits
cover_decision_dimensions_weighed per R-COVER-05.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any

from src.agents.runner import (
    run_cover_pipeline,
    run_cover_pipeline_with_narrative_refresh,
)
from src.evidence_packet.generator import generate_evidence_packet
from src.utils.trading_calendar import trading_days_between


COVER_STRIPPED_KEYS = (
    "valuation_snapshot",
    "valuation_assessment",
)

# §10.14.R-COVER-08 mandatory profit threshold; §33.4 ladder top band.
R_COVER_08_PROFIT_THRESHOLD = 0.35
# §10.14.R-COVER-09 mandatory hold-time threshold (NYSE trading days per §29.1).
R_COVER_09_DAYS_THRESHOLD = 30
# §10.14.R-COVER-09 loss-magnitude bound (loss ≤10% means profit_pct ≥ -0.10).
R_COVER_09_LOSS_BOUND = -0.10


def compute_unrealized_profit_pct(
    short_position: dict, current_price: float,
) -> float:
    """Short profit = (entry - current) / entry. Positive when current < entry.

    Pass 8 Step B2-mini-D (2026-05-05): supports pyramid positions via
    weighted_avg_entry_price; otherwise uses entry_price.
    """
    entry = (short_position.get("weighted_avg_entry_price")
             or short_position.get("entry_price"))
    if entry is None or float(entry) <= 0:
        raise ValueError(
            f"Invalid entry price for "
            f"{short_position.get('ticker', '?')}: {entry}"
        )
    if current_price is None or float(current_price) <= 0:
        raise ValueError(
            f"compute_unrealized_profit_pct called with "
            f"current_price={current_price} for "
            f"{short_position.get('ticker', '?')}; cannot compute profit."
        )
    entry = float(entry)
    return (entry - float(current_price)) / entry


def run_cover_evaluation(
    *,
    today: date,
    short_position: dict,
    portfolio_state: dict,
    live_adapters: Any = None,
    rule_version: str = "v0.9.0_pass8_hardrule",
    provider=None,
    cache=None,
    new_corporate_event: bool = False,
) -> dict:
    """Cover-eval entry point.

    Pre-LLM mechanical bypasses (Pass 8 Step B2-mini-D, 2026-05-05):
      - §10.14.R-COVER-08: profit_pct >= 0.35 → cover_full, $0 LLM cost
      - §10.14.R-COVER-09: days_held >= 30 (NYSE trading days per §29.1)
        AND profit_pct >= -0.10 (loss ≤ 10%) → cover_full, $0 LLM cost

    Otherwise runs the §33.5 LLM pipeline:
      - new_corporate_event=False (default): risk + pm only (2 agents)
      - new_corporate_event=True: narrative_event + alt_data_verify
        + risk + pm (4 agents) per R-COVER-10 condition iv

    Returns:
      {
        "decision": "cover" | "hold",
        "action":   "cover_full" | "hold",
        "rationale": str,
        "dimensions_weighed": list[str],
        "cost_usd": float,
        "llm_bypassed": bool,
        "agent_count": int,
        "profit_pct": float | None,
        "days_held":  int   | None,
        "shares_to_cover": float,
        "pm_decision_raw": dict,
        "alt_data_verify_raw": dict,
        "risk_raw": dict,
        "narrative_event_raw": dict,
      }
    """
    ticker = short_position.get("ticker")
    if not ticker:
        return _hold("missing ticker on short_position")

    # ── Pre-LLM mechanical bypasses (§10.14.R-COVER-08 + R-COVER-09) ──
    current_price = short_position.get("current_price")
    profit_pct: float | None = None
    days_held: int | None = None

    if current_price is not None and float(current_price) > 0:
        try:
            profit_pct = compute_unrealized_profit_pct(
                short_position, float(current_price))
        except ValueError as e:
            # entry_price missing/invalid: fall through to hold (no LLM)
            return _hold(f"profit_compute_failed: {e}")

        # §10.14.R-COVER-08 ≥35% mandatory profit cover
        if profit_pct >= R_COVER_08_PROFIT_THRESHOLD:
            return _bypass_cover(
                reason=(
                    f"§10.14.R-COVER-08 mandatory profit "
                    f">={int(R_COVER_08_PROFIT_THRESHOLD*100)}%"
                ),
                profit_pct=profit_pct,
                days_held=None,
                shares=float(short_position.get("shares", 0.0)),
                rule_id="R-COVER-08",
            )

        # §10.14.R-COVER-09 30-trading-day stop on small-loss-or-better
        try:
            entry_d = short_position.get("entry_date")
            if entry_d:
                entry_iso = str(entry_d)[:10]
                days_held = max(
                    0, len(trading_days_between(entry_iso, today.isoformat())) - 1
                )
        except Exception:
            days_held = None
        if (days_held is not None
                and days_held >= R_COVER_09_DAYS_THRESHOLD
                and profit_pct >= R_COVER_09_LOSS_BOUND):
            return _bypass_cover(
                reason=(
                    f"§10.14.R-COVER-09 mandatory "
                    f">={R_COVER_09_DAYS_THRESHOLD}-trading-day stop, "
                    f"loss within {int(abs(R_COVER_09_LOSS_BOUND)*100)}%"
                ),
                profit_pct=profit_pct,
                days_held=days_held,
                shares=float(short_position.get("shares", 0.0)),
                rule_id="R-COVER-09",
            )

    # ── §33.5 LLM pipeline (no bypass condition met) ──
    decision_timestamp = today.isoformat() + "T12:30:00-04:00"

    try:
        packet = generate_evidence_packet(
            ticker=ticker,
            decision_timestamp=decision_timestamp,
            live_adapters=live_adapters,
        )
    except Exception as e:
        return _hold(f"packet_gen_failed: {type(e).__name__}: {e}")

    # §10.14 + user 2026-05-03: strip valuation fields before LLM call.
    packet = copy.deepcopy(packet)
    for k in COVER_STRIPPED_KEYS:
        packet.pop(k, None)

    packet["review_context"] = {
        "mode": "cover_eval",
        "cover_mode": True,
        "existing_position": _summarize_position(short_position, today),
        "profit_pct": profit_pct,
        "days_held": days_held,
        "rule_version": rule_version,
        "new_corporate_event": new_corporate_event,
    }
    packet["candidate_type"] = "surge_short_cover"

    pipeline_fn = (run_cover_pipeline_with_narrative_refresh
                   if new_corporate_event else run_cover_pipeline)
    pipeline_result = pipeline_fn(
        evidence_packet=packet,
        existing_short_position=short_position,
        provider=provider,
        cache=cache,
    )

    pm_record = pipeline_result.get("pm") or {}
    pm_parsed = pm_record.get("parsed_output") or pm_record.get("parsed") or {}
    decision = (pm_parsed.get("decision")
                or pm_parsed.get("recommended_action")
                or "hold").lower()
    if decision not in ("cover", "hold", "cover_full"):
        decision = "hold"
    action = "cover_full" if decision in ("cover", "cover_full") else "hold"

    return {
        "decision": decision,
        "action": action,
        "rationale": (pm_parsed.get("cover_decision_rationale")
                      or pm_parsed.get("reason") or ""),
        "dimensions_weighed": list(
            pm_parsed.get("cover_decision_dimensions_weighed") or []
        ),
        "cost_usd": float(pipeline_result.get("cost_usd", 0.0) or 0.0),
        "llm_bypassed": False,
        "agent_count": int(pipeline_result.get("agent_count", 0)),
        "profit_pct": profit_pct,
        "days_held": days_held,
        "shares_to_cover": (float(short_position.get("shares", 0.0))
                            if action == "cover_full" else 0.0),
        "pm_decision_raw": pm_parsed,
        "alt_data_verify_raw": (pipeline_result.get("alt_data_verify") or {})
            .get("parsed_output", {}),
        "risk_raw": (pipeline_result.get("risk") or {})
            .get("parsed_output", {}),
        "narrative_event_raw": (pipeline_result.get("narrative_event") or {})
            .get("parsed_output", {}),
    }


def _bypass_cover(
    *,
    reason: str,
    profit_pct: float,
    days_held: int | None,
    shares: float,
    rule_id: str,
) -> dict:
    """Mechanical R-COVER-08 / R-COVER-09 bypass — no LLM call, $0 cost."""
    return {
        "decision": "cover",
        "action": "cover_full",
        "rationale": reason,
        "dimensions_weighed": ["mechanical_bypass", rule_id],
        "cost_usd": 0.0,
        "llm_bypassed": True,
        "agent_count": 0,
        "profit_pct": profit_pct,
        "days_held": days_held,
        "shares_to_cover": shares,
        "pm_decision_raw": {},
        "alt_data_verify_raw": {},
        "risk_raw": {},
        "narrative_event_raw": {},
    }


def apply_cover_to_state(
    state: dict,
    short_pos: dict,
    cover_result: dict,
) -> None:
    """Close the short position, realize P&L, update cash.

    Pass 8 Step B2-mini-D (2026-05-05): raise on missing/invalid
    current_price rather than silently falling back to entry_price (which
    would realize $0 P&L on every cover and silently corrupt the
    backtest's PnL series). Caller MUST set short_pos['current_price']
    before invoking.
    """
    cp_raw = short_pos.get("current_price")
    if cp_raw is None or float(cp_raw) <= 0:
        raise ValueError(
            f"apply_cover_to_state called with current_price={cp_raw} "
            f"for {short_pos.get('ticker', '?')}; this would silently "
            f"realize $0 P&L. Caller MUST update current_price before cover."
        )
    current_price = float(cp_raw)
    entry_price = float(short_pos.get("entry_price", 0.0))
    shares = float(short_pos.get("shares", 0.0))
    realized_pnl = (entry_price - current_price) * shares
    if "cash_balance" in state:
        state["cash_balance"] = (float(state["cash_balance"])
                                 + entry_price * shares + realized_pnl)
    elif "cash" in state:
        state["cash"] = (float(state["cash"])
                         + entry_price * shares + realized_pnl)
    # §5.16 v2.15 (PART 4 wiring 2026-05-08): flat 15 bps tx cost on the
    # cover leg. Cover notional = current_price * shares (the buyback
    # amount). Tx cost is a cash deduction; entry-leg tx cost was already
    # charged at the original short-entry site.
    from src.portfolio.cost_helpers import apply_tx_cost
    cover_tx = apply_tx_cost(current_price * shares)
    if "cash_balance" in state:
        state["cash_balance"] = float(state["cash_balance"]) - cover_tx
    elif "cash" in state:
        state["cash"] = float(state["cash"]) - cover_tx
    state["tx_cost_today"] = float(state.get("tx_cost_today", 0.0)) + cover_tx
    state["tx_cost_cumulative"] = float(state.get("tx_cost_cumulative", 0.0)) + cover_tx
    short_pos["tx_cost_cover_usd"] = float(short_pos.get("tx_cost_cover_usd", 0.0)) + cover_tx
    short_pos["status"] = "closed"
    short_pos["close_date"] = state.get("as_of")
    short_pos["close_price"] = current_price
    short_pos["realized_pnl"] = realized_pnl
    state.setdefault("realized_pnl_history", []).append({
        "ticker": short_pos.get("ticker"),
        "side": "short",
        "entry_date": short_pos.get("entry_date"),
        "close_date": state.get("as_of"),
        "entry_price": entry_price,
        "close_price": current_price,
        "shares": shares,
        "realized_pnl": realized_pnl,
        "rationale": cover_result.get("rationale", ""),
        "dimensions_weighed": cover_result.get("dimensions_weighed", []),
    })


def _summarize_position(pos: dict, today: date) -> dict:
    """Pass 8 Step B2-mini-D (2026-05-05): days_held now uses NYSE
    trading days per §29.1, not calendar days."""
    entry_date = pos.get("entry_date") or pos.get("entry_date_iso")
    days_held = None
    try:
        if entry_date:
            entry_iso = str(entry_date)[:10]
            days_held = max(
                0, len(trading_days_between(entry_iso, today.isoformat())) - 1
            )
    except Exception:
        days_held = None
    return {
        "ticker": pos.get("ticker"),
        "side": pos.get("side"),
        "entry_price": pos.get("entry_price"),
        "current_price": pos.get("current_price"),
        "shares": pos.get("shares"),
        "entry_date": entry_date,
        "days_held": days_held,
        "size_pct_at_open": pos.get("size_pct_at_open"),
        "notional_at_open": pos.get("notional_at_open"),
    }


def _hold(reason: str) -> dict:
    return {
        "decision": "hold",
        "rationale": f"cover_eval bypassed: {reason}",
        "dimensions_weighed": [],
        "cost_usd": 0.0,
        "pm_decision_raw": {},
        "alt_data_verify_raw": {},
        "risk_raw": {},
    }


__all__ = [
    "run_cover_evaluation",
    "apply_cover_to_state",
    "compute_unrealized_profit_pct",
    "COVER_STRIPPED_KEYS",
    "R_COVER_08_PROFIT_THRESHOLD",
    "R_COVER_09_DAYS_THRESHOLD",
    "R_COVER_09_LOSS_BOUND",
]
