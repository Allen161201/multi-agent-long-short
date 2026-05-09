"""Cost helpers — flat 15 bps tx cost + 100% APY daily borrow.

Per RULES.md §5.16 v2.15. Wired 2026-05-08 via PART 4 retrofit harness wiring.

These helpers are the SINGLE SOURCE OF TRUTH for runtime cost-model parameters.
The harness (`scripts/portfolio_5day_2026_04_27_to_05_01.py`) and the
src/portfolio/* modules (cover_eval / ql_review / fi_review) all import from
here so the bps and APY constants are consistent across every code path.

Cost model is a portfolio-side runtime parameter. It does NOT enter the
evidence packet and does NOT affect the canonical AAPL packet hash
(sha256:626266c7…). No rule_version bump required when this file is edited.
"""
from __future__ import annotations

# §5.16 v2.15 — flat single-side per-fill (= 30 bps round-trip per Menos AI bench)
TX_COST_BPS: float = 15.0

# §5.16 — uniform borrow rate, charged daily on open shorts
BORROW_APY: float = 1.00

# Calendar convention for daily borrow accrual
BORROW_DAYS_PER_YEAR: int = 365


def apply_tx_cost(notional_usd: float) -> float:
    """Return the tx cost (positive USD) for a single fill of the given notional.

    Per RULES.md §5.16 v2.15: flat 15 bps single-side. Applies uniformly to
    surge-short entries, pyramid adds, covers, quality-long entries / trims /
    exits, §31 force-buys, profit reinvest buys, UST purchases / early sales,
    BIL bootstrap, and forced corp-action conversion legs.

    Passive income events (dividends, interest accrual, UST HTM redemption,
    ETF expense ratio) MUST NOT call this helper — they incur no tx cost.
    """
    return abs(float(notional_usd)) * (TX_COST_BPS / 10_000.0)


def apply_daily_borrow(short_market_value_usd: float) -> float:
    """Return the daily borrow charge (positive USD) for a short position with
    the given EOD market value.

    Per RULES.md §5.16: 100% APY, charged once per trading day, entry day
    inclusive and cover day exclusive. The caller is responsible for the
    open-at-EOD test (status != 'closed' OR close_date > day).
    """
    return abs(float(short_market_value_usd)) * (BORROW_APY / BORROW_DAYS_PER_YEAR)


__all__ = [
    "TX_COST_BPS",
    "BORROW_APY",
    "BORROW_DAYS_PER_YEAR",
    "apply_tx_cost",
    "apply_daily_borrow",
]
