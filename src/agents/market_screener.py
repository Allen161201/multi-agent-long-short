"""
Agent 1: Market Screener Agent.

Pass 8 Step B1.6 (2026-05-04) hardening — closes the four lookahead vectors
identified in the B1.5 PIT verification report:

  V1. mode parameter is now MANDATORY at the public entry point.
      Replay-mode calls with decision_date=None raise ValueError immediately
      (fail-closed) — they no longer silently fall through to the live FMP
      biggest-gainers endpoint.

  V2. Replay mode self-computes surge from Polygon /v2/aggs/grouped/...
      OHLCV using the inline `_compute_historical_surge()` function below.
      Inputs are decision_date and previous_trading_day(decision_date) —
      no current-state field is touched.

  V3. Live mode (FMP biggest-gainers, current-day only) is segregated and
      asserted to never run in replay paths. Live mode is for actual
      real-time operation only.

  V4. The legacy `validate_market_data_adapter()` helper used to call
      get_top_gainers() with no date — replaced (in validation_report.py)
      with a pure connectivity ping that doesn't touch surge ranking.

Schema returned by the screener mirrors the existing contract consumed by
src.rules.surge_short_rules.filter_surge_candidates() so downstream rules
stay unchanged. change_pct is in PERCENTAGE units (60 = 60%, NOT 0.60).

Fetches daily top gainers, filters surge-short candidates, identifies
quality-long universe. Does NOT make final trade decisions.
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime
from typing import Optional

from src.data_adapters.market_data import (
    get_top_gainers as _market_data_get_top_gainers,
    polygon_grouped_daily,
)
from src.rules.surge_short_rules import filter_surge_candidates
from src.utils.trading_calendar import previous_trading_day

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Pass 8 §2.1 mechanical screen — Polygon-OHLCV-only thresholds.
# These are the FLOOR; downstream filter_surge_candidates() applies the
# fundamental gate (EPS_TTM<0 AND OM_TTM<0) and §2.15 technical-rebound
# exclusion using FMP fundamentals + 10-day lookback respectively.
# ─────────────────────────────────────────────────────────────────────
SURGE_PCT_FLOOR_DECIMAL = 0.60      # 60%
SURGE_VOLUME_FLOOR      = 1_000_000
SURGE_PRIOR_CLOSE_FLOOR = 2.00      # USD

# Anomaly threshold: surge > 500% on a single day strongly suggests an
# unadjusted corporate action even when polygon returned adjusted=true.
# We log + skip rather than emit; this is a defensive guardrail against
# bad upstream data.
SURGE_ANOMALY_THRESHOLD = 5.0       # 500%


def _coerce_iso_date(d) -> str:
    """Accept date | datetime | ISO string. Return YYYY-MM-DD."""
    if isinstance(d, str):
        return d.split("T")[0]
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()


def _compute_historical_surge(decision_date) -> list[dict]:
    """PIT-safe surge ranker for one historical decision_date.

    Reads Polygon grouped-daily OHLCV (always adjusted=True) for
    decision_date and the previous NYSE trading day. Computes
        surge_pct = (today.open - prior.close) / prior.close
    and applies the §2.1 mechanical floor (>=60% / >=1M vol / >$2 prior).

    No call to current-state endpoints; no `date is None` ever reached.

    Returns rows in the schema that filter_surge_candidates() expects:
        {ticker, name, close, prior_close, change_pct (percentage units),
         volume, market_cap, exchange, security_type, ...}

    `change_pct` here is in PERCENTAGE units (60 = 60%) to match the
    existing live-mode adapter contract that filter_surge_candidates()
    already accepts.
    """
    dd_iso = _coerce_iso_date(decision_date)
    prior_iso = previous_trading_day(decision_date).isoformat()

    today_blob = polygon_grouped_daily(dd_iso, adjusted=True)
    prior_blob = polygon_grouped_daily(prior_iso, adjusted=True)

    if today_blob.get("source") == "polygon_failed":
        logger.error(
            "_compute_historical_surge: today fetch failed for %s: %s",
            dd_iso, today_blob.get("error_class")
        )
        return []
    if prior_blob.get("source") == "polygon_failed":
        logger.error(
            "_compute_historical_surge: prior fetch failed for %s: %s",
            prior_iso, prior_blob.get("error_class")
        )
        return []

    today_results: dict[str, dict] = today_blob.get("results", {})
    prior_results: dict[str, dict] = prior_blob.get("results", {})

    candidates: list[dict] = []
    anomalies: list[tuple[str, float]] = []
    for ticker, today in today_results.items():
        prior = prior_results.get(ticker)
        if not prior:
            continue

        prior_close = prior.get("c") or 0
        today_open  = today.get("o") or 0
        today_close = today.get("c") or 0
        today_volume = today.get("v") or 0

        if prior_close <= 0 or today_open <= 0:
            continue

        surge_decimal = (today_open - prior_close) / prior_close

        if surge_decimal < SURGE_PCT_FLOOR_DECIMAL:
            continue
        if today_volume < SURGE_VOLUME_FLOOR:
            continue
        if prior_close <= SURGE_PRIOR_CLOSE_FLOOR:
            continue

        if surge_decimal > SURGE_ANOMALY_THRESHOLD:
            anomalies.append((ticker, surge_decimal))
            logger.warning(
                "_compute_historical_surge: %s surge %.2f%% on %s exceeds "
                "anomaly threshold (%.0f%%); possible unadjusted corp "
                "action despite adjusted=true. Skipping.",
                ticker, surge_decimal * 100, dd_iso,
                SURGE_ANOMALY_THRESHOLD * 100,
            )
            continue

        candidates.append({
            "ticker": ticker,
            "name": ticker,  # no name in grouped-daily; downstream may enrich
            "close": today_close,
            "prior_close": prior_close,
            "change": today_open - prior_close,
            "change_pct": surge_decimal * 100.0,  # PERCENTAGE units
            "volume": today_volume,
            "market_cap": 0,
            "exchange": "",
            "security_type": "",
            "source": "polygon_grouped_self_computed",
            "live_only": False,
            "pit_safe": True,
            "decision_date": dd_iso,
            "prior_trading_day": prior_iso,
            "available_as_of": f"{dd_iso}T16:00:00-05:00",
            "today_open": today_open,
            "today_volume": today_volume,
        })

    # Sort by surge_pct descending — highest surge first.
    candidates.sort(key=lambda c: c["change_pct"], reverse=True)

    logger.info(
        "_compute_historical_surge %s vs %s: %d candidates pass §2.1 "
        "OHLCV-only screen (anomalies skipped: %d)",
        dd_iso, prior_iso, len(candidates), len(anomalies),
    )
    return candidates


def get_top_gainers(
    decision_date,
    mode: str = "replay",
    limit: Optional[int] = None,
) -> list[dict]:
    """Public surge-ranker entry point.

    Args:
      decision_date: date | datetime | ISO string. REQUIRED in replay mode.
        Pass None ONLY when mode='live' (current-day FMP biggest-gainers).
      mode: 'replay' (default — PIT-safe historical surge from Polygon
        grouped-daily) or 'live' (current FMP biggest-gainers; raises if
        decision_date is non-None — FMP endpoint has no date param).
      limit: optional max number of candidates returned. None = no limit.

    Raises:
      ValueError: if mode='replay' and decision_date is None.
        Message: "PIT replay mode requires explicit decision_date; date=None
        would inject lookahead. See §9.2 + §32.2."
      ValueError: if mode='live' and decision_date is non-None.
      ValueError: if mode is anything other than 'replay'/'live'.

    Returns: list of candidate dicts compatible with
      src.rules.surge_short_rules.filter_surge_candidates().
    """
    if mode == "replay":
        if decision_date is None:
            raise ValueError(
                "PIT replay mode requires explicit decision_date; "
                "date=None would inject lookahead. See §9.2 + §32.2."
            )
        rows = _compute_historical_surge(decision_date)

    elif mode == "live":
        if decision_date is not None:
            raise ValueError(
                "Live-mode get_top_gainers does not accept decision_date "
                "(FMP biggest-gainers has no date parameter and returns "
                "current-day only). Pass decision_date=None for live, or "
                "use mode='replay' for any historical date."
            )
        # Live mode is for actual real-time operation only. The
        # market_data adapter is responsible for the FMP call; we
        # explicitly pass mode='live' so the adapter does not raise.
        rows = _market_data_get_top_gainers(date=None, mode="live")

    else:
        raise ValueError(
            f"market_screener.get_top_gainers: mode='{mode}' is invalid. "
            "Expected 'replay' or 'live'."
        )

    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return rows


def run(
    date,
    quality_universe: list[str] | None = None,
    mode: str = "replay",
) -> dict:
    """
    Run the Market Screener Agent for one decision date.

    Pass 8 Step B1.6 (2026-05-04): `mode` parameter added.
      - mode='replay' (default): historical surge via Polygon grouped-daily.
        `date` must be a real ISO string / date / datetime. The function
        will fail-closed if date is None.
      - mode='live': current-day FMP biggest-gainers; date should be None.

    Returns:
      dict with surge_short_candidates and quality_long_tickers.
    """
    if quality_universe is None:
        quality_universe = ["AAPL", "MSFT", "AMZN", "NVDA", "GOOG", "META", "UBER", "NFLX"]

    if mode == "replay":
        gainers = get_top_gainers(decision_date=date, mode="replay")
        # Pass 8 Step B1.7 (2026-05-04): thread decision_date so the §2.1
        # EPS<0 + OM<0 gate fetches PIT-safe fundamentals
        # (acceptedDate <= decision_date) instead of evaluating with
        # whatever happened to be on the gainer dict.
        surge_candidates = filter_surge_candidates(gainers, decision_date=date)
    elif mode == "live":
        gainers = get_top_gainers(decision_date=None, mode="live")
        # Live mode runs the gate against pre-attached current TTM values
        # if the live adapter populates them (legacy behavior). decision_date
        # is None so no PIT fetch is triggered.
        surge_candidates = filter_surge_candidates(gainers, decision_date=None)
    else:
        raise ValueError(
            f"market_screener.run: mode='{mode}' invalid. Use 'replay' or 'live'."
        )

    return {
        "agent": "market_screener",
        "date": date if isinstance(date, str) else _coerce_iso_date(date) if date is not None else None,
        "mode": mode,
        "total_gainers": len(gainers),
        "surge_short_candidates": [
            {
                "ticker": c["ticker"],
                "name": c.get("name", c["ticker"]),
                "close": c.get("close", 0),
                "prior_close": c.get("prior_close", 0),
                "change_pct": c.get("change_pct", 0),
                "volume": c.get("volume", 0),
                "market_cap": c.get("market_cap", 0),
                "sector": c.get("sector", "Unknown"),
            }
            for c in surge_candidates
        ],
        "quality_long_tickers": quality_universe,
        "all_gainers": [
            {
                "ticker": g["ticker"],
                "name": g.get("name", g["ticker"]),
                "change_pct": g.get("change_pct", 0),
                "volume": g.get("volume", 0),
                "prior_close": g.get("prior_close", 0),
            }
            for g in gainers
        ],
    }
