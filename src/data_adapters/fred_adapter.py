"""
FRED Adapter — Federal Reserve Economic Data API client.

Fetches macro indicators for regime classification + full yield curve.
Falls back to mock data if FRED_API_KEY is not set.

Point-in-time safety:
- Uses realtime_start/realtime_end parameters when available
- Applies conservative lag for series without vintage support
- All observations carry data_available_as_of timestamps

Caching:
- Daily cache in outputs/fred_cache/
- Auto-fetch once per day on dashboard open or pipeline run
- Manual refresh via "Refresh FRED Data" button
"""
import os
import json
import logging
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _fetch_with_retry(call_fn, max_attempts: int = 3):
    """Retry HTTP call on 5xx; do not retry on 4xx or unknown failures.
    Backoff 1s, 2s, 4s. Returns response object or None on exhaustion.

    Pass 7.2: Pass 7.1 replay observed 9× FRED 500 errors silently
    fail-through-to-mock for a Friday FI review. Retry covers transient
    upstream outages without changing the no-data fail path.
    """
    for attempt in range(max_attempts):
        try:
            r = call_fn()
            if r is None:
                return None
            status = getattr(r, "status_code", None)
            if status is None:
                return r
            if status == 200:
                return r
            if 400 <= status < 500:
                return r
        except Exception:
            pass
        if attempt < max_attempts - 1:
            _time.sleep(2 ** attempt)
    return None


def _load_cache_stale(decision_date: str, max_age_days: int = 14):
    """Walk backward from decision_date to find a prior live-mode cache.
    Bypasses the per-day cache_date key by trying nearby dates. Returns
    (envelope_or_None, age_days). Only live-mode envelopes are accepted —
    a mock-mode envelope as fallback would be a quiet downgrade with no
    informational gain over a fresh mock fetch.
    """
    try:
        target = datetime.strptime(decision_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None, 0
    for delta in range(1, max_age_days + 1):
        prior_date = (target - timedelta(days=delta)).strftime("%Y-%m-%d")
        p = _cache_path(prior_date)
        if not p.exists():
            continue
        try:
            with open(p, "r") as f:
                envelope = json.load(f)
        except Exception:
            continue
        if envelope.get("data_mode") == "live":
            return envelope, delta
    return None, 0


def _today_et() -> str:
    """Return today's date string (YYYY-MM-DD) in America/New_York timezone."""
    return datetime.now(_ET).strftime("%Y-%m-%d")

logger = logging.getLogger(__name__)

_requests = None
FRED_BASE_URL = "https://api.stlouisfed.org/fred"
DATA_UNAVAILABLE = "Data unavailable"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = PROJECT_ROOT / "outputs" / "fred_cache"

# ─── All FRED series ───────────────────────────────────────────
FRED_SERIES = {
    # Yield curve maturities
    "treasury_1m":  {"series_id": "DGS1MO", "label": "1-Month Treasury",   "frequency": "daily", "lag_days": 1, "maturity_months": 1},
    "treasury_3m":  {"series_id": "DGS3MO", "label": "3-Month Treasury",   "frequency": "daily", "lag_days": 1, "maturity_months": 3},
    "treasury_6m":  {"series_id": "DGS6MO", "label": "6-Month Treasury",   "frequency": "daily", "lag_days": 1, "maturity_months": 6},
    "treasury_1y":  {"series_id": "DGS1",   "label": "1-Year Treasury",    "frequency": "daily", "lag_days": 1, "maturity_months": 12},
    "treasury_2y":  {"series_id": "DGS2",   "label": "2-Year Treasury",    "frequency": "daily", "lag_days": 1, "maturity_months": 24},
    "treasury_5y":  {"series_id": "DGS5",   "label": "5-Year Treasury",    "frequency": "daily", "lag_days": 1, "maturity_months": 60},
    "treasury_7y":  {"series_id": "DGS7",   "label": "7-Year Treasury",    "frequency": "daily", "lag_days": 1, "maturity_months": 84},
    "treasury_10y": {"series_id": "DGS10",  "label": "10-Year Treasury",   "frequency": "daily", "lag_days": 1, "maturity_months": 120},
    "treasury_20y": {"series_id": "DGS20",  "label": "20-Year Treasury",   "frequency": "daily", "lag_days": 1, "maturity_months": 240},
    "treasury_30y": {"series_id": "DGS30",  "label": "30-Year Treasury",   "frequency": "daily", "lag_days": 1, "maturity_months": 360},
    # Yield curve spreads
    "yield_curve_10y2y":  {"series_id": "T10Y2Y",  "label": "10Y-2Y Spread",  "frequency": "daily", "lag_days": 1},
    "yield_curve_10y3m":  {"series_id": "T10Y3M",  "label": "10Y-3M Spread",  "frequency": "daily", "lag_days": 1},
    # Policy rate
    "fed_funds_rate": {"series_id": "DFF",     "label": "Effective Fed Funds Rate", "frequency": "daily", "lag_days": 1},
    # Inflation
    "cpi_index":      {"series_id": "CPIAUCSL", "label": "CPI All Urban (SA)",       "frequency": "monthly", "lag_days": 30},
    # Unemployment
    "unemployment_rate": {"series_id": "UNRATE", "label": "Unemployment Rate",       "frequency": "monthly", "lag_days": 35},
    # Credit spreads (added v0.7 — informational descriptors per §11.6;
    # HY OAS feeds the macro side-signal disambiguation per §4.12)
    "hy_oas":         {"series_id": "BAMLH0A0HYM2", "label": "ICE BofA US High Yield OAS (%)", "frequency": "daily", "lag_days": 1},
    "ig_oas":         {"series_id": "BAMLC0A0CM",   "label": "ICE BofA US Corporate OAS (%)",   "frequency": "daily", "lag_days": 1},
    "bbb_oas":        {"series_id": "BAMLC0A4CBBB", "label": "ICE BofA BBB Corporate OAS (%)",  "frequency": "daily", "lag_days": 1},
    "breakeven_10y":  {"series_id": "T10YIE",       "label": "10-Year Breakeven Inflation (%)", "frequency": "daily", "lag_days": 1},
}

# Which keys are used for regime classification (subset of all)
REGIME_KEYS = [
    "treasury_10y", "treasury_2y", "yield_curve_10y2y",
    "fed_funds_rate", "cpi_yoy", "unemployment_rate",
]

# Yield curve maturity order for chart
YIELD_CURVE_MATURITIES = [
    ("treasury_1m",  "1M"),
    ("treasury_3m",  "3M"),
    ("treasury_6m",  "6M"),
    ("treasury_1y",  "1Y"),
    ("treasury_2y",  "2Y"),
    ("treasury_5y",  "5Y"),
    ("treasury_7y",  "7Y"),
    ("treasury_10y", "10Y"),
    ("treasury_20y", "20Y"),
    ("treasury_30y", "30Y"),
]


def _get_requests():
    global _requests
    if _requests is None:
        import requests
        _requests = requests
    return _requests


def _get_api_key():
    return os.environ.get("FRED_API_KEY", "").strip() or None


# ═══════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════

def _cache_path(cache_date: str) -> Path:
    return CACHE_DIR / f"fred_{cache_date}.json"


def _load_cache(cache_date: str) -> dict | None:
    p = _cache_path(cache_date)
    if p.exists():
        try:
            with open(p, "r") as f:
                data = json.load(f)
            logger.info(f"FRED cache hit: {p.name}")
            return data
        except Exception as e:
            logger.warning(f"FRED cache read error: {e}")
    return None


def _save_cache(cache_date: str, data: dict, data_mode_label: str | None = None):
    """Persist a daily FRED cache envelope.

    data_mode_label: explicit "live" | "mock" label. If omitted, inferred from
    the presence of FRED_API_KEY (legacy behavior). Callers that have already
    fallen back from live to mock should pass "mock" explicitly so the cache
    file accurately records what it contains.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(cache_date)
    try:
        if data_mode_label is None:
            data_mode_label = "live" if _get_api_key() else "mock"
        cache_envelope = {
            "cache_date": cache_date,
            "fetched_at": datetime.now(_ET).isoformat(),
            "data_mode": data_mode_label,
            "api_success": data.get("_api_success", True),
            "series_retrieved": data.get("_series_retrieved", []),
            "series_missing": data.get("_series_missing", []),
            "indicators": {k: v for k, v in data.items() if not k.startswith("_")},
        }
        with open(p, "w") as f:
            json.dump(cache_envelope, f, indent=2, default=str)
        logger.info(f"FRED cache saved: {p.name} (mode={data_mode_label})")
    except Exception as e:
        logger.warning(f"FRED cache write error: {e}")


def get_cache_status(cache_date: str | None = None) -> dict:
    """Return cache metadata for a given date (defaults to today)."""
    if cache_date is None:
        cache_date = _today_et()
    p = _cache_path(cache_date)
    if p.exists():
        try:
            with open(p, "r") as f:
                data = json.load(f)
            return {
                "cached": True,
                "cache_date": cache_date,
                "fetched_at": data.get("fetched_at"),
                "data_mode": data.get("data_mode"),
                "api_success": data.get("api_success"),
                "series_retrieved": data.get("series_retrieved", []),
                "series_missing": data.get("series_missing", []),
            }
        except Exception:
            pass
    return {"cached": False, "cache_date": cache_date}


# ═══════════════════════════════════════════════════════════════
# LIVE FRED API
# ═══════════════════════════════════════════════════════════════

def _fred_api_call(endpoint, params=None):
    api_key = _get_api_key()
    if not api_key:
        return None
    requests = _get_requests()
    url = f"{FRED_BASE_URL}/{endpoint}"
    if params is None:
        params = {}
    params["api_key"] = api_key
    params["file_type"] = "json"

    def _do_call():
        return requests.get(url, params=params, timeout=15)

    try:
        resp = _fetch_with_retry(_do_call, max_attempts=3)
        if resp is None:
            logger.warning(f"FRED API call failed ({endpoint}): all retries exhausted")
            return None
        if resp.status_code != 200:
            logger.warning(f"FRED API call failed ({endpoint}): status={resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        logger.warning(f"FRED API call failed ({endpoint}): {e}")
        return None


def get_series_observations(
    series_id: str,
    observation_date: str | None = None,
    realtime_start: str | None = None,
    realtime_end: str | None = None,
    limit: int = 5,
) -> list[dict]:
    params = {
        "series_id": series_id,
        "sort_order": "desc",
        "limit": limit,
    }
    if observation_date:
        params["observation_end"] = observation_date
    # Only add realtime params if they are actual valid past dates
    # FRED rejects realtime_end values beyond its database date
    if realtime_start:
        params["realtime_start"] = realtime_start
    if realtime_end:
        # Don't send realtime_end — let FRED use latest available vintage
        # This avoids 400 errors when decision_date is beyond FRED's latest data
        pass

    data = _fred_api_call("series/observations", params)
    if not data or "observations" not in data:
        return []

    results = []
    for obs in data["observations"]:
        val = obs.get("value", ".")
        results.append({
            "date": obs.get("date", ""),
            "value": float(val) if val not in (".", "") else None,
            "realtime_start": obs.get("realtime_start", ""),
            "realtime_end": obs.get("realtime_end", ""),
        })
    return results


def get_macro_indicators_live(decision_date: str) -> dict:
    """Fetch all macro indicators from FRED for a given decision date."""
    indicators = {}
    retrieved = []
    missing = []

    for key, meta in FRED_SERIES.items():
        lag = meta["lag_days"]
        dt = datetime.strptime(decision_date, "%Y-%m-%d")
        effective_obs_end = (dt - timedelta(days=lag)).strftime("%Y-%m-%d")

        obs = get_series_observations(
            series_id=meta["series_id"],
            observation_date=effective_obs_end,
            realtime_end=decision_date,
            limit=15 if meta["series_id"] == "CPIAUCSL" else 3,
        )

        if obs and obs[0]["value"] is not None:
            indicator_entry = {
                "series_id": meta["series_id"],
                "label": meta["label"],
                "value": obs[0]["value"],
                "observation_date": obs[0]["date"],
                "realtime_start": obs[0]["realtime_start"],
                "realtime_end": obs[0]["realtime_end"],
                "data_available_as_of": decision_date,
                "conservative_lag_days": lag,
                "conservative_lag_applied": True,
                "vintage_date": obs[0].get("realtime_end", DATA_UNAVAILABLE),
                "missing_data_flag": False,
                "source": "FRED",
            }

            # Store prior observation for shift detection
            # DFF: use the 2nd-most-recent daily observation
            if key == "fed_funds_rate" and len(obs) >= 2 and obs[1]["value"] is not None:
                indicator_entry["prior_value"] = obs[1]["value"]
                indicator_entry["prior_observation_date"] = obs[1]["date"]

            # UNRATE: fetch ~3-month-ago observation for trend detection
            if key == "unemployment_rate":
                try:
                    cur_dt = datetime.strptime(obs[0]["date"], "%Y-%m-%d")
                    target_dt = cur_dt - timedelta(days=90)
                    prior_obs = get_series_observations(
                        series_id="UNRATE",
                        observation_date=target_dt.strftime("%Y-%m-%d"),
                        limit=1,
                    )
                    if prior_obs and prior_obs[0]["value"] is not None:
                        indicator_entry["prior_value"] = prior_obs[0]["value"]
                        indicator_entry["prior_observation_date"] = prior_obs[0]["date"]
                except Exception:
                    pass

            indicators[key] = indicator_entry
            retrieved.append(key)

            # CPI YoY: if this is cpi_index, compute YoY from 13-month-ago observation
            if key == "cpi_index" and len(obs) >= 2:
                # Find observation ~12 months ago
                current_val = obs[0]["value"]
                current_date = obs[0]["date"]
                try:
                    cur_dt = datetime.strptime(current_date, "%Y-%m-%d")
                    target_dt = cur_dt - timedelta(days=365)
                    # Find closest observation to 12 months ago
                    prior_obs = get_series_observations(
                        series_id="CPIAUCSL",
                        observation_date=target_dt.strftime("%Y-%m-%d"),
                        realtime_end=decision_date,
                        limit=1,
                    )
                    if prior_obs and prior_obs[0]["value"] is not None:
                        yoy_pct = round(
                            (current_val - prior_obs[0]["value"])
                            / prior_obs[0]["value"] * 100, 2
                        )
                        indicators["cpi_yoy"] = {
                            "series_id": "CPIAUCSL (YoY calc)",
                            "label": "CPI YoY Inflation %",
                            "value": yoy_pct,
                            "observation_date": current_date,
                            "prior_observation_date": prior_obs[0]["date"],
                            "prior_value": prior_obs[0]["value"],
                            "current_value": current_val,
                            "realtime_start": obs[0]["realtime_start"],
                            "realtime_end": obs[0]["realtime_end"],
                            "data_available_as_of": decision_date,
                            "conservative_lag_days": lag,
                            "conservative_lag_applied": True,
                            "vintage_date": obs[0].get("realtime_end", DATA_UNAVAILABLE),
                            "missing_data_flag": False,
                            "source": "FRED",
                        }
                        retrieved.append("cpi_yoy")
                except Exception:
                    pass
        else:
            indicators[key] = {
                "series_id": meta["series_id"],
                "label": meta["label"],
                "value": None,
                "observation_date": DATA_UNAVAILABLE,
                "realtime_start": DATA_UNAVAILABLE,
                "realtime_end": DATA_UNAVAILABLE,
                "data_available_as_of": DATA_UNAVAILABLE,
                "conservative_lag_days": lag,
                "conservative_lag_applied": True,
                "vintage_date": DATA_UNAVAILABLE,
                "missing_data_flag": True,
                "source": "FRED",
            }
            missing.append(key)

    # Compute spreads if missing but components available
    _compute_spread(indicators, "yield_curve_10y2y", "treasury_10y", "treasury_2y", retrieved)
    _compute_spread(indicators, "yield_curve_10y3m", "treasury_10y", "treasury_3m", retrieved)

    indicators["_api_success"] = True
    indicators["_series_retrieved"] = retrieved
    indicators["_series_missing"] = missing
    return indicators


def _compute_spread(indicators, spread_key, long_key, short_key, retrieved):
    """Compute a yield spread from components if the direct series is missing."""
    ind = indicators.get(spread_key, {})
    if ind.get("value") is None:
        long_val = indicators.get(long_key, {}).get("value")
        short_val = indicators.get(short_key, {}).get("value")
        if long_val is not None and short_val is not None:
            indicators[spread_key] = {
                "series_id": f"{long_key}-{short_key} (computed)",
                "label": indicators.get(spread_key, {}).get("label", f"{long_key}-{short_key}"),
                "value": round(long_val - short_val, 2),
                "observation_date": indicators[long_key]["observation_date"],
                "realtime_start": indicators[long_key]["realtime_start"],
                "realtime_end": indicators[long_key]["realtime_end"],
                "data_available_as_of": indicators[long_key]["data_available_as_of"],
                "conservative_lag_days": 1,
                "conservative_lag_applied": True,
                "vintage_date": indicators[long_key].get("vintage_date", DATA_UNAVAILABLE),
                "missing_data_flag": False,
                "source": "FRED",
            }
            if spread_key not in retrieved:
                retrieved.append(spread_key)


# ═══════════════════════════════════════════════════════════════
# MOCK DATA
# ═══════════════════════════════════════════════════════════════

def get_macro_indicators_mock(decision_date: str) -> dict:
    """Mock macro indicators simulating a weakening environment.

    Note: observation_date is set to decision_date as a *synthetic placeholder*.
    The values are constants and do NOT represent actual observations on that
    date. Each indicator carries `synthetic_observation: True` so consumers
    can clearly label these as mock placeholders rather than real FRED dates.
    """
    base = {
        "treasury_1m":  4.50, "treasury_3m": 4.55, "treasury_6m": 4.48,
        "treasury_1y":  4.40, "treasury_2y": 4.60, "treasury_5y": 4.35,
        "treasury_7y":  4.28, "treasury_10y": 4.25, "treasury_20y": 4.50,
        "treasury_30y": 4.42,
        "yield_curve_10y2y": -0.35, "yield_curve_10y3m": -0.30,
        "fed_funds_rate": 5.33,
        "cpi_index": 315.6, "cpi_yoy": 3.2,
        "unemployment_rate": 4.0,
        # Credit spreads (mock, in % — 4.10 == 410 bps).
        # Mid-cycle baseline values; tests synthesize their own thresholds.
        "hy_oas": 4.10,
        "ig_oas": 1.05,
        "bbb_oas": 1.45,
        "breakeven_10y": 2.30,
    }
    mock = {}
    for key, val in base.items():
        meta = FRED_SERIES.get(key, {})
        mock[key] = {
            "series_id": meta.get("series_id", key),
            "label": meta.get("label", key),
            "value": val,
            "observation_date": decision_date,
            "realtime_start": decision_date,
            "realtime_end": decision_date,
            "data_available_as_of": f"{decision_date}T08:00:00-05:00",
            "conservative_lag_days": meta.get("lag_days", 1),
            "conservative_lag_applied": True,
            "vintage_date": decision_date,
            "missing_data_flag": False,
            "source": "mock",
            "synthetic_observation": True,
        }
    # cpi_yoy is a computed indicator, not in FRED_SERIES
    mock["cpi_yoy"]["series_id"] = "CPIAUCSL (YoY calc)"
    mock["cpi_yoy"]["label"] = "CPI YoY Inflation %"
    # Add prior values for shift detection
    mock["fed_funds_rate"]["prior_value"] = 5.33
    mock["fed_funds_rate"]["prior_observation_date"] = decision_date
    mock["unemployment_rate"]["prior_value"] = 3.9
    mock["unemployment_rate"]["prior_observation_date"] = decision_date
    mock["_api_success"] = True
    mock["_series_retrieved"] = list(base.keys())
    mock["_series_missing"] = []
    return mock


# ═══════════════════════════════════════════════════════════════
# UNIFIED INTERFACE
# ═══════════════════════════════════════════════════════════════

def get_macro_indicators(decision_date: str, force_refresh: bool = False) -> dict:
    """Fetch macro indicators with daily cache, keyed by `decision_date`.

    BACKTEST / pipeline semantics: caller supplies the historical decision_date
    and the cache is keyed by that date. For live dashboard use,
    `get_macro_indicators_for_dashboard()` should be used instead — it
    enforces cache_date == today_ET and never loads stale-date caches.
    """
    use_mock = os.environ.get("USE_MOCK_DATA", "true").lower() == "true"
    data_mode = os.environ.get("DATA_MODE", "mock").lower()
    has_key = _get_api_key() is not None
    use_live = has_key and not use_mock and data_mode != "mock"

    # Check cache first (unless force refresh)
    if not force_refresh:
        cached = _load_cache(decision_date)
        if cached:
            indicators = cached.get("indicators", {})
            if indicators:
                indicators["_from_cache"] = True
                indicators["_cache_fetched_at"] = cached.get("fetched_at")
                indicators["_cache_data_mode"] = cached.get("data_mode", "unknown")
                return indicators

    if use_live:
        try:
            indicators = get_macro_indicators_live(decision_date)
            non_null = sum(1 for k, v in indicators.items()
                          if not k.startswith("_") and v.get("value") is not None)
            if non_null > 0:
                logger.info(f"FRED live: {non_null} indicators retrieved")
                _save_cache(decision_date, indicators, data_mode_label="live")
                return indicators
            logger.warning("FRED: All indicators null, attempting stale-cache fallback")
        except Exception as e:
            logger.warning(f"FRED API failed: {e}, attempting stale-cache fallback")

        # Pass 7.2: stale-cache fallback before mock-fallback. The 9 FRED 500
        # errors in Pass 7.1 replay caused all-null indicators which fell
        # straight to mock and silently corrupted Friday FI deployment
        # reasoning. A recent prior-day live cache is closer to truth than
        # synthetic mock values.
        stale_envelope, stale_age = _load_cache_stale(decision_date)
        if stale_envelope is not None:
            stale_indicators = dict(stale_envelope.get("indicators", {}))
            if stale_indicators:
                stale_indicators["_stale_flag"] = True
                stale_indicators["_stale_reason"] = "fred_5xx_fallback"
                stale_indicators["_stale_age_hours"] = stale_age * 24
                stale_indicators["_stale_origin_date"] = stale_envelope.get("cache_date")
                logger.info(
                    f"FRED stale-cache fallback: using {stale_age}-day-old "
                    f"live cache ({stale_envelope.get('cache_date')}) for {decision_date}"
                )
                return stale_indicators
        logger.warning("FRED stale-cache fallback: no prior live cache; using mock")

    indicators = get_macro_indicators_mock(decision_date)
    if use_live:
        # Pass 7.2: mark mock fallback as a data-quality warning when live
        # was intended. PM/FI consumers can read this flag and reason under
        # §13.6.SOFT_REASONING / FI default-deviation guidance.
        indicators["_data_quality"] = {
            "severity": "warning",
            "reason": "fred_5xx_no_cache",
        }
    _save_cache(decision_date, indicators, data_mode_label="mock")
    return indicators


# ═══════════════════════════════════════════════════════════════
# DASHBOARD-MODE INTERFACE
#   - cache_date == dashboard_run_date == today_ET (always)
#   - stale-date caches are never loaded as live dashboard data
#   - explicit data_mode / source labels on every response
# ═══════════════════════════════════════════════════════════════

def get_macro_indicators_for_dashboard(force_refresh: bool = False) -> dict:
    """Live dashboard fetch.

    Invariant: cache_date == dashboard_run_date == today in America/New_York.
    Stale FRED cache files keyed by other dates are NEVER loaded here, even
    if they exist on disk.

    Adds these underscore-prefixed metadata fields to the returned dict (the
    leading underscore keeps them out of indicator iteration):
      _dashboard_run_date: today (ET) — the visible "now" date
      _cache_date:         today (ET) — always equals dashboard_run_date
      _data_mode:          "live" | "mock"
      _source:             "fresh_api" | "daily_cache" | "mock_fallback"
      _fetched_at:         ISO timestamp ET (when this indicator set was retrieved)
      _from_cache:         bool
    """
    dashboard_run_date = _today_et()
    cache_date = dashboard_run_date

    has_key = _get_api_key() is not None
    use_mock_env = os.environ.get("USE_MOCK_DATA", "true").lower() == "true"
    data_mode_env = os.environ.get("DATA_MODE", "mock").lower()
    use_live = has_key and not use_mock_env and data_mode_env != "mock"
    intended_mode = "live" if use_live else "mock"

    # 1. Try TODAY's cache only — never stale-date caches.
    #    Cache-mode validation: a mock-mode cache MUST NOT satisfy a live request,
    #    and a live-mode cache MUST NOT satisfy a forced-mock request. Otherwise
    #    a mock cache file written earlier today would silently serve as if it
    #    were live FRED data after FRED_API_KEY is added.
    if not force_refresh:
        if _cache_path(cache_date).exists():
            cached = _load_cache(cache_date)
            if cached:
                indicators = dict(cached.get("indicators", {}))
                if indicators:
                    cached_mode = cached.get("data_mode", "unknown")
                    cached_api_success = cached.get("api_success", True)
                    mode_matches = (cached_mode == intended_mode)
                    if mode_matches and cached_api_success:
                        indicators["_dashboard_run_date"] = dashboard_run_date
                        indicators["_cache_date"] = cache_date
                        indicators["_data_mode"] = cached_mode
                        indicators["_source"] = "daily_cache"
                        indicators["_fetched_at"] = cached.get("fetched_at")
                        indicators["_from_cache"] = True
                        return indicators
                    else:
                        logger.info(
                            f"FRED cache rejected: cached_mode={cached_mode}, "
                            f"intended_mode={intended_mode}, "
                            f"api_success={cached_api_success}. "
                            f"Will refetch (cache file remains on disk for audit)."
                        )

    # 2. Try live FRED
    if use_live:
        try:
            indicators = get_macro_indicators_live(dashboard_run_date)
            non_null = sum(
                1 for k, v in indicators.items()
                if not k.startswith("_") and isinstance(v, dict)
                and v.get("value") is not None
            )
            if non_null > 0:
                _save_cache(cache_date, indicators, data_mode_label="live")
                indicators["_dashboard_run_date"] = dashboard_run_date
                indicators["_cache_date"] = cache_date
                indicators["_data_mode"] = "live"
                indicators["_source"] = "fresh_api"
                indicators["_fetched_at"] = datetime.now(_ET).isoformat()
                indicators["_from_cache"] = False
                return indicators
            logger.warning("FRED live: all indicators null — falling back to mock")
        except Exception as e:
            logger.warning(f"FRED live failed: {e} — falling back to mock")

    # 3. Mock fallback
    indicators = get_macro_indicators_mock(dashboard_run_date)
    _save_cache(cache_date, indicators, data_mode_label="mock")
    indicators["_dashboard_run_date"] = dashboard_run_date
    indicators["_cache_date"] = cache_date
    indicators["_data_mode"] = "mock"
    indicators["_source"] = "mock_fallback"
    indicators["_fetched_at"] = datetime.now(_ET).isoformat()
    indicators["_from_cache"] = False
    return indicators


def build_date_metadata(indicators: dict, decision_date: str | None = None) -> dict:
    """Build a normalized 'dates' block for API responses.

    Pulls dashboard / cache / mode / source from indicator metadata fields
    set by `get_macro_indicators_for_dashboard()`. For backtest mode, pass
    `decision_date` explicitly.

    Returns:
      {
        dashboard_run_date: "YYYY-MM-DD" | null   (live mode)
        cache_date:         "YYYY-MM-DD" | null
        decision_date:      "YYYY-MM-DD" | null   (backtest mode)
        data_mode:          "live" | "mock" | "unknown"
        source:             "fresh_api" | "daily_cache" | "mock_fallback" | "unknown"
        fetched_at:         ISO timestamp | null
        from_cache:         bool
        observation_date_by_series: { series_id: observation_date, ... }
        synthetic_observation_dates: bool   (true if any indicator is mock)
      }
    """
    obs_by_series: dict[str, str] = {}
    has_synthetic = False
    for key, ind in indicators.items():
        if key.startswith("_") or not isinstance(ind, dict):
            continue
        sid = ind.get("series_id")
        obs = ind.get("observation_date", DATA_UNAVAILABLE)
        if sid:
            obs_by_series[sid] = obs
        if ind.get("synthetic_observation"):
            has_synthetic = True

    return {
        "dashboard_run_date": indicators.get("_dashboard_run_date"),
        "cache_date": indicators.get("_cache_date"),
        "decision_date": decision_date,
        "data_mode": indicators.get("_data_mode", "unknown"),
        "source": indicators.get("_source", "unknown"),
        "fetched_at": indicators.get("_fetched_at"),
        "from_cache": indicators.get("_from_cache", False),
        "observation_date_by_series": obs_by_series,
        "synthetic_observation_dates": has_synthetic,
    }


def get_yield_curve_data(
    decision_date: str | None = None,
    indicators: dict | None = None,
) -> dict:
    """Get yield curve data points for charting.

    If `indicators` is provided, use it directly (avoids re-fetching when the
    caller has already pulled today's indicators via
    `get_macro_indicators_for_dashboard()`).
    """
    if indicators is None:
        if decision_date is None:
            decision_date = _today_et()
        indicators = get_macro_indicators(decision_date)
    points = []
    missing_maturities = []
    obs_dates = set()

    for key, label in YIELD_CURVE_MATURITIES:
        ind = indicators.get(key, {})
        val = ind.get("value")
        if val is not None:
            points.append({
                "maturity": label,
                "yield_pct": val,
                "observation_date": ind.get("observation_date", ""),
                "series_id": ind.get("series_id", ""),
            })
            obs_date = ind.get("observation_date", "")
            if obs_date and obs_date != DATA_UNAVAILABLE:
                obs_dates.add(obs_date)
        else:
            missing_maturities.append(label)

    # Determine curve shape
    shape = "insufficient_data"
    if len(points) >= 3:
        short_end = points[0]["yield_pct"]
        long_end = points[-1]["yield_pct"]
        mid = points[len(points) // 2]["yield_pct"]
        if long_end > short_end + 0.3:
            shape = "upward_sloping"
        elif long_end < short_end - 0.3:
            shape = "inverted"
        else:
            shape = "flat"

    spread_10y2y = indicators.get("yield_curve_10y2y", {}).get("value")
    spread_10y3m = indicators.get("yield_curve_10y3m", {}).get("value")
    source = next(
        (v.get("source", "mock") for k, v in indicators.items()
         if not k.startswith("_") and isinstance(v, dict)),
        "mock"
    )

    return {
        "points": points,
        "missing_maturities": missing_maturities,
        "curve_shape": shape,
        "spread_10y_2y": spread_10y2y,
        "spread_10y_3m": spread_10y3m,
        "latest_observation_date": max(obs_dates) if obs_dates else DATA_UNAVAILABLE,
        "decision_date": decision_date,
        "source": source,
        "from_cache": indicators.get("_from_cache", False),
        "warnings": [f"Missing maturities: {', '.join(missing_maturities)}"]
                     if missing_maturities else [],
    }


def generate_validation_report(
    decision_date: str | None = None,
    indicators: dict | None = None,
) -> dict:
    """Generate FRED API validation report.

    If `indicators` is provided, use it directly (avoids re-fetching when the
    caller has already pulled today's indicators via
    `get_macro_indicators_for_dashboard()`).
    """
    if decision_date is None:
        decision_date = _today_et()

    has_key = _get_api_key() is not None
    if indicators is None:
        indicators = get_macro_indicators(decision_date)
    cache_status = get_cache_status(decision_date)

    total_series = len(FRED_SERIES) + 1  # +1 for cpi_yoy
    retrieved = [k for k, v in indicators.items()
                 if not k.startswith("_") and isinstance(v, dict) and v.get("value") is not None]
    missing = [k for k, v in indicators.items()
               if not k.startswith("_") and isinstance(v, dict) and v.get("value") is None]

    series_detail = []
    for key in sorted(indicators.keys()):
        if key.startswith("_"):
            continue
        ind = indicators[key]
        if not isinstance(ind, dict):
            continue
        series_detail.append({
            "key": key,
            "series_id": ind.get("series_id", ""),
            "label": ind.get("label", ""),
            "value": ind.get("value"),
            "observation_date": ind.get("observation_date", DATA_UNAVAILABLE),
            "realtime_start": ind.get("realtime_start", DATA_UNAVAILABLE),
            "realtime_end": ind.get("realtime_end", DATA_UNAVAILABLE),
            "has_realtime": ind.get("realtime_start", DATA_UNAVAILABLE) != DATA_UNAVAILABLE,
            "missing": ind.get("missing_data_flag", False),
            "source": ind.get("source", "unknown"),
        })

    warnings = []
    if not has_key:
        warnings.append("FRED_API_KEY not set -- using mock data")
    if missing:
        warnings.append(f"{len(missing)} series missing: {', '.join(missing)}")
    from_cache = indicators.get("_from_cache", False)
    if from_cache:
        warnings.append(f"Data loaded from cache (fetched: {indicators.get('_cache_fetched_at', '?')})")

    return {
        "validation_timestamp": datetime.now(_ET).isoformat(),
        "fred_api_key_set": has_key,
        "api_connection": "success" if has_key and not from_cache else ("cached" if from_cache else "mock_fallback"),
        "decision_date": decision_date,
        "total_series_requested": total_series,
        "series_retrieved": len(retrieved),
        "series_missing": len(missing),
        "retrieved_keys": retrieved,
        "missing_keys": missing,
        "series_detail": series_detail,
        "realtime_support": all(s.get("has_realtime") for s in series_detail if not s.get("missing")),
        "usable_for_live": len(retrieved) >= 6,
        "usable_for_backtest": all(s.get("has_realtime") for s in series_detail if not s.get("missing")),
        "cache_status": cache_status,
        "warnings": warnings,
        "source": indicators.get(next(
            (k for k in retrieved if k in indicators), ""), {}).get("source", "unknown") if retrieved else "none",
    }


def test_connection() -> dict:
    """Test FRED API connection."""
    api_key = _get_api_key()
    if not api_key:
        return {"connected": False, "provider": "fred", "reason": "FRED_API_KEY not set"}
    obs = get_series_observations("DGS10", limit=1)
    if obs:
        return {
            "connected": True,
            "provider": "fred",
            "test_series": "DGS10",
            "latest_value": obs[0]["value"],
            "latest_date": obs[0]["date"],
        }
    return {"connected": False, "provider": "fred", "reason": "No data returned"}
