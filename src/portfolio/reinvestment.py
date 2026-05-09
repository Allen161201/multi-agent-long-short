"""
Profit Reinvestment — spec stub (Step C, frozen as v0.6).

This module is a SPEC STUB for the Friday 15:30 ET realized-P&L
reinvestment trigger. The runtime today does NOT implement this; a
future promotion will route through here.

Per RULES.md §4.9 / §4.10.table / §6.6 / §6.7:

  Trigger    : Friday 15:30 ET
  Source     : REALIZED short P&L (closed positions only). Quality-long
               unrealized gains are NEVER liquidated for reinvestment.
  Execution  : Monday 09:30 ET open. Friday packet is immutable per §5.7.

  Regime-conditional bands (% to quality-long / ETF, % to cash / FI):
    crisis        : 70-80% / 20-30%
    poor          : 60-70% / 30-40%
    normal        : 40-60% / 40-60%
    strengthening : 30-40% / 60-70%
    overheat      : 20-30% / 70-80%

  Selection criteria within bands (agent-discretionary):
    - current valuation vs historical
    - market trend
    - dividend yield
    - agent conviction

  Fixed income = U.S. Treasuries + investment-grade bonds + broker cash
                 sweep at short-term T-bill equivalent rate.

Wired into pnl_backtest harness on 2026-04-30 (§19.8 promotion).
midpoint_split_for_regime + ReinvestmentDecision are invoked by
src/engine/pnl_backtest.py on every Friday close where realised
surge-short P&L for the week is positive. Long-side ticker selection
is intentionally NOT performed by the harness (agent discretion per
RULES.md §4.10) — the trigger logs the decision into manifest.json
without converting any positions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Regime = Literal["crisis", "poor", "normal", "strengthening", "overheat"]


REINVESTMENT_BANDS: dict[Regime, dict[str, tuple[float, float]]] = {
    "crisis":        {"to_long_pct": (70.0, 80.0), "to_cash_pct": (20.0, 30.0)},
    "poor":          {"to_long_pct": (60.0, 70.0), "to_cash_pct": (30.0, 40.0)},
    "normal":        {"to_long_pct": (40.0, 60.0), "to_cash_pct": (40.0, 60.0)},
    "strengthening": {"to_long_pct": (30.0, 40.0), "to_cash_pct": (60.0, 70.0)},
    "overheat":      {"to_long_pct": (20.0, 30.0), "to_cash_pct": (70.0, 80.0)},
}


@dataclass(frozen=True)
class ReinvestmentDecision:
    decision_timestamp_iso: str       # Friday 15:30 ET timestamp
    execution_timestamp_iso: str      # Monday 09:30 ET timestamp
    regime: Regime
    realized_short_pnl_dollars: float
    to_long_pct: float                # midpoint within regime band by default
    to_cash_pct: float
    selection_rationale: str          # agent prose, not a rule output


def midpoint_split_for_regime(regime: Regime) -> tuple[float, float]:
    """Return the midpoint (to_long%, to_cash%) for the given regime.

    Agent has discretion within the band per §4.10. This helper returns
    the midpoint as a default starting point; callers may override.
    """
    band = REINVESTMENT_BANDS[regime]
    long_lo, long_hi = band["to_long_pct"]
    cash_lo, cash_hi = band["to_cash_pct"]
    return ((long_lo + long_hi) / 2.0, (cash_lo + cash_hi) / 2.0)


def is_in_band(*, regime: Regime, to_long_pct: float, to_cash_pct: float) -> bool:
    """Validate a candidate (long%, cash%) split against the regime band."""
    band = REINVESTMENT_BANDS[regime]
    long_lo, long_hi = band["to_long_pct"]
    cash_lo, cash_hi = band["to_cash_pct"]
    long_ok = long_lo <= to_long_pct <= long_hi
    cash_ok = cash_lo <= to_cash_pct <= cash_hi
    sums_to_100 = abs((to_long_pct + to_cash_pct) - 100.0) < 1e-6
    return long_ok and cash_ok and sums_to_100


__all__ = [
    "Regime",
    "REINVESTMENT_BANDS",
    "ReinvestmentDecision",
    "midpoint_split_for_regime",
    "is_in_band",
]
