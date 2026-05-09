"""
Cost Model — spec stub (Step C, frozen as v0.6).

This module is a SPEC STUB. The runtime today does NOT charge borrow or
transaction costs against decisions; backtest pulls these numbers from
`config/frozen_rules_v0.6_agentic_allocation_5regime.yaml::cost_model`.

Per RULES.md §5.16:

  - Borrow cost: 15% annualized, charged DAILY on open short positions
  - Transaction cost (commission + slippage) tiers, by position notional:
      ≤ $5,000          → 10 bps
      $5,001 – $20,000  → 20 bps
      > $20,000         → 30 bps
  - Tier applies to BOTH sleeves (surge-short and quality-long).

Wired into pnl_backtest harness on 2026-04-30 (§19.8 promotion).
borrow_cost_dollars is charged daily on every open short by
src/engine/pnl_backtest.py. transaction_cost_dollars (tiered) is the
fallback when the harness's `transaction_cost_bps` parameter is set
to None; otherwise the harness charges a flat per-leg bps.
"""
from __future__ import annotations

BORROW_COST_ANNUAL_PCT = 15.0
TRADING_DAYS_PER_YEAR = 252


def borrow_cost_per_day_pct() -> float:
    """Daily borrow cost as a fraction of the open short notional."""
    return BORROW_COST_ANNUAL_PCT / 100.0 / TRADING_DAYS_PER_YEAR


def borrow_cost_dollars(*, short_notional: float, days_held: int) -> float:
    """Total borrow cost over `days_held` for a short position of `short_notional`.

    Charged daily — flat-rate model. Real-world implementation should use
    daily mark-to-market and tier by hard-to-borrow availability.
    """
    return short_notional * borrow_cost_per_day_pct() * days_held


def transaction_cost_bps(position_value_usd: float) -> int:
    """Tiered bps schedule. Captures commission + slippage combined."""
    if position_value_usd <= 5_000.0:
        return 10
    if position_value_usd <= 20_000.0:
        return 20
    return 30


def transaction_cost_dollars(position_value_usd: float) -> float:
    """One-side transaction cost in dollars."""
    bps = transaction_cost_bps(position_value_usd)
    return position_value_usd * bps / 10_000.0


def round_trip_transaction_cost_dollars(position_value_usd: float) -> float:
    """Round-trip (buy + sell, or short + cover) transaction cost in dollars.

    Each leg charged at the tier corresponding to that leg's notional.
    For pyramid-add positions, callers should sum the per-leg costs at
    each leg's notional; this helper is for the simple round-trip case.
    """
    return 2.0 * transaction_cost_dollars(position_value_usd)


__all__ = [
    "BORROW_COST_ANNUAL_PCT",
    "TRADING_DAYS_PER_YEAR",
    "borrow_cost_per_day_pct",
    "borrow_cost_dollars",
    "transaction_cost_bps",
    "transaction_cost_dollars",
    "round_trip_transaction_cost_dollars",
]
