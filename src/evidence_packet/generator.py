"""
Evidence Packet Generator — v1 live mode orchestrator.

Public:
    generate_evidence_packet(ticker, decision_mode="live",
                              decision_timestamp=None,
                              decision_sub_mode=None) -> dict

Live-mode-only in v1; replay raises NotImplementedError. The signature
already accepts (ticker, decision_mode, decision_timestamp) so adding
replay later is non-breaking.

What this orchestrator owns directly (not in a per-block file):
  - the envelope (top-level metadata)
  - the source_list aggregation
  - data_quality_flags aggregation
  - PIT_safety_flags aggregation
  - agent_ready_notes aggregation
  - filing_confirmation block (placeholder — no SEC adapter)
  - narrative_price_gap_assessment block (placeholder — no agent in v1)
  - hash + locked_decision_id derivation
  - cutoff-violation re-check after all blocks are built

What it does NOT do:
  - no LLM calls
  - no rule-engine invocation
  - no writes to disk (the CLI does that)
  - no modification of any production adapter / rule / dashboard file
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Ensure src/ is on path when imported as a top-level package by the CLI
_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from .blocks import (
    alt_data, calendar, decision_time, fundamental, integrity,
    macro, news, price, sentiment_ownership, valuation,
)
from .blocks.decision_time import ET, auto_detect_sub_mode
from .hash_utils import compute_evidence_packet_hash, compute_locked_decision_id
from .hindsight_rules import run_hindsight_audit
from .pit_violation import PITViolationError
from .schema import (
    BlockKey, BlockStatus, DecisionMode, DecisionSubMode,
    GENERATOR_VERSION, SCHEMA_DOC_REVISION, SCHEMA_VERSION, Source,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_et() -> datetime:
    return datetime.now(ET)


def _ensure_et(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def _coerce_decision_timestamp(decision_timestamp: str | datetime | None,
                                 analysis_run_time_utc: datetime) -> datetime:
    """Live mode: decision_timestamp == analysis_run_time (in ET).
    If a caller supplies one explicitly, honour it (must be ET-aware or ISO)."""
    if decision_timestamp is None:
        return analysis_run_time_utc.astimezone(ET)
    if isinstance(decision_timestamp, datetime):
        return _ensure_et(decision_timestamp)
    # ISO string
    try:
        dt = datetime.fromisoformat(decision_timestamp.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            f"decision_timestamp must be ISO-8601; got {decision_timestamp!r}"
        ) from e
    return _ensure_et(dt)


def _normalise_sub_mode(value: str | None, decision_dt_et: datetime) -> str:
    if value is None:
        return auto_detect_sub_mode(decision_dt_et)
    if value not in DecisionSubMode.ALL:
        raise ValueError(
            f"decision_sub_mode={value!r} not in {DecisionSubMode.ALL}"
        )
    return value


def _build_filing_confirmation_placeholder() -> dict:
    """schema §7 — SEC 8-K confirmation. Today no SEC adapter. Placeholder."""
    return {
        "status": BlockStatus.NOT_EVALUATED,
        "source": Source.NOT_CONNECTED,
        "as_of": None,
        "available_as_of": None,
        "SEC_8K_confirmation": None,
        "filing_date": None,
        "accepted_datetime": None,
        "filing_url": None,
        "press_release_confirmation": None,
        "earnings_call_confirmation": None,
        "filing_support_score": None,
        "reason": (
            "v0.8.7 wires sec_edgar (filing_index) + sec_8k_fulltext "
            "(full_text) via the live_adapters overlay; this skeleton text "
            "remains when no 8-K rows were delivered for this ticker — see "
            "filing_index.sec_edgar and full_text.sec_8k_fulltext sub-blocks "
            "for actual adapter status. status/source/reason are overwritten "
            "by _finalize_filing_confirmation when sec_edgar delivers rows."
        ),
    }


def _build_narrative_gap_placeholder() -> dict:
    """schema §9 — narrative-price gap. Requires agent reasoning. Placeholder."""
    return {
        "status": BlockStatus.NOT_EVALUATED,
        "source": "agent_internal_pending",
        "score_band": "not_evaluated",
        "rationale": (
            "v1 generator does not invoke an LLM; narrative-price gap "
            "assessment is computed downstream by Agent 03."
        ),
        "evidence_used": [
            "news_event_summary.items",
            "filing_confirmation",
            "price_snapshot.return_5d_pct",
            "price_snapshot.relative_volume_vs_20d",
        ],
        "evidence_missing": ["agent_LLM_reasoning"],
        "uncertainty": "high",
    }


def _parse_as_of_to_utc(value: str) -> datetime | None:
    """Parse a heterogeneous as_of string into a UTC-aware datetime.

    Handles:
      - ISO-8601 with offset:           "2026-04-27T22:24:15-04:00"
      - ISO-8601 with Z:                "2026-04-28T02:24:15Z"
      - Naive ISO-8601:                 "2026-04-28T02:24:15"  (treat as UTC)
      - Date only:                      "2026-04-27"           (treat as 23:59:59 ET)
      - FMP statement accepted_date:    "2026-01-30 06:01:32"  (treat as ET)
    """
    if not isinstance(value, str) or not value:
        return None
    s = value.strip().replace("Z", "+00:00")
    # Date-only — treat as start-of-day UTC. This is conservative: a date-
    # tagged datum (e.g. FRED macro indicators for "2026-04-27") never
    # represents data from after midnight UTC on that date, and treating
    # it as start-of-day prevents false-positive lookahead violations
    # for blocks whose granularity is daily.
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            d = datetime.strptime(s, "%Y-%m-%d")
            return d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    # FMP statement format: "YYYY-MM-DD HH:MM:SS"
    if len(s) == 19 and s[4] == "-" and s[10] == " ":
        try:
            d = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return d.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        except ValueError:
            pass
    # ISO-8601
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except ValueError:
        return None


def _check_lookahead(packet: dict, cutoff_dt_utc: datetime) -> tuple[bool, list[dict]]:
    """Return (lookahead_safe, violations).

    Violations are reported when a block's `as_of` (or `available_as_of`)
    parses to a UTC datetime strictly greater than the cutoff. Strings
    that can't be parsed are treated as missing (no violation, no pass).
    """
    violations: list[dict] = []
    for key, block in packet.items():
        if not isinstance(block, dict):
            continue
        as_of_str = block.get("as_of") or block.get("available_as_of")
        as_of_dt = _parse_as_of_to_utc(as_of_str) if isinstance(as_of_str, str) else None
        if as_of_dt is None:
            continue
        if as_of_dt > cutoff_dt_utc:
            violations.append({
                "block": key,
                "as_of": as_of_str,
                "cutoff_utc": cutoff_dt_utc.isoformat(),
                "kind": "block_as_of_after_cutoff",
            })
    return (len(violations) == 0), violations


# Step A3: canonical block id set used to validate `enabled_blocks`. The
# names match the BlockKey constants. Documented in
# docs/EVIDENCE_PACKET_BLOCK_IDS.md.
TOGGLEABLE_BLOCK_IDS = frozenset({
    BlockKey.PRICE_SNAPSHOT,
    BlockKey.MACRO_REGIME,
    BlockKey.FUNDAMENTAL_SNAPSHOT,
    BlockKey.VALUATION_SNAPSHOT,
    BlockKey.NEWS_EVENT_SUMMARY,
    BlockKey.CORPORATE_CALENDAR,
    BlockKey.ALTERNATIVE_DATA_FEATURES,
    BlockKey.INFORMATION_INTEGRITY,
    BlockKey.SENTIMENT_OWNERSHIP,
    BlockKey.DECISION_TIME_DISCIPLINE,
    BlockKey.FILING_CONFIRMATION,
    BlockKey.NARRATIVE_PRICE_GAP,
})


def _validate_enabled_blocks(enabled_blocks: set[str] | None) -> set[str] | None:
    """Coerce and validate the enabled_blocks set.

    Returns the validated set (or None for the default). Raises
    ValueError on unknown ids so a typo is surfaced immediately rather
    than silently producing an all-null packet.
    """
    if enabled_blocks is None:
        return None
    if not isinstance(enabled_blocks, (set, frozenset, list, tuple)):
        raise ValueError(
            f"enabled_blocks must be a set/list/tuple of block ids; "
            f"got {type(enabled_blocks).__name__}"
        )
    coerced = set(enabled_blocks)
    unknown = coerced - TOGGLEABLE_BLOCK_IDS
    if unknown:
        raise ValueError(
            f"Unknown block ids in enabled_blocks: {sorted(unknown)}. "
            f"Valid ids: {sorted(TOGGLEABLE_BLOCK_IDS)}"
        )
    return coerced


def generate_evidence_packet(
    ticker: str,
    decision_mode: str = DecisionMode.LIVE,
    decision_timestamp: str | datetime | None = None,
    decision_sub_mode: str | None = None,
    enabled_blocks: set[str] | None = None,
    live_adapters: bool | list | tuple | set | frozenset | None = None,
    strict_pit_mode: bool | None = None,
) -> dict:
    """Build a v1 evidence packet for `ticker`.

    Live mode is the only supported `decision_mode` today. A future
    `historical_replay` mode will reuse the same signature.

    enabled_blocks (Step A3): optional set of canonical block ids to
    populate. None (default) = all 12 blocks populated, byte-identical
    to the pre-A3 packet. Any non-None value (even the canonical full
    set) records `enabled_blocks` in the envelope and includes it in
    the packet hash, so toggle subsets produce distinct hashes for
    cache isolation. See docs/EVIDENCE_PACKET_BLOCK_IDS.md.

    live_adapters (Step D): when None (default), the alt-data + OpenCLI
    adapters are NOT invoked and the packet is byte-identical to the
    pre-Step-D packet (preserves the Step A6 30/30 regression hash).
    When True, all 4 alt-data adapters + 2 OpenCLI use cases run.
    When a list/tuple/set, only the named source_ids run. The hash
    naturally differs from the no-adapter packet because the manifest
    + adapter rows enter the hashable region.
    """
    if decision_mode not in DecisionMode.SUPPORTED:
        raise NotImplementedError(
            f"decision_mode={decision_mode!r} not supported; "
            f"valid values: {DecisionMode.SUPPORTED}."
        )

    # Pass 8 Step B1.8 (2026-05-04) — close C1 from the B1.7 sign-off.
    # strict_pit_mode now DEFAULTS from decision_mode:
    #   - replay → True   (forbidden-paths scrubber + lookahead-abort)
    #   - live   → False  (live data legitimately carries live_quote/etc.)
    # Callers may still opt strict-mode ON for live (e.g. test fixtures
    # that pin a past decision_timestamp), but they may NOT opt strict-mode
    # OFF for replay — that would re-open the leak surface C1 documented.
    is_replay = (decision_mode == DecisionMode.HISTORICAL_REPLAY)
    if strict_pit_mode is None:
        strict_pit_mode = is_replay
    if is_replay and not strict_pit_mode:
        raise AssertionError(
            "strict_pit_mode=False with decision_mode='historical_replay' "
            "is forbidden — would leak forbidden fields per §8.R3 "
            "(see Pass 8 Step B1.8 PIT-lock closure)."
        )

    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("ticker must be a non-empty string")
    ticker = ticker.strip().upper()

    enabled_blocks_validated = _validate_enabled_blocks(enabled_blocks)
    # is_toggle_active means the caller explicitly asked for subsetting.
    # When None, behaviour is byte-identical to the pre-A3 generator.
    is_toggle_active = enabled_blocks_validated is not None

    analysis_run_time_utc = _now_utc()
    decision_dt_et = _coerce_decision_timestamp(decision_timestamp,
                                                  analysis_run_time_utc)
    sub_mode = _normalise_sub_mode(decision_sub_mode, decision_dt_et)
    allowed_data_cutoff = decision_dt_et   # live mode: cutoff == decision

    # ── 1. Build all live blocks ──
    # Step A3: when enabled_blocks is provided, blocks NOT in the set
    # are skipped entirely (no adapter call) and represented as null in
    # the final packet. When None, all blocks are built (byte-identical
    # to pre-A3 behaviour).
    def _enabled(block_id: str) -> bool:
        if not is_toggle_active:
            return True
        return block_id in enabled_blocks_validated

    block_results: dict[str, dict] = {}

    if _enabled(BlockKey.PRICE_SNAPSHOT):
        block_results[BlockKey.PRICE_SNAPSHOT] = price.build(
            ticker=ticker, allowed_data_cutoff=allowed_data_cutoff,
        )
    if _enabled(BlockKey.MACRO_REGIME):
        block_results[BlockKey.MACRO_REGIME] = macro.build(
            allowed_data_cutoff=allowed_data_cutoff,
        )
    if _enabled(BlockKey.FUNDAMENTAL_SNAPSHOT):
        block_results[BlockKey.FUNDAMENTAL_SNAPSHOT] = fundamental.build(
            ticker=ticker, allowed_data_cutoff=allowed_data_cutoff,
        )
    if _enabled(BlockKey.VALUATION_SNAPSHOT):
        block_results[BlockKey.VALUATION_SNAPSHOT] = valuation.build(
            ticker=ticker, allowed_data_cutoff=allowed_data_cutoff,
        )
    if _enabled(BlockKey.NEWS_EVENT_SUMMARY):
        block_results[BlockKey.NEWS_EVENT_SUMMARY] = news.build(
            ticker=ticker, allowed_data_cutoff=allowed_data_cutoff,
        )
    if _enabled(BlockKey.CORPORATE_CALENDAR):
        block_results[BlockKey.CORPORATE_CALENDAR] = calendar.build(
            ticker=ticker, allowed_data_cutoff=allowed_data_cutoff,
            pit_calendar=bool(strict_pit_mode),
        )
    if _enabled(BlockKey.ALTERNATIVE_DATA_FEATURES):
        block_results[BlockKey.ALTERNATIVE_DATA_FEATURES] = alt_data.build()
    if _enabled(BlockKey.INFORMATION_INTEGRITY):
        block_results[BlockKey.INFORMATION_INTEGRITY] = integrity.build()
    if _enabled(BlockKey.SENTIMENT_OWNERSHIP):
        block_results[BlockKey.SENTIMENT_OWNERSHIP] = sentiment_ownership.build()

    # ── 2. Compose the packet shell (without hash / locked_id) ──
    packet: dict[str, Any] = {}

    # Envelope first — generator-level metadata
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "schema_doc_revision": SCHEMA_DOC_REVISION,
        "generator_version": GENERATOR_VERSION,
        "rule_version": "v0.9.0_pass8_hardrule",  # bumped 2026-05-04 Pass 8 hard-rule architecture overhaul (RULES.md §22 v2.7→v2.8; user explicit authorization)
        "ticker": ticker,
        "decision_mode": decision_mode,
        "decision_sub_mode": sub_mode,

        "analysis_run_time_utc": analysis_run_time_utc.isoformat(),
        "analysis_run_time": analysis_run_time_utc.astimezone(ET).isoformat(),
        "decision_timestamp": decision_dt_et.isoformat(),
        "allowed_data_cutoff": decision_dt_et.isoformat(),

        # Filled after hashing
        "evidence_packet_hash": None,
        "locked_decision_id": None,
        "immutable_decision_flag": True,

        # Look-ahead summary (filled after we build all blocks)
        "data_after_cutoff_used": False,
        "lookahead_safe": True,
        "lookahead_violations": [],

        # Hindsight summary (NEW v1.1) — populated by the hindsight auditor
        "hindsight_safe": True,
        "hindsight_violations": [],
        # R5 — universe PIT-validated. In live mode the universe is "now"
        # by definition; in replay mode this becomes True only after
        # universe-PIT filtering (utils/delisting_detection.py + a future
        # PIT membership filter) has been applied.
        "universe_pit_validated": (decision_mode == DecisionMode.LIVE),
        # Whether any block legitimately consumed a current-state value.
        # Aggregated from per-block uses_current_state flags below.
        "uses_current_state": False,
    }
    # Step A3: only inject `enabled_blocks` when the caller requested
    # subsetting. Default behaviour (None) is byte-identical to the
    # pre-A3 generator and must NOT add this field.
    if is_toggle_active:
        envelope["enabled_blocks"] = sorted(enabled_blocks_validated)
    packet[BlockKey.ENVELOPE] = envelope

    # Place all populated blocks under their canonical keys; missing
    # toggleable blocks are emitted as null so downstream consumers can
    # tell "absent" apart from "empty".
    for block_id in TOGGLEABLE_BLOCK_IDS - {
        BlockKey.DECISION_TIME_DISCIPLINE,
        BlockKey.FILING_CONFIRMATION,
        BlockKey.NARRATIVE_PRICE_GAP,
    }:
        if block_id in block_results:
            packet[block_id] = block_results[block_id]["block"]
        elif is_toggle_active:
            packet[block_id] = None

    # decision_time_discipline — toggleable but normally on. If a caller
    # explicitly disables it, the agents must operate without the mode
    # mirror (their own invariants will trip via the new null-handling
    # clause). Toggling it off is supported but not recommended.
    if _enabled(BlockKey.DECISION_TIME_DISCIPLINE):
        packet[BlockKey.DECISION_TIME_DISCIPLINE] = decision_time.build(
            ticker=ticker,
            decision_timestamp_et=decision_dt_et,
            sub_mode=sub_mode,
            analysis_run_time_utc=analysis_run_time_utc,
        )
    elif is_toggle_active:
        packet[BlockKey.DECISION_TIME_DISCIPLINE] = None

    # filing_confirmation + narrative_price_gap_assessment placeholders
    if _enabled(BlockKey.FILING_CONFIRMATION):
        packet[BlockKey.FILING_CONFIRMATION] = _build_filing_confirmation_placeholder()
    elif is_toggle_active:
        packet[BlockKey.FILING_CONFIRMATION] = None

    if _enabled(BlockKey.NARRATIVE_PRICE_GAP):
        packet[BlockKey.NARRATIVE_PRICE_GAP] = _build_narrative_gap_placeholder()
    elif is_toggle_active:
        packet[BlockKey.NARRATIVE_PRICE_GAP] = None

    # ── 3. Aggregate meta lists (source_list, quality, PIT, agent_notes) ──
    source_list: list[dict] = []
    quality_flags: list[dict] = []
    pit_flags: list[dict] = []
    agent_notes: list[str] = []
    api_calls_per_block: dict[str, int] = {}

    for key, result in block_results.items():
        for entry in result.get("source_list_entries", []):
            entry_with_block = dict(entry)
            entry_with_block.setdefault("block", key)
            source_list.append(entry_with_block)
        quality_flags.extend(result.get("quality_flags", []))
        pit_flags.extend(result.get("pit_flags", []))
        agent_notes.extend(result.get("agent_notes", []))
        api_calls_per_block[key] = result.get("api_calls_made", 0)

    # Add the always-on placeholders' source/PIT signals too. Step A3:
    # skip when the block is disabled by the toggle.
    if _enabled(BlockKey.FILING_CONFIRMATION):
        source_list.append({
            "block": BlockKey.FILING_CONFIRMATION,
            "label": "sec_8k", "source": Source.NOT_CONNECTED,
            "url": None, "as_of": None,
        })
    if _enabled(BlockKey.NARRATIVE_PRICE_GAP):
        source_list.append({
            "block": BlockKey.NARRATIVE_PRICE_GAP,
            "label": "agent_internal", "source": "agent_internal_pending",
            "url": None, "as_of": None,
        })

    # ── 4. Look-ahead re-check (every block's as_of <= cutoff) ──
    cutoff_iso = decision_dt_et.isoformat()
    cutoff_dt_utc = decision_dt_et.astimezone(timezone.utc)
    # Run the check against the data blocks only (envelope, source_list,
    # decision_time_discipline carry their own timestamps by design).
    data_blocks = {k: v for k, v in packet.items()
                   if k not in (BlockKey.ENVELOPE,
                                BlockKey.DECISION_TIME_DISCIPLINE,
                                BlockKey.SOURCE_LIST,
                                BlockKey.DATA_QUALITY_FLAGS,
                                BlockKey.PIT_SAFETY_FLAGS,
                                BlockKey.AGENT_READY_NOTES)}
    lookahead_safe, violations = _check_lookahead(data_blocks, cutoff_dt_utc)

    envelope["data_after_cutoff_used"] = bool(violations)
    envelope["lookahead_safe"] = lookahead_safe
    envelope["lookahead_violations"] = violations
    dtd_block = packet.get(BlockKey.DECISION_TIME_DISCIPLINE)
    if isinstance(dtd_block, dict):
        dtd_block["data_after_cutoff_used"] = bool(violations)
        dtd_block["lookahead_safe"] = lookahead_safe

    if not lookahead_safe:
        for v in violations:
            quality_flags.append({
                "kind": "lookahead_violation",
                "severity": "critical",
                "detail": (f"block {v['block']} reports as_of "
                            f"{v['as_of']} > cutoff {v['cutoff_utc']}"),
            })

    # Strict-PIT replay mode (Task 7, 2026-04-29 PM): when the caller
    # opts into strict mode, any cutoff violation aborts packet generation
    # rather than emitting a flagged packet. The default path (False)
    # is byte-identical to pre-Task-7 behaviour and is what the regression
    # matrix exercises. The pit_mode envelope stamp is only added when the
    # caller explicitly opts in, so omitting strict_pit_mode keeps the
    # envelope byte-identical to the frozen baseline.
    if strict_pit_mode:
        envelope["pit_mode"] = "replay_strict_pit"
        if not lookahead_safe:
            raise PITViolationError(
                f"Strict-PIT replay mode aborted: {len(violations)} "
                f"adapter row(s) report as_of > cutoff "
                f"{cutoff_iso}. See exception.violations for details.",
                violations=violations,
            )
        # Pass 8 Step B1.7 (2026-05-04): physically scrub forbidden
        # current-state field paths (live_quote, last_price, etc.) from
        # the packet so downstream replay-mode agents cannot read them.
        # Default-mode packets keep the fields tagged-present per the
        # existing live-mode contract; this strip is opt-in via
        # strict_pit_mode=True. Removed paths are recorded in the
        # envelope for audit. The regression matrix exercises the
        # default path (strict_pit_mode=False), so the §22 frozen
        # byte-identical hash is unaffected.
        from .hindsight_rules import scrub_forbidden_replay_paths
        # Scrub the data_blocks dict in-place. We do NOT scrub envelope
        # itself because envelope/source_list/quality_flags carry
        # paths-as-strings (e.g. in lookahead_violations) that would
        # match suffix patterns and create infinite-recursion-style
        # noise. Only block payloads contain raw current-state fields.
        _scrub_target = {k: v for k, v in packet.items()
                          if k not in (BlockKey.ENVELOPE, BlockKey.SOURCE_LIST,
                                       BlockKey.DATA_QUALITY_FLAGS,
                                       BlockKey.PIT_SAFETY_FLAGS,
                                       BlockKey.AGENT_READY_NOTES)}
        _, _removed = scrub_forbidden_replay_paths(_scrub_target)
        # The scrubber mutates in-place, so the original packet's block
        # dicts are also mutated (they're the same dict references).
        envelope["forbidden_paths_scrubbed"] = list(_removed)
        envelope["forbidden_paths_scrubbed_count"] = len(_removed)

    # ── 4b. Hindsight audit (R1-R6 per docs/HINDSIGHT_POLICY.md) ──
    # Audit only the data blocks; envelope/source_list/decision_time
    # carry their own meta and are inspected separately if at all.
    hindsight_report = run_hindsight_audit(
        {k: v for k, v in packet.items()
         if k not in (BlockKey.ENVELOPE, BlockKey.SOURCE_LIST,
                      BlockKey.DATA_QUALITY_FLAGS, BlockKey.PIT_SAFETY_FLAGS,
                      BlockKey.AGENT_READY_NOTES)},
        decision_mode=decision_mode,
        cutoff_dt_utc=cutoff_dt_utc,
    )
    envelope["hindsight_safe"] = hindsight_report["hindsight_safe"]
    envelope["hindsight_violations"] = hindsight_report["hindsight_violations"]
    envelope["records_removed_by_pit_filter"] = hindsight_report["records_removed_by_pit"]

    # Aggregate uses_current_state from blocks
    envelope["uses_current_state"] = any(
        isinstance(b, dict) and b.get("uses_current_state") is True
        for b in data_blocks.values()
    )

    if not hindsight_report["hindsight_safe"]:
        for v in hindsight_report["hindsight_violations"]:
            quality_flags.append({
                "kind": "hindsight_violation",
                "severity": "critical",
                "detail": f"{v.get('kind')} at {v.get('path')}: {v.get('remediation','')}",
            })

    # R6 — log the PIT-removal actions into source_list as audit trail
    for action in hindsight_report["hindsight_filter_actions"]:
        source_list.append({
            "block": "hindsight_filter",
            "label": "pit_row_removal",
            "source": "generator_internal",
            "action": action.get("action"),
            "block_field": action.get("block_field"),
            "count": action.get("count"),
            "cutoff_utc": action.get("cutoff_utc"),
        })

    # ── 5. Attach meta lists to the packet ──
    packet[BlockKey.SOURCE_LIST] = source_list
    packet[BlockKey.DATA_QUALITY_FLAGS] = quality_flags
    packet[BlockKey.PIT_SAFETY_FLAGS] = pit_flags
    packet[BlockKey.AGENT_READY_NOTES] = agent_notes

    # ── 5b. (Step D) optional alt-data + OpenCLI adapter wiring ──
    # When live_adapters is None (default) this block is a no-op and
    # the packet is byte-identical to the pre-Step-D output. When
    # explicitly requested, the wiring layer overlays adapter rows
    # onto the relevant blocks and writes packet["alt_data_manifest"].
    # The manifest is part of the hashable region (per R-ALTDATA-04).
    if live_adapters is not None and live_adapters is not False:
        from .adapter_wiring import apply_adapters
        apply_adapters(
            packet=packet,
            ticker=ticker,
            decision_timestamp=decision_dt_et.astimezone(timezone.utc),
            enabled_blocks=enabled_blocks_validated,
            live_adapters=live_adapters,
        )
        # Aggregate adapter data_quality_flags into the always-on list.
        manifest = packet.get("alt_data_manifest", {})
        for call in manifest.get("calls", []):
            ec = call.get("error_class")
            if ec:
                quality_flags.append({
                    "kind": "alt_data_adapter_error",
                    "severity": "warning",
                    "detail": (
                        f"adapter={call['source_id']} error_class={ec} "
                        f"extraction_status={call.get('extraction_status')}"
                    ),
                })

    # ── 6. Compute hash + locked_decision_id ──
    # Placeholder so build_telemetry is present at hash time and gets
    # masked to sentinel deterministically; real value populated in §7.
    packet["build_telemetry"] = None
    packet_hash = compute_evidence_packet_hash(packet)
    locked_id = compute_locked_decision_id(ticker, cutoff_iso, packet_hash)
    envelope["evidence_packet_hash"] = packet_hash
    envelope["locked_decision_id"] = locked_id
    if isinstance(dtd_block, dict):
        dtd_block["evidence_packet_hash_ref"] = packet_hash
        dtd_block["locked_decision_id_ref"] = locked_id

    # ── 7. Build telemetry (out-of-band; not hashed) ──
    extra_built: list[str] = []
    if isinstance(packet.get(BlockKey.DECISION_TIME_DISCIPLINE), dict):
        extra_built.append(BlockKey.DECISION_TIME_DISCIPLINE)
    if isinstance(packet.get(BlockKey.FILING_CONFIRMATION), dict):
        extra_built.append(BlockKey.FILING_CONFIRMATION)
    if isinstance(packet.get(BlockKey.NARRATIVE_PRICE_GAP), dict):
        extra_built.append(BlockKey.NARRATIVE_PRICE_GAP)
    packet["build_telemetry"] = {
        "api_calls_per_block": api_calls_per_block,
        "api_calls_total_for_packet": sum(api_calls_per_block.values()),
        "blocks_built": list(block_results.keys()) + extra_built,
        "build_finished_at_utc": _now_utc().isoformat(),
    }

    return packet
