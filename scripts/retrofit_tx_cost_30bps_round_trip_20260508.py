#!/usr/bin/env python3
"""Combined tx-cost (15 bps single-side per fill) + borrow-cost (100% APY daily)
retrofit to ALL Phase 1 March + Phase 2 April backtest artifacts.

Read-only on originals; writes parallel `<cell>_tx30bps_borrow100/` directories.

Per RULES.md §5.16 v2.15:
  - Tx cost = abs(notional) * 0.0015 on every fill (entries, adds, exits, §31, UST,
    BIL bootstrap, forced corp-action legs).
  - Borrow cost = abs(short market value at EOD) * 1.00 / 365 per day.
  - Convention: borrow accrues entry day INCLUSIVE, cover day EXCLUSIVE.

For Phase 2 (April cells), also extends forward to 2025-05-30 (Polygon cache end):
  - Short positions still open at 4-22 cut continue accruing borrow forward.
  - DFS→COF merger on 2025-05-19 synthesized as TWO legs each charged 15 bps.
  - ANSS merger 2025-07-17 is out of cache; ANSS held flat through 5-30 with no
    conversion, but no new tx event in the forward window.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT_DIR = ROOT / "data" / "decisions" / "_portfolios"
DIAG = ROOT / "data" / "diagnostics"
POLY_DIR = ROOT / "data" / "cache" / "polygon" / "grouped_daily"

TX_BPS = 15.0
TX_RATE = TX_BPS / 10_000.0
BORROW_APY = 1.00
BORROW_DAILY = BORROW_APY / 365.0

# Corp action: DFS → 1.0192 COF on 2025-05-19 (last DFS trading 2025-05-16)
DFS_LAST_TRADING = "2025-05-16"
DFS_MERGER_DATE = "2025-05-19"
DFS_TO_COF_RATIO = 1.0192
ANSS_LAST_AVAILABLE = "2025-05-30"  # out-of-cache, no conversion applied
FORWARD_END = "2025-05-30"
APR_CUT = "2025-04-22"

CELLS = [
    # (name, port_dir_name, phase, has_forward)
    ("Solo (Mar)",            "phase1_cell1_solo_20260507",          "Mar", False),
    ("Multi-noADaS (Mar)",    "phase1_cell2_noadas_20260507",        "Mar", False),
    ("Multi+ADaS (Mar)",      "phase1_cell3_default_20260507",       "Mar", False),
    ("Cell A (Apr default)",  "phase1_apr_cell3_default_20260507",   "Apr", True),
    ("Cell B (Apr no-SEC)",   "phase1_apr_cell4_no_sec_20260507",    "Apr", True),
]


# ---------------- Polygon helpers ----------------

def load_poly(d: str) -> dict | None:
    p = POLY_DIR / f"{d}_adj1.json"
    if not p.exists():
        p = POLY_DIR / f"{d}_adj0.json"
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as f:
        return json.load(f).get("results")


def get_close(d: str, ticker: str) -> float | None:
    rs = load_poly(d)
    if rs is None or ticker not in rs or rs[ticker] is None:
        return None
    return rs[ticker].get("c")


def trading_dates(start: str, end: str) -> list[str]:
    seen = set(); out = []
    for f in sorted(POLY_DIR.glob("*.json")):
        s = f.stem.replace("_adj1", "").replace("_adj0", "")
        if start <= s <= end and s not in seen:
            seen.add(s); out.append(s)
    return out


# ---------------- EOD loaders ----------------

def cell_eod_files(cell: Path) -> list[Path]:
    return sorted(cell.glob("*_eod_state.json"))


def load_eod(p: Path) -> dict:
    with p.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------- Trade reconstruction ----------------

@dataclass
class TradeEvent:
    date: str
    kind: str              # entry / exit / corp_action_leg / bil_bootstrap
    ticker: str | None
    sleeve: str | None
    side: str | None
    notional_usd: float    # absolute (positive)
    detail: str = ""


@dataclass
class CellResult:
    name: str
    phase: str
    port_dir: Path
    eod_dates: list[str] = field(default_factory=list)
    orig_nav: dict[str, float] = field(default_factory=dict)
    tx_events: list[TradeEvent] = field(default_factory=list)
    borrow_per_day: dict[str, float] = field(default_factory=dict)
    tx_per_day: dict[str, float] = field(default_factory=dict)
    short_holding_days: list[int] = field(default_factory=list)
    forward_dates: list[str] = field(default_factory=list)
    forward_nav_orig: dict[str, float] = field(default_factory=dict)
    forward_nav_combined: dict[str, float] = field(default_factory=dict)
    forward_tx_per_day: dict[str, float] = field(default_factory=dict)
    forward_borrow_per_day: dict[str, float] = field(default_factory=dict)


def reconstruct_events(cell_dir: Path) -> tuple[list[TradeEvent], list[int], list[str], dict[str, float]]:
    """Walk LATEST EOD JSON, emit one entry/exit per equity record + UST entries.

    Returns (events, short_holding_days_list, ordered_eod_dates, orig_nav_by_date).
    """
    files = cell_eod_files(cell_dir)
    if not files:
        return [], [], [], {}

    eod_dates: list[str] = []
    orig_nav: dict[str, float] = {}
    for p in files:
        s = load_eod(p)
        d = s.get("as_of") or p.stem.replace("_eod_state", "")
        eod_dates.append(d)
        orig_nav[d] = float(s.get("total_nav", 0.0))

    last = load_eod(files[-1])
    events: list[TradeEvent] = []
    holding_days: list[int] = []

    seen_decision_ids: set[str] = set()
    for pos in last.get("positions", []):
        kind = pos.get("kind")
        if kind == "ust":
            # UST entry on purchase_date with notional = purchase_pv
            pdate = pos.get("purchase_date") or pos.get("entry_date")
            pv = pos.get("purchase_pv") or pos.get("face_value") or 0.0
            if pdate and pv:
                events.append(TradeEvent(
                    date=pdate, kind="entry", ticker=None, sleeve="fixed_income",
                    side=None, notional_usd=float(pv),
                    detail=f"UST {pos.get('tenor', '?')} face={pos.get('face_value')} purchase_pv={pv}",
                ))
            continue

        ticker = pos.get("ticker")
        if not ticker:
            continue
        did = pos.get("decision_id")
        if did and did in seen_decision_ids:
            continue  # de-dupe (shouldn't happen but be safe)
        if did:
            seen_decision_ids.add(did)

        side = pos.get("side")
        sleeve = pos.get("sleeve")
        entry_date = pos.get("entry_date")
        notional_open = pos.get("notional_at_open")
        if not notional_open:
            sh = pos.get("shares")
            ep = pos.get("entry_price")
            if sh and ep:
                notional_open = float(sh) * float(ep)
        if entry_date and notional_open:
            is_bil_bootstrap = (pos.get("decision_id") == "bil_bootstrap_t0")
            kind_label = "bil_bootstrap" if is_bil_bootstrap else "entry"
            events.append(TradeEvent(
                date=entry_date, kind=kind_label, ticker=ticker, sleeve=sleeve, side=side,
                notional_usd=abs(float(notional_open)),
                detail=f"{ticker} {side} {sleeve} did={pos.get('decision_id')}",
            ))

        # Exit, if closed
        if pos.get("status") == "closed":
            close_date = pos.get("close_date")
            close_price = pos.get("close_price")
            shares = pos.get("shares")
            if close_date and close_price and shares:
                exit_notional = abs(float(shares) * float(close_price))
                events.append(TradeEvent(
                    date=close_date, kind="exit", ticker=ticker, sleeve=sleeve, side=side,
                    notional_usd=exit_notional,
                    detail=f"{ticker} {side} close@{close_price}",
                ))
                if entry_date and close_date and side == "short":
                    try:
                        d0 = datetime.strptime(entry_date, "%Y-%m-%d").date()
                        d1 = datetime.strptime(close_date, "%Y-%m-%d").date()
                        # business days approximation
                        delta_cal = (d1 - d0).days
                        delta_bus = max(1, round(delta_cal * 5 / 7))
                        holding_days.append(delta_bus)
                    except Exception:
                        pass

    return events, holding_days, eod_dates, orig_nav


# ---------------- Borrow accrual ----------------

def borrow_per_day(cell_dir: Path) -> tuple[dict[str, float], dict[str, float]]:
    """Return (date->daily_borrow_usd, ticker->total_borrow_usd) by walking each EOD JSON.

    For each day's EOD, for each short position that was open at end of day
    (status != 'closed' OR close_date > day), add current_value × BORROW_DAILY.
    """
    daily: dict[str, float] = {}
    by_ticker: dict[str, float] = {}
    files = cell_eod_files(cell_dir)
    for p in files:
        s = load_eod(p)
        day = s.get("as_of") or p.stem.replace("_eod_state", "")
        total = 0.0
        for pos in s.get("positions", []):
            if pos.get("side") != "short":
                continue
            close_date = pos.get("close_date")
            status = pos.get("status")
            open_at_eod = (status != "closed") or (close_date and close_date > day)
            if not open_at_eod:
                continue
            mv = pos.get("current_value")
            if mv is None or mv <= 0:
                # fallback: shares * current_price
                sh = pos.get("shares"); cp = pos.get("current_price") or pos.get("entry_price")
                if sh and cp:
                    mv = abs(float(sh) * float(cp))
                else:
                    continue
            charge = abs(float(mv)) * BORROW_DAILY
            total += charge
            tic = pos.get("ticker") or "?"
            by_ticker[tic] = by_ticker.get(tic, 0.0) + charge
        daily[day] = total
    return daily, by_ticker


# ---------------- Forward MtM (Phase 2 only) ----------------

def forward_block(cell_dir: Path, cum_friction_at_cut: float,
                  orig_nav_cut: float) -> dict:
    """Replicates presentation_assets _build.py forward equity MtM, but with
    tx + borrow friction added.

    Returns dict with:
      forward_dates, nav_orig (frictionless), nav_combined (with friction),
      tx_per_day (dict), borrow_per_day (dict),
      forward_tx_total, forward_borrow_total.
    """
    eod_cut = load_eod(cell_dir / f"{APR_CUT}_eod_state.json")
    cash_cut = float(eod_cut.get("cash_balance", 0.0))
    se = eod_cut.get("sleeve_exposure") or {}
    ust_face = float(se.get("fixed_income", 0.0))

    # Build equity-position list (longs + shorts) — exclude UST + already-closed
    eq_positions: list[dict] = []
    for pos in eod_cut.get("positions", []):
        if pos.get("kind") == "ust":
            continue
        if pos.get("status") == "closed":
            continue
        if pos.get("ticker") and pos.get("shares"):
            eq_positions.append(pos)

    # Cash + UST residual at cut
    eq_cut, _miss = _equity_mtm(eq_positions, APR_CUT)
    cash_plus_ust_at_cut = orig_nav_cut - eq_cut

    fwd_dates = trading_dates(APR_CUT, FORWARD_END)
    if not fwd_dates or fwd_dates[0] != APR_CUT:
        fwd_dates = [APR_CUT] + [d for d in fwd_dates if d > APR_CUT]

    nav_orig: dict[str, float] = {}
    nav_combined: dict[str, float] = {}
    tx_per_day: dict[str, float] = {}
    borrow_per_day_fwd: dict[str, float] = {}

    cum_friction = cum_friction_at_cut

    for i, d in enumerate(fwd_dates):
        days_from_cut = i
        ust_accrual = ust_face * 0.0432 * (days_from_cut / 365.0)  # proxy; matches presentation
        eq_v, _miss = _equity_mtm(eq_positions, d)
        nav_o = eq_v + cash_plus_ust_at_cut + ust_accrual
        nav_orig[d] = nav_o

        # Daily borrow on shorts MtM'd forward (entry day inclusive, cover day excl.;
        # since none of these are explicitly covered in forward, borrow accrues on each forward day)
        b_today = 0.0
        for pos in eq_positions:
            if pos.get("side") == "short":
                tic = pos["ticker"]
                sh = float(pos["shares"])
                close_d = get_close(d, tic) or pos.get("entry_price")
                mv = abs(sh * float(close_d))
                b_today += mv * BORROW_DAILY
        borrow_per_day_fwd[d] = b_today

        # Tx legs: DFS→COF synthesized on 2025-05-19 (or first trading day on/after 5/19)
        tx_today = 0.0
        if d == DFS_MERGER_DATE or (d > DFS_LAST_TRADING and DFS_MERGER_DATE not in fwd_dates and i > 0 and fwd_dates[i-1] <= DFS_LAST_TRADING):
            for pos in eq_positions:
                if pos.get("ticker") == "DFS":
                    sh = float(pos["shares"])
                    # sell DFS leg @ DFS_LAST_TRADING close
                    sell_close = get_close(DFS_LAST_TRADING, "DFS")
                    if sell_close:
                        sell_notional = abs(sh * float(sell_close))
                        tx_today += sell_notional * TX_RATE
                    # buy COF leg @ d open or close, ratio 1.0192
                    buy_close = get_close(d, "COF")
                    if buy_close:
                        buy_notional = abs(sh * DFS_TO_COF_RATIO * float(buy_close))
                        tx_today += buy_notional * TX_RATE
        tx_per_day[d] = tx_today

        cum_friction += b_today + tx_today
        nav_combined[d] = nav_o - cum_friction

    return {
        "forward_dates": fwd_dates,
        "nav_orig": nav_orig,
        "nav_combined": nav_combined,
        "tx_per_day": tx_per_day,
        "borrow_per_day": borrow_per_day_fwd,
        "forward_tx_total": sum(tx_per_day.values()),
        "forward_borrow_total": sum(borrow_per_day_fwd.values()),
        "cum_friction_at_end": cum_friction,
    }


def _equity_mtm(positions: list[dict], d: str) -> tuple[float, dict]:
    total = 0.0; miss = {}
    for p in positions:
        tic = p.get("ticker"); sh = float(p.get("shares") or 0.0)
        side = p.get("side")
        sign = -1.0 if side == "short" else 1.0
        if tic == "DFS" and d > DFS_LAST_TRADING:
            c = get_close(d, "COF")
            if c is None:
                miss[tic] = "COF missing"; continue
            total += sign * sh * DFS_TO_COF_RATIO * c
        else:
            c = get_close(d, tic)
            if c is None:
                miss[tic] = f"{tic} missing"; continue
            total += sign * sh * c
    return total, miss


# ---------------- Metrics ----------------

def daily_returns(nav_series: list[tuple[str, float]]) -> list[float]:
    rs = []
    for i in range(1, len(nav_series)):
        prev = nav_series[i - 1][1]; cur = nav_series[i][1]
        if prev > 0:
            rs.append(cur / prev - 1.0)
    return rs


def sharpe(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    mu = statistics.mean(returns)
    sd = statistics.pstdev(returns)
    if sd == 0:
        return None
    return mu / sd * math.sqrt(252)


def sortino(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    mu = statistics.mean(returns)
    downside = [min(r, 0.0) for r in returns]
    sd_d = math.sqrt(sum(x * x for x in downside) / len(downside))
    if sd_d == 0:
        return None
    return mu / sd_d * math.sqrt(252)


def max_drawdown(nav_series: list[tuple[str, float]]) -> float:
    peak = -float("inf"); mdd = 0.0
    for _, v in nav_series:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def annualized_vol(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(252)


# ---------------- Per-cell processor ----------------

def process_cell(name: str, port_name: str, phase: str, has_forward: bool) -> dict:
    cell_dir = PORT_DIR / port_name
    out_dir = ROOT / "data" / "decisions" / f"{port_name}_tx30bps_borrow100"
    out_dir.mkdir(parents=True, exist_ok=True)

    events, holding_days, eod_dates, orig_nav = reconstruct_events(cell_dir)
    daily_borrow, borrow_by_ticker = borrow_per_day(cell_dir)

    # Aggregate tx by day
    daily_tx: dict[str, float] = {}
    for ev in events:
        daily_tx[ev.date] = daily_tx.get(ev.date, 0.0) + ev.notional_usd * TX_RATE

    # Build adjusted NAV time series
    cum_tx = 0.0; cum_borrow = 0.0
    adj_nav_tx_only: dict[str, float] = {}
    adj_nav_combined: dict[str, float] = {}
    rows_pnl = []
    for d in eod_dates:
        cum_tx += daily_tx.get(d, 0.0)
        cum_borrow += daily_borrow.get(d, 0.0)
        nav_o = orig_nav.get(d, 0.0)
        adj_nav_tx_only[d] = nav_o - cum_tx
        adj_nav_combined[d] = nav_o - cum_tx - cum_borrow
        rows_pnl.append({
            "as_of": d,
            "orig_total_nav": nav_o,
            "tx_only_nav": adj_nav_tx_only[d],
            "combined_nav": adj_nav_combined[d],
            "daily_tx_cost_usd": round(daily_tx.get(d, 0.0), 4),
            "daily_borrow_cost_usd": round(daily_borrow.get(d, 0.0), 4),
            "cumulative_tx_cost_usd": round(cum_tx, 4),
            "cumulative_borrow_cost_usd": round(cum_borrow, 4),
            "cumulative_combined_friction_usd": round(cum_tx + cum_borrow, 4),
        })

    # Adjusted trade ledger
    ledger_path = out_dir / "adjusted_trade_ledger.csv"
    with ledger_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "kind", "ticker", "sleeve", "side", "notional_usd", "tx_cost_usd", "detail"])
        for ev in sorted(events, key=lambda e: (e.date, e.ticker or "")):
            w.writerow([ev.date, ev.kind, ev.ticker, ev.sleeve, ev.side,
                        round(ev.notional_usd, 2),
                        round(ev.notional_usd * TX_RATE, 4), ev.detail])

    # Adjusted pnl_history.csv
    pnl_path = out_dir / "adjusted_pnl_history.csv"
    with pnl_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_pnl[0].keys()) if rows_pnl else [])
        if rows_pnl:
            w.writeheader()
            for r in rows_pnl:
                w.writerow(r)

    # Forward block (Phase 2 only)
    forward = None
    if has_forward and APR_CUT in orig_nav:
        cum_friction_at_cut = cum_tx + cum_borrow  # at last in-window date == APR_CUT
        forward = forward_block(cell_dir, cum_friction_at_cut, orig_nav[APR_CUT])
        # Persist forward CSV
        with (out_dir / "adjusted_forward_nav.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "nav_orig_forward", "nav_combined_forward",
                        "daily_fwd_tx_cost_usd", "daily_fwd_borrow_cost_usd"])
            for d in forward["forward_dates"]:
                w.writerow([d, round(forward["nav_orig"][d], 2),
                            round(forward["nav_combined"][d], 2),
                            round(forward["tx_per_day"].get(d, 0.0), 4),
                            round(forward["borrow_per_day"].get(d, 0.0), 4)])

    # Metrics on adjusted in-window NAV series
    nav_series_combined = [(d, adj_nav_combined[d]) for d in eod_dates]
    nav_series_orig = [(d, orig_nav[d]) for d in eod_dates]
    rets_combined = daily_returns(nav_series_combined)
    rets_orig = daily_returns(nav_series_orig)

    final_orig = orig_nav[eod_dates[-1]]
    final_tx_only = adj_nav_tx_only[eod_dates[-1]]
    final_combined = adj_nav_combined[eod_dates[-1]]

    summary = {
        "cell": name,
        "phase": phase,
        "port_dir": str(cell_dir),
        "n_eod_days": len(eod_dates),
        "first_date": eod_dates[0] if eod_dates else None,
        "last_date": eod_dates[-1] if eod_dates else None,
        "n_trade_events": len(events),
        "median_short_holding_days": (statistics.median(holding_days) if holding_days else None),
        "n_short_closes": len(holding_days),
        "total_tx_cost_usd": round(cum_tx, 2),
        "total_borrow_cost_usd": round(cum_borrow, 2),
        "total_combined_friction_usd": round(cum_tx + cum_borrow, 2),
        "borrow_by_ticker_top5": sorted(borrow_by_ticker.items(), key=lambda x: -x[1])[:5],
        "orig_final_nav": round(final_orig, 2),
        "orig_return_pct": round((final_orig / 1e6 - 1) * 100, 4),
        "tx_only_final_nav": round(final_tx_only, 2),
        "tx_only_return_pct": round((final_tx_only / 1e6 - 1) * 100, 4),
        "combined_final_nav": round(final_combined, 2),
        "combined_return_pct": round((final_combined / 1e6 - 1) * 100, 4),
        "orig_sharpe": sharpe(rets_orig),
        "combined_sharpe": sharpe(rets_combined),
        "orig_sortino": sortino(rets_orig),
        "combined_sortino": sortino(rets_combined),
        "orig_maxdd_pct": round(max_drawdown(nav_series_orig) * 100, 4),
        "combined_maxdd_pct": round(max_drawdown(nav_series_combined) * 100, 4),
        "orig_ann_vol": annualized_vol(rets_orig),
        "combined_ann_vol": annualized_vol(rets_combined),
    }
    if forward:
        summary["forward_orig_nav_5_30"] = round(forward["nav_orig"][forward["forward_dates"][-1]], 2)
        summary["forward_combined_nav_5_30"] = round(forward["nav_combined"][forward["forward_dates"][-1]], 2)
        summary["forward_tx_total_usd"] = round(forward["forward_tx_total"], 2)
        summary["forward_borrow_total_usd"] = round(forward["forward_borrow_total"], 2)
        summary["forward_combined_friction_total_usd"] = round(
            cum_tx + cum_borrow + forward["forward_tx_total"] + forward["forward_borrow_total"], 2)

    # adjusted_summary.json
    with (out_dir / "adjusted_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    # retrofit_audit.md
    audit_lines = [
        f"# Retrofit audit — {name}",
        "",
        f"Cell: `{name}`  Phase: `{phase}`",
        f"Source portfolio dir: `{cell_dir}`",
        f"Output dir: `{out_dir}`",
        "",
        "## Friction summary",
        "",
        f"- Total tx cost (15 bps × N fills): **${cum_tx:,.2f}** across {len(events)} fills",
        f"- Total borrow cost (100% APY × MV daily): **${cum_borrow:,.2f}**",
        f"- Combined friction: **${cum_tx + cum_borrow:,.2f}**",
        "",
        "## Trade events (count by kind)",
        "",
    ]
    by_kind: dict[str, int] = {}
    for ev in events:
        by_kind[ev.kind] = by_kind.get(ev.kind, 0) + 1
    for k, n in sorted(by_kind.items()):
        audit_lines.append(f"- `{k}`: {n}")
    audit_lines += ["", "## Borrow cost — top 5 tickers", ""]
    for t, v in sorted(borrow_by_ticker.items(), key=lambda x: -x[1])[:5]:
        audit_lines.append(f"- `{t}`: ${v:,.2f}")
    audit_lines += ["", "## NAV impact (in-window)", "",
        f"- Original final NAV: **${final_orig:,.2f}** ({summary['orig_return_pct']:+.2f}%)",
        f"- Tx-only final NAV: **${final_tx_only:,.2f}** ({summary['tx_only_return_pct']:+.2f}%)",
        f"- Combined (tx + borrow) final NAV: **${final_combined:,.2f}** ({summary['combined_return_pct']:+.2f}%)",
        f"- ΔNAV from friction: ${final_combined - final_orig:,.2f} ({(final_combined - final_orig)/final_orig*100:+.4f}%)",
    ]
    if holding_days:
        audit_lines += ["", "## Short-side hold-period sanity check", "",
            f"- Median short holding period (business days): {statistics.median(holding_days):.1f}",
            f"- Short closes observed: {len(holding_days)}",
            f"- Per RULES.md §5.16, expected median ~3 trading days; holding-period borrow cost <1% in 95%+ cases at 100% midpoint.",
        ]
    if forward:
        audit_lines += ["", "## Forward extension (Phase 2 only)", "",
            f"- Forward window: 2025-04-22 → {FORWARD_END} ({len(forward['forward_dates'])} trading days)",
            f"- Forward tx cost (DFS→COF synthesized legs): **${forward['forward_tx_total']:,.2f}**",
            f"- Forward borrow cost (open shorts × 1.00/365 daily): **${forward['forward_borrow_total']:,.2f}**",
            f"- Forward NAV (frictionless): ${forward['nav_orig'][forward['forward_dates'][-1]]:,.2f}",
            f"- Forward NAV (combined-friction): ${forward['nav_combined'][forward['forward_dates'][-1]]:,.2f}",
            "",
            "**Synthesis assumption — DFS→COF (2025-05-19)**: sell DFS leg at 2025-05-16 close, buy COF leg at 2025-05-19 close; ratio 1.0192 COF / DFS share; cash component $0.00 (per merger terms). Each leg charged 15 bps on its own notional.",
            "**ANSS merger 2025-07-17 OUT OF CACHE**: Polygon cache ends 2025-05-30, so ANSS held flat at last available close with no synthesized legs (no tx event in forward window for ANSS).",
        ]
    audit_lines += ["", "## Convention notes", "",
        "- Borrow accrues entry day INCLUSIVE, cover day EXCLUSIVE.",
        "- Borrow rate: flat 100% APY ÷ 365 = ~0.27397%/day applied to abs(short market value at EOD).",
        "- Tx cost rate: flat 15 bps single-side per fill (= 30 bps round-trip).",
        "- Cash held at 0% interest in forward extension; UST face accrued at proxy 4.32% APY (presentation_assets parity).",
        "- Median surge-short business days computed as round(calendar_days × 5/7); approximation only.",
    ]
    with (out_dir / "retrofit_audit.md").open("w", encoding="utf-8") as f:
        f.write("\n".join(audit_lines))

    return summary


# ---------------- Master report ----------------

def build_master_report(summaries: list[dict]) -> Path:
    by_name = {s["cell"]: s for s in summaries}
    L = ["# Combined tx + borrow cost retrofit — master report (2026-05-08)",
         "",
         "Friction model (RULES.md §5.16 v2.15):",
         "- **Tx cost**: flat 15 bps single-side per fill (= 30 bps round-trip).",
         "- **Borrow cost**: 100% APY × short market value, charged daily; entry day inclusive, cover day exclusive.",
         "",
         "Output dirs (parallel to `_portfolios/`):",
         "  - `data/decisions/<cell>_tx30bps_borrow100/`",
         "",
         "## Top-line table",
         "",
         "| Cell | Phase | Orig Final NAV | Tx-only NAV | Combined NAV | Orig Ret | Combined Ret | ΔRet (pp) | Tx Total | Borrow Total | Combined Friction | Orig Sharpe | Combined Sharpe | Orig MaxDD | Combined MaxDD |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]

    def fmt_opt(x, fmt):
        return format(x, fmt) if x is not None else "n/a"

    for s in summaries:
        delta_ret = s["combined_return_pct"] - s["orig_return_pct"]
        sh_o = fmt_opt(s["orig_sharpe"], ".3f")
        sh_c = fmt_opt(s["combined_sharpe"], ".3f")
        L.append(
            f"| {s['cell']} | {s['phase']} | "
            f"${s['orig_final_nav']:,.0f} | "
            f"${s['tx_only_final_nav']:,.0f} | "
            f"${s['combined_final_nav']:,.0f} | "
            f"{s['orig_return_pct']:+.2f}% | "
            f"{s['combined_return_pct']:+.2f}% | "
            f"{delta_ret:+.2f} | "
            f"${s['total_tx_cost_usd']:,.0f} | "
            f"${s['total_borrow_cost_usd']:,.0f} | "
            f"${s['total_combined_friction_usd']:,.0f} | "
            f"{sh_o} | {sh_c} | "
            f"{s['orig_maxdd_pct']:+.2f}% | {s['combined_maxdd_pct']:+.2f}% |"
        )

    # Bucket spreads
    def fnav_combined(name):
        return by_name[name]["combined_final_nav"] / 1e6

    def fnav_orig(name):
        return by_name[name]["orig_final_nav"] / 1e6

    L += ["", "## Bucket spreads (Phase 1 March)", "",
          "| Bucket | Comparison | Orig Δpp | Combined-friction Δpp |",
          "|---|---|---:|---:|"]
    if "Multi-noADaS (Mar)" in by_name and "Solo (Mar)" in by_name:
        oa = (fnav_orig("Multi-noADaS (Mar)") - fnav_orig("Solo (Mar)")) * 100
        ca = (fnav_combined("Multi-noADaS (Mar)") - fnav_combined("Solo (Mar)")) * 100
        L.append(f"| A | Multi-noADaS – Solo (coordination value) | {oa:+.2f} | {ca:+.2f} |")
    if "Multi+ADaS (Mar)" in by_name and "Multi-noADaS (Mar)" in by_name:
        ob = (fnav_orig("Multi+ADaS (Mar)") - fnav_orig("Multi-noADaS (Mar)")) * 100
        cb = (fnav_combined("Multi+ADaS (Mar)") - fnav_combined("Multi-noADaS (Mar)")) * 100
        L.append(f"| B | Multi+ADaS – Multi-noADaS (ADaS marginal) | {ob:+.2f} | {cb:+.2f} |")
    L += ["", "## SEC ablation spreads (Phase 2 April)", "",
          "| Window | Orig Δpp (B–A) | Combined-friction Δpp (B–A) |",
          "|---|---:|---:|"]
    if "Cell A (Apr default)" in by_name and "Cell B (Apr no-SEC)" in by_name:
        oc = (fnav_orig("Cell B (Apr no-SEC)") - fnav_orig("Cell A (Apr default)")) * 100
        cc = (fnav_combined("Cell B (Apr no-SEC)") - fnav_combined("Cell A (Apr default)")) * 100
        L.append(f"| In-window (2025-04-22) | {oc:+.2f} | {cc:+.2f} |")
        # Forward
        sa = by_name["Cell A (Apr default)"]; sb = by_name["Cell B (Apr no-SEC)"]
        if "forward_orig_nav_5_30" in sa and "forward_orig_nav_5_30" in sb:
            of = (sb["forward_orig_nav_5_30"] / 1e6 - sa["forward_orig_nav_5_30"] / 1e6) * 100
            cf = (sb["forward_combined_nav_5_30"] / 1e6 - sa["forward_combined_nav_5_30"] / 1e6) * 100
            L.append(f"| Forward (2025-05-30) | {of:+.2f} | {cf:+.2f} |")

    # Sanity check
    L += ["", "## Sanity check — surge-short hold-period vs RULES.md §5.16 narrative", "",
          "RULES.md §5.16 narrative claims: median ~3 trading days, holding-period borrow cost <1% in 95%+ of cases at 100% midpoint.",
          "",
          "| Cell | Median short hold (bus days) | N short closes | Total borrow $ | Borrow as % of $1M | Sanity |",
          "|---|---:|---:|---:|---:|---|"]
    for s in summaries:
        med = s["median_short_holding_days"]
        if med is None:
            L.append(f"| {s['cell']} | n/a | 0 | ${s['total_borrow_cost_usd']:,.0f} | {s['total_borrow_cost_usd']/1e4:.3f}% | n/a |")
        else:
            sanity = "OK" if med <= 5 and s["total_borrow_cost_usd"] / 1e4 <= 1.5 else "INVESTIGATE"
            L.append(f"| {s['cell']} | {med} | {s['n_short_closes']} | ${s['total_borrow_cost_usd']:,.0f} | {s['total_borrow_cost_usd']/1e4:.4f}% | {sanity} |")

    L += ["", "## Forward extension (Phase 2 only — Apr Cells)", "",
          "| Cell | Orig fwd NAV (5-30) | Combined-friction fwd NAV (5-30) | Fwd Tx | Fwd Borrow | Fwd Combined Friction |",
          "|---|---:|---:|---:|---:|---:|"]
    for s in summaries:
        if "forward_orig_nav_5_30" in s:
            L.append(
                f"| {s['cell']} | "
                f"${s['forward_orig_nav_5_30']:,.0f} | "
                f"${s['forward_combined_nav_5_30']:,.0f} | "
                f"${s['forward_tx_total_usd']:,.0f} | "
                f"${s['forward_borrow_total_usd']:,.0f} | "
                f"${s['forward_combined_friction_total_usd']:,.0f} |"
            )

    L += ["", "## Methodology",
          "",
          "1. **Trade events** reconstructed by walking the LATEST EOD JSON `positions[]` and emitting one ENTRY event per position record (date = `entry_date` for equities, `purchase_date` for UST; notional = `notional_at_open` for equities, `purchase_pv` for UST). Closed equity positions also emit one EXIT event (date = `close_date`, notional = `shares × close_price`). The harness appends a fresh position record for every fill (including pyramid adds), so this rule captures every fill exactly once.",
          "2. **Tx cost** = `abs(notional) × 0.0015` per event. Aggregated by date and accumulated cumulatively against `total_nav`.",
          "3. **Borrow cost** = `abs(short market value at EOD) × 1.00 / 365` per day, summed across all open shorts. \"Open at EOD\" = `status != 'closed' OR close_date > day`. Convention: entry day inclusive, cover day exclusive.",
          "4. **Adjusted NAV** = `original NAV − cumulative_tx − cumulative_borrow`. Tx and borrow are pure cash deductions; they do not change underlying P&L dynamics, only translate the NAV curve downward.",
          "5. **Sharpe / Sortino / MaxDD / Vol** recomputed on the adjusted daily NAV series.",
          "6. **Forward extension** (Phase 2 only): cash + UST residual at 4-22 cut held flat (cash 0%, UST proxy 4.32% APY); equity positions MtM'd via Polygon adj1 closes; DFS→COF synthesized as 2 legs each charged 15 bps on 2025-05-19; open shorts at cut accrue 100%/365 × MtM daily through 2025-05-30. ANSS merger 2025-07-17 is out of cache (Polygon ends 5-30); no synthesized legs.",
          "",
          "## Caveats",
          "",
          "- Median-short business days approximated as `round(calendar_days × 5/7)`.",
          "- Borrow cost uses each EOD's `current_value` field as the day's MTM (matches what the harness recorded).",
          "- Forward extension uses `0.0432` UST APY proxy for parity with `presentation_assets_20260508/_build.py`. Original purchase yields are stored in EOD JSONs (1m=4.36, 3m=4.31, 6m=4.20, 2y=3.71); a per-tenor accrual is more accurate but beyond this retrofit's scope.",
          "- Cell A and Cell B both held DFS×2 at the 4-22 cut; both incur the synthesized DFS→COF tx legs in the forward window.",
          ""]
    out = DIAG / "tx_cost_retrofit_master_20260508.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out


# ---------------- Main ----------------

def main() -> int:
    summaries = []
    for name, port_name, phase, has_forward in CELLS:
        cell_dir = PORT_DIR / port_name
        if not cell_dir.exists():
            print(f"  SKIP missing {port_name}", file=sys.stderr); continue
        s = process_cell(name, port_name, phase, has_forward)
        summaries.append(s)
        print(f"  OK {name}: orig=${s['orig_final_nav']:,.0f} -> combined=${s['combined_final_nav']:,.0f}  "
              f"tx=${s['total_tx_cost_usd']:,.0f}  borrow=${s['total_borrow_cost_usd']:,.0f}")

    master = build_master_report(summaries)
    print()
    print(f"MASTER: {master}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
