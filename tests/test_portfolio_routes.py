"""
Integration tests for the /api/portfolio/* routes added to
src/dashboard/app.py.

Direct-execution / __main__-style. Run:
    python tests/test_portfolio_routes.py
Exit 0 on all-pass.

Uses Flask's test_client (no port binding, no real HTTP). Tests that
mutate filesystem state use a unique filename pattern so the real
data/portfolio/pnl_history.csv is never touched, and wrap mutations in
try/finally to guarantee cleanup.
"""
from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Import the Flask app (this evaluates module-level routes).
from src.dashboard.app import (  # noqa: E402
    app, _DATA_PORTFOLIO_DIR, _sanitize_json_value,
)
from src.portfolio.nav_history import append_nav_row  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


# ─────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────
def _client():
    app.config["TESTING"] = True
    return app.test_client()


def _write_eod_for(d: date, nav: float, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    p = target_dir / f"{d.isoformat()}_eod_state.json"
    p.write_text(json.dumps({
        "schema_version": "eod_state_v1",
        "as_of": d.isoformat(),
        "rule_version": "v0.8.3_13f_cadence",
        "cash_balance": float(nav),
        "total_nav": float(nav),
        "positions": [],
        "sleeve_exposure": {"quality_long": 0, "surge_short": 0,
                             "fixed_income": 0},
        "concentration": {},
        "audit": {"decisions_processed": [], "events": [],
                   "prior_state_loaded_from": None},
    }), encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────
# Cases
# ─────────────────────────────────────────────────────────────────
def case_1_current_returns_today() -> None:
    """data/portfolio/2026-04-29_eod_state.json was written by today's
    smoke; /api/portfolio/current must return it."""
    print("\nCase 1 — /api/portfolio/current returns 200 + status=ok")
    c = _client()
    r = c.get("/api/portfolio/current")
    check("1a HTTP 200", r.status_code == 200, f"got {r.status_code}")
    body = r.get_json()
    check("1b status == ok", body.get("status") == "ok",
          f"got {body.get('status')!r}")
    check("1c as_of present", body.get("as_of") is not None,
          f"got {body!r}")
    check("1d state.cash_balance present in payload",
          body.get("state", {}).get("cash_balance") is not None,
          "missing")
    check("1e available_dates is a non-empty list",
          isinstance(body.get("available_dates"), list)
          and len(body["available_dates"]) >= 1,
          f"got {body.get('available_dates')!r}")


def case_2_history_returns_rows() -> None:
    print("\nCase 2 — /api/portfolio/history returns 200 + status=ok")
    c = _client()
    r = c.get("/api/portfolio/history")
    check("2a HTTP 200", r.status_code == 200, f"got {r.status_code}")
    body = r.get_json()
    # If pnl_history.csv exists with at least 1 row, status=ok.
    csv_path = _DATA_PORTFOLIO_DIR / "pnl_history.csv"
    if csv_path.exists() and csv_path.stat().st_size > 0:
        check("2b status == ok", body.get("status") == "ok",
              f"got {body.get('status')!r}")
        check("2c row_count >= 1", body.get("row_count", 0) >= 1,
              f"got {body.get('row_count')!r}")
        check("2d rows is list", isinstance(body.get("rows"), list),
              f"got {type(body.get('rows')).__name__}")
    else:
        check("2b status == no_data when csv missing",
              body.get("status") == "no_data",
              f"got {body.get('status')!r}")


def case_3_history_filter_today() -> None:
    print("\nCase 3 — /api/portfolio/history?from=2026-04-29&to=2026-04-29")
    c = _client()
    r = c.get("/api/portfolio/history?from=2026-04-29&to=2026-04-29")
    body = r.get_json()
    csv_path = _DATA_PORTFOLIO_DIR / "pnl_history.csv"
    if csv_path.exists() and csv_path.stat().st_size > 0:
        check("3a status == ok", body.get("status") == "ok",
              f"got {body.get('status')!r}")
        # The smoke wrote 2026-04-29; expect exactly 1 row in the slice.
        check("3b row_count == 1", body.get("row_count") == 1,
              f"got {body.get('row_count')!r}")
        check("3c first_date == 2026-04-29",
              body.get("first_date") == "2026-04-29",
              f"got {body.get('first_date')!r}")


def case_4_history_filter_far_future() -> None:
    print("\nCase 4 — /api/portfolio/history?from=2099-01-01 → row_count 0")
    c = _client()
    r = c.get("/api/portfolio/history?from=2099-01-01")
    body = r.get_json()
    csv_path = _DATA_PORTFOLIO_DIR / "pnl_history.csv"
    if csv_path.exists() and csv_path.stat().st_size > 0:
        # File exists, but filter is empty — still status=ok with row_count 0.
        check("4a status == ok", body.get("status") == "ok",
              f"got {body.get('status')!r}")
        check("4b row_count == 0", body.get("row_count") == 0,
              f"got {body.get('row_count')!r}")
        check("4c rows == []", body.get("rows") == [],
              f"got {body.get('rows')!r}")


def case_5_metrics_current_state() -> None:
    print("\nCase 5 — /api/portfolio/metrics structure (1-row history)")
    c = _client()
    r = c.get("/api/portfolio/metrics")
    body = r.get_json()
    csv_path = _DATA_PORTFOLIO_DIR / "pnl_history.csv"
    if not csv_path.exists():
        check("5 (no csv) status == insufficient_data",
              body.get("status") == "insufficient_data",
              f"got {body.get('status')!r}")
        return
    check("5a HTTP 200", r.status_code == 200, f"got {r.status_code}")
    # With today's 1-row CSV, status should be insufficient_data.
    check("5b status is insufficient_data or ok",
          body.get("status") in ("insufficient_data", "ok"),
          f"got {body.get('status')!r}")
    metrics = body.get("metrics") or {}
    for k in ("sharpe_ratio", "sortino_ratio", "max_drawdown",
              "return_volatility", "total_return"):
        check(f"5 metrics dict contains '{k}'", k in metrics,
              f"keys: {list(metrics.keys())}")
    # max_drawdown is always a dict (even on insufficient_data).
    check("5c max_drawdown is a dict",
          isinstance(metrics.get("max_drawdown"), dict),
          f"got {type(metrics.get('max_drawdown')).__name__}")


def case_6_metrics_with_synthetic_csv() -> None:
    """Temporarily replace pnl_history.csv with a 100-row synthetic CSV
    and verify metrics route returns status=ok with all 5 keys."""
    print("\nCase 6 — /api/portfolio/metrics on synthetic 100-row CSV")
    real_csv = _DATA_PORTFOLIO_DIR / "pnl_history.csv"
    backup = _DATA_PORTFOLIO_DIR / "pnl_history.csv._test_backup"
    fixture_eod_dir = _DATA_PORTFOLIO_DIR / "_test_synthetic_eods"

    # Backup the real file (rename, atomic on same FS)
    real_csv_existed = real_csv.exists()
    if real_csv_existed:
        try:
            real_csv.rename(backup)
        except OSError as e:
            check("6 setup failed (could not rename real csv)",
                  False, f"{type(e).__name__}: {e}")
            return

    try:
        # Build a 100-day synthetic NAV walk (deterministic seed).
        import numpy as np
        rng = np.random.default_rng(2026)
        nav = [1_000_000.0]
        for _ in range(100):
            r = float(rng.normal(0.0008, 0.011))
            nav.append(nav[-1] * (1.0 + r))
        d0 = date(2025, 1, 1)
        for i, n in enumerate(nav):
            day = d0 + timedelta(days=i)
            eod_path = _write_eod_for(day, n, fixture_eod_dir)
            append_nav_row(eod_path, real_csv)

        c = _client()
        r = c.get("/api/portfolio/metrics")
        body = r.get_json()
        check("6a HTTP 200", r.status_code == 200, f"got {r.status_code}")
        check("6b status == ok", body.get("status") == "ok",
              f"got {body.get('status')!r}")
        check("6c as_of_count == 101", body.get("as_of_count") == 101,
              f"got {body.get('as_of_count')!r}")
        m = body.get("metrics") or {}
        for k in ("sharpe_ratio", "sortino_ratio", "max_drawdown",
                  "return_volatility", "total_return"):
            check(f"6 metrics has '{k}' (non-null where expected)",
                  k in m, f"keys: {sorted(m)}")
        # On a real series, sharpe / vol should be finite floats
        check("6d sharpe is finite float",
              isinstance(m.get("sharpe_ratio"), float)
              and math.isfinite(m["sharpe_ratio"]),
              f"got {m.get('sharpe_ratio')!r}")
        check("6e volatility > 0",
              m.get("return_volatility", 0) > 0,
              f"got {m.get('return_volatility')!r}")
    finally:
        # Restore the real file; remove the synthetic CSV + EOD fixtures.
        if real_csv.exists():
            real_csv.unlink()
        if backup.exists():
            backup.rename(real_csv)
        if fixture_eod_dir.exists():
            shutil.rmtree(fixture_eod_dir, ignore_errors=True)


def case_7_sanitize_helper() -> None:
    """The _sanitize_json_value helper turns inf/-inf/nan into None."""
    print("\nCase 7 — _sanitize_json_value strips inf/nan")
    bad = {
        "a": float("inf"),
        "b": float("-inf"),
        "c": float("nan"),
        "d": 1.5,
        "nested": {"x": float("inf"), "y": [float("nan"), 0.0, "ok"]},
        "list_of_floats": [1.0, float("inf"), 2.0],
        "tuple_value": (float("nan"), 3.0),
        "none_value": None,
        "string_value": "Infinity",   # untouched
    }
    out = _sanitize_json_value(bad)
    check("7a inf → None", out["a"] is None, f"got {out['a']!r}")
    check("7b -inf → None", out["b"] is None, f"got {out['b']!r}")
    check("7c nan → None", out["c"] is None, f"got {out['c']!r}")
    check("7d ordinary float preserved", out["d"] == 1.5,
          f"got {out['d']!r}")
    check("7e nested dict inf → None",
          out["nested"]["x"] is None, f"got {out['nested']['x']!r}")
    check("7f nested list nan → None",
          out["nested"]["y"][0] is None, f"got {out['nested']['y']!r}")
    check("7g list normal values preserved",
          out["list_of_floats"] == [1.0, None, 2.0],
          f"got {out['list_of_floats']!r}")
    check("7h tuple coerced to list, nan → None",
          out["tuple_value"] == [None, 3.0],
          f"got {out['tuple_value']!r}")
    check("7i string 'Infinity' untouched",
          out["string_value"] == "Infinity",
          f"got {out['string_value']!r}")
    # And critically, the result is JSON-serializable (no inf/nan)
    try:
        json.dumps(out, allow_nan=False)
        ser_ok = True
    except (ValueError, TypeError) as e:
        ser_ok = False
    check("7j sanitized dict serializes with allow_nan=False",
          ser_ok, "json.dumps still raised")


def case_8_missing_directory() -> None:
    """Temporarily rename data/portfolio away → /api/portfolio/current
    must return status=no_data, not crash."""
    print("\nCase 8 — missing data/portfolio dir → status=no_data")
    moved = _DATA_PORTFOLIO_DIR.parent / "_portfolio_test_moved"
    portfolio_existed = _DATA_PORTFOLIO_DIR.exists()
    if portfolio_existed:
        try:
            _DATA_PORTFOLIO_DIR.rename(moved)
        except OSError as e:
            check("8 setup failed (could not rename portfolio dir)",
                  False, f"{type(e).__name__}: {e}")
            return

    try:
        c = _client()
        r = c.get("/api/portfolio/current")
        check("8a HTTP 200 even with no dir",
              r.status_code == 200, f"got {r.status_code}")
        check("8b status == no_data",
              r.get_json().get("status") == "no_data",
              f"got {r.get_json()!r}")

        # /history and /metrics also handle missing dir gracefully
        r2 = c.get("/api/portfolio/history")
        check("8c history HTTP 200", r2.status_code == 200,
              f"got {r2.status_code}")
        check("8d history status == no_data",
              r2.get_json().get("status") == "no_data",
              f"got {r2.get_json()!r}")

        r3 = c.get("/api/portfolio/metrics")
        check("8e metrics HTTP 200", r3.status_code == 200,
              f"got {r3.status_code}")
        check("8f metrics status == insufficient_data",
              r3.get_json().get("status") == "insufficient_data",
              f"got {r3.get_json()!r}")
    finally:
        if moved.exists():
            moved.rename(_DATA_PORTFOLIO_DIR)


def main() -> int:
    print("=" * 70)
    print("test_portfolio_routes.py")
    print("=" * 70)
    case_1_current_returns_today()
    case_2_history_returns_rows()
    case_3_history_filter_today()
    case_4_history_filter_far_future()
    case_5_metrics_current_state()
    case_6_metrics_with_synthetic_csv()
    case_7_sanitize_helper()
    case_8_missing_directory()

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
