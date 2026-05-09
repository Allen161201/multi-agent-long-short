"""
B.1 — Endpoint reconnaissance for historical daily-gainers reconstruction.

Read-only diagnostic. Probes plausible FMP /stable surfaces that COULD
return cross-sectional / batch / bulk historical EOD data for one date,
so we can compare strategies for reconstructing daily top-gainers across
the universe without melting the API budget.

Hard budget: ≤ 8 raw FMP calls.

For every probe call we record:
  - the exact path + params used
  - HTTP status, error short
  - top-level shape (list-vs-dict, row count, sample keys)
  - first row excerpt (only descriptive — no API key)

Result is written to outputs/_inspector/probe_batch_historical_endpoints.json
and printed to stdout.

This script does NOT build the reconstruction itself.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

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
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from data_adapters import fmp_adapter as fmp  # noqa: E402

OUT_DIR = ROOT / "outputs" / "_inspector"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "probe_batch_historical_endpoints.json"

MAX_CALLS = 8
TEST_DATE = "2024-06-17"   # arbitrary historical trading day
TEST_FROM = "2024-06-17"
TEST_TO   = "2024-06-17"


def call_count() -> int:
    return sum(fmp.get_call_group_summary().values())


def probe(label: str, path: str, params: dict | None, group: str) -> dict:
    if call_count() >= MAX_CALLS:
        return {"label": label, "status": "BUDGET_EXHAUSTED",
                "path": path, "params": params}
    data, meta = fmp._api_call(path, params, group=group)
    shape = "unknown"
    row_count = None
    sample_keys: list[str] = []
    first_row_excerpt = None
    if isinstance(data, list):
        shape = "list"
        row_count = len(data)
        if data and isinstance(data[0], dict):
            sample_keys = sorted(list(data[0].keys()))
            first_row_excerpt = data[0]
    elif isinstance(data, dict):
        shape = "dict"
        sample_keys = sorted(list(data.keys()))
        first_row_excerpt = {k: data.get(k) for k in sample_keys[:8]}
    elif data is None:
        shape = "none"

    return {
        "label": label,
        "path": path,
        "params": params,
        "http_status": meta.get("http_status"),
        "ok": meta.get("ok"),
        "error_short": meta.get("error_short"),
        "shape": shape,
        "row_count": row_count,
        "sample_keys": sample_keys[:30],
        "first_row_excerpt": first_row_excerpt,
    }


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    print("=" * 60)
    print("B.1 Endpoint Reconnaissance — historical batch / bulk surfaces")
    print("=" * 60)
    print(f"Started: {started_at}")
    print(f"Budget : ≤ {MAX_CALLS} raw FMP calls\n")

    probes: list[dict] = []

    # 1) Multi-symbol comma list on the per-symbol historical EOD endpoint.
    #    If FMP /stable accepts comma-joined symbols, ONE call could return
    #    many tickers' history. This is the single highest-leverage thing
    #    to confirm.
    probes.append(probe(
        "historical-price-eod/full multi-symbol",
        "historical-price-eod/full",
        {"symbol": "AAPL,MSFT,NVDA",
         "from": TEST_FROM, "to": TEST_TO},
        "probe_eod_multi_symbol",
    ))

    # 2) "light" historical EOD — FMP has a slimmer variant. Same comma test.
    probes.append(probe(
        "historical-price-eod/light multi-symbol",
        "historical-price-eod/light",
        {"symbol": "AAPL,MSFT,NVDA",
         "from": TEST_FROM, "to": TEST_TO},
        "probe_eod_light_multi",
    ))

    # 3) Speculative "bulk EOD prices" surface. /v3/ exposed a similar idea.
    probes.append(probe(
        "batch-quote (current snapshot, multi-symbol)",
        "batch-quote",
        {"symbols": "AAPL,MSFT,NVDA"},
        "probe_batch_quote",
    ))

    # 4) Historical S&P 500 constituents — gives us a PIT-correct universe
    #    slice for any historical date. Useful for Strategy B narrowing.
    probes.append(probe(
        "historical-sp500-constituent",
        "historical-sp500-constituent",
        None,
        "probe_hist_sp500",
    ))

    # 5) Speculative bulk EOD: "/stable/eod-bulk" or similar — try a
    #    plausible name. If 404, we mark NOT_USABLE; if 200, big win.
    probes.append(probe(
        "speculative bulk EOD by date",
        "historical-price-eod-bulk",
        {"date": TEST_DATE},
        "probe_bulk_eod",
    ))

    # 6) batch-historical price (another speculative plural name).
    probes.append(probe(
        "speculative batch-historical-price",
        "batch-historical-price",
        {"symbols": "AAPL,MSFT,NVDA",
         "from": TEST_FROM, "to": TEST_TO},
        "probe_batch_hist_price",
    ))

    # 7) Sector / industry historical performance — if exists, gives
    #    aggregate but not per-ticker. Quick check.
    probes.append(probe(
        "historical-sector-performance",
        "historical-sectors-performance",
        {"from": TEST_FROM, "to": TEST_TO},
        "probe_hist_sector_perf",
    ))

    # 8) Russell / Nasdaq historical constituents — Strategy B narrowing.
    probes.append(probe(
        "historical-nasdaq-constituent",
        "historical-nasdaq-constituent",
        None,
        "probe_hist_nasdaq",
    ))

    # Done. Verdict on each.
    def verdict_for(p: dict) -> str:
        if p.get("status") == "BUDGET_EXHAUSTED":
            return "SKIPPED (budget)"
        if not p.get("ok"):
            return f"NOT_USABLE (HTTP {p.get('http_status')})"
        # Heuristic: large row count on a list → cross-sectional capable
        rows = p.get("row_count") or 0
        if p.get("shape") == "list" and rows >= 3 and p.get("params") and (
            "symbol" in (p.get("params") or {}) or "symbols" in (p.get("params") or {})
        ):
            # Multi-symbol query returned data — check if it spans multiple
            # symbols or just AAPL.
            sym = (p.get("first_row_excerpt") or {}).get("symbol")
            return f"USABLE (rows={rows}; first.symbol={sym})"
        if rows >= 1:
            return f"PARTIAL (rows={rows})"
        return "EMPTY"

    for p in probes:
        p["verdict_from_probe"] = verdict_for(p)

    payload = {
        "started_at_utc": started_at,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "probes": probes,
        "calls_made": call_count(),
        "calls_by_group": fmp.get_call_group_summary(),
        "budget": MAX_CALLS,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2, default=str))

    print("\n=== probe summary ===")
    for p in probes:
        print(f"  {p['label']:50s}  →  {p['verdict_from_probe']}")
    print(f"\nTotal calls: {call_count()} / {MAX_CALLS}")
    print(f"Sidecar: {OUT_PATH}")


if __name__ == "__main__":
    main()
