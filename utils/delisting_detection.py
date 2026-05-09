"""
PIT-correct delisting detection from price-tail behaviour.

Rationale
---------
The feasibility probe (`docs/DATA_FEASIBILITY_PROBE_RESULT.md` Capability 2)
showed that FMP `profile.isActivelyTrading` is unreliable for delisted
issuers — BBBY returns `isActivelyTrading=True` and `delistedDate=null`
despite being delisted in 2023. So we cannot use that flag as an
"active vs delisted" signal anywhere in the backtest.

This util replaces it with a behavioural signal: *what does the EOD
price tape look like up to (and only up to) the as-of date?* If the
last available trading day is far behind the as-of cutoff, the issuer
was delisted (or halted) by then.

Critical PIT property
---------------------
Every verdict is computed using ONLY data with `date <= as_of_date`.
This is what makes it safe inside `decision_mode = historical_replay`:
when the backtest replays 2021-06-15, BBBY must come back as `active`
(it was — bankruptcy was April 2023), not as `likely_delisted` based
on today's knowledge. The function exists precisely to prevent
hindsight contamination.

NOT CALLED in the universe-builder task. The universe artifact is just
the ticker-list scaffold; this util is the verdict that downstream
modules (e.g. surge-short universe filter at decision time) call once
they have a `decision_timestamp`.

Cache
-----
Historical price pulls go through a small JSON file cache rooted at
`data/cache/historical_prices/<SYMBOL>.json`. The cache is keyed by
symbol only (history is monotonic — old rows do not change), so a
single warm-up populates it for every subsequent decision date. Cache
files store the full FMP response trimmed to the columns we need.

The cache is intentionally local to this util (file-based) instead of
hooked into the FMP adapter's process-local TTL `_cache`, because:
  * the adapter's `get_historical_daily` does not interact with that
    cache today;
  * the adapter file is a production artifact this task is forbidden
    from modifying;
  * delisting verdicts are most useful when survivable across process
    restarts (they feed nightly batch jobs).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

# Make `src/` importable so we can reuse fmp_adapter._api_call
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from data_adapters import fmp_adapter as fmp  # noqa: E402

# ── Cache root ─────────────────────────────────────────────────────
CACHE_ROOT = ROOT / "data" / "cache" / "historical_prices"
CACHE_ROOT.mkdir(parents=True, exist_ok=True)


# ── Trading-day arithmetic (cheap; no external dep) ────────────────
def _is_weekday(d: datetime) -> bool:
    return d.weekday() < 5  # Mon=0 ... Fri=4


def _trading_days_back(d: datetime, n: int) -> datetime:
    """Step back `n` trading days from `d`. Treats weekends as non-trading.
    Holidays are ignored — accuracy is good enough for the threshold logic
    here (we already have ±a few days of slack in the heuristic)."""
    cur = d
    moved = 0
    while moved < n:
        cur = cur - timedelta(days=1)
        if _is_weekday(cur):
            moved += 1
    return cur


def _count_trading_days_between(early: datetime, late: datetime) -> int:
    """Number of weekdays in the half-open interval (early, late]."""
    if late <= early:
        return 0
    days = 0
    cur = early + timedelta(days=1)
    while cur <= late:
        if _is_weekday(cur):
            days += 1
        cur = cur + timedelta(days=1)
    return days


# ── Cache helpers ──────────────────────────────────────────────────
def _cache_path(symbol: str) -> Path:
    safe = "".join(c for c in symbol if c.isalnum() or c in ("-", "_"))
    return CACHE_ROOT / f"{safe}.json"


def _read_cache(symbol: str) -> list[dict] | None:
    p = _cache_path(symbol)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    rows = payload.get("rows")
    return rows if isinstance(rows, list) else None


def _write_cache(symbol: str, rows: list[dict]) -> None:
    p = _cache_path(symbol)
    payload = {
        "symbol": symbol,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "row_count": len(rows),
        "rows": rows,
    }
    p.write_text(json.dumps(payload, default=str))


def _fetch_history(symbol: str, start_date: str, end_date: str) -> list[dict]:
    """Pull `historical-price-eod/full` directly via fmp_adapter._api_call.
    We do not call `fmp_adapter.get_historical_daily` because that wrapper
    rewrites the row schema and drops fields we may want later."""
    data, meta = fmp._api_call(
        "historical-price-eod/full",
        {"symbol": symbol, "from": start_date, "to": end_date},
        group="delisting_detector",
    )
    if not meta.get("ok"):
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = data.get("historical")
        return rows if isinstance(rows, list) else []
    return []


def _get_history_through_cache(
    symbol: str,
    as_of_date: datetime,
    lookback_days: int,
) -> tuple[list[dict], str]:
    """Return rows whose `date` ≤ `as_of_date`, drawn from cache when warm.

    Returns (rows, source) where `source` ∈ {"cache", "live", "live_failed"}.
    """
    cached = _read_cache(symbol)
    if cached is not None:
        rows = [r for r in cached if r.get("date") and r["date"] <= as_of_date.strftime("%Y-%m-%d")]
        return rows, "cache"

    # Cold cache — pull a wide window so future decision dates can reuse it.
    end_for_pull = max(as_of_date, datetime.utcnow().replace(tzinfo=timezone.utc))
    # Wide pull so the cache is reusable for years of backtests
    start_for_pull = as_of_date - timedelta(days=max(365 * 8, lookback_days * 6))
    rows = _fetch_history(
        symbol,
        start_date=start_for_pull.strftime("%Y-%m-%d"),
        end_date=end_for_pull.strftime("%Y-%m-%d"),
    )
    if not rows:
        # Persist an empty cache entry so we don't re-hit FMP for known-empty
        # tickers in the same day.
        _write_cache(symbol, [])
        return [], "live_failed"

    _write_cache(symbol, rows)
    rows = [r for r in rows if r.get("date") and r["date"] <= as_of_date.strftime("%Y-%m-%d")]
    return rows, "live"


# ── Public API ─────────────────────────────────────────────────────
def detect_delisting_from_price_tail(
    symbol: str,
    as_of_date: str,
    lookback_days: int = 30,
) -> dict:
    """Decide whether `symbol` was effectively delisted as of `as_of_date`.

    Strict PIT contract: the verdict is computed using only price rows whose
    `date` is ≤ `as_of_date`. Today's knowledge of future delistings is not
    leaked back. This is what makes the function safe inside
    `decision_mode = historical_replay` (see
    `docs/DECISION_TIME_AND_LOOKAHEAD_POLICY.md`).

    Args:
        symbol: ticker.
        as_of_date: ISO date "YYYY-MM-DD" — judgment is made relative to
            this date, not to today.
        lookback_days: how far back, in calendar days, to inspect the
            price tail. The thresholds are expressed in trading days so
            this only bounds how much history is considered, not the
            verdict logic.

    Returns:
        {
          "symbol":                          str,
          "as_of_date":                      str,
          "verdict":                         "active" | "likely_delisted" | "unknown",
          "last_trade_date":                 str | None,
          "trading_days_since_last_trade":   int | None,
          "evidence":                        str,
          "rows_inspected":                  int,
          "history_source":                  "cache" | "live" | "live_failed",
        }

    Heuristic thresholds (open to revision; calibrated against the BBBY
    case where price-tape stops cleanly at the bankruptcy event):
      * 0 rows ≤ as_of_date              → "likely_delisted" (no PIT trades exist)
      * last_trade > as_of - 3 td        → "active"          (still trading recently)
      * 3 td < gap ≤ 14 td               → "unknown"         (halt vs. delist ambiguous)
      * gap > 14 td                      → "likely_delisted"
    """
    try:
        as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return {
            "symbol": symbol,
            "as_of_date": as_of_date,
            "verdict": "unknown",
            "last_trade_date": None,
            "trading_days_since_last_trade": None,
            "evidence": f"as_of_date '{as_of_date}' not in YYYY-MM-DD format",
            "rows_inspected": 0,
            "history_source": "live_failed",
        }

    rows, source = _get_history_through_cache(symbol, as_of_dt, lookback_days)

    if not rows:
        return {
            "symbol": symbol,
            "as_of_date": as_of_date,
            "verdict": "likely_delisted",
            "last_trade_date": None,
            "trading_days_since_last_trade": None,
            "evidence": (
                f"no historical-price rows ≤ {as_of_date} (history_source={source}). "
                "Empty tape ≤ as_of is treated as effectively delisted at PIT, "
                "with the caveat that it could also be a coverage gap rather "
                "than a true delisting."
            ),
            "rows_inspected": 0,
            "history_source": source,
        }

    # Find the row with the maximum date ≤ as_of_date.
    last_row = max(rows, key=lambda r: r.get("date", ""))
    last_date_str = last_row.get("date")
    try:
        last_dt = datetime.strptime(last_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return {
            "symbol": symbol,
            "as_of_date": as_of_date,
            "verdict": "unknown",
            "last_trade_date": last_date_str,
            "trading_days_since_last_trade": None,
            "evidence": f"could not parse last-row date '{last_date_str}'",
            "rows_inspected": len(rows),
            "history_source": source,
        }

    gap_td = _count_trading_days_between(last_dt, as_of_dt)

    if gap_td <= 3:
        verdict = "active"
        evidence = (
            f"last trade {last_date_str} is within 3 trading days of "
            f"as_of {as_of_date} (gap={gap_td}td). Tape is current → active."
        )
    elif gap_td <= 14:
        verdict = "unknown"
        evidence = (
            f"last trade {last_date_str} is {gap_td} trading days before "
            f"as_of {as_of_date}. Within 3-14td: indistinguishable from a "
            "trading halt or short suspension. Verdict deferred."
        )
    else:
        verdict = "likely_delisted"
        evidence = (
            f"last trade {last_date_str} is {gap_td} trading days before "
            f"as_of {as_of_date} (>14td). Tape stopped → likely delisted "
            "or permanently halted by PIT."
        )

    return {
        "symbol": symbol,
        "as_of_date": as_of_date,
        "verdict": verdict,
        "last_trade_date": last_date_str,
        "trading_days_since_last_trade": gap_td,
        "evidence": evidence,
        "rows_inspected": len(rows),
        "history_source": source,
    }


def detect_delisting_for_symbols(
    symbols: Iterable[str],
    as_of_date: str,
    lookback_days: int = 30,
) -> list[dict]:
    """Convenience batch wrapper. Caller is responsible for any rate-limit
    pacing beyond what fmp_adapter._MinuteRateLimiter already enforces."""
    return [
        detect_delisting_from_price_tail(sym, as_of_date, lookback_days)
        for sym in symbols
    ]
