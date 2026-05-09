"""
fundamental_snapshot block — schema §4.

PIT discipline (per feasibility probe Capability 4): each statement row
carries `filingDate` AND `acceptedDate`. We use `acceptedDate` (the more
conservative truth-time) as the PIT anchor when comparing against the
allowed_data_cutoff.

Source: FMP `income-statement`, `balance-sheet-statement`, `cash-flow-statement`
(period=quarter, limit=4).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from data_adapters import fmp_adapter as fmp

from ..schema import BlockKey, BlockStatus, Source

DATA_UNAVAILABLE_STR = "Data unavailable"

# FMP `accepted_date` strings are ET-wall-clock (matching SEC EDGAR's
# acceptance timestamps). All cutoff comparisons must therefore use ET.
ET = ZoneInfo("America/New_York")


def _cutoff_et_naive(allowed_data_cutoff: datetime) -> datetime:
    """Convert the allowed-data cutoff to an ET-wall-clock naive datetime so
    it can be compared against `accepted_date` strings from FMP without
    depending on the host machine's local timezone. A naive cutoff is
    assumed to already be ET (caller responsibility)."""
    if allowed_data_cutoff.tzinfo is None:
        return allowed_data_cutoff
    return allowed_data_cutoff.astimezone(ET).replace(tzinfo=None)


def _api_call_count() -> int:
    return sum(fmp.get_call_group_summary().values())


def _parse_accepted(row: dict) -> datetime | None:
    """Parse `accepted_date` (timestamp string) into a datetime."""
    s = row.get("accepted_date")
    if not s or s == DATA_UNAVAILABLE_STR:
        return None
    # Format observed: "YYYY-MM-DD HH:MM:SS"
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(s))
        except (TypeError, ValueError):
            return None


def _first_pit_safe(rows: list[dict], cutoff_naive: datetime) -> dict | None:
    """Return the most recent row whose acceptedDate <= cutoff. Rows are
    typically newest-first from FMP. The filing_date fallback fires ONLY
    when accepted_date is missing — a post-cutoff accepted_date hard-skips
    the row (otherwise FMP wall-clock leaks where accepted_date is post-
    cutoff but filing_date is on the cutoff day would silently slip
    through the date-string fallback)."""
    for row in rows:
        accepted = _parse_accepted(row)
        if accepted is not None:
            if accepted <= cutoff_naive:
                return row
            # accepted_date present but post-cutoff → row is too new; skip
            continue
        # accepted_date missing → fall back to filing_date (date-only)
        fd = row.get("filing_date")
        if isinstance(fd, str) and fd != DATA_UNAVAILABLE_STR \
                and fd <= cutoff_naive.strftime("%Y-%m-%d"):
            return row
    return None


def build(*, ticker: str, allowed_data_cutoff: datetime) -> dict:
    calls_before = _api_call_count()
    source_list_entries: list[dict] = []
    quality_flags: list[dict] = []
    pit_flags: list[dict] = []
    agent_notes: list[str] = []

    # FMP's accepted_date is ET-wall-clock; convert the cutoff to ET first
    # (host-timezone independent) before stripping tzinfo for comparison.
    cutoff_naive = _cutoff_et_naive(allowed_data_cutoff)

    income = fmp.get_income_statement(ticker, period="quarter", limit=4)
    balance = fmp.get_balance_sheet(ticker, period="quarter", limit=4)
    cashflow = fmp.get_cash_flow_statement(ticker, period="quarter", limit=4)

    for label, rows in (
        ("fmp_income_statement", income),
        ("fmp_balance_sheet", balance),
        ("fmp_cash_flow", cashflow),
    ):
        source_list_entries.append({
            "label": label,
            "source": Source.LIVE_FMP if rows else Source.LIVE_FMP_FAILED,
            "url": f"https://financialmodelingprep.com/stable/"
                    f"{label.replace('fmp_', '').replace('_', '-')}",
            "as_of": rows[0].get("accepted_date") if rows else None,
            "rows": len(rows),
        })

    if not income and not balance and not cashflow:
        block = {
            "status": BlockStatus.DATA_UNAVAILABLE,
            "ticker": ticker,
            "source": Source.LIVE_FMP_FAILED,
            "reason": "no statement rows returned by FMP",
            "available_as_of": None,
            "filed_window_used": None,
        }
        quality_flags.append({
            "kind": "fundamentals_missing",
            "severity": "warn",
            "detail": "FMP returned no quarterly income/balance/cashflow rows",
        })
        return {
            "block": block,
            "source_list_entries": source_list_entries,
            "quality_flags": quality_flags,
            "pit_flags": pit_flags,
            "agent_notes": agent_notes,
            "api_calls_made": _api_call_count() - calls_before,
        }

    inc_pit = _first_pit_safe(income, cutoff_naive)
    bal_pit = _first_pit_safe(balance, cutoff_naive)
    cf_pit  = _first_pit_safe(cashflow, cutoff_naive)

    filed_window_used = {
        "income_statement": ({"filing_date": inc_pit.get("filing_date"),
                                "accepted_date": inc_pit.get("accepted_date"),
                                "fiscal_period_end": inc_pit.get("fiscal_period_end"),
                                "period": inc_pit.get("period"),
                                "fiscal_year": inc_pit.get("fiscal_year")}
                              if inc_pit else None),
        "balance_sheet":    ({"filing_date": bal_pit.get("filing_date"),
                                "accepted_date": bal_pit.get("accepted_date"),
                                "fiscal_period_end": bal_pit.get("fiscal_period_end"),
                                "period": bal_pit.get("period"),
                                "fiscal_year": bal_pit.get("fiscal_year")}
                              if bal_pit else None),
        "cash_flow":        ({"filing_date": cf_pit.get("filing_date"),
                                "accepted_date": cf_pit.get("accepted_date"),
                                "fiscal_period_end": cf_pit.get("fiscal_period_end"),
                                "period": cf_pit.get("period"),
                                "fiscal_year": cf_pit.get("fiscal_year")}
                              if cf_pit else None),
    }

    # Compose top-line metrics using PIT-safe rows
    def get(row, key):
        if not row:
            return None
        v = row.get(key)
        return v if isinstance(v, (int, float)) else None

    rev = get(inc_pit, "revenue")
    net_income = get(inc_pit, "net_income")
    operating_income = get(inc_pit, "operating_income")
    ebitda = get(inc_pit, "ebitda")
    eps = get(inc_pit, "eps")
    gross_margin_pct = round(get(inc_pit, "gross_profit") / rev * 100, 2) \
        if rev and get(inc_pit, "gross_profit") is not None and rev != 0 else None
    net_margin_pct = round(net_income / rev * 100, 2) \
        if rev and net_income is not None and rev != 0 else None

    fcf = get(cf_pit, "free_cash_flow")
    op_cf = get(cf_pit, "operating_cash_flow")
    total_assets = get(bal_pit, "total_assets")
    total_liabilities = get(bal_pit, "total_liabilities")
    total_equity = get(bal_pit, "total_equity")
    total_debt = get(bal_pit, "total_debt")

    # Status
    status = BlockStatus.OK
    if not (inc_pit and bal_pit and cf_pit):
        status = BlockStatus.INSUFFICIENT_EVIDENCE
        quality_flags.append({
            "kind": "fundamentals_partial",
            "severity": "info",
            "detail": "one or more of (income, balance, cashflow) had no PIT-safe row",
        })

    block = {
        "status": status,
        "ticker": ticker,
        "source": Source.LIVE_FMP,
        "as_of": (inc_pit.get("accepted_date") if inc_pit else None),
        "available_as_of": (inc_pit.get("accepted_date") if inc_pit else None),
        "filed_window_used": filed_window_used,
        "snapshot_quarter": {
            "revenue": rev,
            "net_income": net_income,
            "operating_income": operating_income,
            "ebitda": ebitda,
            "eps": eps,
            "gross_margin_pct": gross_margin_pct,
            "net_margin_pct": net_margin_pct,
            "operating_cash_flow": op_cf,
            "free_cash_flow": fcf,
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "total_debt": total_debt,
            "is_point_in_time_safe": True,
            "pit_anchor": "accepted_date",
        },
    }

    pit_flags.append({
        "field": "fundamental_snapshot.snapshot_quarter",
        "PIT_safe": True,
        "note": "anchored to accepted_date <= allowed_data_cutoff",
    })
    pit_flags.append({
        "field": "fundamental_snapshot.filed_window_used",
        "PIT_safe": True,
        "note": "explicit filing/accepted timestamps",
    })

    if inc_pit:
        agent_notes.append(
            f"Latest PIT-safe income statement: "
            f"{inc_pit.get('fiscal_year')} {inc_pit.get('period')} "
            f"(accepted {inc_pit.get('accepted_date')}), revenue={rev}"
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
    return BlockKey.FUNDAMENTAL_SNAPSHOT
