"""
Data Feasibility Probe — FMP Premium for 7-year historical backtest.

READ-ONLY DIAGNOSTIC. Does not modify rules, schema, adapters, or pipeline.
Reuses the existing FMP adapter (`src/data_adapters/fmp_adapter._api_call`),
so rate-limiter, sticky 429-pause, key redaction, and TTL cache stay engaged.

Hard budget: ≤ 20 raw FMP calls (counted via `get_call_group_summary`).

Probes:
  1. Historical Top Gainers — does `biggest-gainers` accept a `date` param?
  2. Delisted Ticker Historical Data (BBBY, SIVB, WEWK).
  3. Historical News Depth (FMP `news/stock-latest` / `news/stock`).
  4. PIT-Correct Fundamentals — `income-statement` field surface.

Output:
  - Prints a compact summary to stdout.
  - Writes raw evidence JSON to outputs/_inspector/probe_fmp_feasibility_raw.json
    (so the markdown report can quote excerpts).

Run:
    python scripts/probe_fmp_historical_feasibility.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Locate project root and load .env ─────────────────────────────
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    import os
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

# ── Import adapter primitives (do not modify) ─────────────────────
from data_adapters import fmp_adapter as fmp  # noqa: E402

OUT_DIR = ROOT / "outputs" / "_inspector"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_PATH = OUT_DIR / "probe_fmp_feasibility_raw.json"

# Probe-wide hard budget
MAX_CALLS = 20


def call_count() -> int:
    """Total raw FMP calls billed by the adapter so far."""
    return sum(fmp.get_call_group_summary().values())


def trim(d, n=600):
    """Return a JSON-serialisable, length-capped representation for raw evidence."""
    s = json.dumps(d, default=str)
    return s if len(s) <= n else s[:n] + "...<truncated>"


def small_keys(obj, limit=40):
    """Return list of keys for a dict (or first dict if list-of-dicts)."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return list(obj[0].keys())[:limit]
    if isinstance(obj, dict):
        return list(obj.keys())[:limit]
    return []


def first_n(rows, n=3):
    if isinstance(rows, list):
        return rows[:n]
    return rows


# ════════════════════════════════════════════════════════════════
# CAPABILITY 1: Historical Top Gainers
# ════════════════════════════════════════════════════════════════

def probe_capability_1():
    """biggest-gainers — does it accept a `date` parameter and return as-of?"""
    print("\n[1/4] CAPABILITY 1: Historical Top Gainers")
    print("-" * 60)

    findings = {
        "calls": [],
        "verdict": None,
        "notes": [],
    }

    # Call A: no date param (baseline — current snapshot)
    if call_count() >= MAX_CALLS:
        findings["verdict"] = "BUDGET_EXHAUSTED"
        return findings
    data_a, meta_a = fmp._api_call("biggest-gainers", group="probe_gainers_now")
    findings["calls"].append({
        "label": "no_date_param",
        "endpoint": "biggest-gainers",
        "params": {},
        "http_status": meta_a.get("http_status"),
        "ok": meta_a.get("ok"),
        "error_short": meta_a.get("error_short"),
        "row_count": len(data_a) if isinstance(data_a, list) else None,
        "first_row_keys": small_keys(data_a),
        "first_row_excerpt": first_n(data_a, 1) if isinstance(data_a, list) else data_a,
    })
    print(f"  A) no date     → HTTP {meta_a.get('http_status')} ok={meta_a.get('ok')} "
          f"rows={len(data_a) if isinstance(data_a, list) else 'n/a'}")

    # Call B: date=2024-06-17 (an arbitrary past trading day)
    if call_count() >= MAX_CALLS:
        findings["verdict"] = "BUDGET_EXHAUSTED"
        return findings
    test_date = "2024-06-17"
    data_b, meta_b = fmp._api_call("biggest-gainers",
                                    {"date": test_date},
                                    group="probe_gainers_dated")
    findings["calls"].append({
        "label": "date_param_2024-06-17",
        "endpoint": "biggest-gainers",
        "params": {"date": test_date},
        "http_status": meta_b.get("http_status"),
        "ok": meta_b.get("ok"),
        "error_short": meta_b.get("error_short"),
        "row_count": len(data_b) if isinstance(data_b, list) else None,
        "first_row_keys": small_keys(data_b),
        "first_row_excerpt": first_n(data_b, 2) if isinstance(data_b, list) else data_b,
    })
    print(f"  B) date={test_date} → HTTP {meta_b.get('http_status')} ok={meta_b.get('ok')} "
          f"rows={len(data_b) if isinstance(data_b, list) else 'n/a'}")

    # Compare: are A and B different snapshots?
    same_payload = False
    if isinstance(data_a, list) and isinstance(data_b, list) and data_a and data_b:
        sym_a = [r.get("symbol") for r in data_a[:5]]
        sym_b = [r.get("symbol") for r in data_b[:5]]
        same_payload = (sym_a == sym_b)
        findings["notes"].append(
            f"top-5 symbols: A={sym_a} | B={sym_b} | identical={same_payload}"
        )
        print(f"  → A top-5: {sym_a}")
        print(f"  → B top-5: {sym_b}")
        print(f"  → identical payload? {same_payload}")

    # Verdict logic:
    #  PASS:        date param accepted AND payload differs from current snapshot
    #  WORKAROUND:  date param ignored OR accepted but returns current snapshot
    #  FAIL:        endpoint errored entirely
    if not (meta_a.get("ok") and meta_b.get("ok")):
        findings["verdict"] = "FAIL"
        findings["notes"].append("at least one call failed; endpoint not usable")
    elif same_payload:
        findings["verdict"] = "WORKAROUND_NEEDED"
        findings["notes"].append(
            "date param appears IGNORED (same snapshot returned). "
            "FMP returns current top gainers regardless of date param. "
            "For 7-yr backtest: cannot pull historical top-gainers list directly."
        )
    else:
        findings["verdict"] = "PASS"
        findings["notes"].append(
            "Different payload across date params — historical top-gainers "
            "appear retrievable. Need to confirm semantics (as-of vs intraday)."
        )

    return findings


# ════════════════════════════════════════════════════════════════
# CAPABILITY 2: Delisted Ticker Historical Data
# ════════════════════════════════════════════════════════════════

DELISTED_TICKERS = [
    {"symbol": "BBBYQ",  "context": "Bed Bath & Beyond — Ch.11 Apr 2023, delisted"},
    {"symbol": "SIVBQ",  "context": "Silicon Valley Bank — failure Mar 2023"},
    {"symbol": "WEWKQ",  "context": "WeWork — Ch.11 Nov 2023"},
]
# Note: post-bankruptcy "Q" suffix tickers (BBBYQ / SIVBQ / WEWKQ) are how
# OTC pink sheets often re-list bankrupt issuers. We try these because the
# pre-bankruptcy NASDAQ tickers (BBBY/SIVB/WE) were halted.
# We will also try the un-suffixed legacy ticker as a fallback.


def probe_capability_2():
    """Delisted ticker historical price + active-trading flag."""
    print("\n[2/4] CAPABILITY 2: Delisted Ticker Historical Data")
    print("-" * 60)

    findings = {"per_ticker": [], "verdict": None, "notes": []}
    legacy_alts = {"BBBYQ": "BBBY", "SIVBQ": "SIVB", "WEWKQ": "WE"}

    for entry in DELISTED_TICKERS:
        sym = entry["symbol"]
        legacy = legacy_alts.get(sym)
        ticker_record = {
            "symbol_tried": sym,
            "context": entry["context"],
            "historical": None,
            "profile": None,
            "fallback_legacy": None,
        }

        # --- Historical EOD ---
        if call_count() >= MAX_CALLS:
            findings["verdict"] = "BUDGET_EXHAUSTED"
            findings["per_ticker"].append(ticker_record)
            return findings

        params_h = {"symbol": sym, "from": "2022-01-01", "to": "2024-01-01"}
        data_h, meta_h = fmp._api_call("historical-price-eod/full", params_h,
                                         group="probe_delisted_hist")
        rows = data_h if isinstance(data_h, list) else (
            data_h.get("historical") if isinstance(data_h, dict) else None
        )
        n_rows = len(rows) if isinstance(rows, list) else 0
        ticker_record["historical"] = {
            "endpoint": "historical-price-eod/full",
            "params": params_h,
            "http_status": meta_h.get("http_status"),
            "ok": meta_h.get("ok"),
            "row_count": n_rows,
            "first_row": rows[0] if n_rows else None,
            "last_row":  rows[-1] if n_rows else None,
            "first_date": rows[0].get("date") if n_rows else None,
            "last_date": rows[-1].get("date") if n_rows else None,
        }
        print(f"  {sym}: hist HTTP {meta_h.get('http_status')} ok={meta_h.get('ok')} rows={n_rows}")

        # If primary symbol returned 0 rows AND we have a legacy alt, try it.
        if n_rows == 0 and legacy and call_count() < MAX_CALLS:
            params_h2 = {"symbol": legacy, "from": "2022-01-01", "to": "2024-01-01"}
            data_h2, meta_h2 = fmp._api_call("historical-price-eod/full", params_h2,
                                              group="probe_delisted_hist_legacy")
            rows2 = data_h2 if isinstance(data_h2, list) else (
                data_h2.get("historical") if isinstance(data_h2, dict) else None
            )
            n2 = len(rows2) if isinstance(rows2, list) else 0
            ticker_record["fallback_legacy"] = {
                "symbol_tried": legacy,
                "endpoint": "historical-price-eod/full",
                "params": params_h2,
                "http_status": meta_h2.get("http_status"),
                "ok": meta_h2.get("ok"),
                "row_count": n2,
                "first_date": rows2[0].get("date") if n2 else None,
                "last_date": rows2[-1].get("date") if n2 else None,
                "last_row": rows2[-1] if n2 else None,
            }
            print(f"  {sym} ↘ legacy {legacy}: hist HTTP {meta_h2.get('http_status')} "
                  f"ok={meta_h2.get('ok')} rows={n2}")

        findings["per_ticker"].append(ticker_record)

    # --- Profile (only one shared call to limit budget): use first delisted ---
    # We probe profile of one ticker to inspect whether `isActivelyTrading` is exposed.
    if call_count() < MAX_CALLS:
        probe_profile_sym = (
            findings["per_ticker"][0].get("fallback_legacy", {}).get("symbol_tried")
            or findings["per_ticker"][0]["symbol_tried"]
        )
        data_p, meta_p = fmp._api_call("profile", {"symbol": probe_profile_sym},
                                         group="probe_delisted_profile")
        is_actively_trading = None
        keys_seen = []
        first_row = None
        if meta_p.get("ok") and isinstance(data_p, list) and data_p:
            row = data_p[0]
            keys_seen = list(row.keys())[:40]
            is_actively_trading = row.get("isActivelyTrading")
            # keep a small excerpt
            first_row = {k: row.get(k) for k in (
                "symbol", "companyName", "isActivelyTrading", "isAdr",
                "isFund", "isEtf", "exchange", "ipoDate", "delistedDate"
            )}
        findings["profile_probe"] = {
            "symbol_tried": probe_profile_sym,
            "http_status": meta_p.get("http_status"),
            "ok": meta_p.get("ok"),
            "is_actively_trading": is_actively_trading,
            "keys_seen": keys_seen,
            "first_row_excerpt": first_row,
        }
        print(f"  profile({probe_profile_sym}): HTTP {meta_p.get('http_status')} "
              f"isActivelyTrading={is_actively_trading}")

    # Verdict logic:
    successes = []
    for t in findings["per_ticker"]:
        h = t.get("historical") or {}
        legacy = t.get("fallback_legacy") or {}
        if h.get("row_count", 0) > 0 or legacy.get("row_count", 0) > 0:
            successes.append(t["symbol_tried"])
    if len(successes) == len(DELISTED_TICKERS):
        findings["verdict"] = "PASS"
    elif len(successes) > 0:
        findings["verdict"] = "PARTIAL"
    else:
        findings["verdict"] = "FAIL"
    findings["notes"].append(f"successful tickers: {successes} / {len(DELISTED_TICKERS)}")

    return findings


# ════════════════════════════════════════════════════════════════
# CAPABILITY 3: Historical News Depth
# ════════════════════════════════════════════════════════════════

def probe_capability_3():
    """How far back does FMP `news/stock-latest` / `news/stock` go for AAPL?"""
    print("\n[3/4] CAPABILITY 3: Historical News Depth")
    print("-" * 60)

    findings = {"calls": [], "verdict": None, "notes": []}

    # Call 1: latest news, default page (sanity-check the endpoint exists)
    if call_count() >= MAX_CALLS:
        findings["verdict"] = "BUDGET_EXHAUSTED"
        return findings
    data1, meta1 = fmp._api_call("news/stock-latest",
                                   {"symbols": "AAPL", "limit": 5},
                                   group="probe_news_latest")
    findings["calls"].append({
        "label": "news_latest_default",
        "endpoint": "news/stock-latest",
        "params": {"symbols": "AAPL", "limit": 5},
        "http_status": meta1.get("http_status"),
        "ok": meta1.get("ok"),
        "error_short": meta1.get("error_short"),
        "row_count": len(data1) if isinstance(data1, list) else None,
        "first_row_keys": small_keys(data1),
        "first_row_excerpt": data1[0] if isinstance(data1, list) and data1 else data1,
    })
    print(f"  A) news/stock-latest         → HTTP {meta1.get('http_status')} ok={meta1.get('ok')}")

    # Call 2: dated news pull, attempt deep history (2018-01-01 → 2018-03-01)
    if call_count() >= MAX_CALLS:
        findings["verdict"] = "BUDGET_EXHAUSTED"
        return findings
    data2, meta2 = fmp._api_call("news/stock",
                                   {"symbols": "AAPL",
                                    "from": "2018-01-01",
                                    "to":   "2018-03-01",
                                    "limit": 5},
                                   group="probe_news_2018")
    findings["calls"].append({
        "label": "news_search_2018",
        "endpoint": "news/stock",
        "params": {"symbols": "AAPL", "from": "2018-01-01", "to": "2018-03-01", "limit": 5},
        "http_status": meta2.get("http_status"),
        "ok": meta2.get("ok"),
        "error_short": meta2.get("error_short"),
        "row_count": len(data2) if isinstance(data2, list) else None,
        "first_row_keys": small_keys(data2),
        "first_excerpt": data2[0] if isinstance(data2, list) and data2 else data2,
        "last_excerpt": data2[-1] if isinstance(data2, list) and len(data2) > 1 else None,
    })
    print(f"  B) news/stock 2018-01..03    → HTTP {meta2.get('http_status')} ok={meta2.get('ok')} "
          f"rows={len(data2) if isinstance(data2, list) else 'n/a'}")

    # Call 3: probe even deeper (2015) to estimate floor
    if call_count() >= MAX_CALLS:
        findings["verdict"] = "BUDGET_EXHAUSTED"
        return findings
    data3, meta3 = fmp._api_call("news/stock",
                                   {"symbols": "AAPL",
                                    "from": "2015-01-01",
                                    "to":   "2015-03-01",
                                    "limit": 5},
                                   group="probe_news_2015")
    findings["calls"].append({
        "label": "news_search_2015",
        "endpoint": "news/stock",
        "params": {"symbols": "AAPL", "from": "2015-01-01", "to": "2015-03-01", "limit": 5},
        "http_status": meta3.get("http_status"),
        "ok": meta3.get("ok"),
        "error_short": meta3.get("error_short"),
        "row_count": len(data3) if isinstance(data3, list) else None,
        "first_row_keys": small_keys(data3),
        "first_excerpt": data3[0] if isinstance(data3, list) and data3 else data3,
        "last_excerpt": data3[-1] if isinstance(data3, list) and len(data3) > 1 else None,
    })
    print(f"  C) news/stock 2015-01..03    → HTTP {meta3.get('http_status')} ok={meta3.get('ok')} "
          f"rows={len(data3) if isinstance(data3, list) else 'n/a'}")

    # Verdict logic
    def first_date(data):
        if isinstance(data, list) and data:
            return data[-1].get("publishedDate") or data[-1].get("date") or data[-1].get("publishedAt")
        return None

    earliest_2018 = first_date(data2)
    earliest_2015 = first_date(data3)

    has_2018 = isinstance(data2, list) and len(data2) > 0
    has_2015 = isinstance(data3, list) and len(data3) > 0

    if not meta1.get("ok"):
        findings["verdict"] = "FAIL"
        findings["notes"].append("news endpoint inaccessible / not on plan")
    elif has_2015:
        findings["verdict"] = "PASS"
        findings["notes"].append("news returned for 2015 → coverage ≥ ~10 years")
    elif has_2018:
        findings["verdict"] = "PARTIAL"
        findings["notes"].append("news returned for 2018 but NOT 2015 → coverage roughly 7-10 years")
    else:
        findings["verdict"] = "PARTIAL"
        findings["notes"].append("news endpoint exists but historical depth is shallow; need GDELT fallback")

    findings["earliest_returned_2018_window"] = earliest_2018
    findings["earliest_returned_2015_window"] = earliest_2015
    return findings


# ════════════════════════════════════════════════════════════════
# CAPABILITY 4: PIT-Correct Fundamentals
# ════════════════════════════════════════════════════════════════

PIT_FIELDS_OF_INTEREST = (
    "date", "fiscalYear", "period",
    "filingDate", "acceptedDate", "acceptedDatetime",
    "calendarYear", "reportedCurrency", "cik", "symbol",
)


def probe_capability_4():
    """income-statement: do rows carry filingDate / acceptedDate?"""
    print("\n[4/4] CAPABILITY 4: PIT-Correct Fundamentals")
    print("-" * 60)

    findings = {"calls": [], "verdict": None, "notes": []}

    if call_count() >= MAX_CALLS:
        findings["verdict"] = "BUDGET_EXHAUSTED"
        return findings

    data, meta = fmp._api_call("income-statement",
                                {"symbol": "AAPL", "period": "quarter", "limit": 8},
                                group="probe_pit_income")

    field_summary = {}
    rows_excerpt = []
    if meta.get("ok") and isinstance(data, list) and data:
        # Aggregate field presence across all 8 rows
        all_keys = set()
        for r in data:
            all_keys.update(r.keys())
        # Mark which PIT fields show up
        for f in PIT_FIELDS_OF_INTEREST:
            field_summary[f] = (f in all_keys)
        # Compact excerpt: per-row PIT-relevant fields only
        for r in data:
            rows_excerpt.append({f: r.get(f) for f in PIT_FIELDS_OF_INTEREST if f in r})
        # Add a sample full-keys list (one row, alphabetised, capped)
        sample_keys = sorted(list(data[0].keys()))[:60]
    else:
        sample_keys = []

    findings["calls"].append({
        "endpoint": "income-statement",
        "params": {"symbol": "AAPL", "period": "quarter", "limit": 8},
        "http_status": meta.get("http_status"),
        "ok": meta.get("ok"),
        "error_short": meta.get("error_short"),
        "row_count": len(data) if isinstance(data, list) else None,
        "pit_field_presence": field_summary,
        "all_row_pit_excerpts": rows_excerpt,
        "sample_full_keys": sample_keys,
    })

    print(f"  income-statement(AAPL,q,8) → HTTP {meta.get('http_status')} ok={meta.get('ok')} "
          f"rows={len(data) if isinstance(data, list) else 'n/a'}")
    print(f"  PIT field presence: {field_summary}")

    # Verdict logic — require date + filingDate + (acceptedDate OR acceptedDatetime)
    has_date = field_summary.get("date", False)
    has_filing = field_summary.get("filingDate", False)
    has_accepted = (field_summary.get("acceptedDate", False)
                    or field_summary.get("acceptedDatetime", False))
    if has_date and has_filing and has_accepted:
        findings["verdict"] = "PASS"
        findings["notes"].append(
            "income-statement rows carry date (period end) + filingDate + acceptedDate; "
            "PIT reconstruction is feasible."
        )
    elif has_date and (has_filing or has_accepted):
        findings["verdict"] = "PARTIAL"
        missing = [k for k, v in field_summary.items() if k in ("filingDate", "acceptedDate", "acceptedDatetime") and not v]
        findings["notes"].append(f"missing PIT fields: {missing}")
    else:
        findings["verdict"] = "FAIL"
        findings["notes"].append("essential PIT date fields missing")

    return findings


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    started_at = datetime.now(timezone.utc).isoformat()

    fp_meta = fmp.get_key_fingerprint()
    print("=" * 60)
    print("FMP Historical Feasibility Probe — read-only diagnostic")
    print("=" * 60)
    print(f"Started:    {started_at}")
    print(f"Key set:    {fp_meta.get('key_set')}")
    print(f"Key length: {fp_meta.get('key_length')}")
    print(f"Key fingerprint (sha256[:8]): {fp_meta.get('key_fingerprint')}")
    print(f"Hard cap:   {MAX_CALLS} raw FMP calls")

    cap1 = probe_capability_1()
    cap2 = probe_capability_2()
    cap3 = probe_capability_3()
    cap4 = probe_capability_4()

    finished_at = datetime.now(timezone.utc).isoformat()
    total_calls = call_count()
    by_group = fmp.get_call_group_summary()
    cache_stats = fmp.get_cache_stats()
    rate_status = fmp.get_rate_limit_status()

    summary = {
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "key_meta": fp_meta,
        "max_calls_budget": MAX_CALLS,
        "total_calls_made": total_calls,
        "calls_by_group": by_group,
        "cache_stats": cache_stats,
        "rate_status": rate_status,
        "capability_1_top_gainers": cap1,
        "capability_2_delisted":    cap2,
        "capability_3_news":        cap3,
        "capability_4_pit_fundamentals": cap4,
        "verdicts": {
            "1_top_gainers": cap1.get("verdict"),
            "2_delisted":    cap2.get("verdict"),
            "3_news":        cap3.get("verdict"),
            "4_pit_fundamentals": cap4.get("verdict"),
        },
    }

    RAW_PATH.write_text(json.dumps(summary, indent=2, default=str))

    print("\n" + "=" * 60)
    print("PROBE SUMMARY")
    print("=" * 60)
    for k, v in summary["verdicts"].items():
        print(f"  {k:30s} = {v}")
    print(f"\n  Total raw FMP calls: {total_calls} / {MAX_CALLS}")
    print(f"  Per-group: {by_group}")
    print(f"  Cache stats: {cache_stats}")
    print(f"  Sticky pause active: {rate_status.get('sticky_paused')}")
    print(f"\n  Raw evidence written to: {RAW_PATH}")


if __name__ == "__main__":
    main()
