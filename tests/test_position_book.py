"""
Unit tests for src/portfolio/position_book.py

Direct-execution / __main__-style (matches test_regression_matrix.py and
test_fundamental_snapshot_pit_filter.py — pytest is not installed).

Run:  python tests/test_position_book.py
Exit code 0 on all-pass, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from portfolio.position_book import (  # noqa: E402
    DEFAULT_STARTING_CASH, Position, PositionBook,
)


PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


def make_decision(
    *, ticker="AAPL", candidate_type="quality_long", side="long",
    size_pct=2.0, decision_id="sha256:test_001",
    decision_label="buy",
) -> dict:
    return {
        "ticker": ticker,
        "candidate_type": candidate_type,
        "evidence_packet_hash": "sha256:packet_test_001",
        "final_decision": {
            "ticker": ticker,
            "candidate_type": candidate_type,
            "decision": decision_label,
            "position_size_pct": size_pct,
            "locked_decision_id": decision_id,
            "decision_timestamp": "2026-04-29T16:15:00-04:00",
            "execution_plan": {
                "order_type": "T+1 next-open",
                "execution_timestamp": "2026-04-30T09:30:00-04:00",
                "ticker": ticker,
                "side": side,
                "size_pct_of_portfolio": size_pct,
            },
        },
    }


def case_1_empty_book() -> None:
    print("\nCase 1 — empty book starts with $1M cash, 0 positions")
    b = PositionBook()
    check("1a cash_balance == $1M", b.cash_balance == DEFAULT_STARTING_CASH,
          f"got {b.cash_balance}")
    check("1b positions == []", b.positions == [], f"got {b.positions}")
    check("1c total_nav == cash", b.get_total_nav() == DEFAULT_STARTING_CASH,
          f"got {b.get_total_nav()}")
    check("1d concentration is empty",
          b.get_position_concentration() == {},
          f"got {b.get_position_concentration()}")


def case_2_apply_buy() -> None:
    print("\nCase 2 — apply_decision with side=long opens a long position")
    b = PositionBook()
    decision = make_decision(side="long", size_pct=2.0)
    events = b.apply_decision(
        decision_dict=decision, entry_price=100.0,
        evidence_packet_hash="sha256:pkt_001",
    )
    check("2a one event emitted", len(events) == 1, f"got {len(events)}")
    check("2b event kind == open", events[0].kind == "open",
          f"got {events[0].kind}")
    check("2c position count == 1", len(b.positions) == 1,
          f"got {len(b.positions)}")
    p = b.positions[0]
    # 2% of $1M / $100 = 200 shares
    check("2d size_shares == 200", abs(p.size_shares - 200.0) < 1e-9,
          f"got {p.size_shares}")
    check("2e cost_basis == $20,000", abs(p.cost_basis - 20_000.0) < 1e-9,
          f"got {p.cost_basis}")
    check("2f cash debited", abs(b.cash_balance - 980_000.0) < 1e-9,
          f"got {b.cash_balance}")
    check("2g sleeve == quality_long", p.sleeve == "quality_long",
          f"got {p.sleeve}")


def case_3_apply_short() -> None:
    print("\nCase 3 — apply_decision with side=short opens a short position")
    b = PositionBook()
    decision = make_decision(
        ticker="GME", candidate_type="surge_short", side="short",
        size_pct=1.0, decision_label="short",
    )
    events = b.apply_decision(
        decision_dict=decision, entry_price=50.0,
        evidence_packet_hash="sha256:pkt_002",
    )
    check("3a one event emitted, kind=open", len(events) == 1
          and events[0].kind == "open", f"got {[e.kind for e in events]}")
    p = b.positions[0]
    check("3b side == short", p.side == "short", f"got {p.side}")
    check("3c sleeve == surge_short", p.sleeve == "surge_short",
          f"got {p.sleeve}")
    # 1% of $1M / $50 = 200 shares
    check("3d size_shares == 200", abs(p.size_shares - 200.0) < 1e-9,
          f"got {p.size_shares}")
    # Short sells: cash CREDITED by notional
    check("3e cash credited (short proceeds)",
          abs(b.cash_balance - 1_010_000.0) < 1e-9, f"got {b.cash_balance}")


def case_4_no_op_decisions() -> None:
    print("\nCase 4 — watch / no_trade / veto / side=none all no-op")
    b = PositionBook()
    starting_cash = b.cash_balance

    for label in ("watch", "no_trade", "veto"):
        decision = make_decision(side="none", size_pct=0.0,
                                 decision_label=label)
        events = b.apply_decision(
            decision_dict=decision, entry_price=100.0,
            evidence_packet_hash="sha256:pkt_noop",
        )
        check(f"4 {label} emits one no_op event",
              len(events) == 1 and events[0].kind == "no_op",
              f"got {[e.kind for e in events]}")

    check("4z cash unchanged", b.cash_balance == starting_cash,
          f"got {b.cash_balance}")
    check("4y positions unchanged", b.positions == [], f"got {b.positions}")


def case_5_partial_close_long() -> None:
    print("\nCase 5 — partial close (sell) reduces long size_shares")
    b = PositionBook()
    # Open 5% long @ $100 → 500 shares
    open_dec = make_decision(side="long", size_pct=5.0,
                             decision_id="sha256:open_001")
    b.apply_decision(decision_dict=open_dec, entry_price=100.0,
                     evidence_packet_hash="sha256:pkt_open")
    # Reduce by 2% → should leave 3% size_pct (≈ 300 shares)
    sell_dec = make_decision(side="sell", size_pct=2.0,
                             decision_id="sha256:sell_001",
                             decision_label="close_partial")
    events = b.apply_decision(decision_dict=sell_dec, entry_price=100.0,
                              evidence_packet_hash="sha256:pkt_sell")
    check("5a event kind == reduce_partial",
          events[0].kind == "reduce_partial", f"got {events[0].kind}")
    p = b.positions[0]
    check("5b size_pct reduced from 5 to 3", abs(p.size_pct - 3.0) < 1e-9,
          f"got {p.size_pct}")
    check("5c size_shares ≈ 300", abs(p.size_shares - 300.0) < 1e-9,
          f"got {p.size_shares}")
    check("5d status == closed_partial",
          p.status == "closed_partial", f"got {p.status}")


def case_6_mark_to_market() -> None:
    print("\nCase 6 — mark_to_market refreshes current_price + PnL")
    b = PositionBook()
    decision = make_decision(side="long", size_pct=2.0)
    b.apply_decision(decision_dict=decision, entry_price=100.0,
                     evidence_packet_hash="sha256:pkt_mtm")
    # Bump price to $110
    b.mark_to_market(lambda t: 110.0, as_of_iso="2026-04-29")
    p = b.positions[0]
    check("6a current_price == 110", p.current_price == 110.0,
          f"got {p.current_price}")
    # 200 shares × ($110 − $100) = $2,000 PnL
    check("6b unrealized_pnl == $2000", abs(p.unrealized_pnl - 2_000.0) < 1e-9,
          f"got {p.unrealized_pnl}")
    check("6c unrealized_pnl_pct == 10%",
          abs(p.unrealized_pnl_pct - 10.0) < 1e-6,
          f"got {p.unrealized_pnl_pct}")
    check("6d last_marked_at set",
          p.last_marked_at == "2026-04-29", f"got {p.last_marked_at}")


def case_7_pnl_signs() -> None:
    print("\nCase 7 — PnL sign for long vs short when price rises")
    b = PositionBook()
    long_dec = make_decision(side="long", size_pct=1.0,
                             decision_id="sha256:long_x")
    short_dec = make_decision(ticker="GME", candidate_type="surge_short",
                              side="short", size_pct=1.0,
                              decision_id="sha256:short_x",
                              decision_label="short")
    b.apply_decision(decision_dict=long_dec, entry_price=100.0,
                     evidence_packet_hash="sha256:pkt_long")
    b.apply_decision(decision_dict=short_dec, entry_price=50.0,
                     evidence_packet_hash="sha256:pkt_short")
    # Price rises 10% on each
    def fetch(t: str) -> float:
        return {"AAPL": 110.0, "GME": 55.0}[t]
    b.mark_to_market(fetch)
    long_p = b.get_positions(side="long")[0]
    short_p = b.get_positions(side="short")[0]
    check("7a long PnL > 0 when price rises", long_p.unrealized_pnl > 0,
          f"got {long_p.unrealized_pnl}")
    check("7b short PnL < 0 when price rises", short_p.unrealized_pnl < 0,
          f"got {short_p.unrealized_pnl}")


def case_8_total_nav() -> None:
    print("\nCase 8 — total_nav = cash + adjusted positions")
    b = PositionBook()
    long_dec = make_decision(side="long", size_pct=2.0,
                             decision_id="sha256:nav_long")
    b.apply_decision(decision_dict=long_dec, entry_price=100.0,
                     evidence_packet_hash="sha256:pkt_nav")
    # Baseline: cash $980k, position cost $20k → NAV = $1M unchanged
    check("8a NAV at entry == $1M",
          abs(b.get_total_nav() - 1_000_000.0) < 1e-6,
          f"got {b.get_total_nav()}")
    # Mark to $110: position now $22k, cash $980k → NAV = $1.002M
    b.mark_to_market(lambda t: 110.0)
    check("8b NAV after +10% MTM == $1,002,000",
          abs(b.get_total_nav() - 1_002_000.0) < 1e-6,
          f"got {b.get_total_nav()}")
    # Mark to $90: position now $18k → NAV = $998k
    b.mark_to_market(lambda t: 90.0)
    check("8c NAV after -10% MTM == $998,000",
          abs(b.get_total_nav() - 998_000.0) < 1e-6,
          f"got {b.get_total_nav()}")


def case_9_sleeve_exposure() -> None:
    print("\nCase 9 — sleeve exposure groups by sleeve")
    b = PositionBook()
    b.apply_decision(
        decision_dict=make_decision(ticker="AAPL", candidate_type="quality_long",
                                    side="long", size_pct=2.0,
                                    decision_id="sha256:s_a"),
        entry_price=100.0, evidence_packet_hash="sha256:pkt_a")
    b.apply_decision(
        decision_dict=make_decision(ticker="MSFT", candidate_type="quality_long",
                                    side="long", size_pct=3.0,
                                    decision_id="sha256:s_m",
                                    decision_label="buy"),
        entry_price=200.0, evidence_packet_hash="sha256:pkt_m")
    b.apply_decision(
        decision_dict=make_decision(ticker="GME", candidate_type="surge_short",
                                    side="short", size_pct=1.0,
                                    decision_id="sha256:s_g",
                                    decision_label="short"),
        entry_price=50.0, evidence_packet_hash="sha256:pkt_g")
    # Quality_long total at entry: 2% + 3% × $1M = $50k
    ql = b.get_sleeve_exposure("quality_long")
    ss = b.get_sleeve_exposure("surge_short")
    fi = b.get_sleeve_exposure("fixed_income")
    check("9a quality_long ≈ $50k", abs(ql - 50_000.0) < 1e-6, f"got {ql}")
    check("9b surge_short ≈ $10k",  abs(ss - 10_000.0) < 1e-6, f"got {ss}")
    check("9c fixed_income == 0",   fi == 0.0, f"got {fi}")


def case_10_round_trip_serialization() -> None:
    print("\nCase 10 — to_dict → from_dict round-trip")
    b = PositionBook()
    b.apply_decision(
        decision_dict=make_decision(side="long", size_pct=2.0),
        entry_price=100.0, evidence_packet_hash="sha256:pkt_rt")
    b.mark_to_market(lambda t: 105.0, as_of_iso="2026-04-29")
    b.last_eod_timestamp = "2026-04-29"
    d = b.to_dict()
    js = json.loads(json.dumps(d, default=str))
    b2 = PositionBook.from_dict(js)
    check("10a cash_balance preserved", b.cash_balance == b2.cash_balance,
          f"{b.cash_balance} vs {b2.cash_balance}")
    check("10b positions count preserved",
          len(b.positions) == len(b2.positions),
          f"{len(b.positions)} vs {len(b2.positions)}")
    check("10c last_eod_timestamp preserved",
          b.last_eod_timestamp == b2.last_eod_timestamp,
          f"{b.last_eod_timestamp} vs {b2.last_eod_timestamp}")
    check("10d position fields equal",
          b.positions[0].to_dict() == b2.positions[0].to_dict(),
          "fields differ after round-trip")


def case_11_full_close_via_sell() -> None:
    print("\nCase 11 — sell with size_pct == position size_pct → full close")
    b = PositionBook()
    open_dec = make_decision(side="long", size_pct=2.0,
                             decision_id="sha256:fc_open")
    b.apply_decision(decision_dict=open_dec, entry_price=100.0,
                     evidence_packet_hash="sha256:pkt_fc_open")
    sell_dec = make_decision(side="sell", size_pct=2.0,
                             decision_id="sha256:fc_close",
                             decision_label="close_full")
    events = b.apply_decision(decision_dict=sell_dec, entry_price=100.0,
                              evidence_packet_hash="sha256:pkt_fc_close")
    check("11a event kind == close_full", events[0].kind == "close_full",
          f"got {events[0].kind}")
    check("11b position list empty", len(b.positions) == 0,
          f"got {len(b.positions)}")
    # Cash returned to ~$1M (200 sh × $100 = $20k)
    check("11c cash restored to ~$1M",
          abs(b.cash_balance - 1_000_000.0) < 1e-6, f"got {b.cash_balance}")


def case_12_unknown_side_raises() -> None:
    print("\nCase 12 — unknown execution_plan.side raises ValueError")
    b = PositionBook()
    bad = make_decision(side="teleport", size_pct=1.0)
    raised = False
    try:
        b.apply_decision(decision_dict=bad, entry_price=100.0,
                         evidence_packet_hash="sha256:pkt_bad")
    except ValueError:
        raised = True
    check("12 raises ValueError on unknown side", raised, "no exception")


def main() -> int:
    print("=" * 70)
    print("test_position_book.py")
    print("=" * 70)
    case_1_empty_book()
    case_2_apply_buy()
    case_3_apply_short()
    case_4_no_op_decisions()
    case_5_partial_close_long()
    case_6_mark_to_market()
    case_7_pnl_signs()
    case_8_total_nav()
    case_9_sleeve_exposure()
    case_10_round_trip_serialization()
    case_11_full_close_via_sell()
    case_12_unknown_side_raises()
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
