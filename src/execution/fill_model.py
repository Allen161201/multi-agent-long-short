"""
Fill Model — spec stub (Step C, frozen as v0.6).

This module is a SPEC STUB for the T+1 next-open fill model used in
backtests. The runtime today computes fills inline; a future promotion
will route through this module.

Per RULES.md §6.1 / §6.2:

  - Decision at `decision_timestamp` → trade at next-day market open
    (09:30 ET) of the next trading day after `decision_timestamp.date()`.
  - `execution_plan.order_type` MUST be exactly "T+1 next-open".
  - After-close news → trade next trading day (§6.3).

Slippage is captured by the tiered transaction-cost bps schedule in
`src/execution/cost_model.py` (RULES.md §5.16). This module does NOT
introduce its own slippage on top.

Wired into pnl_backtest harness on 2026-04-30 (§19.8 promotion).
FillIntent + next_trading_day_open are invoked by
src/engine/pnl_backtest.py to compute the execution_timestamp for
every non-no-op decision. The harness fetches the fill price via the
caller-injected price_history_provider at next_trading_day_open(D).date().
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal


@dataclass(frozen=True)
class FillIntent:
    decision_timestamp: datetime
    ticker: str
    side: Literal["long", "short", "cover", "sell"]
    order_type: str = "T+1 next-open"


def next_trading_day_open(decision_dt: datetime) -> datetime:
    """Return the 09:30 ET datetime of the next trading day after decision_dt.

    Spec-only — does not consult an exchange calendar. A real
    implementation must skip weekends and NYSE holidays.
    """
    candidate = decision_dt.date() + timedelta(days=1)
    # Naive weekend skip; real version uses pandas-market-calendars.
    while candidate.weekday() >= 5:
        candidate = candidate + timedelta(days=1)
    return datetime.combine(candidate, time(9, 30))


def fill_price_t1_next_open(
    *,
    intent: FillIntent,
    next_day_open_price: float,
) -> dict:
    """Spec-only fill price = next-day open. Slippage handled by cost_model."""
    return {
        "ticker": intent.ticker,
        "side": intent.side,
        "decision_timestamp": intent.decision_timestamp.isoformat(),
        "execution_timestamp": next_trading_day_open(intent.decision_timestamp).isoformat(),
        "fill_price": next_day_open_price,
        "order_type": intent.order_type,
        "fill_model": "T+1 next-open (spec v0.6)",
    }


__all__ = [
    "FillIntent",
    "next_trading_day_open",
    "fill_price_t1_next_open",
]
