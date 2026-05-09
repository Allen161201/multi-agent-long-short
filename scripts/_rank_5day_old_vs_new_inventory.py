"""
MUST-FIX 1 verification: 5-day trigger inventory OLD vs NEW.

Reproduces the OLD (FMP intraday open-to-close) and NEW
(prior_close → today_open gap) ranking formulas side-by-side across
2026-04-27..2026-05-01 from the cached historical_prices store.

Outputs per day:
- top-10 by NEW formula (prior_close → today_open) with all gates
  applied
- top-5 passing §2.1 (≥50% / vol ≥1M / prior_close > $2)
- side-by-side OLD vs NEW top-5 selection delta

Specifically verifies CUE + LABT presence in 5/1 NEW top-5.

NO LLM call. NO evidence-packet generation. NO replay. Pure ranking
diagnostic.
"""
from __future__ import annotations

import json
import os
import sys
from glob import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

CACHE_DIR = ROOT / "data" / "cache" / "historical_prices"
RANGE_KEY = "2026-04-27_2026-05-01"
WINDOW_DAYS = ["2026-04-27", "2026-04-28", "2026-04-29",
               "2026-04-30", "2026-05-01"]

SURGE_PCT_GATE = 50.0
SURGE_VOL_GATE = 1_000_000
SURGE_PRIOR_CLOSE_GATE = 2.0

EXCLUDED_FI_ETFS = frozenset({"BIL", "SHY", "IEF", "TLT", "IEI", "SHV"})


def _load_all() -> dict[str, list[dict]]:
    """Returns {ticker: sorted-by-date rows} for every cached ticker."""
    out: dict[str, list[dict]] = {}
    for f in glob(str(CACHE_DIR / "*.json")):
        base = os.path.basename(f)
        if base.startswith("_"):
            continue
        ticker = base.replace(".json", "")
        if ticker in EXCLUDED_FI_ETFS:
            continue
        try:
            d = json.loads(open(f, encoding="utf-8").read())
        except Exception:
            continue
        rows = d.get(RANGE_KEY) or []
        if rows:
            out[ticker] = sorted(rows, key=lambda r: r.get("date", ""))
    return out


def rank_old(date_iso: str, all_rows: dict) -> list[dict]:
    """OLD formula: row.get('change_pct') (FMP intraday open-to-close);
    prior_close derived backwards from close / (1 + change_pct/100)."""
    out = []
    for ticker, rows in all_rows.items():
        today = next((r for r in rows if r.get("date") == date_iso), None)
        if today is None:
            continue
        cp = float(today.get("change_pct") or 0.0)
        v = int(today.get("volume") or 0)
        c = float(today.get("close") or 0.0)
        if cp >= SURGE_PCT_GATE and v >= SURGE_VOL_GATE:
            pc = c / (1.0 + cp / 100.0) if cp > -100 else 0.0
            if pc > SURGE_PRIOR_CLOSE_GATE:
                out.append({
                    "ticker": ticker, "change_pct": round(cp, 4),
                    "volume": v, "close": c,
                    "today_open": float(today.get("open") or 0.0),
                    "prior_close": round(pc, 4),
                })
    out.sort(key=lambda x: x["change_pct"], reverse=True)
    return out


def rank_new(date_iso: str, all_rows: dict) -> list[dict]:
    """NEW formula: (today_open - prior_close) / prior_close * 100,
    sourced from the same range_key list."""
    out = []
    for ticker, rows in all_rows.items():
        today_idx = None
        for i, r in enumerate(rows):
            if r.get("date") == date_iso:
                today_idx = i
                break
        if today_idx is None:
            continue
        if today_idx == 0:
            continue  # no prior in window
        today = rows[today_idx]
        try:
            today_open = float(today.get("open") or 0.0)
        except (TypeError, ValueError):
            continue
        try:
            prior_close = float(rows[today_idx - 1].get("close") or 0.0)
        except (TypeError, ValueError):
            prior_close = 0.0
        if prior_close <= 0 or today_open <= 0:
            continue
        v = int(today.get("volume") or 0)
        c = float(today.get("close") or 0.0)
        cp = (today_open - prior_close) / prior_close * 100.0
        if cp >= SURGE_PCT_GATE and v >= SURGE_VOL_GATE:
            if prior_close > SURGE_PRIOR_CLOSE_GATE:
                out.append({
                    "ticker": ticker, "change_pct": round(cp, 4),
                    "volume": v, "close": c,
                    "today_open": today_open,
                    "prior_close": round(prior_close, 4),
                })
    out.sort(key=lambda x: x["change_pct"], reverse=True)
    return out


def _print_top(label: str, day: str, results: list[dict], n: int):
    print(f"\n  {label}: {day} — {len(results)} pass §2.1 (showing top-{min(n,len(results))}):")
    if not results:
        print("    (none)")
        return
    print(f"    {'rank':<4} {'ticker':<7} {'pct':>10}  {'prior_close':>12}  {'today_open':>11}  {'today_close':>12}  {'volume':>13}")
    for i, r in enumerate(results[:n], 1):
        print(f"    {i:<4} {r['ticker']:<7} {r['change_pct']:>9.2f}%  "
              f"{r['prior_close']:>12}  {r.get('today_open', 0):>11.2f}  "
              f"{r['close']:>12.2f}  {r['volume']:>13,}")


def main() -> int:
    print("=" * 92)
    print("MUST-FIX 1 — 5-day trigger inventory: OLD vs NEW ranking formula")
    print("=" * 92)
    all_rows = _load_all()
    print(f"\nLoaded {len(all_rows)} ticker cache files (excluding "
          f"§27.10 ETF FI universe)")

    summary: list[dict] = []
    for day in WINDOW_DAYS:
        print(f"\n{'=' * 92}")
        print(f"DAY: {day}")
        print("=" * 92)
        old = rank_old(day, all_rows)
        new = rank_new(day, all_rows)

        _print_top("OLD (FMP intraday open-to-close)", day, old, 10)
        _print_top("NEW (prior_close → today_open gap)", day, new, 10)

        # Side-by-side top-5
        old5 = [r["ticker"] for r in old[:5]]
        new5 = [r["ticker"] for r in new[:5]]
        old_set = set(old5)
        new_set = set(new5)
        added = [t for t in new5 if t not in old_set]
        dropped = [t for t in old5 if t not in new_set]
        held = [t for t in new5 if t in old_set]
        print(f"\n  TOP-5 SELECTION DELTA (OLD top-5 → NEW top-5):")
        print(f"    OLD top-5: {old5}")
        print(f"    NEW top-5: {new5}")
        print(f"    HELD     : {held}")
        print(f"    ADDED (NEW only): {added}")
        print(f"    DROPPED (OLD only): {dropped}")

        # CUE / LABT presence checks (5/1)
        if day == "2026-05-01":
            cue_in_new5 = "CUE" in new5
            labt_in_new5 = "LABT" in new5
            cue_in_old5 = "CUE" in old5
            labt_in_old5 = "LABT" in old5
            print(f"\n  PRESENCE CHECKS (2026-05-01):")
            print(f"    CUE  — OLD top-5: {cue_in_old5}   NEW top-5: {cue_in_new5}  "
                  f"({'PASS' if cue_in_new5 else 'FAIL'})")
            print(f"    LABT — OLD top-5: {labt_in_old5}   NEW top-5: {labt_in_new5}  "
                  f"({'PASS' if labt_in_new5 else 'FAIL'})")

        summary.append({
            "day": day, "old_count": len(old), "new_count": len(new),
            "old_top5": old5, "new_top5": new5, "added": added,
            "dropped": dropped,
        })

    print(f"\n{'=' * 92}")
    print("SUMMARY")
    print("=" * 92)
    print(f"\n  {'day':<12} {'OLD #pass':>10} {'NEW #pass':>10}  "
          f"{'OLD top-5':<35}  NEW top-5")
    for s in summary:
        print(f"  {s['day']:<12} {s['old_count']:>10} {s['new_count']:>10}  "
              f"{str(s['old_top5']):<35}  {s['new_top5']}")

    out_path = ROOT / "data" / "decisions" / "rank_5day_old_vs_new_2026_05_03.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n  saved: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
