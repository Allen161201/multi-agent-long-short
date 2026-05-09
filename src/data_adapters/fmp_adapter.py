"""
FMP Adapter — Financial Modeling Prep client (Phase A).

Targets FMP's CURRENT /stable/ surface. The legacy /api/v3/ endpoints were
deprecated by FMP on 2025-08-31 and now return HTTP 403 "Legacy Endpoint"
for non-grandfathered keys.

Security invariants (never violate):
  - The API key is read from os.environ only.
  - The API key is never embedded in a URL string. It is passed only via
    the `params` dict to requests.get().
  - We do NOT call response.raise_for_status() — its message format
    "<status> Client Error: <reason> for url: <full URL with apikey>"
    leaks the key. Errors are inspected manually and logged with
    redacted URLs only.
  - _redact() strips apikey from any string before logging.

Source flags returned in every response:
  - "live_fmp"      : real FMP /stable response (HTTP 200, valid JSON)
  - "live_fmp_failed": live attempt failed; caller should fall back to mock
  - "cache"         : (reserved; not used today)
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

FMP_BASE_URL = "https://financialmodelingprep.com/stable"
DATA_UNAVAILABLE = "Data unavailable"
DEFAULT_HISTORICAL_DAYS = 30

# ── Source flags (single source of truth) ──
SOURCE_LIVE = "live_fmp"
SOURCE_LIVE_FAILED = "live_fmp_failed"
SOURCE_CACHE = "cache"
SOURCE_MOCK_FALLBACK = "mock_fallback"  # tagged by market_data.py, not here

# ── Rate-limit policy (Premium plan, conservative) ──
# FMP Premium official cap: 750 calls/min. We cap at 600 internally.
RATE_LIMIT_PER_MINUTE = 600
RATE_LIMIT_HARD_PAUSE_SECONDS = 60  # how long we ignore FMP after a 429

# Per-endpoint cache TTL (seconds)
CACHE_TTL_DEFAULT = 300
CACHE_TTL_QUOTE = 300              # 5 min
CACHE_TTL_INTRADAY = 300           # 5 min
CACHE_TTL_TECHNICAL = 300          # 5 min
CACHE_TTL_PROFILE = 24 * 3600      # 24 hr
CACHE_TTL_FUNDAMENTALS = 12 * 3600 # 12 hr
CACHE_TTL_CALENDAR = 12 * 3600     # 12 hr
CACHE_TTL_DCF = 12 * 3600          # 12 hr
CACHE_TTL_NEWS = 300               # 5 min — same as quote (near-real-time)
CACHE_TTL_CHART_INTRADAY = 60      # 1 min — multi-timeframe chart (1D/3D/1W slices)
CACHE_TTL_CHART_DAILY = 12 * 3600  # 12 hr — multi-timeframe chart (1M+ slices)

# Match `apikey=<value>` in any string and replace the value with REDACTED.
_APIKEY_RE = re.compile(r"(apikey)=([^&\s\"']+)", re.IGNORECASE)


def _redact(s: object) -> str:
    """Strip apikey=... from any string. Always call before logging."""
    if s is None:
        return ""
    return _APIKEY_RE.sub(r"\1=REDACTED", str(s))


class _RedactingFilter(logging.Filter):
    """Logger filter that redacts apikey=... from msg and string args.
    Used to defang noisy third-party loggers (urllib3, requests) that
    otherwise emit full request URLs at DEBUG level."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = _redact(record.msg)
            if record.args:
                record.args = tuple(
                    _redact(a) if isinstance(a, str) else a
                    for a in record.args
                )
        except Exception:
            pass
        return True


class _MinuteRateLimiter:
    """Rolling 60-second window. Block (with capped sleep) if the next
    call would exceed `max_per_minute`. Thread-safe."""

    def __init__(self, max_per_minute: int = RATE_LIMIT_PER_MINUTE):
        self.max = max_per_minute
        self.times: deque[float] = deque()
        self.lock = threading.Lock()
        self.total_calls = 0
        self.last_call_at: float | None = None
        self.last_wait_s: float = 0.0

    def acquire(self) -> float:
        """Reserve a slot; returns the wait incurred, in seconds."""
        with self.lock:
            now = time.time()
            cutoff = now - 60
            while self.times and self.times[0] < cutoff:
                self.times.popleft()
            wait = 0.0
            if len(self.times) >= self.max:
                wait = max(0.0, 60.0 - (now - self.times[0]) + 0.05)
            slot_t = now + wait
            self.times.append(slot_t)
            self.total_calls += 1
            self.last_call_at = slot_t
            self.last_wait_s = wait
        if wait > 0:
            time.sleep(min(wait, 5.0))  # cap each sleep so the dashboard never hangs
        return wait

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            cutoff = now - 60
            current = sum(1 for t in self.times if t >= cutoff)
            return {
                "configured_max_per_minute": self.max,
                "current_rolling_minute_count": current,
                "total_calls_since_start": self.total_calls,
                "last_call_at": (
                    datetime.fromtimestamp(self.last_call_at, tz=timezone.utc)
                    .isoformat() if self.last_call_at else None),
                "last_wait_s": round(self.last_wait_s, 3),
            }


_rate_limiter = _MinuteRateLimiter()


# ── Per-endpoint call counter (telemetry) ──
_call_group_counts: dict[str, int] = defaultdict(int)
_call_group_lock = threading.Lock()


def _bump_group(group: str) -> None:
    with _call_group_lock:
        _call_group_counts[group] += 1


def get_call_group_summary() -> dict[str, int]:
    with _call_group_lock:
        return dict(_call_group_counts)


# ── Sticky 429 / rate-limit pause ──
_sticky_pause_until: float = 0.0
_sticky_pause_reason: str | None = None
_sticky_pause_lock = threading.Lock()


def _set_sticky_pause(reason: str, seconds: float = RATE_LIMIT_HARD_PAUSE_SECONDS):
    global _sticky_pause_until, _sticky_pause_reason
    with _sticky_pause_lock:
        _sticky_pause_until = time.time() + seconds
        _sticky_pause_reason = reason


def _is_sticky_paused() -> tuple[bool, str | None, float]:
    """Returns (paused, reason, seconds_remaining)."""
    with _sticky_pause_lock:
        now = time.time()
        if now < _sticky_pause_until:
            return True, _sticky_pause_reason, round(_sticky_pause_until - now, 1)
    return False, None, 0.0


def reset_sticky_pause() -> dict:
    """Clear any sticky 429/402 pause. Used after API key rotation or when
    operator wants to retry immediately. Safe to call repeatedly."""
    global _sticky_pause_until, _sticky_pause_reason
    with _sticky_pause_lock:
        prior_remaining = max(0.0, _sticky_pause_until - time.time())
        prior_reason = _sticky_pause_reason
        _sticky_pause_until = 0.0
        _sticky_pause_reason = None
    return {
        "cleared": True,
        "prior_pause_seconds_remaining": round(prior_remaining, 1),
        "prior_reason": prior_reason,
    }


def get_rate_limit_status() -> dict:
    paused, reason, remaining = _is_sticky_paused()
    snap = _rate_limiter.snapshot()
    snap["sticky_paused"] = paused
    snap["sticky_pause_reason"] = reason
    snap["sticky_pause_seconds_remaining"] = remaining
    return snap


# ── TTL cache (process-local, thread-safe) ──
class _TTLCache:
    def __init__(self):
        self._store: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple, ttl: float) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                self.misses += 1
                return None
            stored_at, value = entry
            if (time.time() - stored_at) > ttl:
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.time(), value)

    def stats(self) -> dict:
        with self._lock:
            return {"hits": self.hits, "misses": self.misses,
                    "entries": len(self._store)}


_cache = _TTLCache()


def get_cache_stats() -> dict:
    return _cache.stats()


def clear_cache(prefix: str | None = None) -> dict:
    """Drop cached entries. If `prefix` is None, drop everything; otherwise
    drop only entries whose first key element equals `prefix` (e.g. "quote",
    "profile", "search"). Safe to call repeatedly."""
    with _cache._lock:
        if prefix is None:
            removed = len(_cache._store)
            _cache._store.clear()
        else:
            keys = [k for k in _cache._store if k and k[0] == prefix]
            for k in keys:
                _cache._store.pop(k, None)
            removed = len(keys)
    return {"cleared_entries": removed, "prefix": prefix}


def get_key_fingerprint() -> dict:
    """Return safe metadata about the loaded FMP_API_KEY. Never returns the
    key value. The fingerprint is the first 8 chars of sha256(key) so that
    two different keys produce different fingerprints, but the original
    cannot be recovered from it."""
    import hashlib
    raw = os.environ.get("FMP_API_KEY", "") or ""
    stripped = raw.strip()
    if not stripped:
        return {"key_set": False, "key_length": 0, "key_fingerprint": None,
                "had_outer_whitespace": raw != stripped}
    fp = hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:8]
    return {
        "key_set": True,
        "key_length": len(stripped),
        "key_fingerprint": fp,
        "had_outer_whitespace": raw != stripped,
    }


def _harden_third_party_loggers() -> None:
    """Run once at module import.

    1) Force urllib3 / requests connectionpool loggers to WARNING so
       request URLs are not logged at DEBUG.
    2) Install a redacting filter so that even if some other handler
       logs a record at DEBUG, the apikey value is replaced with REDACTED.
    """
    targets = (
        "urllib3",
        "urllib3.connectionpool",
        "requests",
        "requests.packages.urllib3",
        "requests.packages.urllib3.connectionpool",
    )
    flt = _RedactingFilter()
    for name in targets:
        lg = logging.getLogger(name)
        if lg.level == logging.NOTSET or lg.level < logging.WARNING:
            lg.setLevel(logging.WARNING)
        lg.addFilter(flt)


_harden_third_party_loggers()


def _get_api_key() -> str | None:
    key = os.environ.get("FMP_API_KEY", "").strip()
    return key if key else None


def _api_call(path: str, params: dict | None = None,
              group: str | None = None) -> tuple[Any, dict]:
    """
    Authenticated FMP /stable call with rate-limiter and 429-pause.

    Returns (data, meta). `data` is the parsed JSON on HTTP 200, else None.
    """
    api_key = _get_api_key()
    meta: dict[str, Any] = {
        "ok": False,
        "http_status": None,
        "error_class": None,
        "error_short": None,
        "elapsed_ms": None,
        "wait_s": 0.0,
    }
    if not api_key:
        meta["error_class"] = "MissingApiKey"
        meta["error_short"] = "FMP_API_KEY not set"
        return None, meta

    # Honour the sticky pause first — never retry after 429 until it expires.
    paused, reason, remaining = _is_sticky_paused()
    if paused:
        meta["error_class"] = "RateLimitPaused"
        meta["error_short"] = f"{reason} (resumes in {remaining}s)"
        meta["http_status"] = 429
        return None, meta

    # Reserve a slot in the rolling-minute window (may sleep up to 5s).
    wait_s = _rate_limiter.acquire()
    meta["wait_s"] = round(wait_s, 3)
    if group:
        _bump_group(group)

    url = f"{FMP_BASE_URL}/{path}"
    call_params: dict[str, Any] = {} if params is None else dict(params)
    call_params["apikey"] = api_key

    safe_path = path
    safe_param_keys = sorted(k for k in call_params if k != "apikey")

    t0 = datetime.now()
    try:
        resp = requests.get(url, params=call_params, timeout=15)
    except requests.RequestException as e:
        meta["elapsed_ms"] = int((datetime.now() - t0).total_seconds() * 1000)
        meta["error_class"] = type(e).__name__
        meta["error_short"] = _redact(str(e))[:200]
        logger.warning("FMP %s [%s] failed: %s",
                       safe_path, safe_param_keys, meta["error_short"])
        return None, meta

    meta["elapsed_ms"] = int((datetime.now() - t0).total_seconds() * 1000)
    meta["http_status"] = resp.status_code

    # Manual handling — do NOT call resp.raise_for_status() (it leaks URL).
    if resp.status_code != 200:
        body_short = ""
        try:
            j = resp.json()
            if isinstance(j, dict):
                body_short = str(j.get("Error Message")
                                 or j.get("error") or "")[:200]
        except ValueError:
            body_short = (resp.text or "")[:200]
        meta["error_class"] = f"HTTP{resp.status_code}"
        meta["error_short"] = _redact(body_short)
        logger.warning("FMP %s HTTP %s: %s",
                       safe_path, resp.status_code, meta["error_short"])
        # Trip the sticky pause on rate-limit / quota signals.
        if resp.status_code in (429, 402):
            _set_sticky_pause(f"HTTP{resp.status_code}",
                              RATE_LIMIT_HARD_PAUSE_SECONDS)
        return None, meta

    try:
        data = resp.json()
    except ValueError:
        meta["error_class"] = "InvalidJSON"
        meta["error_short"] = "non-JSON response"
        logger.warning("FMP %s returned non-JSON body", safe_path)
        return None, meta

    # FMP error responses sometimes come as 200 + {"Error Message": ...}
    if isinstance(data, dict) and ("Error Message" in data or "error" in data):
        msg = str(data.get("Error Message") or data.get("error") or "")[:200]
        meta["error_class"] = "FMPError"
        meta["error_short"] = _redact(msg)
        logger.warning("FMP %s error in body: %s", safe_path, meta["error_short"])
        return None, meta

    meta["ok"] = True
    return data, meta


def _failure_payload(kind: str, ticker: str | None, meta: dict) -> dict:
    """Build a non-misleading failure dict for callers."""
    return {
        "status": DATA_UNAVAILABLE,
        "source": SOURCE_LIVE_FAILED,
        "ticker": ticker,
        "kind": kind,
        "error_class": meta.get("error_class"),
        "error_short": meta.get("error_short"),
        "http_status": meta.get("http_status"),
        "available_as_of": DATA_UNAVAILABLE,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# CONNECTION TEST
# ═══════════════════════════════════════════════════════════════

def test_connection() -> dict:
    api_key = _get_api_key()
    if not api_key:
        return {
            "connected": False,
            "provider": "fmp",
            "base_url": FMP_BASE_URL,
            "reason": "FMP_API_KEY not set",
            "fallback": "mock",
        }
    data, meta = _api_call("profile", {"symbol": "AAPL"}, group="connection")
    if meta["ok"] and isinstance(data, list) and data:
        return {
            "connected": True,
            "provider": "fmp",
            "base_url": FMP_BASE_URL,
            "test_ticker": "AAPL",
            "test_result": "success",
            "elapsed_ms": meta["elapsed_ms"],
            "fields_available": list(data[0].keys())[:30],
            "timestamp": _now_iso(),
        }
    return {
        "connected": False,
        "provider": "fmp",
        "base_url": FMP_BASE_URL,
        "reason": meta.get("error_short") or "empty/error response",
        "error_class": meta.get("error_class"),
        "http_status": meta.get("http_status"),
        "fallback": "mock",
    }


# ═══════════════════════════════════════════════════════════════
# TICKER / COMPANY SEARCH  (live; cached 12h per query)
# ═══════════════════════════════════════════════════════════════

CACHE_TTL_SEARCH = 12 * 3600  # 12 hours per (lower-cased) query

# US-equity exchange whitelist used for filtering search results.
US_EQUITY_EXCHANGES = {
    # Common identifiers FMP uses across symbol and name endpoints.
    "NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM",
    "NYSE", "NYQ",
    "AMEX", "ASE", "NYSEAMERICAN", "NYSE AMERICAN",
    "BATS", "CBOE",
}

_NON_EQUITY_TYPES = {"crypto", "forex", "fund", "etf", "index", "commodity"}

# When FMP /stable returns no `type`, the company name often gives it away.
# These regex tokens are a best-effort second line of defence so we don't
# pollute the search dropdown with ETFs/mutual funds/leveraged ETFs.
_NAME_NON_EQUITY = re.compile(
    r"\b("
    r"ETF|"
    r"ETN|"
    r"Trust|"
    r"Fund|"
    r"Fd|"
    r"Income\s+Strategy|"
    r"Daily\s+Target|"
    r"Bull\s+\dX|"
    r"Bear\s+\dX|"
    r"\dX\s+(Long|Short|Inverse|Bull|Bear)|"
    r"(Long|Short|Inverse|Bull|Bear)\s+\dX|"
    r"Leveraged|"
    r"Note(s)?|"
    r"Preferred|"
    r"Warrants?"
    r")\b",
    re.IGNORECASE,
)


def _row_is_us_equity(row: dict) -> bool:
    """Best-effort filter — keep rows that look like US-listed common stock.
    Reject crypto / forex / ETFs / mutual funds when we have enough metadata
    to tell. When in doubt, keep the row but the dashboard will tag it
    `uncertain`."""
    if not isinstance(row, dict):
        return False
    sec_type = str(row.get("type") or row.get("securityType") or "").lower()
    if sec_type in _NON_EQUITY_TYPES:
        return False
    exch = str(row.get("exchange") or row.get("exchangeShortName") or "").upper()
    name = str(row.get("name") or row.get("companyName") or "")
    # Name-pattern second line of defence (FMP /stable often omits type).
    if name and _NAME_NON_EQUITY.search(name):
        return False
    if not exch:
        return True  # uncertain — let the caller label it
    return any(tag in exch for tag in US_EQUITY_EXCHANGES)


def _normalize_search_row(row: dict) -> dict | None:
    """Reduce one FMP search result to the dashboard schema. Returns None if
    the row is missing a usable symbol."""
    if not isinstance(row, dict):
        return None
    sym = (row.get("symbol") or row.get("ticker") or "").strip().upper()
    if not sym or not sym.isalnum() or len(sym) > 10:
        return None
    name = row.get("name") or row.get("companyName") or row.get("description") or ""
    exch = (row.get("exchange") or row.get("exchangeShortName")
            or row.get("exchangeFullName") or "")
    sec_type = (row.get("type") or row.get("securityType") or "")
    currency = row.get("currency") or ""
    return {
        "symbol": sym,
        "name": str(name)[:120],
        "exchange": str(exch),
        "type": str(sec_type).lower() if sec_type else "",
        "currency": str(currency).upper() if currency else "",
    }


def search_symbols(query: str, limit: int = 10, us_only: bool = True) -> dict:
    """Search FMP for tickers and company names.

    Strategy:
      - Query both `/stable/search-symbol` (matches by ticker) and
        `/stable/search-name` (matches by company name) so users can type
        either form. Results are merged and de-duplicated by symbol.
      - Optional US-equity filter via `_row_is_us_equity` (recommended).
      - Whole result is cached for `CACHE_TTL_SEARCH` per lower-cased query.

    Returns a dict shaped for the dashboard:
        {
          "query": str,
          "results": [ {symbol, name, exchange, type, currency, us_listed}, ... ],
          "count": int,
          "source": "live_fmp" | "live_fmp_failed",
          "errors": [...],
          "cache_ttl_seconds": int,
          "served_from_cache": bool,
        }
    """
    q = (query or "").strip()
    if len(q) < 2:
        return {
            "query": q,
            "results": [],
            "count": 0,
            "source": SOURCE_LIVE_FAILED,
            "errors": [{"reason": "query too short"}],
            "cache_ttl_seconds": CACHE_TTL_SEARCH,
            "served_from_cache": False,
        }

    cache_key = ("search", q.lower(), int(bool(us_only)), int(limit))
    cached = _cache.get(cache_key, CACHE_TTL_SEARCH)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    errors: list[dict] = []
    rows: list[dict] = []

    sym_data, sym_meta = _api_call(
        "search-symbol", {"query": q, "limit": max(limit * 2, 20)},
        group="search",
    )
    if sym_meta.get("ok") and isinstance(sym_data, list):
        rows.extend(sym_data)
    else:
        errors.append({
            "endpoint": "search-symbol",
            "error_class": sym_meta.get("error_class"),
            "error_short": sym_meta.get("error_short"),
            "http_status": sym_meta.get("http_status"),
        })

    name_data, name_meta = _api_call(
        "search-name", {"query": q, "limit": max(limit * 2, 20)},
        group="search",
    )
    if name_meta.get("ok") and isinstance(name_data, list):
        rows.extend(name_data)
    else:
        errors.append({
            "endpoint": "search-name",
            "error_class": name_meta.get("error_class"),
            "error_short": name_meta.get("error_short"),
            "http_status": name_meta.get("http_status"),
        })

    seen: set[str] = set()
    normalized: list[dict] = []
    for r in rows:
        nr = _normalize_search_row(r)
        if nr is None or nr["symbol"] in seen:
            continue
        seen.add(nr["symbol"])
        nr["us_listed"] = _row_is_us_equity(r)
        if us_only and not nr["us_listed"]:
            continue
        normalized.append(nr)
        if len(normalized) >= limit:
            break

    # If we got nothing usable AND both endpoints errored, surface a live
    # failure so the UI shows "FMP search failed" — never silently return [].
    if not normalized and errors and len(errors) >= 2:
        # Don't cache outright failures — the next attempt may succeed once
        # the operator clears state or the rate-limit pause expires.
        return {
            "query": q,
            "results": [],
            "count": 0,
            "source": SOURCE_LIVE_FAILED,
            "errors": errors,
            "cache_ttl_seconds": CACHE_TTL_SEARCH,
            "served_from_cache": False,
        }

    payload = {
        "query": q,
        "results": normalized,
        "count": len(normalized),
        "source": SOURCE_LIVE,
        "errors": errors,
        "cache_ttl_seconds": CACHE_TTL_SEARCH,
        "served_from_cache": False,
    }
    _cache.set(cache_key, payload)
    return payload


# ═══════════════════════════════════════════════════════════════
# TOP GAINERS  (live current-day; not point-in-time-safe)
# ═══════════════════════════════════════════════════════════════

def get_top_gainers_live(max_count: int = 5) -> list[dict]:
    data, meta = _api_call("biggest-gainers", group="gainers")
    if not meta["ok"] or not isinstance(data, list):
        return []
    now = _now_iso()
    results: list[dict] = []
    for item in data[:max_count]:
        price = item.get("price") or 0
        change = item.get("change") or 0
        results.append({
            "ticker": item.get("symbol", ""),
            "name": item.get("name", ""),
            "close": price,
            "change": change,
            "change_pct": item.get("changesPercentage", 0),
            "prior_close": (price - change) if isinstance(price, (int, float))
                            and isinstance(change, (int, float)) else 0,
            "volume": 0,  # not in this endpoint
            "market_cap": 0,
            "exchange": item.get("exchange", DATA_UNAVAILABLE),
            "security_type": "",
            "source": SOURCE_LIVE,
            "live_only": True,  # current-only; no historical equivalent on free
            "timestamp": now,
            "available_as_of": now,
        })
    return results


# ═══════════════════════════════════════════════════════════════
# QUOTE  (live; for dashboard display)
# ═══════════════════════════════════════════════════════════════

def get_quote(ticker: str) -> dict:
    cache_key = ("quote", ticker)
    cached = _cache.get(cache_key, CACHE_TTL_QUOTE)
    if cached is not None:
        return {**cached, "served_from_cache": True}
    data, meta = _api_call("quote", {"symbol": ticker}, group="quote")
    if not meta["ok"] or not isinstance(data, list) or not data:
        return _failure_payload("quote", ticker, meta)
    q = data[0]
    ts_unix = q.get("timestamp")
    ts_iso = (datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat()
              if isinstance(ts_unix, (int, float)) else _now_iso())
    payload = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": q.get("symbol", ticker),
        "name": q.get("name", DATA_UNAVAILABLE),
        "price": q.get("price"),
        "change": q.get("change"),
        "change_pct": q.get("changePercentage"),
        "volume": q.get("volume"),
        "day_low": q.get("dayLow"),
        "day_high": q.get("dayHigh"),
        "year_low": q.get("yearLow"),
        "year_high": q.get("yearHigh"),
        "market_cap": q.get("marketCap"),
        "price_avg_50d": q.get("priceAvg50"),
        "price_avg_200d": q.get("priceAvg200"),
        "open": q.get("open"),
        "previous_close": q.get("previousClose"),
        "exchange": q.get("exchange"),
        "timestamp_unix": ts_unix,
        "timestamp": ts_iso,
        "available_as_of": ts_iso,
        "served_from_cache": False,
    }
    _cache.set(cache_key, payload)
    return payload


# ═══════════════════════════════════════════════════════════════
# NEWS  (live; cached 5 min — same TTL bucket as quote)
# ═══════════════════════════════════════════════════════════════

def get_news_latest(ticker: str, limit: int = 20) -> dict:
    """Fetch the latest stock-news rows for `ticker` from FMP
    `/stable/news/stock-latest`. Cached for `CACHE_TTL_NEWS` seconds (5 min)
    in the same process-local TTL store as `get_quote`.

    Returns a dict envelope (NOT the raw FMP list) so callers can
    distinguish a real failure from an empty list:

        {
            "status":            "available" | "Data unavailable",
            "source":            "live_fmp" | "live_fmp_failed",
            "ticker":            ticker,
            "items":             [ /* raw FMP rows */ ],
            "row_count":         int,
            "available_as_of":   ISO-8601,
            "served_from_cache": bool,
            "http_status":       int | None,
            "error_short":       str | None,
        }

    The raw rows are passed through verbatim (with FMP's own field names
    like `publishedDate`, `publisher`, `title`, `text`, `url`, `site`,
    `symbol`). Block builders are responsible for any per-row PIT filter.
    """
    # Cache key includes `limit` because two different limits would have
    # legitimately different payloads.
    cache_key = ("news_latest", ticker, limit)
    cached = _cache.get(cache_key, CACHE_TTL_NEWS)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    data, meta = _api_call(
        "news/stock-latest",
        {"symbols": ticker, "limit": limit},
        group="news",
    )
    if not meta["ok"]:
        return {
            "status": DATA_UNAVAILABLE,
            "source": SOURCE_LIVE_FAILED,
            "ticker": ticker,
            "items": [],
            "row_count": 0,
            "available_as_of": DATA_UNAVAILABLE,
            "served_from_cache": False,
            "http_status": meta.get("http_status"),
            "error_short": meta.get("error_short"),
        }

    rows = data if isinstance(data, list) else []
    payload = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": ticker,
        "items": rows,
        "row_count": len(rows),
        "available_as_of": (rows[0].get("publishedDate")
                              if rows and isinstance(rows[0], dict) else _now_iso()),
        "served_from_cache": False,
        "http_status": meta.get("http_status"),
        "error_short": None,
    }
    _cache.set(cache_key, payload)
    return payload


# ═══════════════════════════════════════════════════════════════
# COMPANY PROFILE
# ═══════════════════════════════════════════════════════════════

def get_company_profile(ticker: str) -> dict:
    cache_key = ("profile", ticker)
    cached = _cache.get(cache_key, CACHE_TTL_PROFILE)
    if cached is not None:
        return {**cached, "served_from_cache": True}
    data, meta = _api_call("profile", {"symbol": ticker}, group="profile")
    if not meta["ok"] or not isinstance(data, list) or not data:
        return _failure_payload("profile", ticker, meta)
    p = data[0]
    payload = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": p.get("symbol", ticker),
        "name": p.get("companyName", ""),
        "sector": p.get("sector", DATA_UNAVAILABLE),
        "industry": p.get("industry", DATA_UNAVAILABLE),
        "description": p.get("description", DATA_UNAVAILABLE),
        "market_cap": p.get("marketCap", 0),
        "country": p.get("country", DATA_UNAVAILABLE),
        "exchange": p.get("exchange", DATA_UNAVAILABLE),
        "exchange_full_name": p.get("exchangeFullName", DATA_UNAVAILABLE),
        "is_etf": bool(p.get("isEtf", False)),
        "is_actively_trading": bool(p.get("isActivelyTrading", True)),
        "is_adr": bool(p.get("isAdr", False)),
        "is_fund": bool(p.get("isFund", False)),
        # §27.18 (v0.8.7 UST refactor 2026-05-02): surface ETF expense
        # ratio when FMP returns it. None when missing — caller defaults
        # to 0 + data_quality_flag per spec.
        "expense_ratio": p.get("expenseRatio") if p.get("expenseRatio") is not None else None,
        "ipo_date": p.get("ipoDate", DATA_UNAVAILABLE),
        "ceo": p.get("ceo", DATA_UNAVAILABLE),
        "full_time_employees": p.get("fullTimeEmployees", DATA_UNAVAILABLE),
        "website": p.get("website", DATA_UNAVAILABLE),
        "currency": p.get("currency", "USD"),
        "cik": p.get("cik", DATA_UNAVAILABLE),
        "isin": p.get("isin", DATA_UNAVAILABLE),
        "image": p.get("image", DATA_UNAVAILABLE),
        "available_as_of": _now_iso(),
        "served_from_cache": False,
    }
    _cache.set(cache_key, payload)
    return payload


# ═══════════════════════════════════════════════════════════════
# HISTORICAL OHLCV  (bounded by from/to)
# ═══════════════════════════════════════════════════════════════

# ── Historical-OHLC disk cache ──
# Per-ticker JSON file at data/cache/historical_prices/<ticker>.json,
# content is dict[range_key, rows] where range_key = f"{start}_{end}".
# No TTL — historical daily OHLC is immutable. Atomic writes via
# tempfile + os.replace, mirroring src/llm/cache.py. Per-ticker lock so
# concurrent same-ticker calls do not lose entries; different tickers
# proceed without contention.
_HISTORICAL_PRICES_CACHE_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "cache" / "historical_prices"
)
_historical_cache_locks: dict[str, threading.Lock] = {}
_historical_cache_locks_lock = threading.Lock()
_historical_cache_stats = {"hits": 0, "misses": 0}
_historical_cache_stats_lock = threading.Lock()


def _historical_lock(ticker: str) -> threading.Lock:
    with _historical_cache_locks_lock:
        lk = _historical_cache_locks.get(ticker)
        if lk is None:
            lk = threading.Lock()
            _historical_cache_locks[ticker] = lk
        return lk


def _historical_cache_path(ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace("\\", "_")
    return _HISTORICAL_PRICES_CACHE_ROOT / f"{safe}.json"


def _historical_cache_get(
    ticker: str, start_date: str, end_date: str
) -> list[dict] | None:
    path = _historical_cache_path(ticker)
    if not path.exists():
        with _historical_cache_stats_lock:
            _historical_cache_stats["misses"] += 1
        return None
    range_key = f"{start_date}_{end_date}"
    with _historical_lock(ticker):
        try:
            with path.open("r", encoding="utf-8") as f:
                blob = json.load(f)
        except (OSError, json.JSONDecodeError):
            with _historical_cache_stats_lock:
                _historical_cache_stats["misses"] += 1
            return None
    if not isinstance(blob, dict):
        with _historical_cache_stats_lock:
            _historical_cache_stats["misses"] += 1
        return None
    rows = blob.get(range_key)
    if isinstance(rows, list):
        with _historical_cache_stats_lock:
            _historical_cache_stats["hits"] += 1
        return rows
    with _historical_cache_stats_lock:
        _historical_cache_stats["misses"] += 1
    return None


def _historical_cache_put(
    ticker: str, start_date: str, end_date: str, rows: list[dict]
) -> None:
    if not rows:
        return  # do not cache empty — let next attempt retry
    path = _historical_cache_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    range_key = f"{start_date}_{end_date}"
    with _historical_lock(ticker):
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, dict):
                    existing = {}
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing[range_key] = rows
        fd, tmp_path = tempfile.mkstemp(
            prefix=".tmp_", suffix=".json", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def get_historical_cache_stats() -> dict:
    """Process-local hit/miss counters for the on-disk historical-OHLC
    cache. Counters reset on import; reset() not exposed."""
    with _historical_cache_stats_lock:
        return dict(_historical_cache_stats)


def get_historical_daily(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = DEFAULT_HISTORICAL_DAYS,
) -> list[dict]:
    """
    Fetch daily OHLCV for [start_date, end_date].
    If both bounds are None, defaults to the last `days` calendar days
    ending today. /stable/historical-price-eod/full ignores 'limit',
    so bounds are mandatory to keep responses small.

    Disk cache: data/cache/historical_prices/<ticker>.json keyed by
    f"{start}_{end}". No TTL (historical OHLC is immutable). Cache hit
    bypasses the rate limiter and HTTP call.
    """
    if not end_date:
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
    if not start_date:
        start_dt = datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days)
        start_date = start_dt.strftime("%Y-%m-%d")

    cached = _historical_cache_get(ticker, start_date, end_date)
    if cached is not None:
        return cached

    params = {"symbol": ticker, "from": start_date, "to": end_date}
    data, meta = _api_call("historical-price-eod/full", params,
                            group="historical_eod")
    if not meta["ok"]:
        return []

    rows = data if isinstance(data, list) else data.get("historical", [])
    results: list[dict] = []
    for d in rows:
        date_str = d.get("date", "")
        results.append({
            "ticker": ticker,
            "date": date_str,
            "open": d.get("open", 0),
            "high": d.get("high", 0),
            "low": d.get("low", 0),
            "close": d.get("close", 0),
            "adj_close": d.get("adjClose", d.get("close", 0)),
            "volume": d.get("volume", 0),
            "vwap": d.get("vwap"),
            "change_pct": d.get("changePercent", 0),
            "source": SOURCE_LIVE,
            "timestamp": f"{date_str}T16:00:00-05:00" if date_str else "",
            "available_as_of": f"{date_str}T16:15:00-05:00" if date_str else "",
        })
    results.sort(key=lambda r: r["date"])

    if results:
        _historical_cache_put(ticker, start_date, end_date, results)

    return results


# ═══════════════════════════════════════════════════════════════
# FINANCIAL STATEMENTS (carry filingDate + acceptedDate — PIT-safe)
# ═══════════════════════════════════════════════════════════════

def _normalize_statement_row(item: dict) -> dict:
    """Common date fields across income / balance / cash-flow."""
    return {
        "date": item.get("date", ""),
        "fiscal_period_end": item.get("date", ""),
        "fiscal_year": item.get("fiscalYear", DATA_UNAVAILABLE),
        "period": item.get("period", DATA_UNAVAILABLE),
        "filing_date": item.get("filingDate", DATA_UNAVAILABLE),
        "accepted_date": item.get("acceptedDate", DATA_UNAVAILABLE),
        "reported_currency": item.get("reportedCurrency", "USD"),
        "cik": item.get("cik", DATA_UNAVAILABLE),
        "source": SOURCE_LIVE,
    }


def get_income_statement(ticker: str, period: str = "quarter",
                          limit: int = 4) -> list[dict]:
    data, meta = _api_call("income-statement",
                            {"symbol": ticker, "period": period, "limit": limit},
                            group="statements")
    if not meta["ok"] or not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        row = _normalize_statement_row(item)
        row.update({
            "revenue": item.get("revenue", 0),
            "cost_of_revenue": item.get("costOfRevenue", 0),
            "gross_profit": item.get("grossProfit", 0),
            "operating_income": item.get("operatingIncome", 0),
            "ebit": item.get("ebit", 0),
            "ebitda": item.get("ebitda", 0),
            "net_income": item.get("netIncome", 0),
            "eps": item.get("eps", 0),
            "eps_diluted": item.get("epsDiluted", item.get("epsdiluted", 0)),
            "rd_expense": item.get("researchAndDevelopmentExpenses", 0),
            "sga_expense": item.get("sellingGeneralAndAdministrativeExpenses", 0),
        })
        out.append(row)
    return out


def get_balance_sheet(ticker: str, period: str = "quarter",
                      limit: int = 4) -> list[dict]:
    data, meta = _api_call("balance-sheet-statement",
                            {"symbol": ticker, "period": period, "limit": limit},
                            group="statements")
    if not meta["ok"] or not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        row = _normalize_statement_row(item)
        row.update({
            "total_assets": item.get("totalAssets", 0),
            "total_liabilities": item.get("totalLiabilities", 0),
            "total_equity": item.get("totalStockholdersEquity",
                                    item.get("totalEquity", 0)),
            "cash_and_equivalents": item.get("cashAndCashEquivalents", 0),
            "short_term_debt": item.get("shortTermDebt", 0),
            "long_term_debt": item.get("longTermDebt", 0),
            "total_debt": item.get("totalDebt", 0),
            "total_current_assets": item.get("totalCurrentAssets", 0),
            "total_current_liabilities": item.get("totalCurrentLiabilities", 0),
        })
        out.append(row)
    return out


def get_cash_flow_statement(ticker: str, period: str = "quarter",
                             limit: int = 4) -> list[dict]:
    data, meta = _api_call("cash-flow-statement",
                            {"symbol": ticker, "period": period, "limit": limit},
                            group="statements")
    if not meta["ok"] or not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        row = _normalize_statement_row(item)
        row.update({
            "operating_cash_flow":
                item.get("netCashProvidedByOperatingActivities", 0),
            "capital_expenditure":
                item.get("investmentsInPropertyPlantAndEquipment",
                         item.get("capitalExpenditure", 0)),
            "free_cash_flow": item.get("freeCashFlow", 0),
            "stock_based_compensation": item.get("stockBasedCompensation", 0),
        })
        out.append(row)
    return out


# ═══════════════════════════════════════════════════════════════
# RATIOS & KEY METRICS
# ═══════════════════════════════════════════════════════════════

def _safe_pct(value) -> float:
    if value is None:
        return 0.0
    try:
        return round(float(value) * 100, 2)
    except (ValueError, TypeError):
        return 0.0


def get_financial_ratios(ticker: str, period: str = "quarter",
                          limit: int = 4) -> list[dict]:
    data, meta = _api_call("ratios",
                            {"symbol": ticker, "period": period, "limit": limit},
                            group="ratios")
    if not meta["ok"] or not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        out.append({
            "period": item.get("period", DATA_UNAVAILABLE),
            "fiscal_year": item.get("fiscalYear", DATA_UNAVAILABLE),
            "date": item.get("date", DATA_UNAVAILABLE),
            "pe_ratio": item.get("priceToEarningsRatio",
                                  item.get("priceEarningsRatio")),
            "price_to_sales": item.get("priceToSalesRatio"),
            "price_to_book": item.get("priceToBookRatio"),
            "debt_to_equity": item.get("debtToEquityRatio",
                                        item.get("debtEquityRatio")),
            "current_ratio": item.get("currentRatio"),
            "roe": item.get("returnOnEquity"),
            "roa": item.get("returnOnAssets"),
            "gross_margin_pct": _safe_pct(item.get("grossProfitMargin")),
            "operating_margin_pct": _safe_pct(item.get("operatingProfitMargin")),
            "net_margin_pct": _safe_pct(item.get("netProfitMargin")),
            "peg_ratio": item.get("priceToEarningsGrowthRatio",
                                   item.get("priceEarningsToGrowthRatio")),
            "dividend_yield": item.get("dividendYield"),
            "source": SOURCE_LIVE,
        })
    return out


def get_key_metrics(ticker: str) -> dict:
    """Returns TTM key metrics. NOTE: TTM endpoint has no fiscal anchor —
    not safe for point-in-time backtest. Live mode only."""
    data, meta = _api_call("key-metrics-ttm", {"symbol": ticker},
                            group="metrics")
    if not meta["ok"] or not isinstance(data, list) or not data:
        return _failure_payload("key_metrics_ttm", ticker, meta)
    m = data[0]
    return {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": m.get("symbol", ticker),
        "market_cap": m.get("marketCap", 0),
        "enterprise_value": m.get("enterpriseValueTTM"),
        "ev_to_sales": m.get("evToSalesTTM"),
        "ev_to_ebitda": m.get("evToEBITDATTM"),
        "ev_to_fcf": m.get("evToFreeCashFlowTTM"),
        "net_debt_to_ebitda": m.get("netDebtToEBITDATTM"),
        "current_ratio": m.get("currentRatioTTM"),
        "roe": m.get("returnOnEquityTTM"),
        "roa": m.get("returnOnAssetsTTM"),
        "roic": m.get("returnOnInvestedCapitalTTM"),
        "earnings_yield": m.get("earningsYieldTTM"),
        "fcf_yield": m.get("freeCashFlowYieldTTM"),
        "live_only": True,
        "available_as_of": _now_iso(),
    }


def get_ratios_ttm(ticker: str) -> dict:
    """TTM ratios. Live-only (no fiscal anchor)."""
    data, meta = _api_call("ratios-ttm", {"symbol": ticker},
                            group="ratios_ttm")
    if not meta["ok"] or not isinstance(data, list) or not data:
        return _failure_payload("ratios_ttm", ticker, meta)
    r = data[0]
    return {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": r.get("symbol", ticker),
        "pe_ratio": r.get("priceToEarningsRatioTTM"),
        "peg_ratio": r.get("priceToEarningsGrowthRatioTTM"),
        "price_to_book": r.get("priceToBookRatioTTM"),
        "price_to_sales": r.get("priceToSalesRatioTTM"),
        "price_to_fcf": r.get("priceToFreeCashFlowRatioTTM"),
        "debt_to_equity": r.get("debtToEquityRatioTTM"),
        "current_ratio": r.get("currentRatioTTM"),
        "quick_ratio": r.get("quickRatioTTM"),
        "gross_margin_pct": _safe_pct(r.get("grossProfitMarginTTM")),
        "operating_margin_pct": _safe_pct(r.get("operatingProfitMarginTTM")),
        "net_margin_pct": _safe_pct(r.get("netProfitMarginTTM")),
        "ebitda_margin_pct": _safe_pct(r.get("ebitdaMarginTTM")),
        "live_only": True,
        "available_as_of": _now_iso(),
    }


# ═══════════════════════════════════════════════════════════════
# COMPOSED FUNDAMENTALS  (used by get_fundamentals_for_scoring)
# ═══════════════════════════════════════════════════════════════

def get_fundamentals_for_scoring(ticker: str) -> dict:
    """
    Compose profile + key-metrics + quarterly ratios + recent income statements
    into a dict matching the existing fundamentals contract.

    On any live failure, this returns {} so the caller can fall back to mock
    explicitly. We do NOT manufacture a partial dict that would look successful.
    """
    profile = get_company_profile(ticker)
    if profile.get("status") == DATA_UNAVAILABLE:
        return {}  # caller falls back to mock

    metrics = get_key_metrics(ticker)        # EV multiples, ROE/ROA TTM
    ratios_ttm = get_ratios_ttm(ticker)      # PE / PB / PS / margins TTM
    ratios_list = get_financial_ratios(ticker, period="quarter", limit=2)
    income_list = get_income_statement(ticker, period="quarter", limit=4)
    cashflow_list = get_cash_flow_statement(ticker, period="quarter", limit=4)

    revenue_growth: float | str = DATA_UNAVAILABLE
    if len(income_list) >= 2:
        curr_rev = income_list[0].get("revenue", 0) or 0
        prev_rev = income_list[1].get("revenue", 0) or 0
        if prev_rev > 0:
            revenue_growth = round((curr_rev - prev_rev) / prev_rev * 100, 1)

    latest_ratios = ratios_list[0] if ratios_list else {}
    latest_income = income_list[0] if income_list else {}

    revenue_ttm = sum((i.get("revenue", 0) or 0) for i in income_list[:4])
    net_income_ttm = sum((i.get("net_income", 0) or 0) for i in income_list[:4])
    rd_expense_ttm = sum((i.get("rd_expense", 0) or 0) for i in income_list[:4])
    fcf_ttm = sum((c.get("free_cash_flow", 0) or 0) for c in cashflow_list[:4])

    filing_date = latest_income.get("filing_date", DATA_UNAVAILABLE)
    fiscal_period_end = latest_income.get("fiscal_period_end", DATA_UNAVAILABLE)
    accepted_date = latest_income.get("accepted_date", DATA_UNAVAILABLE)

    return {
        "ticker": ticker,
        "name": profile.get("name", ticker),
        "sector": profile.get("sector", DATA_UNAVAILABLE),
        "industry": profile.get("industry", DATA_UNAVAILABLE),
        "market_cap": profile.get("market_cap")
                      or (metrics.get("market_cap", 0) if isinstance(metrics, dict) else 0),
        "is_etf": profile.get("is_etf", False),

        # Revenue & Growth
        "revenue_ttm": revenue_ttm,
        "revenue_growth_pct": revenue_growth
            if revenue_growth != DATA_UNAVAILABLE else 0,

        # Margins (TTM preferred; quarterly ratios as fallback)
        "gross_margin_pct": (ratios_ttm.get("gross_margin_pct")
                              if ratios_ttm.get("status") == "available"
                              else latest_ratios.get("gross_margin_pct", 0)),
        "operating_margin_pct": (ratios_ttm.get("operating_margin_pct")
                                  if ratios_ttm.get("status") == "available"
                                  else latest_ratios.get("operating_margin_pct", 0)),

        # Profitability
        "net_income_ttm": net_income_ttm,
        "free_cash_flow_ttm": fcf_ttm,

        # Debt & Risk
        "debt_to_equity": (ratios_ttm.get("debt_to_equity")
                            if ratios_ttm.get("status") == "available"
                            else latest_ratios.get("debt_to_equity", 0)),
        "going_concern": False,
        "dilution_risk": "unknown",

        # Valuation (TTM ratios)
        "pe_ratio": ratios_ttm.get("pe_ratio"),
        "forward_pe": None,
        "peg_ratio": ratios_ttm.get("peg_ratio"),
        "price_to_fcf": ratios_ttm.get("price_to_fcf"),
        "price_to_sales": ratios_ttm.get("price_to_sales"),
        "price_to_book": ratios_ttm.get("price_to_book"),
        "ev_to_ebitda": metrics.get("ev_to_ebitda"),
        "drawdown_from_ath_pct": 0,

        # R&D
        "rd_expense_ttm": rd_expense_ttm,

        # Point-in-time stamps
        "filing_date": filing_date,
        "fiscal_period_end": fiscal_period_end,
        "accepted_date": accepted_date,

        # Source flags
        "source": SOURCE_LIVE,
        "data_source": "fmp",
        "available_as_of": _now_iso(),
    }


# ═══════════════════════════════════════════════════════════════
# INTRADAY CHART  (Premium)
# ═══════════════════════════════════════════════════════════════

VALID_INTRADAY_INTERVALS = ("1min", "5min", "15min", "30min", "1hour")


def get_intraday_chart(ticker: str, interval: str = "5min",
                       max_rows: int = 80) -> dict:
    """Recent intraday OHLCV bars for `ticker`. Live-only (no PIT anchor).

    Cached for CACHE_TTL_INTRADAY seconds. The free/premium /stable
    endpoint returns the latest few trading days' worth of bars; we
    truncate to the last `max_rows` bars for the dashboard chart.
    """
    interval = interval if interval in VALID_INTRADAY_INTERVALS else "5min"
    cache_key = ("intraday", ticker, interval, max_rows)
    cached = _cache.get(cache_key, CACHE_TTL_INTRADAY)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    data, meta = _api_call(f"historical-chart/{interval}",
                           {"symbol": ticker}, group="intraday")
    if not meta["ok"] or not isinstance(data, list):
        return {
            "status": DATA_UNAVAILABLE,
            "source": SOURCE_LIVE_FAILED,
            "ticker": ticker, "interval": interval,
            "error_class": meta.get("error_class"),
            "http_status": meta.get("http_status"),
            "error_short": meta.get("error_short"),
        }
    # API returns newest-first; clip and reverse so caller gets ascending order.
    rows = list(reversed(data[:max_rows]))
    bars = [
        {
            "datetime": r.get("date", ""),
            "open": r.get("open"),
            "high": r.get("high"),
            "low":  r.get("low"),
            "close": r.get("close"),
            "volume": r.get("volume"),
        }
        for r in rows
    ]
    payload = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": ticker,
        "interval": interval,
        "bars": bars,
        "row_count": len(bars),
        "first_datetime": bars[0]["datetime"] if bars else None,
        "last_datetime": bars[-1]["datetime"] if bars else None,
        "available_as_of": _now_iso(),
        "live_only": True,
        "served_from_cache": False,
    }
    _cache.set(cache_key, payload)
    return payload


def get_chart_intraday(ticker: str, interval: str = "5min") -> dict:
    """Wide intraday window for the dashboard's multi-timeframe chart.

    Cache key intentionally OMITS any window-size param: the frontend
    slices to 1D / 3D / 1W from the same cached payload, so switching
    among those timeframes never refires the API. TTL = 60 s dedupes
    rapid clicks without serving stale wall-clock data.
    """
    interval = interval if interval in VALID_INTRADAY_INTERVALS else "5min"
    cache_key = ("chart_intraday", ticker, interval)
    cached = _cache.get(cache_key, CACHE_TTL_CHART_INTRADAY)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    data, meta = _api_call(f"historical-chart/{interval}",
                           {"symbol": ticker}, group="chart_intraday")
    if not meta["ok"] or not isinstance(data, list):
        return {
            "status": DATA_UNAVAILABLE,
            "source": SOURCE_LIVE_FAILED,
            "ticker": ticker, "interval": interval,
            "error_class": meta.get("error_class"),
            "http_status": meta.get("http_status"),
            "error_short": meta.get("error_short"),
        }
    rows = list(reversed(data))
    bars = [
        {
            "datetime": r.get("date", ""),
            "open": r.get("open"),
            "high": r.get("high"),
            "low":  r.get("low"),
            "close": r.get("close"),
            "volume": r.get("volume"),
        }
        for r in rows
    ]
    payload = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": ticker,
        "interval": interval,
        "bars": bars,
        "row_count": len(bars),
        "first_datetime": bars[0]["datetime"] if bars else None,
        "last_datetime": bars[-1]["datetime"] if bars else None,
        "available_as_of": _now_iso(),
        "live_only": True,
        "served_from_cache": False,
    }
    _cache.set(cache_key, payload)
    return payload


def get_chart_daily(ticker: str, years_back: int = 25) -> dict:
    """Full daily EOD history for the dashboard's multi-timeframe chart.

    Cache key intentionally OMITS the date range: the frontend slices to
    1M / 3M / 1Y / 5Y / ALL from the same cached payload, so switching
    among those timeframes never refires the API. TTL = 12 h matches the
    update cadence of EOD bars.
    """
    cache_key = ("chart_daily", ticker)
    cached = _cache.get(cache_key, CACHE_TTL_CHART_DAILY)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    end_date = datetime.utcnow().strftime("%Y-%m-%d")
    start_date = (datetime.utcnow() - timedelta(days=365 * years_back))\
        .strftime("%Y-%m-%d")
    params = {"symbol": ticker, "from": start_date, "to": end_date}
    data, meta = _api_call("historical-price-eod/full", params,
                           group="chart_daily")
    if not meta["ok"]:
        return {
            "status": DATA_UNAVAILABLE,
            "source": SOURCE_LIVE_FAILED,
            "ticker": ticker,
            "error_class": meta.get("error_class"),
            "http_status": meta.get("http_status"),
            "error_short": meta.get("error_short"),
        }
    rows = data if isinstance(data, list) else data.get("historical", [])
    bars = [
        {
            "date": d.get("date", ""),
            "open": d.get("open"),
            "high": d.get("high"),
            "low":  d.get("low"),
            "close": d.get("close"),
            "volume": d.get("volume"),
        }
        for d in rows
    ]
    bars.sort(key=lambda r: r["date"])
    payload = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": ticker,
        "bars": bars,
        "row_count": len(bars),
        "first_date": bars[0]["date"] if bars else None,
        "last_date": bars[-1]["date"] if bars else None,
        "available_as_of": _now_iso(),
        "served_from_cache": False,
    }
    _cache.set(cache_key, payload)
    return payload


# ═══════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS  (Premium)
# ═══════════════════════════════════════════════════════════════

VALID_TECHNICAL_TYPES = ("sma", "ema", "wma", "dema", "tema", "rsi",
                          "standardDeviation", "williams", "adx")


def get_technical_indicator(ticker: str, indicator_type: str,
                              period_length: int = 14,
                              timeframe: str = "1day") -> dict:
    """Latest value of a single technical indicator. Live-only.

    Returns the most recent row (date, indicator value). Cached
    for CACHE_TTL_TECHNICAL seconds.
    """
    if indicator_type not in VALID_TECHNICAL_TYPES:
        return {"status": DATA_UNAVAILABLE, "source": SOURCE_LIVE_FAILED,
                "ticker": ticker, "indicator_type": indicator_type,
                "error_short": f"unsupported indicator '{indicator_type}'"}

    cache_key = ("technical", ticker, indicator_type, period_length, timeframe)
    cached = _cache.get(cache_key, CACHE_TTL_TECHNICAL)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    data, meta = _api_call(f"technical-indicators/{indicator_type}",
                           {"symbol": ticker,
                            "periodLength": period_length,
                            "timeframe": timeframe},
                           group="technicals")
    if not meta["ok"] or not isinstance(data, list) or not data:
        return {
            "status": DATA_UNAVAILABLE,
            "source": SOURCE_LIVE_FAILED,
            "ticker": ticker,
            "indicator_type": indicator_type,
            "period_length": period_length,
            "timeframe": timeframe,
            "error_class": meta.get("error_class"),
            "http_status": meta.get("http_status"),
            "error_short": meta.get("error_short"),
        }
    # /stable returns newest-first; we want the latest row.
    latest = data[0]
    value = latest.get(indicator_type)
    payload = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": ticker,
        "indicator_type": indicator_type,
        "period_length": period_length,
        "timeframe": timeframe,
        "value": value,
        "as_of_date": latest.get("date"),
        "close_at_that_date": latest.get("close"),
        "available_as_of": _now_iso(),
        "live_only": True,
        "served_from_cache": False,
    }
    _cache.set(cache_key, payload)
    return payload


# ═══════════════════════════════════════════════════════════════
# CORPORATE CALENDAR  (Premium)
# ═══════════════════════════════════════════════════════════════

def _split_past_upcoming(rows: list[dict], today_iso: str,
                         upcoming_n: int = 2,
                         past_n: int = 4) -> tuple[list[dict], list[dict]]:
    upcoming = [r for r in rows if r.get("date", "") >= today_iso]
    past = [r for r in rows if r.get("date", "") < today_iso]
    upcoming.sort(key=lambda r: r.get("date", ""))
    past.sort(key=lambda r: r.get("date", ""), reverse=True)
    return upcoming[:upcoming_n], past[:past_n]


def get_corporate_calendar_pit(
    ticker: str,
    decision_timestamp_utc: datetime,
    *,
    lookback_days: int = 90,
    lookahead_days: int = 90,
) -> dict:
    """PIT-aware corporate-calendar fetch (D5 Option C, Task C).

    Uses the cross-ticker `earnings-calendar` endpoint with from/to
    parameters, then filters by symbol and applies the PIT cutoff so
    historical backtests see real earnings events. The legacy
    `earnings` per-symbol endpoint returns nothing on the user's plan
    for the historical 2026-04 window (D4 diagnostic confirmed); the
    cross-ticker calendar with date range works at 4000+ rows.

    PIT discipline: every event with `date > cutoff_utc.date()` is
    dropped. Past events are kept (they are factual: an event that
    already happened is allowable evidence at decision time).

    Cache key includes the cutoff date so two backtest cells at
    different cutoffs do not collide.
    """
    cutoff_date = decision_timestamp_utc.astimezone(timezone.utc).date()
    cache_key = ("calendar_pit", ticker, cutoff_date.isoformat(),
                  lookback_days, lookahead_days)
    cached = _cache.get(cache_key, CACHE_TTL_CALENDAR)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    from_iso = (cutoff_date - timedelta(days=lookback_days)).isoformat()
    # `to` window covers a forward range so the runner can warn on
    # imminent events; PIT filter strips events with date > cutoff
    # before they reach the agent. The full lookahead window is kept
    # in `upcoming_pit_dropped` for forensics.
    to_iso = (cutoff_date + timedelta(days=lookahead_days)).isoformat()

    out: dict = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": ticker,
        "as_of": cutoff_date.isoformat(),
        "earnings": {"upcoming": [], "past": [], "source": SOURCE_LIVE_FAILED},
        "dividends": {"upcoming": [], "past": [], "source": SOURCE_LIVE_FAILED},
        "splits": {"upcoming": [], "past": [], "source": SOURCE_LIVE_FAILED},
        "ipo_date": None,
        "served_from_cache": False,
        "available_as_of": _now_iso(),
        "pit_dropped_post_cutoff": {"earnings": 0, "dividends": 0, "splits": 0},
        "endpoint": "earnings-calendar (cross-ticker, from/to)",
    }
    successes = 0

    # ── Earnings via per-symbol endpoint, PIT-filtered client-side ──
    # The cross-ticker `earnings-calendar` with from/to caps at 4000
    # rows alphabetically — reliable AAPL/NVDA/TSLA coverage requires
    # the per-symbol `earnings` endpoint (returns past + future for
    # the specific ticker, no plan limit, no row cap). This was the
    # endpoint the diagnostic-script labeled "empty" — a diagnostic-
    # script bug, not an adapter bug. We filter post-cutoff events
    # out client-side to enforce PIT discipline.
    e_data, e_meta = _api_call(
        "earnings", {"symbol": ticker, "limit": 20},
        group="calendar_pit_earnings",
    )
    if e_meta["ok"] and isinstance(e_data, list):
        kept_past: list[dict] = []
        dropped_post = 0
        for r in e_data:
            if not isinstance(r, dict):
                continue
            d = (r.get("date") or "")[:10]
            if not d:
                continue
            if d > cutoff_date.isoformat():
                dropped_post += 1
                continue
            kept_past.append({
                "date": d,
                "eps_actual": r.get("epsActual"),
                "eps_estimated": r.get("epsEstimated"),
                "revenue_actual": r.get("revenueActual"),
                "revenue_estimated": r.get("revenueEstimated"),
                "last_updated": r.get("lastUpdated"),
            })
        kept_past.sort(key=lambda x: x.get("date", ""), reverse=True)
        out["earnings"] = {
            "upcoming": [],                   # always [] under PIT
            "past": kept_past[:4],
            "source": SOURCE_LIVE,
            "rows_in_window": len(e_data),
            "rows_dropped_post_cutoff": dropped_post,
        }
        out["pit_dropped_post_cutoff"]["earnings"] = dropped_post
        successes += 1

    # ── Dividends via per-symbol endpoint (works on plan; PIT filter) ──
    d_data, d_meta = _api_call("dividends", {"symbol": ticker, "limit": 20},
                                group="calendar_pit_dividends")
    if d_meta["ok"] and isinstance(d_data, list):
        kept_past = []
        dropped_post = 0
        for r in d_data:
            if not isinstance(r, dict):
                continue
            d = (r.get("date") or "")[:10]
            if not d:
                continue
            if d > cutoff_date.isoformat():
                dropped_post += 1
                continue
            kept_past.append({
                "date": d, "dividend": r.get("dividend"),
                "yield": r.get("yield"), "record_date": r.get("recordDate"),
                "payment_date": r.get("paymentDate"),
            })
        kept_past.sort(key=lambda x: x.get("date", ""), reverse=True)
        out["dividends"] = {
            "upcoming": [], "past": kept_past[:4],
            "source": SOURCE_LIVE,
            "rows_in_window": len(d_data),
            "rows_dropped_post_cutoff": dropped_post,
        }
        out["pit_dropped_post_cutoff"]["dividends"] = dropped_post
        successes += 1

    # ── Splits via per-symbol endpoint (works on plan; PIT filter) ──
    s_data, s_meta = _api_call("splits", {"symbol": ticker, "limit": 10},
                                group="calendar_pit_splits")
    if s_meta["ok"] and isinstance(s_data, list):
        kept_past = []
        dropped_post = 0
        for r in s_data:
            if not isinstance(r, dict):
                continue
            d = (r.get("date") or "")[:10]
            if not d:
                continue
            if d > cutoff_date.isoformat():
                dropped_post += 1
                continue
            kept_past.append({
                "date": d,
                "ratio": f"{r.get('numerator')}:{r.get('denominator')}",
            })
        kept_past.sort(key=lambda x: x.get("date", ""), reverse=True)
        out["splits"] = {
            "upcoming": [], "past": kept_past[:3],
            "source": SOURCE_LIVE,
            "rows_in_window": len(s_data),
            "rows_dropped_post_cutoff": dropped_post,
        }
        out["pit_dropped_post_cutoff"]["splits"] = dropped_post
        successes += 1

    if successes == 0:
        out["status"] = DATA_UNAVAILABLE
        out["source"] = SOURCE_LIVE_FAILED

    _cache.set(cache_key, out)
    return out


def get_corporate_calendar(ticker: str) -> dict:
    """Combine earnings, dividends, splits into a compact panel.

    Cached for CACHE_TTL_CALENDAR seconds. Each sub-block is
    independent so a partial failure still yields useful data.
    """
    cache_key = ("calendar", ticker)
    cached = _cache.get(cache_key, CACHE_TTL_CALENDAR)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    today = datetime.utcnow().strftime("%Y-%m-%d")
    out: dict = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": ticker,
        "as_of": today,
        "earnings": {"upcoming": [], "past": [], "source": SOURCE_LIVE_FAILED},
        "dividends": {"upcoming": [], "past": [], "source": SOURCE_LIVE_FAILED},
        "splits": {"upcoming": [], "past": [], "source": SOURCE_LIVE_FAILED},
        "ipo_date": None,
        "served_from_cache": False,
        "available_as_of": _now_iso(),
    }
    successes = 0

    e_data, e_meta = _api_call("earnings", {"symbol": ticker, "limit": 8},
                                group="calendar_earnings")
    if e_meta["ok"] and isinstance(e_data, list):
        upcoming, past = _split_past_upcoming(e_data, today, 2, 4)
        out["earnings"] = {
            "upcoming": [{"date": r.get("date"),
                           "eps_estimated": r.get("epsEstimated"),
                           "revenue_estimated": r.get("revenueEstimated"),
                           "last_updated": r.get("lastUpdated")}
                          for r in upcoming],
            "past": [{"date": r.get("date"),
                       "eps_actual": r.get("epsActual"),
                       "eps_estimated": r.get("epsEstimated"),
                       "revenue_actual": r.get("revenueActual"),
                       "revenue_estimated": r.get("revenueEstimated"),
                       "last_updated": r.get("lastUpdated")}
                      for r in past],
            "source": SOURCE_LIVE,
        }
        successes += 1

    d_data, d_meta = _api_call("dividends", {"symbol": ticker, "limit": 5},
                                group="calendar_dividends")
    if d_meta["ok"] and isinstance(d_data, list):
        upcoming, past = _split_past_upcoming(d_data, today, 1, 4)
        out["dividends"] = {
            "upcoming": [{"date": r.get("date"),
                           "dividend": r.get("dividend"),
                           "yield": r.get("yield"),
                           "record_date": r.get("recordDate"),
                           "payment_date": r.get("paymentDate")}
                          for r in upcoming],
            "past": [{"date": r.get("date"),
                       "dividend": r.get("dividend"),
                       "yield": r.get("yield"),
                       "record_date": r.get("recordDate"),
                       "payment_date": r.get("paymentDate")}
                      for r in past],
            "source": SOURCE_LIVE,
        }
        successes += 1

    s_data, s_meta = _api_call("splits", {"symbol": ticker, "limit": 5},
                                group="calendar_splits")
    if s_meta["ok"] and isinstance(s_data, list):
        upcoming, past = _split_past_upcoming(s_data, today, 1, 3)
        out["splits"] = {
            "upcoming": [{"date": r.get("date"),
                           "ratio": f"{r.get('numerator')}:{r.get('denominator')}"}
                          for r in upcoming],
            "past": [{"date": r.get("date"),
                       "ratio": f"{r.get('numerator')}:{r.get('denominator')}"}
                      for r in past],
            "source": SOURCE_LIVE,
        }
        successes += 1

    if successes == 0:
        out["status"] = DATA_UNAVAILABLE
        out["source"] = SOURCE_LIVE_FAILED

    _cache.set(cache_key, out)
    return out


# ═══════════════════════════════════════════════════════════════
# DCF  (Premium)
# ═══════════════════════════════════════════════════════════════

def get_dcf_valuation(ticker: str) -> dict:
    """FMP-computed Discounted Cash Flow vs. current price.

    Display only. Cached for CACHE_TTL_DCF seconds. Not point-in-time
    safe — there is no fiscal anchor on the response.
    """
    cache_key = ("dcf", ticker)
    cached = _cache.get(cache_key, CACHE_TTL_DCF)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    data, meta = _api_call("discounted-cash-flow", {"symbol": ticker},
                            group="dcf")
    if not meta["ok"] or not isinstance(data, list) or not data:
        return {
            "status": DATA_UNAVAILABLE,
            "source": SOURCE_LIVE_FAILED,
            "ticker": ticker,
            "error_class": meta.get("error_class"),
            "http_status": meta.get("http_status"),
            "error_short": meta.get("error_short"),
        }
    row = data[0]
    dcf = row.get("dcf")
    price = row.get("Stock Price") or row.get("stockPrice") or row.get("price")
    upside_pct = None
    if isinstance(dcf, (int, float)) and isinstance(price, (int, float)) and price:
        upside_pct = round(((dcf - price) / price) * 100, 2)
    payload = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": ticker,
        "dcf": dcf,
        "current_price": price,
        "implied_upside_pct": upside_pct,
        "as_of_date": row.get("date"),
        "available_as_of": _now_iso(),
        "is_point_in_time_safe": False,  # explicit display flag
        "served_from_cache": False,
    }
    _cache.set(cache_key, payload)
    return payload


# ═══════════════════════════════════════════════════════════════
# COMPACT FUNDAMENTALS SNAPSHOT  (consolidated for the Inspector)
# ═══════════════════════════════════════════════════════════════

def get_fundamentals_snapshot(ticker: str) -> dict:
    """Light fundamentals + ratios payload for the dashboard inspector.

    PIT-safety labelling:
      - Latest *quarterly* income/balance/cashflow rows carry
        `filing_date` and `accepted_date` and are PIT-safe.
      - The `*_TTM` block has no fiscal anchor and is labelled
        `point_in_time_safe = False`.
    """
    cache_key = ("fundamentals_snapshot", ticker)
    cached = _cache.get(cache_key, CACHE_TTL_FUNDAMENTALS)
    if cached is not None:
        return {**cached, "served_from_cache": True}

    income = get_income_statement(ticker, period="quarter", limit=2)
    balance = get_balance_sheet(ticker, period="quarter", limit=1)
    cashflow = get_cash_flow_statement(ticker, period="quarter", limit=1)
    ratios_ttm = get_ratios_ttm(ticker)
    metrics_ttm = get_key_metrics(ticker)

    if not income and ratios_ttm.get("status") != "available":
        # Both core sources failed
        return {
            "status": DATA_UNAVAILABLE,
            "source": SOURCE_LIVE_FAILED,
            "ticker": ticker,
        }

    latest = income[0] if income else {}
    bal = balance[0] if balance else {}
    cf = cashflow[0] if cashflow else {}

    # Margin sanity (statement values are absolute dollars)
    rev = latest.get("revenue") or 0
    gross = latest.get("gross_profit") or 0
    net = latest.get("net_income") or 0
    gross_margin = round((gross / rev) * 100, 2) if rev else None
    net_margin = round((net / rev) * 100, 2) if rev else None

    snapshot_quarter = {
        "fiscal_period_end": latest.get("fiscal_period_end"),
        "fiscal_year": latest.get("fiscal_year"),
        "period": latest.get("period"),
        "filing_date": latest.get("filing_date"),
        "accepted_date": latest.get("accepted_date"),
        "revenue": rev,
        "net_income": net,
        "gross_margin_pct": gross_margin,
        "net_margin_pct": net_margin,
        "operating_income": latest.get("operating_income"),
        "ebitda": latest.get("ebitda"),
        "eps": latest.get("eps"),
        "operating_cash_flow": cf.get("operating_cash_flow"),
        "free_cash_flow": cf.get("free_cash_flow"),
        "total_assets": bal.get("total_assets"),
        "total_liabilities": bal.get("total_liabilities"),
        "total_equity": bal.get("total_equity"),
        "total_debt": bal.get("total_debt"),
        "current_ratio": (
            (bal.get("total_current_assets") /
             bal.get("total_current_liabilities"))
            if bal.get("total_current_liabilities") else None
        ),
        "debt_to_equity": (
            (bal.get("total_debt") / bal.get("total_equity"))
            if bal.get("total_equity") else None
        ),
        "source": SOURCE_LIVE if income else SOURCE_LIVE_FAILED,
        "is_point_in_time_safe": True,
        "pit_anchor": "filing_date / accepted_date",
    }

    snapshot_ttm = {
        "pe_ratio": ratios_ttm.get("pe_ratio"),
        "peg_ratio": ratios_ttm.get("peg_ratio"),
        "price_to_sales": ratios_ttm.get("price_to_sales"),
        "price_to_book": ratios_ttm.get("price_to_book"),
        "ev_to_ebitda": metrics_ttm.get("ev_to_ebitda"),
        "roe": metrics_ttm.get("roe"),
        "roa": metrics_ttm.get("roa"),
        "current_ratio_ttm_pit": ratios_ttm.get("current_ratio"),
        "debt_to_equity_ttm": ratios_ttm.get("debt_to_equity"),
        "gross_margin_pct_ttm": ratios_ttm.get("gross_margin_pct"),
        "net_margin_pct_ttm": ratios_ttm.get("net_margin_pct"),
        "source": ratios_ttm.get("source", SOURCE_LIVE_FAILED),
        "is_point_in_time_safe": False,
        "pit_note": "Current snapshot only / not point-in-time safe",
    }

    payload = {
        "status": "available",
        "source": SOURCE_LIVE,
        "ticker": ticker,
        "quarter": snapshot_quarter,
        "ttm": snapshot_ttm,
        "available_as_of": _now_iso(),
        "served_from_cache": False,
    }
    _cache.set(cache_key, payload)
    return payload


# ═══════════════════════════════════════════════════════════════
# HISTORICAL PIT UNIVERSE  (Pass 8 Step B1.6 — 2026-05-04)
# ═══════════════════════════════════════════════════════════════
#
# /stable/historical-sp500-constituent and /stable/historical-nasdaq-
# constituent return the changes log: each row says "on date D, ticker
# A was added (replacing B)". To reconstruct the index as of any past
# date, we start with TODAY's snapshot and reverse-replay every change
# whose `date` is later than the target decision_date.
#
# Reproducibility: both the changes log and today's snapshot are cached
# to disk with a query timestamp. Subsequent reads use the cached files
# (NOT a fresh API call) so a backtest produces the same universe every
# run. A new query writes a new file with a new timestamp; old caches are
# preserved for audit. historical_universe_as_of(decision_date) refuses
# to answer for dates AFTER the cache's query_timestamp (would be lookahead).
#
# Verified in Step B1.5: 2025-03-17 SP500 reconstructs to 504 names,
# differing from today's 503-name snapshot by 25 dropped + 24 added
# (real index events: COIN add, MTCH drop, etc.).

_UNIVERSE_CACHE_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "cache" / "universe"
)


def _universe_cache_dir() -> Path:
    _UNIVERSE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return _UNIVERSE_CACHE_ROOT


def _latest_cache_file(prefix: str) -> Path | None:
    """Most recently written cache file matching <prefix>_<ts>.json (or None)."""
    d = _universe_cache_dir()
    matches = sorted(d.glob(f"{prefix}_*.json"))
    return matches[-1] if matches else None


def _atomic_write_json(path: Path, blob: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _fetch_or_load_constituent(
    endpoint: str, prefix: str, refresh: bool = False,
) -> dict:
    """Generic fetch-or-load for one constituent endpoint.

    endpoint: FMP /stable/ path (e.g. 'historical-sp500-constituent').
    prefix:   filename prefix under data/cache/universe/.
    refresh:  if True, force a new fetch even when a cache file exists.

    Returns: {'query_timestamp': iso, 'endpoint': ..., 'rows': [...]}.
    """
    if not refresh:
        existing = _latest_cache_file(prefix)
        if existing is not None:
            try:
                with existing.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                logger.warning("universe cache: %s unreadable; refetching", existing)

    data, meta = _api_call(endpoint, group="universe")
    if not meta["ok"] or not isinstance(data, list):
        raise RuntimeError(
            f"FMP {endpoint} fetch failed: "
            f"{meta.get('error_class')} {meta.get('error_short')}"
        )

    ts = _now_iso().replace(":", "").replace("-", "")[:15]  # 20260504T141532
    blob = {
        "query_timestamp": _now_iso(),
        "endpoint": endpoint,
        "rows": data,
    }
    out_path = _universe_cache_dir() / f"{prefix}_{ts}.json"
    _atomic_write_json(out_path, blob)
    return blob


def _parse_change_date(raw: str) -> datetime | None:
    """Robust to 'YYYY-MM-DDThh:mm:ss', 'YYYY-MM-DD', or 'Month DD, YYYY'."""
    if not raw:
        return None
    s = str(raw)
    try:
        return datetime.fromisoformat(s[:10])
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%B %d, %Y")
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%B %d %Y")
    except ValueError:
        pass
    return None


_INDEX_PREFIXES = {
    "sp500":      ("historical-sp500-constituent",  "sp500-constituent",  "historical_sp500_constituent",  "current_sp500_constituent"),
    "nasdaq100":  ("historical-nasdaq-constituent", "nasdaq-constituent", "historical_nasdaq_constituent", "current_nasdaq_constituent"),
}


def historical_universe_as_of(
    decision_date,
    index_set: str = "sp500",
    refresh: bool = False,
) -> set[str]:
    """Return the set of tickers in `index_set` as it stood at end-of-day
    on `decision_date`.

    Args:
      decision_date: date | datetime | ISO string.
      index_set: 'sp500', 'nasdaq100', or 'union' (set-union of both).
      refresh: if True, re-fetch the constituent endpoints (writes a new
        cache file with a new query_timestamp; old caches are preserved
        for audit). Default False — read from latest cached file.

    Raises:
      ValueError: if decision_date > query_timestamp of cached snapshot
        (would be asking for a future-as-of, i.e. lookahead).
      ValueError: if index_set is unknown.
      RuntimeError: if no cache file exists AND refresh fetch fails.

    PIT semantics:
      - Reverse-replays the changes log: starts from today's snapshot,
        for each change row whose date > decision_date undoes that change
        (removes the added ticker, re-adds the removed ticker).
      - The resulting set IS the index as it stood on decision_date.
    """
    if isinstance(decision_date, str):
        dd = datetime.fromisoformat(decision_date.split("T")[0])
    elif isinstance(decision_date, datetime):
        dd = decision_date
    else:
        # date object
        dd = datetime.combine(decision_date, datetime.min.time())

    if index_set == "union":
        return (
            historical_universe_as_of(decision_date, "sp500", refresh=refresh)
            | historical_universe_as_of(decision_date, "nasdaq100", refresh=refresh)
        )

    if index_set not in _INDEX_PREFIXES:
        raise ValueError(
            f"historical_universe_as_of: unknown index_set='{index_set}'. "
            f"Expected one of: sp500, nasdaq100, union."
        )

    hist_endpoint, cur_endpoint, hist_prefix, cur_prefix = _INDEX_PREFIXES[index_set]

    hist_blob = _fetch_or_load_constituent(hist_endpoint, hist_prefix, refresh=refresh)
    cur_blob  = _fetch_or_load_constituent(cur_endpoint,  cur_prefix,  refresh=refresh)

    # PIT guard: refuse decision_date > min(query_timestamp). The two cache
    # files were written close in time but use the older of the two as the
    # safe upper bound.
    qt_hist = datetime.fromisoformat(hist_blob["query_timestamp"].replace("Z", "+00:00"))
    qt_cur  = datetime.fromisoformat(cur_blob["query_timestamp"].replace("Z", "+00:00"))
    qt_min  = min(qt_hist, qt_cur)
    qt_min_naive = qt_min.replace(tzinfo=None)
    if dd > qt_min_naive:
        raise ValueError(
            f"historical_universe_as_of: decision_date {dd.date().isoformat()} "
            f"is after cache query_timestamp {qt_min.isoformat()}. "
            f"Refusing to answer (would be lookahead). Pass refresh=True to "
            f"fetch a fresh snapshot if you need a more recent as-of."
        )

    # Today's universe.
    universe: set[str] = {
        row["symbol"] for row in cur_blob["rows"]
        if isinstance(row, dict) and row.get("symbol")
    }

    # Reverse-replay every change whose effective date is AFTER decision_date.
    # Sort newest-first (chronological reverse) so the inverse operations are
    # applied in the correct order.
    post_changes: list[tuple[datetime, str | None, str | None]] = []
    for row in hist_blob["rows"]:
        if not isinstance(row, dict):
            continue
        d = _parse_change_date(row.get("date") or row.get("dateAdded") or "")
        if d is None or d <= dd:
            continue
        post_changes.append((d, row.get("symbol"), row.get("removedTicker")))
    post_changes.sort(key=lambda x: -x[0].toordinal())

    for _d, added_sym, removed_sym in post_changes:
        if added_sym:
            universe.discard(added_sym)
        if removed_sym:
            universe.add(removed_sym)

    return universe


# ═══════════════════════════════════════════════════════════════
# PIT FUNDAMENTALS  (Pass 8 Step B1.6 — 2026-05-04)
# ═══════════════════════════════════════════════════════════════
#
# get_fundamentals_for_scoring_pit() filters quarterly statements by
# acceptedDate <= decision_date and recomputes TTM aggregates from the
# filtered quarters. Never uses /key-metrics-ttm or /ratios-ttm in PIT
# mode because those endpoints return the CURRENT TTM as of the call
# (FMP does not expose a vintage-aware TTM endpoint).
#
# When called with decision_date=None this delegates to
# get_fundamentals_for_scoring() (live mode, current snapshot).

def get_fundamentals_for_scoring_pit(
    ticker: str, decision_date,
) -> dict:
    """PIT-safe fundamentals snapshot.

    Args:
      ticker: equity symbol.
      decision_date: date | datetime | ISO string. Quarterly statements
        with acceptedDate > decision_date are EXCLUDED (would be data
        unavailable to a real-time observer at decision_date).

    Returns:
      Same shape as get_fundamentals_for_scoring(), plus:
        - 'data_available_as_of' = max(quarterly.accepted_date filtered)
        - 'pit_decision_date'    = decision_date (ISO)
        - 'pit_safe'             = True
        - 'pit_quarters_used'    = N (number of quarters that satisfied
                                       acceptedDate <= decision_date)

    If no quarter satisfies the filter (company too young / decision_date
    before any FMP-available filing), returns
    {'status': 'Data unavailable', 'pit_safe': True, ...}.

    Live TTM endpoints (/key-metrics-ttm, /ratios-ttm) are NEVER called
    in PIT mode because they return current state.
    """
    if isinstance(decision_date, str):
        dd_iso = decision_date.split("T")[0]
    elif isinstance(decision_date, datetime):
        dd_iso = decision_date.date().isoformat()
    else:
        dd_iso = decision_date.isoformat()

    profile = get_company_profile(ticker)
    if profile.get("status") == DATA_UNAVAILABLE:
        return {
            "status": DATA_UNAVAILABLE, "ticker": ticker,
            "pit_safe": True, "pit_decision_date": dd_iso,
            "pit_quarters_used": 0,
            "data_available_as_of": DATA_UNAVAILABLE,
        }

    # Pull more quarters than needed; we filter then trim to 4 trailing.
    income_raw   = get_income_statement(ticker,   period="quarter", limit=12)
    cashflow_raw = get_cash_flow_statement(ticker, period="quarter", limit=12)

    def _accepted_le(rows: list[dict]) -> list[dict]:
        out: list[dict] = []
        for r in rows:
            ad = (r.get("accepted_date") or "")
            if not ad or ad == DATA_UNAVAILABLE:
                continue  # unknown filing time → cannot prove PIT-safe
            if ad[:10] <= dd_iso:
                out.append(r)
        return out

    income_pit   = _accepted_le(income_raw)
    cashflow_pit = _accepted_le(cashflow_raw)

    if not income_pit:
        return {
            "status": DATA_UNAVAILABLE, "ticker": ticker,
            "pit_safe": True, "pit_decision_date": dd_iso,
            "pit_quarters_used": 0,
            "data_available_as_of": DATA_UNAVAILABLE,
            "reason": (
                f"No quarterly income statement with acceptedDate <= "
                f"{dd_iso} for {ticker}."
            ),
        }

    # Sort newest-first by accepted_date so [0:4] is the trailing 4 quarters.
    income_pit.sort(key=lambda r: (r.get("accepted_date") or ""), reverse=True)
    cashflow_pit.sort(key=lambda r: (r.get("accepted_date") or ""), reverse=True)

    latest_income = income_pit[0]
    revenue_ttm    = sum((q.get("revenue", 0)        or 0) for q in income_pit[:4])
    net_income_ttm = sum((q.get("net_income", 0)     or 0) for q in income_pit[:4])
    rd_expense_ttm = sum((q.get("rd_expense", 0)     or 0) for q in income_pit[:4])
    eps_ttm        = sum((q.get("eps", 0)            or 0) for q in income_pit[:4])
    op_inc_ttm     = sum((q.get("operating_income", 0) or 0) for q in income_pit[:4])
    fcf_ttm        = sum((c.get("free_cash_flow", 0) or 0) for c in cashflow_pit[:4])
    op_margin_pit  = (op_inc_ttm / revenue_ttm * 100) if revenue_ttm else None

    revenue_growth: float | str = DATA_UNAVAILABLE
    if len(income_pit) >= 2:
        cur = income_pit[0].get("revenue", 0) or 0
        prv = income_pit[1].get("revenue", 0) or 0
        if prv > 0:
            revenue_growth = round((cur - prv) / prv * 100, 1)

    return {
        "ticker": ticker,
        "name": profile.get("name", ticker),
        "sector": profile.get("sector", DATA_UNAVAILABLE),
        "industry": profile.get("industry", DATA_UNAVAILABLE),
        "market_cap": profile.get("market_cap", 0),
        "is_etf": profile.get("is_etf", False),

        "revenue_ttm": revenue_ttm,
        "revenue_growth_pct": revenue_growth if revenue_growth != DATA_UNAVAILABLE else 0,

        "operating_margin_pct": op_margin_pit,
        # Pass 8 Step B1.7 (2026-05-04): alias under the §2.1 gate's
        # naming so `surge_short_rules.filter_surge_candidates` picks
        # it up directly. operating_margin_pct IS the trailing-4-quarter
        # margin computed from PIT-filtered quarters, so the _ttm suffix
        # is semantically correct.
        "operating_margin_ttm": op_margin_pit,
        "operating_income_ttm": op_inc_ttm,

        "net_income_ttm": net_income_ttm,
        "free_cash_flow_ttm": fcf_ttm,
        "rd_expense_ttm": rd_expense_ttm,
        "eps_ttm": eps_ttm,

        # PIT stamps
        "filing_date":       latest_income.get("filing_date"),
        "fiscal_period_end": latest_income.get("fiscal_period_end"),
        "accepted_date":     latest_income.get("accepted_date"),
        "data_available_as_of": latest_income.get("accepted_date"),
        "pit_decision_date": dd_iso,
        "pit_safe": True,
        "pit_quarters_used": min(4, len(income_pit)),

        # Source flags
        "source": SOURCE_LIVE,
        "data_source": "fmp_pit_filtered",

        # Explicit nulls — TTM ratio endpoints not safe in PIT
        "pe_ratio": None, "forward_pe": None, "peg_ratio": None,
        "price_to_fcf": None, "price_to_sales": None,
        "price_to_book": None, "ev_to_ebitda": None,
        "debt_to_equity": None,
        "going_concern": False, "dilution_risk": "unknown",
        "drawdown_from_ath_pct": 0,

        # Hard assertion: data_available_as_of corresponds to latest
        # quarter's acceptedDate that satisfied the PIT filter.
        "_pit_invariant": "data_available_as_of == max(quarterly.accepted_date <= decision_date)",
    }
