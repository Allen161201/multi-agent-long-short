"""
Dividend accrual + ETF expense ratio drag (RULES.md §27.17 / §27.18).

Single source of truth for two cash adjustments that the v0.8.6 replay
harness silently dropped:

  - §27.17 dividend accrual on equity / ETF positions: dividends are
    credited to cash on the ex-dividend date as reported by FMP's
    `corporate_calendar.dividends` block. Frequency-agnostic
    (monthly / quarterly / annual all handled uniformly).

  - §27.18 ETF expense ratio drag: equity-sleeve ETF positions accrue
    daily cash drag = NAV × expense_ratio / 365. If FMP profile returns
    no expense_ratio, default to 0 with a data_quality_flag.

Both helpers are pure functions on a `state` dict matching the replay
harness convention (`state["positions"]`, `state["cash"]`). Returned
audit-event dicts can be appended to the EOD events list.

NO LLM calls. NO HTTP retries (the underlying FMP helpers cache).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any


# ── Dividend accrual (§27.17) ─────────────────────────────────────────

def _to_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"unsupported date type: {type(value).__name__}")


def accrue_dividends_for_today(
    state: dict,
    today_iso: str,
    *,
    fmp_get_corporate_calendar=None,
) -> list[dict]:
    """Credit any equity / ETF dividends with ex-date == today to cash.

    For each position in state["positions"] that is NOT a UST (kind !=
    "ust") and NOT an excluded FI ETF, we query FMP's
    `get_corporate_calendar(ticker)` and look for a row in the
    `dividends.past` (or `dividends.upcoming`) list whose `date` field
    equals today_iso. If found, cash += dividend_per_share × shares.

    Args:
        state: replay state dict with state["positions"] (list of dicts
               carrying at least {ticker, shares, side}) and state["cash"].
        today_iso: YYYY-MM-DD ex-date being checked.
        fmp_get_corporate_calendar: callable(ticker) -> dict matching
               `fmp_adapter.get_corporate_calendar`. Pass for testability;
               defaults to the real adapter when None.

    Returns:
        List of audit-event dicts, one per dividend credited:
            {kind: "dividend_credit", ticker, ex_date, dividend_per_share,
             shares, total_credit_usd}
    """
    if fmp_get_corporate_calendar is None:
        from src.data_adapters.fmp_adapter import get_corporate_calendar
        fmp_get_corporate_calendar = get_corporate_calendar

    events: list[dict] = []
    today_str = today_iso[:10]

    for p in state.get("positions", []):
        if not isinstance(p, dict):
            continue
        if p.get("kind") == "ust":
            continue  # USTs accrue interest separately (§27.12)
        # Only LONG equity positions earn dividends. Short positions
        # PAY dividends on ex-date — modelled as a negative event.
        ticker = p.get("ticker")
        shares = float(p.get("shares") or 0.0)
        side = p.get("side")
        if not ticker or shares <= 0:
            continue
        try:
            cal = fmp_get_corporate_calendar(ticker)
        except Exception as e:  # noqa: BLE001
            events.append({
                "kind": "dividend_lookup_failed", "ticker": ticker,
                "ex_date": today_str,
                "error_class": type(e).__name__,
                "error_message": str(e)[:200],
            })
            continue

        divs = (cal.get("dividends") or {}) if isinstance(cal, dict) else {}
        # Combine past + upcoming so a same-day ex-date isn't missed
        # depending on which bucket FMP returned.
        candidates = []
        for bucket in ("past", "upcoming"):
            rows = divs.get(bucket) or []
            if isinstance(rows, list):
                candidates.extend(rows)

        for row in candidates:
            if not isinstance(row, dict):
                continue
            ex_date = (row.get("date") or "")[:10]
            if ex_date != today_str:
                continue
            dps = row.get("dividend")
            if dps is None:
                continue
            try:
                dps_f = float(dps)
            except (TypeError, ValueError):
                continue
            credit = dps_f * shares
            if side == "short":
                # Short positions pay dividends on ex-date — debit cash.
                state["cash"] -= credit
                events.append({
                    "kind": "dividend_debit_short", "ticker": ticker,
                    "ex_date": ex_date, "dividend_per_share": dps_f,
                    "shares": shares, "total_debit_usd": round(credit, 4),
                })
            else:
                state["cash"] += credit
                events.append({
                    "kind": "dividend_credit", "ticker": ticker,
                    "ex_date": ex_date, "dividend_per_share": dps_f,
                    "shares": shares, "total_credit_usd": round(credit, 4),
                })
            break  # one credit per ticker per day; FMP rarely double-lists

    return events


# ── ETF expense ratio drag (§27.18) ───────────────────────────────────

# Default expense ratio when FMP returns no value. §27.18: 0 + data
# quality flag.
_MISSING_EXPENSE_RATIO_DEFAULT = 0.0


def accrue_etf_expense_drag_for_today(
    state: dict,
    today_iso: str,
    *,
    fmp_get_company_profile=None,
    position_value_fn=None,
) -> list[dict]:
    """Apply daily ETF expense ratio drag to cash.

    For each equity position whose FMP profile is_etf=True, debits cash
    by position_NAV × expense_ratio / 365. If FMP returns no
    expense_ratio, defaults to 0 (no debit) AND records a
    `data_quality_flag` event for the EOD audit trail.

    Args:
        state: replay state dict.
        today_iso: YYYY-MM-DD trading day.
        fmp_get_company_profile: callable(ticker) -> dict matching
               `fmp_adapter.get_company_profile`. None defaults to real.
        position_value_fn: callable(position, today_iso) -> USD value.
               None defaults to importing the replay script's
               `_position_value` (when available).

    Returns:
        List of audit-event dicts:
            {kind: "etf_expense_drag", ticker, expense_ratio_annual,
             position_value_usd, daily_drag_usd}
        or  {kind: "etf_expense_drag_data_quality_flag", ticker, reason}
    """
    if fmp_get_company_profile is None:
        from src.data_adapters.fmp_adapter import get_company_profile
        fmp_get_company_profile = get_company_profile

    events: list[dict] = []
    today_str = today_iso[:10]

    for p in state.get("positions", []):
        if not isinstance(p, dict):
            continue
        if p.get("kind") == "ust":
            continue
        ticker = p.get("ticker")
        if not ticker:
            continue
        try:
            prof = fmp_get_company_profile(ticker)
        except Exception as e:  # noqa: BLE001
            events.append({
                "kind": "etf_expense_drag_lookup_failed", "ticker": ticker,
                "as_of": today_str,
                "error_class": type(e).__name__,
                "error_message": str(e)[:200],
            })
            continue

        if not isinstance(prof, dict) or not prof.get("is_etf"):
            continue  # not an ETF; no expense ratio applies

        er = prof.get("expense_ratio")
        if er is None:
            events.append({
                "kind": "etf_expense_drag_data_quality_flag",
                "ticker": ticker, "as_of": today_str,
                "reason": ("FMP profile returned no expense_ratio; "
                           "defaulting to 0 per §27.18"),
                "expense_ratio_annual": _MISSING_EXPENSE_RATIO_DEFAULT,
            })
            continue

        try:
            er_f = float(er)
        except (TypeError, ValueError):
            events.append({
                "kind": "etf_expense_drag_data_quality_flag",
                "ticker": ticker, "as_of": today_str,
                "reason": f"FMP expense_ratio not numeric: {er!r}",
                "expense_ratio_annual": _MISSING_EXPENSE_RATIO_DEFAULT,
            })
            continue

        # Resolve current position value. If no value-fn was provided,
        # fall back to entry_price × shares (no MTM); the replay caller
        # injects its own _position_value to get same-day MTM.
        if position_value_fn is not None:
            pv = float(position_value_fn(p, today_str))
        else:
            pv = float(p.get("shares") or 0.0) * float(
                p.get("entry_price") or 0.0)
        if pv <= 0:
            continue
        drag = pv * er_f / 365.0
        state["cash"] -= drag
        events.append({
            "kind": "etf_expense_drag", "ticker": ticker,
            "as_of": today_str, "expense_ratio_annual": er_f,
            "position_value_usd": round(pv, 2),
            "daily_drag_usd": round(drag, 6),
        })

    return events


__all__ = [
    "accrue_dividends_for_today",
    "accrue_etf_expense_drag_for_today",
]
