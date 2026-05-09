"""
Unit tests for src/portfolio/metrics.py

Direct-execution / __main__-style. Run:
    python tests/test_metrics.py
Exit 0 on all-pass.

No FMP / no LLM / no HTTP.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from portfolio.metrics import (  # noqa: E402
    DEFAULT_PERIODS_PER_YEAR, compute_all_metrics, information_ratio,
    max_drawdown, return_volatility, sharpe_ratio, sortino_ratio,
    total_return,
)
from portfolio.nav_history import append_nav_row  # noqa: E402


PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


def approx(a, b, eps=1e-9):
    if a is None or b is None:
        return a == b
    return abs(a - b) < eps


# ─────────────────────────────────────────────────────────────────
# A. Sharpe ratio
# ─────────────────────────────────────────────────────────────────
def case_A1_sharpe_known() -> None:
    print("\nCase A1 — Sharpe matches manual calc")
    # Simple symmetric returns, mean 0.001, std small.
    returns = [0.001, -0.002, 0.003, -0.001, 0.002, 0.001]
    expected_mean = float(np.mean(returns))           # 0.0006666...
    expected_std = float(np.std(returns, ddof=1))      # 0.001861...
    expected = (expected_mean * 252) / (expected_std * math.sqrt(252))
    got = sharpe_ratio(returns)
    check("A1 matches manual",
          approx(got, expected, eps=1e-12),
          f"got {got!r}, expected {expected!r}")


def case_A2_sharpe_rf() -> None:
    print("\nCase A2 — Sharpe with non-zero risk-free")
    returns = [0.005, 0.005, 0.005, 0.005]
    # Constant returns => std=0; mean_excess = 0.005 - 0.003 = 0.002 > 0 => +inf
    got = sharpe_ratio(returns, risk_free_rate=0.003)
    check("A2 inf when std=0 and mean_excess > 0",
          got == math.inf, f"got {got!r}")
    # Non-trivial: rf reduces excess
    rs = [0.01, -0.01, 0.02, -0.02]
    s_no_rf = sharpe_ratio(rs, risk_free_rate=0.0)
    s_with_rf = sharpe_ratio(rs, risk_free_rate=0.001)
    check("A2 rf>0 reduces Sharpe (since mean_excess shrinks)",
          s_with_rf < s_no_rf, f"no_rf={s_no_rf} with_rf={s_with_rf}")


def case_A3_sharpe_zero() -> None:
    print("\nCase A3 — Sharpe of all-zero returns is 0.0")
    got = sharpe_ratio([0.0, 0.0, 0.0, 0.0])
    check("A3 == 0.0 not NaN", got == 0.0, f"got {got!r}")


def case_A4_sharpe_single() -> None:
    print("\nCase A4 — Sharpe of single value is None")
    got = sharpe_ratio([0.01])
    check("A4 == None", got is None, f"got {got!r}")


# ─────────────────────────────────────────────────────────────────
# B. Sortino ratio
# ─────────────────────────────────────────────────────────────────
def case_B1_sortino_mixed() -> None:
    print("\nCase B1 — Sortino matches manual on mixed returns")
    returns = np.array([0.01, -0.02, 0.015, -0.005, 0.02, -0.01])
    target = 0.0
    excess = returns - target
    downside = excess[excess < 0]            # [-0.02, -0.005, -0.01]
    expected_mean = float(np.mean(excess))
    expected_dstd = float(np.std(downside, ddof=1))
    expected = (expected_mean * 252) / (expected_dstd * math.sqrt(252))
    got = sortino_ratio(returns.tolist())
    check("B1 matches manual", approx(got, expected, eps=1e-12),
          f"got {got!r}, expected {expected!r}")


def case_B2_sortino_all_positive() -> None:
    print("\nCase B2 — Sortino all-positive returns is +inf")
    got = sortino_ratio([0.01, 0.02, 0.005, 0.015])
    check("B2 == +inf", got == math.inf, f"got {got!r}")


def case_B3_sortino_all_negative() -> None:
    print("\nCase B3 — Sortino all-negative returns is finite negative")
    returns = [-0.01, -0.02, -0.005, -0.015]
    got = sortino_ratio(returns)
    check("B3 finite negative",
          got is not None and math.isfinite(got) and got < 0,
          f"got {got!r}")


def case_B4_sortino_target_shifts() -> None:
    print("\nCase B4 — Sortino target_return shifts downside threshold")
    returns = [0.01, 0.005, 0.015, 0.012, 0.008, 0.011]
    # All > 0 => default target=0 returns +inf
    s_default = sortino_ratio(returns)
    # target = 0.013 makes 4 of 6 returns below target => finite
    s_high_target = sortino_ratio(returns, target_return=0.013)
    check("B4a default target → +inf", s_default == math.inf,
          f"got {s_default!r}")
    check("B4b higher target → finite",
          s_high_target is not None and math.isfinite(s_high_target),
          f"got {s_high_target!r}")


# ─────────────────────────────────────────────────────────────────
# C. Max drawdown
# ─────────────────────────────────────────────────────────────────
def case_C1_drawdown_no_recovery() -> None:
    print("\nCase C1 — NAV [100, 110, 105, 120, 90, 100], no full recovery")
    nav = [100.0, 110.0, 105.0, 120.0, 90.0, 100.0]
    dates = ["d0", "d1", "d2", "d3", "d4", "d5"]
    out = max_drawdown(nav, dates=dates)
    check("C1a value == -0.25", approx(out["value"], -0.25),
          f"got {out['value']!r}")
    check("C1b peak_date == d3 (NAV 120)", out["peak_date"] == "d3",
          f"got {out['peak_date']!r}")
    check("C1c trough_date == d4 (NAV 90)", out["trough_date"] == "d4",
          f"got {out['trough_date']!r}")
    check("C1d recovery_date is None", out["recovery_date"] is None,
          f"got {out['recovery_date']!r}")


def case_C2_drawdown_with_recovery() -> None:
    print("\nCase C2 — NAV recovers fully → recovery_date set")
    nav = [100.0, 120.0, 90.0, 110.0, 125.0]
    dates = ["d0", "d1", "d2", "d3", "d4"]
    out = max_drawdown(nav, dates=dates)
    check("C2a value == -0.25", approx(out["value"], -0.25),
          f"got {out['value']!r}")
    check("C2b peak_date == d1", out["peak_date"] == "d1",
          f"got {out['peak_date']!r}")
    check("C2c trough_date == d2", out["trough_date"] == "d2",
          f"got {out['trough_date']!r}")
    check("C2d recovery_date == d4 (first ≥ 120 after trough)",
          out["recovery_date"] == "d4",
          f"got {out['recovery_date']!r}")


def case_C3_drawdown_monotonic() -> None:
    print("\nCase C3 — Monotonically increasing NAV → value 0")
    nav = [100.0, 105.0, 110.0, 115.0]
    out = max_drawdown(nav, dates=["d0", "d1", "d2", "d3"])
    check("C3a value == 0.0", out["value"] == 0.0, f"got {out['value']!r}")
    check("C3b peak_date == d0 (per spec)", out["peak_date"] == "d0",
          f"got {out['peak_date']!r}")
    check("C3c trough_date == d0", out["trough_date"] == "d0",
          f"got {out['trough_date']!r}")
    check("C3d recovery_date None", out["recovery_date"] is None,
          f"got {out['recovery_date']!r}")


def case_C4_drawdown_single() -> None:
    print("\nCase C4 — single NAV → value 0, peak=trough=that date")
    out = max_drawdown([100.0], dates=["only"])
    check("C4a value == 0", out["value"] == 0.0, f"got {out['value']!r}")
    check("C4b peak == only", out["peak_date"] == "only",
          f"got {out['peak_date']!r}")
    check("C4c trough == only", out["trough_date"] == "only",
          f"got {out['trough_date']!r}")
    check("C4d recovery None", out["recovery_date"] is None,
          f"got {out['recovery_date']!r}")


def case_C5_drawdown_empty() -> None:
    print("\nCase C5 — empty NAV → value None")
    out = max_drawdown([])
    check("C5 value None and dates None",
          out["value"] is None and out["peak_date"] is None
          and out["trough_date"] is None and out["recovery_date"] is None,
          f"got {out!r}")


# ─────────────────────────────────────────────────────────────────
# D. Volatility
# ─────────────────────────────────────────────────────────────────
def case_D1_vol_known() -> None:
    print("\nCase D1 — vol matches numpy std × sqrt(252)")
    returns = [0.01, -0.005, 0.015, -0.02, 0.008]
    expected = float(np.std(returns, ddof=1)) * math.sqrt(252)
    got = return_volatility(returns)
    check("D1 matches", approx(got, expected, eps=1e-12),
          f"got {got!r}, expected {expected!r}")


def case_D2_vol_constant() -> None:
    print("\nCase D2 — constant returns → vol 0.0")
    got = return_volatility([0.005, 0.005, 0.005])
    check("D2 == 0.0", got == 0.0, f"got {got!r}")


def case_D3_vol_uses_ddof1() -> None:
    print("\nCase D3 — vol uses sample stdev (ddof=1), not population")
    returns = [0.01, 0.02, 0.03]
    pop_vol = float(np.std(returns, ddof=0)) * math.sqrt(252)
    sample_vol = float(np.std(returns, ddof=1)) * math.sqrt(252)
    got = return_volatility(returns)
    check("D3a equals sample (ddof=1)",
          approx(got, sample_vol, eps=1e-12),
          f"got {got}, sample={sample_vol}, pop={pop_vol}")
    check("D3b NOT equal to population",
          not approx(got, pop_vol, eps=1e-12),
          "got equals pop_vol — wrong ddof")


def case_D4_vol_single() -> None:
    print("\nCase D4 — single return → None")
    check("D4 == None", return_volatility([0.01]) is None,
          f"got {return_volatility([0.01])!r}")


# ─────────────────────────────────────────────────────────────────
# E. Total return
# ─────────────────────────────────────────────────────────────────
def case_E1_total_return_positive() -> None:
    print("\nCase E1 — NAV $1M → $1.05M => +0.05")
    got = total_return([1_000_000.0, 1_050_000.0])
    check("E1 == 0.05", approx(got, 0.05), f"got {got!r}")


def case_E2_total_return_negative() -> None:
    print("\nCase E2 — NAV $1M → $0.9M => -0.10")
    got = total_return([1_000_000.0, 900_000.0])
    check("E2 == -0.10", approx(got, -0.10), f"got {got!r}")


def case_E3_total_return_single() -> None:
    print("\nCase E3 — single NAV → 0.0")
    check("E3 == 0.0", total_return([1_000_000.0]) == 0.0,
          f"got {total_return([1_000_000.0])!r}")


def case_E4_total_return_empty() -> None:
    print("\nCase E4 — empty NAV → None")
    check("E4 == None", total_return([]) is None, f"got {total_return([])!r}")


# ─────────────────────────────────────────────────────────────────
# F. Information ratio
# ─────────────────────────────────────────────────────────────────
def case_F1_ir_constant_outperformance() -> None:
    print("\nCase F1 — portfolio outperforms benchmark by constant amount → +inf")
    p = [0.011, 0.011, 0.011, 0.011]
    b = [0.010, 0.010, 0.010, 0.010]
    got = information_ratio(p, b)
    check("F1 == +inf (zero tracking error, positive active return)",
          got == math.inf, f"got {got!r}")


def case_F2_ir_match_benchmark() -> None:
    print("\nCase F2 — portfolio == benchmark exactly → 0.0")
    p = [0.01, -0.005, 0.02, -0.01, 0.015]
    got = information_ratio(p, list(p))
    check("F2 == 0.0", got == 0.0, f"got {got!r}")


def case_F3_ir_length_mismatch() -> None:
    print("\nCase F3 — mismatched lengths → ValueError")
    raised = False
    try:
        information_ratio([0.01, 0.02], [0.01, 0.02, 0.03])
    except ValueError:
        raised = True
    check("F3 raises ValueError", raised, "no exception")


def case_F4_ir_realistic() -> None:
    print("\nCase F4 — realistic active-return + tracking error case")
    p = np.array([0.012, -0.003, 0.018, -0.008, 0.020, 0.005])
    b = np.array([0.010, -0.004, 0.014, -0.006, 0.018, 0.004])
    active = p - b
    expected = (
        float(np.mean(active)) * 252
        / (float(np.std(active, ddof=1)) * math.sqrt(252))
    )
    got = information_ratio(p.tolist(), b.tolist())
    check("F4 matches manual", approx(got, expected, eps=1e-12),
          f"got {got!r}, expected {expected!r}")


# ─────────────────────────────────────────────────────────────────
# G. Integration
# ─────────────────────────────────────────────────────────────────
def case_G1_compute_all_single_row() -> None:
    print("\nCase G1 — compute_all_metrics on a synthetic 1-row CSV")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        eod = tmp / "2026-04-29_eod_state.json"
        eod.write_text(json.dumps({
            "schema_version": "eod_state_v1",
            "as_of": "2026-04-29",
            "rule_version": "v0.8.3_13f_cadence",
            "cash_balance": 1_000_000.0,
            "total_nav": 1_000_000.0,
            "positions": [],
            "sleeve_exposure": {"quality_long": 0, "surge_short": 0,
                                "fixed_income": 0},
            "concentration": {},
            "audit": {"decisions_processed": [], "events": [],
                       "prior_state_loaded_from": None},
        }), encoding="utf-8")
        csv = tmp / "pnl_history.csv"
        append_nav_row(eod, csv)
        out = compute_all_metrics(csv)
        check("G1a as_of_count == 1", out["as_of_count"] == 1,
              f"got {out['as_of_count']}")
        check("G1b total_return == 0.0", out["total_return"] == 0.0,
              f"got {out['total_return']!r}")
        check("G1c max_drawdown.value == 0.0",
              out["max_drawdown"]["value"] == 0.0,
              f"got {out['max_drawdown']!r}")
        check("G1d sharpe is None (single row)",
              out["sharpe_ratio"] is None, f"got {out['sharpe_ratio']!r}")
        check("G1e sortino is None", out["sortino_ratio"] is None,
              f"got {out['sortino_ratio']!r}")
        check("G1f volatility is None", out["return_volatility"] is None,
              f"got {out['return_volatility']!r}")
        check("G1g information_ratio key absent (no benchmark)",
              "information_ratio" not in out,
              f"keys: {sorted(out)}")


def case_G2_compute_all_100_days() -> None:
    print("\nCase G2 — synthetic 100-day random-walk NAV produces sane metrics")
    rng = np.random.default_rng(42)
    daily_returns = rng.normal(loc=0.0005, scale=0.012, size=100)
    nav = [1_000_000.0]
    for r in daily_returns:
        nav.append(nav[-1] * (1.0 + r))
    # Build CSV from synthetic NAV walk via append_nav_row.
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out_csv = tmp / "pnl_history.csv"
        d0 = date(2025, 1, 1)
        for i, n in enumerate(nav):
            day = d0 + timedelta(days=i)
            eod = tmp / f"{day.isoformat()}_eod_state.json"
            eod.write_text(json.dumps({
                "schema_version": "eod_state_v1",
                "as_of": day.isoformat(),
                "rule_version": "v0.8.3_13f_cadence",
                "cash_balance": float(n),
                "total_nav": float(n),
                "positions": [],
                "sleeve_exposure": {"quality_long": 0, "surge_short": 0,
                                    "fixed_income": 0},
                "concentration": {},
                "audit": {"decisions_processed": [], "events": [],
                           "prior_state_loaded_from": None},
            }), encoding="utf-8")
            append_nav_row(eod, out_csv)

        out = compute_all_metrics(out_csv)
        check("G2a as_of_count == 101", out["as_of_count"] == 101,
              f"got {out['as_of_count']}")
        check("G2b sharpe is finite float",
              isinstance(out["sharpe_ratio"], float)
              and math.isfinite(out["sharpe_ratio"]),
              f"got {out['sharpe_ratio']!r}")
        check("G2c sortino is finite float (or +inf if no down days)",
              isinstance(out["sortino_ratio"], float),
              f"got {out['sortino_ratio']!r}")
        check("G2d volatility > 0",
              out["return_volatility"] > 0,
              f"got {out['return_volatility']!r}")
        check("G2e total_return is float",
              isinstance(out["total_return"], float),
              f"got {out['total_return']!r}")
        check("G2f max_drawdown.value ≤ 0",
              out["max_drawdown"]["value"] <= 0,
              f"got {out['max_drawdown']['value']!r}")


def case_G3_compute_all_with_benchmark() -> None:
    print("\nCase G3 — compute_all_metrics with benchmark_returns")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out_csv = tmp / "pnl_history.csv"
        # 5-day NAV walk
        nav_seq = [1_000_000.0, 1_010_000.0, 1_005_000.0, 1_020_000.0, 1_015_000.0]
        d0 = date(2026, 1, 1)
        for i, n in enumerate(nav_seq):
            day = d0 + timedelta(days=i)
            eod = tmp / f"{day.isoformat()}_eod_state.json"
            eod.write_text(json.dumps({
                "schema_version": "eod_state_v1",
                "as_of": day.isoformat(),
                "rule_version": "v0.8.3_13f_cadence",
                "cash_balance": float(n),
                "total_nav": float(n),
                "positions": [],
                "sleeve_exposure": {"quality_long": 0, "surge_short": 0,
                                    "fixed_income": 0},
                "concentration": {},
                "audit": {"decisions_processed": [], "events": [],
                           "prior_state_loaded_from": None},
            }), encoding="utf-8")
            append_nav_row(eod, out_csv)
        # Benchmark must align to daily_return series (4 rows post-first-row).
        benchmark = [0.005, -0.002, 0.010, -0.008]
        out = compute_all_metrics(out_csv, benchmark_returns=benchmark)
        check("G3a information_ratio key present",
              "information_ratio" in out, f"keys: {sorted(out)}")
        check("G3b information_ratio is finite float",
              isinstance(out["information_ratio"], float)
              and math.isfinite(out["information_ratio"]),
              f"got {out['information_ratio']!r}")


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────
def main() -> int:
    print("=" * 70)
    print("test_metrics.py")
    print("=" * 70)
    case_A1_sharpe_known()
    case_A2_sharpe_rf()
    case_A3_sharpe_zero()
    case_A4_sharpe_single()
    case_B1_sortino_mixed()
    case_B2_sortino_all_positive()
    case_B3_sortino_all_negative()
    case_B4_sortino_target_shifts()
    case_C1_drawdown_no_recovery()
    case_C2_drawdown_with_recovery()
    case_C3_drawdown_monotonic()
    case_C4_drawdown_single()
    case_C5_drawdown_empty()
    case_D1_vol_known()
    case_D2_vol_constant()
    case_D3_vol_uses_ddof1()
    case_D4_vol_single()
    case_E1_total_return_positive()
    case_E2_total_return_negative()
    case_E3_total_return_single()
    case_E4_total_return_empty()
    case_F1_ir_constant_outperformance()
    case_F2_ir_match_benchmark()
    case_F3_ir_length_mismatch()
    case_F4_ir_realistic()
    case_G1_compute_all_single_row()
    case_G2_compute_all_100_days()
    case_G3_compute_all_with_benchmark()

    print("\n" + "=" * 70)
    print(f"PASS: {len(PASS)}    FAIL: {len(FAIL)}")
    if FAIL:
        print("\nFailures:")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("All cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
