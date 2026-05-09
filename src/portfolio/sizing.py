"""
Surge-Short Pyramid Sizing — spec stub (Step C, frozen as v0.6).

This module is a SPEC STUB. The dashboard implementation lives in
`src/rules/surge_short_rules.py` (already updated to Step C v0.6 in
the same commit); this stub captures the spec for promotion-path
clarity per §19.8.

Per RULES.md §4.8 / §2.3 (superseded):

  Initial entry      = 0.5% of portfolio at the trigger price
  Add rule           = +0.5% per +100% rise from ORIGINAL entry price
                       (NOT from prior add level — distinct from v0.4)
  Per-position cap   = 5% of portfolio (hard veto by Risk agent above)
  Sleeve cap         = 10% of portfolio
  Margin             = NEVER used

Logic check: a 1000% squeeze before reversion still leaves cumulative
position below 5%, which the portfolio absorbs without margin call.

Wired into pnl_backtest harness on 2026-04-30 (§19.8 promotion).
PER_POSITION_CAP_PCT and SLEEVE_CAP_PCT are consulted by
src/engine/pnl_backtest.py::_apply_sizing_caps before each open / add.
"""
from __future__ import annotations

INITIAL_PCT = 0.5
ADD_PCT = 0.5
PER_POSITION_CAP_PCT = 5.0
SLEEVE_CAP_PCT = 10.0


def add_trigger_price(*, original_entry_price: float, prior_adds: int) -> float:
    """Next add allowed when price ≥ original × (prior_adds + 2)."""
    return original_entry_price * (prior_adds + 2)


def position_after_n_adds(*, prior_adds: int) -> float:
    """Cumulative position size in % after `prior_adds` successful adds.

    Initial = 0.5%; each add = +0.5%. So after `n` adds, cumulative is
    INITIAL_PCT + n * ADD_PCT.
    """
    return INITIAL_PCT + prior_adds * ADD_PCT


def max_adds_before_position_cap() -> int:
    """How many adds can fit under the per-position 5% cap."""
    return int((PER_POSITION_CAP_PCT - INITIAL_PCT) / ADD_PCT)


__all__ = [
    "INITIAL_PCT",
    "ADD_PCT",
    "PER_POSITION_CAP_PCT",
    "SLEEVE_CAP_PCT",
    "add_trigger_price",
    "position_after_n_adds",
    "max_adds_before_position_cap",
]
