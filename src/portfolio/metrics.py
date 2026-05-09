"""
Financial-ratios compute module (D4 step 1 cont. — analytics layer).

Pure-numpy / stdlib functions. No I/O inside the low-level layer; the
two convenience wrappers at the bottom read pnl_history.csv via
nav_history.load_nav_history.

Six metrics, all annualization-aware (default periods_per_year=252,
US trading-day count):

    sharpe_ratio          ann. (mean_excess) / ann. std
    sortino_ratio         ann. (mean_excess) / ann. downside std
    max_drawdown          {value, peak_date, trough_date, recovery_date}
    return_volatility     ann. std of daily returns
    total_return          (nav[-1] − nav[0]) / nav[0]
    information_ratio     ann. (mean_active) / ann. tracking error

Conventions:
  - Sample standard deviation (numpy `ddof=1`) is used everywhere a
    standard deviation appears. Population stdev would understate
    volatility for short series.
  - "Returns" here means *period* (per-day) decimal returns, NOT log-
    returns. NAV[t] / NAV[t-1] - 1.
  - "Excess return" = period_return − period_risk_free_rate. The default
    risk_free_rate=0.0 is fine for v1; in production wire this to a per-
    period FRED 3M T-bill yield (deferred).
  - Annualization assumes daily frequency. For non-daily inputs, set
    periods_per_year accordingly (12 = monthly, 4 = quarterly, etc.).

Edge-case contract (consistent across all six functions):
  - Empty input            → return None (no math defined)
  - Single point           → return None for std-dependent metrics;
                              total_return returns 0.0;
                              max_drawdown returns
                              {value: 0.0, peak_date: <the one date>,
                               trough_date: same, recovery_date: None}
  - All-zero returns       → numerator 0, denominator 0 → return 0.0
                              (NOT NaN, NOT raise)
  - Sortino, all-positive  → downside std = 0 → return float('inf')
                              with a comment in the function body
                              explaining why (mean is positive, so the
                              risk-adjusted ratio is unbounded)
  - Information ratio,
    zero tracking error    → return float('inf') if active return > 0,
                              0.0 if active return == 0,
                              float('-inf') if active return < 0
  - Mismatched lengths
    (info ratio)           → raise ValueError with diagnostic
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

import numpy as np

from .nav_history import load_nav_history

DEFAULT_PERIODS_PER_YEAR = 252


# ── helpers ───────────────────────────────────────────────────────

def _to_array(x: Sequence[float]) -> np.ndarray:
    """Coerce input to a 1-D float64 numpy array. Raises ValueError if
    any element is non-finite (NaN/inf) so we surface bad inputs at the
    boundary instead of silently propagating NaN."""
    a = np.asarray(list(x), dtype=np.float64).ravel()
    if a.size and not np.all(np.isfinite(a)):
        raise ValueError(
            f"non-finite value in input array: {a!r} "
            f"(NaN/inf must be filtered upstream)"
        )
    return a


def _drop_first_none(rows: list[dict], field: str) -> list[float]:
    """Pull `field` from each row, skipping the first row (where
    daily_return is None by nav_history's contract). Used by the
    convenience wrappers."""
    out: list[float] = []
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        out.append(float(v))
    return out


# ── 1. Sharpe ─────────────────────────────────────────────────────

def sharpe_ratio(
    returns: Sequence[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> float | None:
    """Annualized Sharpe ratio.

        Sharpe = (mean(excess) × P) / (std(returns) × sqrt(P))

    where excess = returns − risk_free_rate (per-period rate, not
    annualized). Returns None if fewer than 2 observations (std
    undefined). Returns 0.0 when both numerator and denominator are 0.
    """
    r = _to_array(returns)
    if r.size < 2:
        return None
    excess = r - float(risk_free_rate)
    std = float(np.std(r, ddof=1))
    mean_excess = float(np.mean(excess))
    if std == 0.0:
        return 0.0 if mean_excess == 0.0 else (
            math.inf if mean_excess > 0 else -math.inf
        )
    return (mean_excess * periods_per_year) / (std * math.sqrt(periods_per_year))


# ── 2. Sortino ────────────────────────────────────────────────────

def sortino_ratio(
    returns: Sequence[float],
    target_return: float = 0.0,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> float | None:
    """Annualized Sortino ratio.

        Sortino = (mean(returns − target) × P) / (downside_std × sqrt(P))

    where `downside_std` is the sample stdev of (target − r) for r <
    target (the "downside semi-deviation"). Per-period target.

    All-positive returns ⇒ downside_std = 0 ⇒ +inf (mean is positive).
    Constant returns at target ⇒ both 0 ⇒ 0.0.
    """
    r = _to_array(returns)
    if r.size < 2:
        return None
    excess = r - float(target_return)
    mean_excess = float(np.mean(excess))
    downside = excess[excess < 0]
    if downside.size == 0:
        # No periods below target → downside risk is 0 by definition.
        # If mean excess is also 0 → ratio is 0 (not 0/0). Otherwise
        # the risk-adjusted ratio is unbounded in the favorable direction.
        if mean_excess == 0.0:
            return 0.0
        return math.inf if mean_excess > 0 else -math.inf
    # Downside std uses the same observations *only*, sample stdev.
    if downside.size < 2:
        # Single downside observation: sample stdev undefined.
        # Fall back to the magnitude of that single shortfall as a
        # conservative downside proxy. Document by example in tests.
        downside_std = float(abs(downside[0]))
    else:
        downside_std = float(np.std(downside, ddof=1))
    if downside_std == 0.0:
        # Defensive — single-element downside with value 0 reaches here.
        return 0.0 if mean_excess == 0.0 else (
            math.inf if mean_excess > 0 else -math.inf
        )
    return (mean_excess * periods_per_year) / (
        downside_std * math.sqrt(periods_per_year)
    )


# ── 3. Max drawdown ───────────────────────────────────────────────

def max_drawdown(
    nav_series: Sequence[float],
    dates: Sequence[str] | None = None,
) -> dict:
    """Maximum peak-to-trough decline in NAV.

    Returns a dict:
        {value: float (≤ 0; -0.25 means -25%),
         peak_date: str | None,
         trough_date: str | None,
         recovery_date: str | None}

    `dates` is parallel to `nav_series` and must have the same length
    if provided; if omitted, indices are used as date strings.

    Monotonically increasing NAV ⇒ value = 0.0, peak_date = trough_date
    = first date, recovery_date = None. Empty NAV ⇒ value = None and
    all dates None. Single point ⇒ value = 0.0, peak/trough = that
    date, recovery_date = None.
    """
    nav = _to_array(nav_series)
    if dates is not None:
        if len(dates) != nav.size:
            raise ValueError(
                f"dates length {len(dates)} != nav length {nav.size}"
            )
        date_list: list[str] = [str(d) for d in dates]
    else:
        date_list = [str(i) for i in range(nav.size)]

    if nav.size == 0:
        return {"value": None, "peak_date": None, "trough_date": None,
                "recovery_date": None}
    if nav.size == 1:
        return {"value": 0.0, "peak_date": date_list[0],
                "trough_date": date_list[0], "recovery_date": None}

    # Running peak (cum-max), then drawdown from the peak.
    running_peak = np.maximum.accumulate(nav)
    drawdown = (nav - running_peak) / running_peak
    trough_idx = int(np.argmin(drawdown))
    dd_value = float(drawdown[trough_idx])

    # Peak is the most recent index ≤ trough_idx whose value equals
    # running_peak[trough_idx] (the peak that produced the worst dd).
    peak_value = running_peak[trough_idx]
    # Search backwards from trough_idx for the first match.
    peak_idx = trough_idx
    for i in range(trough_idx, -1, -1):
        if nav[i] >= peak_value:
            peak_idx = i
            break

    if dd_value == 0.0:
        # Monotonically non-decreasing (or single-day all-equal): no
        # drawdown event. Peak = trough = first date.
        return {"value": 0.0,
                "peak_date": date_list[0],
                "trough_date": date_list[0],
                "recovery_date": None}

    # Recovery: first index AFTER trough where NAV regains peak_value.
    recovery_idx: int | None = None
    for i in range(trough_idx + 1, nav.size):
        if nav[i] >= peak_value:
            recovery_idx = i
            break

    return {
        "value": dd_value,
        "peak_date": date_list[peak_idx],
        "trough_date": date_list[trough_idx],
        "recovery_date": (
            date_list[recovery_idx] if recovery_idx is not None else None
        ),
    }


# ── 4. Return volatility ──────────────────────────────────────────

def return_volatility(
    returns: Sequence[float],
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> float | None:
    """Annualized standard deviation of period returns:

        vol = std(returns) × sqrt(periods_per_year)

    Sample stdev (ddof=1). Returns None for fewer than 2 observations,
    0.0 for constant returns.
    """
    r = _to_array(returns)
    if r.size < 2:
        return None
    return float(np.std(r, ddof=1)) * math.sqrt(periods_per_year)


# ── 5. Total return ───────────────────────────────────────────────

def total_return(nav_series: Sequence[float]) -> float | None:
    """Cumulative return:  (nav[-1] − nav[0]) / nav[0].

    Returns None on empty input, 0.0 on single-point input. Raises if
    nav[0] is zero (undefined ratio)."""
    nav = _to_array(nav_series)
    if nav.size == 0:
        return None
    if nav.size == 1:
        return 0.0
    if nav[0] == 0.0:
        raise ValueError(
            f"total_return undefined: nav[0] is zero (got {nav.tolist()[:3]}…)"
        )
    return float((nav[-1] - nav[0]) / nav[0])


# ── 6. Information ratio ──────────────────────────────────────────

def information_ratio(
    portfolio_returns: Sequence[float],
    benchmark_returns: Sequence[float],
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> float | None:
    """Annualized information ratio:

        IR = (mean(active) × P) / (std(active) × sqrt(P))

    where active = portfolio − benchmark (per-period). Sample stdev.

    Length mismatch raises ValueError. < 2 observations → None.
    Zero tracking error: +inf if mean_active > 0, 0.0 if == 0, -inf if < 0.
    """
    p = _to_array(portfolio_returns)
    b = _to_array(benchmark_returns)
    if p.size != b.size:
        raise ValueError(
            f"information_ratio: portfolio length {p.size} != "
            f"benchmark length {b.size}"
        )
    if p.size < 2:
        return None
    active = p - b
    mean_active = float(np.mean(active))
    te = float(np.std(active, ddof=1))
    if te == 0.0:
        if mean_active == 0.0:
            return 0.0
        return math.inf if mean_active > 0 else -math.inf
    return (mean_active * periods_per_year) / (te * math.sqrt(periods_per_year))


# ── high-level convenience wrappers ───────────────────────────────

def compute_sharpe_from_history(
    history_csv_path: Path,
    risk_free_rate: float = 0.0,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> float | None:
    rows = load_nav_history(history_csv_path)
    returns = _drop_first_none(rows, "daily_return")
    return sharpe_ratio(returns, risk_free_rate=risk_free_rate,
                        periods_per_year=periods_per_year)


def compute_all_metrics(
    history_csv_path: Path,
    benchmark_returns: Sequence[float] | None = None,
    risk_free_rate: float = 0.0,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> dict:
    """Compute every metric this module knows about from a pnl_history.csv.

    Returns a dict with these keys (all six functions). When the history
    is too short for a metric, the value is None.

    `benchmark_returns` must be aligned to the *daily_return* series
    (i.e. the post-first-row series, since nav_history puts None on the
    first row). If None, `information_ratio` is omitted from the output.
    """
    rows = load_nav_history(history_csv_path)
    nav = [float(r["total_nav"]) for r in rows]
    dates = [str(r["as_of"]) for r in rows]
    returns = _drop_first_none(rows, "daily_return")

    out: dict = {
        "as_of_count": len(rows),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "sharpe_ratio": sharpe_ratio(
            returns, risk_free_rate=risk_free_rate,
            periods_per_year=periods_per_year),
        "sortino_ratio": sortino_ratio(
            returns, periods_per_year=periods_per_year),
        "max_drawdown": max_drawdown(nav, dates=dates),
        "return_volatility": return_volatility(
            returns, periods_per_year=periods_per_year),
        "total_return": total_return(nav),
    }
    if benchmark_returns is not None:
        out["information_ratio"] = information_ratio(
            returns, benchmark_returns, periods_per_year=periods_per_year)
    return out


__all__ = [
    "DEFAULT_PERIODS_PER_YEAR",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "return_volatility",
    "total_return",
    "information_ratio",
    "compute_sharpe_from_history",
    "compute_all_metrics",
]
