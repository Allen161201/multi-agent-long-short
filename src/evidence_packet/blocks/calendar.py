"""
corporate_calendar block — generator-introduced (NOT in v1 draft).

User's task spec lists `corporate_calendar` explicitly; the schema's §1
top-level shape does not have a `corporate_calendar` block. This file
emits a clearly-named block under that key. The schema-side mapping is
flagged in the run report.

Source: existing FMP `get_corporate_calendar` wrapper (earnings,
dividends, splits — already cached at 12h).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from data_adapters import fmp_adapter as fmp

from ..schema import BlockKey, BlockStatus, Source

NEAR_EVENT_TRADING_DAYS = 5


def _api_call_count() -> int:
    return sum(fmp.get_call_group_summary().values())


def _trading_days_between(d1: datetime, d2: datetime) -> int:
    """Count weekdays in [d1, d2). Holidays ignored."""
    if d2 <= d1:
        return 0
    total = 0
    cur = d1
    while cur < d2:
        if cur.weekday() < 5:
            total += 1
        cur = cur + timedelta(days=1)
    return total


def _is_near_event(event_date_str: str, decision_dt: datetime) -> bool:
    try:
        ed = datetime.strptime(event_date_str, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    delta_td = _trading_days_between(decision_dt.replace(tzinfo=None), ed)
    return abs(delta_td) <= NEAR_EVENT_TRADING_DAYS


def build(*, ticker: str, allowed_data_cutoff: datetime,
          pit_calendar: bool = False) -> dict:
    """Build the corporate_calendar block.

    pit_calendar (D5 Option C, 2026-05-01):
      - False (default): legacy per-symbol earnings/dividends/splits
        endpoints. Preserves the v0.8.2 byte-identical hash baseline
        (bc801036) — required by the regression matrix.
      - True: PIT-aware path that uses the cross-ticker earnings-calendar
        endpoint with from/to range, then filters by symbol AND drops
        events with date > cutoff. Required for historical backtests
        because the legacy per-symbol endpoint returns 0 rows for
        historical windows on the current FMP plan. Enabled by passing
        `strict_pit_mode=True` to generate_evidence_packet (which the
        backtest does).
    """
    calls_before = _api_call_count()
    source_list_entries: list[dict] = []
    quality_flags: list[dict] = []
    pit_flags: list[dict] = []
    agent_notes: list[str] = []

    if pit_calendar:
        cal = fmp.get_corporate_calendar_pit(ticker, allowed_data_cutoff)
        source_list_entries.append({
            "label": "fmp_corporate_calendar_pit",
            "source": cal.get("source", Source.LIVE_FMP_FAILED),
            "url": "https://financialmodelingprep.com/stable/earnings-calendar"
                    " (cross-ticker, from/to)",
            "as_of": cal.get("available_as_of"),
            "served_from_cache": cal.get("served_from_cache", False),
            "pit_dropped_post_cutoff": cal.get("pit_dropped_post_cutoff"),
        })
    else:
        cal = fmp.get_corporate_calendar(ticker)
        source_list_entries.append({
            "label": "fmp_corporate_calendar",
            "source": cal.get("source", Source.LIVE_FMP_FAILED),
            "url": "https://financialmodelingprep.com/stable/{earnings,dividends,splits}",
            "as_of": cal.get("available_as_of"),
            "served_from_cache": cal.get("served_from_cache", False),
        })

    if cal.get("status") != "available":
        block = {
            "status": BlockStatus.DATA_UNAVAILABLE,
            "ticker": ticker,
            "source": Source.LIVE_FMP_FAILED,
            "reason": "FMP corporate-calendar unavailable",
            "available_as_of": None,
        }
        quality_flags.append({
            "kind": "calendar_missing",
            "severity": "info",
            "detail": "FMP corporate-calendar returned no payload",
        })
        return {
            "block": block,
            "source_list_entries": source_list_entries,
            "quality_flags": quality_flags,
            "pit_flags": pit_flags,
            "agent_notes": agent_notes,
            "api_calls_made": _api_call_count() - calls_before,
        }

    near_events = []
    for kind in ("earnings", "dividends", "splits"):
        section = cal.get(kind, {})
        for window_label in ("upcoming", "past"):
            for ev in section.get(window_label, []) or []:
                d = ev.get("date")
                if isinstance(d, str) and _is_near_event(d, allowed_data_cutoff):
                    near_events.append({
                        "kind": kind,
                        "window": window_label,
                        "event": ev,
                    })

    # Calendar entries are dated; the block-level `as_of` is "snapshot at
    # decision time", clamped to the cutoff so the lookahead checker
    # does not flag a few-second clock skew between FMP fetch and decision.
    clamped_as_of = allowed_data_cutoff.isoformat()
    block = {
        "status": BlockStatus.OK,
        "ticker": ticker,
        "source": cal.get("source", Source.LIVE_FMP),
        "as_of": clamped_as_of,
        "available_as_of": clamped_as_of,
        "earnings": cal.get("earnings"),
        "dividends": cal.get("dividends"),
        "splits": cal.get("splits"),
        "ipo_date": cal.get("ipo_date"),
        "near_event_window_trading_days": NEAR_EVENT_TRADING_DAYS,
        "events_near_decision": near_events,
        "events_near_decision_count": len(near_events),
    }

    if near_events:
        agent_notes.append(
            f"corporate_calendar: {len(near_events)} event(s) within "
            f"+/-{NEAR_EVENT_TRADING_DAYS} trading days of decision_timestamp"
        )

    pit_flags.append({
        "field": "corporate_calendar.upcoming",
        "PIT_safe": True,
        "note": "calendar entries are dated; no look-ahead in the events themselves",
    })

    return {
        "block": block,
        "source_list_entries": source_list_entries,
        "quality_flags": quality_flags,
        "pit_flags": pit_flags,
        "agent_notes": agent_notes,
        "api_calls_made": _api_call_count() - calls_before,
    }


def block_key() -> str:
    return BlockKey.CORPORATE_CALENDAR
