"""
valuation_snapshot block — schema §5.

Combines `ratios-ttm`, `key-metrics-ttm`, and `discounted-cash-flow` from
FMP. The TTM block is explicitly NOT PIT-safe (no fiscal anchor), and
`DCF_gap_display_only` is a model-derived display field. Both are flagged
in PIT_safety_flags.
"""
from __future__ import annotations

from datetime import datetime

from data_adapters import fmp_adapter as fmp

from ..schema import BlockKey, BlockStatus, Source


def _api_call_count() -> int:
    return sum(fmp.get_call_group_summary().values())


def _f(v):
    return v if isinstance(v, (int, float)) else None


def build(*, ticker: str, allowed_data_cutoff: datetime) -> dict:
    calls_before = _api_call_count()
    source_list_entries: list[dict] = []
    quality_flags: list[dict] = []
    pit_flags: list[dict] = []
    agent_notes: list[str] = []

    ratios_ttm = fmp.get_ratios_ttm(ticker)
    key_metrics = fmp.get_key_metrics(ticker)
    dcf = fmp.get_dcf_valuation(ticker)
    quote = fmp.get_quote(ticker)  # cached at this point if price block already pulled

    for label, payload, url in (
        ("fmp_ratios_ttm", ratios_ttm, "https://financialmodelingprep.com/stable/ratios-ttm"),
        ("fmp_key_metrics_ttm", key_metrics, "https://financialmodelingprep.com/stable/key-metrics-ttm"),
        ("fmp_discounted_cash_flow", dcf, "https://financialmodelingprep.com/stable/discounted-cash-flow"),
    ):
        source_list_entries.append({
            "label": label,
            "source": payload.get("source", Source.LIVE_FMP_FAILED),
            "url": url,
            "as_of": payload.get("available_as_of"),
            "served_from_cache": payload.get("served_from_cache", False),
        })

    if (ratios_ttm.get("status") != "available"
            and key_metrics.get("status") != "available"):
        block = {
            "status": BlockStatus.DATA_UNAVAILABLE,
            "ticker": ticker,
            "source": Source.LIVE_FMP_FAILED,
            "reason": "ratios_ttm + key_metrics_ttm both unavailable",
            "available_as_of": None,
        }
        quality_flags.append({
            "kind": "valuation_missing",
            "severity": "warn",
            "detail": "FMP ratios_ttm and key_metrics_ttm both failed",
        })
        return {
            "block": block,
            "source_list_entries": source_list_entries,
            "quality_flags": quality_flags,
            "pit_flags": pit_flags,
            "agent_notes": agent_notes,
            "api_calls_made": _api_call_count() - calls_before,
        }

    pe = _f(ratios_ttm.get("pe_ratio"))
    peg = _f(ratios_ttm.get("peg_ratio"))
    p_to_s = _f(ratios_ttm.get("price_to_sales"))
    p_to_b = _f(ratios_ttm.get("price_to_book"))
    fcf_yield = _f(ratios_ttm.get("fcf_yield"))
    ev_ebitda = _f(key_metrics.get("ev_to_ebitda"))
    ev_ebit = _f(key_metrics.get("ev_to_ebit"))
    roe = _f(key_metrics.get("roe"))
    roa = _f(key_metrics.get("roa"))
    debt_to_equity_ttm = _f(ratios_ttm.get("debt_to_equity"))
    current_ratio_ttm = _f(ratios_ttm.get("current_ratio"))
    gross_margin_pct_ttm = _f(ratios_ttm.get("gross_margin_pct"))
    net_margin_pct_ttm = _f(ratios_ttm.get("net_margin_pct"))

    # DCF display-only gap
    dcf_value = _f(dcf.get("dcf"))
    dcf_price = _f(dcf.get("current_price")) or _f(quote.get("price"))
    dcf_gap = None
    if isinstance(dcf_value, (int, float)) and isinstance(dcf_price, (int, float)) and dcf_price:
        dcf_gap = round((dcf_value - dcf_price) / dcf_price, 4)

    # The TTM payload is a current snapshot — no fiscal anchor. We clamp
    # the block's top-level `as_of` to the cutoff so the lookahead checker
    # treats the block as "current as of decision_timestamp". The actual
    # FMP fetch wall-clock is preserved in source_list.
    clamped_as_of = allowed_data_cutoff.isoformat()
    block = {
        "status": BlockStatus.OK,
        "ticker": ticker,
        "source": Source.LIVE_FMP,
        "as_of": clamped_as_of,
        "available_as_of": clamped_as_of,
        # R3 / R5 anti-hindsight tag — TTM ratios + DCF are current-state.
        # See docs/HINDSIGHT_POLICY.md.
        "uses_current_state": True,
        "uses_current_state_reason": (
            "TTM ratios + DCF are current-state model outputs; "
            "replay must source these from PIT-stamped quarterly statements instead"
        ),

        "valuation_inputs": {
            "pe_ratio": pe,
            "peg_ratio": peg,
            "price_to_sales": p_to_s,
            "price_to_book": p_to_b,
            "fcf_yield": fcf_yield,
            "ev_to_ebitda": ev_ebitda,
            "ev_to_ebit": ev_ebit,
            "roe": roe,
            "roa": roa,
            "debt_to_equity_ttm": debt_to_equity_ttm,
            "current_ratio_ttm_pit": current_ratio_ttm,
            "gross_margin_pct_ttm": gross_margin_pct_ttm,
            "net_margin_pct_ttm": net_margin_pct_ttm,
        },
        "current_snapshot_not_PIT_safe": True,
        "DCF_value_display_only": dcf_value,
        "DCF_current_price_used": dcf_price,
        "DCF_gap_display_only": dcf_gap,
        "DCF_as_of_date": dcf.get("as_of_date"),
        "is_dcf_pit_safe": False,
    }

    pit_flags.append({
        "field": "valuation_snapshot.valuation_inputs",
        "PIT_safe": False,
        "note": "TTM ratios; no fiscal anchor",
    })
    pit_flags.append({
        "field": "valuation_snapshot.DCF_*",
        "PIT_safe": False,
        "note": "FMP DCF is a model-derived display field; not market data",
    })

    quality_flags.append({
        "kind": "ttm_snapshot_used",
        "severity": "info",
        "detail": "ratios_ttm flagged not_PIT_safe — for display, not for PIT inputs",
    })

    if pe is not None:
        agent_notes.append(
            f"Valuation TTM: PE={pe} PB={p_to_b} EV/EBITDA={ev_ebitda} "
            f"DCF_gap={dcf_gap} (display only)"
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
    return BlockKey.VALUATION_SNAPSHOT
