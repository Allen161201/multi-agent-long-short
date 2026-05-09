"""
Unit test for src/evidence_packet/blocks/price._select_pit_anchor and
the surrounding cutoff-aware logic in price.build.

Direct-execution / __main__-style (matches test_regression_matrix.py and
test_json_parse_helper.py — pytest is not installed). Run with:

    python tests/test_price_snapshot_intraday_cutoff.py

Exit code 0 on all-pass, 1 on any failure.

The 7 cases below exercise the design contract from the P1.5 fix:
  1. Cutoff at 09:30 ET trading-day morning  → prior-day daily anchor
  2. Cutoff at 10:30 ET                       → most recent 5-min bar
  3. Cutoff at 16:15 ET                       → today's daily (tie wins)
  4. Cutoff at 22:00 ET                       → today's daily (regular close)
  5. Cutoff on Saturday                       → Friday's daily
  6. Cutoff in non-ET timezone (CT)           → ET-converted before bar pick
  7. Cutoff exactly equal to a bar's close    → bar IS included (≤ rule)

Synthetic bars are constructed in-memory; FMP is not called. The helper
under test is the pure function _select_pit_anchor, which is what
price.build now uses to compute pit_as_of.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evidence_packet.blocks.price import (  # noqa: E402
    _select_pit_anchor,
    _parse_intraday_bar_close,
    _daily_bar_close,
)

ET = ZoneInfo("America/New_York")


def _intraday_bars_for(dates_and_minutes: list[tuple[str, int, int]]) -> list[dict]:
    """Helper to build synthetic 5-min intraday bars.
    Input: list of (YYYY-MM-DD, hour, minute) tuples. Returns FMP-shape dicts."""
    return [
        {"datetime": f"{d} {h:02d}:{m:02d}:00", "close": 100.0, "volume": 1000}
        for d, h, m in dates_and_minutes
    ]


def _daily_rows_for(dates: list[str]) -> list[dict]:
    return [{"date": d, "close": 100.0, "volume": 1000000} for d in dates]


def _fmt(v) -> str:
    if v is None:
        return "None"
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, tuple):
        return "(" + ", ".join(_fmt(x) for x in v) + ")"
    return repr(v)


def _check(label: str, actual, expected) -> tuple[bool, str]:
    ok = actual == expected
    return ok, f"  {('PASS' if ok else 'FAIL'):4s}  {label}\n         expected={_fmt(expected)}  actual={_fmt(actual)}"


def main() -> int:
    print("\n=== price_snapshot intraday cutoff unit test ===\n")

    results: list[tuple[bool, str]] = []

    # Trading days: 2026-04-27 Mon, 28 Tue, 29 Wed, 30 Thu, 05-01 Fri, 02 Sat, 03 Sun.
    daily = _daily_rows_for([
        "2026-04-24",  # Friday before
        "2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30",
    ])
    # Synthetic intraday for 04-30: 09:30, 09:35, 10:00, 10:25, 10:30, 12:00, 15:55, 16:00.
    intraday_0430 = _intraday_bars_for([
        ("2026-04-30", 9, 30),
        ("2026-04-30", 9, 35),
        ("2026-04-30", 10, 0),
        ("2026-04-30", 10, 25),
        ("2026-04-30", 10, 30),
        ("2026-04-30", 12, 0),
        ("2026-04-30", 15, 55),
        ("2026-04-30", 16, 0),
    ])

    # Case 1: 09:30 ET trading-day morning.
    # FMP convention: 5-min bar's `datetime` is its CLOSE timestamp. A
    # 09:30 close bar covers 09:25-09:30 (pre-market). Strict-PIT
    # treats it as valid available-at-cutoff data. So with synthetic
    # intraday that has a 09:30 bar, the anchor IS that bar. Without
    # any intraday bar at-or-before 09:30, the anchor falls to the
    # prior-day daily — that's the "no intraday yet" sub-case we test
    # by passing intraday_bars=[].
    cutoff = datetime(2026, 4, 30, 9, 30, tzinfo=ET)
    cdt, kind = _select_pit_anchor([], daily, cutoff)
    results.append(_check(
        "case 1 (09:30 ET no intraday) → prior-day daily 04-29T16:15",
        (cdt, kind),
        (datetime(2026, 4, 29, 16, 15, tzinfo=ET), "daily_eod"),
    ))

    cdt, kind = _select_pit_anchor(intraday_0430, daily, cutoff)
    results.append(_check(
        "case 1b (09:30 ET with pre-market 09:30 bar) → 04-30T09:30 intraday",
        (cdt, kind),
        (datetime(2026, 4, 30, 9, 30, tzinfo=ET), "intraday_5min"),
    ))

    # Case 2: 10:30 ET → most recent 5-min bar at-or-before 10:30.
    cutoff = datetime(2026, 4, 30, 10, 30, tzinfo=ET)
    cdt, kind = _select_pit_anchor(intraday_0430, daily, cutoff)
    results.append(_check(
        "case 2 (10:30 ET) → 04-30T10:30 intraday bar",
        (cdt, kind),
        (datetime(2026, 4, 30, 10, 30, tzinfo=ET), "intraday_5min"),
    ))

    # Case 3: 16:15 ET → today's daily (tie at 16:15; daily wins).
    # The 16:00 intraday bar is ≤ 16:15 too, but daily-priority wins
    # (preserves backward-compat with the legacy 16:15 ET hardcode).
    cutoff = datetime(2026, 4, 30, 16, 15, tzinfo=ET)
    cdt, kind = _select_pit_anchor(intraday_0430, daily, cutoff)
    results.append(_check(
        "case 3 (16:15 ET) → 04-30T16:15 daily (tie wins for backward-compat)",
        (cdt, kind),
        (datetime(2026, 4, 30, 16, 15, tzinfo=ET), "daily_eod"),
    ))

    # Case 4: 22:00 ET (after-hours). No intraday bar that late in
    # synthetic data → today's daily.
    cutoff = datetime(2026, 4, 30, 22, 0, tzinfo=ET)
    cdt, kind = _select_pit_anchor(intraday_0430, daily, cutoff)
    results.append(_check(
        "case 4 (22:00 ET after-hours) → 04-30T16:15 daily",
        (cdt, kind),
        (datetime(2026, 4, 30, 16, 15, tzinfo=ET), "daily_eod"),
    ))

    # Case 5: Saturday cutoff → Friday's daily. We only seeded daily
    # rows through 04-30 (Thursday), so "Friday" here means the most
    # recent daily ≤ Saturday — which IS Thursday 04-30 in this
    # synthetic universe. To exercise the actual Friday-as-most-recent
    # case, add a Friday row and re-test.
    daily_with_friday = daily + _daily_rows_for(["2026-05-01"])
    cutoff = datetime(2026, 5, 2, 12, 0, tzinfo=ET)  # Saturday noon
    cdt, kind = _select_pit_anchor([], daily_with_friday, cutoff)
    results.append(_check(
        "case 5 (Saturday noon, Fri 05-01 in data) → 05-01T16:15 daily",
        (cdt, kind),
        (datetime(2026, 5, 1, 16, 15, tzinfo=ET), "daily_eod"),
    ))

    # Case 6: cutoff in non-ET timezone (Central Time, UTC-5 in DST).
    # 11:30 CT == 12:30 ET. Most recent intraday at-or-before 12:30 ET
    # is the 12:00 bar (synthetic data has no 12:30 bar).
    CT = timezone(timedelta(hours=-5))  # CDT in late April
    cutoff = datetime(2026, 4, 30, 11, 30, tzinfo=CT)  # 11:30 CT = 12:30 ET
    cdt, kind = _select_pit_anchor(intraday_0430, daily, cutoff)
    results.append(_check(
        "case 6 (11:30 CT = 12:30 ET) → 04-30T12:00 intraday",
        (cdt, kind),
        (datetime(2026, 4, 30, 12, 0, tzinfo=ET), "intraday_5min"),
    ))

    # Case 7: cutoff exactly equal to a bar's close → bar IS included.
    cutoff = datetime(2026, 4, 30, 15, 55, tzinfo=ET)
    cdt, kind = _select_pit_anchor(intraday_0430, daily, cutoff)
    results.append(_check(
        "case 7 (cutoff == 15:55 intraday close) → 04-30T15:55 intraday",
        (cdt, kind),
        (datetime(2026, 4, 30, 15, 55, tzinfo=ET), "intraday_5min"),
    ))

    # Edge: empty inputs → (None, None).
    cdt, kind = _select_pit_anchor([], [], datetime(2026, 4, 30, 12, 0, tzinfo=ET))
    results.append(_check(
        "edge (no bars) → (None, None)",
        (cdt, kind),
        (None, None),
    ))

    # Edge: cutoff before all data → (None, None).
    cdt, kind = _select_pit_anchor(
        [], _daily_rows_for(["2030-01-01"]),
        datetime(2026, 4, 30, 12, 0, tzinfo=ET),
    )
    results.append(_check(
        "edge (cutoff before any bar) → (None, None)",
        (cdt, kind),
        (None, None),
    ))

    # Edge: malformed bar `datetime` strings ignored, valid ones still picked.
    bars = [
        {"datetime": "garbage", "close": 100, "volume": 1},
        {"datetime": None, "close": 100, "volume": 1},
        {"datetime": "2026-04-30 10:00:00", "close": 100, "volume": 1},
    ]
    cdt, kind = _select_pit_anchor(
        bars, [], datetime(2026, 4, 30, 12, 0, tzinfo=ET),
    )
    results.append(_check(
        "edge (malformed bars ignored) → 10:00 intraday",
        (cdt, kind),
        (datetime(2026, 4, 30, 10, 0, tzinfo=ET), "intraday_5min"),
    ))

    # Edge: parse helpers handle malformed input without raising.
    results.append(_check(
        "helper _parse_intraday_bar_close('') → None",
        _parse_intraday_bar_close(""), None,
    ))
    results.append(_check(
        "helper _daily_bar_close(None) → None",
        _daily_bar_close(None), None,
    ))

    print("=== Results ===")
    n_pass = sum(1 for ok, _ in results if ok)
    n_fail = sum(1 for ok, _ in results if not ok)
    for _, line in results:
        print(line)
    print(f"\n{n_pass} pass / {n_fail} fail / {len(results)} total")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
