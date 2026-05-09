"""
Agent runner / orchestrator.

The runner is the only place that:
  1. derives the cache key from (agent, model, prompt_version, ticker, ts, packet_hash)
  2. looks up the cache
  3. calls the LLMProvider
  4. validates the response against the agent's pydantic schema
  5. writes the cache record
  6. wires upstream agent outputs into Risk/PM's input packet

Logging discipline (Step 5 spec): we log prompt_version + cache_key + a
short summary, never the full prompt body. Full prompts and packets
remain in the cached record for audit/replay.
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Literal, Optional

import csv
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ._failclosed import (
    FAILCLOSED_VALIDATION_STATUS,
    build_failclosed_output,
)
from ._json_parse import LLMJsonParseError, parse_llm_json
from .prompts import AGENT_PROMPTS
from .schemas import SCHEMA_REGISTRY, validate_agent_output
from src.altdata.ndi import compute_ndi
from src.evidence_packet.hash_utils import (
    compute_evidence_packet_hash,
    compute_locked_decision_id,
)
from src.llm.cache import LLMCache, build_cache_key, make_record
from src.llm.factory import get_provider
from src.llm.provider import LLMProvider


# ── Prompt-cache padding (D4, RULES.md §22 doc v1.5) ─────────────────
#
# The Anthropic prompt cache (set by anthropic_provider.py via
# `cache_control: {type: "ephemeral"}` on the system block) requires the
# cached block to clear a per-model minimum token threshold. For
# claude-haiku-4-5-20251001 the threshold is 2048 input tokens; below it
# the request still succeeds but cache_creation_input_tokens stays 0 and
# cache_read_input_tokens never goes positive on subsequent calls. The
# in-tree per-agent SYSTEM_PROMPT strings are ~500 tokens each, so by
# themselves they never qualify.
#
# The fix here is to APPEND a stable padding block when we send the
# system prompt to the provider. The padding is verbatim excerpts from
# RULES.md (the project's single source of truth — stable across runs,
# never personalized per call). Padding is added inside the runner, not
# inside each prompt file, so:
#   1. The on-disk SYSTEM_PROMPT strings stay unchanged (no PROMPT_VERSION
#      bumps required per RULES.md §14.1).
#   2. The on-disk LLM cache_key stays unchanged (it depends on
#      PROMPT_VERSION, not the body bytes).
#   3. The Anthropic prompt cache sees a long, stable system block and
#      now successfully creates / reads cache.
#
# Estimated size: ~7000 chars ≈ ~1750 tokens (Anthropic's char/4
# heuristic). Combined with a ~500-token agent SYSTEM_PROMPT, the
# cached block totals ~2250 tokens — clear of the 2048 threshold with
# margin. Print _PROMPT_CACHE_PADDING_TOKEN_ESTIMATE on import for
# operators.
_PROMPT_CACHE_PADDING = """

────────────────────────────────────────────────────────────────────
PROJECT-LEVEL RULE EXCERPTS (RULES.md, single-source-of-truth padding;
included here so the system block clears the prompt-cache threshold —
Haiku 4.5 minimum 2048 tokens. Reference only; do not echo back.)
────────────────────────────────────────────────────────────────────

§11 ALT-DATA SOURCE RULES (selected)
- §11.1 Every feature carries a timestamp (`as_of` or implicit via
  `accepted_datetime` / `filing_date` / `publication_timestamp`).
- §11.2 Missing data is `Data unavailable` (or `not_evaluated`), never
  zero. Zero is a real zero.
- §11.3 Every feature carries a source flag in
  {live_<src>, cache, mock_fallback, live_<src>_failed}. mock_fallback
  rows are PERMITTED at decision time but the agent MUST NOT treat them
  as ground truth — they appear in `data_quality_flags` and downgrade
  `evidence_sufficiency` accordingly. Strict missing data (no row at
  all, even mock_fallback) is `Data unavailable / not_evaluated`.
- §11.5 No feature may inject look-ahead. Retroactive "filing exists"
  check at decision time T must use only filings whose
  `filing_date <= T`.
- §11.6 Adapter outputs are evidence DESCRIPTORS, not trading rules.
  No "GitHub commits up = buy", "patent count high = buy", "Phase 2
  enrollment on track = buy".
- §11.7 Agent interprets evidence in context. Two tickers with
  identical adapter rows can deserve different decisions.
- §11.13 Sentiment / community / ownership are descriptor-only layers.
  None of "hedge fund owns it = buy", "hedge fund sold it = short",
  "high institutional ownership = safe", "low ownership = bad" allowed.
- §11.14 13F / ownership PIT enforcement: when ownership_positioning
  data_available=true, accepted_datetime MUST be set, MUST be ≤
  allowed_data_cutoff, and PIT_safety_notes MUST explain the lag.

§12 EVIDENCE PACKET (selected)
- §12.1 12 canonical block IDs: price_snapshot, macro_regime,
  fundamental_snapshot, valuation_snapshot, news_event_summary,
  corporate_calendar, alternative_data_features,
  information_integrity_assessment,
  sentiment_community_ownership_evidence, decision_time_discipline,
  filing_confirmation, narrative_price_gap_assessment.
- §12.2 Always-on (NOT toggleable): envelope, source_list,
  data_quality_flags, PIT_safety_flags, agent_ready_notes,
  build_telemetry.
- §12.5 Block-level `status` enum (REQUIRED on every block):
  ok | data_unavailable | not_evaluated | insufficient_evidence | stale.
- §12.6 Anti-hindsight envelope fields (REQUIRED): lookahead_safe,
  hindsight_safe, hindsight_violations, universe_pit_validated,
  uses_current_state.
- §12.7 Packet hash policy: sha256 of canonicalized JSON AFTER
  stripping wall-clock / cache-flag fields. Two consecutive runs at
  same decision_timestamp and warm cache produce same hash.
- §12.8 locked_decision_id = sha256(ticker | decision_timestamp |
  evidence_packet_hash).
- §12.10 Forbidden-fields list (replay): profile.isActivelyTrading,
  profile.delistedDate, price_snapshot.live_quote / last_price /
  last_quote_timestamp, valuation_snapshot.DCF_*, any leaf prefixed
  current_*. Live mode tags any current-state field with
  uses_current_state: true instead of removing it.
- §12.11 Corporate-calendar block (REQUIRED): every packet emits it;
  FMP returns nothing → status=data_unavailable (never silently
  dropped). Block-level as_of clamped to cutoff. Near-event window
  (±5 trading days) is informational, not a trade trigger.

§13 AGENT PROMPT MANDATORY CLAUSES
- §13.1 Read-only clause: information present in the evidence packet
  only. No external knowledge about future events involving this
  ticker.
- §13.2 Anti-hindsight clause: training data may include events that
  occurred AFTER the decision_timestamp. MUST NOT use such information.
- §13.3 Output-format clause: valid JSON matching the agent's schema.
  No prose before or after the JSON. No markdown code fences. The JSON
  must parse on the first attempt.
- §13.4 Fail-closed clause: when evidence is insufficient, agent emits
  the needs_more_evidence-equivalent value for its decision field with
  non-empty evidence_missing.
- §13.5 Alt-data-emphasis clause: alt-data is the project's primary
  verification mechanism; weight it appropriately; flag absence and
  lower confidence.
- §13.6 Null-block-handling clause (R7): if a block in the evidence
  packet is null, treat as ABSENT; no inference / hallucination /
  external substitution; declare absence in reasoning; reduce
  confidence; if essential block absent, fail-closed.
- §13.9 Forbidden phrasings: "use your best judgment", "act as an
  experienced trader" — these delegate guardrails away from the rule
  engine.

§15 CACHE KEY
- §15.1 cache_key = sha256(agent_name | model_id | prompt_version |
  ticker | decision_timestamp | evidence_packet_hash). Pipe-delimited
  string is canonical and human-reproducible.
- §15.5 candidate_type extension: build_cache_key is unchanged. The
  runner extends the key for {risk_pm, risk, pm, baseline_solo,
  pm_flat} by passing
  evidence_packet_hash|candidate_type=<value> as the
  evidence_packet_hash argument. The canonical pipe-delimited payload
  for these agents expands to 7 pipe-separated tokens. The
  key_components audit dict stores the original packet hash and adds
  a separate candidate_type field.
- §15.6 Specialists' cache key is unchanged across pipeline / flat
  topologies; the same cached record serves both modes.
- §15.7 pm_flat cache namespace separate from risk_pm (different
  agent_name + different prompt_version).

§16 COORDINATION TOPOLOGY
- §16.1 Three orthogonal axes: agent_mode ∈ {multi, solo}, topology ∈
  {pipeline, flat, solo}, enabled_blocks ⊆ 12 canonical IDs.
  Defaults: multi / pipeline / all 12 enabled.
- §16.2 When agent_mode='solo': topology forced to 'solo';
  agent_outputs contains exactly one key (baseline_solo);
  final_decision = baseline_solo's parsed output.
- §16.3 When agent_mode='multi' + topology='pipeline': 8 entries
  (5 prelude + 1 sleeve + risk + pm); risk and pm see
  upstream_agent_outputs.
- §16.4 When agent_mode='multi' + topology='flat': 6 specialists run
  in parallel against the raw packet (no cross-talk); pm_flat
  aggregates parallel verdicts under specialist_outputs.

§19 META-GOVERNANCE (selected)
- §19.4 LLMs may suggest improvements but cannot silently change rules
  / code / risk limits / trading decisions.
- §19.6 No LLM call may execute orders / place trades / talk to a
  broker / change cache state / rotate API keys.
- §19.7 The rule engine is the source of truth for guardrails. No LLM
  overrides the four canonical decision modes, T+1 execution,
  surge-short ladder, sleeve caps, no-margin / no-derivatives /
  no-crypto, missing-data policy, or v0.6 5-regime allocation.
- §19.11 Forbidden agent behaviors include: silently changing trading
  rules, modifying any code file, changing risk limits, overriding
  guardrails, treating unfetched web sources as authority, converting
  research claim to hard-coded conclusion, trading / placing orders /
  holding positions / marking-to-market / settling outside official
  backtest engine, rotating API keys / clearing caches / changing rate
  limits, sending outbound traffic outside whitelisted endpoints.

End of project-level rule excerpts. Resume role-specific reasoning.
"""

# Char/4 heuristic — Anthropic's published rough estimate for English
# input. Not exact; the actual count comes back in usage.input_tokens
# on every call.
_PROMPT_CACHE_PADDING_TOKEN_ESTIMATE = len(_PROMPT_CACHE_PADDING) // 4


def _pad_for_prompt_cache(system_prompt: str) -> str:
    """Append the stable RULES.md padding so the system block clears
    Haiku 4.5's prompt-cache minimum (2048 tokens). The padding is the
    same string for every call; Anthropic's prompt cache will hit on
    repeated calls within the 5-minute ephemeral TTL."""
    return system_prompt + _PROMPT_CACHE_PADDING

logger = logging.getLogger(__name__)

# Default model id used when the provider does not override.
DEFAULT_MODEL_ID = "stub-v1"


def _effective_model_id(provider: LLMProvider, agent_name: str) -> str:
    """Pick the model id used for both the cache key and the provider call.

    Stub provider: keep "stub-v1" so the Step A6 regression baseline is
    byte-identical (the cache key is a function of model_id).

    Anthropic provider: use the provider's default model. Per-agent
    overrides can be added later by reading `prompt_mod.PREFERRED_MODEL`
    or similar; today every agent runs at the provider default.
    """
    if provider.name == "anthropic":
        # Anthropic's default lives on the instance; fall back to its
        # module constant if a future build constructs it differently.
        return getattr(provider, "_default_model", "claude-sonnet-4-6")
    return DEFAULT_MODEL_ID

# §10.14 split (2026-04-29): the unified fund_net_val + risk_pm
# pipeline is replaced by independent split agents:
#   prelude = candidate-type-specific (see below)
#   sleeve  = surge_short OR quality_long (per candidate_type)
#   final   = risk → pm   (Risk advises; PM decides; §10.14 R-COVER-*)
#
# Risk consumes upstream_agent_outputs of the prelude+sleeve. PM
# consumes upstream_agent_outputs INCLUDING Risk's output.
#
# §5.17 candidate-type-specific prelude split (Chunk B, 2026-05-02):
#
#   surge_short prelude (3 agents): narrative_event, alt_data_verify,
#       fundamental. Pipeline = 3 prelude + [sleeve, risk, pm] = 6
#       agents total. EXCLUDED: network_effect, valuation. Rationale:
#       surge-short is event-driven on small/micro-caps where
#       network-effect classification is not meaningful and valuation
#       is dominated by short-horizon catalyst dynamics; fundamental
#       remains in-scope to evaluate hard_gates_pass for the bear
#       thesis.
#
#   quality_long prelude (4 agents): narrative_event, alt_data_verify,
#       network_effect, valuation. Pipeline = 4 prelude + [sleeve,
#       risk, pm] = 7 agents total. EXCLUDED: fundamental. Rationale:
#       large-cap fundamentals are transparent and pre-known; the
#       quality_long sleeve agent + valuation handle the "good company
#       at reasonable price" gating directly without a separate
#       fundamental verdict.
#
# macro_regime is rule-based (no LLM call) and is NOT in either
# prelude tuple — it lives in the evidence packet via blocks/macro.py.
#
# PIPELINE_PRELUDE retained as a backward-compat alias for external
# callers; defaults to the surge_short tuple (the smaller, more
# common path). New code should call _select_prelude(candidate_type).
# Pass 8 §16.7: surge_short prelude is narrative_event + alt_data_verify
# only. fundamental_agent is REMOVED (the §2.1 EPS<0+OM<0 gate is mechanical
# at filter time per surge_short_rules.filter_surge_candidates).
# valuation_agent never was in the surge prelude (P/E undefined for EPS<0).
# network_effect_agent likewise — §2.12 §10.15-verified-classification check
# is a mechanical lookup against prior packets.
PIPELINE_PRELUDE_SURGE = (
    "narrative_event",
    "alt_data_verify",
)
# Pass 8 §16.7: quality_long prelude unchanged (fundamental_agent already
# absent — §3.8 hard gate is mechanical at filter time).
PIPELINE_PRELUDE_QUALITY = (
    "narrative_event",
    "alt_data_verify",
    "network_effect",
    "valuation",
)
PIPELINE_PRELUDE = PIPELINE_PRELUDE_SURGE  # backward-compat alias
RISK_AGENT = "risk"
PM_AGENT = "pm"
# Legacy unified aggregator name retained as an alias for any external
# caller still referencing it. NOT in the default pipeline.
RISK_PM = "risk_pm"


def _select_prelude(candidate_type: str) -> tuple[str, ...]:
    """Return the prelude tuple for a given candidate_type per §5.17
    surge-short integrity-veto exception architecture (Chunk B). Raises
    ValueError on unknown candidate_type so a typo surfaces immediately
    rather than silently selecting the wrong agent set."""
    if candidate_type == "surge_short":
        return PIPELINE_PRELUDE_SURGE
    if candidate_type == "quality_long":
        return PIPELINE_PRELUDE_QUALITY
    raise ValueError(
        f"_select_prelude: unknown candidate_type={candidate_type!r}; "
        f"expected 'surge_short' or 'quality_long'"
    )

# Agents whose output (or cache reuse) genuinely depends on candidate_type
# and therefore must include candidate_type in the cache key. Step A1 fix
# for the cross-type collision flagged in docs/LLM_INTEGRATION_LAYER.md §2:
# the final decider produces a candidate_type-specific decision, so its
# cache record must NOT be reused across types. Per §10.14 split, BOTH
# the new `pm` and the legacy `risk_pm` qualify; `risk` itself also
# produces candidate_type-specific advisory output.
CANDIDATE_TYPE_DEPENDENT_AGENTS = frozenset({
    "risk_pm",       # legacy unified — retained for backward compat
    "risk",          # §10.14 split — advisory output is candidate-type-specific
    "pm",            # §10.14 split — final decision is candidate-type-specific
    "baseline_solo",
    "pm_flat",
})


# ── Pass 8 (2026-05-04) §31/§32/§33 helpers ──────────────────────────
#
# Mandatory-short bypass + score-band override are wired into
# run_all_agents_for_candidate. Helpers live here so they are unit-testable
# without spinning up the full orchestrator.

def _compute_surge_pct_from_packet(evidence_packet: dict) -> Optional[float]:
    """Pass 8 §32.1 surge_pct = (trigger_day_open - last_eod_close) / last_eod_close.

    Scope 4 fix 2026-05-06: numerator reads price_snapshot.trigger_day_open
    (PIT-guarded 9:30 ET open of the decision-day's regular session,
    populated by src/evidence_packet/blocks/price.py only when the cutoff
    is in [09:30, 16:15) ET on the bar's date). Denominator reads
    last_eod_close — under the §6.6 standard 16:00 ET cutoff this is the
    prior trading day's close (the trigger-day EOD bar is filtered out by
    the cutoff filter, so history[-1] is the prior trading day).

    Returns None when either field is missing — caller treats None as
    'cannot determine surge' and routes to the 60-90% pool (the safer
    default — never auto-mandatory-short).
    """
    ps = evidence_packet.get("price_snapshot") or {}
    today_open = ps.get("trigger_day_open")
    prior_close = ps.get("last_eod_close")
    if today_open is None or prior_close is None:
        return None
    try:
        prior_close = float(prior_close)
        today_open = float(today_open)
    except (TypeError, ValueError):
        return None
    if prior_close <= 0:
        return None
    return (today_open - prior_close) / prior_close


def _candidate_dict_from_packet(evidence_packet: dict) -> dict:
    """Project the evidence packet into the dict shape expected by
    src.rules.surge_short_rules.is_mandatory_short_eligible. We pull the
    fields that affect §32.2 gates (a-i)."""
    ps = evidence_packet.get("price_snapshot") or {}
    fs = evidence_packet.get("fundamental_snapshot") or {}
    cc = evidence_packet.get("corporate_calendar") or {}
    # Bug X3 fix 2026-05-06: change_pct is nested in price_snapshot.live_quote
    # per src/evidence_packet/blocks/price.py:321; the top-level lookup was
    # silently returning None and starving the §32.2 mechanical gate eval.
    _live_quote = ps.get("live_quote") or {}
    out: dict = {
        "change_pct": _live_quote.get("change_pct"),
        "volume": ps.get("volume"),
        "prior_close": ps.get("prior_close"),
        "security_type": ps.get("security_type"),
        "eps_ttm": fs.get("eps_ttm"),
        "operating_margin_ttm": fs.get("operating_margin_pct"),
        "earnings_within_5_trading_days": bool(
            cc.get("earnings_within_5_trading_days", False)
        ),
        "earnings_proximate": bool(
            cc.get("earnings_within_5_trading_days", False)
        ),
        # cumulative_10d_decline_pct + baseline_exclusion_id should be
        # populated by the candidate ranker upstream; missing = 0/None.
        "cumulative_10d_decline_pct": ps.get("cumulative_10d_decline_pct"),
        "baseline_exclusion_id": evidence_packet.get("baseline_exclusion_id"),
    }
    return out


def _build_synthetic_pm_for_mandatory_short(
    *,
    evidence_packet: dict,
    surge_pct: float,
    gate_results: dict,
) -> dict:
    """§32.3 — bypass LLM PM and emit a synthetic PM record for a
    mandatory-short execution. Contains the §32.5 audit trail (gate
    booleans, surge_pct, llm_calls_bypassed list, mandatory_short flag).

    The synthetic record is dict-shaped (NOT pydantic-validated) because
    it is generated by the rule engine, not by an LLM call. Downstream
    code that reads `final_decision` should tolerate decision='short' +
    score_threshold_band='mandatory_short'.
    """
    meta = _packet_metadata(evidence_packet)
    return {
        "agent_id": "pm__pass8_mandatory_short_bypass",
        "candidate_type": "surge_short",
        "decision": "short",
        "position_size_pct": 0.005,
        "reason": (
            f"§31 mandatory-short triggered: surge_pct={surge_pct:.4f} "
            f">= 0.90 AND all §32.2 gates passed. LLM pipeline bypassed."
        ),
        "short_conviction_score": None,
        "score_components": {},
        "score_threshold_band": "mandatory_short",
        "pm_override_reason": None,
        "veto_conditions_evaluated": [],  # mechanical §5.13 checks done by caller
        "decision_log": [
            "§31 mandatory_short_triggered=True",
            f"surge_pct={surge_pct:.4f}",
            f"§32.2 gate_results={gate_results}",
            "llm_calls_bypassed=['narrative_event','alt_data_verification','risk','pm']",
        ],
        "ust_actions": [],
        # Pass 8 audit trail per §32.5
        "pass8_audit": {
            "mandatory_short_triggered": True,
            "surge_pct": surge_pct,
            "gate_results": gate_results,
            "llm_calls_bypassed": [
                "narrative_event", "alt_data_verification", "risk", "pm",
            ],
            "decision_timestamp": meta["decision_timestamp"],
        },
    }


def _apply_score_band_override(
    pm_output: dict,
) -> dict:
    """§33.3 — rule-engine override on PM-emitted score_threshold_band.

    Pass 8: when band='mandatory_short' the rule engine forces
    decision='short' / position_size_pct=0.005 (PM watch/no_trade is
    OVERRIDDEN). When band='mandatory_no_trade' the rule engine forces
    decision='no_trade'. Other bands ('strong_tilt','discretion') leave
    PM's emitted decision unchanged. PM's pm_override_reason is required
    when band='strong_tilt' AND PM did not emit short — we annotate but
    do not enforce here (validation belongs in PM prompt + audit, not in
    the rule engine).
    """
    if not isinstance(pm_output, dict):
        return pm_output
    band = (pm_output.get("score_threshold_band") or "").strip().lower()
    if band == "mandatory_short":
        if pm_output.get("decision") != "short":
            pm_output.setdefault("decision_log", []).append(
                f"§33.3 RULE-ENGINE OVERRIDE: PM emitted "
                f"decision={pm_output.get('decision')!r} but band='mandatory_short'; "
                f"forcing decision='short', position_size_pct=0.005."
            )
        pm_output["decision"] = "short"
        pm_output["position_size_pct"] = 0.005
    elif band == "mandatory_no_trade":
        if pm_output.get("decision") != "no_trade":
            pm_output.setdefault("decision_log", []).append(
                f"§33.3 RULE-ENGINE OVERRIDE: PM emitted "
                f"decision={pm_output.get('decision')!r} but band='mandatory_no_trade'; "
                f"forcing decision='no_trade'."
            )
        pm_output["decision"] = "no_trade"
        pm_output["position_size_pct"] = 0.0
    return pm_output


# ── PM JSON failure forensics (D5 task 1.1; per-attempt logging) ─────
_PM_JSON_FAILURES_CSV = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "altdata" / "pm_json_failures.csv"
)
_PM_JSON_FAILURES_HEADER = [
    "timestamp_utc",
    "ticker",
    "agent_name",
    "prompt_version",
    "attempt_number",
    "error_class",
    "error_message_first_200_chars",
    "raw_response_first_500_chars",
    "final_outcome",
]

# §5.12 retry cap = 2 retries → 3 attempts total.
_MAX_JSON_PARSE_ATTEMPTS = 3

# Prompt clarifiers appended on retry. The body of these strings is
# stable across runs (no per-call interpolation) so the cache_key — which
# the runner derives from prompt_version, NOT from the raw text — is
# unchanged.
_RETRY_CLARIFIER_1 = (
    "\n\nIMPORTANT: Your previous response was not valid JSON. "
    "Return ONLY a JSON object that matches the output schema above. "
    "Do not wrap it in markdown fences, do not add commentary before or "
    "after, and do not include trailing commas."
)
_RETRY_CLARIFIER_2 = (
    "\n\nThis is your final attempt. If you cannot produce valid JSON, "
    "return the cautious fail-closed envelope: "
    '{"decision_or_assessment": "needs_more_evidence", '
    '"confidence": "low"} and the rest of the schema fields filled with '
    "their most cautious legal values."
)


def _log_pm_attempt(
    *,
    ticker: str,
    agent_name: str,
    prompt_version: str,
    attempt_number: int,
    error_class: str,
    error_message: str,
    raw_response: str,
    final_outcome: str,
) -> None:
    """Append one row per attempt (success or failure) to
    data/altdata/pm_json_failures.csv. Best-effort: OSError must not
    crash the run."""
    try:
        _PM_JSON_FAILURES_CSV.parent.mkdir(parents=True, exist_ok=True)
        write_header = not _PM_JSON_FAILURES_CSV.exists()
        with _PM_JSON_FAILURES_CSV.open(
            "a", newline="", encoding="utf-8"
        ) as f:
            w = csv.DictWriter(f, fieldnames=_PM_JSON_FAILURES_HEADER)
            if write_header:
                w.writeheader()
            w.writerow({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "ticker": ticker,
                "agent_name": agent_name,
                "prompt_version": prompt_version,
                "attempt_number": attempt_number,
                "error_class": error_class,
                "error_message_first_200_chars": (error_message or "")[:200],
                "raw_response_first_500_chars": (raw_response or "")[:500],
                "final_outcome": final_outcome,
            })
    except OSError as e:
        logger.warning("pm_json_failures.csv append failed: %s", e)


def _envelope_field(packet: dict, name: str) -> Any:
    """Read a field from packet.envelope.* (the packet's canonical
    metadata block). Falls back to top-level packet[name] for flexibility."""
    env = packet.get("envelope") or {}
    if name in env:
        return env[name]
    return packet.get(name)


def _packet_metadata(packet: dict) -> dict:
    """Pull (ticker, decision_timestamp, evidence_packet_hash) — the
    three packet-side inputs to the cache key."""
    return {
        "ticker": _envelope_field(packet, "ticker") or "",
        "decision_timestamp": _envelope_field(packet, "decision_timestamp") or "",
        "evidence_packet_hash":
            _envelope_field(packet, "evidence_packet_hash") or "",
    }


def _render_user_prompt(template: str, *, evidence_packet: dict,
                        output_schema_json: str) -> str:
    """Substitute {{evidence_packet_json}} and {{output_schema_json}}.

    We use str.replace rather than str.format because the rendered
    JSON contains many literal braces and would clash with format()'s
    placeholder syntax.
    """
    return (
        template
        .replace(
            "{{evidence_packet_json}}",
            json.dumps(evidence_packet, ensure_ascii=False, indent=2, default=str),
        )
        .replace("{{output_schema_json}}", output_schema_json)
    )


def run_agent(
    *,
    agent_name: str,
    evidence_packet: dict,
    candidate_type: Optional[Literal["surge_short", "quality_long"]] = None,
    provider: Optional[LLMProvider] = None,
    cache: Optional[LLMCache] = None,
    force_refresh: bool = False,
) -> dict:
    """Run a single agent end-to-end.

    Returns the canonical cache record (cache_key, key_components,
    raw_response, parsed_output, validation_status, schema_version,
    created_at). On a cache hit (force_refresh=False) the on-disk
    record is returned as-is.

    Schema validation failure is captured: parsed_output is the
    synthetic fail-closed envelope, validation_status =
    "schema_failed_returned_needs_more_evidence", error message is in
    parsed_output["reasoning_summary"].
    """
    if agent_name not in AGENT_PROMPTS:
        raise KeyError(f"Unknown agent_name: {agent_name}. "
                        f"Allowed: {sorted(AGENT_PROMPTS)}")

    prompt_mod = AGENT_PROMPTS[agent_name]
    schema_name = prompt_mod.OUTPUT_SCHEMA_NAME
    prompt_version = prompt_mod.PROMPT_VERSION

    if provider is None:
        provider = get_provider()
    if cache is None:
        cache = LLMCache()

    meta = _packet_metadata(evidence_packet)
    model_id = _effective_model_id(provider, agent_name)

    # Step A1: Risk/PM (and baseline_solo, Step A2) emit a candidate_type-
    # specific final decision. For these agents we extend the cache key with
    # candidate_type so that a surge_short record is never served to a
    # quality_long request. All other specialists keep their existing cache
    # key formula unchanged — their inputs (the evidence packet) and prompts
    # do not branch on candidate_type, so the packet hash already covers
    # them.
    needs_candidate_type = agent_name in CANDIDATE_TYPE_DEPENDENT_AGENTS
    if needs_candidate_type and not candidate_type:
        raise ValueError(
            f"agent_name={agent_name!r} requires candidate_type to derive "
            f"its cache key; got candidate_type={candidate_type!r}."
        )
    extended_packet_hash = (
        f"{meta['evidence_packet_hash']}|candidate_type={candidate_type}"
        if needs_candidate_type
        else meta["evidence_packet_hash"]
    )

    cache_key = build_cache_key(
        agent_name=agent_name,
        model_id=model_id,
        prompt_version=prompt_version,
        ticker=meta["ticker"],
        decision_timestamp=meta["decision_timestamp"],
        evidence_packet_hash=extended_packet_hash,
    )
    key_components = {
        "agent_name": agent_name,
        "model_id": model_id,
        "prompt_version": prompt_version,
        "ticker": meta["ticker"],
        "decision_timestamp": meta["decision_timestamp"],
        "evidence_packet_hash": meta["evidence_packet_hash"],
    }
    if needs_candidate_type:
        key_components["candidate_type"] = candidate_type

    if not force_refresh:
        hit = cache.get(agent_name, cache_key)
        if hit is not None:
            logger.info(
                "agent=%s cache=HIT key=%s prompt_version=%s",
                agent_name, cache_key, prompt_version,
            )
            hit["__cache_event__"] = "hit"
            return hit

    logger.info(
        "agent=%s cache=MISS key=%s prompt_version=%s -> calling provider=%s",
        agent_name, cache_key, prompt_version, provider.name,
    )

    # Build user prompt — schema_json from pydantic
    schema_cls = SCHEMA_REGISTRY[schema_name]
    schema_json_str = json.dumps(schema_cls.model_json_schema(), indent=2)
    user_prompt = _render_user_prompt(
        prompt_mod.USER_PROMPT_TEMPLATE,
        evidence_packet=evidence_packet,
        output_schema_json=schema_json_str,
    )

    def _do_provider_call(prompt_text: str) -> dict:
        return provider.complete(
            system_prompt=_pad_for_prompt_cache(prompt_mod.SYSTEM_PROMPT),
            user_prompt=prompt_text,
            model_id=model_id,
            max_tokens=16384,
            temperature=0.0,
            response_format="json_object",
            agent_schema_name=schema_name,
        )

    # ── LLMJsonParseError bounded-retry policy (D5 task 1.1) ─────────
    # PM observed a ~20% JSON-parse failure rate during the D4 5-day
    # mini-smoke (long output occasionally produced a missing comma at
    # ~26KB). §5.12 caps retries at 2 → 3 attempts total. On retry we
    # append a clarifier asking for valid JSON only; on the final
    # attempt we additionally instruct the model to emit the cautious
    # fail-closed envelope. Per-attempt rows go to
    # data/altdata/pm_json_failures.csv for forensics.
    #
    # The deterministic stub provider can never produce invalid JSON
    # (its output is built from build_stub_skeleton then json.dumps'd),
    # so we explicitly skip the retry path for it. This both saves
    # cycles in tests and makes the stub behaviour identical to the
    # pre-retry implementation — any LLMJsonParseError from the stub
    # would indicate a programming error in the skeleton builder, not a
    # transient generation glitch.
    is_stub_provider = (getattr(provider, "name", "") == "deterministic_stub")
    if is_stub_provider:
        max_attempts = 1
    else:
        max_attempts = _MAX_JSON_PARSE_ATTEMPTS

    attempt_errors: list[tuple[int, LLMJsonParseError]] = []
    raw_response: dict = {}
    payload: Optional[dict] = None
    parsed_output: Optional[dict] = None
    validation_status: str = ""

    for attempt_number in range(1, max_attempts + 1):
        if attempt_number == 1:
            attempt_prompt = user_prompt
        elif attempt_number == 2:
            attempt_prompt = user_prompt + _RETRY_CLARIFIER_1
        else:
            attempt_prompt = (
                user_prompt + _RETRY_CLARIFIER_1 + _RETRY_CLARIFIER_2
            )

        raw_response = _do_provider_call(attempt_prompt)
        raw_text = raw_response.get("raw_text", "") or ""
        try:
            payload = parse_llm_json(raw_text)
        except LLMJsonParseError as e:
            attempt_errors.append((attempt_number, e))
            is_last = attempt_number == max_attempts
            outcome = "exhausted" if is_last else "failed_will_retry"
            _log_pm_attempt(
                ticker=meta.get("ticker", ""),
                agent_name=agent_name,
                prompt_version=prompt_version,
                attempt_number=attempt_number,
                error_class=type(e).__name__,
                error_message=str(e),
                raw_response=raw_text,
                final_outcome=outcome,
            )
            log_fn = logger.error if is_last else logger.warning
            log_fn(
                "agent=%s LLMJsonParseError on attempt %d/%d: %s",
                agent_name, attempt_number, max_attempts, e,
            )
            continue
        else:
            if attempt_number > 1:
                _log_pm_attempt(
                    ticker=meta.get("ticker", ""),
                    agent_name=agent_name,
                    prompt_version=prompt_version,
                    attempt_number=attempt_number,
                    error_class="",
                    error_message="",
                    raw_response=raw_text,
                    final_outcome=f"recovered_attempt_{attempt_number}",
                )
                logger.info(
                    "agent=%s JSON parse recovered on attempt %d/%d",
                    agent_name, attempt_number, max_attempts,
                )
            parsed_output, validation_status, _ = validate_agent_output(
                schema_name, payload
            )
            break
    else:
        # Loop exhausted — every attempt raised LLMJsonParseError.
        first_err = attempt_errors[0][1] if attempt_errors else None
        last_err = attempt_errors[-1][1] if attempt_errors else None
        error_summary = (
            f"json.loads error after {max_attempts} attempts; "
            f"first={first_err}; last={last_err}"
        )
        parsed_output = build_failclosed_output(schema_name, error_summary)
        validation_status = FAILCLOSED_VALIDATION_STATUS

    # Record retry telemetry into the cached record for audit.
    if attempt_errors and isinstance(parsed_output, dict):
        recovered_on = (
            attempt_errors[-1][0] + 1
            if (payload is not None and len(attempt_errors) < max_attempts)
            else None
        )
        parsed_output["_pm_json_retry"] = {
            "retry_attempted": True,
            "attempts_made": (
                recovered_on if recovered_on is not None else max_attempts
            ),
            "max_attempts": max_attempts,
            "recovered": payload is not None,
            "errors_by_attempt": [
                {"attempt": n, "error": str(e)} for n, e in attempt_errors
            ],
        }

    record = make_record(
        cache_key=cache_key,
        key_components=key_components,
        raw_response=dict(raw_response),
        parsed_output=parsed_output,
        schema_version="agent_output_v1",
        validation_status=validation_status,
    )
    cache.put(agent_name, cache_key, record)
    record["__cache_event__"] = "miss"
    return record


def _build_packet_with_upstream(
    base_packet: dict, upstream_outputs: dict[str, dict]
) -> dict:
    """Synthesize a packet that contains every upstream agent's
    parsed_output under upstream_agent_outputs. Used by Risk and PM
    in the §10.14 split pipeline (and by the legacy risk_pm alias).
    The Risk prompt sees prelude+sleeve outputs; the PM prompt sees
    prelude+sleeve+risk outputs."""
    p = copy.deepcopy(base_packet)
    p["upstream_agent_outputs"] = upstream_outputs
    return p


# Backward-compat alias for any external caller. New code should use
# _build_packet_with_upstream.
_build_packet_for_risk_pm = _build_packet_with_upstream


_PRIMARY_SIGNAL_FIELDS: dict[str, tuple[str, ...]] = {
    "narrative_event": ("catalyst_type", "evidence_sufficiency"),
    "alt_data_verify": ("recommendation_to_pm", "verdict"),
    "fundamental":     ("fundamental_assessment", "hard_gates_pass"),
    "network_effect":  ("classification",),
    "valuation":       ("valuation_assessment",),
    "quality_long":    ("recommended_action", "thesis_status"),
    "surge_short":     ("recommended_action", "thesis_status"),
    "risk":            ("information_integrity_veto",),
    "pm":              ("decision",),
}


def _build_disagreement_summary(
    agent_outputs: dict, final_decision: Optional[dict]
) -> dict:
    per_agent: list[dict] = []
    dispersion = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for name, out in agent_outputs.items():
        if not isinstance(out, dict):
            continue
        signal: Any = "unknown"
        for f in _PRIMARY_SIGNAL_FIELDS.get(name, ()):
            v = out.get(f)
            if v is None or v == "":
                continue
            signal = str(v).lower() if isinstance(v, bool) else v
            break
        conf_raw = out.get("confidence")
        conf = str(conf_raw).lower() if conf_raw else "unknown"
        dispersion[conf if conf in dispersion else "unknown"] += 1
        per_agent.append({"agent": name, "decision": signal, "confidence": conf})

    pm = agent_outputs.get(PM_AGENT) or final_decision or {}
    if not isinstance(pm, dict):
        pm = {}
    pm_decision = pm.get("decision") or "unknown"
    pm_conf_raw = pm.get("confidence")
    pm_confidence = str(pm_conf_raw).lower() if pm_conf_raw else "unknown"

    chain: Optional[dict] = None
    veto_conds = pm.get("veto_conditions_evaluated") or []
    tripped = [
        c.get("name")
        for c in veto_conds
        if isinstance(c, dict) and c.get("tripped") and c.get("name")
    ]
    if tripped:
        rule_path = pm.get("rule_engine_path") or []
        path_strs = [
            str(s) for s in rule_path if "tripped=true" in str(s).lower()
        ]
        chain = {
            "tripped_veto_conditions": tripped,
            "rule_engine_path_tripped": path_strs,
        }

    return {
        "pm_decision": pm_decision,
        "pm_confidence": pm_confidence,
        "per_agent_signals": per_agent,
        "confidence_dispersion": dispersion,
        "veto_authority_chain": chain,
    }


# ── ADaS persistence + t+1 lag (RULES.md §24) ─────────────────────────
#
# ADaS rows are appended to data/altdata/adas_timeseries.csv after the
# 7 non-PM agents complete (prelude + sleeve + risk = 5+1+1 = 7). The
# PM evidence packet then receives `adas_lagged` = the most-recent CSV
# row with timestamp < (current trigger - 1 trading day). PM is the
# ONLY agent that sees ADaS; per §25 isolation no specialist agent
# receives any ADaS field in its packet.

_ADAS_CSV_DEFAULT = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "altdata" / "adas_timeseries.csv"
)
_ADAS_CSV_HEADER = [
    "trigger_id", "timestamp", "ticker", "candidate_type", "num_agents",
    "num_dissenting_agents", "confidence_dispersion_high",
    "confidence_dispersion_medium", "confidence_dispersion_low",
    "confidence_dispersion_unknown", "confidence_dispersion_std_proxy",
    "veto_authority_chain_json", "per_agent_signals_json",
    "pm_agent_decision_seen", "rule_version",
]


def _adas_dispersion_std_proxy(disp: dict) -> float:
    """Approximate std of an ordinal {high, medium, low} confidence
    distribution. Encode high=2 / medium=1 / low=0 / unknown=excluded
    and compute population std. Returns 0.0 when fewer than 2 valid
    counts."""
    encode = {"high": 2.0, "medium": 1.0, "low": 0.0}
    samples: list[float] = []
    for k, v in (disp or {}).items():
        if k in encode and isinstance(v, int) and v > 0:
            samples.extend([encode[k]] * v)
    if len(samples) < 2:
        return 0.0
    mean = sum(samples) / len(samples)
    var = sum((s - mean) ** 2 for s in samples) / len(samples)
    return var ** 0.5


def _persist_adas_row(
    *, csv_path: Path, ticker: str, candidate_type: str,
    decision_timestamp: str,
    disagreement_summary: dict,
    rule_version: str,
) -> dict:
    """Append one ADaS row to the timeseries CSV. Returns the persisted
    row dict so callers can log it. Header is written on first write."""
    per_agent = disagreement_summary.get("per_agent_signals") or []
    disp = disagreement_summary.get("confidence_dispersion") or {}
    chain = disagreement_summary.get("veto_authority_chain") or None

    num_agents = len(per_agent)
    # Dissenting = count of agents whose decision string is not the
    # plurality decision among the 7. Best-effort; not load-bearing.
    decisions = [p.get("decision") for p in per_agent if p.get("decision")]
    if decisions:
        from collections import Counter  # local import to limit blast radius
        plurality, _ = Counter(decisions).most_common(1)[0]
        num_dissent = sum(1 for d in decisions if d != plurality)
    else:
        num_dissent = 0

    row = {
        "trigger_id": uuid.uuid4().hex[:16],
        "timestamp": decision_timestamp,
        "ticker": ticker,
        "candidate_type": candidate_type,
        "num_agents": num_agents,
        "num_dissenting_agents": num_dissent,
        "confidence_dispersion_high": int(disp.get("high", 0)),
        "confidence_dispersion_medium": int(disp.get("medium", 0)),
        "confidence_dispersion_low": int(disp.get("low", 0)),
        "confidence_dispersion_unknown": int(disp.get("unknown", 0)),
        "confidence_dispersion_std_proxy": round(
            _adas_dispersion_std_proxy(disp), 4
        ),
        "veto_authority_chain_json": json.dumps(
            chain, ensure_ascii=False, default=str
        ) if chain else "",
        "per_agent_signals_json": json.dumps(
            per_agent, ensure_ascii=False, default=str
        ),
        "pm_agent_decision_seen": "false",   # written BEFORE PM call
        "rule_version": rule_version,
    }

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_ADAS_CSV_HEADER)
        if write_header:
            w.writeheader()
        w.writerow(row)
    return row


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _read_lagged_adas(
    csv_path: Path, current_trigger_iso: str, *, lag_trading_days: int = 1,
) -> Optional[dict]:
    """Return the most-recent ADaS row whose timestamp is strictly
    before (current_trigger - lag_trading_days). 'Trading days' here is
    approximated as calendar days; a full trading-calendar filter is
    deferred. Per §24.4 the lag is one full trading day, so a calendar
    approximation is conservative (returns slightly older rows than a
    trading-calendar implementation would)."""
    if not csv_path.exists():
        return None
    cur = _parse_iso(current_trigger_iso)
    if cur is None:
        return None
    cutoff = cur - timedelta(days=lag_trading_days)

    candidates: list[tuple[datetime, dict]] = []
    try:
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                ts = _parse_iso(row.get("timestamp", ""))
                if ts is None:
                    continue
                if ts < cutoff:
                    candidates.append((ts, row))
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1]   # most recent that satisfies lag


def _adas_row_to_packet_field(row_tuple: tuple[datetime, dict]) -> dict:
    """Convert a CSV row tuple to the lightweight dict the PM agent
    sees. Hides internal column names; surfaces semantic fields."""
    ts, row = row_tuple
    chain = None
    raw_chain = row.get("veto_authority_chain_json") or ""
    if raw_chain:
        try:
            chain = json.loads(raw_chain)
        except json.JSONDecodeError:
            chain = None
    per_agent_signals: Any = []
    raw_pa = row.get("per_agent_signals_json") or ""
    if raw_pa:
        try:
            per_agent_signals = json.loads(raw_pa)
        except json.JSONDecodeError:
            per_agent_signals = []
    return {
        "trigger_id": row.get("trigger_id"),
        "timestamp": row.get("timestamp"),
        "ticker": row.get("ticker"),
        "candidate_type": row.get("candidate_type"),
        "num_agents": int(row.get("num_agents", 0) or 0),
        "num_dissenting_agents": int(
            row.get("num_dissenting_agents", 0) or 0
        ),
        "confidence_dispersion": {
            "high": int(row.get("confidence_dispersion_high", 0) or 0),
            "medium": int(row.get("confidence_dispersion_medium", 0) or 0),
            "low": int(row.get("confidence_dispersion_low", 0) or 0),
            "unknown": int(
                row.get("confidence_dispersion_unknown", 0) or 0
            ),
            "std_proxy": float(
                row.get("confidence_dispersion_std_proxy", 0.0) or 0.0
            ),
        },
        "veto_authority_chain": chain,
        "per_agent_signals": per_agent_signals,
        "rule_version": row.get("rule_version"),
    }


# ── NDI runtime wiring (RULES.md §23, D4 v0.8.2_ndi_runtime_wired) ────
#
# The runner is the only place that calls compute_ndi. The patched
# packet (with news_event_summary.ndi_score and ndi_metadata) is what
# every downstream agent sees, and the envelope evidence_packet_hash is
# resealed so cache_keys honestly reflect the patched contents.
#
# Per Standing Rule 4 the NDI value is advisory input only — neither
# §23 nor §24 carry hardcoded numerical modulation thresholds. The
# narrative_event and risk agent prompts read the field as framing
# context; PM discretion under §5 governs sizing.

def _maybe_patch_ndi(
    packet: dict, provider: LLMProvider
) -> tuple[dict, dict]:
    """Compute NDI for the news_event_summary block and patch the packet.

    Always runs the wiring regardless of provider — under the stub the
    compute returns mode='stub' with score=None, but the metadata is
    still attached for audit completeness. The envelope hash and
    locked_decision_id are recomputed post-patch (the patched packet is
    what the agents reason about, so the cache_key SHOULD reflect it)."""
    news_block = packet.get("news_event_summary") or {}
    items = news_block.get("items") or []
    env = packet.get("envelope") or {}
    ticker = env.get("ticker") or ""
    decision_ts = env.get("decision_timestamp") or ""
    anchor = _parse_iso(decision_ts) if decision_ts else None

    ndi_result = compute_ndi(
        items,
        event_cluster_id=f"{ticker}:1d",
        decision_timestamp_utc=anchor,
        window_hours=24,
        provider=provider,
    )

    patched = copy.deepcopy(packet)
    nb = patched.setdefault("news_event_summary", {})
    nb["ndi_score"] = ndi_result.get("score")
    # Bounded, deterministic metadata (omit cache_hit + extracted_frames
    # which are non-deterministic / verbose).
    nb["ndi_metadata"] = {
        "mode": ndi_result.get("mode"),
        "n_sources": int(ndi_result.get("n_sources", 0) or 0),
        "n_items_considered": int(
            ndi_result.get("n_items_considered", 0) or 0
        ),
        "rationale": ndi_result.get("rationale", "") or "",
    }

    p_env = patched.setdefault("envelope", {})
    p_env["evidence_packet_hash"] = compute_evidence_packet_hash(patched)
    p_env["locked_decision_id"] = compute_locked_decision_id(
        ticker, decision_ts, p_env["evidence_packet_hash"]
    )
    return patched, dict(ndi_result)


def _build_packet_for_pm(
    base_packet: dict,
    upstream_outputs: dict[str, dict],
    adas_lagged: Optional[dict],
) -> dict:
    """PM-bound packet: same upstream_agent_outputs synthesis as the
    other late-stage agents, PLUS the t+1-lagged ADaS row. The
    `adas_lagged` field is added at the TOP LEVEL so PM sees it
    directly; it MUST NOT be added to packets bound for other agents
    (§25 isolation)."""
    p = copy.deepcopy(base_packet)
    p["upstream_agent_outputs"] = upstream_outputs
    p["adas_lagged"] = adas_lagged   # may be None
    return p


def run_all_agents_for_candidate(
    *,
    evidence_packet: dict,
    candidate_type: Literal["surge_short", "quality_long"],
    provider: Optional[LLMProvider] = None,
    cache: Optional[LLMCache] = None,
    force_refresh: bool = False,
    agent_mode: Literal["multi", "solo"] = "multi",
    topology: Literal["pipeline", "flat"] = "pipeline",
) -> dict:
    """Run an agent configuration for one candidate.

    Three orthogonal axes (Step A2/A4):
      - agent_mode  — "multi" (default) runs the full multi-agent
                      configuration; "solo" runs the single-agent
                      baseline_solo only and returns its output as the
                      final decision. `topology` is ignored when
                      agent_mode="solo".
      - topology    — "pipeline" (default) is the original sequential
                      flow; "flat" runs the four specialists in
                      parallel against the evidence packet (no
                      cross-talk) and feeds their parsed outputs to
                      risk_pm under a separate prompt (pm_flat). Only
                      meaningful when agent_mode="multi".
      - candidate_type — surge_short OR quality_long. Selects the
                      sleeve agent in multi-mode and is part of the
                      run-level envelope in every mode.

    Returns a run-level envelope with the agent_mode and topology fields
    recorded explicitly so downstream audit/replay can reconstruct the
    configuration.
    """
    if candidate_type not in ("surge_short", "quality_long"):
        raise ValueError(
            f"candidate_type must be 'surge_short' or 'quality_long', "
            f"got {candidate_type!r}"
        )
    if agent_mode not in ("multi", "solo"):
        raise ValueError(
            f"agent_mode must be 'multi' or 'solo', got {agent_mode!r}"
        )
    if topology not in ("pipeline", "flat"):
        raise ValueError(
            f"topology must be 'pipeline' or 'flat', got {topology!r}"
        )
    if provider is None:
        provider = get_provider()
    if cache is None:
        cache = LLMCache()

    # §23 NDI runtime wiring (D4 v0.8.2_ndi_runtime_wired). Compute and
    # patch BEFORE branching so every topology (solo / flat / pipeline)
    # sees the same NDI-augmented packet and reseals to the same hash.
    evidence_packet, ndi_result = _maybe_patch_ndi(evidence_packet, provider)

    # ── Pass 8 §31/§32 mandatory-short bypass — Bug X2 hoist 2026-05-06 ──
    # Hoisted ABOVE the solo/flat/multi branch so §32 fires for ALL
    # ablation cells (Cell 1 baseline_solo previously skipped §32 by
    # early-returning to _run_solo before reaching this check). When
    # surge >= 90% AND §32.2 gates pass we emit the synthetic-PM envelope
    # immediately and skip every downstream LLM dispatch.
    pass8_mandatory_short_record = None
    if candidate_type == "surge_short":
        try:
            from src.rules.surge_short_rules import (
                is_mandatory_short_eligible,
                baseline_exclusion,
            )
            surge_pct = _compute_surge_pct_from_packet(evidence_packet)
            if surge_pct is not None and surge_pct >= 0.90:
                cand_dict = _candidate_dict_from_packet(evidence_packet)
                excl = baseline_exclusion(cand_dict)
                if excl.get("blocked"):
                    cand_dict["baseline_exclusion_id"] = excl.get("exclusion_id")
                eligible, gate_results = is_mandatory_short_eligible(
                    cand_dict, surge_pct,
                    baseline_exclusion_blocked=bool(excl.get("blocked")),
                    network_effect_evidence=cand_dict.get(
                        "network_effect_evidence"),
                )
                if eligible:
                    logger.info(
                        "§31 MANDATORY-SHORT triggered: ticker=%s "
                        "surge_pct=%.4f gate_results=%s",
                        (evidence_packet.get("ticker") or "?"),
                        surge_pct, gate_results,
                    )
                    pass8_mandatory_short_record = (
                        _build_synthetic_pm_for_mandatory_short(
                            evidence_packet=evidence_packet,
                            surge_pct=surge_pct,
                            gate_results=gate_results,
                        )
                    )
        except Exception as e:
            logger.warning(
                "Pass 8 mandatory-short check raised %s: %s — "
                "continuing with standard LLM pipeline.",
                type(e).__name__, str(e)[:200],
            )

    if pass8_mandatory_short_record is not None:
        meta = _packet_metadata(evidence_packet)
        return {
            "candidate_type": candidate_type,
            "ticker": meta["ticker"],
            "decision_timestamp": meta["decision_timestamp"],
            "evidence_packet_hash": meta["evidence_packet_hash"],
            "agent_mode": agent_mode,
            "topology": topology,
            "agent_outputs": {PM_AGENT: pass8_mandatory_short_record},
            "final_decision": pass8_mandatory_short_record,
            "agent_disagreement_summary": {
                "num_agents": 0,
                "num_dissenting_agents": 0,
                "confidence_dispersion": 0.0,
                "veto_authority_chain": [],
                "per_agent_signals": {},
                "pass8_bypass": True,
            },
            "adas_persisted": None,
            "adas_lagged_seen_by_pm": None,
            "ndi_compute_result": ndi_result,
            "evidence_packet_patched": evidence_packet,
            "cache_summary": {"hits": 0, "misses": 0, "per_agent": {},
                              "pass8_bypass": True},
            "provider": provider.name,
            "pm_error": None,
        }

    if agent_mode == "solo":
        out = _run_solo(
            evidence_packet=evidence_packet,
            candidate_type=candidate_type,
            provider=provider,
            cache=cache,
            force_refresh=force_refresh,
        )
        out["ndi_compute_result"] = ndi_result
        out["evidence_packet_patched"] = evidence_packet
        return out

    if topology == "flat":
        out = _run_flat(
            evidence_packet=evidence_packet,
            candidate_type=candidate_type,
            provider=provider,
            cache=cache,
            force_refresh=force_refresh,
        )
        out["ndi_compute_result"] = ndi_result
        out["evidence_packet_patched"] = evidence_packet
        return out

    # ── multi / pipeline (§10.14 split + §24 ADaS) ────────────────────
    # prelude → sleeve → risk → [persist ADaS] → pm. Risk receives
    # upstream_agent_outputs over prelude+sleeve. PM receives
    # upstream_agent_outputs over prelude+sleeve+risk PLUS adas_lagged
    # (§24.4 t+1 lag). ADaS rows are written from the 7 non-PM outputs
    # BEFORE PM runs (§24.6) so PM never sees its own current-trigger
    # ADaS contribution.
    sleeve_agent = "surge_short" if candidate_type == "surge_short" else "quality_long"
    non_pm_pipeline = list(_select_prelude(candidate_type)) + [sleeve_agent, RISK_AGENT]

    # (§32 mandatory-short bypass moved up to before solo/flat/multi
    # branching — Bug X2 fix 2026-05-06. Reaching this line means surge
    # was either <90%, gates failed, or candidate_type≠surge_short, so
    # we fall through to the multi/pipeline LLM dispatch below.)

    agent_outputs: dict[str, dict] = {}
    cache_summary = {"hits": 0, "misses": 0, "per_agent": {}}

    # Phase 1: 7 non-PM agents
    for agent_name in non_pm_pipeline:
        packet_for_agent = (
            _build_packet_with_upstream(evidence_packet, agent_outputs)
            if agent_name == RISK_AGENT else evidence_packet
        )
        record = run_agent(
            agent_name=agent_name,
            evidence_packet=packet_for_agent,
            candidate_type=candidate_type,
            provider=provider,
            cache=cache,
            force_refresh=force_refresh,
        )
        ev = record.pop("__cache_event__", "miss")
        cache_summary["per_agent"][agent_name] = ev
        if ev == "hit":
            cache_summary["hits"] += 1
        else:
            cache_summary["misses"] += 1
        agent_outputs[agent_name] = record["parsed_output"]

    # ── §5.17 + §13.6 + §3.8 surge-short veto-scope filter (2026-05-04) ──
    # Mutate Risk output IN PLACE before PM packet build so PM receives
    # the filtered Risk veto state at decision time. Pre-fix, agents
    # inconsistently applied RULES.md scoping at runtime; this is the
    # deterministic enforcer. ADaS disagreement summary (Phase 2) and
    # _build_packet_for_pm (Phase 4) both consume agent_outputs[RISK_AGENT]
    # and now see the corrected state. No-op for non-surge_short.
    risk_out = agent_outputs.get(RISK_AGENT)
    if isinstance(risk_out, dict):
        from src.portfolio.veto_filter import filter_risk_veto_by_candidate_type
        filter_risk_veto_by_candidate_type(risk_out, candidate_type)

    # Phase 2: persist ADaS row from the 7 outputs BEFORE PM runs
    # (§24.6). Disagreement summary is built without PM (final_decision
    # placeholder is None — _build_disagreement_summary tolerates that).
    meta = _packet_metadata(evidence_packet)
    pre_pm_disagreement = _build_disagreement_summary(
        agent_outputs, final_decision=None
    )
    adas_csv = Path(
        os.environ.get("ADAS_CSV_PATH") or _ADAS_CSV_DEFAULT
    )
    persisted_adas = None
    # Step B Cell 2 ablation hook: ABLATION_DISABLE_ADAS=1 short-circuits
    # both the row-persistence side-effect AND the t+1-lagged read used
    # to build PM's packet. PM then sees adas_lagged=None — equivalent
    # to the §24 ADaS subsystem being absent for this cell only.
    _adas_disabled = os.environ.get("ABLATION_DISABLE_ADAS") == "1"
    if not _adas_disabled:
        try:
            persisted_adas = _persist_adas_row(
                csv_path=adas_csv,
                ticker=meta["ticker"] or "",
                candidate_type=candidate_type,
                decision_timestamp=meta["decision_timestamp"] or "",
                disagreement_summary=pre_pm_disagreement,
                rule_version="v0.9.0_pass8_hardrule",
            )
        except OSError as e:
            logger.warning(
                "ADaS persistence failed (continuing without): %s", e
            )

    # Phase 3: read t+1-lagged ADaS for PM packet (§24.4)
    if _adas_disabled:
        adas_lagged_field = None
    else:
        lagged_tuple = _read_lagged_adas(
            adas_csv, meta["decision_timestamp"] or ""
        )
        adas_lagged_field = (
            _adas_row_to_packet_field(lagged_tuple)
            if lagged_tuple is not None else None
        )
    logger.info(
        "ADaS persistence: row written=%s; adas_lagged available for PM=%s",
        persisted_adas is not None, adas_lagged_field is not None,
    )

    # Phase 4: PM with PM-only packet (upstream + adas_lagged)
    pm_packet = _build_packet_for_pm(
        evidence_packet, agent_outputs, adas_lagged_field
    )
    # Pass 6 robustness 2026-05-03: wrap PM call so a PM failure (timeout,
    # JSON parse error, input cap breach) does NOT discard the 7 successful
    # prelude outputs. Caller gets a partial envelope with `final_decision
    # = None` and a populated `pm_error` field; downstream forensic dump
    # captures the prelude rationale for debugging.
    pm_error_info = None
    try:
        pm_record = run_agent(
            agent_name=PM_AGENT,
            evidence_packet=pm_packet,
            candidate_type=candidate_type,
            provider=provider,
            cache=cache,
            force_refresh=force_refresh,
        )
        ev = pm_record.pop("__cache_event__", "miss")
        cache_summary["per_agent"][PM_AGENT] = ev
        if ev == "hit":
            cache_summary["hits"] += 1
        else:
            cache_summary["misses"] += 1
        agent_outputs[PM_AGENT] = pm_record["parsed_output"]
        # ── Pass 8 §33.3 score-band override ────────────────────────
        # Mutate PM output IN PLACE before final_decision is read. When
        # band='mandatory_short' OR 'mandatory_no_trade', rule engine
        # forces the corresponding decision regardless of PM's emission.
        if candidate_type == "surge_short":
            try:
                _apply_score_band_override(agent_outputs[PM_AGENT])
            except Exception as e:
                logger.warning(
                    "Pass 8 score-band override raised %s: %s — "
                    "leaving PM decision unmodified.",
                    type(e).__name__, str(e)[:200],
                )
    except Exception as e:
        logger.error(
            "PM agent failed; returning partial envelope with %d prelude "
            "outputs intact. error=%s: %s",
            len(agent_outputs), type(e).__name__, str(e)[:200],
        )
        pm_error_info = {
            "error_type": type(e).__name__,
            "error_message": str(e)[:500],
            "prelude_outputs_count": len(agent_outputs),
        }
        cache_summary["per_agent"][PM_AGENT] = "error"

    final_decision = agent_outputs.get(PM_AGENT)
    return {
        "candidate_type": candidate_type,
        "ticker": meta["ticker"],
        "decision_timestamp": meta["decision_timestamp"],
        "evidence_packet_hash": meta["evidence_packet_hash"],
        "agent_mode": "multi",
        "topology": "pipeline",
        "agent_outputs": agent_outputs,
        "final_decision": final_decision,
        "agent_disagreement_summary": _build_disagreement_summary(
            agent_outputs, final_decision
        ),
        "adas_persisted": persisted_adas,
        "adas_lagged_seen_by_pm": adas_lagged_field,
        "ndi_compute_result": ndi_result,
        "evidence_packet_patched": evidence_packet,
        "cache_summary": cache_summary,
        "provider": provider.name,
        "pm_error": pm_error_info,
    }


# ── Solo (single-agent baseline) ─────────────────────────────────────

def _run_solo(
    *,
    evidence_packet: dict,
    candidate_type: Literal["surge_short", "quality_long"],
    provider: LLMProvider,
    cache: LLMCache,
    force_refresh: bool,
) -> dict:
    """Run only the baseline_solo agent on the evidence packet.

    No upstream synthesis, no sleeve dispatch — the single agent reads
    the packet end-to-end and emits a PM-equivalent final decision.
    """
    record = run_agent(
        agent_name="baseline_solo",
        evidence_packet=evidence_packet,
        candidate_type=candidate_type,
        provider=provider,
        cache=cache,
        force_refresh=force_refresh,
    )
    ev = record.pop("__cache_event__", "miss")
    cache_summary = {
        "hits": 1 if ev == "hit" else 0,
        "misses": 0 if ev == "hit" else 1,
        "per_agent": {"baseline_solo": ev},
    }
    parsed = record["parsed_output"]
    meta = _packet_metadata(evidence_packet)
    return {
        "candidate_type": candidate_type,
        "ticker": meta["ticker"],
        "decision_timestamp": meta["decision_timestamp"],
        "evidence_packet_hash": meta["evidence_packet_hash"],
        "agent_mode": "solo",
        "topology": "solo",
        "agent_outputs": {"baseline_solo": parsed},
        "final_decision": parsed,
        "cache_summary": cache_summary,
        "provider": provider.name,
    }


# ── Flat ensemble (Step A4) ─────────────────────────────────────────

PM_FLAT = "pm_flat"


def _build_packet_for_pm_flat(
    base_packet: dict, specialist_outputs: dict[str, dict]
) -> dict:
    """Synthesize a packet for the flat-mode PM aggregator. Specialists'
    parsed outputs go under `specialist_outputs` (a flat dict keyed by
    agent name). The pm_flat prompt explicitly relies on this block —
    distinct from pipeline mode's `upstream_agent_outputs` so the model
    does not confuse the two."""
    p = copy.deepcopy(base_packet)
    p["specialist_outputs"] = specialist_outputs
    return p


def _run_flat(
    *,
    evidence_packet: dict,
    candidate_type: Literal["surge_short", "quality_long"],
    provider: LLMProvider,
    cache: LLMCache,
    force_refresh: bool,
) -> dict:
    """Flat-ensemble topology.

    Four specialists run independently in parallel against the same
    evidence packet (no upstream synthesis, no cross-talk). Their
    parsed outputs are then aggregated by `pm_flat`, which uses a
    separate prompt (`pm_flat_v0.1`) and therefore a separate cache
    namespace from pipeline-mode `risk_pm`.

    Specialists' cache keys are unchanged across pipeline / flat modes:
    each specialist sees the same evidence_packet and the same
    prompt_version regardless of topology, so the second run hits
    cache cleanly. The PM aggregator's cache key differs (different
    agent_name + prompt_version) so flat-mode aggregations are kept
    separate from pipeline-mode ones.

    "Parallel" here is logical, not literal: agents are independent
    (no upstream output), but we run them sequentially in this
    process so the deterministic stub remains deterministic. A future
    real-LLM build can switch to a thread/async pool without changing
    the contract.
    """
    # §10.14 split: flat ensemble specialists are the prelude + sleeve.
    # PM_FLAT aggregates them. Risk/PM split is pipeline-only.
    # §5.17 (Chunk B): prelude is candidate-type-specific.
    sleeve_agent = "surge_short" if candidate_type == "surge_short" else "quality_long"
    specialists = list(_select_prelude(candidate_type)) + [sleeve_agent]

    specialist_outputs: dict[str, dict] = {}
    cache_summary = {"hits": 0, "misses": 0, "per_agent": {}}

    for agent_name in specialists:
        record = run_agent(
            agent_name=agent_name,
            evidence_packet=evidence_packet,  # raw packet — no upstream block
            candidate_type=candidate_type,
            provider=provider,
            cache=cache,
            force_refresh=force_refresh,
        )
        ev = record.pop("__cache_event__", "miss")
        cache_summary["per_agent"][agent_name] = ev
        if ev == "hit":
            cache_summary["hits"] += 1
        else:
            cache_summary["misses"] += 1
        specialist_outputs[agent_name] = record["parsed_output"]

    # Now run pm_flat with the synthesised specialist_outputs block.
    pm_packet = _build_packet_for_pm_flat(evidence_packet, specialist_outputs)
    pm_record = run_agent(
        agent_name=PM_FLAT,
        evidence_packet=pm_packet,
        candidate_type=candidate_type,
        provider=provider,
        cache=cache,
        force_refresh=force_refresh,
    )
    ev = pm_record.pop("__cache_event__", "miss")
    cache_summary["per_agent"][PM_FLAT] = ev
    if ev == "hit":
        cache_summary["hits"] += 1
    else:
        cache_summary["misses"] += 1

    # Compose run-level envelope. We expose pm_flat under the canonical
    # "risk_pm" key in agent_outputs so downstream consumers (the CLI,
    # the saved JSON) can compare flat vs pipeline final_decision side
    # by side without branching on topology.
    agent_outputs: dict[str, dict] = dict(specialist_outputs)
    agent_outputs[PM_FLAT] = pm_record["parsed_output"]

    meta = _packet_metadata(evidence_packet)
    return {
        "candidate_type": candidate_type,
        "ticker": meta["ticker"],
        "decision_timestamp": meta["decision_timestamp"],
        "evidence_packet_hash": meta["evidence_packet_hash"],
        "agent_mode": "multi",
        "topology": "flat",
        "agent_outputs": agent_outputs,
        "final_decision": pm_record["parsed_output"],
        "cache_summary": cache_summary,
        "provider": provider.name,
    }


# ── Pass 6 (2026-05-03) — review-mode pipelines ─────────────────────
# Each entry point is a thin wrapper around `run_agent` that handles
# the abbreviated agent sequencing for one of the three Friday/daily
# review modes:
#   - run_pm_for_fi_review    — single PM call (candidate_type='fi_review')
#   - run_cover_pipeline      — alt_data_verify + risk + pm (cover-mode)
#   - run_ql_review_pipeline  — fundamental + valuation + risk + pm (review-mode)
#
# All three accept an evidence_packet that the caller has already augmented
# with `review_context` and `candidate_type`. They return a dict with each
# agent's full record so the caller can inspect parsed_output + cost.

def run_pm_for_fi_review(
    *,
    evidence_packet: dict,
    provider: Optional[LLMProvider] = None,
    cache: Optional[LLMCache] = None,
) -> dict:
    """§27.16 Friday FI review — single PM call.

    The packet must carry candidate_type='fi_review' (set by the caller in
    src/portfolio/fi_review.py::_build_fi_review_packet). PM emits
    ust_actions in decision_log per the FI REVIEW MODE prompt section.
    """
    if provider is None:
        provider = get_provider()
    if cache is None:
        cache = LLMCache()
    return run_agent(
        agent_name=PM_AGENT,
        evidence_packet=evidence_packet,
        candidate_type="fi_review",
        provider=provider,
        cache=cache,
    )


def run_cover_pipeline(
    *,
    evidence_packet: dict,
    existing_short_position: dict,
    provider: Optional[LLMProvider] = None,
    cache: Optional[LLMCache] = None,
) -> dict:
    """§33.5 cover-eval default pipeline — risk + pm only (2 agents).

    Pass 8 Step B2-mini-D (2026-05-05): dropped alt_data_verify per §33.5
    (entry-time alt_data verdict persists as packet context; no re-run on
    routine cover-eval). Use run_cover_pipeline_with_narrative_refresh
    when corporate_calendar reports a new event for the held ticker
    between entry and cover-eval (R-COVER-10 condition iv).

    Excluded per pre-existing rules: fundamental (R-COVER-01),
    macro (R-COVER-06), valuation (user 2026-05-03), network_effect
    (entry verdict persists), narrative_event (entry verdict persists
    unless new corporate event — see narrative_refresh variant).
    """
    if provider is None:
        provider = get_provider()
    if cache is None:
        cache = LLMCache()
    cand = "surge_short_cover"

    risk_record = run_agent(
        agent_name="risk",
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    pm_record = run_agent(
        agent_name=PM_AGENT,
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    return {
        "risk": risk_record,
        "pm": pm_record,
        "agent_count": 2,
        "cost_usd": (
            float(risk_record.get("__cost_usd__", 0.0) or 0.0)
            + float(pm_record.get("__cost_usd__", 0.0) or 0.0)
        ),
    }


def run_cover_pipeline_with_narrative_refresh(
    *,
    evidence_packet: dict,
    existing_short_position: dict,
    provider: Optional[LLMProvider] = None,
    cache: Optional[LLMCache] = None,
) -> dict:
    """§33.5 + R-COVER-10 condition iv — 4-agent cover-eval pipeline.

    Pass 8 Step B2-mini-D (2026-05-05): invoked ONLY when
    `check_new_corporate_calendar_event` returns True for the held
    ticker. narrative_event re-runs against the new event; alt_data_verify
    re-runs to corroborate; risk + pm consume both.

    Cost target: 4 LLM calls. Most cover-evals will use the 2-agent
    `run_cover_pipeline` instead.
    """
    if provider is None:
        provider = get_provider()
    if cache is None:
        cache = LLMCache()
    cand = "surge_short_cover"

    narrative_record = run_agent(
        agent_name="narrative_event",
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    alt_record = run_agent(
        agent_name="alt_data_verify",
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    risk_record = run_agent(
        agent_name="risk",
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    pm_record = run_agent(
        agent_name=PM_AGENT,
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    return {
        "narrative_event": narrative_record,
        "alt_data_verify": alt_record,
        "risk": risk_record,
        "pm": pm_record,
        "agent_count": 4,
        "cost_usd": (
            float(narrative_record.get("__cost_usd__", 0.0) or 0.0)
            + float(alt_record.get("__cost_usd__", 0.0) or 0.0)
            + float(risk_record.get("__cost_usd__", 0.0) or 0.0)
            + float(pm_record.get("__cost_usd__", 0.0) or 0.0)
        ),
    }


def run_ql_review_pipeline(
    *,
    evidence_packet: dict,
    existing_ql_position: dict,
    provider: Optional[LLMProvider] = None,
    cache: Optional[LLMCache] = None,
) -> dict:
    """§30.2 Friday QL review — abbreviated 4-agent pipeline.

    fundamental → valuation → risk → pm, each with
    candidate_type='quality_long_review'. Skipped (already established at
    entry): narrative_event, alt_data_verify, network_effect.
    """
    if provider is None:
        provider = get_provider()
    if cache is None:
        cache = LLMCache()
    cand = "quality_long_review"

    fund_record = run_agent(
        agent_name="fundamental",
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    val_record = run_agent(
        agent_name="valuation",
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    risk_record = run_agent(
        agent_name="risk",
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    pm_record = run_agent(
        agent_name=PM_AGENT,
        evidence_packet=evidence_packet,
        candidate_type=cand,
        provider=provider,
        cache=cache,
    )
    return {
        "fundamental": fund_record,
        "valuation": val_record,
        "risk": risk_record,
        "pm": pm_record,
        "cost_usd": (
            float(fund_record.get("__cost_usd__", 0.0) or 0.0)
            + float(val_record.get("__cost_usd__", 0.0) or 0.0)
            + float(risk_record.get("__cost_usd__", 0.0) or 0.0)
            + float(pm_record.get("__cost_usd__", 0.0) or 0.0)
        ),
    }
