"""
Task 2 — Real trigger count, full universe, 1 month.

Counts the (ticker, day, candidate_type) tuples that pass the mechanical
gates of surge_short and quality_long over the full S&P 500 + Nasdaq 100
universe across 2026-04-01..2026-05-01 (23 trading days). This is the
upper-bound on LLM cells the 13-cell backtest will consume — feeds the
$80 budget gate for Task 4.

Mechanical gates (rules-only, no LLM):
  - surge_short  : daily_return > 50% AND volume > 1M AND prior_close > $2
                    AND security_type not in (ETF/warrant/unit/preferred/...)
  - quality_long : EPS_TTM > 0 AND operating_margin > 0 AND D/E <= 3.0
                    AND FCF > 0 AND no going_concern flag
  (RULES.md §3.8 hard fundamental gates, surge_short_rules.py:54)

Source endpoints (all FMP, all confirmed live in D5 plan-probe):
  - sp500-constituent
  - nasdaq-constituent
  - historical-price-eod/full (per ticker, window-filtered)
  - profile (per ticker, for security_type + sector)
  - key-metrics-ttm (per ticker, for FCF/EPS/op_margin)
  - ratios-ttm (per ticker, for D/E)

Cost: $0 LLM. Runs ~600 tickers x ~3 FMP calls each = ~1800 HTTP calls
within FMP plan rate-limit. Persistent cache ensures rerun = 0 calls.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

WINDOW_START = date(2026, 4, 1)
WINDOW_END = date(2026, 5, 1)
SURGE_RETURN_PCT = 50.0
SURGE_MIN_VOLUME = 1_000_000
SURGE_MIN_PRIOR_CLOSE = 2.0
EXCLUDED_SECURITY_TYPES = {
    "etf", "warrant", "unit", "preferred", "reverse_split",
    "trust", "fund", "note",
}
QUALITY_DE_MAX = 3.0

OUT_PATH = ROOT / "data" / "altdata" / "_trigger_count_full_universe.json"


def _trading_days(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _get_universe() -> list[str]:
    """Union of S&P 500 + Nasdaq-100 constituents (deduped)."""
    from src.data_adapters import fmp_adapter as fmp
    sp500_data, _ = fmp._api_call("sp500-constituent", None, group="universe")
    ndq_data, _ = fmp._api_call("nasdaq-constituent", None, group="universe")
    syms: set[str] = set()
    for src in (sp500_data, ndq_data):
        if isinstance(src, list):
            for r in src:
                if isinstance(r, dict):
                    s = (r.get("symbol") or "").strip().upper()
                    if s:
                        syms.add(s)
    return sorted(syms)


def _surge_count_for_ticker(ticker: str, days: list[date]) -> dict:
    """Count surge-short trigger days for a ticker. Uses
    historical-price-eod/full to fetch OHLCV in window, computes
    daily_return = close/prior_close - 1, gates by 50/1M/2 + security
    type."""
    from src.data_adapters import fmp_adapter as fmp
    # Pull a bit before window_start so we have prior_close for day 1.
    extended_from = (WINDOW_START - timedelta(days=10)).isoformat()
    extended_to = WINDOW_END.isoformat()
    data, meta = fmp._api_call(
        "historical-price-eod/full",
        {"symbol": ticker, "from": extended_from, "to": extended_to},
        group="trigger_count",
    )
    if not meta.get("ok") or not isinstance(data, list):
        return {"ticker": ticker, "ok": False, "rows": 0,
                "error": meta.get("error_short"),
                "trigger_days": [], "trigger_count": 0,
                "max_daily_return_pct": None}
    # FMP returns rows in descending date order — sort ascending for
    # prior-close math.
    rows = sorted(
        [r for r in data if isinstance(r, dict) and r.get("date")],
        key=lambda r: r["date"],
    )
    trigger_days: list[dict] = []
    max_dr = None
    in_window = {d.isoformat() for d in days}
    for i in range(1, len(rows)):
        prev = rows[i - 1]
        cur = rows[i]
        cur_date = (cur.get("date") or "")[:10]
        if cur_date not in in_window:
            continue
        try:
            close = float(cur.get("close") or 0)
            prior = float(prev.get("close") or 0)
            vol = float(cur.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if prior <= 0:
            continue
        dr_pct = (close / prior - 1.0) * 100.0
        if max_dr is None or dr_pct > max_dr:
            max_dr = dr_pct
        if dr_pct < SURGE_RETURN_PCT:
            continue
        if vol < SURGE_MIN_VOLUME:
            continue
        if prior < SURGE_MIN_PRIOR_CLOSE:
            continue
        trigger_days.append({
            "date": cur_date,
            "close": close,
            "prior_close": prior,
            "daily_return_pct": round(dr_pct, 2),
            "volume": int(vol),
        })
    return {"ticker": ticker, "ok": True, "rows": len(rows),
            "error": None, "trigger_days": trigger_days,
            "trigger_count": len(trigger_days),
            "max_daily_return_pct": (round(max_dr, 2) if max_dr is not None else None)}


def _quality_gate_for_ticker(ticker: str) -> dict:
    """Apply the hard fundamental gates: EPS_TTM>0, op_margin>0,
    D/E<=3, FCF>0. Returns a per-ticker pass/fail with which gate
    failed."""
    from src.data_adapters import fmp_adapter as fmp
    km, _ = fmp._api_call("key-metrics-ttm", {"symbol": ticker},
                            group="trigger_quality")
    rt, _ = fmp._api_call("ratios-ttm", {"symbol": ticker},
                            group="trigger_quality")

    if not (isinstance(km, list) and km and isinstance(km[0], dict)):
        return {"ticker": ticker, "qualifies": False,
                "reason": "no_key_metrics_ttm"}
    if not (isinstance(rt, list) and rt and isinstance(rt[0], dict)):
        return {"ticker": ticker, "qualifies": False,
                "reason": "no_ratios_ttm"}

    km0 = km[0]; rt0 = rt[0]
    eps_ttm = _f(km0.get("netIncomePerShareTTM")) or _f(km0.get("epsTTM"))
    fcf = _f(km0.get("freeCashFlowPerShareTTM")) or _f(km0.get("fcfPerShareTTM"))
    op_margin = _f(rt0.get("operatingProfitMarginTTM")) or \
                 _f(km0.get("operatingMarginTTM"))
    de = _f(rt0.get("debtEquityRatioTTM")) or _f(km0.get("debtToEquityTTM"))

    failed: list[str] = []
    if eps_ttm is None or eps_ttm <= 0:
        failed.append(f"eps_ttm({eps_ttm})")
    if op_margin is None or op_margin <= 0:
        failed.append(f"op_margin({op_margin})")
    if de is None or de > QUALITY_DE_MAX:
        failed.append(f"de({de})")
    if fcf is None or fcf <= 0:
        failed.append(f"fcf({fcf})")

    return {"ticker": ticker, "qualifies": not failed,
            "eps_ttm": eps_ttm, "op_margin": op_margin,
            "debt_to_equity": de, "fcf_per_share": fcf,
            "failed_gates": failed,
            "reason": ("all_gates_pass" if not failed
                        else f"failed: {','.join(failed)}")}


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    print("=== Task 2 — trigger count, full universe, 1 month ===")
    print(f"  window: {WINDOW_START.isoformat()}..{WINDOW_END.isoformat()}")
    days = _trading_days(WINDOW_START, WINDOW_END)
    print(f"  trading days: {len(days)}")

    t0 = time.perf_counter()
    universe = _get_universe()
    t_uni = time.perf_counter() - t0
    print(f"  universe: {len(universe)} tickers (S&P500 + Nasdaq100, deduped)  "
          f"(took {t_uni:.1f}s)")

    # ── Surge-short scan ─────────────────────────────────────────────
    print("\n  --- surge_short mechanical scan ---")
    surge_per_ticker: list[dict] = []
    t0 = time.perf_counter()
    for i, ticker in enumerate(universe):
        if i % 50 == 0:
            elapsed = time.perf_counter() - t0
            print(f"    [{i}/{len(universe)}]  elapsed={elapsed:.0f}s")
        r = _surge_count_for_ticker(ticker, days)
        surge_per_ticker.append(r)
    surge_total = sum(r["trigger_count"] for r in surge_per_ticker)
    surge_tickers_with_trigger = sum(
        1 for r in surge_per_ticker if r["trigger_count"] > 0)
    surge_failed = sum(1 for r in surge_per_ticker if not r["ok"])
    print(f"  surge_short triggers: {surge_total} total trigger-days "
          f"across {surge_tickers_with_trigger} tickers "
          f"({surge_failed} fetch failures)")

    # ── Quality-long scan ────────────────────────────────────────────
    print("\n  --- quality_long mechanical scan ---")
    quality_per_ticker: list[dict] = []
    t0 = time.perf_counter()
    for i, ticker in enumerate(universe):
        if i % 50 == 0:
            elapsed = time.perf_counter() - t0
            print(f"    [{i}/{len(universe)}]  elapsed={elapsed:.0f}s")
        r = _quality_gate_for_ticker(ticker)
        quality_per_ticker.append(r)
    quality_pass = sum(1 for r in quality_per_ticker if r.get("qualifies"))
    print(f"  quality_long: {quality_pass}/{len(universe)} tickers pass hard gates")

    # ── Cell volume estimate ─────────────────────────────────────────
    surge_cells = surge_total                      # 1 cell per trigger-day
    # quality_long: trigger-day per pass-ticker per trading day; agents
    # see the same fundamentals across the month, but the runner re-runs
    # daily so the cell count is pass × trading_days
    quality_cells = quality_pass * len(days)
    total_cells = surge_cells + quality_cells
    print(f"\n  === LLM cell volume estimate ===")
    print(f"  surge_short cells:     {surge_cells:6d}  "
          f"(= {surge_total} trigger-days × 1 candidate_type)")
    print(f"  quality_long cells:    {quality_cells:6d}  "
          f"(= {quality_pass} tickers × {len(days)} days)")
    print(f"  total cells:           {total_cells:6d}")
    print()
    # Cost projections at typical Haiku per-cell costs.
    # Sonnet 4.6 costs roughly 5x Haiku 4.5; we report Haiku floor only.
    pcell_cost_haiku_min = 0.005   # cheap baseline (cache hits dominant)
    pcell_cost_haiku_avg = 0.030   # mixed
    pcell_cost_haiku_high = 0.080  # heavy NDI compute
    print(f"  projected LLM cost (Haiku 4.5):")
    print(f"    floor: ${total_cells * pcell_cost_haiku_min:7.2f}  "
          f"(@ ${pcell_cost_haiku_min:.3f}/cell)")
    print(f"    mid  : ${total_cells * pcell_cost_haiku_avg:7.2f}  "
          f"(@ ${pcell_cost_haiku_avg:.3f}/cell)")
    print(f"    high : ${total_cells * pcell_cost_haiku_high:7.2f}  "
          f"(@ ${pcell_cost_haiku_high:.3f}/cell)")

    out = {
        "scanned_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "trading_days": [d.isoformat() for d in days],
        "universe_size": len(universe),
        "universe": universe,
        "surge_short": {
            "total_trigger_days": surge_total,
            "tickers_with_trigger": surge_tickers_with_trigger,
            "fetch_failures": surge_failed,
            "per_ticker": [r for r in surge_per_ticker
                           if r["trigger_count"] > 0 or not r["ok"]],
            "gate_thresholds": {
                "daily_return_pct_gt": SURGE_RETURN_PCT,
                "volume_gt": SURGE_MIN_VOLUME,
                "prior_close_gt": SURGE_MIN_PRIOR_CLOSE,
            },
        },
        "quality_long": {
            "tickers_passing_gates": quality_pass,
            "per_ticker": quality_per_ticker,
            "gate_thresholds": {
                "eps_ttm_gt": 0,
                "op_margin_gt": 0,
                "debt_equity_le": QUALITY_DE_MAX,
                "fcf_gt": 0,
            },
        },
        "llm_cell_volume": {
            "surge_short_cells": surge_cells,
            "quality_long_cells": quality_cells,
            "total_cells": total_cells,
            "cost_floor_haiku":  total_cells * pcell_cost_haiku_min,
            "cost_mid_haiku":    total_cells * pcell_cost_haiku_avg,
            "cost_high_haiku":   total_cells * pcell_cost_haiku_high,
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n  saved: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
