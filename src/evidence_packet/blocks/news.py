"""
news_event_summary block — schema §6.

v1 scope (per user task spec):
  - Pull last N=20 news items via FMP `news/stock-latest`.
  - Filter to `published_at <= allowed_data_cutoff`.
  - No LLM analysis — raw items + minimal metadata only.
  - Schema-derived fields that require an agent (catalyst_specificity_score,
    vague_narrative_flag, etc.) are emitted as null with status flag.

The FMP adapter does not currently expose a `news` wrapper; we call
`fmp_adapter._api_call("news/stock-latest", ...)` directly. This was
proven working in `docs/DATA_FEASIBILITY_PROBE_RESULT.md` Capability 3.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from data_adapters import fmp_adapter as fmp

from ..schema import BlockKey, BlockStatus, Source

NEWS_LIMIT = 20
ET = ZoneInfo("America/New_York")


def _api_call_count() -> int:
    return sum(fmp.get_call_group_summary().values())


def _parse_published_utc(raw: dict) -> datetime | None:
    """FMP news rows carry `publishedDate` as 'YYYY-MM-DD HH:MM:SS', which
    FMP returns in US/Eastern wall-clock time. Convert to UTC explicitly
    so the cutoff comparison does not depend on the host system timezone.
    """
    s = raw.get("publishedDate")
    if not s:
        return None
    try:
        et_naive = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return et_naive.replace(tzinfo=ET).astimezone(timezone.utc)
    except (TypeError, ValueError):
        # Fallback for already-tz-aware ISO strings
        try:
            d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=ET)
            return d.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None


def build(*, ticker: str, allowed_data_cutoff: datetime) -> dict:
    calls_before = _api_call_count()
    source_list_entries: list[dict] = []
    quality_flags: list[dict] = []
    pit_flags: list[dict] = []
    agent_notes: list[str] = []

    # Use the cached wrapper (Part 4 of Step 3). Warm-cache hits are no-API.
    news_payload = fmp.get_news_latest(ticker, limit=NEWS_LIMIT)
    rows = news_payload.get("items", []) or []
    served_from_cache = news_payload.get("served_from_cache", False)

    source_list_entries.append({
        "label": "fmp_news_stock_latest",
        "source": news_payload.get("source", Source.LIVE_FMP_FAILED),
        "url": "https://financialmodelingprep.com/stable/news/stock-latest",
        "as_of": news_payload.get("available_as_of"),
        "rows_received": len(rows),
        "http_status": news_payload.get("http_status"),
        "error_short": news_payload.get("error_short"),
        "served_from_cache": served_from_cache,
    })

    if news_payload.get("status") != "available":
        block = {
            "status": BlockStatus.DATA_UNAVAILABLE,
            "ticker": ticker,
            "source": Source.LIVE_FMP_FAILED,
            "reason": news_payload.get("error_short")
                       or f"HTTP {news_payload.get('http_status')}",
            "available_as_of": None,
            "items": [],
            "items_count": 0,
            "items_dropped_post_cutoff": 0,
        }
        quality_flags.append({
            "kind": "news_endpoint_failed",
            "severity": "warn",
            "detail": f"FMP news/stock-latest failed for {ticker}",
        })
        return {
            "block": block,
            "source_list_entries": source_list_entries,
            "quality_flags": quality_flags,
            "pit_flags": pit_flags,
            "agent_notes": agent_notes,
            "api_calls_made": _api_call_count() - calls_before,
        }

    cutoff_utc = allowed_data_cutoff.astimezone(timezone.utc)

    kept = []
    dropped_post_cutoff = 0
    for r in rows:
        pub_utc = _parse_published_utc(r)
        if pub_utc is None:
            kept.append({"published_at": r.get("publishedDate"),
                          "title": r.get("title"),
                          "publisher": r.get("publisher"),
                          "site": r.get("site"),
                          "url": r.get("url"),
                          "symbol": r.get("symbol"),
                          "text_excerpt": (r.get("text") or "")[:600],
                          "_cutoff_compare": "skipped_unparseable_date"})
            continue
        if pub_utc > cutoff_utc:
            dropped_post_cutoff += 1
            continue
        kept.append({
            "published_at": r.get("publishedDate"),
            "published_at_utc": pub_utc.isoformat(),
            "title": r.get("title"),
            "publisher": r.get("publisher"),
            "site": r.get("site"),
            "url": r.get("url"),
            "symbol": r.get("symbol"),
            "text_excerpt": (r.get("text") or "")[:600],
        })

    if not kept:
        status = BlockStatus.DATA_UNAVAILABLE
        agent_notes.append(f"No FMP news items <= cutoff for {ticker}")
    else:
        status = BlockStatus.OK
    # Default NDI score per RULES.md §23 — NaN/None when no event cluster
    # information is available. Set to None here; populated by NDI compute
    # pass when integrated.

    block = {
        "status": status,
        "ticker": ticker,
        "source": Source.LIVE_FMP,
        "as_of": kept[0]["published_at"] if kept else None,
        "available_as_of": kept[0]["published_at"] if kept else None,

        "items_count": len(kept),
        "items_dropped_post_cutoff": dropped_post_cutoff,
        "items": kept,

        # NDI (Narrative Divergence Index) — RULES.md §23. Cross-source
        # narrative disagreement on the same event_cluster_id within 24h.
        # Computed downstream by src/altdata/ndi.py; defaults to None
        # until computation lands. NaN or null when <2 sources for cluster.
        "ndi_score": None,

        # Agent-derived fields (schema §6) — left null in v1 (no LLM)
        "trigger_article": None,
        "news_attention_count_1d": None,
        "news_attention_count_3d": None,
        "news_attention_count_7d": None,
        "news_attention_zscore_60d": None,
        "source_diversity_score": None,
        "repeated_press_release_flag": None,
        "event_recency_hours": None,
        "catalyst_type": None,
        "catalyst_specificity_score": None,
        "vague_narrative_flag": None,
        "ai_pivot_flag": None,
        "crypto_pivot_flag": None,
        "financing_flag": None,
        "fda_event_flag": None,
        "earnings_event_flag": None,
        "contract_or_partnership_flag": None,
        "agent_derived_fields_status": BlockStatus.NOT_EVALUATED,
        "agent_derived_fields_reason": "no LLM in v1 — agent will populate downstream",
    }

    pit_flags.append({
        "field": "news_event_summary.items",
        "PIT_safe": True,
        "note": "items filtered to publishedDate <= allowed_data_cutoff",
    })

    if dropped_post_cutoff:
        quality_flags.append({
            "kind": "news_post_cutoff_filtered",
            "severity": "info",
            "detail": f"{dropped_post_cutoff} items dropped because publishedDate > cutoff",
        })
    if status == BlockStatus.OK:
        agent_notes.append(
            f"News: {len(kept)} item(s) kept; latest at {kept[0]['published_at']}"
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
    return BlockKey.NEWS_EVENT_SUMMARY
