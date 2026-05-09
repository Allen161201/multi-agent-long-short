"""
Unit tests for src/portfolio/nav_history.py

Direct-execution / __main__-style. Run:
    python tests/test_nav_history.py
Exit code 0 on all-pass, 1 on any failure.

No FMP / no HTTP. Synthetic EOD state JSONs are written to a tempdir.
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from portfolio.nav_history import (  # noqa: E402
    CSV_HEADER, append_nav_row, get_nav_at, load_nav_history,
)


PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


def _eod(*, as_of: str, cash: float, total_nav: float,
         positions: list | None = None,
         rule_version: str = "v0.8.3_13f_cadence") -> dict:
    return {
        "schema_version": "eod_state_v1",
        "as_of": as_of,
        "rule_version": rule_version,
        "cash_balance": cash,
        "total_nav": total_nav,
        "positions": positions if positions is not None else [],
        "sleeve_exposure": {"quality_long": 0, "surge_short": 0,
                            "fixed_income": 0},
        "concentration": {},
        "audit": {"decisions_processed": [], "events": [],
                  "prior_state_loaded_from": None},
    }


def _write_eod(tmp: Path, eod: dict) -> Path:
    p = tmp / f"{eod['as_of']}_eod_state.json"
    p.write_text(json.dumps(eod), encoding="utf-8")
    return p


def case_1_first_append() -> None:
    print("\nCase 1 — first append: daily_return empty, cumulative_return 0.0")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        eod_path = _write_eod(tmp, _eod(as_of="2026-04-29",
                                         cash=1_000_000.0,
                                         total_nav=1_000_000.0))
        csv_path = tmp / "pnl_history.csv"
        row = append_nav_row(eod_path, csv_path)

        check("1a row daily_return is None", row["daily_return"] is None,
              f"got {row['daily_return']!r}")
        check("1b row cumulative_return == 0.0",
              row["cumulative_return"] == 0.0,
              f"got {row['cumulative_return']!r}")
        check("1c CSV file exists", csv_path.exists(), str(csv_path))
        with csv_path.open("r", encoding="utf-8") as f:
            text = f.read()
        check("1d header line is correct", text.splitlines()[0]
              == ",".join(CSV_HEADER),
              f"got: {text.splitlines()[0]}")


def case_2_second_append() -> None:
    print("\nCase 2 — second append: daily/cumulative returns vs nav[0]")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_path = tmp / "pnl_history.csv"
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-28",
                                              cash=1_000_000.0,
                                              total_nav=1_000_000.0)),
                       csv_path)
        row = append_nav_row(
            _write_eod(tmp, _eod(as_of="2026-04-29",
                                  cash=1_000_000.0,
                                  total_nav=1_020_000.0)),
            csv_path,
        )
        # daily_return = (1.02M − 1M) / 1M = 0.02
        check("2a daily_return == 0.02",
              abs(row["daily_return"] - 0.02) < 1e-12,
              f"got {row['daily_return']!r}")
        # cumulative_return = (1.02M − 1M) / 1M = 0.02
        check("2b cumulative_return == 0.02",
              abs(row["cumulative_return"] - 0.02) < 1e-12,
              f"got {row['cumulative_return']!r}")


def case_3_third_append_uses_base() -> None:
    print("\nCase 3 — third append: cumulative uses nav[0], not nav[1]")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_path = tmp / "pnl_history.csv"
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-27",
                                              cash=1_000_000.0,
                                              total_nav=1_000_000.0)),
                       csv_path)
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-28",
                                              cash=1_000_000.0,
                                              total_nav=1_020_000.0)),
                       csv_path)
        row = append_nav_row(
            _write_eod(tmp, _eod(as_of="2026-04-29",
                                  cash=1_000_000.0,
                                  total_nav=1_050_000.0)),
            csv_path,
        )
        # daily_return: (1.05M − 1.02M) / 1.02M ≈ 0.029411765
        check("3a daily_return uses prior NAV",
              abs(row["daily_return"] - (1_050_000.0 - 1_020_000.0) / 1_020_000.0)
              < 1e-12, f"got {row['daily_return']!r}")
        # cumulative_return uses base 1.0M, not 1.02M:
        # (1.05M − 1.0M) / 1.0M = 0.05
        check("3b cumulative_return uses base NAV (0.05)",
              abs(row["cumulative_return"] - 0.05) < 1e-12,
              f"got {row['cumulative_return']!r}")


def case_4_replay_replaces() -> None:
    print("\nCase 4 — replay (same as_of as latest): replace, no duplicate")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_path = tmp / "pnl_history.csv"
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-28",
                                              cash=1_000_000.0,
                                              total_nav=1_000_000.0)),
                       csv_path)
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-29",
                                              cash=1_000_000.0,
                                              total_nav=1_020_000.0)),
                       csv_path)
        # Replay 2026-04-29 with a different NAV
        eod_path_v2 = tmp / "2026-04-29_eod_state.json"
        eod_path_v2.write_text(json.dumps(_eod(as_of="2026-04-29",
                                                 cash=1_000_000.0,
                                                 total_nav=1_030_000.0)),
                               encoding="utf-8")
        row = append_nav_row(eod_path_v2, csv_path)

        rows = load_nav_history(csv_path)
        check("4a row count remains 2 (no duplicate)",
              len(rows) == 2, f"got {len(rows)}")
        # Tail row is the replayed one
        check("4b tail row total_nav updated to 1.03M",
              rows[-1]["total_nav"] == 1_030_000.0,
              f"got {rows[-1]['total_nav']!r}")
        # And returns recomputed from prior row (1.0M)
        check("4c daily_return recomputed: (1.03M-1M)/1M",
              abs(rows[-1]["daily_return"] - 0.03) < 1e-12,
              f"got {rows[-1]['daily_return']!r}")
        check("4d returned dict matches",
              row["total_nav"] == 1_030_000.0,
              f"got {row['total_nav']!r}")


def case_5_out_of_order_raises() -> None:
    print("\nCase 5 — earlier as_of after later one raises ValueError")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_path = tmp / "pnl_history.csv"
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-29",
                                              cash=1_000_000.0,
                                              total_nav=1_000_000.0)),
                       csv_path)
        raised = False
        try:
            append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-28",
                                                  cash=1_000_000.0,
                                                  total_nav=1_010_000.0)),
                           csv_path)
        except ValueError:
            raised = True
        check("5 ValueError raised on out-of-order append", raised,
              "no exception")


def case_6_load_nav_history_types() -> None:
    print("\nCase 6 — load_nav_history sorted + types coerced")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_path = tmp / "pnl_history.csv"
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-27",
                                              cash=1_000_000.0,
                                              total_nav=1_000_000.0,
                                              positions=[])),
                       csv_path)
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-28",
                                              cash=900_000.0,
                                              total_nav=1_010_000.0,
                                              positions=[{"x": 1}, {"y": 2}])),
                       csv_path)
        rows = load_nav_history(csv_path)
        check("6a 2 rows", len(rows) == 2, f"got {len(rows)}")
        check("6b sorted by as_of asc",
              rows[0]["as_of"] < rows[1]["as_of"],
              f"got {[r['as_of'] for r in rows]}")
        check("6c total_nav is float", isinstance(rows[0]["total_nav"], float),
              f"got {type(rows[0]['total_nav']).__name__}")
        check("6d num_positions is int",
              isinstance(rows[1]["num_positions"], int)
              and rows[1]["num_positions"] == 2,
              f"got {rows[1]['num_positions']!r}")
        check("6e first row daily_return is None",
              rows[0]["daily_return"] is None,
              f"got {rows[0]['daily_return']!r}")


def case_7_get_nav_at() -> None:
    print("\nCase 7 — get_nav_at lookup")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_path = tmp / "pnl_history.csv"
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-29",
                                              cash=1_000_000.0,
                                              total_nav=1_000_000.0)),
                       csv_path)
        check("7a existing date returns float",
              get_nav_at(csv_path, "2026-04-29") == 1_000_000.0,
              f"got {get_nav_at(csv_path, '2026-04-29')!r}")
        check("7b missing date returns None",
              get_nav_at(csv_path, "2026-01-01") is None,
              f"got {get_nav_at(csv_path, '2026-01-01')!r}")


def case_8_missing_csv_returns_empty() -> None:
    print("\nCase 8 — load_nav_history on missing CSV → []")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        rows = load_nav_history(tmp / "nonexistent.csv")
        check("8 rows == []", rows == [], f"got {rows!r}")


def case_9_first_row_empty_daily_cell() -> None:
    print("\nCase 9 — first row daily_return cell is literally empty in CSV")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_path = tmp / "pnl_history.csv"
        append_nav_row(_write_eod(tmp, _eod(as_of="2026-04-29",
                                              cash=1_000_000.0,
                                              total_nav=1_000_000.0)),
                       csv_path)
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        check("9a cell is empty string (not 'None' / 'null')",
              row["daily_return"] == "",
              f"got {row['daily_return']!r}")


def case_10_growth_chain() -> None:
    print("\nCase 10 — NAV $1M → $1.02M → $0.99M → $1.05M chain")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        csv_path = tmp / "pnl_history.csv"
        sequence = [
            ("2026-04-26", 1_000_000.0),
            ("2026-04-27", 1_020_000.0),
            ("2026-04-28",   990_000.0),
            ("2026-04-29", 1_050_000.0),
        ]
        for d, nav in sequence:
            append_nav_row(
                _write_eod(tmp, _eod(as_of=d, cash=nav, total_nav=nav)),
                csv_path,
            )
        rows = load_nav_history(csv_path)
        check("10a 4 rows", len(rows) == 4, f"got {len(rows)}")
        # Row 0: NAV 1M, daily None, cum 0
        check("10b row0 daily None",
              rows[0]["daily_return"] is None,
              f"got {rows[0]['daily_return']!r}")
        check("10c row0 cum 0",
              rows[0]["cumulative_return"] == 0.0,
              f"got {rows[0]['cumulative_return']!r}")
        # Row 1: NAV 1.02M, daily +2%, cum +2%
        check("10d row1 daily +0.02",
              abs(rows[1]["daily_return"] - 0.02) < 1e-12,
              f"got {rows[1]['daily_return']!r}")
        check("10e row1 cum +0.02",
              abs(rows[1]["cumulative_return"] - 0.02) < 1e-12,
              f"got {rows[1]['cumulative_return']!r}")
        # Row 2: NAV 0.99M, daily = (0.99M − 1.02M)/1.02M ≈ -0.02941
        check("10f row2 daily ≈ -2.94%",
              abs(rows[2]["daily_return"]
                  - ((990_000 - 1_020_000) / 1_020_000)) < 1e-12,
              f"got {rows[2]['daily_return']!r}")
        # Row 2 cum: (0.99M − 1.0M) / 1.0M = -0.01
        check("10g row2 cum -0.01",
              abs(rows[2]["cumulative_return"] - (-0.01)) < 1e-12,
              f"got {rows[2]['cumulative_return']!r}")
        # Row 3: NAV 1.05M, daily = (1.05M − 0.99M)/0.99M
        check("10h row3 daily uses prior 0.99M base",
              abs(rows[3]["daily_return"]
                  - ((1_050_000 - 990_000) / 990_000)) < 1e-12,
              f"got {rows[3]['daily_return']!r}")
        # Row 3 cum: (1.05M − 1.0M) / 1.0M = 0.05
        check("10i row3 cum +0.05",
              abs(rows[3]["cumulative_return"] - 0.05) < 1e-12,
              f"got {rows[3]['cumulative_return']!r}")


def case_11_missing_eod_raises() -> None:
    print("\nCase 11 — missing eod_state file raises FileNotFoundError")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        raised = False
        try:
            append_nav_row(tmp / "nope.json", tmp / "pnl.csv")
        except FileNotFoundError:
            raised = True
        check("11 raises FileNotFoundError", raised, "no exception")


def case_12_malformed_eod_raises() -> None:
    print("\nCase 12 — malformed JSON raises ValueError")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bad = tmp / "bad_eod_state.json"
        bad.write_text("{not json", encoding="utf-8")
        raised = False
        try:
            append_nav_row(bad, tmp / "pnl.csv")
        except ValueError:
            raised = True
        check("12 raises ValueError on bad JSON", raised, "no exception")


def main() -> int:
    print("=" * 70)
    print("test_nav_history.py")
    print("=" * 70)
    case_1_first_append()
    case_2_second_append()
    case_3_third_append_uses_base()
    case_4_replay_replaces()
    case_5_out_of_order_raises()
    case_6_load_nav_history_types()
    case_7_get_nav_at()
    case_8_missing_csv_returns_empty()
    case_9_first_row_empty_daily_cell()
    case_10_growth_chain()
    case_11_missing_eod_raises()
    case_12_malformed_eod_raises()

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
