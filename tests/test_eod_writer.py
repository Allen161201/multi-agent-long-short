"""
Unit tests for src/portfolio/eod_writer.py

Direct-execution / __main__-style. Run:
    python tests/test_eod_writer.py

No FMP / no HTTP — synthetic decision + packet JSONs are written to a
tempdir and a hardcoded price_fetcher is injected.
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
sys.path.insert(0, str(ROOT / "src"))

from portfolio.eod_writer import (  # noqa: E402
    write_eod_state, make_packet_anchor_price_fetcher,
)
from portfolio.position_book import DEFAULT_STARTING_CASH  # noqa: E402


PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


def _decision_dict(*, ticker, candidate_type, side, size_pct,
                   decision_id, decision_label):
    return {
        "ticker": ticker,
        "candidate_type": candidate_type,
        "evidence_packet_hash": f"sha256:packet_{ticker.lower()}",
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


def _packet(ticker: str, last_eod_close: float) -> dict:
    return {
        "envelope": {"evidence_packet_hash": f"sha256:packet_{ticker.lower()}"},
        "price_snapshot": {
            "status": "ok", "ticker": ticker,
            "as_of": "2026-04-29T16:15:00-04:00",
            "last_eod_close": last_eod_close,
            "last_price": last_eod_close,
        },
    }


def _seed_decision_pair(workdir: Path, *, ticker, candidate_type, side,
                        size_pct, decision_id, decision_label,
                        last_eod_close):
    workdir.mkdir(parents=True, exist_ok=True)
    dec = _decision_dict(ticker=ticker, candidate_type=candidate_type,
                         side=side, size_pct=size_pct,
                         decision_id=decision_id,
                         decision_label=decision_label)
    pkt = _packet(ticker, last_eod_close)
    dec_path = workdir / f"{ticker}_{candidate_type}_decision.json"
    pkt_path = workdir / f"{ticker}_evidence_packet.json"
    dec_path.write_text(json.dumps(dec), encoding="utf-8")
    pkt_path.write_text(json.dumps(pkt), encoding="utf-8")
    return dec_path, pkt_path


def case_1_no_prior_state() -> None:
    print("\nCase 1 — no prior file → fresh book with $1M cash, watch only")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        decisions = tmp / "decisions"
        outdir = tmp / "portfolio"
        dec_path, _ = _seed_decision_pair(
            decisions, ticker="AAPL", candidate_type="quality_long",
            side="none", size_pct=0.0, decision_id="sha256:c1",
            decision_label="watch", last_eod_close=270.22,
        )
        out = write_eod_state(
            decision_files=[dec_path],
            price_fetcher=lambda t: 270.22,
            output_dir=outdir,
            as_of_date=date(2026, 4, 29),
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        check("1a output file exists", out.exists(), str(out))
        check("1b cash_balance == $1M",
              payload["cash_balance"] == DEFAULT_STARTING_CASH,
              f"got {payload['cash_balance']}")
        check("1c total_nav == $1M",
              payload["total_nav"] == DEFAULT_STARTING_CASH,
              f"got {payload['total_nav']}")
        check("1d positions == []", payload["positions"] == [],
              f"got {payload['positions']}")
        check("1e exactly one no_op event recorded",
              len(payload["audit"]["events"]) == 1
              and payload["audit"]["events"][0]["kind"] == "no_op",
              f"got {payload['audit']['events']}")
        check("1f schema_version stamped",
              payload["schema_version"] == "eod_state_v1",
              f"got {payload.get('schema_version')}")


def case_2_load_prior_state() -> None:
    print("\nCase 2 — prior file loaded; new decision extends the book")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        outdir = tmp / "portfolio"
        decisions_d1 = tmp / "decisions_d1"
        decisions_d2 = tmp / "decisions_d2"

        # Day 1: open AAPL long
        dec1, _ = _seed_decision_pair(
            decisions_d1, ticker="AAPL", candidate_type="quality_long",
            side="long", size_pct=2.0, decision_id="sha256:d1_buy",
            decision_label="buy", last_eod_close=100.0,
        )
        write_eod_state(
            decision_files=[dec1],
            price_fetcher=lambda t: 100.0,
            output_dir=outdir,
            as_of_date=date(2026, 4, 28),
        )

        # Day 2: watch
        dec2, _ = _seed_decision_pair(
            decisions_d2, ticker="AAPL", candidate_type="quality_long",
            side="none", size_pct=0.0, decision_id="sha256:d2_watch",
            decision_label="watch", last_eod_close=105.0,
        )
        out = write_eod_state(
            decision_files=[dec2],
            price_fetcher=lambda t: 105.0,
            output_dir=outdir,
            as_of_date=date(2026, 4, 29),
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        check("2a position carried over", len(payload["positions"]) == 1,
              f"got {len(payload['positions'])}")
        p = payload["positions"][0]
        check("2b ticker == AAPL", p["ticker"] == "AAPL", f"got {p['ticker']}")
        check("2c marked at $105", p["current_price"] == 105.0,
              f"got {p['current_price']}")
        # 200 shares × ($105 − $100) = $1000 PnL
        check("2d unrealized_pnl == $1000",
              abs(p["unrealized_pnl"] - 1_000.0) < 1e-9,
              f"got {p['unrealized_pnl']}")
        check("2e prior state path recorded",
              "2026-04-28_eod_state.json"
              in (payload["audit"]["prior_state_loaded_from"] or ""),
              f"got {payload['audit']['prior_state_loaded_from']}")


def case_3_multiple_decisions() -> None:
    print("\nCase 3 — multiple decision files in one EOD run all processed")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        decisions = tmp / "decisions"
        outdir = tmp / "portfolio"
        d1, _ = _seed_decision_pair(
            decisions, ticker="AAPL", candidate_type="quality_long",
            side="long", size_pct=2.0, decision_id="sha256:m1",
            decision_label="buy", last_eod_close=100.0,
        )
        d2, _ = _seed_decision_pair(
            decisions, ticker="MSFT", candidate_type="quality_long",
            side="long", size_pct=3.0, decision_id="sha256:m2",
            decision_label="buy", last_eod_close=200.0,
        )
        d3, _ = _seed_decision_pair(
            decisions, ticker="GME", candidate_type="surge_short",
            side="short", size_pct=1.0, decision_id="sha256:m3",
            decision_label="short", last_eod_close=50.0,
        )
        prices = {"AAPL": 100.0, "MSFT": 200.0, "GME": 50.0}
        out = write_eod_state(
            decision_files=[d1, d2, d3],
            price_fetcher=lambda t: prices[t],
            output_dir=outdir,
            as_of_date=date(2026, 4, 29),
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        check("3a 3 positions opened", len(payload["positions"]) == 3,
              f"got {len(payload['positions'])}")
        sleeve_ql = payload["sleeve_exposure"]["quality_long"]
        sleeve_ss = payload["sleeve_exposure"]["surge_short"]
        check("3b quality_long sleeve ≈ $50k",
              abs(sleeve_ql - 50_000.0) < 1e-6, f"got {sleeve_ql}")
        check("3c surge_short sleeve ≈ $10k",
              abs(sleeve_ss - 10_000.0) < 1e-6, f"got {sleeve_ss}")
        check("3d 3 events recorded",
              len(payload["audit"]["events"]) == 3,
              f"got {len(payload['audit']['events'])}")
        check("3e 3 decision_ids recorded",
              len(payload["audit"]["decisions_processed"]) == 3,
              f"got {len(payload['audit']['decisions_processed'])}")


def case_4_schema_keys_present() -> None:
    print("\nCase 4 — output JSON has all required top-level keys")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        decisions = tmp / "decisions"
        outdir = tmp / "portfolio"
        dec, _ = _seed_decision_pair(
            decisions, ticker="AAPL", candidate_type="quality_long",
            side="none", size_pct=0.0, decision_id="sha256:s",
            decision_label="watch", last_eod_close=270.22,
        )
        out = write_eod_state(
            decision_files=[dec],
            price_fetcher=lambda t: 270.22,
            output_dir=outdir,
            as_of_date=date(2026, 4, 29),
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        for key in ("schema_version", "as_of", "rule_version",
                    "cash_balance", "total_nav", "positions",
                    "sleeve_exposure", "concentration", "audit"):
            check(f"4 contains '{key}'", key in payload,
                  f"keys: {list(payload.keys())}")
        check("4y rule_version stamped",
              payload["rule_version"] == "v0.8.3_13f_cadence",
              f"got {payload['rule_version']}")


def case_5_idempotency() -> None:
    print("\nCase 5 — running twice with same inputs gives identical output")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        decisions = tmp / "decisions"
        outdir1 = tmp / "p1"
        outdir2 = tmp / "p2"
        dec, _ = _seed_decision_pair(
            decisions, ticker="AAPL", candidate_type="quality_long",
            side="long", size_pct=2.0, decision_id="sha256:idem",
            decision_label="buy", last_eod_close=100.0,
        )
        out1 = write_eod_state(
            decision_files=[dec],
            price_fetcher=lambda t: 100.0,
            output_dir=outdir1,
            as_of_date=date(2026, 4, 29),
        )
        out2 = write_eod_state(
            decision_files=[dec],
            price_fetcher=lambda t: 100.0,
            output_dir=outdir2,
            as_of_date=date(2026, 4, 29),
        )
        p1 = json.loads(out1.read_text(encoding="utf-8"))
        p2 = json.loads(out2.read_text(encoding="utf-8"))
        # prior_state_loaded_from will differ (None in both, so OK)
        # All other fields should be byte-identical.
        check("5a cash_balance identical", p1["cash_balance"] == p2["cash_balance"],
              f"{p1['cash_balance']} vs {p2['cash_balance']}")
        check("5b total_nav identical", p1["total_nav"] == p2["total_nav"],
              f"{p1['total_nav']} vs {p2['total_nav']}")
        check("5c positions identical", p1["positions"] == p2["positions"],
              "positions differ")
        check("5d sleeve_exposure identical",
              p1["sleeve_exposure"] == p2["sleeve_exposure"],
              "sleeves differ")


def case_6_missing_packet_raises() -> None:
    print("\nCase 6 — missing evidence packet raises FileNotFoundError")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        decisions = tmp / "decisions"
        outdir = tmp / "portfolio"
        dec_path, pkt_path = _seed_decision_pair(
            decisions, ticker="AAPL", candidate_type="quality_long",
            side="long", size_pct=2.0, decision_id="sha256:mp",
            decision_label="buy", last_eod_close=100.0,
        )
        # Delete the packet to simulate the error
        pkt_path.unlink()
        raised = False
        try:
            write_eod_state(
                decision_files=[dec_path],
                price_fetcher=lambda t: 100.0,
                output_dir=outdir,
                as_of_date=date(2026, 4, 29),
            )
        except FileNotFoundError:
            raised = True
        check("6 raises FileNotFoundError when packet missing", raised,
              "no exception raised")


def case_7_packet_anchor_fetcher() -> None:
    print("\nCase 7 — make_packet_anchor_price_fetcher returns last_eod_close")
    pkts = {"AAPL": _packet("AAPL", 270.22), "MSFT": _packet("MSFT", 415.50)}
    fetcher = make_packet_anchor_price_fetcher(pkts)
    check("7a AAPL price", fetcher("AAPL") == 270.22, f"got {fetcher('AAPL')}")
    check("7b MSFT price", fetcher("MSFT") == 415.50, f"got {fetcher('MSFT')}")
    raised = False
    try:
        fetcher("UNKNOWN")
    except KeyError:
        raised = True
    check("7c unknown ticker raises KeyError", raised, "no exception")


def case_8_pnl_history_written() -> None:
    """Integration: write_eod_state must also produce pnl_history.csv
    with one matching row. Added when nav_history.append_nav_row was
    hooked into eod_writer."""
    print("\nCase 8 — pnl_history.csv produced alongside EOD JSON")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        decisions = tmp / "decisions"
        outdir = tmp / "portfolio"
        dec, _ = _seed_decision_pair(
            decisions, ticker="AAPL", candidate_type="quality_long",
            side="none", size_pct=0.0, decision_id="sha256:hist_smoke",
            decision_label="watch", last_eod_close=270.22,
        )
        out = write_eod_state(
            decision_files=[dec],
            price_fetcher=lambda t: 270.22,
            output_dir=outdir,
            as_of_date=date(2026, 4, 29),
        )
        history_csv = outdir / "pnl_history.csv"
        check("8a pnl_history.csv exists", history_csv.exists(),
              str(history_csv))
        # Reuse nav_history loader
        from portfolio.nav_history import load_nav_history  # noqa
        rows = load_nav_history(history_csv)
        check("8b exactly 1 row written", len(rows) == 1, f"got {len(rows)}")
        r = rows[0]
        check("8c as_of matches", r["as_of"] == "2026-04-29",
              f"got {r['as_of']!r}")
        check("8d total_nav == cash == $1M",
              r["total_nav"] == DEFAULT_STARTING_CASH
              and r["cash_balance"] == DEFAULT_STARTING_CASH,
              f"got nav={r['total_nav']} cash={r['cash_balance']}")
        check("8e positions_value == 0",
              r["positions_value"] == 0.0,
              f"got {r['positions_value']!r}")
        check("8f num_positions == 0",
              r["num_positions"] == 0,
              f"got {r['num_positions']!r}")
        check("8g daily_return is None (first row)",
              r["daily_return"] is None,
              f"got {r['daily_return']!r}")
        check("8h cumulative_return == 0.0",
              r["cumulative_return"] == 0.0,
              f"got {r['cumulative_return']!r}")
        # Sanity that the EOD JSON itself is also still produced
        check("8i EOD JSON also exists", out.exists(), str(out))


def main() -> int:
    print("=" * 70)
    print("test_eod_writer.py")
    print("=" * 70)
    case_1_no_prior_state()
    case_2_load_prior_state()
    case_3_multiple_decisions()
    case_4_schema_keys_present()
    case_5_idempotency()
    case_6_missing_packet_raises()
    case_7_packet_anchor_fetcher()
    case_8_pnl_history_written()
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
