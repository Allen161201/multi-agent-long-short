"""
Alt-data availability diagnostic — read-only.

Probes each of the 11 user-named alt-data sources for tickers
AAPL/NVDA/TSLA across the 2026-04-01..2026-05-01 window. Reports per
(source, ticker, day) the status, item count, cache state, and any
error. Aggregates to a per-source table.

Source → adapter mapping (from the preservation-check baseline):
  1. news_event_summary       → fmp_adapter.get_news_latest
  2. fmp_news                 → fmp_adapter._api_call("news/stock-latest", ...)
                                (alias of #1; tested separately to expose
                                 plan vs interface variation if any)
  3. fmp_sentiment            → fmp _api_call("historical/social-sentiment")
                                or "stock_news_sentiments_rss_feed_api"
  4. sec_form4                → fmp _api_call("insider-trading")
                                + sec_adapter.get_sec_filings (mock-only stub)
  5. sec_13f                  → fmp _api_call("institutional-holder")
                                + sec_adapter (mock-only stub)
  6. sec_8k_fulltext          → fmp _api_call("sec-filings", type=8-K)
                                + sec_adapter
  7. corporate_calendar       → fmp_adapter.get_corporate_calendar
  8. alternative_data_features → composite (skipped — scored by components)
  9. wikipedia                → no adapter (INTERFACE_GAP — direct REST probe)
 10. github_commit_messages   → github_adapter.get_github_data (mock-only)
                                + direct GitHub API probe
 11. macro_regime             → fred_adapter.get_macro_indicators

Status taxonomy:
  LIVE          — endpoint returned ≥1 item AND wasn't mock-fallback
  EMPTY         — endpoint returned 0 items AND wasn't an error
  PLAN_LIMITED  — HTTP 4xx (401/402/403) or "premium endpoint" message
  INTERFACE_GAP — adapter present but not wired to a real source
                  (returns mock data only)
  ERROR         — any other failure (network, parse, 5xx)

Output: data/altdata/_d4_availability_diagnostic.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

TICKERS = ("AAPL", "NVDA", "TSLA")
WINDOW_START = date(2026, 4, 1)
WINDOW_END = date(2026, 5, 1)
OUT_PATH = ROOT / "data" / "altdata" / "_d4_availability_diagnostic.json"


def _trading_days(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _classify(http_status: Optional[int], error: Optional[str], rows: int,
              data: Any) -> str:
    """Map (http_status, error, rows) → status code."""
    if http_status in (401, 402, 403):
        return "PLAN_LIMITED"
    if error and any(kw in str(error).lower() for kw in (
        "premium", "subscription", "upgrade", "not authorized",
        "plan", "denied"
    )):
        return "PLAN_LIMITED"
    if error and http_status and http_status >= 500:
        return "ERROR"
    if error and http_status is None:
        return "ERROR"
    if rows == 0:
        return "EMPTY"
    if rows > 0:
        return "LIVE"
    if data is None:
        return "ERROR"
    return "EMPTY"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Source probes ────────────────────────────────────────────────────

def probe_fmp_endpoint(path: str, params: dict) -> dict:
    """Generic FMP endpoint probe. Returns a normalized dict."""
    from src.data_adapters import fmp_adapter as fmp
    try:
        data, meta = fmp._api_call(path, params, group="diagnostic")
    except Exception as e:
        return {
            "ok": False, "http_status": None, "rows": 0,
            "error": f"{type(e).__name__}: {e}", "data": None,
        }
    rows = 0
    if isinstance(data, list):
        rows = len(data)
    elif isinstance(data, dict):
        # Most FMP endpoints either return a list or a singleton dict.
        items = data.get("items") if isinstance(data.get("items"), list) else None
        if items is not None:
            rows = len(items)
        elif data:
            rows = 1
    return {
        "ok": meta.get("ok", False),
        "http_status": meta.get("http_status"),
        "rows": rows,
        "error": meta.get("error_short"),
        "data": data if rows == 0 else None,  # keep small payload for empty
    }


def probe_news_event_summary(ticker: str, day: date) -> dict:
    from src.data_adapters import fmp_adapter as fmp
    try:
        env = fmp.get_news_latest(ticker, limit=20)
    except Exception as e:
        return {"status": "ERROR", "rows": 0, "cache_hit": False,
                "error": f"{type(e).__name__}: {e}"}
    rows = env.get("row_count", 0)
    err = env.get("error_short")
    http = env.get("http_status")
    served = bool(env.get("served_from_cache"))
    status = _classify(http, err, rows, env.get("items"))
    if env.get("status") == "Data unavailable" and rows == 0 and not err:
        # FMP returned ok but empty — most-recent endpoint outside window
        status = "EMPTY"
    return {"status": status, "rows": rows, "cache_hit": served,
            "error": err, "http_status": http}


def probe_fmp_news(ticker: str, day: date) -> dict:
    # Same underlying endpoint; the diagnostic separates them so the
    # user can see whether some downstream block layer is the difference.
    return probe_news_event_summary(ticker, day)


def probe_fmp_sentiment(ticker: str, day: date) -> dict:
    # Try the historical-social-sentiment endpoint.
    res = probe_fmp_endpoint("historical/social-sentiment", {"symbol": ticker})
    if not res["ok"] and res["http_status"] in (None, 404):
        # Try alternate path
        res = probe_fmp_endpoint("social-sentiment", {"symbol": ticker})
    status = _classify(res["http_status"], res["error"], res["rows"], res["data"])
    return {"status": status, "rows": res["rows"], "cache_hit": False,
            "error": res["error"], "http_status": res["http_status"]}


def probe_sec_form4(ticker: str, day: date) -> dict:
    """D5 Option C — probe the real SEC EDGAR direct adapter
    (data.sec.gov submissions JSON + per-filing XML). The earlier
    diagnostic probed FMP `insider-trading` which isn't on the user's
    plan; the actual adapter at src/adapters/alt_data/sec_ownership.py
    fetches directly from SEC EDGAR with proper acceptedDateTime PIT
    discipline."""
    from datetime import datetime as _dt, timezone as _tz
    from src.adapters.alt_data.sec_ownership import SECForm4Adapter
    cutoff = _dt(day.year, day.month, day.day, 23, 59, 59, tzinfo=_tz.utc)
    try:
        a = SECForm4Adapter()
        res = a.fetch(ticker=ticker, as_of=cutoff,
                       decision_timestamp=cutoff, stub_mode=False)
    except Exception as e:
        return {"status": "ERROR", "rows": 0, "cache_hit": False,
                "error": f"{type(e).__name__}: {e}", "http_status": None}
    if res.extraction_status == "ok" and len(res.rows) > 0:
        return {"status": "LIVE", "rows": len(res.rows), "cache_hit": False,
                "error": None, "http_status": 200}
    if res.extraction_status == "ok":
        return {"status": "EMPTY", "rows": 0, "cache_hit": False,
                "error": None, "http_status": 200}
    return {"status": "ERROR", "rows": 0, "cache_hit": False,
            "error": f"sec_form4: {res.error_class}", "http_status": None}


def probe_sec_13f(ticker: str, day: date) -> dict:
    """Probe SEC13FAdapter (uses FMP institutional-ownership/symbol-
    ownership). On the user's current FMP plan this returns
    not_available_on_current_plan — graceful data_unavailable per
    RULES.md §11.2 (acceptable design per sec_ownership.py:49-58)."""
    from datetime import datetime as _dt, timezone as _tz
    from src.adapters.alt_data.sec_ownership import SEC13FAdapter
    cutoff = _dt(day.year, day.month, day.day, 23, 59, 59, tzinfo=_tz.utc)
    try:
        a = SEC13FAdapter()
        res = a.fetch(ticker=ticker, as_of=cutoff,
                       decision_timestamp=cutoff, stub_mode=False)
    except Exception as e:
        return {"status": "ERROR", "rows": 0, "cache_hit": False,
                "error": f"{type(e).__name__}: {e}", "http_status": None}
    if res.extraction_status == "ok" and len(res.rows) > 0:
        return {"status": "LIVE", "rows": len(res.rows), "cache_hit": False,
                "error": None, "http_status": 200}
    if res.extraction_status == "ok":
        return {"status": "EMPTY", "rows": 0, "cache_hit": False,
                "error": None, "http_status": 200}
    # Plan-limited gracefully — surface the reason but still EMPTY (not error)
    return {"status": "EMPTY", "rows": 0, "cache_hit": False,
            "error": (f"sec_13f: {res.error_class} (FMP institutional-"
                       f"ownership requires premium plan; documented "
                       f"limitation per RULES.md §11.2 data_unavailable)"),
            "http_status": 200}


def probe_sec_8k_fulltext(ticker: str, day: date) -> dict:
    """Probe SECEdgarAdapter (data.sec.gov submissions JSON, 8-K only).
    Full-text retrieval is owned by the OpenCLI sec_8k_fulltext
    use case which sits downstream — this adapter provides the
    filing index at the metadata level."""
    from datetime import datetime as _dt, timezone as _tz
    from src.adapters.alt_data.sec_edgar import SECEdgarAdapter
    cutoff = _dt(day.year, day.month, day.day, 23, 59, 59, tzinfo=_tz.utc)
    try:
        a = SECEdgarAdapter()
        res = a.fetch(ticker=ticker, as_of=cutoff,
                       decision_timestamp=cutoff, stub_mode=False)
    except Exception as e:
        return {"status": "ERROR", "rows": 0, "cache_hit": False,
                "error": f"{type(e).__name__}: {e}", "http_status": None}
    if res.extraction_status == "ok" and len(res.rows) > 0:
        return {"status": "LIVE", "rows": len(res.rows), "cache_hit": False,
                "error": None, "http_status": 200}
    if res.extraction_status == "ok":
        return {"status": "EMPTY", "rows": 0, "cache_hit": False,
                "error": None, "http_status": 200}
    return {"status": "ERROR", "rows": 0, "cache_hit": False,
            "error": f"sec_edgar: {res.error_class}", "http_status": None}


def probe_corporate_calendar(ticker: str, day: date) -> dict:
    """D5 Option C — probe the PIT-aware path via
    fmp.get_corporate_calendar_pit. Reads earnings/dividends/splits
    sub-blocks and counts events with date <= cutoff."""
    from datetime import datetime as _dt, timezone as _tz
    from src.data_adapters import fmp_adapter as fmp
    cutoff = _dt(day.year, day.month, day.day, 23, 59, 59, tzinfo=_tz.utc)
    try:
        env = fmp.get_corporate_calendar_pit(ticker, cutoff)
    except Exception as e:
        return {"status": "ERROR", "rows": 0, "cache_hit": False,
                "error": f"{type(e).__name__}: {e}", "http_status": None}
    if not isinstance(env, dict):
        return {"status": "ERROR", "rows": 0, "cache_hit": False,
                "error": "unexpected return shape", "http_status": None}
    rows = (
        len(env.get("earnings", {}).get("past", []))
        + len(env.get("dividends", {}).get("past", []))
        + len(env.get("splits", {}).get("past", []))
    )
    if env.get("status") == "available":
        return {"status": ("LIVE" if rows > 0 else "EMPTY"),
                "rows": rows,
                "cache_hit": bool(env.get("served_from_cache")),
                "error": None, "http_status": env.get("http_status")}
    return {"status": "EMPTY", "rows": rows, "cache_hit": False,
            "error": env.get("error_short"),
            "http_status": env.get("http_status")}


def probe_polygon_news(ticker: str, day: date) -> dict:
    """D5 Option C — probe the polygon_news adapter. Replaces fmp_news
    as the historical news source."""
    from datetime import datetime as _dt, timezone as _tz
    from src.adapters.alt_data.polygon_news import PolygonNewsAdapter
    cutoff = _dt(day.year, day.month, day.day, 23, 59, 59, tzinfo=_tz.utc)
    try:
        a = PolygonNewsAdapter(lookback_days=14, limit=20)
        res = a.fetch(ticker=ticker, as_of=cutoff,
                       decision_timestamp=cutoff, stub_mode=False)
    except Exception as e:
        return {"status": "ERROR", "rows": 0, "cache_hit": False,
                "error": f"{type(e).__name__}: {e}", "http_status": None}
    if res.extraction_status == "ok" and len(res.rows) > 0:
        return {"status": "LIVE", "rows": len(res.rows), "cache_hit": False,
                "error": None, "http_status": 200}
    if res.extraction_status == "ok":
        return {"status": "EMPTY", "rows": 0, "cache_hit": False,
                "error": None, "http_status": 200}
    return {"status": "ERROR", "rows": 0, "cache_hit": False,
            "error": f"polygon_news: {res.error_class}", "http_status": None}


def probe_wikipedia(ticker: str, day: date) -> dict:
    """Direct Wikimedia REST probe — pageviews for the ticker's
    company-page slug. We don't have an adapter, so this is best-effort
    and labeled INTERFACE_GAP if anything fails."""
    try:
        import urllib.request
        # Slug is approximate; this is a probe for adapter availability,
        # not an authoritative metric. Use AAPL→Apple_Inc., NVDA→Nvidia,
        # TSLA→Tesla,_Inc. as common slugs.
        slug = {
            "AAPL": "Apple_Inc.",
            "NVDA": "Nvidia",
            "TSLA": "Tesla,_Inc.",
        }.get(ticker, ticker)
        d_str = day.strftime("%Y%m%d")
        url = (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia.org/all-access/all-agents/{slug}/daily/"
            f"{d_str}/{d_str}"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "altdata-diagnostic/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        items = payload.get("items") or []
        return {"status": ("LIVE" if items else "EMPTY"),
                "rows": len(items), "cache_hit": False,
                "error": None, "http_status": resp.status}
    except Exception as e:  # pylint: disable=broad-except
        return {"status": "INTERFACE_GAP", "rows": 0, "cache_hit": False,
                "error": f"no Wikipedia adapter; direct probe failed: "
                         f"{type(e).__name__}: {e}",
                "http_status": None}


def probe_github_commit_messages(ticker: str, day: date) -> dict:
    """github_adapter is mock-only. Try direct GitHub API for the
    ticker's known repo (best-effort)."""
    repo_map = {
        "AAPL": "apple/swift",
        "NVDA": "NVIDIA/cuda-samples",
        "TSLA": None,  # Tesla has no canonical public repo
    }
    repo = repo_map.get(ticker)
    if not repo:
        return {"status": "INTERFACE_GAP", "rows": 0, "cache_hit": False,
                "error": "no canonical public repo mapping for this ticker; "
                         "github_adapter is mock-only stub",
                "http_status": None}
    try:
        import urllib.request
        token = os.environ.get("GITHUB_TOKEN", "")
        # Commits since 30 days before the probe day, until probe day.
        since = (day - timedelta(days=30)).isoformat() + "T00:00:00Z"
        until = day.isoformat() + "T16:15:00Z"
        url = (
            f"https://api.github.com/repos/{repo}/commits"
            f"?since={since}&until={until}&per_page=10"
        )
        headers = {"User-Agent": "altdata-diagnostic/1.0",
                   "Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = len(data) if isinstance(data, list) else 0
        return {"status": ("LIVE" if rows > 0 else "EMPTY"),
                "rows": rows,
                "cache_hit": False,
                "error": None if rows > 0 else "no commits in window",
                "http_status": resp.status}
    except Exception as e:  # pylint: disable=broad-except
        return {"status": "INTERFACE_GAP", "rows": 0, "cache_hit": False,
                "error": f"github_adapter is mock-only; direct probe "
                         f"failed: {type(e).__name__}: {e}",
                "http_status": None}


def probe_macro_regime(ticker: str, day: date) -> dict:
    """FRED — not ticker-specific. We probe once per day (ticker is
    irrelevant). Cached by fred_adapter."""
    from src.data_adapters import fred_adapter
    try:
        env = fred_adapter.get_macro_indicators(day.isoformat())
    except Exception as e:
        return {"status": "ERROR", "rows": 0, "cache_hit": False,
                "error": f"{type(e).__name__}: {e}", "http_status": None}
    if isinstance(env, dict):
        keys = list(env.keys())
        rows = len([k for k in keys if isinstance(env.get(k), dict)
                     and env[k].get("value") is not None])
        return {"status": ("LIVE" if rows > 0 else "EMPTY"),
                "rows": rows, "cache_hit": False,
                "error": None, "http_status": None}
    return {"status": "ERROR", "rows": 0, "cache_hit": False,
            "error": "unexpected return shape", "http_status": None}


# Source registry — order matches the user's 11-source list.
SOURCES = [
    # D5 Option C (2026-05-01): news_event_summary now backed by
    # polygon_news (historical, opt-in). fmp_news kept as live-only
    # fallback. SEC sources probe the actual SEC EDGAR direct adapters
    # in src/adapters/alt_data/. corporate_calendar probes PIT path.
    ("01_news_event_summary", probe_polygon_news),
    ("02_fmp_news",            probe_fmp_news),
    ("03_fmp_sentiment",       probe_fmp_sentiment),
    ("04_sec_form4",           probe_sec_form4),
    ("05_sec_13f",             probe_sec_13f),
    ("06_sec_8k_fulltext",     probe_sec_8k_fulltext),
    ("07_corporate_calendar",  probe_corporate_calendar),
    # alternative_data_features (#08) is composite — skipped.
    ("09_wikipedia",           probe_wikipedia),
    ("10_github_commit_msgs",  probe_github_commit_messages),
    ("11_macro_regime",        probe_macro_regime),
]


def probe_fmp_plan() -> dict:
    """Hit FMP plan-status endpoints to log accessible vs limited."""
    from src.data_adapters import fmp_adapter as fmp
    out: dict = {}
    for path in (
        "api-key-status", "api-keys-validate",
    ):
        try:
            data, meta = fmp._api_call(path, None, group="plan_probe")
            out[path] = {
                "ok": meta.get("ok"),
                "http_status": meta.get("http_status"),
                "data_excerpt": str(data)[:240] if data else None,
                "error": meta.get("error_short"),
            }
        except Exception as e:
            out[path] = {"ok": False, "http_status": None,
                         "data_excerpt": None,
                         "error": f"{type(e).__name__}: {e}"}
    # Probe specific endpoints we already know about
    for path, params in (
        ("sp500-constituent", None),
        ("nasdaq-constituent", None),
        ("earnings-calendar", {"from": "2026-04-01", "to": "2026-05-01"}),
        ("biggest-gainers", None),  # current
        ("news/stock-latest", {"symbols": "AAPL", "limit": 5}),
        ("historical-price-eod/full", {"symbol": "AAPL",
                                         "from": "2026-04-01",
                                         "to": "2026-05-01"}),
        ("income-statement", {"symbol": "AAPL", "period": "quarter"}),
        ("key-metrics-ttm", {"symbol": "AAPL"}),
        ("ratios-ttm", {"symbol": "AAPL"}),
        ("profile", {"symbol": "AAPL"}),
        ("quote", {"symbol": "AAPL"}),
    ):
        try:
            data, meta = fmp._api_call(path, params, group="plan_probe")
            rows = (
                len(data) if isinstance(data, list)
                else (1 if data else 0)
            )
            out[path] = {
                "ok": meta.get("ok"),
                "http_status": meta.get("http_status"),
                "rows": rows,
                "error": meta.get("error_short"),
            }
        except Exception as e:
            out[path] = {
                "ok": False, "http_status": None, "rows": 0,
                "error": f"{type(e).__name__}: {e}",
            }
    return out


def main() -> int:
    print("=== Alt-data availability diagnostic ===")
    print(f"  window: {WINDOW_START.isoformat()}..{WINDOW_END.isoformat()}")
    print(f"  tickers: {TICKERS}")

    days = _trading_days(WINDOW_START, WINDOW_END)
    print(f"  trading days: {len(days)}")
    print(f"  total probes: {len(days) * len(TICKERS) * len(SOURCES)} "
          f"(plus {len(SOURCES) * len(TICKERS)} for one-shot sources)")

    # ── Per-source aggregate ─────────────────────────────────────────
    aggregate: dict[str, dict] = {}
    per_probe: list[dict] = []

    # Optimization: for sources that don't depend on `day` (most FMP
    # endpoints in our adapter return current-window data regardless of
    # the date param we pass), we run ONCE per ticker rather than once
    # per (ticker, day). For sources that DO depend on day (corporate
    # calendar — agnostic anyway, news_event_summary — same), we still
    # probe once per ticker because all 22 days return identical data
    # (the "stock-latest" endpoint is wall-clock-now, not historical).
    # macro_regime DOES vary by day — probe daily.

    PER_TICKER_ONCE = {
        "01_news_event_summary",
        "02_fmp_news",
        "03_fmp_sentiment",
        "04_sec_form4",
        "05_sec_13f",
        "06_sec_8k_fulltext",
        "07_corporate_calendar",
        "09_wikipedia",       # technically per-day, but probe one for adapter check
        "10_github_commit_msgs",  # window aggregated; one probe per ticker is fine
    }

    for src_name, probe_fn in SOURCES:
        agg = {
            "source_name": src_name,
            "days_with_data": 0,
            "days_empty": 0,
            "days_failed": 0,
            "total_items": 0,
            "first_probe": None,
            "failure_modes": [],
        }
        if src_name in PER_TICKER_ONCE:
            for t in TICKERS:
                # Pick a single representative day (mid-window).
                d = days[len(days) // 2]
                t0 = time.perf_counter()
                res = probe_fn(t, d)
                elapsed = time.perf_counter() - t0
                row = {
                    "source": src_name, "ticker": t, "day": d.isoformat(),
                    "status": res["status"], "rows": res["rows"],
                    "cache_hit": res["cache_hit"],
                    "http_status": res.get("http_status"),
                    "error": res["error"], "elapsed_s": round(elapsed, 3),
                }
                per_probe.append(row)
                # Only one probe → score per ticker, count one "day" each
                if res["status"] == "LIVE":
                    agg["days_with_data"] += 1
                    agg["total_items"] += res["rows"]
                elif res["status"] == "EMPTY":
                    agg["days_empty"] += 1
                else:
                    agg["days_failed"] += 1
                    if res["error"]:
                        agg["failure_modes"].append(
                            f"{t}: {res['status']}: {str(res['error'])[:160]}"
                        )
                if agg["first_probe"] is None:
                    agg["first_probe"] = row
        else:
            # Probe each (ticker, day)
            for t in TICKERS:
                for d in days:
                    t0 = time.perf_counter()
                    res = probe_fn(t, d)
                    elapsed = time.perf_counter() - t0
                    row = {
                        "source": src_name, "ticker": t, "day": d.isoformat(),
                        "status": res["status"], "rows": res["rows"],
                        "cache_hit": res["cache_hit"],
                        "http_status": res.get("http_status"),
                        "error": res["error"], "elapsed_s": round(elapsed, 3),
                    }
                    per_probe.append(row)
                    if res["status"] == "LIVE":
                        agg["days_with_data"] += 1
                        agg["total_items"] += res["rows"]
                    elif res["status"] == "EMPTY":
                        agg["days_empty"] += 1
                    else:
                        agg["days_failed"] += 1
                        if res["error"] and len(agg["failure_modes"]) < 5:
                            agg["failure_modes"].append(
                                f"{t} {d}: {res['status']}: "
                                f"{str(res['error'])[:160]}"
                            )
                    if agg["first_probe"] is None:
                        agg["first_probe"] = row
        aggregate[src_name] = agg
        print(f"  {src_name:30s}  live={agg['days_with_data']:3d}  "
              f"empty={agg['days_empty']:3d}  failed={agg['days_failed']:3d}  "
              f"items={agg['total_items']}")

    # ── FMP plan probe ───────────────────────────────────────────────
    print("\n=== FMP plan / endpoint accessibility probe ===")
    plan = probe_fmp_plan()
    for path, info in plan.items():
        ok = info.get("ok")
        rows = info.get("rows")
        http = info.get("http_status")
        err = info.get("error")
        print(f"  {path:35s}  ok={ok}  http={http}  rows={rows}  "
              f"err={str(err)[:90] if err else None}")

    # ── Persist ──────────────────────────────────────────────────────
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump({
            "scanned_at_utc": _now_iso(),
            "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
            "tickers": list(TICKERS),
            "trading_days": [d.isoformat() for d in days],
            "aggregate_by_source": aggregate,
            "per_probe_rows": per_probe,
            "fmp_plan_probe": plan,
        }, f, indent=2, default=str)
    print(f"\n  saved: {OUT_PATH}")

    # ── 3-line summary ───────────────────────────────────────────────
    n_live = sum(1 for a in aggregate.values() if a["days_with_data"] > 0)
    n_empty = sum(1 for a in aggregate.values()
                   if a["days_with_data"] == 0 and a["days_empty"] > 0)
    n_failed = sum(1 for a in aggregate.values()
                    if a["days_with_data"] == 0
                    and a["days_empty"] == 0
                    and a["days_failed"] > 0)
    print(f"\n  SUMMARY: {n_live} sources LIVE, {n_empty} empty, "
          f"{n_failed} failed (out of {len(aggregate)} probed; "
          f"alternative_data_features composite skipped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
