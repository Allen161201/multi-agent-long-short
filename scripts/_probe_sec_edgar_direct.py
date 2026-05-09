"""Probe the live SEC EDGAR adapters (sec_edgar / sec_form4 / sec_13f /
sec_def14a) — these are the *actual* paths the evidence packet uses.

Confirms Task A. The diagnostic that flagged sec_form4/13f/8k as INTERFACE_GAP
was probing legacy FMP endpoints (insider-trading / institutional-holder /
sec-filings) that are not on the user's plan; the real adapters in
src/adapters/alt_data/ already fetch from data.sec.gov directly with proper
acceptedDateTime PIT discipline. This script proves they return real rows
on AAPL/NVDA/TSLA in the 2026-04-01..2026-05-01 window.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

# Force live-attempt path; the adapters still fall back to stub on
# missing credentials, so we want zero ambiguity.
os.environ["STUB_MODE"] = "false"

CUTOFF = datetime(2026, 4, 30, 16, 15, tzinfo=timezone.utc)
TICKERS = ("AAPL", "NVDA", "TSLA")


def _probe(adapter_cls, ticker: str) -> dict:
    adapter = adapter_cls()
    res = adapter.fetch(
        ticker=ticker, as_of=CUTOFF, decision_timestamp=CUTOFF, stub_mode=False,
    )
    accepted_max = None
    accepted_min = None
    for r in res.rows:
        ad = r.get("accepted_datetime") or r.get("as_of")
        if not ad:
            continue
        if accepted_max is None or ad > accepted_max:
            accepted_max = ad
        if accepted_min is None or ad < accepted_min:
            accepted_min = ad
    pit_violations = []
    for r in res.rows[:5]:
        ad = r.get("accepted_datetime") or r.get("as_of")
        if ad and ad > CUTOFF.isoformat():
            pit_violations.append(ad)
    return {
        "source_id": adapter.source_id,
        "extraction_status": res.extraction_status,
        "source_flag": res.source_flag,
        "rows_returned": len(res.rows),
        "error_class": res.error_class,
        "accepted_min": accepted_min,
        "accepted_max": accepted_max,
        "pit_violations_in_first_5": pit_violations,
        "first_row": res.rows[0] if res.rows else None,
    }


def main() -> int:
    from src.adapters.alt_data.sec_edgar import SECEdgarAdapter
    from src.adapters.alt_data.sec_ownership import (
        SECForm4Adapter, SEC13FAdapter, SECDef14AAdapter,
    )

    print("=== Task A — SEC EDGAR direct adapter probe ===")
    print(f"  cutoff: {CUTOFF.isoformat()}")
    print(f"  user-agent (env SEC_EDGAR_USER_AGENT): "
          f"{'SET' if os.environ.get('SEC_EDGAR_USER_AGENT') else 'UNSET'}")
    print(f"  fmp key: "
          f"{'SET' if os.environ.get('FMP_API_KEY') else 'UNSET'}")

    out: dict = {"cutoff": CUTOFF.isoformat(), "results": {}}

    adapters = [
        ("sec_edgar (8-K)", SECEdgarAdapter),
        ("sec_form4",       SECForm4Adapter),
        ("sec_13f",         SEC13FAdapter),
        ("sec_def14a",      SECDef14AAdapter),
    ]

    for label, cls in adapters:
        print(f"\n  --- {label} ---")
        per_source: dict = {}
        for t in TICKERS:
            r = _probe(cls, t)
            per_source[t] = r
            flag = ("LIVE" if r["extraction_status"] == "ok" and r["rows_returned"] > 0
                     else "EMPTY" if r["extraction_status"] == "ok"
                     else "FAIL")
            print(f"    {flag:5s}  {t:6s}  rows={r['rows_returned']:4d}  "
                  f"flag={r['source_flag']!s:30s}  "
                  f"accepted_max={r['accepted_max']}")
            if r["error_class"]:
                print(f"            error_class={r['error_class']}")
            if r["pit_violations_in_first_5"]:
                print(f"            PIT VIOLATIONS: {r['pit_violations_in_first_5']}")
        out["results"][label] = per_source

    # Verdict
    print("\n  === VERDICT ===")
    for label, _ in adapters:
        live_count = sum(
            1 for t in TICKERS
            if out["results"][label][t]["extraction_status"] == "ok"
            and out["results"][label][t]["rows_returned"] > 0
        )
        print(f"  {label:25s}  LIVE for {live_count}/{len(TICKERS)} tickers")

    out_path = ROOT / "data" / "altdata" / "_sec_edgar_direct_probe.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n  saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
