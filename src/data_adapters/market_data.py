"""
Market Data Adapter — unified interface for price/gainer/fundamental data.

Routing:
  - If FMP_API_KEY is set and DATA_MODE != "mock" / USE_MOCK_DATA != "true"
    → use FMP /stable adapter
  - Otherwise → fall back to mock JSON

Source flags returned to callers:
  - "live_fmp"      : real FMP /stable response
  - "mock_fallback" : live attempted and failed (or live disabled), mock loaded
  - "cache"         : reserved (FRED only today)

A failed live attempt NEVER returns a payload that looks live — it either
returns a mock-tagged payload or an explicit DATA_UNAVAILABLE dict.

Pass 8 Step B1.6 (2026-05-04): added polygon_grouped_daily for PIT-safe
historical OHLCV bulk fetch. The grouped-daily endpoint returns full-market
OHLCV for any historical date in one call. Cache key includes adjusted
flag so adjusted/unadjusted data never cross-contaminate. The historical
surge ranker in src/agents/market_screener.py calls this; live-mode
get_top_gainers (FMP biggest-gainers, current-only) is segregated and
fail-closed for replay mode.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from src.data_adapters.mock_loader import load_top_gainers, load_fundamentals

logger = logging.getLogger(__name__)

DATA_UNAVAILABLE = "Data unavailable"

SOURCE_LIVE = "live_fmp"
SOURCE_MOCK_FALLBACK = "mock_fallback"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _use_live_fmp() -> bool:
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    data_mode = os.environ.get("DATA_MODE", "mock").strip().lower()
    use_mock = os.environ.get("USE_MOCK_DATA", "true").strip().lower()
    if data_mode == "mock" or use_mock == "true":
        return False
    return bool(api_key)


def _tag_mock(d: dict | list, default_date: str | None = None) -> dict | list:
    """Stamp mock-fallback source on a dict or list-of-dicts."""
    iso = _now_iso()
    ts_close = (f"{default_date}T16:00:00-05:00" if default_date else iso)
    ts_avail = (f"{default_date}T16:15:00-05:00" if default_date else iso)
    if isinstance(d, dict):
        d.setdefault("source", SOURCE_MOCK_FALLBACK)
        d["source"] = SOURCE_MOCK_FALLBACK
        d.setdefault("filing_date", DATA_UNAVAILABLE)
        d.setdefault("available_as_of", ts_avail)
        return d
    if isinstance(d, list):
        for item in d:
            if isinstance(item, dict):
                item["source"] = SOURCE_MOCK_FALLBACK
                item.setdefault("timestamp", ts_close)
                item.setdefault("available_as_of", ts_avail)
        return d
    return d


# ═══════════════════════════════════════════════════════════════
# TOP GAINERS
# ═══════════════════════════════════════════════════════════════

def get_top_gainers(date: str | None = None,
                    mode: str = "live") -> list[dict]:
    """
    LIVE-MODE-ONLY adapter wrapper for FMP /stable/biggest-gainers.

    Pass 8 §32.2 / Step B1.6 (2026-05-04): this function is fail-closed
    for replay mode. Backtest / PIT paths must call
    `src.agents.market_screener.get_top_gainers(decision_date, mode='replay')`
    instead — that routes through polygon_grouped_daily and self-computes
    surge from historical OHLCV (lookahead-free). The FMP biggest-gainers
    endpoint returns CURRENT-day-only data with no `date` parameter, so
    using it in replay would inject lookahead.

    Modes:
      - mode='live': returns FMP biggest-gainers (snapshot of today). The
        `date` parameter must be None — it is ignored by FMP and a non-None
        value would falsely imply historical-as-of semantics.
      - mode='replay' (or anything else): raises ValueError. The replay
        surge ranker is implemented in src/agents/market_screener.py.

    Mock-mode behavior is preserved: when DATA_MODE=mock or USE_MOCK_DATA=true,
    we route through the mock JSON loader regardless of `mode`. Mock loader
    needs `date` to find a sample file; in live-mode use, `date=None`.
    """
    if mode != "live":
        raise ValueError(
            f"market_data.get_top_gainers: mode='{mode}' is not supported by "
            "this adapter. FMP biggest-gainers is current-only and would "
            "inject lookahead in replay/backtest. Call "
            "src.agents.market_screener.get_top_gainers(decision_date, "
            "mode='replay') instead. (Pass 8 §32.2 / Step B1.6)"
        )

    if _use_live_fmp() and date is None:
        try:
            from src.data_adapters.fmp_adapter import get_top_gainers_live
            gainers = get_top_gainers_live(max_count=5)
            if gainers:
                logger.info("FMP: %d top gainers (live)", len(gainers))
                return gainers
            logger.warning("FMP top gainers: empty result, falling back to mock")
        except Exception as e:
            logger.warning("FMP top gainers exception (%s); falling back to mock",
                           type(e).__name__)

    gainers = load_top_gainers(date) or []
    return _tag_mock(gainers, default_date=date)


# ═══════════════════════════════════════════════════════════════
# POLYGON GROUPED-DAILY — PIT-safe bulk OHLCV (Pass 8 Step B1.6)
# ═══════════════════════════════════════════════════════════════
#
# Polygon's /v2/aggs/grouped/locale/us/market/stocks/{date} returns one
# OHLCV row for EVERY US-listed instrument on a single historical date.
# Verified in Step B1.5: ~10,972 instruments per call, ~450ms latency,
# survivorship-safe (delisted tickers still appear on dates they traded).
#
# adjusted=true tells Polygon to apply splits / spinoffs / etc. We DEFAULT
# to adjusted=true and require callers to be explicit if they want raw
# unadjusted bars (which would contaminate surge_pct with split jumps).
#
# Disk cache layout: data/cache/polygon/grouped_daily/<YYYY-MM-DD>_adj{0|1}.json
# The adjusted flag is in the path so adjusted and unadjusted requests
# never overwrite each other. Files are immutable (historical OHLCV does
# not change), so no TTL is needed.

_POLYGON_GROUPED_CACHE_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "cache" / "polygon" / "grouped_daily"
)


def _polygon_grouped_cache_path(date_str: str, adjusted: bool) -> Path:
    """File path for one (date, adjusted) cache entry."""
    flag = 1 if adjusted else 0
    return _POLYGON_GROUPED_CACHE_ROOT / f"{date_str}_adj{flag}.json"


def polygon_grouped_daily(date: str, adjusted: bool = True) -> dict:
    """
    Return Polygon grouped-daily OHLCV for one US trading date.

    Args:
      date: ISO date string (YYYY-MM-DD).
      adjusted: if True (default), Polygon applies splits/dividends/spinoffs.
        Pass adjusted=False ONLY when you need raw unadjusted bars and
        understand that surge_pct against unadjusted prior_close will spike
        on split days. The cache key includes this flag so the two never
        cross-contaminate.

    Returns:
      {
        "date": "YYYY-MM-DD",
        "adjusted": bool,
        "results": { ticker: {"o","h","l","c","v","vw","n","t"} },  # dict by ticker
        "result_count": int,
        "served_from_cache": bool,
        "source": "polygon_live" | "polygon_cache" | "polygon_failed",
        "error_class": str | None,
        "http_status": int | None,
      }

    PIT-safety:
      Bulk historical endpoint — the response for date X is identical
      regardless of when the call is made (post-trading-day). Caller
      must pass a date <= execution-day for PIT compliance.

    Caching:
      data/cache/polygon/grouped_daily/<date>_adj{0|1}.json. Hits bypass
      the network entirely. Misses fetch + write atomically.
    """
    if not adjusted:
        logger.warning(
            "polygon_grouped_daily: adjusted=False requested for %s. "
            "Surge_pct computed against unadjusted prior_close will spike "
            "on split days. Cache key isolates this from adjusted=True data.",
            date,
        )

    cache_path = _polygon_grouped_cache_path(date, adjusted)
    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as f:
                blob = json.load(f)
            blob["served_from_cache"] = True
            blob["source"] = "polygon_cache"
            return blob
        except (OSError, json.JSONDecodeError):
            logger.warning("polygon_grouped_daily: cache read failed for %s "
                           "adj=%s; refetching", date, adjusted)

    api_key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not api_key:
        return {
            "date": date, "adjusted": adjusted,
            "results": {}, "result_count": 0,
            "served_from_cache": False, "source": "polygon_failed",
            "error_class": "MissingApiKey",
            "http_status": None,
        }

    adj_q = "true" if adjusted else "false"
    url = (
        f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}"
        f"?adjusted={adj_q}&apiKey={api_key}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "altdata-pit"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            data = json.loads(body)
    except urllib.error.HTTPError as e:
        return {
            "date": date, "adjusted": adjusted,
            "results": {}, "result_count": 0,
            "served_from_cache": False, "source": "polygon_failed",
            "error_class": f"HTTP{e.code}",
            "http_status": e.code,
        }
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {
            "date": date, "adjusted": adjusted,
            "results": {}, "result_count": 0,
            "served_from_cache": False, "source": "polygon_failed",
            "error_class": type(e).__name__,
            "http_status": None,
        }

    rows = data.get("results", []) or []
    by_ticker: dict[str, dict] = {}
    for r in rows:
        sym = r.get("T")
        if not sym:
            continue
        by_ticker[sym] = {
            "o": r.get("o"), "h": r.get("h"),
            "l": r.get("l"), "c": r.get("c"),
            "v": r.get("v"), "vw": r.get("vw"),
            "n": r.get("n"), "t": r.get("t"),
        }

    blob = {
        "date": date,
        "adjusted": adjusted,
        "results": by_ticker,
        "result_count": len(by_ticker),
        "served_from_cache": False,
        "source": "polygon_live",
        "error_class": None,
        "http_status": 200,
    }

    # Atomic write so partial files don't corrupt the cache.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json",
                                dir=str(cache_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(blob, f)
        os.replace(tmp, cache_path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise

    return blob


# ═══════════════════════════════════════════════════════════════
# FUNDAMENTALS
# ═══════════════════════════════════════════════════════════════

def get_fundamentals(ticker: str) -> dict:
    if _use_live_fmp():
        try:
            from src.data_adapters.fmp_adapter import get_fundamentals_for_scoring
            fund = get_fundamentals_for_scoring(ticker)
            if fund:
                logger.info("FMP: fundamentals %s (live)", ticker)
                return fund
            logger.warning("FMP fundamentals %s: empty; mock fallback", ticker)
        except Exception as e:
            logger.warning("FMP fundamentals %s exception (%s); mock fallback",
                           ticker, type(e).__name__)

    data = load_fundamentals(ticker) or {}
    return _tag_mock(dict(data)) if data else {
        "ticker": ticker,
        "status": DATA_UNAVAILABLE,
        "source": SOURCE_MOCK_FALLBACK,
        "filing_date": DATA_UNAVAILABLE,
        "available_as_of": DATA_UNAVAILABLE,
    }


# ═══════════════════════════════════════════════════════════════
# HISTORICAL OHLCV
# ═══════════════════════════════════════════════════════════════

def get_historical_ohlcv(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 30,
) -> list[dict]:
    """Live mode only. Returns [] in mock mode."""
    if _use_live_fmp():
        try:
            from src.data_adapters.fmp_adapter import get_historical_daily
            data = get_historical_daily(ticker, start_date, end_date, days=days)
            if data:
                logger.info("FMP: %d OHLCV rows %s (live)", len(data), ticker)
                return data
            logger.warning("FMP OHLCV %s: empty result", ticker)
        except Exception as e:
            logger.warning("FMP OHLCV %s exception (%s)", ticker, type(e).__name__)
    return []


# ═══════════════════════════════════════════════════════════════
# COMPANY PROFILE
# ═══════════════════════════════════════════════════════════════

def get_company_profile(ticker: str) -> dict:
    if _use_live_fmp():
        try:
            from src.data_adapters.fmp_adapter import (
                get_company_profile as fmp_profile,
            )
            profile = fmp_profile(ticker)
            if profile.get("status") == "available":
                return profile
            logger.warning("FMP profile %s: %s",
                           ticker, profile.get("error_class") or "unavailable")
        except Exception as e:
            logger.warning("FMP profile %s exception (%s)",
                           ticker, type(e).__name__)

    fund = load_fundamentals(ticker) or {}
    return {
        "status": "mock",
        "source": SOURCE_MOCK_FALLBACK,
        "ticker": ticker,
        "name": fund.get("name", ticker),
        "sector": fund.get("sector", DATA_UNAVAILABLE),
    }


# ═══════════════════════════════════════════════════════════════
# QUOTE  (new — surfaced for dashboard live-price display)
# ═══════════════════════════════════════════════════════════════

def get_quote(ticker: str) -> dict:
    """Live FMP quote, or fallback dict that preserves failure metadata
    so callers can detect quota-exhaustion without retrying."""
    err_class: str | None = None
    http_status: int | None = None
    if _use_live_fmp():
        try:
            from src.data_adapters.fmp_adapter import get_quote as fmp_quote
            q = fmp_quote(ticker)
            if q.get("status") == "available":
                return q
            err_class = q.get("error_class")
            http_status = q.get("http_status")
            logger.warning("FMP quote %s: %s",
                           ticker, err_class or "unavailable")
        except Exception as e:
            err_class = type(e).__name__
            logger.warning("FMP quote %s exception (%s)", ticker, err_class)
    return {
        "status": DATA_UNAVAILABLE,
        "source": SOURCE_MOCK_FALLBACK,
        "ticker": ticker,
        "error_class": err_class,
        "http_status": http_status,
    }


# ═══════════════════════════════════════════════════════════════
# API STATUS
# ═══════════════════════════════════════════════════════════════

def get_api_status() -> dict:
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    data_mode = os.environ.get("DATA_MODE", "mock").strip().lower()
    use_mock = os.environ.get("USE_MOCK_DATA", "true").strip().lower()

    status: dict = {
        "provider": "fmp",
        "api_key_set": bool(api_key),
        "data_mode": data_mode,
        "use_mock_flag": use_mock,
        "active_source": "mock",
        "timestamp_support": True,
    }

    # Adapter-level static flags (always present for the UI)
    from src.data_adapters.fmp_adapter import FMP_BASE_URL
    status["base_url"] = FMP_BASE_URL
    status["legacy_v3_disabled"] = True

    if _use_live_fmp():
        try:
            from src.data_adapters.fmp_adapter import test_connection
            conn = test_connection()
            status["active_source"] = (
                SOURCE_LIVE if conn.get("connected") else "mock"
            )
            status["connection_test"] = conn
        except Exception as e:
            status["active_source"] = "mock"
            status["connection_error_class"] = type(e).__name__
    return status
