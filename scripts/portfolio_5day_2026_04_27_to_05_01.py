"""
5-day historical replay 2026-04-27 .. 2026-05-01 — seeds the dashboard
Current Portfolio tab with real Haiku decisions over a 5-trading-day
window.

Cadence (per user revision 2026-05-02; Chunk C 2026-05-02):
  - Surge-short: rank once per day from cached daily OHLC (daily-proxy
    makes intraday anchors redundant). Fire at the 09:30 ET anchor when
    ≥1 candidate passes §2.1. Take up to MAX_SURGE_PER_DAY=3.
    Pipeline = 6 agents per Chunk B (§5.17 surge-short prelude split:
    narrative_event + alt_data_verify + fundamental + sleeve + risk + pm).
  - Quality-long: weekly event-driven scan on the FIRST trading day of
    the window (Monday 2026-04-27 ONLY). Loads SP500∪NDX100 via FMP
    sp500-constituent + nasdaq-constituent endpoints, queries
    corporate_calendar for earnings in [today, today+14 calendar days],
    caps at 5 candidates by earliest event date, runs the 7-agent
    quality_long pipeline (Chunk B prelude split: narrative_event +
    alt_data_verify + network_effect + valuation + sleeve + risk + pm).
    Other days: no QL entry pipeline (cover/monitoring only; no
    starting positions in this run).
  - Daily alt_data_verify portfolio snapshot: SKIPPED (no helper).
  - Cover evaluation (R-COVER-07): SKIPPED (runtime not built).

Spec divergences (logged in _summary.json):
  - daily_alt_data_verify_skipped
  - cover_eval_R-COVER-07_skipped
  - candidate_selection_lookahead_daily_proxy
  - surge_short_anchor_fired_once_per_day_at_first_anchor

Hard cost cap: $15 (ANTHROPIC_HARD_STOP_USD=15.00). Run aborts cleanly
if exceeded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import uuid
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

os.environ.setdefault("LLM_PROVIDER", "anthropic")
# Default $7 cap. main() may override via --hard-cap. setdefault so a
# pre-set ANTHROPIC_HARD_STOP_USD env var (e.g. CI override) still wins.
os.environ.setdefault("ANTHROPIC_HARD_STOP_USD", "7.00")

# §5.16 v2.15 (PART 4 wiring 2026-05-08) — flat 15 bps tx cost on every fill
# + 100% APY daily borrow on open shorts. Helpers live in
# src/portfolio/cost_helpers.py; this module is the single source of truth
# for the runtime cost-model parameters. Cost model is portfolio-side and
# does NOT enter the evidence packet — canonical AAPL hash is unaffected.
from src.portfolio.cost_helpers import apply_tx_cost, apply_daily_borrow  # noqa: E402


def _parse_args(argv=None) -> argparse.Namespace:
    """Pass 8 Step B2-mini-A (2026-05-05) — accept arbitrary backtest window
    via CLI flags. Defaults preserve the original 2026-04-27..2026-05-01
    5-day window so flag-less invocations and pytest module imports work
    unchanged.
    """
    p = argparse.ArgumentParser()
    p.add_argument("--start-date", type=str, default=None,
                   help="ISO date inclusive; defaults to module WINDOW_DAYS[0]")
    p.add_argument("--end-date", type=str, default=None,
                   help="ISO date inclusive; defaults to module WINDOW_DAYS[-1]")
    p.add_argument("--output", type=str, default=None,
                   help="output dir relative to ROOT (or absolute); "
                        "defaults to data/decisions/replay_5day")
    p.add_argument("--hard-cap", type=float, default=None,
                   help="USD hard cost cap; defaults to module HARD_COST_USD ($7)")
    p.add_argument("--port-dir", type=str, default=None,
                   help="portfolio dir (eod_state + pnl_history) relative "
                        "to ROOT or absolute; defaults to data/portfolio")
    p.add_argument("--cell-id", type=str, default=None,
                   help="Step B ablation cell label (informational; the "
                        "ABLATION_* env vars do the actual routing)")
    return p.parse_args(argv)

from src.llm.factory import get_provider
from src.llm.cache import LLMCache
from src.llm.anthropic_provider import get_cost_ledger
from src.agents.runner import run_all_agents_for_candidate
from src.evidence_packet.generator import generate_evidence_packet
from src.portfolio.veto_filter import filter_risk_veto_by_candidate_type
from src.utils.trading_calendar import (
    is_trading_day, previous_trading_day, trading_days_between,
    next_trading_day,  # B9 fix (2026-05-06): T+1 next-trading-day OPEN
)

ET = ZoneInfo("America/New_York")
WINDOW_DAYS = ["2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01"]
INITIAL_CAPITAL = 1_000_000.0
HARD_COST_USD = 7.0
MAX_SURGE_PER_DAY = 3

# Pass 8 Step B1.8 (2026-05-04) — close C1 from B1.7 sign-off.
# This harness operates exclusively in replay (PIT-safe) mode. The
# packet generator's strict_pit_mode now auto-derives from this:
# decision_mode='historical_replay' → strict_pit_mode=True implicit
# (forbidden-paths scrubber + lookahead-abort fire automatically). The
# assertion in main() guards against accidental flips to 'live'.
BACKTEST_MODE = "replay"
MAX_QL_PER_WEEK = 5
QL_EARNINGS_LOOKAHEAD_DAYS = 14
SURGE_PCT_GATE = 50.0
SURGE_VOL_GATE = 1_000_000
SURGE_PRIOR_CLOSE_GATE = 2.0
ANCHOR_ET = dtime(9, 30)
RULE_VERSION = "v0.9.0_pass8_hardrule"


# ── Pass 8 §31 SPY-drawdown equity floor (Pass 8.G refinement 2026-05-05) ──
# §31 v2 (2026-05-07 user authorization): per-trigger 3% NAV minimum equity
# buy mandate REPLACES the floor-escalation logic. Each trigger fires when
# 5-day rolling drawdown <= -5% (threshold lowered from -10%); each trigger
# is independent (no cooldown); engine ENSURES 3% NAV new equity is added
# that day (with engine SPY ETF fallback when PM allocation < 3%).
# §31.1: drawdown_pct = (SPY_today_close - max(SPY_close[-5:])) / max(SPY_close[-5:])
# §31.2: per-trigger 3% NAV mandate
# §31.5: daily evaluation + T+1 next-open execution

# §31 v2 force-buy mandate constant
SECTION_31_FORCE_BUY_MIN_PCT_NAV = 0.03  # 3% NAV per §31.2 v2


def compute_trigger_history_state() -> dict:
    """Persistent state for §31 trigger tracking across the backtest run (v2).

    Lives at state['trigger_history'] and is persisted by write_eod_state.

    Fields:
      events (list): forensic log of each trigger fire and force-buy outcome.
        Each entry: {date, drawdown_pct, equity_added_pct, force_buy_invoked,
                     audit_event_ref}.
      last_eval_date (str | None): ISO date of last EOD evaluation.

    Note: v2 does NOT carry cumulative_floor_pct, last_trigger_week_anchor,
    last_trigger_date, triggers_history (old schema). State migration handled
    by callers; absent old fields = clean v2 state.
    """
    return {
        'events': [],
        'last_eval_date': None,
    }


def evaluate_section_31_trigger(
    spy_history: list[tuple],   # last ≥5 trading days of (date_iso, close)
    current_date_iso: str,
) -> tuple[bool, float]:
    """§31.1 v2 — daily SPY rolling 5-day drawdown evaluation.

    Returns (trigger_active, drawdown_pct).
    - trigger_active = True iff drawdown_pct <= -0.05 AND ≥5 days of history.
    - drawdown_pct = (today_close - max(last_5_closes)) / max(last_5_closes).

    Threshold lowered v2 (2026-05-07) from -0.10 to -0.05 per user directive.
    No cooldown, no re-anchor — every trigger day fires independently.
    """
    if len(spy_history) < 5:
        return (False, 0.0)
    last_5_closes = [float(c) for (_d, c) in spy_history[-5:]]
    today_close = last_5_closes[-1]
    rolling_high = max(last_5_closes)
    if rolling_high <= 0:
        return (False, 0.0)
    drawdown_pct = (today_close - rolling_high) / rolling_high
    trigger_active = drawdown_pct <= -0.05
    return (trigger_active, float(drawdown_pct))


def section_31_force_buy_gap(
    nav_now: float,
    new_equity_added_today: float,
) -> float:
    """§31.2 v2 — given today's NAV and the dollars of new equity already
    added on this trigger day (across §3.7+§3.8 QL candidates and/or
    §3.9 ETFs), return the additional dollars needed to satisfy the 3% NAV
    minimum. Returns 0.0 if PM allocation already meets/exceeds the mandate.
    """
    target = nav_now * SECTION_31_FORCE_BUY_MIN_PCT_NAV
    gap = target - float(new_equity_added_today)
    return max(0.0, float(gap))


def engine_force_buy_spy_etf(
    *,
    gap_dollars: float,
    decision_timestamp_iso: str,
    nav_now: float,
    state: dict,
    audit_event_ref: str = "",
) -> dict | None:
    """§31.6 v2 engine fallback — buy SPY ETF for the gap.

    Executes T+1 next-open per §6.1. Appends a position with
    kind='quality_long', sleeve='quality_long', ticker='SPY'.
    Marks the position with section_31_force_buy=True for audit.

    Risk §5.13(a) sleeve cap is respected: if the new buy would push the
    equity sleeve above the regime cap (default 40%), downsize defensively.
    Per §31.6 the engine fallback overrides PM but still respects §4.7.

    Returns the appended position dict, or None if no buy was executed
    (insufficient room or invalid gap).
    """
    if gap_dollars <= 0 or nav_now <= 0:
        return None

    # Resolve T+1 next-trading-day open price for SPY.
    decision_date_iso = decision_timestamp_iso[:10]
    try:
        _today_d = date.fromisoformat(decision_date_iso)
        _next_td = next_trading_day(_today_d)
    except Exception as e:
        log_error(decision_date_iso, "SPY",
                  "section_31_force_buy_calendar_failed",
                  f"{type(e).__name__}: {e}")
        return None
    next_iso = _next_td.isoformat()

    # Fetch SPY OHLC for the T+1 open price.
    try:
        from src.data_adapters.fmp_adapter import get_historical_daily
        rows = get_historical_daily(
            "SPY", start_date=decision_date_iso, end_date=next_iso,
        )
    except Exception as e:
        log_error(decision_date_iso, "SPY",
                  "section_31_force_buy_price_fetch_failed",
                  f"{type(e).__name__}: {e}")
        return None
    next_row = next(
        (r for r in (rows or []) if r.get("date") == next_iso), None,
    )
    spy_open = None
    if next_row is not None:
        try:
            spy_open = float(next_row.get("open") or 0.0)
        except (TypeError, ValueError):
            spy_open = None
    if spy_open is None or spy_open <= 0:
        log_error(decision_date_iso, "SPY",
                  "section_31_force_buy_no_t1_open",
                  f"no usable T+1 open for SPY on {next_iso}")
        return None

    # Risk §5.13(a) sleeve cap check (default 40% — same conservative cap
    # as B8 quality_long sleeve clamp). Downsize if gap would breach.
    EQUITY_MAX_DEFAULT = 0.40
    _ql_existing_dollars = sum(
        abs(float(p.get("size_pct_at_open", 0.0) or 0.0)) * nav_now
        for p in state.get("positions", [])
        if p.get("side") == "long"
        and p.get("sleeve", "quality_long") == "quality_long"
        and p.get("status", "open") == "open"
    )
    _ss_dollars = sum(
        abs(float(p.get("size_pct_at_open", 0.0) or 0.0)) * nav_now
        for p in state.get("positions", [])
        if p.get("side") == "short" and p.get("status", "open") == "open"
    )
    sleeve_room_dollars = max(
        0.0,
        nav_now * EQUITY_MAX_DEFAULT - _ss_dollars - _ql_existing_dollars,
    )
    actual_buy_dollars = min(float(gap_dollars), sleeve_room_dollars)
    if actual_buy_dollars <= 0:
        log_error(decision_date_iso, "SPY",
                  "section_31_force_buy_no_sleeve_room",
                  f"gap=${gap_dollars:.2f} sleeve_room=${sleeve_room_dollars:.2f}")
        return None

    shares = round(actual_buy_dollars / spy_open, 2)
    pos = {
        "ticker": "SPY",
        "side": "long",
        "kind": "equity",
        "sleeve": "quality_long",
        "entry_price": spy_open,
        "shares": shares,
        "entry_date": next_iso,
        "decision_id": uuid.uuid4().hex[:12],
        "size_pct_at_open": actual_buy_dollars / nav_now,
        "notional_at_open": round(actual_buy_dollars, 2),
        "section_31_force_buy": True,
        "section_31_audit_event_ref": audit_event_ref,
    }
    state.setdefault("positions", []).append(pos)
    if "cash_balance" in state:
        state["cash_balance"] = float(state["cash_balance"]) - actual_buy_dollars
    elif "cash" in state:
        state["cash"] = float(state["cash"]) - actual_buy_dollars
    # §5.16 v2.15 — flat 15 bps tx cost on §31 force-buy
    forcebuy_tx = apply_tx_cost(actual_buy_dollars)
    if "cash_balance" in state:
        state["cash_balance"] = float(state["cash_balance"]) - forcebuy_tx
    elif "cash" in state:
        state["cash"] = float(state["cash"]) - forcebuy_tx
    state["tx_cost_today"] = float(state.get("tx_cost_today", 0.0)) + forcebuy_tx
    state["tx_cost_cumulative"] = float(state.get("tx_cost_cumulative", 0.0)) + forcebuy_tx
    pos["tx_cost_entry_usd"] = forcebuy_tx
    return pos


# Backward-compat shim — old callers expecting compute_drawdown_floor get
# 0.0 (no floor in v2 semantics). Logical floor in v2 is the 3% per-trigger
# mandate, not a persistent multi-week cumulative floor.
def compute_drawdown_floor(
    spy_history: list,
    regime_baseline: float,
    regime_max_cap: float,
) -> float:
    """DEPRECATED v2 (2026-05-07): §31 v2 has no floor concept; per-trigger
    3% mandate replaces escalation logic. Returns 0.0 to make any leftover
    consumer-side `if floor > X` check fall through. Use
    evaluate_section_31_trigger / section_31_force_buy_gap /
    engine_force_buy_spy_etf instead."""
    return 0.0

# Live adapters wired into every replay packet (2026-05-02). Includes the
# 3 default alt-data sources + 2 default OpenCLI use cases (which
# `live_adapters=True` would also enable) PLUS the OPTIONAL adapters
# scoped 2026-04-29..2026-05-01: sec_form4, sec_13f, polygon_news,
# fmp_sentiment. Without this explicit tuple, replay packets would
# arrive with the new info_integrity / sentiment_ownership / filing
# / narrative_gap blocks empty (the bug Chunk-final fixed).
LIVE_ADAPTERS_TUPLE = (
    "wikipedia_pageviews",
    "sec_edgar",
    "github_public",
    "sec_8k_fulltext",
    "github_commit_messages",
    "sec_form4",
    "sec_13f",
    "polygon_news",
    "fmp_sentiment",
)

# ── Step B 4-cell ablation env-var hooks (additive 2026-05-06) ─────────
# ABLATION_AGENT_MODE       — 'multi' (default) or 'solo' (Cell 1)
# ABLATION_TOPOLOGY         — 'pipeline' (default) or 'flat'
# ABLATION_LEAVE_OUT_SOURCES — comma-separated source ids to remove from
#                              LIVE_ADAPTERS_TUPLE (Cell 4: SEC sources)
# ABLATION_DISABLE_ADAS     — '1' to skip §24 ADaS persistence + lag (Cell 2)
# Defaults reproduce the pre-Step-B harness behaviour exactly when no
# env var is set, so non-ablation invocations (live, prior pytest, etc.)
# remain byte-identical.
_ABLATION_AGENT_MODE = os.environ.get("ABLATION_AGENT_MODE", "multi")
_ABLATION_TOPOLOGY = os.environ.get("ABLATION_TOPOLOGY", "pipeline")
_ABLATION_LEAVE_OUT_RAW = os.environ.get("ABLATION_LEAVE_OUT_SOURCES", "")
_ABLATION_LEAVE_OUT = tuple(
    s.strip() for s in _ABLATION_LEAVE_OUT_RAW.split(",") if s.strip()
)
EFFECTIVE_LIVE_ADAPTERS_TUPLE = tuple(
    s for s in LIVE_ADAPTERS_TUPLE if s not in _ABLATION_LEAVE_OUT
)

# QL universe cache — populated once on first weekly scan
_QL_UNIVERSE_CACHE: list[str] | None = None
_QL_CALENDAR_CACHE_STATS = {"calls_made": 0, "warm_hits": 0, "cold_misses": 0,
                             "errors": 0}


# Fail-fast guard 2026-05-03: abort the replay only when cumulative ≥3
# ticker pipeline failures occur across all days (signals systemic issue
# such as API outage or auth break, not transient hiccup). 1-2 failures
# are tolerated so a single bad ticker / momentary timeout does not
# discard the whole run. Triggered from inside the per-ticker
# try/except wrappers added by the 2026-05-03 robustness pass.
_PIPELINE_FAILURE_COUNT = 0
_PIPELINE_SUCCESS_COUNT = 0
_FAILFAST_THRESHOLD = 3


def _record_pipeline_failure(ticker: str, exception_type: str,
                              exception_msg: str, day_iso: str) -> None:
    global _PIPELINE_FAILURE_COUNT
    _PIPELINE_FAILURE_COUNT += 1
    print(f"[FAIL-FAST] ticker={ticker} day={day_iso} "
          f"failures={_PIPELINE_FAILURE_COUNT} "
          f"successes={_PIPELINE_SUCCESS_COUNT} "
          f"exception={exception_type}: {exception_msg[:200]}",
          flush=True)
    if _PIPELINE_FAILURE_COUNT >= _FAILFAST_THRESHOLD:
        print(f"!!! FAIL-FAST ABORT: cumulative {_PIPELINE_FAILURE_COUNT} "
              f"pipeline failures (threshold {_FAILFAST_THRESHOLD}) — "
              f"systemic issue suspected. Exiting replay.",
              flush=True)
        import sys
        sys.exit(2)


def _record_pipeline_success(ticker: str, day_iso: str) -> None:
    global _PIPELINE_SUCCESS_COUNT
    _PIPELINE_SUCCESS_COUNT += 1


# === ABORT THRESHOLDS (added 2026-05-04 for unattended runs) ===
_FAIL_COUNTER = {
    "agent_exceptions": 0,
    "json_parse_fails": 0,
    "fred_5xx_total": 0,
    "consecutive_candidate_fails": 0,
}
_ABORT_THRESHOLDS = {
    "agent_exceptions": 8,
    "json_parse_fails": 10,
    "fred_5xx_total": 50,
    "consecutive_candidate_fails": 3,
}


def _check_abort_thresholds(label=""):
    """Raise RuntimeError if any failure counter exceeds its threshold.
    Called at strategic points in the run."""
    breached = []
    for k, v in _FAIL_COUNTER.items():
        if v >= _ABORT_THRESHOLDS[k]:
            breached.append(f"{k}={v} (threshold {_ABORT_THRESHOLDS[k]})")
    if breached:
        msg = f"ABORT_THRESHOLD_TRIPPED at {label}: " + "; ".join(breached)
        raise RuntimeError(msg)


def _bump_fail(key, n=1):
    """Increment a fail counter; consecutive must be bumped explicitly."""
    if key in _FAIL_COUNTER:
        _FAIL_COUNTER[key] += n
    if key != "consecutive_candidate_fails":
        # any other fail still adds to consecutive too
        _FAIL_COUNTER["consecutive_candidate_fails"] += 0  # don't auto-bump


def _reset_consecutive():
    """Reset consecutive counter on any successful candidate."""
    _FAIL_COUNTER["consecutive_candidate_fails"] = 0


CACHE_DIR = ROOT / "data" / "cache" / "historical_prices"
RANGE_KEY = "2026-04-27_2026-05-01"
OUT_ROOT = ROOT / "data" / "decisions" / "replay_5day"
PORT_DIR = ROOT / "data" / "portfolio"
SUMMARY_PATH = OUT_ROOT / "_summary.json"
ERRORS_CSV = OUT_ROOT / "errors.csv"

state = {"cash": INITIAL_CAPITAL, "positions": []}
all_triggers: list[dict] = []
per_day: dict[str, dict] = {}


def log_error(date_iso: str, ticker: str, kind: str, msg: str) -> None:
    is_new = not ERRORS_CSV.exists()
    ERRORS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with ERRORS_CSV.open("a", encoding="utf-8") as f:
        if is_new:
            f.write("date,ticker,kind,msg\n")
        safe = msg.replace('"', '""')
        f.write(f'{date_iso},{ticker},{kind},"{safe}"\n')


# §27.10 hard exclusion (2026-05-02 v0.8.7 UST refactor): ETF FI universe
# tickers are EXCLUDED from any sleeve. JEPI/JEPQ remain (equity sleeve).
EXCLUDED_FI_ETFS = frozenset({"BIL", "SHY", "IEF", "TLT", "IEI", "SHV"})


def rank_surge_candidates(date_iso: str) -> list[dict]:
    """Return §2.1-passing surge-short candidates for date_iso, PIT-safe.

    Pass 8 Step B2-mini-A (2026-05-05): replaces the legacy per-ticker
    historical_prices cache scan with the B1.7 PIT-safe pipeline:
        market_screener.get_top_gainers(decision_date=..., mode='replay')
            -> _compute_historical_surge -> polygon_grouped_daily(adj=True)
        filter_surge_candidates(rows, decision_date=...)
            -> §2.1 mechanical floor + §2.1 PIT-safe EPS<0+OM<0 fundamental
            gate + §2.15 technical-rebound exclusion + §2.14 earnings-
            proximate flag

    Returns rows in the schema downstream `run_surge_short()` /
    `update_position_from_trigger()` expect — both consume only
    candidate["ticker"] (and the print loop reads change_pct / volume /
    close), all of which the PIT path returns under identical names.
    """
    from src.agents.market_screener import get_top_gainers
    from src.rules.surge_short_rules import filter_surge_candidates

    today_d = date.fromisoformat(date_iso)
    try:
        gainers = get_top_gainers(decision_date=today_d, mode="replay")
    except ValueError as e:
        log_error(date_iso, "_rank", "rank_surge_replay_error", str(e))
        return []
    # Drop ETF FI tickers (§27.10) before the rules-layer fundamental gate
    # so SHY / TLT / IEF / etc. never enter the equity sleeve candidate list.
    gainers = [g for g in gainers if g.get("ticker") not in EXCLUDED_FI_ETFS]
    candidates = filter_surge_candidates(gainers, decision_date=today_d)
    candidates.sort(key=lambda c: c.get("change_pct", 0), reverse=True)
    return candidates


def ledger_total() -> float:
    try:
        return get_cost_ledger().total_usd()
    except Exception:
        return 0.0


def cost_check_or_abort(label: str) -> None:
    cost = ledger_total()
    if cost > HARD_COST_USD:
        raise RuntimeError(
            f"COST_GATE_TRIPPED at {label}: ${cost:.4f} > ${HARD_COST_USD}"
        )


def _bootstrap_bil_position() -> dict | None:
    """§27 fixed-income sleeve seed (2026-05-02 BIL bug fix).

    Buys BIL with 100% of INITIAL_CAPITAL at the open price on
    WINDOW_DAYS[0]. The position is tagged `sleeve="fixed_income"` so
    write_eod_state routes it to the fixed_income sleeve total.

    BIL OHLC is fetched once via fmp_adapter.get_historical_daily on a
    cache miss and stored under data/cache/historical_prices/BIL.json.
    No cost per agent — single FMP call, then disk-cached.
    """
    cache_file = CACHE_DIR / "BIL.json"
    rows: list[dict] = []
    if cache_file.exists():
        try:
            rows = json.loads(cache_file.read_text()).get(RANGE_KEY, [])
        except (OSError, json.JSONDecodeError):
            rows = []
    if not rows:
        from src.data_adapters.fmp_adapter import get_historical_daily
        rows = get_historical_daily(
            "BIL", start_date=WINDOW_DAYS[0], end_date=WINDOW_DAYS[-1],
        )
    open_row = next((r for r in rows if r.get("date") == WINDOW_DAYS[0]), None)
    if not open_row or not open_row.get("open"):
        log_error(WINDOW_DAYS[0], "BIL", "bil_bootstrap_failed",
                  "no BIL open price for window start; fixed_income sleeve "
                  "will remain at 0 (§27 spec violation)")
        return None
    bil_open = float(open_row["open"])
    shares = round(INITIAL_CAPITAL / bil_open, 2)
    pos = {
        "ticker": "BIL", "side": "long", "sleeve": "fixed_income",
        "entry_price": bil_open, "shares": shares,
        "entry_date": WINDOW_DAYS[0], "decision_id": "bil_bootstrap_t0",
        "size_pct_at_open": 100.0,
        "notional_at_open": round(shares * bil_open, 2),
    }
    state["positions"].append(pos)
    state["cash"] -= shares * bil_open
    # §5.16 v2.15 — flat 15 bps tx cost on BIL bootstrap fill
    bil_tx = apply_tx_cost(shares * bil_open)
    state["cash"] -= bil_tx
    state["tx_cost_today"] = float(state.get("tx_cost_today", 0.0)) + bil_tx
    state["tx_cost_cumulative"] = float(state.get("tx_cost_cumulative", 0.0)) + bil_tx
    pos["tx_cost_entry_usd"] = bil_tx
    return pos


def _current_price(ticker: str, date_iso: str, fallback: float) -> float:
    """Close on date_iso for `ticker`. Pass 8 Step B2-mini-A (2026-05-05):
    primary lookup is Polygon grouped-daily (B2-prep cache; works for any
    date in any backtest window). Falls back to the legacy per-ticker
    historical_prices cache for back-compat with the original
    2026-04-27..2026-05-01 run, then to `fallback` (typically entry_price).
    """
    try:
        from src.data_adapters.market_data import polygon_grouped_daily
        blob = polygon_grouped_daily(date_iso, adjusted=True)
        if blob.get("source") in ("polygon_cache", "polygon_live"):
            row = blob.get("results", {}).get(ticker)
            if row and row.get("c") is not None:
                return float(row["c"])
    except Exception:
        pass
    cache_file = CACHE_DIR / f"{ticker}.json"
    if cache_file.exists():
        try:
            rows = json.loads(cache_file.read_text()).get(RANGE_KEY, [])
            row = next((r for r in rows if r["date"] == date_iso), None)
            if row is not None:
                return float(row["close"])
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            pass
    return fallback


def _position_value(p: dict, date_iso: str) -> float:
    """Mark-to-market SIGNED market value as of date_iso close.

    Bug H fix (2026-05-04): returns SIGNED market value, not P&L.
      long:  +shares * current_price          (asset)
      short: -shares * current_price          (liability)
    Cash already includes short proceeds at entry, so
      NAV = cash + sum(signed_market_value)
    nets the short liability against the proceeds correctly.

    Pass 8 Step B2-mini-E (2026-05-05) — Bug Q fix: UST positions
    appended by `apply_ust_decision_to_state` lack a `ticker` field
    (their identity is `tenor`/`face_value`/`kind="ust"`). Branch
    on `p.get("kind")` so HTM par-value MTM is used for UST instead
    of crashing on `p["ticker"]`.
    """
    if p.get("kind") == "ust":
        # Held-to-maturity par-value MTM — UST positions have no ticker
        # in the equity-data adapters. The §27 sleeve carries them at
        # face value through maturity; if/when an intra-tenor MTM model
        # is wired (yield-curve discount), this branch can switch to it.
        return float(p.get("face_value", 0.0))
    cur = _current_price(p["ticker"], date_iso, p["entry_price"])
    if p["side"] == "long":
        return p["shares"] * cur
    return -p["shares"] * cur


def compute_nav(date_iso: str) -> tuple[float, float, float]:
    pos_value = sum(_position_value(p, date_iso) for p in state["positions"])
    nav = state["cash"] + pos_value
    return nav, state["cash"], pos_value


# ── Pass 8 Step B2-mini-D (2026-05-05) — cover-eval trigger helpers ──
# §33.4 ladder bands at {5%, 15%, 25%, 35%}. Crossing a band in either
# direction since the last eval makes the position newly interesting and
# fires the LLM. Bidirectional handles "ran up to 12% then back to 4%".
_COVER_LADDER_BANDS = (0.05, 0.15, 0.25, 0.35)


def compute_unrealized_profit_pct_dict(short_pos: dict) -> float | None:
    """Dict-shape wrapper around cover_eval.compute_unrealized_profit_pct
    that swallows the ValueError on missing prices and returns None
    instead, so the harness can use it as a trigger gate without try/except."""
    from src.portfolio.cover_eval import compute_unrealized_profit_pct
    cp = short_pos.get("current_price")
    if cp is None:
        return None
    try:
        return compute_unrealized_profit_pct(short_pos, float(cp))
    except (ValueError, TypeError):
        return None


def check_profit_band_crossed(
    short_pos: dict, current_profit_pct: float | None,
) -> bool:
    """Return True iff current_profit_pct lies on the opposite side of
    any §33.4 band from short_pos['last_eval_profit_pct']. First eval
    (no last_eval_profit_pct yet) returns False — the open-day eval is
    handled by the days_held==0 / 30-day-mark / earnings checks, not by
    band crossing."""
    if current_profit_pct is None:
        return False
    last = short_pos.get("last_eval_profit_pct")
    if last is None:
        return False
    last = float(last)
    cur = float(current_profit_pct)
    for band in _COVER_LADDER_BANDS:
        if (last < band <= cur) or (cur < band <= last):
            return True
    return False


def check_new_corporate_calendar_event(
    ticker: str, last_eval_date_iso: str | None, current_date_iso: str,
) -> bool:
    """§10.14.R-COVER-10 condition iv: new earnings event since last eval.

    Uses get_corporate_calendar_pit (cached) so this is cheap on warm
    cache. Returns True iff there is at least one earnings.past event
    with date strictly between last_eval_date_iso (exclusive) and
    current_date_iso (inclusive). On adapter error, returns False (no
    refresh fired) — the bypass branches still cover hard limits."""
    if not last_eval_date_iso:
        return False
    try:
        from src.data_adapters.fmp_adapter import get_corporate_calendar_pit
        cutoff = datetime.fromisoformat(current_date_iso).replace(tzinfo=timezone.utc)
        cal = get_corporate_calendar_pit(ticker, cutoff,
                                          lookback_days=30, lookahead_days=0)
        past = (cal.get("earnings") or {}).get("past") or []
        for r in past:
            d = (r.get("date") or "")[:10]
            if last_eval_date_iso < d <= current_date_iso:
                return True
    except Exception:
        return False
    return False


def _extract_pm_decision(out: dict) -> tuple[dict | None, str]:
    """Find the PM agent output in the run-level envelope."""
    aos = out.get("agent_outputs", {}) or {}
    for k in ("pm", "pm_agent", "pm_flat", "risk_pm"):
        if k in aos:
            v = aos[k]
            if isinstance(v, dict):
                # parsed_output structure varies; try .parsed first
                parsed = v.get("parsed") or v.get("parsed_output") or v
                return parsed, k
    return None, ""


def run_surge_short(
    candidate: dict, decision_ts_iso: str, provider, cache, date_iso: str,
) -> dict | None:
    ticker = candidate["ticker"]
    t0 = time.time()
    cost_before = ledger_total()
    try:
        packet = generate_evidence_packet(
            ticker=ticker, decision_timestamp=decision_ts_iso,
            live_adapters=EFFECTIVE_LIVE_ADAPTERS_TUPLE,
            decision_mode='historical_replay',
        )
    except Exception as e:
        log_error(date_iso, ticker, "packet_gen_failed",
                  f"{type(e).__name__}: {e}")
        return None
    try:
        out = run_all_agents_for_candidate(
            evidence_packet=packet,
            candidate_type="surge_short",
            provider=provider,
            cache=cache,
            agent_mode=_ABLATION_AGENT_MODE,
            topology=_ABLATION_TOPOLOGY,
        )
    except Exception as e:
        log_error(date_iso, ticker, "agent_run_failed",
                  f"{type(e).__name__}: {e}")
        return None

    if out.get("pm_error"):
        _bump_fail("json_parse_fails")
        _check_abort_thresholds("PM JSON parse fail")

    # §5.17 + §13.6 + §3.8 surge-short veto-scope filter (2026-05-04).
    # The runner co-runs Risk + PM inside src/agents/runner.py
    # (off-limits per spec), so the filter is applied post-runner to
    # the Risk output dict in `out["agent_outputs"]["risk"]`. Forensic
    # JSON below captures the downgraded trips. Note: PM's echoed
    # trips (in agent_outputs.pm.veto_conditions_evaluated) are not
    # rewritten — the helper operates on Risk only per spec.
    risk_out = (out.get("agent_outputs") or {}).get("risk")
    if isinstance(risk_out, dict):
        filter_risk_veto_by_candidate_type(risk_out, "surge_short")

    elapsed = time.time() - t0
    cost_after = ledger_total()
    trigger_cost = cost_after - cost_before
    pm_decision, pm_agent_key = _extract_pm_decision(out)
    trigger_id = uuid.uuid4().hex[:12]

    forensic_path = OUT_ROOT / date_iso / f"{ticker}_{trigger_id}.json"
    forensic_path.parent.mkdir(parents=True, exist_ok=True)
    forensic = {
        "trigger_id": trigger_id,
        "ticker": ticker,
        "candidate_type": "surge_short",
        "decision_timestamp": decision_ts_iso,
        "candidate_selection_lookahead": "daily_proxy_close_used",
        "agent_reasoning_pit_clean": True,
        "elapsed_s": round(elapsed, 2),
        "trigger_cost_usd": round(trigger_cost, 6),
        "cumulative_cost_usd": round(cost_after, 6),
        "ranking_resolution": "daily_proxy",
        "candidate_ranking_inputs": candidate,
        "evidence_packet_hash": packet.get("envelope", {}).get(
            "evidence_packet_hash"),
        "locked_decision_id": packet.get("envelope", {}).get(
            "locked_decision_id"),
        "pm_agent_key": pm_agent_key,
        "pm_decision_extract": pm_decision,
        # Pass 6 robustness 2026-05-03: when PM call fails, runner returns
        # partial envelope with pm_error populated and prelude outputs
        # intact. Surface pm_error in forensic so debugger sees the failure
        # mode + prelude rationale together.
        "pm_error": out.get("pm_error"),
        "agent_outputs_summary": {
            k: {"validation_status": (v.get("validation_status") if isinstance(v, dict) else None)}
            for k, v in (out.get("agent_outputs", {}) or {}).items()
        },
        "agent_outputs_full": out.get("agent_outputs", {}),
    }
    forensic_path.write_text(json.dumps(forensic, indent=2, default=str))

    return {
        "ticker": ticker,
        "trigger_id": trigger_id,
        "candidate_type": "surge_short",
        "decision_timestamp": decision_ts_iso,
        "trigger_cost_usd": round(trigger_cost, 6),
        "elapsed_s": round(elapsed, 2),
        "pm_agent_key": pm_agent_key,
        "pm_decision": pm_decision,
        "candidate": candidate,
        "forensic_path": str(forensic_path.relative_to(ROOT)),
    }


def run_quality_long(
    candidate: dict, decision_ts_iso: str, provider, cache, date_iso: str,
) -> dict | None:
    """Run the 7-agent quality_long pipeline for one ticker (Chunk C).

    Mirror of run_surge_short structure but candidate_type='quality_long',
    so the runner.py prelude split (Chunk B) selects the 7-agent path:
    narrative_event, alt_data_verify, network_effect, valuation, sleeve,
    risk, pm. Forensic file: QL_<ticker>_<trigger_id>.json.
    """
    ticker = candidate["ticker"]
    t0 = time.time()
    cost_before = ledger_total()
    try:
        packet = generate_evidence_packet(
            ticker=ticker, decision_timestamp=decision_ts_iso,
            live_adapters=EFFECTIVE_LIVE_ADAPTERS_TUPLE,
            decision_mode='historical_replay',
        )
    except Exception as e:
        log_error(date_iso, ticker, "ql_packet_gen_failed",
                  f"{type(e).__name__}: {e}")
        return None

    # Bug Z fix (2026-05-06): §3.8 mechanical hard-fundamental gate pre-screen.
    # Read fundamental_snapshot block, evaluate gate, attach result to packet
    # so LLM agents can reference it. Per §3.8 spec: failure ⇒ rule engine marks
    # recommended_action ∈ {watch, no_trade, needs_more_evidence} but agent
    # retains discretion. We attach the gate result for LLM visibility but do
    # NOT mechanically short-circuit — letting the LLM weigh fundamentals,
    # valuation, and other inputs in concert with the gate outcome.
    try:
        from src.rules.quality_long_rules import passes_hard_fundamental_gates
        fs = packet.get("fundamental_snapshot") or {}
        gate_result = passes_hard_fundamental_gates(fs)
        packet["fundamental_gate_38"] = {
            "passes": gate_result["passes"],
            "failed_gates": gate_result["failed_gates"],
            "reason": gate_result["reason"],
            "rule_engine_recommended_action": (
                "buy" if gate_result["passes"] else "no_trade"
            ),
        }
    except Exception as e:
        log_error(date_iso, ticker, "ql_gate_eval_failed",
                  f"{type(e).__name__}: {e}")
        packet["fundamental_gate_38"] = {
            "passes": None,
            "failed_gates": [],
            "reason": f"gate_eval_error:{type(e).__name__}",
            "rule_engine_recommended_action": "needs_more_evidence",
        }

    try:
        out = run_all_agents_for_candidate(
            evidence_packet=packet,
            candidate_type="quality_long",
            provider=provider,
            cache=cache,
            agent_mode=_ABLATION_AGENT_MODE,
            topology=_ABLATION_TOPOLOGY,
        )
    except Exception as e:
        log_error(date_iso, ticker, "ql_agent_run_failed",
                  f"{type(e).__name__}: {e}")
        return None

    if out.get("pm_error"):
        _bump_fail("json_parse_fails")
        _check_abort_thresholds("PM JSON parse fail")

    # §5.17 + §13.6 + §3.8 surge-short veto-scope filter (2026-05-04).
    # No-op for quality_long (filter early-returns) — call kept for
    # symmetry / audit clarity.
    risk_out = (out.get("agent_outputs") or {}).get("risk")
    if isinstance(risk_out, dict):
        filter_risk_veto_by_candidate_type(risk_out, "quality_long")

    elapsed = time.time() - t0
    cost_after = ledger_total()
    trigger_cost = cost_after - cost_before
    pm_decision, pm_agent_key = _extract_pm_decision(out)
    trigger_id = uuid.uuid4().hex[:12]

    forensic_path = OUT_ROOT / date_iso / f"QL_{ticker}_{trigger_id}.json"
    forensic_path.parent.mkdir(parents=True, exist_ok=True)
    forensic = {
        "trigger_id": trigger_id,
        "ticker": ticker,
        "candidate_type": "quality_long",
        "decision_timestamp": decision_ts_iso,
        "agent_reasoning_pit_clean": True,
        "elapsed_s": round(elapsed, 2),
        "trigger_cost_usd": round(trigger_cost, 6),
        "cumulative_cost_usd": round(cost_after, 6),
        "candidate_ranking_inputs": candidate,
        "evidence_packet_hash": packet.get("envelope", {}).get(
            "evidence_packet_hash"),
        "locked_decision_id": packet.get("envelope", {}).get(
            "locked_decision_id"),
        "pm_agent_key": pm_agent_key,
        "pm_decision_extract": pm_decision,
        # Pass 6 robustness 2026-05-03: see surge_short forensic above.
        "pm_error": out.get("pm_error"),
        # Tolerant iteration over 7-key shape (Chunk B prelude split).
        "agent_outputs_summary": {
            k: {"validation_status": (v.get("validation_status") if isinstance(v, dict) else None)}
            for k, v in (out.get("agent_outputs", {}) or {}).items()
        },
        "agent_outputs_full": out.get("agent_outputs", {}),
    }
    forensic_path.write_text(json.dumps(forensic, indent=2, default=str))

    return {
        "ticker": ticker,
        "trigger_id": trigger_id,
        "candidate_type": "quality_long",
        "decision_timestamp": decision_ts_iso,
        "trigger_cost_usd": round(trigger_cost, 6),
        "elapsed_s": round(elapsed, 2),
        "pm_agent_key": pm_agent_key,
        "pm_decision": pm_decision,
        "candidate": candidate,
        "forensic_path": str(forensic_path.relative_to(ROOT)),
    }


def load_quality_long_universe(decision_date: str | None = None) -> list[str]:
    """Load SP500 ∪ NDX100 unique tickers AS OF decision_date.

    B7 fix (2026-05-06): use src.data_adapters.fmp_adapter
    .historical_universe_as_of for PIT correctness per §3.7. Reverse-replays
    today's snapshot through the FMP constituent change log to reconstruct
    the index as it stood at decision_date. Falls back to TODAY's
    constituent endpoint (the prior survivorship-biased behavior) if the
    historical reconstruction raises (e.g., no cache file yet and live
    fetch failure). Cache is keyed by decision_date so each Monday scan
    can request a different as-of without re-fetching FMP.
    """
    global _QL_UNIVERSE_CACHE
    cache_key = ("__historical__", decision_date) if decision_date else "__today__"
    if isinstance(_QL_UNIVERSE_CACHE, dict) and cache_key in _QL_UNIVERSE_CACHE:
        return _QL_UNIVERSE_CACHE[cache_key]
    if not isinstance(_QL_UNIVERSE_CACHE, dict):
        # First call — convert legacy single-list cache to dict shape.
        _QL_UNIVERSE_CACHE = {}

    universe: set[str] = set()
    if decision_date is not None:
        try:
            from src.data_adapters.fmp_adapter import historical_universe_as_of
            universe = historical_universe_as_of(
                decision_date, index_set="union",
            )
            print(f"  [ql_universe] PIT as-of {decision_date}: "
                  f"{len(universe)} tickers (sp500 ∪ ndx100, reconstructed "
                  f"from FMP change log)", flush=True)
        except Exception as e:
            log_error("ql_universe", "historical_universe_as_of",
                      "pit_reconstruction_failed",
                      f"{type(e).__name__}: {e} — falling back to today's snapshot")
            universe = set()

    if not universe:
        # Fallback path or decision_date=None — today's constituents.
        from src.data_adapters.fmp_adapter import _api_call
        for endpoint, group in (
            ("sp500-constituent", "ql_universe_sp500"),
            ("nasdaq-constituent", "ql_universe_ndx"),
        ):
            data, meta = _api_call(endpoint, {}, group=group)
            if not meta.get("ok") or not isinstance(data, list):
                log_error("ql_universe", endpoint, "constituent_fetch_failed",
                          f"http={meta.get('http_status')}")
                continue
            for row in data:
                if not isinstance(row, dict):
                    continue
                sym = row.get("symbol")
                if isinstance(sym, str) and sym:
                    universe.add(sym.strip().upper())
        print(f"  [ql_universe] today's snapshot fallback: "
              f"{len(universe)} tickers", flush=True)

    # §27.10 hard exclusion: ETF FI universe (BIL/SHY/IEF/TLT/IEI/SHV)
    # never appears in QL universe.
    universe -= EXCLUDED_FI_ETFS
    out = sorted(universe)
    _QL_UNIVERSE_CACHE[cache_key] = out
    return out


def ql_weekly_scan(
    date_iso: str, universe: list[str],
) -> tuple[list[dict], int]:
    """Scan SP500∪NDX100 for tickers with earnings in
    [date_iso, date_iso + QL_EARNINGS_LOOKAHEAD_DAYS]. Return
    (top-5 candidates by earliest event date, total candidates with event).

    Inspects BOTH `earnings.upcoming` AND `earnings.past` from
    get_corporate_calendar — the audit found that an event scheduled
    for date X may live in `earnings.past` if today > X but X is still
    in the forward window from `date_iso` (e.g. AAPL 2026-04-30
    queried on 2026-05-02 is `past` at fetch time but inside the
    [2026-04-27, +14d] window).
    """
    from src.data_adapters.fmp_adapter import get_corporate_calendar
    from datetime import timedelta as _td
    anchor = date.fromisoformat(date_iso)
    window_end = anchor + _td(days=QL_EARNINGS_LOOKAHEAD_DAYS)
    candidates: list[dict] = []

    for i, sym in enumerate(universe):
        if i % 50 == 0 and i > 0:
            print(f"  [ql_scan] progress {i}/{len(universe)}; "
                  f"candidates_so_far={len(candidates)}", flush=True)
        try:
            cal = get_corporate_calendar(sym)
        except Exception as e:
            _QL_CALENDAR_CACHE_STATS["errors"] += 1
            log_error(date_iso, sym, "ql_calendar_fetch_failed",
                      f"{type(e).__name__}: {e}")
            continue
        _QL_CALENDAR_CACHE_STATS["calls_made"] += 1
        if cal.get("served_from_cache"):
            _QL_CALENDAR_CACHE_STATS["warm_hits"] += 1
        else:
            _QL_CALENDAR_CACHE_STATS["cold_misses"] += 1
        if cal.get("status") != "available":
            continue
        earnings = cal.get("earnings") or {}
        rows = list(earnings.get("upcoming") or []) + list(earnings.get("past") or [])
        earliest_in_window: date | None = None
        for r in rows:
            if not isinstance(r, dict):
                continue
            ds = r.get("date")
            if not isinstance(ds, str):
                continue
            try:
                ed = date.fromisoformat(ds[:10])
            except ValueError:
                continue
            if anchor <= ed <= window_end:
                if earliest_in_window is None or ed < earliest_in_window:
                    earliest_in_window = ed
        if earliest_in_window is not None:
            candidates.append({
                "ticker": sym,
                "earliest_event_date": earliest_in_window.isoformat(),
                "selection_reason": "earnings_window_14d",
            })

    candidates.sort(key=lambda c: c["earliest_event_date"])
    print(f"  [ql_scan] scan complete: {len(candidates)} tickers with "
          f"event in [{anchor.isoformat()}, {window_end.isoformat()}]; "
          f"top {MAX_QL_PER_WEEK} = "
          f"{[c['ticker'] for c in candidates[:MAX_QL_PER_WEEK]]}",
          flush=True)

    # Bug F fix (2026-05-04): populate `close` field on the top-N
    # candidates so update_position_from_trigger can read entry_price
    # the same way it does for surge_short.
    # B9 fix (2026-05-06): also fetch T+1 next-trading-day OPEN per §6.1
    # so QL position open executes at next-day open, not same-day close.
    # The DECISION timestamp stays at day_iso (PIT-correct); the EXECUTION
    # price is T+1 09:30 ET open. Lookup bounded to top-N (5) so we make
    # at most 5 FMP calls per week per metric.
    from src.data_adapters.fmp_adapter import get_historical_daily
    _next_td = next_trading_day(date.fromisoformat(date_iso))
    _next_iso = _next_td.isoformat()
    enriched: list[dict] = []
    for c in candidates[:MAX_QL_PER_WEEK]:
        sym = c["ticker"]
        try:
            rows = get_historical_daily(
                sym, start_date=date_iso, end_date=_next_iso,
            )
        except Exception as e:
            log_error(date_iso, sym, "ql_close_fetch_failed",
                      f"{type(e).__name__}: {e}")
            continue
        row = next(
            (r for r in (rows or []) if r.get("date") == date_iso), None,
        )
        next_row = next(
            (r for r in (rows or []) if r.get("date") == _next_iso), None,
        )
        close_v = None
        if row is not None:
            try:
                close_v = float(row.get("close") or 0.0)
            except (TypeError, ValueError):
                close_v = None
        if close_v is None or close_v <= 0:
            log_error(date_iso, sym, "ql_close_unavailable",
                      f"no usable close for {sym} on {date_iso}")
            continue
        c["close"] = close_v
        # T+1 next-trading-day open per §6.1. Falls back to None if next
        # day's row is unavailable (rare; happens at end of dataset).
        next_open_v = None
        if next_row is not None:
            try:
                next_open_v = float(next_row.get("open") or 0.0)
            except (TypeError, ValueError):
                next_open_v = None
        c["t1_open"] = next_open_v if next_open_v and next_open_v > 0 else None
        c["t1_date"] = _next_iso if c["t1_open"] is not None else None
        enriched.append(c)
    return enriched, len(candidates)


def derive_side_from_pm(pm_output: dict) -> str | None:
    """Translate pm_agent decision to position side.

    Returns "long" / "short" / None.
    Explicit side field takes priority if set; otherwise derive from decision
    field. Pass 7.2 fix: prior code read pm.get("side") directly, but the
    PMAgentOutput schema does not require a side field — agent emits
    decision="buy" or decision="short" with position_size_pct, leaving
    side=None. Without this translation, CINF buy + XTLB short in Pass 7.1
    replay opened 0 positions despite valid agent decisions.
    """
    explicit = pm_output.get("side")
    if explicit in ("long", "short"):
        return explicit
    decision = (pm_output.get("decision") or pm_output.get("recommended_action") or "").lower()
    if decision == "buy":
        return "long"
    if decision == "short":
        return "short"
    return None


def normalize_size_pct_to_fraction(raw_value):
    """Normalize position_size_pct to fraction convention.

    Agents emit different units for the same field:
      - surge_short_agent: 0.005 (fraction representation
        of 0.5%)
      - quality_long_agent: 3.5 (percent representation of
        3.5%)

    Detection: values < 1.0 are treated as fractions,
    values >= 1.0 as percents. This is unambiguous because
    surge_short is fixed at 0.005 (per §4.8) and
    quality_long min sensible size is at least 1% of NAV.

    Returns: float in fraction convention (0.005 = 0.5%,
    0.035 = 3.5%).
    """
    if raw_value is None:
        return 0.0
    if raw_value < 1.0:
        return float(raw_value)  # already fraction
    return float(raw_value) / 100.0  # percent → fraction


def update_position_from_trigger(trig: dict) -> dict | None:
    pm = trig.get("pm_decision") or {}
    side = derive_side_from_pm(pm) or "none"
    size_pct = pm.get("position_size_pct")
    if size_pct is None:
        size_pct = pm.get("size_pct", 0.0) or 0.0
    try:
        size_pct = float(size_pct)
    except (TypeError, ValueError):
        size_pct = 0.0
    side = str(side).lower()
    if side not in ("short", "long") or size_pct <= 0:
        return None
    # §4.8 + docs/SURGE_SHORT_THRESHOLDS.md (active 2026-05-03):
    # defensive runtime clamp — for NEW surge_short entries the size
    # is FIXED at 0.005 (0.5%). Override any agent emit that drifted.
    # PM v0.7+ enforces this in-prompt as well; this is double
    # enforcement so a regressed prompt cannot blow the rule.
    # Bug E fix (2026-05-03): normalize to fraction convention BEFORE
    # clamping so agents emitting in either convention reach the same
    # clamp target. Downstream code operates on fractions exclusively.
    cand_type = trig.get("candidate_type") or "surge_short"
    is_new_entry = side == "short" and cand_type == "surge_short"
    size_pct_frac = normalize_size_pct_to_fraction(size_pct)
    if is_new_entry and abs(size_pct_frac - 0.005) > 1e-6:
        log_error(
            trig.get("decision_timestamp", "")[:10],
            trig.get("candidate", {}).get("ticker", "?"),
            "size_pct_clamped_to_0.005",
            f"emitted {size_pct:.4f} (fraction {size_pct_frac:.4f}) -> 0.005 per §4.8",
        )
        size_pct_frac = 0.005
    # B8 fix (2026-05-06): §3.2 per-position 5% cap + §3.6 sleeve cap for
    # quality_long entries. Surge-short already clamps at 0.005 above; QL
    # had no runtime enforcement and relied on PM self-discipline. Clamp
    # at 0.05 (5%) per §3.2; if the existing quality_long sleeve plus the
    # new entry would breach §4.7 regime equity cap less the surge-short
    # sleeve, downsize defensively. Conservative regime cap of 40% used
    # when no live macro_regime block is wired into this call site.
    if cand_type == "quality_long" and side == "long":
        if size_pct_frac > 0.05:
            log_error(
                trig.get("decision_timestamp", "")[:10],
                trig.get("candidate", {}).get("ticker", "?"),
                "ql_size_pct_clamped_to_0.05",
                f"emitted {size_pct:.4f} (frac {size_pct_frac:.4f}) "
                f"-> 0.05 per §3.2",
            )
            size_pct_frac = 0.05
        # §3.6 sleeve cap: equity_max - surge_short_sleeve_pct.
        # equity_max default 0.40 (Normal regime). Sum existing
        # quality_long sleeve fraction relative to NAV; if new entry
        # would push the sum over equity_max - surge_pct, downsize.
        try:
            _nav_now, _, _ = compute_nav(trig["decision_timestamp"][:10])
            _ql_existing = sum(
                float(p.get("size_pct_at_open", 0.0) or 0.0)
                for p in state.get("positions", [])
                if p.get("side") == "long"
                and p.get("sleeve", "quality_long") == "quality_long"
                and p.get("status", "open") == "open"
            )
            _ss_pct = sum(
                float(p.get("size_pct_at_open", 0.0) or 0.0)
                for p in state.get("positions", [])
                if p.get("side") == "short"
                and p.get("status", "open") == "open"
            )
            EQUITY_MAX_DEFAULT = 0.40
            sleeve_room = max(
                0.0, EQUITY_MAX_DEFAULT - _ss_pct - _ql_existing
            )
            if size_pct_frac > sleeve_room:
                log_error(
                    trig.get("decision_timestamp", "")[:10],
                    trig.get("candidate", {}).get("ticker", "?"),
                    "ql_size_pct_sleeve_clamped",
                    f"emitted {size_pct_frac:.4f} -> "
                    f"{sleeve_room:.4f} per §3.6 (equity_max=0.40 - "
                    f"ss={_ss_pct:.4f} - ql_existing={_ql_existing:.4f})",
                )
                size_pct_frac = sleeve_room
                if size_pct_frac <= 0:
                    return None  # no room
        except Exception as e:
            log_error(
                trig.get("decision_timestamp", "")[:10],
                trig.get("candidate", {}).get("ticker", "?"),
                "ql_sleeve_cap_eval_failed",
                f"{type(e).__name__}: {e}",
            )
    size_pct = size_pct_frac  # downstream uses fraction convention now
    nav_now, _, _ = compute_nav(trig["decision_timestamp"][:10])
    notional = nav_now * size_pct
    # B9 fix (2026-05-06): QL entries use T+1 next-trading-day OPEN per §6.1.
    # Surge-short retains same-day-close semantics (decision is at the 09:30
    # surge anchor; execution within the opening window). For quality_long,
    # the Monday 09:30 ET decision triggers a Tuesday 09:30 ET open execution
    # (or next trading day if a holiday intervenes), modeling the realistic
    # execution lag. Falls back to same-day close when t1_open is unavailable
    # (rare end-of-dataset case).
    cand_obj = trig["candidate"]
    entry_date_str = trig["decision_timestamp"][:10]
    if cand_type == "quality_long" and cand_obj.get("t1_open"):
        entry_price = float(cand_obj["t1_open"])
        entry_date_str = cand_obj.get("t1_date") or entry_date_str
    else:
        entry_price = float(cand_obj["close"])
    if entry_price <= 0:
        return None
    shares = round(notional / entry_price, 2)
    pos = {
        "ticker": cand_obj["ticker"],
        "side": side,
        "entry_price": entry_price,
        "shares": shares,
        "entry_date": entry_date_str,
        "decision_id": trig["trigger_id"],
        "size_pct_at_open": size_pct,
        "notional_at_open": round(notional, 2),
    }
    state["positions"].append(pos)
    if side == "short":
        state["cash"] += shares * entry_price
    else:
        state["cash"] -= shares * entry_price
    # §5.16 v2.15 — flat 15 bps tx cost on entry (long buy / short sell;
    # also used for pyramid adds and profit-reinvest buys, which all route
    # through update_position_from_trigger)
    entry_tx = apply_tx_cost(shares * entry_price)
    state["cash"] -= entry_tx
    state["tx_cost_today"] = float(state.get("tx_cost_today", 0.0)) + entry_tx
    state["tx_cost_cumulative"] = float(state.get("tx_cost_cumulative", 0.0)) + entry_tx
    pos["tx_cost_entry_usd"] = entry_tx
    return pos


def write_eod_state(date_iso: str, decisions: list[str], events: list[dict]) -> Path:
    # §5.16 v2.15 (PART 4 wiring 2026-05-08): charge daily borrow on EVERY
    # open short BEFORE computing NAV so the EOD already reflects the cost.
    # Convention: entry day INCLUSIVE (status not 'closed' at this EOD ⇒ open),
    # cover day EXCLUSIVE (cover_eval sets status='closed' before this loop runs).
    borrow_today = 0.0
    for p in state["positions"]:
        if p.get("kind") == "ust":
            continue
        if p.get("status") == "closed":
            continue
        if p.get("side") != "short":
            continue
        ticker = p.get("ticker"); shares = p.get("shares")
        if not ticker or not shares:
            continue
        cur = _current_price(ticker, date_iso, p["entry_price"])
        mv = abs(float(shares) * float(cur))
        charge = apply_daily_borrow(mv)
        state["cash"] -= charge
        borrow_today += charge
        p["borrow_accrual_usd"] = float(p.get("borrow_accrual_usd", 0.0)) + charge
    state["borrow_cost_today"] = borrow_today
    state["borrow_cost_cumulative"] = float(
        state.get("borrow_cost_cumulative", 0.0)) + borrow_today

    nav, cash, pos_value = compute_nav(date_iso)
    sleeve = {"quality_long": 0.0, "surge_short": 0.0, "fixed_income": 0.0}
    enriched_positions: list[dict] = []
    for p in state["positions"]:
        v = _position_value(p, date_iso)

        # Pass 8 Step B2-mini-E (2026-05-05) — Bug Q: UST positions
        # take a separate enrichment path. They have no `ticker`,
        # `entry_price`, `shares`, or `side`; their identity is
        # `kind="ust"`/`tenor`/`face_value`/`yield_pct`. Routed to the
        # fixed_income sleeve at face value (HTM).
        if p.get("kind") == "ust":
            sleeve["fixed_income"] += float(p.get("face_value", 0.0))
            enriched = dict(p)
            enriched["current_value"] = round(float(p.get("face_value", 0.0)), 2)
            enriched["unrealized_pnl"] = 0.0  # HTM
            enriched["unrealized_pnl_pct"] = 0.0
            enriched["sleeve"] = "fixed_income"
            enriched["size_pct"] = enriched.get("size_pct_at_open", 0.0)
            enriched_positions.append(enriched)
            continue

        # Explicit sleeve tag wins (§27 BIL fix). Otherwise fall back to
        # side-based routing for the equity sleeves.
        sleeve_tag = p.get("sleeve")
        if sleeve_tag in sleeve:
            sleeve[sleeve_tag] += v if p["side"] == "long" else abs(v)
        elif p["side"] == "long":
            sleeve["quality_long"] += v
        else:
            sleeve["surge_short"] += abs(v)

        # Bug I fix (2026-05-04): populate dashboard-visible MTM fields
        # on each position copy. Pre-fix the dashboard rendered "—" for
        # current_price/current_value/unrealized_pnl/pnl_pct because
        # they were never written. Sign convention:
        #   current_price : latest close (or entry as fallback)
        #   current_value : abs notional ( shares × current_price )
        #   unrealized_pnl: profit-positive ( long: (cur-entry)*shares,
        #                   short: (entry-cur)*shares )
        #   unrealized_pnl_pct: pnl / (entry × shares)
        cur = _current_price(p["ticker"], date_iso, p["entry_price"])
        entry = float(p["entry_price"])
        shares = float(p["shares"])
        cost_basis = entry * shares
        if p["side"] == "long":
            unreal = (cur - entry) * shares
        else:
            unreal = (entry - cur) * shares
        pnl_pct = (unreal / cost_basis) if cost_basis > 0 else 0.0
        # Pass 8 Step B2-mini-D (2026-05-05) — persist current_price IN-PLACE
        # on the live state position so cover-eval (which fires on the NEXT
        # day) sees a fresh price. Pre-fix the price was only written into
        # `enriched` (a copy), leaving state["positions"][i].current_price
        # stale/None and breaking §33.4 ladder + R-COVER-08/09 bypass.
        p["current_price"] = round(cur, 4)
        enriched = dict(p)
        enriched["current_price"] = round(cur, 4)
        enriched["current_value"] = round(shares * cur, 2)
        enriched["unrealized_pnl"] = round(unreal, 2)
        enriched["unrealized_pnl_pct"] = round(pnl_pct, 6)
        # Dashboard wiring (2026-05-04): index.html sort keys are
        # `size_pct` and `sleeve`; JS reads p.size_pct (fraction →
        # pfFmtPct × 100) and p.sleeve (string). Position dict stores
        # the entry size under `size_pct_at_open` (fraction post-
        # normalize), and `sleeve` only when explicitly tagged. Alias
        # both so the dashboard columns populate, with `sleeve`
        # derived from side when no explicit tag (mirrors the
        # sleeve_exposure routing above).
        enriched["size_pct"] = enriched.get("size_pct_at_open", 0.0)
        explicit_sleeve = p.get("sleeve")
        if explicit_sleeve in sleeve:
            enriched["sleeve"] = explicit_sleeve
        elif p["side"] == "long":
            enriched["sleeve"] = "quality_long"
        else:
            enriched["sleeve"] = "surge_short"
        enriched_positions.append(enriched)

    eod = {
        "schema_version": "eod_state_v1",
        "as_of": date_iso,
        "rule_version": RULE_VERSION,
        "cash_balance": round(cash, 2),
        "total_nav": round(nav, 2),
        "positions": enriched_positions,
        "sleeve_exposure": {k: round(v, 2) for k, v in sleeve.items()},
        "concentration": {},
        # §5.16 v2.15 (PART 4 wiring 2026-05-08) — friction telemetry
        "tx_cost_today": round(float(state.get("tx_cost_today", 0.0)), 4),
        "tx_cost_cumulative": round(float(state.get("tx_cost_cumulative", 0.0)), 4),
        "borrow_cost_today": round(float(state.get("borrow_cost_today", 0.0)), 4),
        "borrow_cost_cumulative": round(float(state.get("borrow_cost_cumulative", 0.0)), 4),
        # §31 v2 (2026-05-07) — trigger_history schema replaces
        # drawdown_floor_state. trigger_history.events is the forensic log
        # of each trigger fire and force-buy outcome.
        "trigger_history": state.get("trigger_history"),
        "audit": {
            "decisions_processed": decisions,
            "events": events,
            "prior_state_loaded_from": None,
        },
    }
    p = PORT_DIR / f"{date_iso}_eod_state.json"
    p.write_text(json.dumps(eod, indent=2, default=str))
    # §5.16 v2.15 — reset per-day accumulators after EOD write.
    # cumulative counters are NOT reset (they accumulate across days).
    state["tx_cost_today"] = 0.0
    state["borrow_cost_today"] = 0.0
    return p


def append_pnl_row(date_iso: str, prior_nav: float) -> None:
    nav, cash, pos_value = compute_nav(date_iso)
    daily_ret = (nav - prior_nav) / prior_nav if prior_nav else 0.0
    cum_ret = (nav - INITIAL_CAPITAL) / INITIAL_CAPITAL
    csv_path = PORT_DIR / "pnl_history.csv"
    is_new = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write("as_of,total_nav,cash_balance,positions_value,"
                    "num_positions,daily_return,cumulative_return,rule_version\n")
        f.write(
            f"{date_iso},{nav:.2f},{cash:.2f},{pos_value:.2f},"
            f"{len(state['positions'])},{daily_ret:.6f},{cum_ret:.6f},{RULE_VERSION}\n"
        )


def reset_portfolio_state_files() -> None:
    """Wipe pre-existing eod_state files for the window + the pnl_history
    csv so the dashboard sees a clean 5-day timeline. Forensic outputs in
    data/decisions/replay_5day are wiped per fresh run."""
    PORT_DIR.mkdir(parents=True, exist_ok=True)
    for d in WINDOW_DAYS:
        f = PORT_DIR / f"{d}_eod_state.json"
        if f.exists():
            f.unlink()
    pnl = PORT_DIR / "pnl_history.csv"
    if pnl.exists():
        pnl.unlink()
    if OUT_ROOT.exists():
        for sub in OUT_ROOT.glob("*"):
            if sub.is_dir():
                for x in sub.glob("*"):
                    x.unlink(missing_ok=True)
                sub.rmdir()
            else:
                sub.unlink(missing_ok=True)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)


def _assert_backtest_preconditions() -> None:
    """Pass 8 Step B1.6 (2026-05-04) — fail-closed PIT preconditions.

    Asserted at backtest start, before any agent run, to guarantee that
    the four lookahead vectors identified in the B1.5 PIT verification
    report cannot fire. Each precondition has a §-reference so future
    edits know what doctrine they are upholding.

    P1. mode='replay' for every market_screener / get_top_gainers call.
        Any 'live' / None / 'auto' mode in the surge-discovery hot path
        would invoke FMP biggest-gainers (current-day) and inject
        lookahead. Verified by import-time check that the patched
        market_screener module exposes a get_top_gainers signature with
        a `mode` parameter (Pass 8 §32.2 / Step B1.6).

    P2. Packet builder runs in replay mode (not live). hindsight_rules.
        FORBIDDEN_FIELD_PATHS_REPLAY strips isActivelyTrading /
        live_quote / last_price / last_quote_timestamp / current_* fields
        from packets. Verified by importing the packet generator and
        checking that the replay-mode forbidden-fields registry is
        non-empty (per §8.R3 + §12.10).

    P3. decision_date is explicit. The portfolio script passes day_iso
        for every trigger; this is enforced by the patched
        market_screener.get_top_gainers, which raises ValueError if
        decision_date is None in replay mode.

    P4. (post-run, see _assert_no_post_cutoff_violations) no packet in
        the run had data_after_cutoff_used=True. Logged at end-of-window.
    """
    # P1 — surge-ranker exposes mode parameter
    import inspect
    from src.agents import market_screener as _ms_mod
    sig = inspect.signature(_ms_mod.get_top_gainers)
    if "mode" not in sig.parameters:
        raise RuntimeError(
            "Backtest precondition P1 FAILED: market_screener.get_top_gainers "
            "is missing the `mode` parameter. Patched market_screener (Step "
            "B1.6) is not loaded. Refusing to run — would inject lookahead."
        )
    default_mode = sig.parameters["mode"].default
    if default_mode != "replay":
        raise RuntimeError(
            f"Backtest precondition P1 FAILED: market_screener.get_top_gainers "
            f"has mode default='{default_mode}', expected 'replay'. Refusing "
            f"to run — backtest would silently default to live mode."
        )

    # P2 — packet builder replay-mode forbidden-fields registry exists
    try:
        from src.evidence_packet.hindsight_rules import (
            FORBIDDEN_FIELD_PATHS_REPLAY,
        )
    except ImportError as e:
        raise RuntimeError(
            "Backtest precondition P2 FAILED: cannot import "
            "FORBIDDEN_FIELD_PATHS_REPLAY from hindsight_rules. "
            "Replay-mode field redaction is not wired."
        ) from e
    if not FORBIDDEN_FIELD_PATHS_REPLAY:
        raise RuntimeError(
            "Backtest precondition P2 FAILED: FORBIDDEN_FIELD_PATHS_REPLAY "
            "is empty. Replay-mode redaction is a no-op; current-state "
            "fields would leak into packets."
        )
    # Sanity: confirm the canonical four are listed (per §12.10).
    expected = {
        ("profile", "isActivelyTrading"),
        ("price_snapshot", "live_quote"),
        ("price_snapshot", "last_price"),
        ("price_snapshot", "last_quote_timestamp"),
    }
    actual = {tuple(p) for p in FORBIDDEN_FIELD_PATHS_REPLAY}
    missing = expected - actual
    if missing:
        raise RuntimeError(
            f"Backtest precondition P2 FAILED: replay-mode forbidden-fields "
            f"registry is missing canonical paths: {missing}"
        )

    print("[precondition] P1 mode='replay' enforced at "
          "market_screener.get_top_gainers", flush=True)
    print("[precondition] P2 packet replay-mode forbidden-fields "
          f"({len(FORBIDDEN_FIELD_PATHS_REPLAY)} paths) wired", flush=True)
    print("[precondition] P3 decision_date explicit per WINDOW_DAYS", flush=True)
    print("[precondition] Backtest preconditions: mode=replay "
          "packet_mode=replay get_top_gainers_mode=replay "
          "decision_date_explicit=True", flush=True)


def _assert_no_post_cutoff_violations(all_triggers: list[dict]) -> None:
    """Pass 8 Step B1.6 — final-line PIT assertion (P4).

    Scans every trigger / packet generated during the run for
    `data_after_cutoff_used=True`. If ANY packet had data after the
    decision-date cutoff, we log the offenders + raise so the run does
    not silently corrupt the result.
    """
    offenders: list[str] = []
    for trig in all_triggers:
        if not isinstance(trig, dict):
            continue
        # Triggers carry the packet metadata under 'evidence_packet' or
        # 'packet_meta' depending on path — check both shapes.
        for key in ("evidence_packet", "packet_meta", "envelope"):
            sub = trig.get(key)
            if isinstance(sub, dict):
                if sub.get("data_after_cutoff_used") is True:
                    offenders.append(
                        f"{trig.get('trigger_id', '?')}: "
                        f"{trig.get('ticker', '?')} {key}.data_after_cutoff_used=True"
                    )
        if trig.get("data_after_cutoff_used") is True:
            offenders.append(
                f"{trig.get('trigger_id', '?')}: "
                f"{trig.get('ticker', '?')} top-level data_after_cutoff_used=True"
            )
    if offenders:
        for o in offenders:
            print(f"[POST-CUTOFF VIOLATION] {o}", flush=True)
        raise RuntimeError(
            f"Backtest precondition P4 FAILED: {len(offenders)} packet(s) "
            f"used data after their decision-date cutoff. Run result is "
            f"contaminated and must not be reported."
        )
    print(f"[precondition] P4 post-cutoff scan: {len(all_triggers)} triggers "
          f"checked, 0 violations", flush=True)


def main() -> int:
    t_start = time.time()
    # Pass 8 Step B2-mini-A (2026-05-05) — apply CLI overrides before any
    # global is read by downstream code. Defaults preserve the original
    # 2026-04-27..2026-05-01 5-day window when no flags are passed.
    args = _parse_args()
    global WINDOW_DAYS, OUT_ROOT, SUMMARY_PATH, ERRORS_CSV, HARD_COST_USD, PORT_DIR
    if args.start_date and args.end_date:
        from src.utils.trading_calendar import trading_days_between
        WINDOW_DAYS = [d.isoformat()
                       for d in trading_days_between(args.start_date,
                                                     args.end_date)]
        if not WINDOW_DAYS:
            raise RuntimeError(
                f"--start-date/--end-date {args.start_date}..{args.end_date} "
                f"yields zero trading days")
    if args.output:
        out_p = Path(args.output)
        OUT_ROOT = out_p if out_p.is_absolute() else (ROOT / args.output)
        SUMMARY_PATH = OUT_ROOT / "_summary.json"
        ERRORS_CSV = OUT_ROOT / "errors.csv"
    if args.hard_cap is not None:
        HARD_COST_USD = float(args.hard_cap)
        os.environ["ANTHROPIC_HARD_STOP_USD"] = f"{HARD_COST_USD:.2f}"
    # Step B 4-cell ablation: --port-dir overrides PORT_DIR so EOD state
    # JSONs and pnl_history.csv land in a cell-isolated directory.
    if args.port_dir:
        port_p = Path(args.port_dir)
        PORT_DIR = port_p if port_p.is_absolute() else (ROOT / args.port_dir)
    print(f"  ablation cell-id    : {args.cell_id or '(unset)'}")
    print(f"  ablation agent_mode : {_ABLATION_AGENT_MODE}")
    print(f"  ablation topology   : {_ABLATION_TOPOLOGY}")
    print(f"  ablation leave-out  : {list(_ABLATION_LEAVE_OUT) or '(none)'}")
    print(f"  ablation disable_adas: {os.environ.get('ABLATION_DISABLE_ADAS', '0')}")
    print(f"  effective adapters  : {EFFECTIVE_LIVE_ADAPTERS_TUPLE}")
    print(f"  PORT_DIR            : {PORT_DIR}")
    # Pass 8 Step B1.8 (2026-05-04) — fail-closed at the very first line.
    # B2 is replay-only; strict_pit_mode is now implicit from
    # decision_mode='historical_replay' inside generate_evidence_packet.
    assert BACKTEST_MODE == "replay", (
        "B2 backtest must run in replay mode "
        f"(got BACKTEST_MODE={BACKTEST_MODE!r})"
    )
    _assert_backtest_preconditions()
    reset_portfolio_state_files()
    provider = get_provider()
    cache = LLMCache()
    print(f"[start] window={WINDOW_DAYS[0]}..{WINDOW_DAYS[-1]} "
          f"({len(WINDOW_DAYS)} days)  "
          f"hard_cap=${HARD_COST_USD} max_surge/day={MAX_SURGE_PER_DAY}",
          flush=True)
    print(f"[start] cumulative_cost_at_entry=${ledger_total():.4f}",
          flush=True)

    # §27.14 (v0.8.7 UST refactor 2026-05-02): default initial state is
    # 100% cash. The agent decides FI deployment per §27.15 from yield
    # curve + Fed expectations + risk advisory. The §27 BIL bootstrap
    # from v0.8.5 (still defined as `_bootstrap_bil_position` for
    # back-compat with the *.bak files) is SUPERSEDED — DO NOT call it.
    print("[start] §27.14 default initial state: 100% cash (no UST seed). "
          "Agent decides FI deployment per §27.15.", flush=True)

    # §31 v2 (2026-05-07) — trigger_history schema replaces the old
    # drawdown_floor_state. State carries an events list of per-day trigger
    # outcomes (for forensics + audit) and last_eval_date. No
    # cumulative_floor_pct, no re-anchor, no per-week cooldown — every
    # trigger day is independent and fires the 3% NAV mandate via
    # engine_force_buy_spy_etf when PM allocation is short.
    state.setdefault('trigger_history', compute_trigger_history_state())
    # Rolling SPY history list[(date_iso, close)]; populated each trading
    # day. Length grows monotonically over the run; evaluate_section_31_trigger
    # reads only the last 5 entries.
    spy_history_for_floor: list[tuple[str, float]] = []
    # Track this trigger day's PM equity allocations so the engine can
    # measure the gap before invoking SPY ETF fallback. Reset each EOD.
    section_31_today_active: bool = False
    section_31_today_drawdown_pct: float = 0.0

    prior_nav = INITIAL_CAPITAL
    for day_iso in WINDOW_DAYS:
        # §29.3 (2026-05-03 calendar additive): skip non-trading days.
        # The 5-day window happens to be all-trading-days; this guard is
        # for the 16-month rerun that crosses NYSE holidays + weekends.
        today_d = date.fromisoformat(day_iso)
        if not is_trading_day(today_d):
            print(f"[{day_iso}] non-trading day, skipping", flush=True)
            continue
        print(f"\n=== day {day_iso} ===", flush=True)

        # §31 v2 (2026-05-07) — daily SPY-drawdown trigger evaluation.
        # Fetch SPY close from cached polygon_grouped_daily (PIT-safe),
        # append to rolling history, evaluate trigger. v2: no cooldown, no
        # cumulative floor — just a daily yes/no trigger active flag.
        # Force-buy execution happens after PM allocations have been logged
        # for the day (engine measures gap, retries PM once, then SPY ETF
        # fallback fills any remaining gap). T+1 next-open per §6.1.
        try:
            from src.data_adapters.market_data import polygon_grouped_daily
            blob = polygon_grouped_daily(day_iso, adjusted=True)
            spy_row = (blob.get("results") or {}).get("SPY")
            spy_close = float(spy_row["c"]) if spy_row and spy_row.get("c") else None
        except Exception:
            spy_close = None
        section_31_today_active = False
        section_31_today_drawdown_pct = 0.0
        if spy_close is not None and spy_close > 0:
            spy_history_for_floor.append((day_iso, spy_close))
            section_31_today_active, section_31_today_drawdown_pct = (
                evaluate_section_31_trigger(spy_history_for_floor, day_iso)
            )
            if section_31_today_active:
                print(f"  [§31 v2] SPY ${spy_close:.2f} 5d-drawdown="
                      f"{section_31_today_drawdown_pct:.2%} <= -5% → TRIGGER ACTIVE; "
                      f"engine will ensure ≥3% NAV new equity added today (T+1 exec).",
                      flush=True)
            # Persist last_eval_date so daily evaluations are auditable.
            state['trigger_history']['last_eval_date'] = day_iso
        # Expose the trigger flag on state so §3.9 ETF guard and downstream
        # consumers (Friday FI, QL pipeline) can read it without re-evaluating.
        state['section_31_trigger_active_today'] = bool(section_31_today_active)
        state['section_31_drawdown_pct_today'] = float(section_31_today_drawdown_pct)
        per_day[day_iso] = {
            "anchors_scanned": 0, "anchor_fired": None,
            "candidates_passing_screen": 0, "candidates_run": 0,
            "ql_event_check_fired": 0,            # legacy stub counter (kept for back-compat)
            "ql_weekly_scan_run": False,          # Chunk C: only Monday
            "ql_candidates_with_event": 0,        # Chunk C: from corp_calendar scan
            "ql_candidates_processed": 0,         # Chunk C: capped at MAX_QL_PER_WEEK
            "ql_trigger_cost_usd": 0.0,           # Chunk C
            "trigger_cost_usd": 0.0,
            "wall_seconds": 0.0, "nav_eod": 0.0,
        }
        day_t0 = time.time()
        decisions_today: list[str] = []
        events: list[dict] = []

        candidates = rank_surge_candidates(day_iso)
        per_day[day_iso]["candidates_passing_screen"] = len(candidates)
        print(f"  surge-short candidates passing §2.1: {len(candidates)}",
              flush=True)
        for c in candidates[:10]:
            print(f"    {c['ticker']:8s}  +{c['change_pct']:7.2f}%  "
                  f"vol={c['volume']:>12,}  close=${c['close']:.2f}",
                  flush=True)

        if candidates:
            per_day[day_iso]["anchors_scanned"] = 1
            per_day[day_iso]["anchor_fired"] = ANCHOR_ET.strftime("%H:%M")
            anchor_dt = datetime.combine(
                date.fromisoformat(day_iso), ANCHOR_ET, tzinfo=ET)
            decision_ts = anchor_dt.isoformat()
            for cand in candidates[:MAX_SURGE_PER_DAY]:
                cost_check_or_abort(f"{day_iso}/pre-{cand['ticker']}")
                print(f"  [trigger] {cand['ticker']} surge_short  "
                      f"cumulative=${ledger_total():.4f}", flush=True)
                # Pass 6 robustness 2026-05-03: per-ticker try/except so a
                # single failure (timeout, KeyError on malformed trig,
                # downstream raise from update_position_from_trigger) does
                # NOT abort the whole day. cost_check_or_abort stays OUTSIDE
                # so genuine cost-cap aborts still propagate.
                try:
                    trig = run_surge_short(cand, decision_ts, provider, cache, day_iso)
                    if trig is None:
                        continue
                    all_triggers.append(trig)
                    per_day[day_iso]["candidates_run"] += 1
                    per_day[day_iso]["trigger_cost_usd"] += trig["trigger_cost_usd"]
                    decisions_today.append(trig["trigger_id"])
                    pos = update_position_from_trigger(trig)
                    events.append({
                        "timestamp": decision_ts, "kind": "surge_short_decision",
                        "ticker": cand["ticker"],
                        "trigger_id": trig["trigger_id"],
                        "trigger_cost_usd": trig["trigger_cost_usd"],
                        "pm_side": derive_side_from_pm(trig.get("pm_decision") or {}),
                        "position_opened": bool(pos),
                    })
                    print(f"    -> done in {trig['elapsed_s']}s  "
                          f"cost=${trig['trigger_cost_usd']:.4f}  "
                          f"pos_opened={bool(pos)}", flush=True)
                    _record_pipeline_success(cand['ticker'], day_iso)
                    _reset_consecutive()
                except Exception as e:
                    _bump_fail("agent_exceptions")
                    _FAIL_COUNTER["consecutive_candidate_fails"] += 1
                    _check_abort_thresholds(
                        f"candidate {cand['ticker']} exception")
                    log_error(day_iso, cand['ticker'],
                              "ticker_pipeline_exception",
                              f"{type(e).__name__}: {str(e)[:200]}")
                    print(f"  [{cand['ticker']}] EXCEPTION: "
                          f"{type(e).__name__}: {str(e)[:200]} — "
                          f"skipping ticker, continuing day", flush=True)
                    _record_pipeline_failure(
                        ticker=cand['ticker'],
                        exception_type=type(e).__name__,
                        exception_msg=str(e),
                        day_iso=day_iso,
                    )
                    continue

        # ── Quality-long weekly event-driven scan (Chunk C) ──
        # §29.4 (2026-05-03 calendar additive): fire on the FIRST
        # TRADING DAY of each ISO week, not WINDOW_DAYS[0]. Handles
        # MLK / Presidents / Memorial / Labor Mondays — scan slides to
        # Tuesday automatically. For the 4/27..5/1 window, 4/27 Monday
        # remains the first-trading-day-of-week, so behavior is
        # unchanged for this run.
        # Scans SP500∪NDX100 for tickers with earnings in
        # [day_iso, day_iso + QL_EARNINGS_LOOKAHEAD_DAYS], caps at
        # MAX_QL_PER_WEEK, runs the 7-agent quality_long pipeline.
        try:
            _prev_td = previous_trading_day(today_d)
            _is_first_td_of_week = (
                _prev_td.isocalendar().week != today_d.isocalendar().week
                or _prev_td.isocalendar().year != today_d.isocalendar().year
            )
        except ValueError:
            _is_first_td_of_week = False
        if _is_first_td_of_week:
            print(f"  [ql] weekly scan firing (first day of window)",
                  flush=True)
            per_day[day_iso]["ql_weekly_scan_run"] = True
            ql_universe = load_quality_long_universe(decision_date=day_iso)
            ql_top, ql_total_with_event = ql_weekly_scan(day_iso, ql_universe)
            per_day[day_iso]["ql_candidates_with_event"] = ql_total_with_event
            if ql_top:
                if per_day[day_iso]["anchor_fired"] is None:
                    per_day[day_iso]["anchors_scanned"] = 1
                    per_day[day_iso]["anchor_fired"] = ANCHOR_ET.strftime("%H:%M")
                anchor_dt = datetime.combine(
                    date.fromisoformat(day_iso), ANCHOR_ET, tzinfo=ET)
                ql_decision_ts = anchor_dt.isoformat()
                for ql_cand in ql_top:
                    cost_check_or_abort(
                        f"{day_iso}/ql-pre-{ql_cand['ticker']}")
                    print(f"  [trigger] {ql_cand['ticker']} quality_long  "
                          f"earliest_event={ql_cand['earliest_event_date']}  "
                          f"cumulative=${ledger_total():.4f}", flush=True)
                    # Pass 6 robustness 2026-05-03: per-ticker try/except.
                    # Same pattern as surge-short above. cost_check_or_abort
                    # stays OUTSIDE so cost-cap aborts propagate.
                    try:
                        ql_trig = run_quality_long(
                            ql_cand, ql_decision_ts, provider, cache, day_iso)
                        if ql_trig is None:
                            continue
                        all_triggers.append(ql_trig)
                        per_day[day_iso]["ql_candidates_processed"] += 1
                        per_day[day_iso]["ql_trigger_cost_usd"] += (
                            ql_trig["trigger_cost_usd"])
                        decisions_today.append(ql_trig["trigger_id"])
                        pos = update_position_from_trigger(ql_trig)
                        events.append({
                            "timestamp": ql_decision_ts,
                            "kind": "quality_long_decision",
                            "ticker": ql_cand["ticker"],
                            "trigger_id": ql_trig["trigger_id"],
                            "trigger_cost_usd": ql_trig["trigger_cost_usd"],
                            "pm_side": derive_side_from_pm(ql_trig.get("pm_decision") or {}),
                            "position_opened": bool(pos),
                        })
                        print(f"    -> done in {ql_trig['elapsed_s']}s  "
                              f"cost=${ql_trig['trigger_cost_usd']:.4f}  "
                              f"pos_opened={bool(pos)}", flush=True)
                        _record_pipeline_success(ql_cand['ticker'], day_iso)
                        _reset_consecutive()
                    except Exception as e:
                        _bump_fail("agent_exceptions")
                        _FAIL_COUNTER["consecutive_candidate_fails"] += 1
                        _check_abort_thresholds(
                            f"candidate {ql_cand['ticker']} exception")
                        log_error(day_iso, ql_cand['ticker'],
                                  "ticker_pipeline_exception",
                                  f"{type(e).__name__}: {str(e)[:200]}")
                        print(f"  [{ql_cand['ticker']}] EXCEPTION: "
                              f"{type(e).__name__}: {str(e)[:200]} — "
                              f"skipping ticker, continuing day", flush=True)
                        _record_pipeline_failure(
                            ticker=ql_cand['ticker'],
                            exception_type=type(e).__name__,
                            exception_msg=str(e),
                            day_iso=day_iso,
                        )
                        continue
        else:
            per_day[day_iso]["ql_weekly_scan_run"] = False
        per_day[day_iso]["ql_event_check_fired"] = (
            per_day[day_iso]["ql_candidates_processed"]
        )

        # ── Pass 6 (2026-05-03) review-mode wirings ──
        # All three modules read state["cash_balance"] first then fall
        # back to state["cash"]. Set as_of so realized-pnl history rows
        # carry the correct date.
        state["as_of"] = day_iso
        nav_pre, _, _ = compute_nav(day_iso)
        state["total_nav"] = nav_pre

        # §10.14.R-COVER-07 daily cover evaluation for held shorts.
        # Pass 8 Step B2-mini-D (2026-05-05) — TRIGGER-GATED cadence per
        # §33.4 + R-COVER-10. The LLM only fires when at least one
        # condition holds:
        #   (i)   §33.4 ladder band crossed since last eval
        #         {5%, 15%, 25%, 35%}, bidirectional
        #   (ii)  days_held == 30 (NYSE trading days, §29.1) — sets up
        #         R-COVER-09 mechanical bypass
        #   (iii) profit_pct >= 35% — R-COVER-08 mechanical bypass
        #         (also captured by band-cross at 35%)
        #   (iv)  new corporate_calendar event since last eval —
        #         R-COVER-10 narrative-refresh branch
        # Otherwise the position is HOLD-by-default with $0 cost.
        if is_trading_day(today_d):
            from src.portfolio.cover_eval import (
                run_cover_evaluation, apply_cover_to_state,
                R_COVER_08_PROFIT_THRESHOLD, R_COVER_09_DAYS_THRESHOLD,
            )
            open_short_positions = [
                p for p in state.get("positions", [])
                if p.get("side") == "short"
                and p.get("status", "open") == "open"
            ]
            for short_pos in open_short_positions:
                ticker = short_pos.get("ticker", "?")
                # F4 — refresh current_price BEFORE evaluating triggers.
                fresh_cur = _current_price(
                    ticker, day_iso, short_pos.get("entry_price", 0.0))
                short_pos["current_price"] = round(float(fresh_cur), 4)

                # Trigger evaluation (all $0-cost, pure local).
                profit_pct = compute_unrealized_profit_pct_dict(short_pos)
                entry_iso = str(short_pos.get("entry_date") or "")[:10]
                try:
                    days_held = (
                        max(0, len(trading_days_between(
                            entry_iso, day_iso)) - 1)
                        if entry_iso else 0
                    )
                except Exception:
                    days_held = 0

                # Pass 8 Step B2-mini-E (2026-05-05) — Bug R fix:
                # first-eval init. Pre-fix, `last_eval_profit_pct` was
                # only written inside the trigger-fired branch, so
                # `check_profit_band_crossed` saw `last=None` forever
                # and returned False, leaving the §33.4 band-cross
                # trigger permanently dormant (only R-COVER-08 ≥35% and
                # the 30d-mark / new_event paths could ever initiate
                # a cover-eval). Fix: on the very first pass for a
                # position, set the baseline to today's profit and
                # `continue` — establish the comparison anchor without
                # firing. From day 2 onward, band-cross is reachable.
                if "last_eval_profit_pct" not in short_pos:
                    pp_init_str = (f"{profit_pct:.2%}"
                                   if profit_pct is not None else "n/a")
                    short_pos["last_eval_profit_pct"] = profit_pct
                    short_pos["last_eval_date"] = day_iso
                    print(f"  [cover-init] {ticker} baseline "
                          f"profit={pp_init_str} days_held={days_held}",
                          flush=True)
                    continue

                band_crossed = check_profit_band_crossed(
                    short_pos, profit_pct)
                thirty_day_mark = (days_held == R_COVER_09_DAYS_THRESHOLD)
                profit_ge_35 = (profit_pct is not None
                                and profit_pct >= R_COVER_08_PROFIT_THRESHOLD)
                new_event = check_new_corporate_calendar_event(
                    ticker, short_pos.get("last_eval_date"), day_iso)

                triggers = []
                if band_crossed: triggers.append("band_cross")
                if thirty_day_mark: triggers.append("30d_mark")
                if profit_ge_35: triggers.append("profit_>=35%")
                if new_event: triggers.append("new_event")
                if not triggers:
                    pp_str = (f"{profit_pct:.2%}" if profit_pct is not None
                              else "n/a")
                    print(f"  [cover-skip] {ticker} no trigger "
                          f"(profit={pp_str}, days_held={days_held})",
                          flush=True)
                    # Bug R fix: refresh baseline on skip so next-day
                    # band-cross compares to today's reading, not the
                    # stale value from N days ago. Without this, a
                    # ratchet of small daily moves never crosses any
                    # band even if cumulative drift exceeds 5/15/25%.
                    short_pos["last_eval_date"] = day_iso
                    short_pos["last_eval_profit_pct"] = profit_pct
                    continue

                cost_check_or_abort(f"{day_iso}/cover-{ticker}")
                print(f"  [cover-eval] {ticker} triggers={triggers} "
                      f"cumulative=${ledger_total():.4f}", flush=True)
                try:
                    cover_result = run_cover_evaluation(
                        today=today_d,
                        short_position=short_pos,
                        portfolio_state=state,
                        live_adapters=EFFECTIVE_LIVE_ADAPTERS_TUPLE,
                        rule_version=RULE_VERSION,
                        provider=provider,
                        cache=cache,
                        new_corporate_event=new_event,
                    )
                    _record_pipeline_success(ticker, day_iso)
                except Exception as e:
                    log_error(day_iso, ticker, "cover_eval_failed",
                              f"{type(e).__name__}: {e}")
                    _record_pipeline_failure(
                        ticker=ticker,
                        exception_type=type(e).__name__,
                        exception_msg=str(e),
                        day_iso=day_iso,
                    )
                    continue

                # Track last-eval state on the position so future days
                # can detect band crosses and new events relative to
                # this eval (not relative to entry).
                short_pos["last_eval_date"] = day_iso
                short_pos["last_eval_profit_pct"] = profit_pct

                events.append({
                    "timestamp": day_iso + "T12:30:00-04:00",
                    "kind": "cover_evaluation",
                    "ticker": ticker,
                    "decision": cover_result["decision"],
                    "cost_usd": cover_result.get("cost_usd", 0.0),
                    "triggers": triggers,
                    "llm_bypassed": cover_result.get("llm_bypassed", False),
                    "agent_count": cover_result.get("agent_count", 0),
                    "dimensions_weighed": cover_result.get(
                        "dimensions_weighed", []),
                })
                if cover_result["decision"] == "cover":
                    apply_cover_to_state(state, short_pos, cover_result)
                    print(f"    COVER {ticker}: "
                          f"{(cover_result.get('rationale') or '')[:100]}",
                          flush=True)
                else:
                    print(f"    HOLD {ticker}: "
                          f"{(cover_result.get('rationale') or '')[:100]}",
                          flush=True)

        # B4 fix (2026-05-06): §4.9 profit reinvest trigger.
        # On Friday, compute realized weekly short P&L (sum of realized_pnl
        # from positions closed Mon-Fri this week). Look up §4.10 regime
        # band and emit a reinvestment_decision event with the midpoint
        # to_long / to_cash split. The realized P&L is already in cash
        # (cover_eval credits proceeds to state.cash); the reinvestment
        # decision is a SIZING SIGNAL for the next Monday's QL scan, not
        # a separate cash movement. Per §4.9 quality_long_unrealized is
        # NEVER liquidated for reinvest.
        if today_d.weekday() == 4 and is_trading_day(today_d):
            try:
                from src.portfolio.reinvestment import (
                    midpoint_split_for_regime, REINVESTMENT_BANDS,
                )
                # Sum realized P&L from positions that closed in the past
                # 5 trading days (or since the last Friday, whichever
                # is shorter). state.realized_pnl_history is the source.
                _week_start_iso = (
                    previous_trading_day(
                        previous_trading_day(
                            previous_trading_day(
                                previous_trading_day(today_d)))))
                realized_week_pnl = 0.0
                for row in state.get("realized_pnl_history", []):
                    rd = row.get("close_date") or row.get("trim_date")
                    if not rd:
                        continue
                    try:
                        rdd = date.fromisoformat(str(rd)[:10])
                    except (TypeError, ValueError):
                        continue
                    if _week_start_iso <= rdd <= today_d:
                        if row.get("side") in ("short", None):
                            realized_week_pnl += float(
                                row.get("realized_pnl", 0.0) or 0.0)
                            realized_week_pnl += float(
                                row.get("realized_pnl_partial", 0.0) or 0.0)
                # Persist for FI review packet (B5 surfacing) + next-Monday
                # QL scan reference.
                state["realized_weekly_short_pnl_usd"] = realized_week_pnl
                # Look up regime; fallback to 'normal' if classifier didn't run.
                _regime = None
                try:
                    from src.agents.macro_regime import classify_regime
                    from src.data_adapters.fred_adapter import (
                        get_macro_indicators,
                    )
                    _ind = get_macro_indicators(decision_date=day_iso)
                    _regime_dict = classify_regime(_ind)
                    _regime = (_regime_dict.get("macro_regime") or "normal").lower()
                except Exception:
                    _regime = "normal"
                if _regime not in REINVESTMENT_BANDS:
                    _regime = "normal"
                if realized_week_pnl > 0:
                    to_long_pct, to_cash_pct = midpoint_split_for_regime(_regime)
                    to_long_dollars = realized_week_pnl * (to_long_pct / 100.0)
                    to_cash_dollars = realized_week_pnl * (to_cash_pct / 100.0)
                    # Next Monday execution timestamp.
                    _next_mon = next_trading_day(today_d)
                    state["reinvestment_target"] = {
                        "decision_date": day_iso,
                        "execution_date": _next_mon.isoformat(),
                        "regime": _regime,
                        "realized_weekly_short_pnl_usd": realized_week_pnl,
                        "to_long_pct": to_long_pct,
                        "to_cash_pct": to_cash_pct,
                        "to_long_dollars": round(to_long_dollars, 2),
                        "to_cash_dollars": round(to_cash_dollars, 2),
                    }
                    events.append({
                        "timestamp": day_iso + "T15:30:00-04:00",
                        "kind": "profit_reinvest_decision",
                        "regime": _regime,
                        "realized_weekly_short_pnl_usd": realized_week_pnl,
                        "to_long_pct": to_long_pct,
                        "to_cash_pct": to_cash_pct,
                        "to_long_dollars": round(to_long_dollars, 2),
                        "execution_date": _next_mon.isoformat(),
                    })
                    print(f"  [§4.9 reinvest] realized=${realized_week_pnl:,.2f} "
                          f"regime={_regime} -> to_long={to_long_pct}% "
                          f"(${to_long_dollars:,.2f}) to_cash={to_cash_pct}% "
                          f"exec={_next_mon.isoformat()}", flush=True)
            except Exception as e:
                log_error(day_iso, "__REINVEST__",
                          "profit_reinvest_failed",
                          f"{type(e).__name__}: {e}")

        # §27.16 Friday FI review (UST deployment evaluation)
        if today_d.weekday() == 4 and is_trading_day(today_d):
            from src.portfolio.fi_review import (
                run_fi_review, apply_ust_decision_to_state,
            )
            cost_check_or_abort(f"{day_iso}/Friday FI review")
            print(f"  [Friday FI review] {day_iso} "
                  f"cumulative=${ledger_total():.4f}", flush=True)

            # B6 fix (2026-05-06): classify macro regime at decision time
            # and pass it to FI review. Pre-fix the harness passed
            # macro_regime=None, defaulting the FI packet to
            # {"status":"data_unavailable"} and starving PM of regime
            # context. classify_regime is rule-based (no LLM) and runs
            # against FRED indicators fetched at decision_date.
            try:
                from src.agents.macro_regime import classify_regime
                from src.data_adapters.fred_adapter import (
                    get_macro_indicators,
                )
                _ind = get_macro_indicators(decision_date=day_iso)
                macro_regime_dict = classify_regime(_ind)
            except Exception as e:
                log_error(day_iso, "__MACRO__", "regime_classify_failed",
                          f"{type(e).__name__}: {e}")
                macro_regime_dict = None

            try:
                fi_result = run_fi_review(
                    today=today_d,
                    portfolio_state=state,
                    macro_regime=macro_regime_dict,
                    live_adapters=EFFECTIVE_LIVE_ADAPTERS_TUPLE,
                    rule_version=RULE_VERSION,
                    provider=provider,
                    cache=cache,
                )
                _record_pipeline_success("__FI_REVIEW__", day_iso)
            except Exception as e:
                log_error(day_iso, "__FI_REVIEW__",
                          "fi_review_failed",
                          f"{type(e).__name__}: {e}")
                _record_pipeline_failure(
                    ticker="__FI_REVIEW__",
                    exception_type=type(e).__name__,
                    exception_msg=str(e),
                    day_iso=day_iso,
                )
                fi_result = {"ust_decisions": [], "cost_usd": 0.0,
                             "pm_rationale": "fi_review error"}
            events.append({
                "timestamp": day_iso + "T15:30:00-04:00",
                "kind": "friday_fi_review",
                "decisions_count": len(fi_result.get("ust_decisions", [])),
                "cost_usd": fi_result.get("cost_usd", 0.0),
            })
            for ust_decision in fi_result.get("ust_decisions", []):
                apply_ust_decision_to_state(state, ust_decision)
                print(f"    FI: {ust_decision.get('action')} "
                      f"{ust_decision.get('tenor')} "
                      f"face=${ust_decision.get('face_value', 0):,.0f}",
                      flush=True)

        # §30.2 Friday QL review for held QL positions
        if today_d.weekday() == 4 and is_trading_day(today_d):
            from src.portfolio.ql_review import (
                run_ql_friday_review, apply_ql_action_to_state,
            )
            open_ql_positions = [
                p for p in state.get("positions", [])
                if p.get("side") == "long"
                and p.get("status", "open") == "open"
                and p.get("kind", "equity") == "equity"
                and p.get("sleeve", "quality_long") == "quality_long"
            ]
            for ql_pos in open_ql_positions:
                cost_check_or_abort(
                    f"{day_iso}/ql-review-{ql_pos.get('ticker', '?')}"
                )
                print(f"  [§30.2 QL Friday review] {ql_pos.get('ticker')} "
                      f"cumulative=${ledger_total():.4f}", flush=True)
                try:
                    ql_result = run_ql_friday_review(
                        today=today_d,
                        ql_position=ql_pos,
                        portfolio_state=state,
                        live_adapters=EFFECTIVE_LIVE_ADAPTERS_TUPLE,
                        rule_version=RULE_VERSION,
                        provider=provider,
                        cache=cache,
                    )
                    _record_pipeline_success(
                        ql_pos.get("ticker", "?"), day_iso)
                except Exception as e:
                    log_error(day_iso, ql_pos.get("ticker", "?"),
                              "ql_review_failed",
                              f"{type(e).__name__}: {e}")
                    _record_pipeline_failure(
                        ticker=ql_pos.get("ticker", "?"),
                        exception_type=type(e).__name__,
                        exception_msg=str(e),
                        day_iso=day_iso,
                    )
                    continue
                events.append({
                    "timestamp": day_iso + "T15:30:00-04:00",
                    "kind": "ql_friday_review",
                    "ticker": ql_pos.get("ticker"),
                    "decision": ql_result["decision"],
                    "cost_usd": ql_result.get("cost_usd", 0.0),
                })
                if ql_result["decision"] in ("trim", "exit"):
                    apply_ql_action_to_state(state, ql_pos, ql_result)
                    print(f"    {ql_result['decision'].upper()} "
                          f"{ql_pos.get('ticker')}: "
                          f"{(ql_result.get('rationale') or '')[:100]}",
                          flush=True)

        # §31 v2 force-buy gap fill (2026-05-07). When the daily SPY
        # drawdown trigger is active, ensure at least 3% NAV new equity has
        # been added today; if PM allocations fall short, the engine SPY
        # ETF fallback fills the gap (T+1 next-open per §6.1). The PM
        # retry-with-addendum step is conceptually separate from this
        # mechanical fallback — under stub or single-LLM-call scope today
        # we go straight to fallback when the gap is non-zero. Real-LLM
        # PM retry can be wired later if §31 fires often enough to warrant.
        if section_31_today_active:
            nav_for_31, _, _ = compute_nav(day_iso)
            equity_added_today_dollars = sum(
                float(p.get("notional_at_open", 0.0) or 0.0)
                for p in state.get("positions", [])
                if p.get("side") == "long"
                and p.get("entry_date") == day_iso
                and p.get("status", "open") == "open"
                and p.get("kind", "equity") == "equity"
            )
            gap = section_31_force_buy_gap(nav_for_31, equity_added_today_dollars)
            audit_evt_ref = f"section_31_v2_{day_iso}"
            spy_pos = None
            if gap > 0:
                spy_pos = engine_force_buy_spy_etf(
                    gap_dollars=gap,
                    decision_timestamp_iso=day_iso + "T16:00:00-05:00",
                    nav_now=nav_for_31,
                    state=state,
                    audit_event_ref=audit_evt_ref,
                )
            actual_added_total = equity_added_today_dollars + (
                float(spy_pos.get("notional_at_open", 0.0)) if spy_pos else 0.0
            )
            state["trigger_history"]["events"].append({
                "date": day_iso,
                "drawdown_pct": float(section_31_today_drawdown_pct),
                "equity_added_pct": (
                    actual_added_total / nav_for_31 if nav_for_31 > 0 else 0.0
                ),
                "force_buy_invoked": bool(spy_pos),
                "audit_event_ref": audit_evt_ref,
            })
            events.append({
                "timestamp": day_iso + "T16:00:00-05:00",
                "kind": "section_31_trigger",
                "drawdown_pct": float(section_31_today_drawdown_pct),
                "pm_equity_added_dollars": float(equity_added_today_dollars),
                "force_buy_gap_dollars": float(gap),
                "engine_spy_buy_dollars": (
                    float(spy_pos.get("notional_at_open", 0.0)) if spy_pos else 0.0
                ),
                "engine_spy_t1_date": (
                    spy_pos.get("entry_date") if spy_pos else None
                ),
                "total_equity_added_pct_nav": (
                    actual_added_total / nav_for_31 if nav_for_31 > 0 else 0.0
                ),
                "audit_event_ref": audit_evt_ref,
            })
            if spy_pos:
                print(f"  [§31 v2 force-buy] gap=${gap:,.2f} "
                      f"-> SPY ETF face=${spy_pos.get('notional_at_open',0):,.2f} "
                      f"@ T+1 open ({spy_pos.get('entry_date')})", flush=True)

        # §27.17 dividend accrual + §27.18 ETF expense ratio drag
        # (2026-05-02 v0.8.7 UST refactor). Run BEFORE write_eod_state
        # so the EOD audit captures the cash adjustments + events.
        from src.portfolio.dividends_and_expenses import (
            accrue_dividends_for_today, accrue_etf_expense_drag_for_today,
        )
        div_events = accrue_dividends_for_today(state, day_iso)
        exp_events = accrue_etf_expense_drag_for_today(
            state, day_iso, position_value_fn=_position_value,
        )
        events.extend(div_events)
        events.extend(exp_events)

        _check_abort_thresholds(f"end of {day_iso}")

        eod_path = write_eod_state(day_iso, decisions_today, events)
        append_pnl_row(day_iso, prior_nav)
        nav_now, _, _ = compute_nav(day_iso)
        per_day[day_iso]["nav_eod"] = round(nav_now, 2)
        per_day[day_iso]["wall_seconds"] = round(time.time() - day_t0, 1)
        prior_nav = nav_now
        print(f"  eod nav=${nav_now:,.2f} wall={per_day[day_iso]['wall_seconds']}s "
              f"  state-> {eod_path.name}", flush=True)

    wall_total = time.time() - t_start
    cost_total = ledger_total()
    avg_trig = (cost_total / len(all_triggers)) if all_triggers else 0.0
    summary = {
        "window_start": WINDOW_DAYS[0],
        "window_end": WINDOW_DAYS[-1],
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "rule_version": RULE_VERSION,
        "regression_hash": "sha256:6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095",
        "initial_capital_usd": INITIAL_CAPITAL,
        "final_nav_usd": round(prior_nav, 2),
        "total_return_pct": round(
            100.0 * (prior_nav - INITIAL_CAPITAL) / INITIAL_CAPITAL, 4),
        "total_triggers": len(all_triggers),
        "total_cost_usd": round(cost_total, 4),
        "wall_seconds": round(wall_total, 1),
        "avg_trigger_cost_usd": round(avg_trig, 4),
        "extrapolation_1y_cost_usd_naive": round(cost_total * (252 / 5), 2),
        "spec_divergences": [
            "daily_alt_data_verify_skipped",
            "cover_eval_R-COVER-07_skipped",
            "candidate_selection_lookahead_daily_proxy",
            "surge_short_anchor_fired_once_per_day_at_first_anchor",
        ],
        "ql_calendar_cache_stats": dict(_QL_CALENDAR_CACHE_STATS),
        "per_day": per_day,
        "triggers": all_triggers,
        "final_positions": state["positions"],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n[summary] {SUMMARY_PATH}", flush=True)
    print(f"[done] cost=${cost_total:.4f} triggers={len(all_triggers)} "
          f"wall={wall_total:.1f}s", flush=True)
    # Pass 8 Step B1.6 — final P4 assertion: no packet in this run used
    # data after its decision-date cutoff. Raises if any did.
    _assert_no_post_cutoff_violations(all_triggers)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        print("\n[interrupted]", flush=True)
        rc = 130
    except Exception as e:
        print(f"\n[FATAL] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        rc = 1
    sys.exit(rc)
