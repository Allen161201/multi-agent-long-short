"""
Validate the rewritten FMP adapter through the production market_data
routing layer (NOT through a separate smoke-test script).

Confirms each call returns source='live_fmp' and shows real data, and
verifies that no API key fragment appears in any logger output we route
through here.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Capture logger output to verify nothing sensitive is logged.
log_buf = io.StringIO()
log_handler = logging.StreamHandler(log_buf)
log_handler.setLevel(logging.DEBUG)
log_fmt = logging.Formatter("%(levelname)s %(name)s :: %(message)s")
log_handler.setFormatter(log_fmt)
logging.getLogger().addHandler(log_handler)
logging.getLogger().setLevel(logging.DEBUG)

from src.data_adapters import market_data, fmp_adapter  # noqa: E402

API_KEY = os.environ.get("FMP_API_KEY", "")
KEY_LEN = len(API_KEY)

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = PASS if ok else FAIL
    results.append((status, name, detail))
    flag = "[OK]" if ok else "[XX]"
    print(f"  {flag} {name}{(' — ' + detail) if detail else ''}")


def main() -> int:
    print("=" * 70)
    print("PRODUCTION-PATH CHECK — rewritten FMP adapter")
    print(f"DATA_MODE=live, USE_MOCK_DATA=false, API key length={KEY_LEN}")
    print("=" * 70)

    # 1. test_connection
    print("\n1. Connection probe (market_data.get_api_status)")
    status = market_data.get_api_status()
    check("api status active_source == live_fmp",
          status.get("active_source") == "live_fmp",
          f"got {status.get('active_source')}")
    check("connection_test.connected == True",
          status.get("connection_test", {}).get("connected") is True,
          str(status.get("connection_test", {}).get("test_result")))

    # 2. Quote AAPL
    print("\n2. market_data.get_quote('AAPL')")
    qa = market_data.get_quote("AAPL")
    check("quote source == live_fmp", qa.get("source") == "live_fmp",
          f"source={qa.get('source')}")
    check("quote price is numeric > 0",
          isinstance(qa.get("price"), (int, float)) and qa["price"] > 0,
          f"price={qa.get('price')}")
    print(f"  AAPL price: {qa.get('price')} change={qa.get('change_pct')}%")

    # 3. Quote UBER
    print("\n3. market_data.get_quote('UBER')")
    qu = market_data.get_quote("UBER")
    check("quote source == live_fmp", qu.get("source") == "live_fmp")
    check("quote price is numeric > 0",
          isinstance(qu.get("price"), (int, float)) and qu["price"] > 0,
          f"price={qu.get('price')}")

    # 4. Profile
    print("\n4. market_data.get_company_profile('AAPL')")
    pa = market_data.get_company_profile("AAPL")
    check("profile source == live_fmp", pa.get("source") == "live_fmp")
    check("profile sector populated", bool(pa.get("sector")) and pa["sector"] != "Data unavailable")
    check("profile market_cap > 0",
          isinstance(pa.get("market_cap"), (int, float)) and pa["market_cap"] > 0)

    # 5. Historical OHLCV bounded (5 days)
    print("\n5. market_data.get_historical_ohlcv('AAPL', days=5)")
    h = market_data.get_historical_ohlcv("AAPL", days=5)
    check("historical returns 1..15 rows (bounded)",
          1 <= len(h) <= 15, f"rows={len(h)}")
    check("historical row source == live_fmp",
          all(r.get("source") == "live_fmp" for r in h),
          "all rows tagged")

    # 6. Fundamentals composed
    print("\n6. market_data.get_fundamentals('AAPL')")
    f = market_data.get_fundamentals("AAPL")
    check("fundamentals source == live_fmp", f.get("source") == "live_fmp")
    check("filing_date is YYYY-MM-DD",
          isinstance(f.get("filing_date"), str)
          and re.match(r"^\d{4}-\d{2}-\d{2}$", f["filing_date"]) is not None,
          f"filing_date={f.get('filing_date')}")
    check("revenue_ttm > 0",
          isinstance(f.get("revenue_ttm"), (int, float))
          and f["revenue_ttm"] > 0,
          f"revenue_ttm={f.get('revenue_ttm'):,}")
    check("pe_ratio populated",
          isinstance(f.get("pe_ratio"), (int, float)),
          f"pe={f.get('pe_ratio')}")

    # 7. Top gainers (live)
    print("\n7. market_data.get_top_gainers()")
    g = market_data.get_top_gainers()
    check("gainers list non-empty", isinstance(g, list) and len(g) > 0,
          f"count={len(g) if isinstance(g, list) else 'n/a'}")
    if isinstance(g, list) and g:
        check("top gainer source == live_fmp",
              g[0].get("source") == "live_fmp",
              f"first ticker={g[0].get('ticker')} price={g[0].get('close')}")

    # 8. Negative path: a deliberately bogus symbol must NOT look successful
    print("\n8. Negative path — bogus symbol")
    bogus = market_data.get_company_profile("ZZZZZZZZ_BOGUS")
    check("bogus profile source != live_fmp",
          bogus.get("source") != "live_fmp",
          f"got source={bogus.get('source')} status={bogus.get('status')}")

    # 9. Log sanitization — assert API key never appears in logger output
    print("\n9. Log-output sanitization")
    log_text = log_buf.getvalue()
    leaks = []
    if API_KEY and API_KEY in log_text:
        leaks.append("raw_apikey")
    if "apikey=" in log_text:
        # Allowed only if value after = is REDACTED
        for m in re.finditer(r"apikey=([^&\s]+)", log_text, re.IGNORECASE):
            if m.group(1) != "REDACTED":
                leaks.append(f"unredacted:{m.group(1)[:6]}…")
    check("no API key in log output",
          not leaks,
          ", ".join(leaks) if leaks else "all redacted")
    check("no apikey appears in url field of any logged exception",
          "for url:" not in log_text,
          "raise_for_status format absent")

    # ── summary ──
    failed = [r for r in results if r[0] == FAIL]
    print("\n" + "=" * 70)
    print(f"{len(results) - len(failed)} pass / {len(failed)} fail")
    print("=" * 70)
    if failed:
        print("FAILED checks:")
        for _, n, d in failed:
            print(f"  - {n}: {d}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
