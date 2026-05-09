"""
DeterministicStubProvider — fail-closed placeholder used until Step 6.

The stub returns a JSON skeleton that:
  - validates against the requested agent's pydantic schema, and
  - is the most cautious legal value the draft permits for every field.

The orchestrator can swap providers via env var without touching agent
code, so this module is the only thing that ever changes when we wire
real Anthropic calls in Step 6.

Why per-agent skeletons live here (not in the runner): the user's
contract is "skeleton must validate against the agent's schema". To do
that the stub must know which agent it is serving. We accept that as an
optional kwarg `agent_schema_name`; real providers ignore it.
"""
from __future__ import annotations

import json
import time

from .provider import LLMProvider, ProviderResponse


STUB_NOTE = "Deterministic stub. Real LLM not yet connected."
STUB_EVIDENCE_MISSING = ["llm_provider_not_connected_yet"]


def _common_envelope(agent_id: str, agent_role_label: str) -> dict:
    """The §0 common envelope, with every field set to its most cautious
    legal value per the draft's per-field enums."""
    return {
        "agent_id": agent_id,
        "agent_role_label": agent_role_label,
        "schema_version": "agent_output_v1",
        "prompt_version": "v1.1_2026_04_28",
        "rule_version": "v0.9.0_pass8_hardrule",
        "ticker": None,
        "decision_timestamp": "1970-01-01T00:00:00+00:00",
        "data_available_as_of": "1970-01-01T00:00:00+00:00",
        "decision_or_assessment": "needs_more_evidence",
        "confidence": "low",
        "evidence_used": [],
        "evidence_missing": list(STUB_EVIDENCE_MISSING),
        "reasoning_summary": STUB_NOTE,
        "uncertainty": "high",
        "invalidation_conditions": [],
        "risk_flags": [],
        "suggested_improvements": [],
        "data_quality_flags": [],
        "schema_or_prompt_weakness_notes": None,
        "recommended_followups": [],
    }


def build_stub_skeleton(agent_schema_name: str) -> dict:
    """Return a draft-compliant skeleton for the given agent.

    Field values are the most cautious LEGAL values from the draft's
    enums (per the user's instruction "if a value is missing for a stub
    case, pick the most cautious existing value from the draft").

    Raises KeyError on an unknown agent_schema_name so the orchestrator
    surfaces a clear configuration error rather than a silent zero.
    """
    if agent_schema_name == "NarrativeEventAgentOutput":
        env = _common_envelope("02_narrative_event", "Narrative / Event")
        env["decision_or_assessment"] = "catalyst_classified"
        env.update({
            "catalyst_type": "unknown",
            "catalyst_specificity_score": 0.0,
            "vague_narrative_flag": True,
            "company_claim": "",
            "value_creation_assessment": "needs_more_evidence",
            "narrative_taxonomy_examples_matched": [],
            "evidence_sufficiency": "insufficient",
        })
        return env

    if agent_schema_name == "AltDataVerificationAgentOutput":
        env = _common_envelope("03_alt_data_verify", "Alt-Data Verification")
        env["decision_or_assessment"] = "verdict_emitted"
        env.update({
            "verdict": "unverifiable",
            "evidence_score": 0.0,
            "alternative_data_assessment": "unclear",
            "narrative_price_gap_band": "not_evaluated",
            "narrative_price_gap_assessment": "not_evaluated",
            "narrative_price_gap_rationale": "",
            "industry_template_used": "none",
            "selected_adapter_id": "default_sec_gdelt_adapter",
            "adapter_selection_reason": "stub fallback; no real selection",
            "evidence_support_assessment": "not_evaluated",
            "evidence_contradiction_assessment": "not_evaluated",
            "hard_to_fake_signal_summary": "",
            "industry_specific_evidence_used": [],
            "industry_specific_evidence_missing": [],
            "data_quality_warning": None,
            "signals": [],
            # OpenCLI auxiliary block — none populated in stub.
            "opencli_evidence_used": False,
            "opencli_source_summary": None,
            "opencli_sentiment_assessment": "not_evaluated",
            "opencli_community_size_assessment": "not_evaluated",
            "opencli_data_quality_warning": None,
            "opencli_pit_safety_warning": None,
            "opencli_supports_narrative": False,
            "opencli_contradicts_narrative": False,
            "opencli_not_evaluated_reason": "not_connected",
            # Pollution-defense / claim-first.
            "extracted_claims": [],
            "claim_verification_status": "not_evaluated",
            "source_tier_distribution": {"T1": 0, "T2": 0, "T3": 0, "T4": 0, "T5": 0},
            "pollution_risk_level": "unknown",
            "evidence_credibility_assessment": "not_evaluated",
            "primary_source_confirmation": False,
            "reputable_source_confirmation": False,
            "social_only_warning": False,
            "coordinated_campaign_warning": False,
            "rumor_without_evidence_flag": False,
            "can_use_as_primary_signal": False,
            "reason_cannot_use_as_primary_signal":
                "stub fallback; no LLM connected",
            # Sentiment / community / ownership descriptors.
            "market_sentiment_assessment": "not_evaluated",
            "community_size_assessment": "not_evaluated",
            "ownership_positioning_assessment": "not_evaluated",
            "smart_money_support_note": None,
            "ownership_crowding_warning": "not_evaluated",
            "large_holder_concentration_note": None,
            "sentiment_pollution_warning": None,
            "community_attention_warning": "not_evaluated",
            "evidence_weight_to_pm": "not_used",
            "not_evaluated_reason": "not_connected",
            # Decision-time discipline.
            "decision_mode": "end_of_day_surge",
            "allowed_data_cutoff": "1970-01-01T00:00:00+00:00",
            "data_after_cutoff_used": False,
            "lookahead_safe": True,
            "recommendation_to_pm": "needs_more_evidence",
        })
        return env

    if agent_schema_name == "FundNetValAgentOutput":
        env = _common_envelope("04_fund_net_val", "Fundamental / Network / Valuation")
        env["decision_or_assessment"] = "scored"
        env.update({
            "fundamental_score": 0,
            "network_effect_score": 0,
            "valuation_score": 0,
            "valuation_assessment": "not_evaluated",
            "combined_quality_score": 0,
            "quality_long_eligible": False,
            # No "needs_more_evidence" in the draft enum; "no_trade" is the
            # most cautious legal value.
            "decision_hint": "no_trade",
            "fundamental_assessment": "uncertain",
            "key_metrics_used": [],
            "key_metrics_missing": [],
            "valuation_inputs_used": {},
        })
        return env

    if agent_schema_name == "FundamentalAgentOutput":
        env = _common_envelope("04a_fundamental", "Fundamental")
        env["decision_or_assessment"] = "scored"
        env.update({
            "fundamental_score": 0,
            "hard_gates_pass": False,
            "failed_gates": ["data_unavailable_stub"],
            "fundamental_assessment": "uncertain",
            "key_metrics_used": [],
            "key_metrics_missing": list(STUB_EVIDENCE_MISSING),
        })
        return env

    if agent_schema_name == "NetworkEffectAgentOutput":
        env = _common_envelope("04b_network_effect", "Network-Effect")
        env["decision_or_assessment"] = "scored"
        env.update({
            "network_effect_score": 0,
            "network_effect_evidence": "absent",
            "pricing_power_assessment": "uncertain",
            "key_signals_used": [],
            "key_signals_missing": list(STUB_EVIDENCE_MISSING),
        })
        return env

    if agent_schema_name == "ValuationAgentOutput":
        env = _common_envelope("04c_valuation", "Valuation")
        env["decision_or_assessment"] = "scored"
        env.update({
            "valuation_score": 0,
            "valuation_assessment": "not_evaluated",
            "margin_of_safety_assessment": "not_evaluated",
            "valuation_inputs_used": {},
            "valuation_inputs_missing": list(STUB_EVIDENCE_MISSING),
        })
        return env

    if agent_schema_name == "SurgeShortAgentOutput":
        env = _common_envelope("05_surge_short", "Surge-Short")
        env["decision_or_assessment"] = "recommendation"
        env.update({
            "candidate_type": "surge_short",
            "catalyst_type": "unknown",
            "company_claim": "",
            "industry": "unknown",
            "value_creation_assessment": "needs_more_evidence",
            "valuation_justification": "unclear",
            "alternative_data_assessment": "unclear",
            "fundamental_assessment": "uncertain",
            "momentum_vs_value_judgment": "unclear",
            "short_thesis_status": "uncertain",
            "evidence_sufficiency": "insufficient",
            "recommended_action": "needs_more_evidence",
            "initial_position_pct": 0.0,
            "allow_add": False,
            "next_add_trigger_price": None,
            "max_sleeve_exposure_remaining_pct": 10.0,
            "hold_review_exit_reason": "stub fallback; no LLM connected",
            "missing_data_warnings": list(STUB_EVIDENCE_MISSING),
            "audit_rationale": STUB_NOTE,
        })
        return env

    if agent_schema_name == "QualityLongAgentOutput":
        env = _common_envelope("06_quality_long", "Quality-Long")
        env["decision_or_assessment"] = "recommendation"
        env.update({
            "candidate_type": "quality_long",
            "fundamental_assessment": "uncertain",
            "network_effect_evidence": "absent",
            "valuation_assessment": "not_evaluated",
            "selloff_origin_view": "unclear",
            "thesis_status": "uncertain",
            "evidence_sufficiency": "insufficient",
            "recommended_action": "needs_more_evidence",
            "initial_position_pct": 0.0,
            "max_position_pct": 5.0,
            "missing_data_warnings": list(STUB_EVIDENCE_MISSING),
            "audit_rationale": STUB_NOTE,
        })
        return env

    if agent_schema_name == "RiskPMAgentOutput":
        env = _common_envelope("07_risk_pm", "Risk / PM")
        env["decision_or_assessment"] = "final_decision"
        # The draft's `decision` enum has no "needs_more_evidence" member.
        # "veto" is the most cautious legal value when evidence is
        # insufficient (Hard rule §7: "decision is `veto` if any
        # veto_conditions_evaluated is `tripped`"). We trip the
        # insufficient_evidence veto explicitly to keep the audit trail
        # truthful about WHY the position is zero.
        env.update({
            "candidate_type": "quality_long",   # caller may overwrite
            "decision": "veto",
            "position_size_pct": 0.0,
            "reason": STUB_NOTE,
            "risk_notes": [],
            "veto_conditions_evaluated": [
                {"name": "insufficient_evidence", "tripped": True},
                {"name": "hard_risk_limit_breached", "tripped": False},
                {"name": "missing_data_persists_beyond_tolerance", "tripped": False},
                {"name": "information_integrity_veto", "tripped": False},
                {"name": "lookahead_data_used", "tripped": False},
            ],
            "rule_engine_path": [],
            "decision_log": [STUB_NOTE],
            "execution_plan": {
                "order_type": "T+1 next-open",
                "execution_timestamp": "1970-01-01T13:30:00+00:00",
                "ticker": None,
                "side": "none",
                "size_pct_of_portfolio": 0.0,
            },
            "audit_record": {
                "rule_version": "v0.9.0_pass8_hardrule",
                "frozen_rules_file": "config/frozen_rules_v0.6_agentic_allocation_5regime.yaml",
                "agent_prompt_version": "v1.1_2026_04_28",
                "evidence_packet_hash": "sha256:stub",
            },
            # OpenCLI / pollution / sentiment fields — descriptor-only,
            # never raised to primary by the stub.
            "opencli_evidence_weight": "not_used",
            "community_attention_risk_note": None,
            "meme_or_retail_attention_warning": "not_evaluated",
            "source_reliability_adjustment": "not_evaluated",
            "opencli_evidence_final_status": "not_evaluated",
            "information_integrity_veto": False,
            "pollution_risk_adjustment": "not_evaluated",
            "social_signal_weight": "not_used",
            "primary_source_required": True,
            "unverified_claim_note": None,
            "thesis_confirmation_allowed": False,
            "thesis_confirmation_block_reason":
                "stub fallback; no LLM connected",
            "sentiment_risk_adjustment_note": None,
            "ownership_risk_adjustment_note": None,
            "crowding_risk_flag": "not_evaluated",
            "squeeze_risk_flag": "not_evaluated",
            "institutional_sponsorship_note": None,
            "smart_money_benchmark_note": None,
            # Decision-time discipline mirror.
            "decision_mode": "end_of_day_surge",
            "allowed_data_cutoff": "1970-01-01T00:00:00+00:00",
            "execution_timestamp": "1970-01-01T13:30:00+00:00",
            "data_after_cutoff_used": False,
            "lookahead_safe": True,
            "locked_decision_id": "sha256:stub",
            "immutable_decision_flag": True,
        })
        return env

    if agent_schema_name == "RiskAgentOutput":
        env = _common_envelope("07a_risk", "Risk")
        env["decision_or_assessment"] = "advisory_emitted"
        env.update({
            "candidate_type": "quality_long",   # caller may overwrite
            "veto_conditions_evaluated": [
                {"name": "insufficient_evidence", "tripped": True},
                {"name": "hard_risk_limit_breached", "tripped": False},
                {"name": "missing_data_persists_beyond_tolerance", "tripped": False},
                {"name": "information_integrity_veto", "tripped": False},
                {"name": "lookahead_data_used", "tripped": False},
            ],
            # 5-clause advisory per RULES.md §5.14 — stub fallback.
            "advisory_notes": (
                "Stub fallback. No LLM connected. Cannot verify upstream "
                "evidence or signals. Regime context unavailable. "
                "Recommend NO TRADE until LLM provider is wired."
            ),
            "risk_notes": [],
            "rule_engine_path": [],
            "cover_decision_inputs": None,
            "information_integrity_veto": False,
            "pollution_risk_adjustment": "not_evaluated",
            "primary_source_required": True,
            "decision_mode": "end_of_day_surge",
            "allowed_data_cutoff": "1970-01-01T00:00:00+00:00",
            "data_after_cutoff_used": False,
            "lookahead_safe": True,
        })
        return env

    if agent_schema_name == "PMAgentOutput":
        env = _common_envelope("07b_pm", "PM (final)")
        env["decision_or_assessment"] = "final_decision"
        env.update({
            "candidate_type": "quality_long",   # caller may overwrite
            "decision": "veto",
            "position_size_pct": 0.0,
            "reason": STUB_NOTE,
            "risk_notes": [],
            "veto_conditions_evaluated": [
                {"name": "insufficient_evidence", "tripped": True},
                {"name": "hard_risk_limit_breached", "tripped": False},
                {"name": "missing_data_persists_beyond_tolerance", "tripped": False},
                {"name": "information_integrity_veto", "tripped": False},
                {"name": "lookahead_data_used", "tripped": False},
            ],
            "rule_engine_path": [],
            "decision_log": [STUB_NOTE],
            "execution_plan": {
                "order_type": "T+1 next-open",
                "execution_timestamp": "1970-01-01T13:30:00+00:00",
                "ticker": None,
                "side": "none",
                "size_pct_of_portfolio": 0.0,
            },
            "audit_record": {
                "rule_version": "v0.9.0_pass8_hardrule",
                "frozen_rules_file": "config/frozen_rules_v0.6_agentic_allocation_5regime.yaml",
                "agent_prompt_version": "v0.1_2026_04_29",
                "evidence_packet_hash": "sha256:stub",
            },
            "cover_decision_dimensions_weighed": [],
            "cover_decision_rationale": None,
            "opencli_evidence_weight": "not_used",
            "community_attention_risk_note": None,
            "meme_or_retail_attention_warning": "not_evaluated",
            "source_reliability_adjustment": "not_evaluated",
            "opencli_evidence_final_status": "not_evaluated",
            "information_integrity_veto": False,
            "pollution_risk_adjustment": "not_evaluated",
            "social_signal_weight": "not_used",
            "primary_source_required": True,
            "unverified_claim_note": None,
            "thesis_confirmation_allowed": False,
            "thesis_confirmation_block_reason": "stub fallback; no LLM connected",
            "sentiment_risk_adjustment_note": None,
            "ownership_risk_adjustment_note": None,
            "crowding_risk_flag": "not_evaluated",
            "squeeze_risk_flag": "not_evaluated",
            "institutional_sponsorship_note": None,
            "smart_money_benchmark_note": None,
            "decision_mode": "end_of_day_surge",
            "allowed_data_cutoff": "1970-01-01T00:00:00+00:00",
            "execution_timestamp": "1970-01-01T13:30:00+00:00",
            "data_after_cutoff_used": False,
            "lookahead_safe": True,
            "locked_decision_id": "sha256:stub",
            "immutable_decision_flag": True,
        })
        return env

    raise KeyError(
        f"DeterministicStubProvider: unknown agent_schema_name "
        f"{agent_schema_name!r}"
    )


class DeterministicStubProvider(LLMProvider):
    """Returns the cautious-legal skeleton for whichever agent is asked.

    Inputs (system_prompt / user_prompt / model_id / etc.) are accepted
    for interface compatibility but ignored — the response is purely a
    function of `agent_schema_name`.
    """

    name = "deterministic_stub"

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model_id: str = "stub-v1",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        response_format: str = "json_object",
        agent_schema_name: str | None = None,
    ) -> ProviderResponse:
        if agent_schema_name is None:
            raise ValueError(
                "DeterministicStubProvider.complete(): agent_schema_name is "
                "required so the skeleton can validate against the right "
                "pydantic model."
            )
        t0 = time.perf_counter()
        skeleton = build_stub_skeleton(agent_schema_name)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return ProviderResponse(
            raw_text=json.dumps(skeleton, ensure_ascii=False),
            model_id="stub-v1",
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            stop_reason="stub",
            provider="deterministic_stub",
            cache_used=False,
        )
