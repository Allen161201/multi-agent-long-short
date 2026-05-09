"""
FMP Pre-Purchase Smoke Test
---------------------------
Bounded test of the FMP free API key against the *current* /stable/ surface.
NOTE: As of 2025-08-31 FMP deprecated the legacy /api/v3/ paths. The existing
production adapter (src/data_adapters/fmp_adapter.py) still targets v3 and
will return HTTP 403 "Legacy Endpoint" on every call. The smoke test below
probes the new /stable/ paths instead and is the source of truth for what
the free key can and cannot reach today.

Tickers: AAPL (fundamentals), AAPL+UBER (quote/profile/historical), 1 gainers.
Total expected calls: ~17. Well below the free daily quota.

Does NOT print the API key. Does NOT integrate FMP into the pipeline.
Run from project root:
    python -m tests.fmp_smoke_test
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.environ.get("FMP_API_KEY", "").strip()
STABLE = "https://financialmodelingprep.com/stable"
V3 = "https://financialmodelingprep.com/api/v3"  # legacy probe only

CALL_COUNT = 0
RESULTS: list[dict] = []
SAMPLES: dict[str, object] = {}

DATE_FIELDS_OF_INTEREST = (
    "date", "fillingDate", "filingDate", "acceptedDate", "calendarYear",
    "period", "reportedDate", "reportDate", "datetime", "ipoDate",
    "earningsAnnouncement", "fiscalYear",
)


def call(label: str, base: str, path: str, params: dict | None = None,
         ticker: str | None = None) -> dict:
    """Make one FMP call and record the outcome."""
    global CALL_COUNT
    CALL_COUNT += 1
    params = dict(params or {})
    params["apikey"] = API_KEY
    url = f"{base}/{path}"
    redacted_q = urlencode(
        {k: ("***" if k == "apikey" else v) for k, v in params.items()}
    )
    redacted_url = f"{url}?{redacted_q}"

    record: dict = {
        "label": label,
        "ticker": ticker,
        "url_redacted": redacted_url,
        "base": base,
        "path": path,
        "http_status": None,
        "ok": False,
        "is_paid_required": False,
        "is_legacy": False,
        "appears_live": False,
        "top_level_fields": [],
        "date_fields_present": [],
        "filingDate_present": False,
        "acceptedDate_present": False,
        "calendarYear_or_period_present": False,
        "error": None,
        "elapsed_ms": None,
    }

    t0 = time.time()
    try:
        resp = requests.get(url, params=params, timeout=15)
        record["http_status"] = resp.status_code
        record["elapsed_ms"] = int((time.time() - t0) * 1000)
        try:
            data = resp.json()
        except Exception:
            data = None
        record["raw_text_head"] = (resp.text or "")[:240]

        if isinstance(data, dict) and ("Error Message" in data or "error" in data):
            msg = str(data.get("Error Message") or data.get("error"))
            record["error"] = msg
            low = msg.lower()
            if "legacy" in low:
                record["is_legacy"] = True
            if any(s in low for s in
                   ("premium", "exclusive", "subscription",
                    "upgrade", "not available under your", "starter",
                    "professional", "enterprise")):
                record["is_paid_required"] = True
            return record

        if resp.status_code in (401, 402):
            record["error"] = f"HTTP {resp.status_code}: auth/payment required"
            record["is_paid_required"] = resp.status_code == 402
            return record
        if resp.status_code == 403:
            record["error"] = "HTTP 403 forbidden — likely paid-tier endpoint"
            record["is_paid_required"] = True
            return record
        if resp.status_code != 200:
            record["error"] = f"HTTP {resp.status_code}"
            return record

        record["ok"] = True
        record["appears_live"] = True
        sample_for_keys = None
        if isinstance(data, list):
            record["response_shape"] = f"list[{len(data)}]"
            if data and isinstance(data[0], dict):
                sample_for_keys = data[0]
        elif isinstance(data, dict):
            record["response_shape"] = "dict"
            sample_for_keys = data
        else:
            record["response_shape"] = type(data).__name__

        if isinstance(sample_for_keys, dict):
            keys = list(sample_for_keys.keys())
            record["top_level_fields"] = keys[:30]
            present_dates = [k for k in keys if k in DATE_FIELDS_OF_INTEREST]
            record["date_fields_present"] = present_dates
            record["filingDate_present"] = (
                "fillingDate" in keys or "filingDate" in keys
            )
            record["acceptedDate_present"] = "acceptedDate" in keys
            record["calendarYear_or_period_present"] = (
                "calendarYear" in keys or "period" in keys
                or "fiscalYear" in keys
            )
        record["data_preview"] = (
            data if isinstance(data, list) and len(data) <= 1
            else (data[:1] if isinstance(data, list) else data)
        )
    except requests.RequestException as e:
        record["error"] = f"RequestException: {e}"
    return record


def store(rec: dict) -> dict:
    RESULTS.append(rec)
    return rec


def main() -> int:
    if not API_KEY:
        print("FMP_API_KEY not found in .env. Aborting smoke test.")
        return 2

    print("FMP SMOKE TEST — start", datetime.now().isoformat())
    print(f"Key length: {len(API_KEY)} (value redacted)")
    print("Tickers: AAPL (fundamentals + market), UBER (market only).")
    print("-" * 78)

    # 0. Legacy v3 probe — confirms whether the v3 surface is dead for this key.
    store(call("legacy_v3_probe_profile", V3, "profile/AAPL", ticker="AAPL"))

    # 1. Connection / key validity — stable profile.
    rec = store(call("connection_probe_stable", STABLE, "profile",
                     {"symbol": "AAPL"}, ticker="AAPL"))
    if not rec["ok"] and rec["http_status"] in (401, 402, 403) and \
            "Invalid API KEY" in (rec.get("error") or ""):
        print("Stable connection probe says key is INVALID. Abort.")
        print(json.dumps(rec, indent=2, default=str))
        return 1
    SAMPLES["profile_AAPL"] = rec.get("data_preview")

    # 2. Quote — AAPL, UBER
    store(call("quote_AAPL", STABLE, "quote", {"symbol": "AAPL"}, ticker="AAPL"))
    SAMPLES["quote_AAPL"] = RESULTS[-1].get("data_preview")
    store(call("quote_UBER", STABLE, "quote", {"symbol": "UBER"}, ticker="UBER"))

    # 3. Profile — UBER (AAPL covered above)
    store(call("profile_UBER", STABLE, "profile",
               {"symbol": "UBER"}, ticker="UBER"))

    # 4. Historical 5-day OHLCV — AAPL, UBER
    store(call("historical_5d_AAPL", STABLE, "historical-price-eod/full",
               {"symbol": "AAPL"}, ticker="AAPL"))
    SAMPLES["historical_5d_AAPL_full"] = RESULTS[-1].get("data_preview")
    store(call("historical_5d_UBER", STABLE, "historical-price-eod/full",
               {"symbol": "UBER"}, ticker="UBER"))

    # 5-10. Statements — AAPL only (annual + quarter for income / balance / cash flow)
    for stmt_path, label_stem in (
        ("income-statement", "income_statement"),
        ("balance-sheet-statement", "balance_sheet"),
        ("cash-flow-statement", "cash_flow"),
    ):
        for period in ("annual", "quarter"):
            label = f"{label_stem}_{period}_AAPL"
            store(call(label, STABLE, stmt_path,
                       {"symbol": "AAPL", "period": period, "limit": 1},
                       ticker="AAPL"))

    SAMPLES["income_annual_AAPL"] = next(
        (r.get("data_preview") for r in RESULTS
         if r["label"] == "income_statement_annual_AAPL"), None)

    # 11. Key metrics — TTM first, fall back to non-TTM if blocked.
    rec_km = store(call("key_metrics_ttm_AAPL", STABLE, "key-metrics-ttm",
                        {"symbol": "AAPL"}, ticker="AAPL"))
    if not rec_km["ok"]:
        store(call("key_metrics_AAPL", STABLE, "key-metrics",
                   {"symbol": "AAPL", "period": "annual", "limit": 1},
                   ticker="AAPL"))

    # 12. Ratios — TTM first, fall back to non-TTM if blocked.
    rec_r = store(call("ratios_ttm_AAPL", STABLE, "ratios-ttm",
                       {"symbol": "AAPL"}, ticker="AAPL"))
    if not rec_r["ok"]:
        store(call("ratios_AAPL", STABLE, "ratios",
                   {"symbol": "AAPL", "period": "annual", "limit": 1},
                   ticker="AAPL"))
    SAMPLES["ratios_ttm_or_annual_AAPL"] = RESULTS[-1].get("data_preview")

    # 13. Financial growth — annual, limit 1
    store(call("financial_growth_AAPL", STABLE, "financial-growth",
               {"symbol": "AAPL", "period": "annual", "limit": 1},
               ticker="AAPL"))

    # 14. Biggest gainers (current). Single request.
    store(call("biggest_gainers", STABLE, "biggest-gainers"))

    # ── Summary table ──
    print(f"\nTotal FMP calls made: {CALL_COUNT}")
    print("-" * 78)
    hdr = f"{'#':<3}{'Endpoint':<32}{'Tk':<6}{'HTTP':<6}{'OK':<4}{'Paid?':<7}" \
          f"{'fil':<5}{'acc':<5}{'cal/per':<9}"
    print(hdr)
    print("-" * 78)
    for i, r in enumerate(RESULTS, 1):
        print(
            f"{i:<3}{r['label'][:31]:<32}"
            f"{(r.get('ticker') or '-'):<6}"
            f"{str(r['http_status']):<6}"
            f"{('Y' if r['ok'] else 'N'):<4}"
            f"{('Y' if r['is_paid_required'] else '-'):<7}"
            f"{('Y' if r['filingDate_present'] else '-'):<5}"
            f"{('Y' if r['acceptedDate_present'] else '-'):<5}"
            f"{('Y' if r['calendarYear_or_period_present'] else '-'):<9}"
        )
        if r.get("error"):
            print(f"   error: {r['error'][:140]}")

    # Persist a JSON report
    out_dir = PROJECT_ROOT / "outputs" / "fmp_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"fmp_smoke_{stamp}.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "calls": CALL_COUNT,
        "results": RESULTS,
        "samples": SAMPLES,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nReport written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
