"""Probe alternative historical-news endpoints.

Hits FMP's lesser-known news paths plus checks AlphaVantage / Polygon.io
signup requirements. Returns a structured report so the user can pick
which source to wire.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

WINDOW_START = "2026-04-01"
WINDOW_END = "2026-05-01"
TICKER = "AAPL"


def _fmp_probe(path: str, params: dict | None = None) -> dict:
    from src.data_adapters import fmp_adapter as fmp
    try:
        data, meta = fmp._api_call(path, params, group="news_probe")
    except Exception as e:
        return {"path": path, "params": params,
                "ok": False, "http_status": None,
                "error": f"{type(e).__name__}: {e}",
                "rows": 0, "date_range": None,
                "sample": None}
    rows = 0
    date_range = None
    sample = None
    if isinstance(data, list) and data:
        rows = len(data)
        # Find date field
        date_keys = ("publishedDate", "date", "publishedAt", "datetime")
        dates = []
        for it in data:
            if not isinstance(it, dict):
                continue
            for k in date_keys:
                if it.get(k):
                    dates.append(str(it[k]))
                    break
        if dates:
            date_range = (min(dates), max(dates))
        sample = {k: data[0].get(k) for k in
                  ("publishedDate", "date", "publishedAt", "publisher",
                   "site", "title", "symbol")
                  if isinstance(data[0], dict) and data[0].get(k)}
    return {
        "path": path, "params": params,
        "ok": meta.get("ok"),
        "http_status": meta.get("http_status"),
        "error": meta.get("error_short"),
        "rows": rows,
        "date_range": date_range,
        "sample": sample,
    }


def _alphavantage_probe() -> dict:
    """AlphaVantage NEWS_SENTIMENT endpoint requires apikey. Free demo
    key works for 1-2 calls before rate-limiting."""
    av_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    requires_signup = not av_key
    # Try the demo key (AlphaVantage exposes "demo" for the function for testing)
    test_key = av_key or "demo"
    url = (
        f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT"
        f"&tickers={TICKER}&time_from=20260401T0000&time_to=20260501T2359"
        f"&limit=50&apikey={test_key}"
    )
    out = {
        "service": "AlphaVantage NEWS_SENTIMENT",
        "requires_signup": requires_signup,
        "tested_with_key": ("provided" if av_key else "demo"),
        "url": url,
        "probe_result": None,
    }
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "altdata-probe/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        feed = payload.get("feed") or []
        info = payload.get("Information") or payload.get("Note")
        out["probe_result"] = {
            "http_status": resp.status,
            "items_returned": len(feed),
            "items_field_present": "feed" in payload,
            "rate_limit_msg": info[:300] if info else None,
            "sample_dates": [it.get("time_published") for it in feed[:5]]
                            if feed else None,
            "earliest_date": (min(it.get("time_published", "")
                                   for it in feed) if feed else None),
            "latest_date": (max(it.get("time_published", "")
                                 for it in feed) if feed else None),
        }
    except Exception as e:  # pylint: disable=broad-except
        out["probe_result"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def _polygon_probe() -> dict:
    """Polygon.io v2/reference/news. Free tier: 5 req/min, 2yr lookback."""
    pg_key = os.environ.get("POLYGON_API_KEY", "")
    requires_signup = not pg_key
    if not pg_key:
        return {
            "service": "Polygon.io v2/reference/news",
            "requires_signup": True,
            "tested_with_key": None,
            "probe_result": None,
            "note": ("Polygon does NOT expose a 'demo' key. Probe cannot "
                     "run without a real key. Free tier: register at "
                     "polygon.io, 5 req/min limit, 2yr news lookback."),
        }
    url = (
        f"https://api.polygon.io/v2/reference/news"
        f"?ticker={TICKER}&published_utc.gte=2026-04-01"
        f"&published_utc.lte=2026-05-01&limit=50&apiKey={pg_key}"
    )
    out = {
        "service": "Polygon.io v2/reference/news",
        "requires_signup": False,
        "tested_with_key": "provided",
        "probe_result": None,
    }
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "altdata-probe/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        results = payload.get("results") or []
        out["probe_result"] = {
            "http_status": resp.status,
            "items_returned": len(results),
            "earliest_date": (min(r.get("published_utc", "")
                                   for r in results) if results else None),
            "latest_date": (max(r.get("published_utc", "")
                                 for r in results) if results else None),
            "rate_limit_msg": payload.get("status"),
        }
    except urllib.error.HTTPError as e:
        out["probe_result"] = {
            "http_status": e.code,
            "error": f"HTTPError {e.code}: {e.read()[:200].decode('utf-8', 'ignore')}",
        }
    except Exception as e:  # pylint: disable=broad-except
        out["probe_result"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def main() -> int:
    print("=== News-source probe phase (Option C, pre-commit) ===\n")
    print(f"  target window: {WINDOW_START} .. {WINDOW_END}")
    print(f"  target ticker: {TICKER}")

    out: dict = {
        "fmp_alternatives": [],
        "alphavantage": None,
        "polygon": None,
        "verdict": None,
    }

    # ── FMP alternative paths ────────────────────────────────────────
    print("\n  --- FMP alternative news paths ---")
    fmp_probes = [
        ("articles", {"page": 0, "size": 50}),
        ("articles", {"symbol": TICKER, "from": WINDOW_START, "to": WINDOW_END}),
        ("news/general-latest", {"page": 0, "size": 50}),
        ("news/general-latest", {"from": WINDOW_START, "to": WINDOW_END}),
        ("press-releases", {"symbol": TICKER}),
        ("press-releases", {"symbol": TICKER, "from": WINDOW_START, "to": WINDOW_END}),
        ("news/press-releases-latest", {"page": 0, "size": 50}),
        ("stock-news-sentiments-rss-feed", {"page": 0}),
        ("stock-news", {"tickers": TICKER, "limit": 50,
                         "from": WINDOW_START, "to": WINDOW_END}),
        ("news/stock", {"symbols": TICKER, "from": WINDOW_START,
                         "to": WINDOW_END, "limit": 50}),
    ]
    for path, params in fmp_probes:
        r = _fmp_probe(path, params)
        out["fmp_alternatives"].append(r)
        date_str = (
            f"{r['date_range'][0]}..{r['date_range'][1]}"
            if r.get("date_range") else "—"
        )
        flag = "LIVE" if (r["ok"] and r["rows"] > 0) else (
            "EMPTY" if r["ok"] else "FAIL"
        )
        print(f"    {flag:5s}  /{path:38s}  rows={r['rows']:4d}  "
              f"http={r['http_status']}  dates={date_str}")
        if r.get("error"):
            print(f"           err={str(r['error'])[:120]}")
        if r.get("sample"):
            ks = sorted(r['sample'].keys())
            print(f"           sample keys: {ks}")

    # ── AlphaVantage ─────────────────────────────────────────────────
    print("\n  --- AlphaVantage probe ---")
    av = _alphavantage_probe()
    out["alphavantage"] = av
    print(f"    requires_signup: {av['requires_signup']}")
    print(f"    tested with key: {av['tested_with_key']}")
    pr = av.get("probe_result") or {}
    if pr.get("error"):
        print(f"    FAIL: {pr['error']}")
    else:
        print(f"    items returned: {pr.get('items_returned')}")
        print(f"    rate-limit msg: {pr.get('rate_limit_msg')}")
        print(f"    date range:     {pr.get('earliest_date')} .. {pr.get('latest_date')}")

    # ── Polygon.io ───────────────────────────────────────────────────
    print("\n  --- Polygon.io probe ---")
    pg = _polygon_probe()
    out["polygon"] = pg
    print(f"    requires_signup: {pg['requires_signup']}")
    if pg.get("note"):
        print(f"    note: {pg['note']}")
    pr = pg.get("probe_result") or {}
    if pr:
        if pr.get("error"):
            print(f"    FAIL: {pr['error']}")
        else:
            print(f"    items returned: {pr.get('items_returned')}")
            print(f"    date range:     {pr.get('earliest_date')} .. {pr.get('latest_date')}")

    # ── Verdict ──────────────────────────────────────────────────────
    print("\n  === VERDICT ===")
    fmp_winners = [r for r in out["fmp_alternatives"]
                    if r["ok"] and r["rows"] > 0
                    and r.get("date_range") and r["date_range"][0]
                    and r["date_range"][0][:10] <= WINDOW_END]
    av_works = (av["probe_result"] and
                av["probe_result"].get("items_returned", 0) > 0)
    pg_works = (pg["probe_result"] and
                pg["probe_result"].get("items_returned", 0) > 0)

    print(f"  FMP alternatives that returned historical items: {len(fmp_winners)}")
    for w in fmp_winners:
        print(f"    -> {w['path']}  rows={w['rows']}  dates={w['date_range']}")
    print(f"  AlphaVantage usable: {av_works}")
    print(f"  Polygon usable: {pg_works}")

    out["verdict"] = {
        "fmp_alternative_winners": len(fmp_winners),
        "alphavantage_usable": av_works,
        "polygon_usable": pg_works,
        "any_winner": bool(fmp_winners) or bool(av_works) or bool(pg_works),
    }

    out_path = ROOT / "data" / "altdata" / "_news_probe_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
