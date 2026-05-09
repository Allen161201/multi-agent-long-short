"""
Trading calendar utility (NYSE).

Wraps pandas-market-calendars to provide PIT-safe trading-day logic
without hardcoding holiday dates. NYSE calendar covers regular
weekends + all NYSE holidays + early-close days, sourced from
official NYSE schedule.

Used by:
  - scripts/portfolio_5day_*.py daily loop (skip non-trading days)
  - rank_surge_candidates (prior_close := previous trading day's close)
  - load_quality_long_universe (Monday scan should fire on actual
    first-trading-day-of-week)

Reference: §29 in docs/RULES.md (additive, 2026-05-03).
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from functools import lru_cache
import pandas_market_calendars as mcal
import pandas as pd

NYSE = mcal.get_calendar("NYSE")

@lru_cache(maxsize=1)
def _valid_days_index(start: str, end: str) -> pd.DatetimeIndex:
    """Cached lookup. start/end are ISO date strings."""
    return NYSE.valid_days(start_date=start, end_date=end)

def is_trading_day(d) -> bool:
    """True iff d is an NYSE trading day (not weekend, not holiday)."""
    d = _as_date(d)
    iso = d.isoformat()
    idx = _valid_days_index(iso, iso)
    return len(idx) == 1

def previous_trading_day(d) -> date:
    """Most recent NYSE trading day STRICTLY BEFORE d.
    Walks back up to 14 calendar days (covers any holiday cluster)."""
    d = _as_date(d)
    start = (d - timedelta(days=14)).isoformat()
    end = (d - timedelta(days=1)).isoformat()
    idx = _valid_days_index(start, end)
    if len(idx) == 0:
        raise ValueError(f"No trading day found in 14 days before {d}")
    return idx[-1].date()

def next_trading_day(d) -> date:
    """Next NYSE trading day STRICTLY AFTER d."""
    d = _as_date(d)
    start = (d + timedelta(days=1)).isoformat()
    end = (d + timedelta(days=14)).isoformat()
    idx = _valid_days_index(start, end)
    if len(idx) == 0:
        raise ValueError(f"No trading day found in 14 days after {d}")
    return idx[0].date()

def trading_days_between(start, end) -> list:
    """All NYSE trading days in [start, end] inclusive."""
    s = _as_date(start).isoformat()
    e = _as_date(end).isoformat()
    idx = _valid_days_index(s, e)
    return [t.date() for t in idx]

def is_early_close(d) -> bool:
    """True iff d is an NYSE early-close day (e.g. day before holiday).
    Use this if execution timing matters; for trigger-day logic, regular
    is_trading_day is sufficient."""
    d = _as_date(d)
    sched = NYSE.schedule(start_date=d.isoformat(), end_date=d.isoformat())
    if len(sched) == 0:
        return False
    close_time = sched.iloc[0]["market_close"]
    # NYSE regular close is 16:00 ET; early close days close at 13:00 ET.
    # pandas-market-calendars returns tz-aware UTC Timestamp here;
    # convert to America/New_York before reading the hour, otherwise
    # `.hour` reads the UTC hour (21 regular / 18 early) and the
    # comparison `< 16` is always False.
    return close_time.tz_convert("America/New_York").hour < 16

def _as_date(d) -> date:
    if isinstance(d, str):
        return date.fromisoformat(d.split("T")[0])
    if isinstance(d, datetime):
        return d.date()
    return d
