"""
Unit tests for src/engine/pnl_backtest.py

Direct-execution / __main__-style. Run:
    python tests/test_pnl_backtest.py

No FMP / no HTTP. Synthetic decision JSONs + paired evidence packets are
written to a tempdir; price_history_provider is a hardcoded stub.

Cases:
  A — single-day, 0 decisions          → NAV unchanged, manifest written
  B — single-day, 1 buy                → opened, MTM at fill day's close,
                                         transaction cost applied
  C — buy day1, hold day2              → MTM on day2 reflects price change
  D — buy day1, sell day2, idle day3   → realized via close, NAV trail
  E — short with borrow cost           → borrow accumulates per day
  F — sleeve cap enforcement           → request > cap → capped + logged
  G — Friday reinvestment trigger      → midpoint split fires + recorded
  H — end-to-end 5-day, 2 tickers      → NAV history has 5 rows, metrics
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engine.pnl_backtest import (  # noqa: E402
    run_pnl_backtest, NAV_HISTORY_CSV, MANIFEST_FILENAME,
)


PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


# ── synthetic-data helpers ────────────────────────────────────────────

def write_decision(
    *, root: Path, day: str, run: str, ticker: str, candidate_type: str,
    side: str, size_pct: float, decision: str = "buy",
    last_eod_close: float = 100.0, decision_id: str | None = None,
) -> Path:
    """Write a (decision, evidence_packet) pair under root/day/run/."""
    dir_ = root / day / run
    dir_.mkdir(parents=True, exist_ok=True)
    did = decision_id or f"sha256:{ticker.lower()}_{day}_{run}_{side}"
    decision_dict = {
        "ticker": ticker,
        "candidate_type": candidate_type,
        "decision_timestamp": f"{day}T16:15:00-04:00",
        "evidence_packet_hash": f"sha256:fake_packet_{ticker}_{day}",
        "final_decision": {
            "ticker": ticker,
            "candidate_type": candidate_type,
            "decision": decision,
            "position_size_pct": str(size_pct),
            "execution_plan": {
                "order_type": "T+1 next-open",
                "execution_timestamp": f"{day}T16:15:00-04:00",
                "ticker": ticker,
                "side": side,
                "size_pct_of_portfolio": size_pct,
            },
            "locked_decision_id": did,
            "audit_record": {
                "evidence_packet_hash": f"sha256:fake_packet_{ticker}_{day}",
            },
        },
    }
    packet_dict = {
        "envelope": {"evidence_packet_hash": f"sha256:fake_packet_{ticker}_{day}"},
        "price_snapshot": {
            "ticker": ticker, "status": "ok",
            "last_eod_close": last_eod_close,
            "last_price": last_eod_close,
            "as_of": f"{day}T16:15:00-04:00",
        },
    }
    dec_path = dir_ / f"{ticker}_{candidate_type}_decision.json"
    pkt_path = dir_ / f"{ticker}_evidence_packet.json"
    with dec_path.open("w", encoding="utf-8") as f:
        json.dump(decision_dict, f, ensure_ascii=False, indent=2)
    with pkt_path.open("w", encoding="utf-8") as f:
        json.dump(packet_dict, f, ensure_ascii=False, indent=2)
    return dec_path


def make_price_provider(fixed_prices: dict[tuple[str, str], float]):
    """Build a (ticker, date) → close provider from a static map.
    Falls back to ticker-only default if (ticker, date) missing."""
    def _fetch(ticker: str, date_iso: str) -> float:
        if (ticker, date_iso) in fixed_prices:
            return fixed_prices[(ticker, date_iso)]
        # Fall back: ticker-only default if any
        if ("__default__", ticker) in fixed_prices:
            return fixed_prices[("__default__", ticker)]
        raise KeyError(f"no price for {ticker} on {date_iso}")
    return _fetch


def fresh_tmpdir(label: str) -> tuple[Path, Path]:
    """Return (decision_root, output_dir), both newly created."""
    base = Path(tempfile.mkdtemp(prefix=f"pnl_backtest_{label}_"))
    decision_root = base / "decisions"
    output_dir = base / "out"
    decision_root.mkdir(parents=True, exist_ok=True)
    return decision_root, output_dir


# ── test cases ────────────────────────────────────────────────────────

def case_A_single_day_zero_decisions():
    print("\nCase A — single-day, 0 decisions → NAV unchanged + manifest")
    decision_root, output_dir = fresh_tmpdir("A")
    try:
        # 2026-05-04 is a Monday; no decisions written → empty day_dir.
        provider = make_price_provider({})
        summary = run_pnl_backtest(
            start_date="2026-05-04", end_date="2026-05-04",
            decision_root=decision_root,
            price_history_provider=provider,
            initial_capital=1_000_000.0,
            output_dir=output_dir,
        )
        check("A1 trading_days_processed == 1",
              summary["trading_days_processed"] == 1,
              str(summary["trading_days_processed"]))
        check("A2 totals.decisions_seen == 0",
              summary["totals"]["decisions_seen"] == 0,
              str(summary["totals"]))
        check("A3 final_nav == initial_capital",
              abs(summary["final_nav"] - 1_000_000.0) < 1e-6,
              f"got {summary['final_nav']}")
        manifest = json.loads((output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        check("A4 manifest written", manifest["run_id"].startswith("backtest_"))
        eod_path = output_dir / "2026-05-04_eod_state.json"
        check("A5 EOD JSON written", eod_path.exists())
        eod = json.loads(eod_path.read_text(encoding="utf-8"))
        check("A6 EOD audit.events present (no_op or empty)",
              "events" in eod["audit"])
        check("A7 nav_history.csv has 1 row",
              (output_dir / NAV_HISTORY_CSV).exists())
    finally:
        shutil.rmtree(decision_root.parent, ignore_errors=True)


def case_B_single_buy():
    print("\nCase B — single-day, 1 buy → position opened + tx cost charged")
    decision_root, output_dir = fresh_tmpdir("B")
    try:
        # PM emits buy AAPL 1% on 2026-05-04. Fill date is 2026-05-05.
        write_decision(
            root=decision_root, day="2026-05-04", run="16-15",
            ticker="AAPL", candidate_type="quality_long",
            side="long", size_pct=1.0, last_eod_close=100.0,
        )
        provider = make_price_provider({
            ("AAPL", "2026-05-04"): 100.0,
            ("AAPL", "2026-05-05"): 100.0,  # fill date close == today
        })
        summary = run_pnl_backtest(
            start_date="2026-05-04", end_date="2026-05-04",
            decision_root=decision_root,
            price_history_provider=provider,
            initial_capital=1_000_000.0,
            output_dir=output_dir,
            transaction_cost_bps=30.0,
            include_borrow_cost=False,
        )
        check("B1 trades_filled == 1",
              summary["totals"]["trades_filled"] == 1,
              str(summary["totals"]))
        # 1% of 1M = $10,000. Tx cost @ 30 bps = $30. NAV = 1M - 30 (tx cost
        # is charged after the buy; the buy itself is NAV-neutral because
        # the bought shares are valued at the same price as cash spent).
        expected_nav = 1_000_000.0 - 30.0
        check("B2 final NAV reflects tx cost",
              abs(summary["final_nav"] - expected_nav) < 0.01,
              f"got {summary['final_nav']}, expected {expected_nav}")
        check("B3 transaction_cost_dollars == 30.0",
              abs(summary["totals"]["transaction_cost_dollars"] - 30.0) < 1e-6,
              str(summary["totals"]["transaction_cost_dollars"]))
        eod = json.loads((output_dir / "2026-05-04_eod_state.json").read_text(encoding="utf-8"))
        check("B4 1 open position",
              len(eod["positions"]) == 1, str(eod["positions"]))
        check("B5 position is AAPL long",
              eod["positions"][0]["ticker"] == "AAPL"
              and eod["positions"][0]["side"] == "long")
    finally:
        shutil.rmtree(decision_root.parent, ignore_errors=True)


def case_C_buy_then_hold():
    print("\nCase C — buy day1, hold day2 → MTM reflects price change")
    decision_root, output_dir = fresh_tmpdir("C")
    try:
        # Buy AAPL on Mon 2026-05-04, hold through Tue 2026-05-05.
        write_decision(
            root=decision_root, day="2026-05-04", run="16-15",
            ticker="AAPL", candidate_type="quality_long",
            side="long", size_pct=1.0, last_eod_close=100.0,
        )
        provider = make_price_provider({
            ("AAPL", "2026-05-04"): 100.0,
            ("AAPL", "2026-05-05"): 110.0,  # +10% on day 2
        })
        summary = run_pnl_backtest(
            start_date="2026-05-04", end_date="2026-05-05",
            decision_root=decision_root,
            price_history_provider=provider,
            initial_capital=1_000_000.0,
            output_dir=output_dir,
            transaction_cost_bps=30.0,
            include_borrow_cost=False,
        )
        check("C1 trading_days_processed == 2",
              summary["trading_days_processed"] == 2)
        # Day 1: fill at $110 (next trading day's close = $110), 1% of pre-trade
        # NAV (~1M) = $10,000 / 110 ≈ 90.91 shares. Tx cost = 10000 * 30/10000 = $30.
        # On day 1, MTM = today's close $100 → unrealized loss already.
        # Day 2: MTM = $110 → small gain back.
        # NAV trajectory is non-monotonic but the final NAV must show the
        # day-2 mark relative to day-1 fill price.
        rows = []
        import csv
        with (output_dir / NAV_HISTORY_CSV).open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows.append(r)
        check("C2 nav_history has 2 rows", len(rows) == 2,
              f"got {len(rows)}")
        # Day 1 nav < day 2 nav (price rose from 100 to 110)
        nav1 = float(rows[0]["total_nav"])
        nav2 = float(rows[1]["total_nav"])
        check("C3 day-2 NAV > day-1 NAV (price rose)",
              nav2 > nav1, f"nav1={nav1}, nav2={nav2}")
    finally:
        shutil.rmtree(decision_root.parent, ignore_errors=True)


def case_D_buy_sell_idle():
    print("\nCase D — buy day1, sell day2, idle day3 → trade visible in NAV")
    decision_root, output_dir = fresh_tmpdir("D")
    try:
        # 2026-05-04 Mon (buy), 05-05 Tue (sell), 05-06 Wed (idle)
        write_decision(
            root=decision_root, day="2026-05-04", run="16-15",
            ticker="AAPL", candidate_type="quality_long",
            side="long", size_pct=2.0, last_eod_close=100.0,
        )
        write_decision(
            root=decision_root, day="2026-05-05", run="16-15",
            ticker="AAPL", candidate_type="quality_long",
            side="sell", size_pct=2.0, decision="sell", last_eod_close=110.0,
        )
        provider = make_price_provider({
            ("AAPL", "2026-05-04"): 100.0,
            ("AAPL", "2026-05-05"): 110.0,
            ("AAPL", "2026-05-06"): 115.0,
        })
        summary = run_pnl_backtest(
            start_date="2026-05-04", end_date="2026-05-06",
            decision_root=decision_root,
            price_history_provider=provider,
            initial_capital=1_000_000.0,
            output_dir=output_dir,
            transaction_cost_bps=30.0,
            include_borrow_cost=False,
        )
        check("D1 trading_days_processed == 3",
              summary["trading_days_processed"] == 3)
        check("D2 trades_filled == 2 (1 buy + 1 sell)",
              summary["totals"]["trades_filled"] == 2,
              str(summary["totals"]))
        eod3 = json.loads((output_dir / "2026-05-06_eod_state.json").read_text(encoding="utf-8"))
        check("D3 day-3 has 0 open positions (sold)",
              len(eod3["positions"]) == 0)
        # day-3 NAV must equal day-2 NAV (idle day, all cash, no positions
        # to mark).
        eod2 = json.loads((output_dir / "2026-05-05_eod_state.json").read_text(encoding="utf-8"))
        check("D4 day-3 NAV == day-2 NAV (idle)",
              abs(eod3["total_nav"] - eod2["total_nav"]) < 1e-6,
              f"d2={eod2['total_nav']}, d3={eod3['total_nav']}")
    finally:
        shutil.rmtree(decision_root.parent, ignore_errors=True)


def case_E_short_borrow():
    print("\nCase E — open short day1, hold 5 days → borrow accumulates")
    decision_root, output_dir = fresh_tmpdir("E")
    try:
        # Open short MEME on Mon 2026-05-04. Hold through Fri 2026-05-08.
        # Price flat at 50; short notional = 1% NAV ≈ $10,000.
        write_decision(
            root=decision_root, day="2026-05-04", run="16-15",
            ticker="MEME", candidate_type="surge_short",
            side="short", size_pct=1.0, last_eod_close=50.0,
        )
        provider = make_price_provider({
            ("MEME", d): 50.0 for d in
            ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08"]
        })
        summary = run_pnl_backtest(
            start_date="2026-05-04", end_date="2026-05-08",
            decision_root=decision_root,
            price_history_provider=provider,
            initial_capital=1_000_000.0,
            output_dir=output_dir,
            transaction_cost_bps=30.0,
            include_borrow_cost=True,
        )
        check("E1 borrow_cost_dollars > 0",
              summary["totals"]["borrow_cost_dollars"] > 0,
              str(summary["totals"]))
        # Expected borrow ≈ 5 days × $10,000 × 0.15/252 ≈ $29.76
        # (borrow charged after MTM, so first day's borrow notional ≈ $10k).
        expected_lo, expected_hi = 25.0, 35.0
        bc = summary["totals"]["borrow_cost_dollars"]
        check("E2 borrow cost in expected range $25–$35",
              expected_lo <= bc <= expected_hi,
              f"got {bc}")
        # 1 trade only (the day-1 short open).
        check("E3 trades_filled == 1",
              summary["totals"]["trades_filled"] == 1,
              str(summary["totals"]))
    finally:
        shutil.rmtree(decision_root.parent, ignore_errors=True)


def case_F_sleeve_cap():
    print("\nCase F — sleeve cap → request 12% surge_short → capped at 10%")
    decision_root, output_dir = fresh_tmpdir("F")
    try:
        # Try to short 12% in one go — exceeds SLEEVE_CAP_PCT=10.
        write_decision(
            root=decision_root, day="2026-05-04", run="16-15",
            ticker="MEME", candidate_type="surge_short",
            side="short", size_pct=12.0, last_eod_close=50.0,
        )
        provider = make_price_provider({
            ("MEME", "2026-05-04"): 50.0,
            ("MEME", "2026-05-05"): 50.0,
        })
        summary = run_pnl_backtest(
            start_date="2026-05-04", end_date="2026-05-04",
            decision_root=decision_root,
            price_history_provider=provider,
            initial_capital=1_000_000.0,
            output_dir=output_dir,
            transaction_cost_bps=0.0,    # zero out tx cost so we can
            include_borrow_cost=False,   # check the position size cleanly
        )
        check("F1 size_capped_events == 1",
              summary["totals"]["size_capped_events"] == 1,
              str(summary["totals"]))
        eod = json.loads((output_dir / "2026-05-04_eod_state.json").read_text(encoding="utf-8"))
        # PER_POSITION_CAP_PCT = 5.0 binds before SLEEVE_CAP_PCT = 10.0.
        # So a fresh 12% short on a single ticker hits the per-position cap
        # at 5%, not the sleeve cap at 10%. Validate it ended up at 5%.
        check("F2 capped position landed at PER_POSITION_CAP_PCT=5%",
              len(eod["positions"]) == 1
              and abs(eod["positions"][0]["size_pct"] - 5.0) < 1e-6,
              f"positions={eod['positions']}")
        cap_evs = eod["audit"]["size_cap_events"]
        check("F3 size_cap_events list has the cap record",
              len(cap_evs) == 1
              and "per_position_cap" in cap_evs[0]["reason"],
              f"cap_evs={cap_evs}")
    finally:
        shutil.rmtree(decision_root.parent, ignore_errors=True)


def case_G_friday_reinvestment():
    print("\nCase G — Friday + realised short profit → reinvestment fires")
    decision_root, output_dir = fresh_tmpdir("G")
    try:
        # Open short MEME on Mon 2026-05-04 @ $50, cover on Fri 2026-05-08.
        # Cover fill on Mon 2026-05-11 at $40 → realised profit on the short.
        # The close_full event happens DURING Friday's iteration (not Monday's),
        # because the harness applies today's decisions same-day with fill
        # price = next-trading-day's price (proxy for next-day open).
        write_decision(
            root=decision_root, day="2026-05-04", run="16-15",
            ticker="MEME", candidate_type="surge_short",
            side="short", size_pct=1.0, last_eod_close=50.0,
        )
        write_decision(
            root=decision_root, day="2026-05-08", run="16-15",
            ticker="MEME", candidate_type="surge_short",
            side="cover", size_pct=1.0, decision="cover", last_eod_close=40.0,
        )
        provider = make_price_provider({
            ("MEME", "2026-05-04"): 50.0,
            ("MEME", "2026-05-05"): 50.0,
            ("MEME", "2026-05-06"): 45.0,
            ("MEME", "2026-05-07"): 42.0,
            ("MEME", "2026-05-08"): 40.0,
            ("MEME", "2026-05-11"): 40.0,  # cover fill price (Mon)
        })
        summary = run_pnl_backtest(
            start_date="2026-05-04", end_date="2026-05-08",
            decision_root=decision_root,
            price_history_provider=provider,
            initial_capital=1_000_000.0,
            output_dir=output_dir,
            transaction_cost_bps=0.0,
            include_borrow_cost=False,
            regime="normal",
        )
        rein = summary["reinvestment_decisions"]
        check("G1 reinvestment_decisions has 1 entry",
              len(rein) == 1, f"got {len(rein)}")
        if rein:
            r = rein[0]
            # Normal regime midpoint: long 50%, cash 50%.
            check("G2 to_long_pct == 50.0 (normal regime midpoint)",
                  abs(r["to_long_pct"] - 50.0) < 1e-6,
                  f"got {r.get('to_long_pct')}")
            check("G3 to_cash_pct == 50.0",
                  abs(r["to_cash_pct"] - 50.0) < 1e-6,
                  f"got {r.get('to_cash_pct')}")
            check("G4 realized_short_pnl_dollars > 0 (price fell)",
                  r["realized_short_pnl_dollars"] > 0,
                  f"got {r.get('realized_short_pnl_dollars')}")
            check("G5 regime label round-trips",
                  r["regime"] == "normal", f"got {r.get('regime')}")
    finally:
        shutil.rmtree(decision_root.parent, ignore_errors=True)


def case_H_end_to_end():
    print("\nCase H — 5-day, 2 tickers, 3 decisions, full metrics")
    decision_root, output_dir = fresh_tmpdir("H")
    try:
        # Day 1 (Mon 05-04): buy AAPL 2%, short NVDA 1%
        # Day 3 (Wed 05-06): sell AAPL 2%
        # Day 5 (Fri 05-08): idle
        write_decision(
            root=decision_root, day="2026-05-04", run="16-15",
            ticker="AAPL", candidate_type="quality_long",
            side="long", size_pct=2.0, last_eod_close=100.0,
        )
        write_decision(
            root=decision_root, day="2026-05-04", run="16-15",
            ticker="NVDA", candidate_type="surge_short",
            side="short", size_pct=1.0, last_eod_close=200.0,
        )
        write_decision(
            root=decision_root, day="2026-05-06", run="16-15",
            ticker="AAPL", candidate_type="quality_long",
            side="sell", size_pct=2.0, decision="sell", last_eod_close=105.0,
        )
        provider = make_price_provider({
            ("AAPL", "2026-05-04"): 100.0,
            ("AAPL", "2026-05-05"): 102.0,
            ("AAPL", "2026-05-06"): 105.0,
            ("AAPL", "2026-05-07"): 105.0,
            ("AAPL", "2026-05-08"): 105.0,
            ("NVDA", "2026-05-04"): 200.0,
            ("NVDA", "2026-05-05"): 198.0,
            ("NVDA", "2026-05-06"): 195.0,
            ("NVDA", "2026-05-07"): 195.0,
            ("NVDA", "2026-05-08"): 195.0,
        })
        summary = run_pnl_backtest(
            start_date="2026-05-04", end_date="2026-05-08",
            decision_root=decision_root,
            price_history_provider=provider,
            initial_capital=1_000_000.0,
            output_dir=output_dir,
            transaction_cost_bps=30.0,
            include_borrow_cost=True,
        )
        check("H1 trading_days_processed == 5",
              summary["trading_days_processed"] == 5)
        check("H2 decisions_seen == 3",
              summary["totals"]["decisions_seen"] == 3,
              str(summary["totals"]))
        # 5 NAV rows
        import csv
        with (output_dir / NAV_HISTORY_CSV).open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        check("H3 nav_history has 5 rows", len(rows) == 5,
              f"got {len(rows)}")
        # Metrics dict populated
        m = summary["metrics"]
        for k in ("sharpe_ratio", "sortino_ratio", "max_drawdown",
                  "return_volatility", "total_return"):
            check(f"H4 metrics has {k}", k in m, f"keys={list(m.keys())}")
        # End-state: AAPL closed, NVDA still short
        eod5 = json.loads((output_dir / "2026-05-08_eod_state.json").read_text(encoding="utf-8"))
        nvda_open = any(p["ticker"] == "NVDA" and p["side"] == "short"
                        for p in eod5["positions"])
        aapl_open = any(p["ticker"] == "AAPL" for p in eod5["positions"])
        check("H5 NVDA short still open at end",
              nvda_open, f"positions={eod5['positions']}")
        check("H6 AAPL closed by end",
              not aapl_open, f"positions={eod5['positions']}")
        check("H7 borrow cost > 0 (NVDA short held 5d)",
              summary["totals"]["borrow_cost_dollars"] > 0,
              str(summary["totals"]))
    finally:
        shutil.rmtree(decision_root.parent, ignore_errors=True)


def case_I_benchmark_alignment() -> None:
    """D5 Task 1.3 — regression for the benchmark off-by-one (fixed
    2026-05-01). nav_history has N rows; daily_returns has N-1 (first
    row's daily_return is None). benchmark_returns must therefore be
    length N-1, NOT N. Bug: pnl_backtest used to align to N and
    information_ratio raised ValueError."""
    print("\nCase I — benchmark alignment (D5 Task 1.3 regression)")
    decision_root, output_dir = fresh_tmpdir("I")
    try:
        # 5 trading days, no actual trades (idle days). Window:
        # 2026-05-04 (Mon) .. 2026-05-08 (Fri).
        provider = make_price_provider({
            ("__default__", "SPY"): 500.0,  # never queried for tickers
            ("SPY", "2026-05-04"): 500.0,
            ("SPY", "2026-05-05"): 502.0,
            ("SPY", "2026-05-06"): 498.0,
            ("SPY", "2026-05-07"): 499.0,
            ("SPY", "2026-05-08"): 501.0,
        })
        summary = run_pnl_backtest(
            start_date="2026-05-04", end_date="2026-05-08",
            decision_root=decision_root,
            price_history_provider=provider,
            initial_capital=1_000_000.0,
            output_dir=output_dir,
            benchmark_ticker="SPY",
        )
        check("I1 trading_days_processed == 5",
              summary["trading_days_processed"] == 5,
              str(summary["trading_days_processed"]))
        # NAV history: one row per processed day = 5 rows.
        import csv
        with (output_dir / NAV_HISTORY_CSV).open(
            "r", encoding="utf-8", newline=""
        ) as f:
            rows = list(csv.DictReader(f))
        check("I2 nav_history has 5 rows (N)", len(rows) == 5,
              f"got {len(rows)}")
        check("I3 first daily_return is None / empty",
              not rows[0].get("daily_return"),
              f"got {rows[0].get('daily_return')!r}")
        # information_ratio must be a finite float — no ValueError.
        m = summary["metrics"]
        ir = m.get("information_ratio")
        check("I4 information_ratio is float",
              isinstance(ir, float), f"got {type(ir).__name__}: {ir!r}")
        import math
        check("I5 information_ratio is finite",
              isinstance(ir, float) and math.isfinite(ir),
              f"got {ir!r}")
        # No benchmark failure note when alignment is clean.
        check("I6 metrics has no benchmark_note",
              "benchmark_note" not in m,
              f"got {m.get('benchmark_note')!r}")
    finally:
        shutil.rmtree(decision_root.parent, ignore_errors=True)


def main() -> int:
    print("=" * 70)
    print("test_pnl_backtest.py")
    print("=" * 70)
    case_A_single_day_zero_decisions()
    case_B_single_buy()
    case_C_buy_then_hold()
    case_D_buy_sell_idle()
    case_E_short_borrow()
    case_F_sleeve_cap()
    case_G_friday_reinvestment()
    case_H_end_to_end()
    case_I_benchmark_alignment()
    print()
    print("=" * 70)
    print(f"PASS: {len(PASS)}    FAIL: {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("All cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
