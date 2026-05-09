"""
Pydantic models for agent outputs.

Authoritative source: docs/AGENT_OUTPUT_SCHEMA_DRAFT.md (the "draft").
Field names, enum members, and types follow the draft literally. We do
NOT invent fields. If the draft permits an empty list / null / a
"not_evaluated" enum value, we accept that as the cautious default.

Pydantic v2.

Models exported (5 reasoning agents + Risk/PM, per Step 5 prompt):
    NarrativeEventAgentOutput      (§2)
    AltDataVerificationAgentOutput (§3)
    FundNetValAgentOutput          (§4)
    SurgeShortAgentOutput          (§5)
    QualityLongAgentOutput         (§6)
    RiskPMAgentOutput              (§7)

Helpers:
    SCHEMA_REGISTRY  — map agent_schema_name → pydantic class
    validate_agent_output(name, payload) -> (parsed_output: dict, status: str, error: str|None)
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError


# ────────────────────────────────────────────────────────────────────
# §0 Common envelope
# ────────────────────────────────────────────────────────────────────

ConfidenceEnum = Literal["high", "medium", "low"]
UncertaintyEnum = Literal["low", "medium", "high"]


class DataQualityFlag(BaseModel):
    """Sub-record used inside `data_quality_flags`.

    The draft lists `{kind, severity, detail}` as the canonical shape;
    we mirror that and accept extra keys defensively.
    """
    model_config = ConfigDict(extra="allow")
    kind: str
    severity: str
    detail: str = ""


class CommonEnvelope(BaseModel):
    """Per §0 — every agent output starts with these fields."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    agent_role_label: str
    schema_version: str
    prompt_version: str
    rule_version: str

    ticker: Optional[str] = None
    decision_timestamp: str
    data_available_as_of: str

    decision_or_assessment: str
    confidence: ConfidenceEnum
    evidence_used: list[str]
    evidence_missing: list[str]
    reasoning_summary: str
    uncertainty: UncertaintyEnum
    invalidation_conditions: list[str]
    risk_flags: list[str]

    # The learning-policy fields.
    suggested_improvements: list[str] = Field(default_factory=list)
    data_quality_flags: list[DataQualityFlag] = Field(default_factory=list)
    schema_or_prompt_weakness_notes: Optional[str] = None
    recommended_followups: list[str] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# §2 Agent 02 — Narrative / Event
# ────────────────────────────────────────────────────────────────────

NarrativeValueCreationEnum = Literal["real", "momentum", "unclear", "needs_more_evidence"]
EvidenceSufficiencyEnum = Literal["sufficient", "partial", "insufficient"]


class NarrativeEventAgentOutput(CommonEnvelope):
    """Agent 02 — classify the catalyst behind a price move (§2)."""

    catalyst_type: str
    catalyst_specificity_score: float = Field(ge=0.0, le=1.0)
    vague_narrative_flag: bool
    company_claim: str
    value_creation_assessment: NarrativeValueCreationEnum
    narrative_taxonomy_examples_matched: list[str]
    evidence_sufficiency: EvidenceSufficiencyEnum
    # Pass 8 (§33.2): surge-short verdict that maps to score component.
    verdict: Optional[
        Literal['vague', 'fabricated', 'unsupported_claim',
                'supported', 'credible_catalyst']
    ] = None


# ────────────────────────────────────────────────────────────────────
# §3 Agent 03 — Alt-Data Verification
# ────────────────────────────────────────────────────────────────────

VerdictEnum = Literal[
    # Legacy values (pre-Pass 8)
    "narrative_supported", "weakly_supported", "contradicted", "unverifiable",
    # Pass 8 §33.2 surge-short scoring values (added 2026-05-04)
    "narrative_corroborated", "narrative_contradicted",
    "weakly_contradicted", "insufficient_evidence_to_verify",
]
AltDataAssessmentEnum = Literal["supports", "contradicts", "mixed",
                                 "unclear", "not_applicable"]
NarrativeGapEnum = Literal["low", "medium", "high", "not_evaluated"]
SupportContradictEnum = Literal["strong", "weak", "none", "not_evaluated"]
SentimentEnum = Literal["supports", "contradicts", "mixed", "unclear", "not_evaluated"]
SizeEnum = Literal["large", "moderate", "small", "unclear", "not_evaluated"]
OwnershipEnum = Literal["supportive", "neutral", "divergent", "unclear", "not_evaluated"]
CrowdingEnum = Literal["low", "medium", "high", "not_evaluated"]
AttentionEnum = Literal["none", "elevated", "extreme", "not_evaluated"]
EvidenceWeightEnum = Literal["primary", "corroborating", "descriptor_only",
                              "dismissed", "not_used"]
ClaimVerificationEnum = Literal["corroborated", "partially_corroborated",
                                 "unverified", "contradicted", "not_evaluated"]
PollutionRiskEnum = Literal["low", "medium", "high", "unknown"]
CredibilityEnum = Literal["high", "medium", "low", "unverified", "not_evaluated"]
DecisionModeEnum = Literal["pre_market", "opening_window",
                            "end_of_day_surge", "historical_replay",
                            # Bug Y fix (2026-05-06): Friday FI review packet sets
                            # envelope.decision_mode='live'; PM mirrors it back.
                            # Pre-fix the literal was rejected, wiping ust_actions
                            # to [] across all 4 Step B cells on 2025-03-07.
                            "live"]
RecToPmEnum = Literal["support_thesis", "challenge_thesis", "needs_more_evidence",
                      "pollution_risk_high", "not_evaluated"]


class Signal(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    available: bool
    value: Optional[float] = None
    verdict_contribution: Literal["supports", "contradicts", "neutral",
                                   "skipped", "not_evaluated"] = "skipped"


class SourceTierDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    T1: int = 0
    T2: int = 0
    T3: int = 0
    T4: int = 0
    T5: int = 0


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="allow")
    claim: str
    claim_type: Literal["customer_loss", "lawsuit", "executive_departure",
                         "guidance_cut", "trial_failure", "regulator_action",
                         "other"]
    source_tier: Literal["T1", "T2", "T3", "T4", "T5"]


class AltDataVerificationAgentOutput(CommonEnvelope):
    """Agent 03 — narrative-vs-evidence verification (§3)."""

    verdict: VerdictEnum
    evidence_score: float = Field(ge=0.0, le=1.0)
    alternative_data_assessment: AltDataAssessmentEnum
    narrative_price_gap_band: NarrativeGapEnum
    narrative_price_gap_assessment: NarrativeGapEnum
    narrative_price_gap_rationale: str
    industry_template_used: str   # draft enum is open-ended ("...|none")
    selected_adapter_id: str
    adapter_selection_reason: str
    evidence_support_assessment: SupportContradictEnum
    evidence_contradiction_assessment: SupportContradictEnum
    hard_to_fake_signal_summary: str
    industry_specific_evidence_used: list[str]
    industry_specific_evidence_missing: list[str]
    data_quality_warning: Optional[str] = None
    signals: list[Signal] = Field(default_factory=list)

    # Phase 3B OpenCLI auxiliary block — every field optional.
    opencli_evidence_used: bool = False
    opencli_source_summary: Optional[str] = None
    opencli_sentiment_assessment: SentimentEnum = "not_evaluated"
    opencli_community_size_assessment: SizeEnum = "not_evaluated"
    opencli_data_quality_warning: Optional[str] = None
    opencli_pit_safety_warning: Optional[str] = None
    opencli_supports_narrative: bool = False
    opencli_contradicts_narrative: bool = False
    opencli_not_evaluated_reason: Optional[
        Literal["not_connected", "auth_required", "browser_unavailable",
                "rate_limited", "parse_error", "empty_result",
                "not_applicable"]
    ] = None

    # Pollution-defense / claim-first.
    extracted_claims: list[ExtractedClaim] = Field(default_factory=list)
    claim_verification_status: ClaimVerificationEnum = "not_evaluated"
    source_tier_distribution: SourceTierDistribution = Field(
        default_factory=SourceTierDistribution
    )
    pollution_risk_level: PollutionRiskEnum = "unknown"
    evidence_credibility_assessment: CredibilityEnum = "not_evaluated"
    primary_source_confirmation: bool = False
    reputable_source_confirmation: bool = False
    social_only_warning: bool = False
    coordinated_campaign_warning: bool = False
    rumor_without_evidence_flag: bool = False
    can_use_as_primary_signal: bool = False
    reason_cannot_use_as_primary_signal: Optional[str] = None

    # Sentiment / community / ownership descriptors.
    market_sentiment_assessment: SentimentEnum = "not_evaluated"
    community_size_assessment: SizeEnum = "not_evaluated"
    ownership_positioning_assessment: OwnershipEnum = "not_evaluated"
    smart_money_support_note: Optional[str] = None
    ownership_crowding_warning: CrowdingEnum = "not_evaluated"
    large_holder_concentration_note: Optional[str] = None
    sentiment_pollution_warning: Optional[str] = None
    community_attention_warning: AttentionEnum = "not_evaluated"
    evidence_weight_to_pm: EvidenceWeightEnum = "descriptor_only"
    not_evaluated_reason: Optional[
        Literal["not_connected", "source_unavailable", "pit_violation",
                "pollution_risk_high", "not_applicable"]
    ] = None

    # Decision-time discipline mirror.
    decision_mode: DecisionModeEnum
    allowed_data_cutoff: str
    data_after_cutoff_used: bool = False
    lookahead_safe: bool = True

    recommendation_to_pm: RecToPmEnum

    # Pass 8 (§33.2): surge-short expected-blocks-absent feeds PM
    # SHORT_CONVICTION_SCORE +1 per element capped at +2 total. The Pass 8
    # surge-short verdict values (narrative_corroborated /
    # narrative_contradicted / weakly_contradicted /
    # insufficient_evidence_to_verify) are added to the existing
    # `verdict: VerdictEnum` field above (enum widened, not duplicated).
    expected_blocks_absent: list[str] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# §4 Agent 04 — Fund / Net / Val
# ────────────────────────────────────────────────────────────────────

ValuationAssessmentEnum = Literal["attractive", "fair", "expensive",
                                   "very_expensive", "not_evaluated"]
DecisionHintEnum = Literal["buy", "watch", "no_trade"]
FundamentalLongEnum = Literal["supports_long", "argues_against_long", "uncertain"]


class FundNetValAgentOutput(CommonEnvelope):
    """Agent 04 — fundamentals + network effect + valuation (§4).

    DEPRECATED 2026-04-29 in favor of the §10.14 split into three
    independent agents (FundamentalAgentOutput, NetworkEffectAgentOutput,
    ValuationAgentOutput). Retained in the registry for backward
    compatibility with cached records and any external caller still
    using the unified schema. New code should use the split schemas.
    """

    fundamental_score: int = Field(ge=0, le=100)
    network_effect_score: int = Field(ge=0, le=100)
    valuation_score: int = Field(ge=0, le=100)
    valuation_assessment: ValuationAssessmentEnum
    combined_quality_score: int = Field(ge=0, le=100)
    quality_long_eligible: bool
    decision_hint: DecisionHintEnum
    fundamental_assessment: FundamentalLongEnum
    key_metrics_used: list[str]
    key_metrics_missing: list[str]
    valuation_inputs_used: dict[str, Any]


# ────────────────────────────────────────────────────────────────────
# §4a / §4b / §4c — Split Fund/Net/Val agents (Step C v0.6 + §10.14)
# ────────────────────────────────────────────────────────────────────

class FundamentalAgentOutput(CommonEnvelope):
    """Agent 04a — fundamental quality + binary HARD-GATES verdict.

    Per RULES.md §3.8, the binary hard-gates verdict (eps_ttm > 0,
    operating_margin_pct > 0, financial_health = sound) is the
    canonical buy-gate. The 0-100 fundamental_score is an
    INFORMATIONAL DESCRIPTOR only (§11.6).
    """
    fundamental_score: int = Field(ge=0, le=100)
    hard_gates_pass: bool
    failed_gates: list[str] = Field(default_factory=list)
    fundamental_assessment: FundamentalLongEnum
    key_metrics_used: list[str]
    key_metrics_missing: list[str]


class NetworkEffectAgentOutput(CommonEnvelope):
    """Agent 04b — network-effect strength.

    Score is an informational descriptor (§11.6). The categorical
    network_effect_evidence in {strong, partial, weak, absent} is the
    label consumed by §2.12 (never-short list classification).

    Doc v1.4 (2026-04-29 PM) extends the schema with a three-layer
    evidence framework per RULES.md §10.15. The new fields are all
    Optional with empty defaults so the existing deterministic-stub
    output stays byte-identical and backward compatible. Agents wired
    to PROMPT_VERSION >= v0.2_2026_04_29_three_layer SHOULD populate
    them; older callers leave them at defaults.
    """
    network_effect_score: int = Field(ge=0, le=100)
    network_effect_evidence: Literal["strong", "partial", "weak", "absent"]
    pricing_power_assessment: Literal["high", "medium", "low", "none", "uncertain"]
    key_signals_used: list[str]
    key_signals_missing: list[str]

    # ── §10.15 three-layer framework (additive, all optional) ──
    classification: Optional[
        Literal[
            "strong_genuine",
            "narrative_hype",
            "mature_monopoly",
            "emerging",
            "ambiguous",
            "absent",
        ]
    ] = None
    layer_1_evidence: list[str] = Field(default_factory=list)
    layer_2_evidence: list[str] = Field(default_factory=list)
    layer_3_evidence: list[str] = Field(default_factory=list)
    type_direct_vs_two_sided: Optional[
        Literal["direct", "two_sided", "multi_sided", "none", "uncertain"]
    ] = None
    confidence: Optional[Literal["high", "medium", "low"]] = None
    staleness_flags: list[str] = Field(default_factory=list)


class ValuationAgentOutput(CommonEnvelope):
    """Agent 04c — valuation attractiveness.

    Score and categorical assessment are informational descriptors
    (§11.6); the agent reasons over evidence; the score does not BY
    ITSELF gate any decision.
    """
    valuation_score: int = Field(ge=0, le=100)
    valuation_assessment: ValuationAssessmentEnum
    margin_of_safety_assessment: Literal["high", "medium", "low", "none", "not_evaluated"]
    valuation_inputs_used: dict[str, Any]
    valuation_inputs_missing: list[str]


# ────────────────────────────────────────────────────────────────────
# §5 Agent 05 — Surge-Short
# ────────────────────────────────────────────────────────────────────

ValuationJustifiedEnum = Literal["justified", "not_justified", "unclear"]
FundamentalShortEnum = Literal["supports_short", "argues_against_short", "uncertain"]
MomentumValueEnum = Literal["momentum", "value_creation", "mixed", "unclear"]
ShortThesisEnum = Literal["valid", "invalidated", "uncertain", "needs_monitoring"]
SurgeShortActionEnum = Literal["short", "watch", "no_trade", "needs_more_evidence"]


class SurgeShortAgentOutput(CommonEnvelope):
    """Agent 05 — surge-short recommendation under v0.4 sleeve (§5)."""

    candidate_type: Literal["surge_short"]
    catalyst_type: str
    company_claim: str
    industry: str
    value_creation_assessment: NarrativeValueCreationEnum
    valuation_justification: ValuationJustifiedEnum
    alternative_data_assessment: AltDataAssessmentEnum
    fundamental_assessment: FundamentalShortEnum
    momentum_vs_value_judgment: MomentumValueEnum
    short_thesis_status: ShortThesisEnum
    evidence_sufficiency: EvidenceSufficiencyEnum
    recommended_action: SurgeShortActionEnum

    initial_position_pct: float = Field(ge=0.0)
    allow_add: bool
    next_add_trigger_price: Optional[float] = None
    max_sleeve_exposure_remaining_pct: float = Field(ge=0.0)
    hold_review_exit_reason: str
    missing_data_warnings: list[str]
    audit_rationale: str


# ────────────────────────────────────────────────────────────────────
# §6 Agent 06 — Quality-Long
# ────────────────────────────────────────────────────────────────────

NetworkEffectEnum = Literal["strong", "partial", "weak", "absent"]
SelloffOriginEnum = Literal["macro", "sector", "company_specific", "mixed", "unclear"]
ThesisStatusEnum = Literal["intact", "partially_invalidated", "invalidated", "uncertain"]
QualityLongActionEnum = Literal["buy", "watch", "no_trade", "needs_more_evidence"]


class QualityLongAgentOutput(CommonEnvelope):
    """Agent 06 — quality-long recommendation (§6)."""

    candidate_type: Literal["quality_long"]
    fundamental_assessment: FundamentalLongEnum
    network_effect_evidence: NetworkEffectEnum
    valuation_assessment: ValuationAssessmentEnum
    selloff_origin_view: SelloffOriginEnum
    thesis_status: ThesisStatusEnum
    evidence_sufficiency: EvidenceSufficiencyEnum
    recommended_action: QualityLongActionEnum

    initial_position_pct: float = Field(ge=0.0)
    max_position_pct: float = Field(ge=0.0)
    missing_data_warnings: list[str]
    audit_rationale: str


# ────────────────────────────────────────────────────────────────────
# §7 Agent 07 — Risk / PM
# ────────────────────────────────────────────────────────────────────

PMDecisionEnum = Literal[
    "short", "buy", "watch", "no_trade", "veto",
    # Bug G fix (2026-05-04): Friday FI review PM emits these per
    # pm_agent.py FI block ("decision ∈ {deploy, rebalance, hold,
    # defer}"). Pre-fix, decision="deploy" was rejected with
    # PydanticLiteralError, wiping the PM rationale and yielding
    # ust_actions=[] / fixed_income_exposure=$0.
    "deploy", "rebalance", "hold", "defer",
]
ExecutionSideEnum = Literal["buy_to_open", "sell_to_open", "none"]
PollutionAdjustmentEnum = Literal["none", "downweight_one", "downweight_two",
                                   "dismiss", "not_evaluated"]
SocialWeightEnum = Literal["primary", "corroborating", "dismissed", "not_used"]
OpenCLIWeightEnum = Literal["primary", "corroborating", "dismissed", "not_used"]
OpenCLIFinalEnum = Literal["supports", "contradicts", "inconclusive", "not_evaluated"]


class VetoCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    tripped: bool
    # Bug O fix (Pass 8 2026-05-04): PM emitted note alongside name+tripped
    # to mirror the script-side veto filter's downgraded_reason annotation.
    # extra="forbid" rejected the entire VetoCondition with extra_forbidden,
    # which propagated up and zeroed out the PM decision (see XTLB
    # 2026-05-04 replay forensic). Adding note as Optional makes the field
    # accepted; pre-Pass-8 emits without note continue to validate.
    note: Optional[str] = None


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_type: str
    execution_timestamp: str
    ticker: Optional[str] = None
    side: ExecutionSideEnum
    size_pct_of_portfolio: float = Field(ge=0.0)


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    rule_version: str
    frozen_rules_file: str
    agent_prompt_version: str
    evidence_packet_hash: str


class RiskPMAgentOutput(CommonEnvelope):
    """Agent 07 — final position decision + audit record (§7).

    DEPRECATED 2026-04-29 in favor of the §10.14 split: RiskAgentOutput
    (advisory + hard veto) and PMAgentOutput (final decision). Retained
    in the registry for backward compatibility.
    """

    candidate_type: Literal["surge_short", "quality_long"]
    decision: PMDecisionEnum
    position_size_pct: float = Field(ge=0.0)
    reason: str
    risk_notes: list[str]

    veto_conditions_evaluated: list[VetoCondition]
    rule_engine_path: list[str]
    decision_log: list[str]
    execution_plan: ExecutionPlan
    audit_record: AuditRecord

    # OpenCLI at PM level — all optional.
    opencli_evidence_weight: OpenCLIWeightEnum = "not_used"
    community_attention_risk_note: Optional[str] = None
    meme_or_retail_attention_warning: AttentionEnum = "not_evaluated"
    source_reliability_adjustment: PollutionAdjustmentEnum = "not_evaluated"
    opencli_evidence_final_status: OpenCLIFinalEnum = "not_evaluated"

    # Pollution-defense at PM level.
    information_integrity_veto: bool = False
    pollution_risk_adjustment: PollutionAdjustmentEnum = "not_evaluated"
    social_signal_weight: SocialWeightEnum = "not_used"
    primary_source_required: bool = True
    unverified_claim_note: Optional[str] = None
    thesis_confirmation_allowed: bool = False
    thesis_confirmation_block_reason: Optional[str] = None

    # Sentiment / community / ownership at PM level.
    sentiment_risk_adjustment_note: Optional[str] = None
    ownership_risk_adjustment_note: Optional[str] = None
    crowding_risk_flag: CrowdingEnum = "not_evaluated"
    squeeze_risk_flag: CrowdingEnum = "not_evaluated"
    institutional_sponsorship_note: Optional[str] = None
    smart_money_benchmark_note: Optional[str] = None

    # Decision-time discipline mirror.
    decision_mode: DecisionModeEnum
    allowed_data_cutoff: str
    execution_timestamp: str
    data_after_cutoff_used: bool = False
    lookahead_safe: bool = True
    locked_decision_id: str
    immutable_decision_flag: bool = True


# ────────────────────────────────────────────────────────────────────
# §7a / §7b — Split Risk + PM agents (Step C v0.6 + §10.14)
# ────────────────────────────────────────────────────────────────────

CoverDecisionDimensionEnum = Literal[
    "pnl", "days_held", "retracement", "borrow_cost", "volume",
    "catalyst", "squeeze_indicators", "not_applicable",
]


class CoverDecisionInputs(BaseModel):
    """Advisory inputs from Risk to PM for cover decisions (§10.14 R-COVER-02)."""
    model_config = ConfigDict(extra="allow")
    borrow_cost_trajectory: Literal["rising", "stable", "falling", "unknown", "not_applicable"] = "not_applicable"
    shares_available_to_borrow_trend: Literal["rising", "stable", "falling", "unknown", "not_applicable"] = "not_applicable"
    short_interest_spike: bool = False
    liquidity_assessment: Literal["adequate", "thin", "unknown", "not_applicable"] = "not_applicable"
    days_held: Optional[int] = None
    unrealized_pnl_vs_sleeve_pct: Optional[float] = None
    bidirectional_signal_note: Optional[str] = None  # R-COVER-04 — borrow decline + shares-available rise


class RiskAgentOutput(CommonEnvelope):
    """Agent 07a — Risk advisory + hard-veto signals (split from RiskPM per §10.14)."""

    candidate_type: Literal["surge_short", "quality_long"]
    veto_conditions_evaluated: list[VetoCondition]
    advisory_notes: str  # 5-clause format per §5.14, 200-400 chars
    risk_notes: list[str]
    rule_engine_path: list[str]

    # Cover-decision advisory inputs (only meaningful when candidate_type=surge_short)
    cover_decision_inputs: Optional[CoverDecisionInputs] = None

    # Pollution / integrity flags consumed by PM
    information_integrity_veto: bool = False
    pollution_risk_adjustment: PollutionAdjustmentEnum = "not_evaluated"
    primary_source_required: bool = True

    # Decision-time discipline mirror
    decision_mode: DecisionModeEnum
    allowed_data_cutoff: str
    data_after_cutoff_used: bool = False
    lookahead_safe: bool = True


class PMAgentOutput(CommonEnvelope):
    """Agent 07b — final position decision + audit record (split from RiskPM per §10.14).

    For COVER decisions on surge-short positions, PM owns the
    decision (R-COVER-02). Risk advises via cover_decision_inputs
    (consumed from upstream RiskAgent output). Fundamental is OUT of
    cover (R-COVER-01). Cover is independent of macro regime
    (R-COVER-06).
    """

    # Bug Y fix (2026-05-06): Friday FI review packet sets candidate_type='fi_review';
    # PM mirrors it back. Cover-eval similarly uses 'surge_short_cover'. QL Friday
    # review uses 'quality_long_review'. Pre-fix all three would schema-reject and
    # produce the synthetic needs_more_evidence envelope.
    candidate_type: Literal["surge_short", "quality_long",
                            "fi_review", "surge_short_cover", "quality_long_review"]
    decision: PMDecisionEnum
    position_size_pct: float = Field(ge=0.0)
    reason: str
    risk_notes: list[str]

    veto_conditions_evaluated: list[VetoCondition]
    rule_engine_path: list[str]
    decision_log: list[str]
    execution_plan: ExecutionPlan
    audit_record: AuditRecord

    # Cover-decision audit (R-COVER-05) — required when this packet is a cover decision.
    cover_decision_dimensions_weighed: list[CoverDecisionDimensionEnum] = Field(default_factory=list)
    cover_decision_rationale: Optional[str] = None

    # Inherited / mirrored from RiskAgent
    opencli_evidence_weight: OpenCLIWeightEnum = "not_used"
    community_attention_risk_note: Optional[str] = None
    meme_or_retail_attention_warning: AttentionEnum = "not_evaluated"
    source_reliability_adjustment: PollutionAdjustmentEnum = "not_evaluated"
    opencli_evidence_final_status: OpenCLIFinalEnum = "not_evaluated"

    information_integrity_veto: bool = False
    pollution_risk_adjustment: PollutionAdjustmentEnum = "not_evaluated"
    social_signal_weight: SocialWeightEnum = "not_used"
    primary_source_required: bool = True
    unverified_claim_note: Optional[str] = None
    thesis_confirmation_allowed: bool = False
    thesis_confirmation_block_reason: Optional[str] = None

    sentiment_risk_adjustment_note: Optional[str] = None
    ownership_risk_adjustment_note: Optional[str] = None
    crowding_risk_flag: CrowdingEnum = "not_evaluated"
    squeeze_risk_flag: CrowdingEnum = "not_evaluated"
    institutional_sponsorship_note: Optional[str] = None
    smart_money_benchmark_note: Optional[str] = None

    decision_mode: DecisionModeEnum
    allowed_data_cutoff: str
    execution_timestamp: str
    data_after_cutoff_used: bool = False
    lookahead_safe: bool = True
    locked_decision_id: str
    immutable_decision_flag: bool = True

    # Bug L fix (2026-05-04): Friday FI review emits a top-level
    # ust_actions array per pm_agent.py FI block ("ust_actions: array
    # of {action, tenor, face_value, rationale}"). CommonEnvelope's
    # extra="forbid" rejected this with extra_forbidden, wiping the
    # PM rationale and leaving ust_actions_extracted=[]. Default empty
    # list keeps the field optional for surge_short / quality_long
    # decisions, populated only when candidate_type=fi_review.
    ust_actions: list[dict] = Field(default_factory=list)

    # Pass 8 §33 SHORT_CONVICTION_SCORE audit fields. Populated by PM
    # for surge_short candidates in 60-90% pool; rule engine uses
    # score_threshold_band to override decision per §33.3. score_components
    # stores per-component contributions (narrative_event +2, alt_data
    # contradicted +2, expected_blocks_absent capped +2, etc.).
    short_conviction_score: Optional[float] = None
    score_components: dict[str, float] = Field(default_factory=dict)
    score_threshold_band: Optional[
        Literal['mandatory_short', 'strong_tilt',
                'discretion', 'mandatory_no_trade']
    ] = None
    pm_override_reason: Optional[str] = None


# ────────────────────────────────────────────────────────────────────
# Registry + validate helper
# ────────────────────────────────────────────────────────────────────

SCHEMA_REGISTRY: dict[str, type[CommonEnvelope]] = {
    "NarrativeEventAgentOutput":     NarrativeEventAgentOutput,
    "AltDataVerificationAgentOutput": AltDataVerificationAgentOutput,
    # Legacy unified Fund/Net/Val (DEPRECATED 2026-04-29; retained for
    # backward compatibility — see §10.14 split below).
    "FundNetValAgentOutput":         FundNetValAgentOutput,
    # §10.14 split — independent agents for ablation feasibility.
    "FundamentalAgentOutput":        FundamentalAgentOutput,
    "NetworkEffectAgentOutput":      NetworkEffectAgentOutput,
    "ValuationAgentOutput":          ValuationAgentOutput,
    "SurgeShortAgentOutput":         SurgeShortAgentOutput,
    "QualityLongAgentOutput":        QualityLongAgentOutput,
    # Legacy unified Risk/PM (DEPRECATED 2026-04-29; retained for
    # backward compatibility — see §10.14 split below).
    "RiskPMAgentOutput":             RiskPMAgentOutput,
    # §10.14 split — Risk advisory + PM authority.
    "RiskAgentOutput":               RiskAgentOutput,
    "PMAgentOutput":                 PMAgentOutput,
}


def validate_agent_output(
    agent_schema_name: str, payload: dict
) -> tuple[dict, str, str | None]:
    """Validate `payload` against the named pydantic model.

    Returns (parsed_dict, validation_status, error_message).
      - On success: validation_status = "ok", error_message = None.
      - On failure: validation_status = "schema_failed_returned_needs_more_evidence",
                    error_message = full pydantic error string,
                    parsed_dict   = a synthetic fail-closed envelope (NOT
                                    schema-valid; the caller decides what
                                    to do with it).

    The fail-closed synthesis matches Step 5's spec: status =
    "needs_more_evidence" (we use decision_or_assessment per §0),
    confidence = "low" (most cautious legal value in the draft enum),
    evidence_missing = ["schema_validation_failed"], notes = error.
    """
    cls = SCHEMA_REGISTRY.get(agent_schema_name)
    if cls is None:
        raise KeyError(f"Unknown agent_schema_name: {agent_schema_name}")
    try:
        instance = cls.model_validate(payload)
        return instance.model_dump(mode="json"), "ok", None
    except ValidationError as ve:
        synthetic = {
            "agent_schema_name": agent_schema_name,
            "decision_or_assessment": "needs_more_evidence",
            "confidence": "low",
            "evidence_used": [],
            "evidence_missing": ["schema_validation_failed"],
            "reasoning_summary": str(ve),
            "uncertainty": "high",
            "validation_status": "schema_failed_returned_needs_more_evidence",
        }
        return synthetic, "schema_failed_returned_needs_more_evidence", str(ve)
