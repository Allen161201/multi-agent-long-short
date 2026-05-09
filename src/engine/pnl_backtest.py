"""
P&L backtest harness — promotes 4 spec-stub modules to runtime use.

Walks trading days between [start_date, end_date], consumes existing PM
decision JSON artifacts under decision_root/<YYYY-MM-DD>/<run>/, opens /
sizes / closes positions per fill_model + sizing rules, applies
transaction + borrow charges from cost_model, marks-to-market via an
injected price_history_provider, and accumulates an EOD-state +
NAV-history trail under output_dir/.

This module is the §19.8 promotion path for:
  src/portfolio/sizing.py
  src/portfolio/reinvestment.py
  src/execution/cost_model.py
  src/execution/fill_model.py

Decision file convention:
  data/decisions/<YYYY-MM-DD>/<RUN_DIR>/<TICKER>_<CAND>_decision.json
  paired with                          <TICKER>_evidence_packet.json

When multiple RUN_DIR entries exist for one date, the harness picks the
alphabetically-last one (so `16-15-alldata-retry` wins over `16-15`).

Pure Python; no FMP / no HTTP. Trading-day filter is Mon-Fri (US market
holidays not enforced — see RULES.md §6.1 follow-up; gap acknowledged).

Output layout:
  output_dir/
      <YYYY-MM-DD>_eod_state.json     ← one per processed trading day
      pnl_history.csv                  ← one row per processed trading day
      manifest.json                    ← run-level summary + costs + metrics
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from src.portfolio.position_book import (
    DEFAULT_STARTING_CASH, Position, PositionBook, PositionBookEvent,
)
from src.portfolio.nav_history import append_nav_row, load_nav_history
from src.portfolio.metrics import compute_all_metrics
from src.portfolio.sizing import (
    PER_POSITION_CAP_PCT, SLEEVE_CAP_PCT,
)
from src.portfolio.reinvestment import (
    REINVESTMENT_BANDS, ReinvestmentDecision, midpoint_split_for_regime,
)
from src.execution.cost_model import (
    borrow_cost_dollars, transaction_cost_dollars,
)
from src.execution.fill_model import (
    FillIntent, next_trading_day_open,
)


# Local alias: nav_history's CSV filename. Kept inline here so a rename in
# nav_history.py doesn't silently fork the harness output layout.
NAV_HISTORY_CSV = "pnl_history.csv"

RULE_VERSION_DEFAULT = "v0.9.0_pass8_hardrule"
EOD_FILENAME_FMT = "{date}_eod_state.json"
MANIFEST_FILENAME = "manifest.json"

# Sleeves the harness recognises when reporting exposure.
SLEEVES = ("quality_long", "surge_short", "fixed_income")


# ── public API ────────────────────────────────────────────────────────

def run_pnl_backtest(
    *,
    start_date: str,
    end_date: str,
    decision_root: Path,
    price_history_provider: Callable[[str, str], float],
    initial_capital: float = DEFAULT_STARTING_CASH,
    output_dir: Path | None = None,
    transaction_cost_bps: float | None = 30.0,
    include_borrow_cost: bool = True,
    rule_version: str = RULE_VERSION_DEFAULT,
    regime: str = "normal",
    benchmark_ticker: str | None = "SPY",
) -> dict:
    """Run a P&L backtest across the [start_date, end_date] inclusive window.

    Args:
      start_date / end_date: YYYY-MM-DD strings (ISO).
      decision_root: directory holding YYYY-MM-DD/RUN/ subdirs of
                     PM decision JSONs + evidence packets.
      price_history_provider: callable (ticker, YYYY-MM-DD) → close price.
                              Must raise / return non-positive on missing data
                              (the harness propagates).
      initial_capital: starting cash (only used if no prior EOD exists).
      output_dir: where to write per-day EOD JSON, pnl_history.csv, and
                  manifest.json. Defaults to data/backtest/<run_id>/.
      transaction_cost_bps: flat per-leg bps charge. Pass None to fall
                            back to cost_model.transaction_cost_dollars
                            tier (10/20/30 bps). Default 30.0.
      include_borrow_cost: if True, charge cost_model.borrow_cost_dollars
                           daily on every open short.
      rule_version: stamped into each EOD JSON and the manifest.
      regime: macro regime label fed to reinvestment.midpoint_split_for_regime
              when the Friday trigger fires. Default "normal".

    Returns: a summary dict with file paths, day-by-day counters, and
             final-NAV-history metrics.
    """
    if not callable(price_history_provider):
        raise TypeError("price_history_provider must be a callable (ticker, date) -> float")

    start_d = _parse_iso_date(start_date, "start_date")
    end_d = _parse_iso_date(end_date, "end_date")
    if end_d < start_d:
        raise ValueError(f"end_date {end_date!r} < start_date {start_date!r}")

    decision_root = Path(decision_root)
    if not decision_root.exists():
        raise FileNotFoundError(f"decision_root not found: {decision_root}")

    # Run-id: backtest_<wallclock>_<start>_to_<end>
    now_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"backtest_{now_stamp}_{start_d.isoformat()}_to_{end_d.isoformat()}"
    if output_dir is None:
        # Project root is two parents up from this file (src/engine/X.py).
        project_root = Path(__file__).resolve().parent.parent.parent
        output_dir = project_root / "data" / "backtest" / run_id
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    history_csv_path = output_dir / NAV_HISTORY_CSV
    manifest_path = output_dir / MANIFEST_FILENAME

    # Per-day rolling state.
    trading_days = _list_trading_days(start_d, end_d)
    if not trading_days:
        raise ValueError(
            f"no trading days (Mon-Fri) in window {start_date}..{end_date}"
        )

    # Counters accumulated across the run.
    total_decisions_seen = 0
    total_trades_filled = 0   # excludes no_op + size_capped-to-zero
    total_no_ops = 0
    total_size_capped = 0
    total_tx_cost = 0.0
    total_borrow_cost = 0.0

    # Realised short P&L tracker for the Friday reinvestment trigger.
    realised_short_pnl_this_week = 0.0
    reinvestment_decisions: list[dict] = []

    # Per-day report rows (small, captured into manifest).
    per_day_rows: list[dict] = []

    for d in trading_days:
        report = _process_one_day(
            today=d,
            decision_root=decision_root,
            output_dir=output_dir,
            history_csv_path=history_csv_path,
            initial_capital=initial_capital,
            price_history_provider=price_history_provider,
            transaction_cost_bps=transaction_cost_bps,
            include_borrow_cost=include_borrow_cost,
            rule_version=rule_version,
        )
        per_day_rows.append(report)
        total_decisions_seen += report["decisions_seen"]
        total_trades_filled += report["trades_filled"]
        total_no_ops += report["no_ops"]
        total_size_capped += report["size_capped"]
        total_tx_cost += report["transaction_cost_dollars"]
        total_borrow_cost += report["borrow_cost_dollars"]
        realised_short_pnl_this_week += report["realised_short_pnl_today"]

        # Friday reinvestment trigger.
        if d.weekday() == 4 and realised_short_pnl_this_week > 0:
            to_long_pct, to_cash_pct = midpoint_split_for_regime(regime)  # type: ignore[arg-type]
            ts_friday = datetime.combine(d, time(15, 30)).isoformat()
            ts_monday_open = next_trading_day_open(
                datetime.combine(d, time(15, 30))
            ).isoformat()
            rein = ReinvestmentDecision(
                decision_timestamp_iso=ts_friday,
                execution_timestamp_iso=ts_monday_open,
                regime=regime,  # type: ignore[arg-type]
                realized_short_pnl_dollars=realised_short_pnl_this_week,
                to_long_pct=to_long_pct,
                to_cash_pct=to_cash_pct,
                selection_rationale=(
                    "harness midpoint default; agent discretion deferred — "
                    "actual long-side conversion not executed in this v1 "
                    "(no candidate ticker selection logic in the harness)."
                ),
            )
            reinvestment_decisions.append(asdict(rein))

        # Reset weekly bucket on Friday close.
        if d.weekday() == 4:
            realised_short_pnl_this_week = 0.0

    # Final NAV history → metrics. compute_all_metrics takes the CSV path
    # and re-loads internally; we also load_nav_history to grab the final
    # NAV for the summary dict (one extra small read, but keeps the
    # metrics call signature unmodified).
    history_rows = load_nav_history(history_csv_path)

    # ── SPY benchmark wiring (RULES.md §28 / Step C metrics) ──────────
    # When benchmark_ticker is set, reuse price_history_provider to
    # fetch a daily close per processed trading day and compute aligned
    # daily returns. Pass to compute_all_metrics so information_ratio
    # gets a real benchmark series. None ⇒ no benchmark (the metrics
    # output omits information_ratio).
    benchmark_returns: list[float] | None = None
    benchmark_failure_note: str | None = None
    if history_rows and benchmark_ticker:
        try:
            bench_closes: list[float] = []
            for d in trading_days:
                close = float(price_history_provider(
                    benchmark_ticker, d.isoformat()
                ))
                if close <= 0:
                    raise ValueError(
                        f"non-positive {benchmark_ticker} close on {d}"
                    )
                bench_closes.append(close)
            # Daily simple returns aligned to the *daily_return* series
            # consumed by compute_all_metrics. metrics.py drops the
            # first NAV row (its daily_return is None — the seed has no
            # prior close) before computing returns, so benchmark_returns
            # MUST be length n_nav_rows - 1 to match. We compute one
            # return per pair of consecutive closes, then length-align.
            bench_rets: list[float] = []
            for i in range(1, len(bench_closes)):
                if bench_closes[i - 1] <= 0:
                    bench_rets.append(0.0)
                else:
                    bench_rets.append(
                        bench_closes[i] / bench_closes[i - 1] - 1.0
                    )
            n_returns = max(0, len(history_rows) - 1)
            if len(bench_rets) >= n_returns:
                benchmark_returns = bench_rets[:n_returns]
            else:
                # Pad missing tail with zeros — metrics.py information_ratio
                # needs equal-length series. Note in summary so operators see.
                pad = [0.0] * (n_returns - len(bench_rets))
                benchmark_returns = bench_rets + pad
                benchmark_failure_note = (
                    f"benchmark {benchmark_ticker} returned "
                    f"{len(bench_rets)} returns for {n_returns} portfolio "
                    f"daily-return rows; tail-padded with zeros"
                )
        except Exception as e:  # pylint: disable=broad-except
            benchmark_returns = None
            benchmark_failure_note = (
                f"benchmark {benchmark_ticker} fetch failed: "
                f"{type(e).__name__}: {e}; information_ratio omitted"
            )

    if history_rows:
        metrics = compute_all_metrics(
            history_csv_path,
            benchmark_returns=benchmark_returns,
        )
    else:
        metrics = {
            "as_of_count": 0,
            "first_date": None, "last_date": None,
            "sharpe_ratio": None, "sortino_ratio": None,
            "max_drawdown": {"value": None, "peak_date": None,
                             "trough_date": None, "recovery_date": None},
            "return_volatility": None, "total_return": None,
        }
    if benchmark_failure_note:
        metrics["benchmark_note"] = benchmark_failure_note
    final_nav = history_rows[-1]["total_nav"] if history_rows else initial_capital

    summary = {
        "run_id": run_id,
        "schema_version": "pnl_backtest_v1",
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "trading_days_processed": len(trading_days),
        "decision_root": str(decision_root),
        "output_dir": str(output_dir),
        "history_csv_path": str(history_csv_path),
        "manifest_path": str(manifest_path),
        "rule_version": rule_version,
        "regime": regime,
        "initial_capital": float(initial_capital),
        "final_nav": float(final_nav),
        "totals": {
            "decisions_seen": total_decisions_seen,
            "trades_filled": total_trades_filled,
            "no_ops": total_no_ops,
            "size_capped_events": total_size_capped,
            "transaction_cost_dollars": total_tx_cost,
            "borrow_cost_dollars": total_borrow_cost,
        },
        "transaction_cost_bps_setting": transaction_cost_bps,
        "include_borrow_cost": include_borrow_cost,
        "benchmark_ticker": benchmark_ticker,
        "reinvestment_decisions": reinvestment_decisions,
        "per_day": per_day_rows,
        "metrics": metrics,
    }

    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    return summary


# ── per-day flow ──────────────────────────────────────────────────────

def _process_one_day(
    *,
    today: date,
    decision_root: Path,
    output_dir: Path,
    history_csv_path: Path,
    initial_capital: float,
    price_history_provider: Callable[[str, str], float],
    transaction_cost_bps: float | None,
    include_borrow_cost: bool,
    rule_version: str,
) -> dict:
    today_iso = today.isoformat()

    # 1. Load prior state, or start fresh.
    prior_path = _latest_eod_before(output_dir, today)
    if prior_path is not None:
        book = PositionBook.load_from_file(prior_path)
    else:
        book = PositionBook(cash_balance=initial_capital)

    # 2. Find decisions for today.
    decision_files = _decisions_for_date(decision_root, today)

    # 3. Per-decision processing.
    decisions_processed_ids: list[str] = []
    events: list[PositionBookEvent] = []
    cap_events: list[dict] = []
    realised_short_pnl_today = 0.0
    transaction_cost_today = 0.0
    trades_filled = 0
    no_ops = 0
    decisions_seen = len(decision_files)

    for dec_file in decision_files:
        decision_dict, packet_dict = _load_decision_pair(dec_file)
        ticker = (
            decision_dict.get("ticker")
            or (decision_dict.get("final_decision") or {}).get("ticker")
            or "<no_ticker>"
        )
        fd = decision_dict.get("final_decision") or {}
        plan = fd.get("execution_plan") or {}
        side_raw = (plan.get("side") or "none").lower()
        size_pct_requested = float(plan.get("size_pct_of_portfolio") or 0.0)
        candidate_type = (
            fd.get("candidate_type")
            or decision_dict.get("candidate_type")
            or "quality_long"
        )
        ep_hash = (
            decision_dict.get("evidence_packet_hash")
            or (fd.get("audit_record") or {}).get("evidence_packet_hash")
            or "<no_hash>"
        )

        # No-op short-circuit.
        if side_raw == "none" or size_pct_requested <= 0:
            emitted = book.apply_decision(
                decision_dict=decision_dict,
                entry_price=_safe_packet_anchor(packet_dict, ticker),
                evidence_packet_hash=ep_hash,
            )
            events.extend(emitted)
            no_ops += 1
            decisions_processed_ids.append(
                fd.get("locked_decision_id") or "<no_decision_id>"
            )
            continue

        # 3a. Sizing-cap enforcement (open / add only).
        capped_size_pct = size_pct_requested
        cap_note = None
        if side_raw in ("long", "short"):
            cap_result = _apply_sizing_caps(
                book=book, ticker=ticker, side=side_raw,
                candidate_type=candidate_type,
                requested_size_pct=size_pct_requested,
            )
            capped_size_pct = cap_result["capped_size_pct"]
            if cap_result["was_capped"]:
                cap_note = cap_result
                cap_events.append({
                    "ticker": ticker, "side": side_raw,
                    "requested_size_pct": size_pct_requested,
                    "capped_size_pct": capped_size_pct,
                    "reason": cap_result["reason"],
                })
            if capped_size_pct <= 0:
                # Cap reduced the order to zero — treat as no_op-with-note.
                events.append(PositionBookEvent(
                    timestamp=today_iso, kind="no_op", ticker=ticker,
                    side=None, size_pct_delta=0.0,
                    decision_id=fd.get("locked_decision_id") or "<no_decision_id>",
                    note=f"size_capped_to_zero: {cap_note['reason']}"
                         if cap_note else "size_capped_to_zero",
                ))
                no_ops += 1
                decisions_processed_ids.append(
                    fd.get("locked_decision_id") or "<no_decision_id>"
                )
                continue

        # 3b. Resolve fill price (T+1 next-open semantics → next trading
        # day's close as proxy, since price_history_provider returns close).
        fill_dt = next_trading_day_open(
            datetime.combine(today, time(16, 15))
        )
        fill_date_iso = fill_dt.date().isoformat()
        fill_price = _resolve_fill_price(
            provider=price_history_provider,
            ticker=ticker, fill_date_iso=fill_date_iso,
            fallback_today_iso=today_iso,
            fallback_packet=packet_dict,
        )

        # Build a FillIntent (recorded for audit; spec stub wiring).
        intent = FillIntent(
            decision_timestamp=datetime.combine(today, time(16, 15)),
            ticker=ticker, side=side_raw,  # type: ignore[arg-type]
        )
        # Compose a synthesised execution_plan to feed apply_decision with the
        # capped size and the intent's execution timestamp. The original
        # decision's execution_plan is preserved in the JSON; the harness
        # supplies its own filled-in copy for the bookkeeping path.
        synth_decision = dict(decision_dict)
        synth_fd = dict(fd)
        synth_plan = dict(plan)
        synth_plan["size_pct_of_portfolio"] = capped_size_pct
        synth_plan["execution_timestamp"] = next_trading_day_open(
            intent.decision_timestamp
        ).isoformat()
        synth_fd["execution_plan"] = synth_plan
        synth_decision["final_decision"] = synth_fd

        # 3c. Snapshot pre-state for realised P&L on closes.
        pre_snapshot = {
            (p.ticker, p.side): (p.cost_basis, p.size_shares or 0.0,
                                  p.current_price)
            for p in book.positions
        }

        emitted = book.apply_decision(
            decision_dict=synth_decision,
            entry_price=fill_price,
            evidence_packet_hash=ep_hash,
        )
        events.extend(emitted)

        # 3d. Transaction cost on the leg's notional. Notional = capped_size_pct
        #     × NAV(pre-trade) — using NAV-after as a denominator is fine for
        #     small trades but pre-NAV is more interpretable.
        nav_for_notional = book.get_total_nav()
        leg_notional = (capped_size_pct / 100.0) * nav_for_notional
        if leg_notional > 0:
            tx_cost = _transaction_cost(leg_notional, transaction_cost_bps)
            book.cash_balance -= tx_cost
            transaction_cost_today += tx_cost
            trades_filled += 1

        # 3e. Realised P&L from any close_full event.
        for ev in emitted:
            if ev.kind == "close_full":
                key = (ev.ticker, ev.side)
                if key not in pre_snapshot:
                    continue
                pre_cb, pre_sh, _pre_cp = pre_snapshot[key]
                proceeds = pre_sh * fill_price
                if ev.side == "short":
                    realised = pre_cb - proceeds
                    realised_short_pnl_today += realised
                # long realised P&L not reinvestment-eligible per RULES.md §4.10.

        decisions_processed_ids.append(
            fd.get("locked_decision_id") or "<no_decision_id>"
        )

    # 4. Mark-to-market all open positions at today's close.
    def _today_close_fetcher(t: str) -> float:
        return float(price_history_provider(t, today_iso))
    if book.positions:
        book.mark_to_market(_today_close_fetcher, as_of_iso=today_iso)
    book.last_eod_timestamp = today_iso

    # 5. Daily borrow charge on open shorts.
    borrow_cost_today = 0.0
    if include_borrow_cost:
        for p in book.positions:
            if p.side == "short" and p.current_value > 0:
                cost = borrow_cost_dollars(
                    short_notional=p.current_value, days_held=1,
                )
                borrow_cost_today += cost
        if borrow_cost_today > 0:
            book.cash_balance -= borrow_cost_today

    # 5b. §27.17 dividend accrual + §27.18 ETF expense ratio drag
    # (2026-05-02 v0.8.7 UST refactor). PositionBook stores Position
    # dataclasses; the dividends/expenses helpers accept a state dict
    # with `cash` + a `positions` list, so we wrap the book briefly
    # then sync the cash delta back. Events are appended to the EOD
    # event stream for audit.
    from src.portfolio.dividends_and_expenses import (
        accrue_dividends_for_today, accrue_etf_expense_drag_for_today,
    )
    state_view: dict[str, Any] = {
        "cash": book.cash_balance,
        "positions": [
            {"ticker": p.ticker, "shares": p.size_shares or 0.0,
             "side": p.side, "entry_price": p.cost_basis or 0.0}
            for p in book.positions if getattr(p, "kind", None) != "ust"
        ],
    }
    div_events = accrue_dividends_for_today(state_view, today_iso)
    exp_events = accrue_etf_expense_drag_for_today(state_view, today_iso)
    cash_delta = state_view["cash"] - book.cash_balance
    if abs(cash_delta) > 1e-9:
        book.cash_balance = state_view["cash"]
    for ev in (div_events + exp_events):
        events.append(PositionBookEvent(
            timestamp=today_iso,
            kind=ev.get("kind", "dividend_or_expense_event"),
            ticker=ev.get("ticker", ""),
            side=None, size_pct_delta=0.0,
            decision_id="auto_div_expense_v0_8_7",
            note=json.dumps(ev, default=str)[:500],
        ))

    # 6. Write today's EOD JSON.
    eod_payload = _build_eod_payload(
        book=book, today=today, rule_version=rule_version,
        decisions_processed=decisions_processed_ids, events=events,
        prior_state_path=prior_path, cap_events=cap_events,
        transaction_cost_today=transaction_cost_today,
        borrow_cost_today=borrow_cost_today,
        realised_short_pnl_today=realised_short_pnl_today,
    )
    eod_path = output_dir / EOD_FILENAME_FMT.format(date=today_iso)
    with eod_path.open("w", encoding="utf-8") as f:
        json.dump(eod_payload, f, ensure_ascii=False, indent=2, default=str)

    # 7. Append NAV row.
    append_nav_row(eod_path, history_csv_path)

    return {
        "as_of": today_iso,
        "decisions_seen": decisions_seen,
        "trades_filled": trades_filled,
        "no_ops": no_ops,
        "size_capped": len(cap_events),
        "transaction_cost_dollars": transaction_cost_today,
        "borrow_cost_dollars": borrow_cost_today,
        "realised_short_pnl_today": realised_short_pnl_today,
        "total_nav_eod": book.get_total_nav(),
        "cash_balance_eod": book.cash_balance,
        "open_positions": len(book.positions),
        "eod_state_path": str(eod_path),
    }


# ── helpers ───────────────────────────────────────────────────────────

def _parse_iso_date(s: str, label: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"{label} must be YYYY-MM-DD, got {s!r}: {e}") from e


def _list_trading_days(start: date, end: date) -> list[date]:
    """Mon-Fri filter; US market holidays NOT enforced (gap acknowledged
    per RULES.md §6.1 follow-up)."""
    out: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d = d + timedelta(days=1)
    return out


def _latest_eod_before(output_dir: Path, today: date) -> Path | None:
    """Find the most-recent <YYYY-MM-DD>_eod_state.json with date < today."""
    if not output_dir.exists():
        return None
    candidates: list[tuple[date, Path]] = []
    for p in output_dir.glob("*_eod_state.json"):
        stem = p.stem.replace("_eod_state", "")
        try:
            d = datetime.strptime(stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < today:
            candidates.append((d, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def _decisions_for_date(decision_root: Path, today: date) -> list[Path]:
    """Return all *_decision.json files for `today`, picked from the
    alphabetically-last RUN_DIR under decision_root/<today>/."""
    day_dir = decision_root / today.isoformat()
    if not day_dir.exists():
        return []
    run_dirs = sorted(p for p in day_dir.iterdir() if p.is_dir())
    if not run_dirs:
        return []
    chosen = run_dirs[-1]
    return sorted(chosen.glob("*_decision.json"))


def _load_decision_pair(decision_file: Path) -> tuple[dict, dict]:
    """Return (decision_dict, evidence_packet_dict). Raises if pair missing."""
    decision_file = Path(decision_file)
    with decision_file.open("r", encoding="utf-8") as f:
        decision_dict = json.load(f)
    name = decision_file.name
    if "_decision.json" not in name:
        raise ValueError(
            f"decision file name must end with _decision.json: {name!r}"
        )
    ticker = name.split("_", 1)[0]
    packet_path = decision_file.parent / f"{ticker}_evidence_packet.json"
    if not packet_path.exists():
        raise FileNotFoundError(
            f"evidence packet missing for {decision_file}: expected {packet_path}"
        )
    with packet_path.open("r", encoding="utf-8") as f:
        packet_dict = json.load(f)
    return decision_dict, packet_dict


def _safe_packet_anchor(packet: dict, ticker: str) -> float:
    """Best-effort PIT anchor — last_eod_close, then last_price. Returns
    1.0 sentinel for no-op decisions where entry_price is unused (the
    PositionBook validates >0 only for non-no-op paths)."""
    ps = packet.get("price_snapshot")
    if isinstance(ps, dict):
        eod = ps.get("last_eod_close")
        if isinstance(eod, (int, float)) and eod > 0:
            return float(eod)
        last = ps.get("last_price")
        if isinstance(last, (int, float)) and last > 0:
            return float(last)
    # No usable anchor — return a sentinel positive (1.0). For no-op
    # decisions PositionBook ignores entry_price so this never lands in a
    # real trade. For real trades, the caller resolves fill_price via
    # _resolve_fill_price instead and never hits this fallback.
    return 1.0


def _resolve_fill_price(
    *,
    provider: Callable[[str, str], float],
    ticker: str,
    fill_date_iso: str,
    fallback_today_iso: str,
    fallback_packet: dict,
) -> float:
    """T+1 next-open fill price, with documented fallbacks.

    Order of preference:
      1. provider(ticker, fill_date_iso) — the canonical T+1 close as
         proxy for next-day open (the spec's price_history_provider
         signature returns CLOSE; we accept the approximation).
      2. provider(ticker, fallback_today_iso) — today's close, used when
         fill_date is past the available history window.
      3. price_snapshot.last_eod_close from the evidence packet — pure
         PIT anchor, never lookahead.

    Raises ValueError only if all three fail. Never silently substitutes.
    """
    # 1. Primary: next-trading-day close.
    try:
        v = float(provider(ticker, fill_date_iso))
        if v > 0:
            return v
    except Exception:  # noqa: BLE001 — fall through to next strategy
        pass

    # 2. Fallback: today's close (when next-day data unavailable).
    try:
        v = float(provider(ticker, fallback_today_iso))
        if v > 0:
            return v
    except Exception:  # noqa: BLE001
        pass

    # 3. Last-resort: evidence packet's PIT anchor.
    anchor = _safe_packet_anchor(fallback_packet, ticker)
    if anchor > 0 and anchor != 1.0:  # 1.0 is the sentinel "nothing usable"
        return anchor

    raise ValueError(
        f"_resolve_fill_price: no price for {ticker} at {fill_date_iso} "
        f"(also tried {fallback_today_iso} and packet anchor)"
    )


def _apply_sizing_caps(
    *,
    book: PositionBook,
    ticker: str,
    side: str,
    candidate_type: str,
    requested_size_pct: float,
) -> dict:
    """Enforce sizing.PER_POSITION_CAP_PCT and SLEEVE_CAP_PCT.

    Returns: {capped_size_pct, was_capped, reason}.
    Sleeve cap is computed against current sleeve notional / NAV.
    """
    nav = book.get_total_nav()
    if nav <= 0:
        return {"capped_size_pct": requested_size_pct, "was_capped": False,
                "reason": "skipped: NAV<=0"}

    # Per-position cap: existing position's size_pct + requested ≤ cap.
    side_norm = "long" if side == "long" else "short"
    existing = next(
        (p for p in book.positions
         if p.ticker == ticker and p.side == side_norm and p.status != "closed_full"),
        None,
    )
    existing_pct = float(existing.size_pct) if existing is not None else 0.0
    headroom_position = max(0.0, PER_POSITION_CAP_PCT - existing_pct)

    # Sleeve cap (in %-of-NAV terms).
    sleeve = (
        "quality_long" if candidate_type == "quality_long"
        else ("surge_short" if candidate_type == "surge_short"
              else ("quality_long" if side == "long" else "surge_short"))
    )
    sleeve_value = book.get_sleeve_exposure(sleeve)  # type: ignore[arg-type]
    sleeve_pct_now = (sleeve_value / nav) * 100.0
    headroom_sleeve = max(0.0, SLEEVE_CAP_PCT - sleeve_pct_now)

    headroom = min(headroom_position, headroom_sleeve)
    capped = min(requested_size_pct, headroom)
    was_capped = capped < requested_size_pct - 1e-9
    reason = ""
    if was_capped:
        if headroom_position <= headroom_sleeve:
            reason = (
                f"per_position_cap: existing {existing_pct}% + requested "
                f"{requested_size_pct}% would exceed PER_POSITION_CAP_PCT="
                f"{PER_POSITION_CAP_PCT}% (headroom {headroom_position}%)"
            )
        else:
            reason = (
                f"sleeve_cap: sleeve {sleeve} at {sleeve_pct_now:.4f}% + "
                f"requested {requested_size_pct}% would exceed "
                f"SLEEVE_CAP_PCT={SLEEVE_CAP_PCT}% "
                f"(headroom {headroom_sleeve:.4f}%)"
            )
    return {
        "capped_size_pct": capped,
        "was_capped": was_capped,
        "reason": reason,
        "headroom_position_pct": headroom_position,
        "headroom_sleeve_pct": headroom_sleeve,
        "sleeve": sleeve,
    }


def _transaction_cost(
    leg_notional_usd: float, transaction_cost_bps: float | None,
) -> float:
    """Either flat-bps (if transaction_cost_bps is a number) or tiered
    via cost_model.transaction_cost_dollars (if None)."""
    if leg_notional_usd <= 0:
        return 0.0
    if transaction_cost_bps is None:
        return transaction_cost_dollars(leg_notional_usd)
    return leg_notional_usd * float(transaction_cost_bps) / 10_000.0


def _build_eod_payload(
    *,
    book: PositionBook,
    today: date,
    rule_version: str,
    decisions_processed: list[str],
    events: list[PositionBookEvent],
    prior_state_path: Path | None,
    cap_events: list[dict],
    transaction_cost_today: float,
    borrow_cost_today: float,
    realised_short_pnl_today: float,
) -> dict:
    return {
        "schema_version": "eod_state_v1",
        "as_of": today.isoformat(),
        "rule_version": rule_version,
        "cash_balance": book.cash_balance,
        "total_nav": book.get_total_nav(),
        "positions": [p.to_dict() for p in book.positions],
        "sleeve_exposure": {
            s: book.get_sleeve_exposure(s) for s in SLEEVES  # type: ignore[arg-type]
        },
        "concentration": book.get_position_concentration(),
        "audit": {
            "decisions_processed": decisions_processed,
            "events": [e.to_dict() for e in events],
            "prior_state_loaded_from": str(prior_state_path) if prior_state_path else None,
            "size_cap_events": cap_events,
            "transaction_cost_dollars_today": transaction_cost_today,
            "borrow_cost_dollars_today": borrow_cost_today,
            "realised_short_pnl_today": realised_short_pnl_today,
        },
    }


__all__ = [
    "run_pnl_backtest",
    "RULE_VERSION_DEFAULT",
    "NAV_HISTORY_CSV",
    "MANIFEST_FILENAME",
    "EOD_FILENAME_FMT",
    "SLEEVES",
]
