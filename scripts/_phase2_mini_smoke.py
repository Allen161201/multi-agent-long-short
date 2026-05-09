"""
Phase 2 Step 4 — multi-day mini-smoke (1 ticker × 5 trading days).

This driver is the spine the D5 6-year × 13-cell backtest will extend.
Hardcoded constants live at the TOP. The per-day loop is wrapped in
run_window(); the post-loop pnl_backtest call is wrapped in
finalize_window(). To turn this into the D5 driver:

  1. Replace TICKER with the universe loop (one outer for-loop).
  2. Replace START/END with the 6-year window date pair.
  3. Replace ORCHESTRATOR with the 13-cell cross — for each cell,
     re-call run_window() with a different (orchestrator, ablation)
     tuple and a different DECISION_ROOT subdir.
  4. Drive everything from argparse / a YAML file rather than module-
     level constants.

The functions deliberately accept primitive args (ticker / start /
end / orchestrator / decision_root) and return a dict so the D5
ablation loop can compose them into a 13-row results table.

Cost guardrail: aborts the per-day loop when cumulative spend exceeds
COST_GATE_USD, which keeps an unattended D5 run from blowing the
budget if a regime change makes Haiku slow.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

os.environ["LLM_PROVIDER"] = "anthropic"

# ── Hardcoded constants (TOP of file per D5-extension constraint) ─────
HAIKU_MODEL = "claude-haiku-4-5-20251001"
TIMEOUT_S = 300
TICKER = "AAPL"
CANDIDATE_TYPE = "quality_long"
START = "2026-04-23"        # Thu
END = "2026-04-29"          # Wed (5 trading days inclusive)
CUTOFF_TIME_ET = "16:15:00"  # ET close-ish; matches Phase 2 Step 2 cutoff convention
ORCHESTRATOR = "pipeline"   # one of: pipeline | solo | flat
DECISION_ROOT = ROOT / "data" / "decisions" / "phase2_mini_smoke"
BACKTEST_OUT_DIR = ROOT / "data" / "backtest" / "phase2_mini_smoke"
CACHE_ROOT = ROOT / "data" / "cache" / "llm_smoke_haiku_p2step4"
COST_GATE_USD = 4.50        # abort the loop if spend exceeds this
ET = ZoneInfo("America/New_York")


def _trading_days(start_iso: str, end_iso: str) -> list[date]:
    """Inclusive Mon-Fri days between start and end."""
    s = date.fromisoformat(start_iso)
    e = date.fromisoformat(end_iso)
    out: list[date] = []
    d = s
    while d <= e:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _orchestrator_kwargs(orchestrator: str) -> dict:
    """Map a CLI-friendly orchestrator label onto the runner's
    (agent_mode, topology) pair."""
    if orchestrator == "pipeline":
        return {"agent_mode": "multi", "topology": "pipeline"}
    if orchestrator == "solo":
        return {"agent_mode": "solo", "topology": "pipeline"}
    if orchestrator == "flat":
        return {"agent_mode": "multi", "topology": "flat"}
    raise ValueError(f"unknown orchestrator {orchestrator!r}")


def _save_run(
    *, decision_root: Path, ticker: str, candidate_type: str,
    day: date, run_label: str, packet: dict, result: dict,
) -> Path:
    """Persist (evidence_packet, decision) pair under
    decision_root/<YYYY-MM-DD>/<run_label>/{ticker}_evidence_packet.json
    + {ticker}_<candidate>_decision.json. Returns the run dir."""
    run_dir = decision_root / day.isoformat() / run_label
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"{ticker}_evidence_packet.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (run_dir / f"{ticker}_{candidate_type}_decision.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return run_dir


def run_window(
    *,
    ticker: str,
    start: str,
    end: str,
    orchestrator: str,
    candidate_type: str = CANDIDATE_TYPE,
    cutoff_time_et: str = CUTOFF_TIME_ET,
    decision_root: Path = DECISION_ROOT,
    cache_root: Path = CACHE_ROOT,
    cost_gate_usd: float = COST_GATE_USD,
    verbose: bool = True,
) -> dict:
    """Loop over trading days in [start, end] (inclusive). For each day
    build the evidence packet, run the chosen orchestrator at live Haiku,
    and persist (packet, decision) under decision_root/<day>/16-15/.

    Returns a dict with per-day rows + cumulative cost + run flag.
    Aborts the loop when cumulative spend exceeds cost_gate_usd.
    """
    from src.evidence_packet import generate_evidence_packet
    from src.agents.runner import run_all_agents_for_candidate
    from src.llm.cache import LLMCache
    from src.llm.anthropic_provider import (
        AnthropicProvider,
        AnthropicProviderError,
        get_cost_ledger,
    )

    ledger = get_cost_ledger()
    cost_at_entry = ledger.total_usd()

    provider = AnthropicProvider(default_model=HAIKU_MODEL, timeout_s=TIMEOUT_S)
    cache = LLMCache(root=cache_root)

    days = _trading_days(start, end)
    if verbose:
        print(f"  run_window: ticker={ticker} orchestrator={orchestrator} "
              f"days={len(days)} ({days[0]}..{days[-1]})")

    rows: list[dict] = []
    aborted = False
    abort_reason: Optional[str] = None

    orch_kwargs = _orchestrator_kwargs(orchestrator)

    for d in days:
        cutoff_iso = f"{d.isoformat()}T{cutoff_time_et}-04:00"
        run_label = "16-15"
        if verbose:
            print(f"\n    --- {d.isoformat()} cutoff={cutoff_iso} ---")
        cost_before = ledger.total_usd()
        if cost_before - cost_at_entry > cost_gate_usd:
            aborted = True
            abort_reason = (
                f"cumulative window cost {cost_before - cost_at_entry:.4f} "
                f"exceeded gate {cost_gate_usd}"
            )
            if verbose:
                print(f"    ABORT  {abort_reason}")
            break

        t0 = time.perf_counter()
        try:
            packet = generate_evidence_packet(
                ticker=ticker, decision_timestamp=cutoff_iso,
                strict_pit_mode=True,
            )
            result = run_all_agents_for_candidate(
                evidence_packet=packet,
                candidate_type=candidate_type,
                provider=provider,
                cache=cache,
                force_refresh=False,
                **orch_kwargs,
            )
        except AnthropicProviderError as e:
            elapsed = time.perf_counter() - t0
            if verbose:
                print(f"    ABORT  Anthropic error after {elapsed:.1f}s: {e}")
            rows.append({
                "day": d.isoformat(),
                "cutoff": cutoff_iso,
                "error": f"{type(e).__name__}: {e}",
                "wall_clock_s": elapsed,
                "cost_usd": ledger.total_usd() - cost_before,
            })
            aborted = True
            abort_reason = f"Anthropic error on {d.isoformat()}"
            break

        elapsed = time.perf_counter() - t0
        cost = ledger.total_usd() - cost_before
        # Persist the runner-patched packet (the one PM actually saw) so
        # PIT compliance scans see a coherent (envelope.LDI ↔ decision.LDI)
        # pair.
        packet_to_save = result.get("evidence_packet_patched") or packet
        run_dir = _save_run(
            decision_root=decision_root, ticker=ticker,
            candidate_type=candidate_type, day=d, run_label=run_label,
            packet=packet_to_save, result=result,
        )
        final = result.get("final_decision") or {}
        decision = (
            final.get("decision")
            or final.get("recommended_action")
            or final.get("decision_label")
            or "n/a"
        )
        ndi = result.get("ndi_compute_result") or {}
        rows.append({
            "day": d.isoformat(),
            "cutoff": cutoff_iso,
            "run_dir": str(run_dir),
            "decision": str(decision),
            "confidence": str(final.get("confidence") or "n/a"),
            "cache_summary": result.get("cache_summary"),
            "ndi_score": ndi.get("score"),
            "ndi_mode": ndi.get("mode"),
            "ndi_n_sources": ndi.get("n_sources", 0),
            "ndi_n_items_considered": ndi.get("n_items_considered", 0),
            "wall_clock_s": elapsed,
            "cost_usd": cost,
            "pm_rationale": (final.get("reason")
                             or final.get("audit_rationale")
                             or final.get("rationale") or ""),
        })
        if verbose:
            print(f"    decision={decision} conf={final.get('confidence')} "
                  f"cost=${cost:.5f} wall={elapsed:.1f}s "
                  f"ndi={ndi.get('mode')}/{ndi.get('score')}")

    total_cost = ledger.total_usd() - cost_at_entry
    return {
        "ticker": ticker,
        "orchestrator": orchestrator,
        "candidate_type": candidate_type,
        "start": start,
        "end": end,
        "trading_days": [d.isoformat() for d in days],
        "rows": rows,
        "aborted": aborted,
        "abort_reason": abort_reason,
        "window_cost_usd": total_cost,
    }


def _make_price_provider(
    ticker: str, start_iso: str, end_iso: str,
) -> Callable[[str, str], float]:
    """Build (ticker, YYYY-MM-DD) → close from FMP historical-price-eod.
    The closure caches the daily map on first call. Padding ±10 days so
    the pnl_backtest engine's fill-date lookups (T+1 / T+2) don't run
    off the end. Falls back to the last-known close on weekend / holiday
    misses (the engine queries fill_date which may be a non-trading
    Saturday in some edge cases)."""
    from src.data_adapters import fmp_adapter as fmp

    pad_start = (date.fromisoformat(start_iso) - timedelta(days=10)).isoformat()
    pad_end = (date.fromisoformat(end_iso) + timedelta(days=10)).isoformat()

    rows = fmp.get_historical_daily(ticker, pad_start, pad_end)
    by_date: dict[str, float] = {}
    sorted_dates: list[str] = []
    for r in rows:
        d_str = r.get("date") or ""
        close = r.get("close")
        if not d_str:
            continue
        if not isinstance(close, (int, float)) or close <= 0:
            continue
        by_date[d_str] = float(close)
    sorted_dates = sorted(by_date.keys())

    def _provider(t: str, day_iso: str) -> float:
        if t != ticker and t != "SPY":
            raise KeyError(f"price_history_provider scoped to {ticker}/SPY only; "
                           f"asked for {t}")
        if t == "SPY":
            return _spy_provider(day_iso)
        if day_iso in by_date:
            return by_date[day_iso]
        # Fall back to last known close before day_iso
        prior = [d for d in sorted_dates if d <= day_iso]
        if prior:
            return by_date[prior[-1]]
        # Fall forward to first close on or after day_iso
        forward = [d for d in sorted_dates if d >= day_iso]
        if forward:
            return by_date[forward[0]]
        raise KeyError(f"no price data for {t} near {day_iso}")

    # Lazy SPY cache
    _spy_cache: dict[str, float] = {}

    def _spy_provider(day_iso: str) -> float:
        if not _spy_cache:
            spy_rows = fmp.get_historical_daily("SPY", pad_start, pad_end)
            for r in spy_rows:
                d_str = r.get("date") or ""
                close = r.get("close")
                if d_str and isinstance(close, (int, float)) and close > 0:
                    _spy_cache[d_str] = float(close)
        if day_iso in _spy_cache:
            return _spy_cache[day_iso]
        prior = sorted([d for d in _spy_cache if d <= day_iso])
        if prior:
            return _spy_cache[prior[-1]]
        forward = sorted([d for d in _spy_cache if d >= day_iso])
        if forward:
            return _spy_cache[forward[0]]
        raise KeyError(f"no SPY price near {day_iso}")

    return _provider


def finalize_window(
    *,
    decision_root: Path,
    start: str,
    end: str,
    ticker: str = TICKER,
    output_dir: Path = BACKTEST_OUT_DIR,
    benchmark_ticker: Optional[str] = "SPY",
) -> dict:
    """Post-loop: invoke run_pnl_backtest as a library against the
    decision tree written by run_window(). Returns the backtest summary."""
    from src.engine.pnl_backtest import run_pnl_backtest

    output_dir.mkdir(parents=True, exist_ok=True)
    provider = _make_price_provider(ticker, start, end)
    summary = run_pnl_backtest(
        start_date=start,
        end_date=end,
        decision_root=decision_root,
        price_history_provider=provider,
        output_dir=output_dir,
        benchmark_ticker=benchmark_ticker,
    )
    return summary


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ABORT  ANTHROPIC_API_KEY not set")
        return 2

    print("=== Phase 2 Step 4 — mini-smoke (1 ticker × 5 trading days) ===")
    print(f"  ticker={TICKER}  candidate={CANDIDATE_TYPE}")
    print(f"  window={START}..{END}  orchestrator={ORCHESTRATOR}")
    print(f"  decision_root={DECISION_ROOT}")
    print(f"  backtest_out_dir={BACKTEST_OUT_DIR}")
    print(f"  cache_root={CACHE_ROOT}")
    print(f"  cost_gate=${COST_GATE_USD:.2f}")

    from src.llm.anthropic_provider import get_cost_ledger
    ledger = get_cost_ledger()
    cost_before = ledger.total_usd()

    t0 = time.perf_counter()
    win = run_window(
        ticker=TICKER, start=START, end=END,
        orchestrator=ORCHESTRATOR,
        candidate_type=CANDIDATE_TYPE,
        cutoff_time_et=CUTOFF_TIME_ET,
        decision_root=DECISION_ROOT,
        cache_root=CACHE_ROOT,
        cost_gate_usd=COST_GATE_USD,
        verbose=True,
    )
    window_elapsed = time.perf_counter() - t0
    window_cost = ledger.total_usd() - cost_before

    print(f"\n  run_window done: aborted={win['aborted']}  cost=${window_cost:.5f}  "
          f"wall={window_elapsed:.1f}s")

    # ── Decision distribution ────────────────────────────────────────
    decisions = [r.get("decision", "n/a") for r in win["rows"] if not r.get("error")]
    distribution: dict[str, int] = {}
    for d in decisions:
        distribution[d] = distribution.get(d, 0) + 1
    print(f"\n  Decision distribution across {len(decisions)} days: {distribution}")

    # ── NDI summary ──────────────────────────────────────────────────
    print("\n  NDI per day:")
    for r in win["rows"]:
        if r.get("error"):
            print(f"    {r['day']}: ERROR {r['error']}")
            continue
        print(f"    {r['day']}: mode={r.get('ndi_mode')} "
              f"score={r.get('ndi_score')} "
              f"n_sources={r.get('ndi_n_sources')} "
              f"n_items={r.get('ndi_n_items_considered')}")

    # ── PM rationale spot-check ──────────────────────────────────────
    print("\n  PM rationale text (per day, first 200 chars):")
    for r in win["rows"]:
        if r.get("error"):
            continue
        rat = (r.get("pm_rationale") or "").replace("\n", " ").strip()
        if len(rat) > 200:
            rat = rat[:197] + "..."
        print(f"    {r['day']}: {rat!r}")

    # ── Finalize backtest (only if window completed cleanly) ─────────
    backtest_summary: Optional[dict] = None
    if not win["aborted"] and win["rows"]:
        print("\n  Calling finalize_window (run_pnl_backtest)...")
        try:
            backtest_summary = finalize_window(
                decision_root=DECISION_ROOT,
                start=START,
                end=END,
                ticker=TICKER,
                output_dir=BACKTEST_OUT_DIR,
                benchmark_ticker="SPY",
            )
            print(f"    trading_days_processed: "
                  f"{backtest_summary.get('trading_days_processed')}")
            print(f"    final_nav: ${backtest_summary.get('final_nav'):,.2f}")
            print(f"    initial_capital: "
                  f"${backtest_summary.get('initial_capital'):,.2f}")
            print(f"    totals: {backtest_summary.get('totals')}")
            metrics = backtest_summary.get("metrics") or {}
            print(f"    metrics: total_return={metrics.get('total_return')} "
                  f"sharpe={metrics.get('sharpe_ratio')} "
                  f"max_dd={metrics.get('max_drawdown', {}).get('value')}")
        except Exception as e:
            print(f"    finalize_window FAILED: {type(e).__name__}: {e}")
            backtest_summary = {"error": f"{type(e).__name__}: {e}"}

    out_path = DECISION_ROOT.parent / "phase2_step4_summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({
            "ticker": TICKER,
            "candidate_type": CANDIDATE_TYPE,
            "window": [START, END],
            "orchestrator": ORCHESTRATOR,
            "model": HAIKU_MODEL,
            "window_cost_usd": window_cost,
            "window_elapsed_s": window_elapsed,
            "window_summary": win,
            "backtest_summary": backtest_summary,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  saved: {out_path}")

    return 0 if not win["aborted"] else 1


if __name__ == "__main__":
    import traceback
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(99)
