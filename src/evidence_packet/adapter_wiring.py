"""
Adapter wiring layer (Step D7).

Plumbs the alt-data + OpenCLI adapters into the evidence packet
without disturbing the byte-identical default behaviour. This module
is ONLY imported and called when the caller passes
`live_adapters` to `generate_evidence_packet(...)`. When that
parameter is None (default), the generator produces output that is
byte-identical to the pre-Step-D packet — guarding the Step A6 30/30
regression hash.

Mapping (per the Step D spec, finalised 2026-04-28 — Reddit excluded):
  wikipedia_pageviews     -> alternative_data_features.attention
  sec_edgar               -> filing_confirmation.filing_index
  github_public           -> alternative_data_features.tech_activity
  opencli/sec_8k_fulltext -> filing_confirmation.full_text
  opencli/github_commit_messages
                          -> alternative_data_features.tech_activity.narrative

The manifest (per R-ALTDATA-04) records every adapter call: source_id,
called, returned_rows, source_flag, latency_ms, error_class.

Block-toggle discipline: if the target block is not in the
`enabled_blocks` set, the adapter is skipped entirely; the manifest
records `called=false` and the block stays null per R-PACKET-04.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from .schema import BlockKey, BlockStatus, Source

logger = logging.getLogger(__name__)


# Default set of adapters to call when live_adapters=True.
# Reddit was scoped initially and excluded 2026-04-28 (registration
# friction + anti-pollution rationale per RULES.md §10.13).
#
# `fmp_sentiment` (Step D8, 2026-04-29) is registered but intentionally
# OMITTED from the default tuple — opt-in via explicit
# live_adapters=("fmp_sentiment", ...) until it has soaked through a
# regression-baseline re-pin. Adding it here would break the byte-
# identical hash and contaminate the "regression matrix" guard.
DEFAULT_ALT_DATA_SOURCES = (
    "wikipedia_pageviews",
    "sec_edgar",
    "github_public",
)
OPTIONAL_ALT_DATA_SOURCES = (
    "fmp_sentiment",
    "sec_13f",
    "sec_form4",
    "sec_def14a",
    "polygon_news",
)
DEFAULT_OPENCLI_USE_CASES = (
    "sec_8k_fulltext",
    "github_commit_messages",
)


def apply_adapters(
    *,
    packet: dict,
    ticker: str,
    decision_timestamp: datetime,
    enabled_blocks: set[str] | None,
    live_adapters: bool | Iterable[str],
    stub_mode: bool | None = None,
) -> dict:
    """Mutate `packet` in place by overlaying adapter rows onto the
    relevant blocks; return the alt_data_manifest dict.

    The manifest sits at packet["alt_data_manifest"] (top-level, NOT
    a canonical block). It is deliberately recorded in the hashable
    region — per R-ALTDATA-04 the manifest is part of the audit trail.

    `enabled_blocks=None` means "all blocks" (the generator default
    when no toggle is requested). The wiring layer respects this.
    """
    # Lazy imports — these modules are heavy if (e.g.) requests is
    # missing, and we don't want to require the dep just to import
    # the generator.
    from src.adapters.alt_data import REGISTRY as ALTDATA_REGISTRY  # noqa
    from src.adapters.opencli.sec_8k_fulltext import SEC8KFulltextAdapter
    from src.adapters.opencli.github_commit_messages import (
        GitHubCommitMessagesAdapter,
    )

    selected = _select_sources(live_adapters)

    manifest: dict[str, Any] = {
        "schema_version": "alt_data_manifest_v1",
        "ticker": ticker,
        "decision_timestamp": decision_timestamp.isoformat(),
        "calls": [],
    }

    # ── 1. alt-data adapters ─────────────────────────────────────
    for source_id in selected["alt_data"]:
        cls = ALTDATA_REGISTRY.get(source_id)
        if cls is None:
            manifest["calls"].append({
                "source_id": source_id,
                "called": False,
                "skip_reason": "not_in_registry",
            })
            continue
        adapter = cls()
        target_block = adapter.block_target
        if not _block_enabled(target_block, enabled_blocks):
            manifest["calls"].append({
                "source_id": source_id,
                "called": False,
                "skip_reason": f"block_{target_block}_disabled",
            })
            continue

        t0 = time.monotonic()
        try:
            result = adapter.fetch(
                ticker=ticker,
                as_of=decision_timestamp,
                decision_timestamp=decision_timestamp,
                stub_mode=stub_mode,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            manifest["calls"].append({
                "source_id": source_id,
                "called": True,
                "returned_rows": len(result.rows),
                "source_flag": result.source_flag,
                "extraction_status": result.extraction_status,
                "error_class": result.error_class,
                "latency_ms": latency_ms,
            })
            _overlay_alt_data_rows(packet, source_id, result)
        except Exception as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "adapter_wiring: %s raised %s; recorded as failed",
                source_id, type(e).__name__,
            )
            manifest["calls"].append({
                "source_id": source_id,
                "called": True,
                "returned_rows": 0,
                "source_flag": f"live_{source_id}_failed",
                "extraction_status": "failed",
                "error_class": type(e).__name__,
                "latency_ms": latency_ms,
            })

    # ── 2. OpenCLI use cases ─────────────────────────────────────
    if "sec_8k_fulltext" in selected["opencli"] \
       and _block_enabled(BlockKey.FILING_CONFIRMATION, enabled_blocks):
        _wire_sec_8k_fulltext(
            adapter=SEC8KFulltextAdapter(),
            packet=packet, ticker=ticker,
            decision_timestamp=decision_timestamp,
            manifest=manifest, stub_mode=stub_mode,
        )
    else:
        manifest["calls"].append({
            "source_id": "sec_8k_fulltext",
            "called": False,
            "skip_reason": (
                "block_filing_confirmation_disabled"
                if "sec_8k_fulltext" in selected["opencli"]
                else "not_selected"
            ),
        })

    if "github_commit_messages" in selected["opencli"] \
       and _block_enabled(BlockKey.ALTERNATIVE_DATA_FEATURES, enabled_blocks):
        _wire_github_commit_messages(
            adapter=GitHubCommitMessagesAdapter(),
            packet=packet, ticker=ticker,
            decision_timestamp=decision_timestamp,
            manifest=manifest, stub_mode=stub_mode,
        )
    else:
        manifest["calls"].append({
            "source_id": "github_commit_messages",
            "called": False,
            "skip_reason": (
                "block_alternative_data_features_disabled"
                if "github_commit_messages" in selected["opencli"]
                else "not_selected"
            ),
        })

    packet["alt_data_manifest"] = manifest
    _finalize_alt_data_placeholders(packet, manifest)
    # Block-wiring finalizers (2026-05-02). Each runs ONLY when its
    # target block exists in the packet (block-toggle discipline) and
    # only mutates fields the placeholder builder left at sentinel
    # values. None of these introduce LLM calls or new adapter calls;
    # they read the manifest + already-overlaid sub-sections.
    _finalize_filing_confirmation(packet, manifest)
    _finalize_sentiment_ownership(packet, manifest)
    _finalize_information_integrity(packet, manifest)
    _finalize_narrative_price_gap(packet, manifest)
    return manifest


# ── helpers ──────────────────────────────────────────────────────

# Sources whose successful delivery should be reflected in the
# alternative_data_features block placeholder fields. Other sources
# (sec_edgar / fmp_sentiment / sec_13f / sec_form4 / sec_def14a /
# sec_8k_fulltext) target a different block and are not relevant here.
_ALT_DATA_BLOCK_SOURCES = (
    "wikipedia_pageviews",
    "github_public",
    "github_commit_messages",
)


def _finalize_alt_data_placeholders(packet: dict, manifest: dict) -> None:
    """Sync alternative_data_features placeholder fields with the
    manifest's actual delivery so downstream agents stop reading stale
    v1-skeleton text.

    Two fields kept stale by the v1 placeholder skeleton even after a
    real source delivered rows:
      (1) block["reason"]                                — was: "v1 evidence
          packet does not wire any alternative-data adapter"
      (2) block["adapter_selection"]["selected_adapter_id"] — was:
          "default_sec_gdelt_adapter" (a fictional adapter ID not
          present in the live registry; the agent then cites it as
          'placeholder, no live data' and downgrades the verdict).

    Last-writer-wins for selected_adapter_id, matching the same
    behaviour `_overlay_alt_data_rows` and `_wire_github_commit_messages`
    use for `block["source"]`. When no alt-data source delivered, the
    selected_adapter_id is set to "" instead of the misleading
    placeholder string; the reason field is left as-is (the v1 default
    text accurately describes the no-delivery state).

    The default in `blocks/alt_data.py:65` is intentionally left
    unchanged so the Hash A regression invariant (1a24b87e...) — which
    runs on the no-opt-in default packet — stays intact.
    """
    block = packet.get(BlockKey.ALTERNATIVE_DATA_FEATURES)
    if not isinstance(block, dict):
        return

    delivered = [
        c["source_id"] for c in manifest.get("calls", [])
        if c.get("source_id") in _ALT_DATA_BLOCK_SOURCES
        and c.get("called")
        and c.get("returned_rows", 0) > 0
        and c.get("extraction_status") in ("ok", "stub")
    ]

    sel = block.setdefault("adapter_selection", {})

    if delivered:
        winning = delivered[-1]  # last-writer-wins, matches block["source"]
        block["reason"] = (
            "alt-data adapters wired and delivering: "
            + ", ".join(delivered)
        )
        sel["selected_adapter_id"] = winning
        sel["status"] = BlockStatus.OK
        sel["adapter_data_available"] = True
        sel["adapter_selection_reason"] = (
            "resolved from alt_data_manifest delivery: "
            + ", ".join(delivered)
        )
    else:
        # Wiring ran but no alt-data source delivered. Replace the
        # fictional "default_sec_gdelt_adapter" default with "" so
        # agents don't cite a non-existent placeholder adapter. Leave
        # reason as-is — the v1 default text describes this state.
        sel["selected_adapter_id"] = ""

def _select_sources(live_adapters: bool | Iterable[str]) -> dict:
    """Resolve the `live_adapters` parameter into two sets:
    {"alt_data": [...], "opencli": [...]}.

    Adapters in `OPTIONAL_ALT_DATA_SOURCES` are opt-in only — they are
    NEVER included by `live_adapters=True` (which would silently shift
    the regression baseline). They enter the active set only when the
    caller names them explicitly via `live_adapters=("fmp_sentiment",)`."""
    if live_adapters is True:
        return {
            "alt_data": list(DEFAULT_ALT_DATA_SOURCES),
            "opencli": list(DEFAULT_OPENCLI_USE_CASES),
        }
    if isinstance(live_adapters, (list, tuple, set, frozenset)):
        ids = set(live_adapters)
        known_alt = (
            tuple(DEFAULT_ALT_DATA_SOURCES) + tuple(OPTIONAL_ALT_DATA_SOURCES)
        )
        return {
            "alt_data": [s for s in known_alt if s in ids],
            "opencli": [s for s in DEFAULT_OPENCLI_USE_CASES if s in ids],
        }
    # any other truthy value falls back to "all (default tuple only)".
    return {
        "alt_data": list(DEFAULT_ALT_DATA_SOURCES),
        "opencli": list(DEFAULT_OPENCLI_USE_CASES),
    }


def _block_enabled(block_id: str, enabled_blocks: set[str] | None) -> bool:
    if enabled_blocks is None:
        return True
    return block_id in enabled_blocks


def _overlay_alt_data_rows(packet: dict, source_id: str, result) -> None:
    """Insert adapter rows into the relevant block sub-section."""
    block_target = result.block_target
    block = packet.get(block_target)
    if not isinstance(block, dict):
        return
    block.setdefault("live_adapter_rows", {})

    if source_id == "wikipedia_pageviews":
        sub = block.setdefault("attention", {})
        sub["wikipedia_pageviews"] = {
            "status": (BlockStatus.OK if result.extraction_status in ("ok", "stub")
                        else BlockStatus.DATA_UNAVAILABLE),
            "source": result.source_id,
            "source_flag": result.source_flag,
            "rows": result.rows,
            "manifest": result.manifest,
            "data_quality_flags": result.data_quality_flags,
        }
        # alternative_data_features status promotion (2026-04-30 fix —
        # paired with sec_edgar / sec_ownership precedents below). The
        # placeholder block had `status:not_evaluated`; promote to ok
        # whenever any sub-source contributes rows so PM agents stop
        # citing "Alternative-data gap" when alt-data is in fact present.
        if result.extraction_status in ("ok", "stub") and result.rows:
            block["status"] = BlockStatus.OK
            block["source"] = result.source_id
    elif source_id == "sec_edgar":
        sub = block.setdefault("filing_index", {})
        sub["sec_edgar"] = {
            "status": (BlockStatus.OK if result.extraction_status in ("ok", "stub")
                        else BlockStatus.DATA_UNAVAILABLE),
            "source": result.source_id,
            "source_flag": result.source_flag,
            "rows": result.rows,
            "manifest": result.manifest,
            "data_quality_flags": result.data_quality_flags,
        }
        # The placeholder filing_confirmation block had `status:not_evaluated`.
        # Promote to ok when adapter contributes data.
        if result.extraction_status in ("ok", "stub") and result.rows:
            block["status"] = BlockStatus.OK
            block["source"] = result.source_id
    elif source_id == "github_public":
        sub = block.setdefault("tech_activity", {})
        sub["github_public"] = {
            "status": (BlockStatus.OK if result.extraction_status in ("ok", "stub")
                        else BlockStatus.DATA_UNAVAILABLE),
            "source": result.source_id,
            "source_flag": result.source_flag,
            "rows": result.rows,
            "manifest": result.manifest,
            "data_quality_flags": result.data_quality_flags,
        }
        # alternative_data_features status promotion (2026-04-30 fix —
        # see wikipedia_pageviews branch above for the rationale).
        if result.extraction_status in ("ok", "stub") and result.rows:
            block["status"] = BlockStatus.OK
            block["source"] = result.source_id
    elif source_id == "fmp_sentiment":
        # FMP sentiment lands in sentiment_community_ownership_evidence,
        # the block left empty when Reddit was descoped. Rows are
        # tier-tagged (T1 press releases / T2 analyst grades / T4 news
        # sentiment) per RULES.md §10.13. All rows are descriptors-only.
        sub = block.setdefault("fmp_sentiment", {})
        sub.update({
            "status": (BlockStatus.OK if result.extraction_status in ("ok", "stub")
                        else BlockStatus.DATA_UNAVAILABLE),
            "source": result.source_id,
            "source_flag": result.source_flag,
            "rows": result.rows,
            "manifest": result.manifest,
            "data_quality_flags": result.data_quality_flags,
        })
        # Promote the placeholder block to ok if rows arrived.
        if result.extraction_status in ("ok", "stub") and result.rows:
            block["status"] = BlockStatus.OK
            block["source"] = result.source_id
    elif source_id == "polygon_news":
        # D5 Option C — Polygon historical news overrides the FMP
        # `news/stock-latest` items in news_event_summary when Polygon
        # delivers >=1 PIT-safe row. Rationale: FMP news/stock-latest
        # is wall-clock-now and returns 0 items for any historical
        # backtest day; Polygon supports published_utc.gte/lte cleanly
        # with 2-year free-tier lookback. When Polygon returns 0 (no
        # rows in window) we leave the existing FMP-populated items in
        # place so that live (today) decisions still see fresh news.
        # Each delivered item is tagged source:"polygon_news" so PIT
        # forensics can distinguish the two paths.
        if result.extraction_status == "ok" and result.rows:
            polygon_items: list[dict] = []
            for r in result.rows:
                polygon_items.append({
                    "published_at":      r.get("published_at"),
                    "published_at_utc":  r.get("published_at_utc")
                                          or r.get("as_of"),
                    "title":             r.get("title"),
                    "publisher":         r.get("publisher"),
                    "site":              r.get("site"),
                    "url":               r.get("url"),
                    "symbol":            r.get("symbol"),
                    "text_excerpt":      r.get("text_excerpt"),
                    # Polygon-specific extras (preserved for NDI agents).
                    "id":                r.get("id"),
                    "author":            r.get("author"),
                    "keywords":          r.get("keywords"),
                    "tickers":           r.get("tickers"),
                    "insights_for_ticker": r.get("insights_for_ticker"),
                    "source":            "polygon_news",
                })
            block["items"] = polygon_items
            block["items_count"] = len(polygon_items)
            block["status"] = BlockStatus.OK
            block["source"] = "polygon_news"
            block.setdefault("source_replacement_log", []).append({
                "from": "fmp_news",
                "to":   "polygon_news",
                "reason": "polygon_historical_overrides_fmp_live",
                "polygon_rows": len(polygon_items),
            })
        # When polygon returned 0 rows, leave block.items as-is (FMP
        # populated them via blocks/news.py during default build).
        # Always record the call in live_adapter_rows for forensic trace.
        block.setdefault("live_adapter_rows", {})["polygon_news"] = {
            "status": (BlockStatus.OK if result.extraction_status in ("ok", "stub")
                        else BlockStatus.DATA_UNAVAILABLE),
            "source": result.source_id,
            "source_flag": result.source_flag,
            "rows_returned": len(result.rows),
            "manifest": result.manifest,
            "data_quality_flags": result.data_quality_flags,
        }
    elif source_id in ("sec_13f", "sec_form4", "sec_def14a"):
        # SEC ownership / insider / proxy adapters — all three land in
        # sentiment_community_ownership_evidence, each in its own
        # subsection so the dashboard "Top Owners" section can address
        # them independently. Tier 1 per RULES.md §10.13. Rows are
        # descriptor-only per §11.6 / §11.13.
        sub = block.setdefault(source_id, {})
        sub.update({
            "status": (BlockStatus.OK if result.extraction_status in ("ok", "stub")
                        else BlockStatus.DATA_UNAVAILABLE),
            "source": result.source_id,
            "source_flag": result.source_flag,
            "rows": result.rows,
            "manifest": result.manifest,
            "data_quality_flags": result.data_quality_flags,
        })
        if result.extraction_status in ("ok", "stub") and result.rows:
            block["status"] = BlockStatus.OK
            block["source"] = result.source_id
    # Reddit branch removed 2026-04-28 (registration friction +
    # anti-pollution rationale per RULES.md §10.13). The
    # sentiment_community_ownership_evidence block is now populated by
    # the fmp_sentiment adapter when explicitly opted in (Step D8).


def _wire_sec_8k_fulltext(*, adapter, packet, ticker, decision_timestamp,
                            manifest, stub_mode) -> None:
    """Run the OpenCLI sec_8k_fulltext use case for the most recent
    8-K from the SEC EDGAR adapter output (if any)."""
    block = packet.get(BlockKey.FILING_CONFIRMATION)
    if not isinstance(block, dict):
        manifest["calls"].append({
            "source_id": "sec_8k_fulltext",
            "called": False,
            "skip_reason": "filing_confirmation_block_missing",
        })
        return
    rows = (block.get("filing_index", {})
              .get("sec_edgar", {})
              .get("rows", []))
    if not rows:
        manifest["calls"].append({
            "source_id": "sec_8k_fulltext",
            "called": False,
            "skip_reason": "no_8k_rows_available",
        })
        return

    # Take the most-recent 8-K
    latest = sorted(rows, key=lambda r: r.get("filing_date", ""))[-1]
    t0 = time.monotonic()
    try:
        result = adapter.fetch(
            query_terms={
                "filing_url": latest["primary_document_url"],
                "accession_number": latest["accession_number"],
                "items": latest.get("items", []),
            },
            decision_timestamp=decision_timestamp,
            stub_mode=stub_mode,
            cache_key=latest["accession_number"],
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        sub = block.setdefault("full_text", {})
        sub["sec_8k_fulltext"] = result.to_dict()
        manifest["calls"].append({
            "source_id": "sec_8k_fulltext",
            "called": True,
            "returned_rows": (
                1 if result.parsed_payload and result.parsed_payload.get("text")
                else 0
            ),
            "source_flag": (
                "live_opencli" if result.extraction_status == "ok"
                else "mock_fallback" if result.extraction_status == "stub"
                else "live_opencli_failed"
            ),
            "extraction_status": result.extraction_status,
            "error_class": result.error_class,
            "latency_ms": latency_ms,
        })
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        manifest["calls"].append({
            "source_id": "sec_8k_fulltext",
            "called": True,
            "returned_rows": 0,
            "source_flag": "live_opencli_failed",
            "extraction_status": "failed",
            "error_class": type(e).__name__,
            "latency_ms": latency_ms,
        })


def _wire_github_commit_messages(*, adapter, packet, ticker, decision_timestamp,
                                    manifest, stub_mode) -> None:
    """Run the OpenCLI github_commit_messages use case for the
    primary repo identified by the github_public adapter."""
    block = packet.get(BlockKey.ALTERNATIVE_DATA_FEATURES)
    if not isinstance(block, dict):
        manifest["calls"].append({
            "source_id": "github_commit_messages",
            "called": False,
            "skip_reason": "alternative_data_features_block_missing",
        })
        return
    gh_rows = (block.get("tech_activity", {})
                .get("github_public", {})
                .get("rows", []))
    if not gh_rows:
        manifest["calls"].append({
            "source_id": "github_commit_messages",
            "called": False,
            "skip_reason": "no_github_rows_available",
        })
        return

    owner = gh_rows[0].get("github_owner")
    if not owner:
        manifest["calls"].append({
            "source_id": "github_commit_messages",
            "called": False,
            "skip_reason": "github_owner_unknown",
        })
        return

    # Use the owner's most-starred public repo as the target — keep the
    # call simple and deterministic. Repo name reuse: owner == repo
    # is a common convention for org self-named repos; otherwise we
    # default to a placeholder repo name that the OpenCLI adapter
    # will accept.
    repo = owner

    t0 = time.monotonic()
    try:
        result = adapter.fetch(
            query_terms={"owner": owner, "repo": repo, "n": 5},
            decision_timestamp=decision_timestamp,
            stub_mode=stub_mode,
            cache_key=f"{owner}_{repo}_n5",
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        sub = block.setdefault("tech_activity", {}).setdefault("narrative", {})
        sub["github_commit_messages"] = result.to_dict()
        # alternative_data_features status promotion (2026-04-30 fix —
        # paired with wikipedia_pageviews / github_public branches in
        # _overlay_alt_data_rows above). OpenCLI results carry data
        # under `parsed_payload.commits` rather than `rows`, so the
        # truthy gate is the commits list, not result.rows.
        commits = (result.parsed_payload.get("commits", [])
                   if isinstance(result.parsed_payload, dict) else [])
        if result.extraction_status in ("ok", "stub") and commits:
            block["status"] = BlockStatus.OK
            block["source"] = "github_commit_messages"
        manifest["calls"].append({
            "source_id": "github_commit_messages",
            "called": True,
            "returned_rows": len(commits),
            "source_flag": (
                "live_opencli" if result.extraction_status == "ok"
                else "mock_fallback" if result.extraction_status == "stub"
                else "live_opencli_failed"
            ),
            "extraction_status": result.extraction_status,
            "error_class": result.error_class,
            "latency_ms": latency_ms,
        })
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        manifest["calls"].append({
            "source_id": "github_commit_messages",
            "called": True,
            "returned_rows": 0,
            "source_flag": "live_opencli_failed",
            "extraction_status": "failed",
            "error_class": type(e).__name__,
            "latency_ms": latency_ms,
        })


# ── Block-wiring finalizers (2026-05-02) ─────────────────────────
# Each finalizer reads `manifest` + already-overlaid sub-sections and
# fills the placeholder block's top-level summary fields. They do NOT
# call adapters, do NOT call LLMs, and do NOT mutate fields outside
# their block. Block-toggle discipline: every finalizer no-ops when
# its target block is absent from `packet` (None or missing).
#
# Source-tier mapping per RULES.md §10.13:
#   T1 = SEC filings (sec_edgar, sec_13f, sec_form4, sec_def14a,
#        sec_8k_fulltext, fmp_sentiment[press_releases])
#   T2 = analyst expert opinion (fmp_sentiment[grades_*]) + GitHub
#        (github_public, github_commit_messages — source-of-truth code)
#   T3 = curated reference (wikipedia_pageviews — Wikipedia traffic)
#   T4 = aggregated news / sentiment (polygon_news,
#        fmp_sentiment[news_sentiments_rss])
_SOURCE_TIER = {
    "sec_edgar":              "T1",
    "sec_13f":                "T1",
    "sec_form4":              "T1",
    "sec_def14a":             "T1",
    "sec_8k_fulltext":        "T1",
    "github_public":          "T2",
    "github_commit_messages": "T2",
    "wikipedia_pageviews":    "T3",
    "polygon_news":           "T4",
    "fmp_sentiment":          "T4",  # mixed T1/T2/T4 inside; aggregate-tag T4
}


def _delivered_source_ids(manifest: dict) -> list[str]:
    """Return the source_ids that actually delivered rows (>0) and did
    not error out. Stub deliveries count — they are deterministic
    placeholders the wiring layer treats as 'data-shaped' for the
    purposes of block status promotion."""
    return [
        c["source_id"] for c in manifest.get("calls", [])
        if c.get("called")
        and c.get("returned_rows", 0) > 0
        and c.get("extraction_status") in ("ok", "stub")
    ]


# ── Group A — filing_confirmation ────────────────────────────────
def _finalize_filing_confirmation(packet: dict, manifest: dict) -> None:
    """Hoist the most-recent SEC 8-K filing's metadata up to the
    block's top-level summary fields, and record whether the OpenCLI
    full-text retrieval succeeded.

    Read paths:
      filing_index.sec_edgar.rows          (filed 8-Ks, sorted by filing_date)
      full_text.sec_8k_fulltext            (OpenCLI parsed_payload)

    Written fields (only when source_flag != not_connected):
      status, source, as_of, available_as_of
      SEC_8K_confirmation: bool (true iff at least one 8-K row delivered)
      filing_date, accepted_datetime, filing_url
      filing_support_score: 0.0 / 0.5 / 1.0 (0=no filings, 0.5=metadata only,
                                              1.0=metadata + full text)
      reason: human-readable summary of what was wired
    """
    block = packet.get(BlockKey.FILING_CONFIRMATION)
    if not isinstance(block, dict):
        return

    edgar_sub = block.get("filing_index", {}).get("sec_edgar", {})
    edgar_rows = edgar_sub.get("rows", []) if isinstance(edgar_sub, dict) else []
    fulltext_sub = block.get("full_text", {}).get("sec_8k_fulltext", {})
    fulltext_payload = (
        fulltext_sub.get("parsed_payload", {})
        if isinstance(fulltext_sub, dict) else {}
    )
    has_fulltext = bool(
        isinstance(fulltext_payload, dict)
        and (fulltext_payload.get("text") or fulltext_payload.get("excerpt"))
    )

    if not edgar_rows:
        # sec_edgar didn't deliver — leave placeholder defaults intact.
        return

    latest = sorted(edgar_rows, key=lambda r: r.get("filing_date", ""))[-1]
    block["status"] = BlockStatus.OK
    block["source"] = "sec_edgar"
    block["as_of"] = latest.get("as_of") or latest.get("filing_date")
    block["available_as_of"] = latest.get("filing_date")
    block["SEC_8K_confirmation"] = (latest.get("filing_type") == "8-K")
    block["filing_date"] = latest.get("filing_date")
    block["accepted_datetime"] = latest.get("accepted_datetime")
    block["filing_url"] = latest.get("primary_document_url")
    block["filing_support_score"] = 1.0 if has_fulltext else 0.5
    block["reason"] = (
        "filing_confirmation populated from sec_edgar"
        + (" + opencli/sec_8k_fulltext" if has_fulltext else "")
        + f" ({len(edgar_rows)} 8-K rows in lookback window)"
    )


# ── Group B — sentiment_community_ownership_evidence ─────────────
def _finalize_sentiment_ownership(packet: dict, manifest: dict) -> None:
    """Promote sub-section deliveries (fmp_sentiment, sec_13f,
    sec_form4, sec_def14a, github_public.tech_activity) into the
    block's top-level summary fields per schema §8f.

    Each sub-block is independently reported. The top-level `status`
    is OK if ANY sub-section delivered; otherwise left as
    not_evaluated. This matches the precedent set by other multi-
    source blocks (see _overlay_alt_data_rows for the per-source
    promotion pattern).
    """
    block = packet.get(BlockKey.SENTIMENT_OWNERSHIP)
    if not isinstance(block, dict):
        return

    delivered_subs: list[str] = []

    # ── A. Market sentiment from fmp_sentiment rows ──
    fmp_sub = block.get("fmp_sentiment", {})
    fmp_rows = fmp_sub.get("rows", []) if isinstance(fmp_sub, dict) else []
    if fmp_rows:
        ms = block.setdefault("market_sentiment", {})
        ms["status"] = BlockStatus.OK
        ms["data_available"] = True
        ms["sentiment_window"] = "rolling_30d"
        ms["sentiment_source_count"] = len(fmp_rows)
        # Tier-weighted descriptor count (T1 press, T2 grades, T4 news).
        tier_counts = {"T1": 0, "T2": 0, "T4": 0}
        for r in fmp_rows:
            t = r.get("tier")
            if t in tier_counts:
                tier_counts[t] += 1
        ms["sentiment_credibility_adjustment"] = (
            f"tier-weighted: T1={tier_counts['T1']} "
            f"T2={tier_counts['T2']} T4={tier_counts['T4']}"
        )
        delivered_subs.append("market_sentiment")

    # ── B. Community size from github_public (developer metrics) ──
    alt_block = packet.get(BlockKey.ALTERNATIVE_DATA_FEATURES)
    if isinstance(alt_block, dict):
        gh_sub = alt_block.get("tech_activity", {}).get("github_public", {})
        gh_rows = gh_sub.get("rows", []) if isinstance(gh_sub, dict) else []
        if gh_rows:
            cs = block.setdefault("community_size", {})
            cs["status"] = BlockStatus.OK
            cs["data_available"] = True
            cs["community_source"] = "github_public"
            dcm = cs.setdefault("developer_community_metrics", {})
            # Pull the most-recent row's totals (rows are ordered).
            latest_gh = gh_rows[-1]
            dcm["github_stars"] = latest_gh.get("stars_total")
            dcm["github_contributors_30d"] = latest_gh.get(
                "contributors_30d"
            )
            dcm["github_forks"] = latest_gh.get("forks_total")
            delivered_subs.append("community_size")

    # ── C. Ownership / smart-money from sec_13f, sec_form4, sec_def14a ──
    o13f_sub = block.get("sec_13f", {})
    o13f_rows = o13f_sub.get("rows", []) if isinstance(o13f_sub, dict) else []
    f4_sub = block.get("sec_form4", {})
    f4_rows = f4_sub.get("rows", []) if isinstance(f4_sub, dict) else []
    d14a_sub = block.get("sec_def14a", {})
    d14a_rows = d14a_sub.get("rows", []) if isinstance(d14a_sub, dict) else []

    if o13f_rows or f4_rows or d14a_rows:
        op = block.setdefault("ownership_positioning", {})
        op["status"] = BlockStatus.OK
        op["data_available"] = True
        if o13f_rows:
            latest_13f = o13f_rows[-1]
            op["report_date"] = latest_13f.get("report_date")
            op["accepted_datetime"] = latest_13f.get("accepted_datetime")
            op["filing_date"] = latest_13f.get("filing_date")
            op["institutional_ownership_pct"] = latest_13f.get(
                "institutional_ownership_pct"
            )
            op["top_10_holder_concentration"] = latest_13f.get(
                "top_10_holder_concentration"
            )
            op["hedge_fund_holder_count"] = latest_13f.get(
                "hedge_fund_holder_count"
            )
            op["top_holders"] = latest_13f.get("top_holders", []) or []
        if f4_rows:
            op["ownership_change_summary"] = (
                f"sec_form4 delivered {len(f4_rows)} insider transaction "
                f"rows; agent must interpret direction (no embedded buy/sell "
                f"verdict per §11.6 descriptors-only)"
            )
        op["data_staleness_warning"] = (
            "ownership feed connected; PIT-cutoff enforced on "
            "accepted_datetime per RULES.md §11.5/§11.14"
        )
        delivered_subs.append("ownership_positioning")

    if delivered_subs:
        block["status"] = BlockStatus.OK
        block["data_available"] = True
        block["source"] = ", ".join(delivered_subs)
        block["reason"] = (
            "sentiment_community_ownership_evidence populated from: "
            + ", ".join(delivered_subs)
        )


# ── Group B — information_integrity_assessment ───────────────────
def _finalize_information_integrity(packet: dict, manifest: dict) -> None:
    """Populate source_tier_distribution + corroboration descriptors
    from the manifest. The pollution-defense layer is still NOT
    implemented — `use_as_primary_signal_allowed` stays False per
    schema doc v1.1 decision #8 (FAIL-CLOSED governance gate).

    The block transitions from `not_evaluated` to OK only when at
    least one Tier-1 source delivered. Otherwise the block reports
    `insufficient_evidence` so downstream agents know the wiring ran
    but couldn't anchor on a primary source.
    """
    block = packet.get(BlockKey.INFORMATION_INTEGRITY)
    if not isinstance(block, dict):
        return

    delivered = _delivered_source_ids(manifest)
    if not delivered:
        return

    tier_dist = {"T1": 0, "T2": 0, "T3": 0, "T4": 0, "T5": 0}
    for sid in delivered:
        tier = _SOURCE_TIER.get(sid)
        if tier in tier_dist:
            tier_dist[tier] += 1

    block["source_tier_distribution"] = tier_dist
    has_t1 = tier_dist["T1"] > 0
    has_t1_or_t2 = tier_dist["T1"] + tier_dist["T2"] > 0

    block["status"] = (
        BlockStatus.OK if has_t1 else BlockStatus.INSUFFICIENT_EVIDENCE
    )
    block["source"] = "alt_data_manifest"
    block["data_available"] = True
    block["primary_source_found"] = has_t1
    block["reputable_confirmation_found"] = has_t1_or_t2
    block["official_disclosure_confirmation"] = has_t1
    block["claim_source_type"] = (
        "primary_filing" if has_t1
        else "secondary_reputable" if has_t1_or_t2
        else "low_tier_only"
    )
    block["corroboration_status"] = (
        "T1+T2_corroborated" if (has_t1 and tier_dist["T2"] > 0)
        else "T1_only" if has_t1
        else "uncorroborated"
    )
    # Pollution-risk descriptors remain "unknown" — no pollution-
    # defense adapter is wired. The §10.5 metric placeholders stay
    # null. We DO set evidence_credibility_assessment based on tier mix.
    if has_t1:
        block["evidence_credibility_assessment"] = (
            "anchored_on_primary_source"
        )
    elif has_t1_or_t2:
        block["evidence_credibility_assessment"] = (
            "secondary_reputable_only"
        )
    else:
        block["evidence_credibility_assessment"] = "low_tier_only"

    block["reason"] = (
        "information_integrity_assessment populated from alt_data_manifest "
        f"tier distribution; "
        f"T1={tier_dist['T1']} T2={tier_dist['T2']} "
        f"T3={tier_dist['T3']} T4={tier_dist['T4']}. "
        "Pollution-defense layer still unimplemented; "
        "use_as_primary_signal_allowed stays FAIL-CLOSED per schema v1.1 "
        "decision #8."
    )
    # Governance gate: NEVER flip to True from this finalizer. The fail-
    # closed default is the schema doc's contract until pollution-defense
    # ships.
    block["use_as_primary_signal_allowed"] = False


# ── Group C — narrative_price_gap_assessment (option i) ──────────
def _finalize_narrative_price_gap(packet: dict, manifest: dict) -> None:
    """Refresh `evidence_used` / `evidence_missing` to reflect the
    sources that actually delivered for THIS packet. Does not compute
    a verdict (that remains Agent 03's job per the placeholder
    contract); only updates the evidence pointers so the agent doesn't
    cite a stale generic skeleton.
    """
    block = packet.get(BlockKey.NARRATIVE_PRICE_GAP)
    if not isinstance(block, dict):
        return

    delivered = _delivered_source_ids(manifest)
    if not delivered:
        return

    evidence_used = [
        "news_event_summary.items",
        "filing_confirmation",
        "price_snapshot.return_5d_pct",
        "price_snapshot.relative_volume_vs_20d",
    ]
    if "wikipedia_pageviews" in delivered:
        evidence_used.append(
            "alternative_data_features.attention.wikipedia_pageviews"
        )
    if "github_public" in delivered or "github_commit_messages" in delivered:
        evidence_used.append(
            "alternative_data_features.tech_activity"
        )
    if "fmp_sentiment" in delivered:
        evidence_used.append(
            "sentiment_community_ownership_evidence.market_sentiment"
        )
    if any(s in delivered for s in ("sec_13f", "sec_form4", "sec_def14a")):
        evidence_used.append(
            "sentiment_community_ownership_evidence.ownership_positioning"
        )
    if "sec_8k_fulltext" in delivered:
        evidence_used.append("filing_confirmation.full_text.sec_8k_fulltext")

    block["evidence_used"] = evidence_used
    block["evidence_missing"] = ["agent_LLM_reasoning"]
    block["rationale"] = (
        "narrative_price_gap_assessment evidence pointers refreshed from "
        "alt_data_manifest delivery; verdict still computed downstream by "
        "Agent 03 per schema v1 contract."
    )
