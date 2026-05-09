"""
Tiny probe of new /stable endpoints we plan to use under FMP Premium.
Validates exact path + response shape so the adapter doesn't make
multiple wrong-path calls. Total calls: ~6.

Probes (AAPL only):
  - historical-chart/5min
  - technical-indicators/sma
  - earnings   (or earnings-calendar)
  - dividends  (or dividends-calendar)
  - splits     (or splits-calendar)
  - discounted-cash-flow
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
KEY = os.environ.get("FMP_API_KEY", "").strip()
STABLE = "https://financialmodelingprep.com/stable"

CALLS = 0
results = []


def call(label: str, path: str, params: dict | None = None) -> dict:
    global CALLS
    CALLS += 1
    p = dict(params or {})
    p["apikey"] = KEY
    t0 = time.time()
    try:
        r = requests.get(f"{STABLE}/{path}", params=p, timeout=15)
    except Exception as e:
        rec = {"label": label, "ok": False, "error": type(e).__name__,
               "elapsed_ms": int((time.time() - t0) * 1000)}
        results.append(rec)
        return rec
    rec: dict = {
        "label": label, "path": path,
        "params_no_key": [k for k in p if k != "apikey"],
        "http": r.status_code,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "ok": False,
        "shape": None,
        "fields": [],
        "sample": None,
        "error": None,
    }
    if r.status_code != 200:
        try:
            j = r.json()
            rec["error"] = (j.get("Error Message") or j.get("error")
                            or r.text[:200])
        except Exception:
            rec["error"] = r.text[:200]
        results.append(rec)
        return rec
    try:
        data = r.json()
    except ValueError:
        rec["error"] = "non-JSON"
        results.append(rec)
        return rec
    rec["ok"] = True
    if isinstance(data, list):
        rec["shape"] = f"list[{len(data)}]"
        if data and isinstance(data[0], dict):
            rec["fields"] = list(data[0].keys())[:30]
            rec["sample"] = data[0]
    elif isinstance(data, dict):
        rec["shape"] = "dict"
        rec["fields"] = list(data.keys())[:30]
        rec["sample"] = data
    else:
        rec["shape"] = type(data).__name__
    results.append(rec)
    return rec


def main() -> int:
    if not KEY:
        print("FMP_API_KEY missing")
        return 2

    print("Probing /stable endpoints (AAPL, ~6 calls)…")
    call("intraday_5min",       "historical-chart/5min", {"symbol": "AAPL"})
    call("technical_sma",       "technical-indicators/sma",
         {"symbol": "AAPL", "periodLength": 10, "timeframe": "1day"})
    # Calendar endpoints — try the unprefixed forms first
    call("earnings",            "earnings",        {"symbol": "AAPL"})
    call("dividends",           "dividends",       {"symbol": "AAPL"})
    call("splits",              "splits",          {"symbol": "AAPL"})
    call("dcf",                 "discounted-cash-flow", {"symbol": "AAPL"})

    print(f"\nTotal calls: {CALLS}\n")
    print(f"{'#':<3}{'label':<22}{'path':<40}{'http':<5}{'shape':<14}ok")
    print("-" * 100)
    for i, r in enumerate(results, 1):
        print(
            f"{i:<3}{r['label']:<22}{r.get('path',''):<40}"
            f"{str(r.get('http')):<5}{str(r.get('shape')):<14}"
            f"{('Y' if r['ok'] else 'N')}"
        )
        if r.get("error"):
            print(f"     err: {str(r['error'])[:140]}")
        if r["ok"] and r.get("fields"):
            print(f"     fields: {r['fields'][:14]}{'…' if len(r['fields'])>14 else ''}")

    out = ROOT / "outputs" / "fmp_smoke" / "premium_endpoint_probe.json"
    out.write_text(json.dumps({"calls": CALLS, "results": results}, indent=2,
                              default=str))
    print(f"\nSaved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
