"""
price_snapshot block — schema §2.

v1 fields populated:
  - prior close, last price, day OHLC + volume, last-quote timestamp
  - 5d / 20d / 60d total return + average daily volume + dollar volume
  - relative volume vs 20-day average

Sources: FMP `quote` (live or 5-min cache) + FMP `historical-price-eod/full`
for the trailing-60-day window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from data_adapters import fmp_adapter as fmp

from ..schema import BlockKey, BlockStatus, Source

# America/New_York handles DST automatically. Daily-EOD bar close is
# the 16:15 ET settlement-print convention used by FMP historical-eod
# (matches the legacy hardcode at the top-level as_of derivation, so
# 16:15 ET cutoff produces a byte-identical anchor with daily-bar tie).
ET = ZoneInfo("America/New_York")
_DAILY_BAR_CLOSE_TIME = "16:15:00"


def _api_call_count() -> int:
    return sum(fmp.get_call_group_summary().values())


def _safe_pct_change(end: float | None, start: float | None) -> float | None:
    if end is None or start is None or not isinstance(end, (int, float)) \
            or not isinstance(start, (int, float)) or start == 0:
        return None
    return round((end - start) / start * 100.0, 4)


def _select_close_n_back(rows: list[dict], n: int) -> float | None:
    """Return the close that is `n` trading days before the last row.
    Rows are expected ascending by date (`get_historical_daily` already sorts)."""
    if not rows or n < 0:
        return None
    if n + 1 > len(rows):
        return None
    target_idx = len(rows) - 1 - n
    val = rows[target_idx].get("close")
    return val if isinstance(val, (int, float)) else None


def _parse_intraday_bar_close(date_str: str | None) -> datetime | None:
    """FMP 5-min bar `datetime` strings are naive NY time
    (e.g. "2026-04-30 10:30:00"). Treat as ET-aware close timestamp."""
    if not date_str or not isinstance(date_str, str):
        return None
    s = date_str.strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=ET) if dt.tzinfo is None else dt.astimezone(ET)


def _daily_bar_close(date_str: str | None) -> datetime | None:
    """Daily EOD bar close = <date>T16:15:00 in ET. Settlement-print
    convention. Returns None on malformed input."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        return datetime.fromisoformat(
            f"{date_str}T{_DAILY_BAR_CLOSE_TIME}"
        ).replace(tzinfo=ET)
    except ValueError:
        return None


def _select_pit_anchor(
    intraday_bars: list[dict] | None,
    daily_history: list[dict] | None,
    cutoff: datetime,
) -> tuple[datetime | None, str | None]:
    """Pick the most recent bar whose close ≤ cutoff.

    Selection grid: 5-min intraday ∪ daily-EOD. Daily wins ties at the
    16:15 ET boundary (preserves backward-compat with the legacy 16:15
    ET hardcode for EOD-aligned cutoffs). Returns (close_dt, kind)
    where kind ∈ {"intraday_5min", "daily_eod", None}."""
    cutoff_aware = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
    cutoff_et = cutoff_aware.astimezone(ET)
    # priority: daily=1, intraday=0 → daily wins ties under reverse sort
    candidates: list[tuple[datetime, int, str]] = []
    for bar in intraday_bars or []:
        cdt = _parse_intraday_bar_close(bar.get("datetime"))
        if cdt and cdt <= cutoff_et:
            candidates.append((cdt, 0, "intraday_5min"))
    for row in daily_history or []:
        cdt = _daily_bar_close(row.get("date"))
        if cdt and cdt <= cutoff_et:
            candidates.append((cdt, 1, "daily_eod"))
    if not candidates:
        return None, None
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    cdt, _prio, kind = candidates[0]
    return cdt, kind


def _avg_volume(rows: list[dict], n: int) -> float | None:
    """Average daily volume of the last `n` rows."""
    if not rows or n < 1:
        return None
    sample = rows[-n:]
    vols = [r.get("volume") for r in sample
            if isinstance(r.get("volume"), (int, float))]
    if not vols:
        return None
    return round(sum(vols) / len(vols), 2)


def build(*, ticker: str, allowed_data_cutoff: datetime) -> dict:
    """Build price_snapshot for `ticker`. Returns the BlockResult dict."""
    calls_before = _api_call_count()
    source_list_entries: list[dict] = []
    quality_flags: list[dict] = []
    pit_flags: list[dict] = []
    agent_notes: list[str] = []

    quote = fmp.get_quote(ticker)
    source_list_entries.append({
        "label": "fmp_quote",
        "source": quote.get("source", Source.LIVE_FMP_FAILED),
        "url": "https://financialmodelingprep.com/stable/quote",
        "as_of": quote.get("available_as_of"),
        "served_from_cache": quote.get("served_from_cache", False),
    })

    # Historical window: ~95 calendar days back to be safe with weekends + holidays
    end_date = allowed_data_cutoff.strftime("%Y-%m-%d")
    start_date = (allowed_data_cutoff - timedelta(days=95)).strftime("%Y-%m-%d")
    history = fmp.get_historical_daily(ticker, start_date=start_date, end_date=end_date)
    source_list_entries.append({
        "label": "fmp_historical_eod",
        "source": Source.LIVE_FMP if history else Source.LIVE_FMP_FAILED,
        "url": "https://financialmodelingprep.com/stable/historical-price-eod/full",
        "as_of": history[-1]["available_as_of"] if history else None,
        "rows": len(history),
    })

    # Cutoff filter: drop any historical row whose full bar close
    # timestamp (date + 16:15 ET) is after the cutoff. Comparing only
    # the date string would wrongly include today's daily-EOD bar when
    # the cutoff is intraday-today (close 16:15 ET > cutoff 09:30 ET).
    cutoff_aware = (
        allowed_data_cutoff
        if allowed_data_cutoff.tzinfo
        else allowed_data_cutoff.replace(tzinfo=timezone.utc)
    )
    cutoff_et = cutoff_aware.astimezone(ET)

    # Scope 4 fix 2026-05-06: §32.1 mandatory-short surge_pct needs the
    # OPEN price of the decision-day's regular session, not the prior
    # EOD bar's open. Extract it from raw history BEFORE the cutoff
    # filter drops the bar. PIT-guarded: only expose when cutoff is in
    # [09:30, 16:15) ET on the bar's date — i.e., the 9:30 open has
    # printed AND the bar's 16:15 close has not (so the cutoff filter
    # below still drops the bar from `history`, preserving prior-day
    # semantics for `last_eod_close` / `last_eod_date`).
    trigger_day_open: float | None = None
    cutoff_date_str = cutoff_et.strftime("%Y-%m-%d")
    session_open_et = cutoff_et.replace(hour=9, minute=30, second=0, microsecond=0)
    session_close_et = cutoff_et.replace(hour=16, minute=15, second=0, microsecond=0)
    if session_open_et <= cutoff_et < session_close_et:
        for r in history:
            if r.get("date") == cutoff_date_str:
                _open_v = r.get("open")
                if isinstance(_open_v, (int, float)):
                    trigger_day_open = float(_open_v)
                break

    history = [
        r for r in history
        if (_dbc := _daily_bar_close(r.get("date"))) is not None
        and _dbc <= cutoff_et
    ]

    # 5-min intraday bars for sub-EOD PIT anchoring. Live-only adapter,
    # used only to refine the as_of timestamp (returns / volumes stay
    # EOD-anchored in the existing block fields below).
    intraday = fmp.get_intraday_chart(ticker, interval="5min", max_rows=80)
    intraday_bars = (
        intraday.get("bars", []) if intraday.get("status") == "available" else []
    )
    source_list_entries.append({
        "label": "fmp_intraday_5min",
        "source": intraday.get("source", Source.LIVE_FMP_FAILED),
        "url": "https://financialmodelingprep.com/stable/historical-chart/5min",
        "as_of": intraday.get("available_as_of"),
        "rows": len(intraday_bars),
    })

    # Status branch: nothing usable
    if quote.get("status") != "available" and not history:
        block = {
            "status": BlockStatus.DATA_UNAVAILABLE,
            "ticker": ticker,
            "source": Source.LIVE_FMP_FAILED,
            "reason": "both quote and historical-eod unavailable",
            "available_as_of": None,
        }
        quality_flags.append({
            "kind": "price_snapshot_missing",
            "severity": "critical",
            "detail": "FMP quote + historical EOD both failed for this ticker",
        })
        return {
            "block": block,
            "source_list_entries": source_list_entries,
            "quality_flags": quality_flags,
            "pit_flags": pit_flags,
            "agent_notes": agent_notes,
            "api_calls_made": _api_call_count() - calls_before,
        }

    # Returns
    if history:
        last_close = history[-1].get("close")
        ret_5d = _safe_pct_change(last_close, _select_close_n_back(history, 5))
        ret_20d = _safe_pct_change(last_close, _select_close_n_back(history, 20))
        ret_60d = _safe_pct_change(last_close, _select_close_n_back(history, 60))
        avg_vol_20d = _avg_volume(history, 20)
        avg_vol_60d = _avg_volume(history, 60)
        last_volume = history[-1].get("volume")
        rel_vol = (last_volume / avg_vol_20d) if (
            isinstance(last_volume, (int, float)) and isinstance(avg_vol_20d, (int, float))
            and avg_vol_20d > 0
        ) else None
        prior_close = _select_close_n_back(history, 1)
        last_close_iso = history[-1].get("date")
    else:
        last_close = None
        ret_5d = ret_20d = ret_60d = None
        avg_vol_20d = avg_vol_60d = rel_vol = None
        prior_close = None
        last_close_iso = None
        last_volume = None

    # Quote-derived live snapshot (for intraday or post-close updates)
    last_price = quote.get("price") if quote.get("status") == "available" else None
    quote_as_of = quote.get("available_as_of")

    # Cutoff enforcement on the live quote: if quote.timestamp > cutoff,
    # we still record it but flag it.
    quote_post_cutoff = False
    quote_ts_str = quote.get("timestamp")
    if quote_ts_str:
        try:
            qts = datetime.fromisoformat(quote_ts_str.replace("Z", "+00:00"))
            if qts > allowed_data_cutoff.astimezone(timezone.utc):
                quote_post_cutoff = True
        except ValueError:
            pass

    if quote_post_cutoff:
        quality_flags.append({
            "kind": "quote_after_cutoff",
            "severity": "warn",
            "detail": (f"live quote timestamp {quote_ts_str} is after "
                        f"allowed_data_cutoff; quote retained for display only"),
        })

    # Dollar volume on the most recent EOD bar
    dollar_volume = None
    if isinstance(last_close, (int, float)) and isinstance(last_volume, (int, float)):
        dollar_volume = round(last_close * last_volume, 2)

    # Top-level as_of is the PIT anchor — the most recent bar (5-min
    # intraday or daily EOD) whose close ≤ cutoff. The live quote may
    # post-date the cutoff in live mode; that's a display feature, not
    # a PIT input, and is preserved separately below.
    anchor_close, anchor_kind = _select_pit_anchor(
        intraday_bars, history, allowed_data_cutoff
    )
    if anchor_close is not None:
        pit_as_of = anchor_close.isoformat()
    elif last_close_iso:
        # Defensive fallback: history present but anchor selection
        # returned nothing (shouldn't happen — daily bars seed the
        # candidate pool — but preserves a non-None as_of so downstream
        # consumers don't have to special-case).
        pit_as_of = _daily_bar_close(last_close_iso).isoformat()
    else:
        pit_as_of = None
    block: dict[str, Any] = {
        "status": BlockStatus.OK,
        "ticker": ticker,
        "source": quote.get("source", Source.LIVE_FMP)
            if quote.get("status") == "available" else Source.LIVE_FMP,
        "as_of": pit_as_of,
        "available_as_of": pit_as_of,
        # R3 / R5 anti-hindsight tag — the live_quote sub-payload and any
        # last_price/last_quote_timestamp fields are wall-clock current
        # values. Tagged here so the hindsight auditor accepts them in
        # live mode and refuses them in replay mode (per
        # docs/HINDSIGHT_POLICY.md and src/evidence_packet/hindsight_rules.py).
        "uses_current_state": True,
        "uses_current_state_reason": (
            "live_quote / last_price / last_quote_timestamp are wall-clock "
            "current snapshots; PIT anchor for replay must use last_eod_close"
        ),

        # last quote (may be after cutoff in opening_window / intraday_review)
        "last_price": last_price,
        "last_quote_timestamp": quote_ts_str,
        "last_quote_after_cutoff": quote_post_cutoff,
        "exchange": quote.get("exchange"),

        # last completed EOD
        "last_eod_date": last_close_iso,
        "last_eod_close": last_close,
        "prior_close": prior_close,
        "open": history[-1].get("open") if history else None,
        "trigger_day_open": trigger_day_open,
        "day_high": history[-1].get("high") if history else None,
        "day_low":  history[-1].get("low") if history else None,
        "volume":   last_volume,
        "dollar_volume": dollar_volume,

        # returns
        "return_5d_pct":  ret_5d,
        "return_20d_pct": ret_20d,
        "return_60d_pct": ret_60d,

        # liquidity
        "avg_volume_20d": avg_vol_20d,
        "avg_volume_60d": avg_vol_60d,
        "relative_volume_vs_20d": round(rel_vol, 4) if rel_vol is not None else None,

        # Live quote sub-payload (preserved for downstream agents; non-PIT)
        "live_quote": {
            "price": last_price,
            "change": quote.get("change"),
            "change_pct": quote.get("change_pct"),
            "day_low": quote.get("day_low"),
            "day_high": quote.get("day_high"),
            "year_low": quote.get("year_low"),
            "year_high": quote.get("year_high"),
            "volume": quote.get("volume"),
            "market_cap": quote.get("market_cap"),
            "served_from_cache": quote.get("served_from_cache", False),
        },
    }

    pit_flags.append({
        "field": "price_snapshot.live_quote",
        "PIT_safe": False,
        "note": "current snapshot, not point-in-time",
    })
    pit_flags.append({
        "field": "price_snapshot.last_eod_*",
        "PIT_safe": True,
        "note": "anchored to last completed trading-day close",
    })

    if last_close is None:
        agent_notes.append(f"price_snapshot.last_eod_close unavailable for {ticker}")
    if quote_post_cutoff:
        agent_notes.append(
            f"live quote at {quote_ts_str} is after allowed_data_cutoff; "
            "use last_eod_close for PIT anchoring"
        )

    return {
        "block": block,
        "source_list_entries": source_list_entries,
        "quality_flags": quality_flags,
        "pit_flags": pit_flags,
        "agent_notes": agent_notes,
        "api_calls_made": _api_call_count() - calls_before,
    }


def block_key() -> str:
    return BlockKey.PRICE_SNAPSHOT
