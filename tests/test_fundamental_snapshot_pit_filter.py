"""
Unit test for src/evidence_packet/blocks/fundamental — host-timezone-
independent cutoff handling and PIT filter behavior.

Direct-execution / __main__-style (matches test_regression_matrix.py and
test_price_snapshot_intraday_cutoff.py — pytest is not installed). Run:

    python tests/test_fundamental_snapshot_pit_filter.py

Exit code 0 on all-pass, 1 on any failure.

Cases exercise the bug class fixed in this task:

  Bug 1 — fundamental.py:67 used `astimezone()` with no arg, converting
  the cutoff to host-local time before stripping tzinfo. On non-ET hosts
  this let post-cutoff FMP rows pass `_first_pit_safe`. Fix: explicit
  `astimezone(ET).replace(tzinfo=None)` via the new `_cutoff_et_naive`
  helper. Reference pattern: news.py.

  Bug 2 — on cache-miss FMP sometimes returns rows where `acceptedDate`
  is wall-clock-near-now. With Bug 1 active, those rows leaked through
  and got emitted as block `as_of`. Fixing Bug 1 must filter them out;
  no extra defensive layer is added — verified by case 5 below.

Synthetic FMP rows are constructed in-memory; the fmp_adapter module
is monkey-patched. No live API calls.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evidence_packet.blocks import fundamental as fund_block  # noqa: E402
from evidence_packet.blocks.fundamental import (  # noqa: E402
    _cutoff_et_naive,
    _first_pit_safe,
    _parse_accepted,
)
from evidence_packet.schema import BlockStatus  # noqa: E402

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
CT = ZoneInfo("America/Chicago")


# ----------------------------------------------------------------------
# Synthetic-row builders
# ----------------------------------------------------------------------
def _row(accepted: str, fiscal_year: int = 2026, period: str = "Q1",
         filing: str | None = None, **fields) -> dict:
    """Build a synthetic FMP statement row."""
    if filing is None:
        filing = accepted.split(" ", 1)[0]
    base = {
        "accepted_date": accepted,
        "filing_date": filing,
        "fiscal_period_end": "2025-12-28",
        "period": period,
        "fiscal_year": fiscal_year,
        "revenue": 100_000_000,
        "net_income": 25_000_000,
        "operating_income": 30_000_000,
        "ebitda": 35_000_000,
        "eps": 1.50,
        "gross_profit": 60_000_000,
    }
    base.update(fields)
    return base


def _bal_row(accepted: str, **fields) -> dict:
    base = _row(accepted, **fields)
    base.update({
        "total_assets": 500_000_000,
        "total_liabilities": 200_000_000,
        "total_equity": 300_000_000,
        "total_debt": 100_000_000,
    })
    return base


def _cf_row(accepted: str, **fields) -> dict:
    base = _row(accepted, **fields)
    base.update({
        "operating_cash_flow": 40_000_000,
        "free_cash_flow": 30_000_000,
    })
    return base


# ----------------------------------------------------------------------
# Test plumbing
# ----------------------------------------------------------------------
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


def patch_fmp(income: list[dict], balance: list[dict], cashflow: list[dict]):
    """Monkey-patch fmp_adapter getters used by fundamental.build."""
    fund_block.fmp.get_income_statement = lambda *a, **k: list(income)
    fund_block.fmp.get_balance_sheet = lambda *a, **k: list(balance)
    fund_block.fmp.get_cash_flow_statement = lambda *a, **k: list(cashflow)
    fund_block.fmp.get_call_group_summary = lambda: {}


# ----------------------------------------------------------------------
# Cases
# ----------------------------------------------------------------------
def case_1_host_tz_independence() -> None:
    """Same cutoff + same rows → identical _first_pit_safe output regardless
    of how the aware-cutoff is expressed (ET vs UTC vs CT). The fixed
    `_cutoff_et_naive` must collapse all three to the same ET-naive value."""
    print("\nCase 1 — host-timezone independence (cutoff in ET / UTC / CT)")
    cutoff_et = datetime(2026, 4, 30, 10, 30, 0, tzinfo=ET)
    cutoff_utc = cutoff_et.astimezone(UTC)
    cutoff_ct = cutoff_et.astimezone(CT)

    n_et = _cutoff_et_naive(cutoff_et)
    n_utc = _cutoff_et_naive(cutoff_utc)
    n_ct = _cutoff_et_naive(cutoff_ct)

    expected_naive = datetime(2026, 4, 30, 10, 30, 0)
    check("1a ET aware cutoff → ET naive",
          n_et == expected_naive, f"got {n_et!r}")
    check("1b UTC aware cutoff → ET naive",
          n_utc == expected_naive, f"got {n_utc!r}")
    check("1c CT aware cutoff → ET naive",
          n_ct == expected_naive, f"got {n_ct!r}")

    # Critical: a row dated 2026-04-30 11:00:00 ET (post-cutoff) must be
    # rejected under ALL three cutoff representations. Pre-fix, the CT
    # representation would have rejected it for a different reason or let
    # it through depending on host tz.
    rows = [_row("2026-04-30 11:00:00")]
    out_et = _first_pit_safe(rows, n_et)
    out_utc = _first_pit_safe(rows, n_utc)
    out_ct = _first_pit_safe(rows, n_ct)
    check("1d post-cutoff row rejected under ET cutoff", out_et is None,
          f"got {out_et!r}")
    check("1e post-cutoff row rejected under UTC cutoff", out_utc is None,
          f"got {out_utc!r}")
    check("1f post-cutoff row rejected under CT cutoff", out_ct is None,
          f"got {out_ct!r}")


def case_2_post_cutoff_rejected() -> None:
    """A row whose accepted_date is strictly after the cutoff is rejected."""
    print("\nCase 2 — strictly post-cutoff row rejected")
    cutoff = _cutoff_et_naive(datetime(2026, 4, 30, 10, 30, 0, tzinfo=ET))
    row = _row("2026-04-30 10:30:01")  # 1 second past
    out = _first_pit_safe([row], cutoff)
    check("2 strictly-after rejected", out is None, f"got {out!r}")


def case_3_exactly_at_cutoff_included() -> None:
    """Row at exactly the cutoff timestamp is included (≤ semantics)."""
    print("\nCase 3 — row exactly at cutoff included")
    cutoff = _cutoff_et_naive(datetime(2026, 4, 30, 10, 30, 0, tzinfo=ET))
    row = _row("2026-04-30 10:30:00")
    out = _first_pit_safe([row], cutoff)
    check("3 exactly-at-cutoff included",
          out is not None and out["accepted_date"] == "2026-04-30 10:30:00",
          f"got {out!r}")


def case_4_well_before_cutoff_included() -> None:
    """Row well before cutoff is the obvious-good case."""
    print("\nCase 4 — well-before-cutoff row included")
    cutoff = _cutoff_et_naive(datetime(2026, 4, 30, 10, 30, 0, tzinfo=ET))
    row = _row("2026-01-30 06:01:32")  # plausible Q1 FY2026 SEC acceptance
    out = _first_pit_safe([row], cutoff)
    check("4 well-before included",
          out is not None and out["accepted_date"] == "2026-01-30 06:01:32",
          f"got {out!r}")


def case_5_cache_miss_simulation_via_build() -> None:
    """End-to-end through build(): one row's accepted_date is wall-clock-
    near-now (post-cutoff), another is real SEC historical (pre-cutoff).
    Filter must keep the historical row; block as_of must equal the
    historical accepted_date, NOT the wall-clock value.

    This is the Bug 2 symptom from earlier today's smoke. With Bug 1
    fixed (cutoff conversion in ET), the wall-clock row is filtered out
    and the historical row wins."""
    print("\nCase 5 — cache-miss-style mixed rows (build end-to-end)")

    # Cutoff: 2026-04-30 10:30 ET — the failing intraday smoke.
    cutoff = datetime(2026, 4, 30, 10, 30, 0, tzinfo=ET)

    wall_clock_post = "2026-04-30 16:30:41"   # FMP wall-clock leak shape
    real_sec_pre = "2026-01-30 06:01:32"      # plausible Q1 FY2026 acceptance

    # FMP returns newest-first; the wall-clock row would be at index 0.
    income = [
        _row(wall_clock_post, fiscal_year=2026, period="Q2", revenue=120_000_000),
        _row(real_sec_pre, fiscal_year=2026, period="Q1", revenue=100_000_000),
    ]
    balance = [
        _bal_row(wall_clock_post, fiscal_year=2026, period="Q2"),
        _bal_row(real_sec_pre, fiscal_year=2026, period="Q1"),
    ]
    cashflow = [
        _cf_row(wall_clock_post, fiscal_year=2026, period="Q2"),
        _cf_row(real_sec_pre, fiscal_year=2026, period="Q1"),
    ]

    patch_fmp(income, balance, cashflow)
    out = fund_block.build(ticker="AAPL", allowed_data_cutoff=cutoff)
    block = out["block"]

    check("5a build completed (status OK)",
          block["status"] == BlockStatus.OK,
          f"got status={block.get('status')!r}")
    check("5b block as_of == real SEC accepted_date (NOT wall-clock)",
          block.get("as_of") == real_sec_pre,
          f"got as_of={block.get('as_of')!r}, expected {real_sec_pre!r}")
    check("5c available_as_of also == real SEC accepted_date",
          block.get("available_as_of") == real_sec_pre,
          f"got {block.get('available_as_of')!r}")
    fwu = block.get("filed_window_used", {}) or {}
    inc = fwu.get("income_statement") or {}
    check("5d filed_window_used.income_statement uses pre-cutoff row",
          inc.get("accepted_date") == real_sec_pre,
          f"got {inc.get('accepted_date')!r}")
    check("5e snapshot_quarter.revenue from Q1 row (100M, not 120M)",
          (block.get("snapshot_quarter") or {}).get("revenue") == 100_000_000,
          f"got {(block.get('snapshot_quarter') or {}).get('revenue')!r}")


def case_6_no_pre_cutoff_rows_graceful() -> None:
    """All rows are post-cutoff → DATA_UNAVAILABLE-flavored degraded block."""
    print("\nCase 6 — no pre-cutoff rows / graceful degrade")

    cutoff = datetime(2026, 4, 30, 10, 30, 0, tzinfo=ET)
    # Only post-cutoff rows.
    income = [_row("2026-04-30 16:30:41")]
    balance = [_bal_row("2026-04-30 16:30:41")]
    cashflow = [_cf_row("2026-04-30 16:30:41")]

    patch_fmp(income, balance, cashflow)
    out = fund_block.build(ticker="AAPL", allowed_data_cutoff=cutoff)
    block = out["block"]

    # All three statements have no PIT-safe row → INSUFFICIENT_EVIDENCE per
    # the existing block contract (line 161-167).
    check("6a status is INSUFFICIENT_EVIDENCE",
          block["status"] == BlockStatus.INSUFFICIENT_EVIDENCE,
          f"got status={block.get('status')!r}")
    check("6b as_of is None (no PIT-safe income row)",
          block.get("as_of") is None,
          f"got as_of={block.get('as_of')!r}")
    check("6c quality_flags includes fundamentals_partial",
          any(f.get("kind") == "fundamentals_partial"
              for f in out.get("quality_flags", [])),
          f"got {out.get('quality_flags')!r}")

    # And the FMP-returned-empty case → DATA_UNAVAILABLE (early branch).
    patch_fmp([], [], [])
    out2 = fund_block.build(ticker="AAPL", allowed_data_cutoff=cutoff)
    check("6d empty FMP → DATA_UNAVAILABLE",
          out2["block"]["status"] == BlockStatus.DATA_UNAVAILABLE,
          f"got {out2['block'].get('status')!r}")


def case_7_naive_cutoff_passthrough() -> None:
    """Defensive: a naive cutoff (no tzinfo) is assumed to already be ET
    and passes through unchanged. This keeps stub regression byte-identical
    if any caller passes a naive value."""
    print("\nCase 7 — naive cutoff is treated as ET (pass-through)")
    naive = datetime(2026, 4, 30, 10, 30, 0)
    out = _cutoff_et_naive(naive)
    check("7 naive cutoff returned unchanged", out == naive, f"got {out!r}")


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
def main() -> int:
    print("=" * 70)
    print("test_fundamental_snapshot_pit_filter.py")
    print("=" * 70)
    case_1_host_tz_independence()
    case_2_post_cutoff_rejected()
    case_3_exactly_at_cutoff_included()
    case_4_well_before_cutoff_included()
    case_5_cache_miss_simulation_via_build()
    case_6_no_pre_cutoff_rows_graceful()
    case_7_naive_cutoff_passthrough()

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
