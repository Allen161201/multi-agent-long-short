"""
Schema constants + status enums for evidence_packet_v1.

Single source of truth for the field names, status enum, source-flag enum,
and decision-mode enums. Keep this file lean — implementations live in
the block builders, not here.

Naming follows `docs/EVIDENCE_PACKET_V1_DRAFT.md` ("schema authority"). Any
deviation from that doc must be flagged to the user, not silently changed
here.
"""
from __future__ import annotations

# ── Schema identity ───────────────────────────────────────────────
# `SCHEMA_VERSION` stays at "evidence_packet_v1" until release. The schema
# DOC has rolled to v1.1 (CHANGELOG in EVIDENCE_PACKET_V1_DRAFT.md): added
# corporate_calendar block, block-level status enum, fail-closed default
# for use_as_primary_signal_allowed, hindsight_safe envelope field.
SCHEMA_VERSION = "evidence_packet_v1"
SCHEMA_DOC_REVISION = "v1.1_2026_04_28"
GENERATOR_VERSION = "v1.1_hindsight_2026_04_28"


# ── Block status enum (schema doc v1.1, decision #6) ──────────────
# Used by every block's top-level `status` field. Missing data is NEVER
# silently zeroed; it is reported via one of these values. Documented
# in `docs/EVIDENCE_PACKET_V1_DRAFT.md` §Block-status enum.
class BlockStatus:
    OK = "ok"
    """Block populated; all required fields present and PIT-safe."""
    DATA_UNAVAILABLE = "data_unavailable"
    """Source returned no usable data (HTTP error, empty payload, all rows filtered)."""
    NOT_EVALUATED = "not_evaluated"
    """Block deliberately not computed (placeholder, no LLM, no live adapter)."""
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    """Some data present but not enough to draw the verdict the block represents."""
    STALE = "stale"
    """Data older than the block's staleness threshold (e.g. quote > 5 min on a live decision)."""

    ALL = (OK, DATA_UNAVAILABLE, NOT_EVALUATED, INSUFFICIENT_EVIDENCE, STALE)


# ── Source flag enum ──────────────────────────────────────────────
# Mirrors fmp_adapter / fred_adapter convention.
class Source:
    LIVE_FMP = "live_fmp"
    LIVE_FMP_FAILED = "live_fmp_failed"
    LIVE_FRED = "live_fred"
    LIVE_FRED_FAILED = "live_fred_failed"
    LIVE_SEC = "live_sec"
    LIVE_SEC_FAILED = "live_sec_failed"
    CACHE = "cache"
    MOCK_FALLBACK = "mock_fallback"
    NOT_CONNECTED = "not_connected"
    NONE = "none"


# ── Decision-mode enums ───────────────────────────────────────────
# Top-level "decision_mode" (this generator):
#   - live              → caller wants a packet built RIGHT NOW
#   - historical_replay → caller wants a PIT-safe packet for a past
#                         decision_timestamp (Pass 8 Step B1.8,
#                         2026-05-04). When this mode is active, the
#                         generator's strict_pit_mode flag auto-defaults
#                         to True and an explicit False raises
#                         AssertionError per §8.R3.
class DecisionMode:
    LIVE = "live"
    HISTORICAL_REPLAY = "historical_replay"

    SUPPORTED = (LIVE, HISTORICAL_REPLAY)


# Live sub-modes — auto-detected from US/Eastern wall clock OR caller-supplied.
# These are NOT the same enum as `decision_time_discipline.decision_mode`
# in the schema; the latter uses `pre_market | opening_window |
# end_of_day_surge | historical_replay`. The mapping is in
# `blocks.decision_time.SUB_MODE_TO_SCHEMA_MODE`.
class DecisionSubMode:
    PRE_MARKET = "pre_market"
    OPENING_WINDOW = "opening_window"
    END_OF_DAY = "end_of_day"
    INTRADAY_REVIEW = "intraday_review"

    ALL = (PRE_MARKET, OPENING_WINDOW, END_OF_DAY, INTRADAY_REVIEW)


# ── Block keys (top-level packet shape) ───────────────────────────
# Authority: docs/EVIDENCE_PACKET_V1_DRAFT.md §1.
class BlockKey:
    ENVELOPE = "envelope"                          # generator-introduced, contains top-level metadata
    PRICE_SNAPSHOT = "price_snapshot"
    MACRO_REGIME = "macro_regime"
    FUNDAMENTAL_SNAPSHOT = "fundamental_snapshot"
    VALUATION_SNAPSHOT = "valuation_snapshot"
    NEWS_EVENT_SUMMARY = "news_event_summary"
    FILING_CONFIRMATION = "filing_confirmation"
    CORPORATE_CALENDAR = "corporate_calendar"      # not in v1 draft; flagged to user
    ALTERNATIVE_DATA_FEATURES = "alternative_data_features"
    INFORMATION_INTEGRITY = "information_integrity_assessment"
    SENTIMENT_OWNERSHIP = "sentiment_community_ownership_evidence"
    DECISION_TIME_DISCIPLINE = "decision_time_discipline"
    NARRATIVE_PRICE_GAP = "narrative_price_gap_assessment"
    DATA_QUALITY_FLAGS = "data_quality_flags"
    PIT_SAFETY_FLAGS = "PIT_safety_flags"
    SOURCE_LIST = "source_list"
    AGENT_READY_NOTES = "agent_ready_notes"


# ── Hash policy: which keys are excluded from the canonical hash ──
# `evidence_packet_hash` is a sha256 of the canonicalized packet JSON
# AFTER these wall-clock / cache-flag fields have been stripped, so that
# two consecutive runs at the same `decision_timestamp` and warm cache
# produce the same hash.
HASH_EXCLUDED_TOP_LEVEL_PATHS = (
    # Self-referential
    ("envelope", "evidence_packet_hash"),
    ("envelope", "locked_decision_id"),
    # Wall-clock anchors that change every call
    ("envelope", "analysis_run_time"),
    ("envelope", "analysis_run_time_utc"),
    ("envelope", "built_at_utc"),
    ("decision_time_discipline", "analysis_run_time"),
    ("decision_time_discipline", "analysis_run_time_utc"),
    ("decision_time_discipline", "evidence_packet_hash_ref"),
    ("decision_time_discipline", "locked_decision_id_ref"),
    # source_list rows carry per-fetch wall clocks; exclude entire list
    ("source_list",),
    # Build telemetry is per-run only (excluded)
    ("build_telemetry",),
)

# Leaf-level keys that may appear anywhere and should be masked when
# hashing. (e.g. `served_from_cache`, `fetched_at_utc`, etc.)
HASH_EXCLUDED_LEAF_KEYS = frozenset({
    "served_from_cache",
    "fetched_at_utc",
    "fetched_at",
    "_from_cache",
    "_cache_fetched_at",
    "_cache_data_mode",
    "available_as_of",       # FMP-supplied wall-clock at fetch time
    "as_of",                 # block-level fetch time
    "timestamp",             # FMP quote / news timestamps from current source
    "timestamp_unix",
    "request_timestamp_utc",
    "elapsed_ms",
    "wait_s",
    "fetched_at_iso",
    "build_telemetry",
})
