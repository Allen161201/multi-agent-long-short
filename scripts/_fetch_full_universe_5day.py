"""
Full-universe historical OHLC fetch for the 5-day replay window
(2026-04-27 .. 2026-05-01). Populates the disk cache at
data/cache/historical_prices/<ticker>.json so the replay script can
read prices without hitting FMP at decision time.

Universe: active rows from data/universe/universe_master_v1.json with
§2 exclusions applied (drop 0P prefix mutual-fund proxies; drop
companyName non-equity regex; drop symbol non-equity suffix regex).

Concurrency: ThreadPoolExecutor(max_workers=10). The aggregate request
rate is throttled by fmp_adapter._MinuteRateLimiter (600/min).
Self-abort if error_rate > 5% after 200 tickers complete.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from src.data_adapters import fmp_adapter as fmp

WINDOW_START = "2026-04-27"
WINDOW_END = "2026-05-01"
MAX_WORKERS = 10
ABORT_AFTER = 200
ABORT_ERROR_RATE = 0.05
PROGRESS_EVERY = 1000

UNIVERSE_PATH = ROOT / "data" / "universe" / "universe_master_v1.json"
CACHE_DIR = ROOT / "data" / "cache" / "historical_prices"
SUMMARY_PATH = CACHE_DIR / "_run_summary.json"

# Same regexes used by build_universe_v1.py.
_NAME_NON_EQUITY = re.compile(
    r"\b("
    r"ETF|ETN|Trust|Fund|Fd|"
    r"Income\s+Strategy|Daily\s+Target|"
    r"Bull\s+\dX|Bear\s+\dX|"
    r"\dX\s+(Long|Short|Inverse|Bull|Bear)|"
    r"(Long|Short|Inverse|Bull|Bear)\s+\dX|"
    r"Leveraged|Note(s)?|Preferred|Warrants?|Rights"
    r")\b",
    re.IGNORECASE,
)
_SYMBOL_SUFFIX_NON_EQUITY = re.compile(
    r"[.-](WS|PR[A-Z]?|U|W|R|UN|RT|PFD|"
    r"PP|PQ|PA|PB|PC|PD|PE|PF|PG|PH|PI|PJ|PK|PL|"
    r"PM|PN|PR|PS|PT|PU|PV|PW|PX|PY|PZ)$",
    re.IGNORECASE,
)


def load_universe() -> list[str]:
    """Active rows + §2 exclusions, return symbol list (deduped, sorted)."""
    with UNIVERSE_PATH.open() as f:
        data = json.load(f)
    rows = [r for r in data["tickers"] if r.get("status") == "active"]
    syms: set[str] = set()
    drop_0p = 0
    drop_name = 0
    drop_suffix = 0
    for r in rows:
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        if sym.startswith("0P"):
            drop_0p += 1
            continue
        name = r.get("company_name") or ""
        if _NAME_NON_EQUITY.search(name):
            drop_name += 1
            continue
        if _SYMBOL_SUFFIX_NON_EQUITY.search(sym):
            drop_suffix += 1
            continue
        syms.add(sym)
    print(f"[universe] active={len(rows)} drop_0P={drop_0p} "
          f"drop_name={drop_name} drop_suffix={drop_suffix} "
          f"final={len(syms)}", flush=True)
    return sorted(syms)


def fetch_one(ticker: str) -> tuple[str, int, str | None]:
    try:
        rows = fmp.get_historical_daily(ticker, WINDOW_START, WINDOW_END)
        return ticker, len(rows), None
    except Exception as e:
        return ticker, 0, f"{type(e).__name__}: {e}"


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    universe = load_universe()
    n = len(universe)
    print(f"[start] window={WINDOW_START}..{WINDOW_END} tickers={n} "
          f"workers={MAX_WORKERS}", flush=True)

    t0 = time.time()
    done = 0
    n_ok = 0
    n_empty = 0
    n_err = 0
    errors: list[dict] = []
    aborted = False

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_one, t): t for t in universe}
        for fut in as_completed(futures):
            ticker, n_rows, err = fut.result()
            done += 1
            if err is not None:
                n_err += 1
                if len(errors) < 50:
                    errors.append({"ticker": ticker, "error": err})
            elif n_rows == 0:
                n_empty += 1
            else:
                n_ok += 1

            if done % PROGRESS_EVERY == 0:
                stats = fmp.get_historical_cache_stats()
                rate = (time.time() - t0) / done
                eta = rate * (n - done)
                print(f"[progress] done={done}/{n} ok={n_ok} empty={n_empty} "
                      f"err={n_err} cache_hits={stats['hits']} "
                      f"cache_misses={stats['misses']} "
                      f"elapsed={time.time()-t0:.0f}s eta={eta:.0f}s",
                      flush=True)

            if done >= ABORT_AFTER:
                er = n_err / done
                if er > ABORT_ERROR_RATE:
                    print(f"[ABORT] error_rate={er:.2%} > {ABORT_ERROR_RATE:.0%} "
                          f"at done={done}", flush=True)
                    aborted = True
                    for f in futures:
                        f.cancel()
                    break

    elapsed = time.time() - t0
    stats = fmp.get_historical_cache_stats()

    cache_files = list(CACHE_DIR.glob("*.json"))
    total_bytes = sum(p.stat().st_size for p in cache_files)

    summary = {
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "completed_at": datetime.utcnow().isoformat() + "Z",
        "universe_count": n,
        "done": done,
        "ok": n_ok,
        "empty": n_empty,
        "errors": n_err,
        "error_rate": n_err / done if done else 0.0,
        "aborted": aborted,
        "wall_seconds": round(elapsed, 1),
        "cache_hits": stats["hits"],
        "cache_misses": stats["misses"],
        "cache_files": len(cache_files),
        "cache_total_bytes": total_bytes,
        "first_50_errors": errors,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2))
    print(f"[done] done={done}/{n} ok={n_ok} empty={n_empty} err={n_err} "
          f"aborted={aborted} elapsed={elapsed:.0f}s", flush=True)
    print(f"[summary] {SUMMARY_PATH}", flush=True)
    return 1 if aborted else 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 2
    sys.exit(rc)
