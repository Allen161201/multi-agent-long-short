"""
Agent Decision Schema — structured output schema for the future LLM agent
that will reason over the evidence packet and produce a surge-short
investment decision.

Until the LLM API is connected, ``mock_surge_short_agent_decision`` produces
a deterministic, conservative placeholder:
  - it populates every schema field by surfacing the upstream signals
    that the future LLM would also see (catalyst hint, alt-data verdict,
    fundamental score, etc.) — so the audit trail and dashboard already
    show the intended structure.
  - it ALWAYS sets ``recommended_action`` to ``"needs_more_evidence"`` and
    ``confidence`` to ``"low"`` and ``evidence_sufficiency`` to
    ``"insufficient"``. This guarantees the placeholder cannot generate
    fake trades during prototype runs.

When the real LLM is wired in (separate task, not yet authorized), only
this module needs to change — the rule engine and orchestrator continue
to consume the same schema.
"""
from __future__ import annotations

from typing import Any

DATA_UNAVAILABLE = "Data unavailable"
NOT_EVALUATED = "Not evaluated"

# Allowed enum values per schema field — useful for validation later.
SHORT_THESIS_STATUS = {"valid", "invalidated", "uncertain", "needs_monitoring"}
RECOMMENDED_ACTIONS = {"short", "watch", "no_trade", "needs_more_evidence"}
VALUE_CREATION_ASSESSMENTS = {"real", "momentum", "unclear", "needs_more_evidence"}
VALUATION_JUSTIFICATIONS = {"justified", "not_justified", "unclear"}
ALT_DATA_ASSESSMENTS = {
    "supports", "contradicts", "mixed", "unclear", "not_applicable",
}
FUNDAMENTAL_ASSESSMENTS = {
    "supports_short", "argues_against_short", "uncertain",
}
MOMENTUM_VS_VALUE = {"momentum", "value_creation", "mixed", "unclear"}
CONFIDENCES = {"high", "medium", "low"}
EVIDENCE_SUFFICIENCY = {"sufficient", "partial", "insufficient"}


def empty_decision(ticker: str, decision_date: str, rule_version: str,
                   agent_prompt_version: str) -> dict[str, Any]:
    """Return a fully-populated schema with placeholder values."""
    return {
        "ticker": ticker,
        "decision_date": decision_date,
        "catalyst_type": NOT_EVALUATED,
        "company_claim": NOT_EVALUATED,
        "industry": NOT_EVALUATED,
        "value_creation_assessment": "needs_more_evidence",
        "valuation_justification": "unclear",
        "alternative_data_assessment": "unclear",
        "fundamental_assessment": "uncertain",
        "momentum_vs_value_judgment": "unclear",
        "short_thesis_status": "uncertain",
        "confidence": "low",
        "evidence_sufficiency": "insufficient",
        "recommended_action": "needs_more_evidence",
        "initial_position_pct": 0.0,
        "allow_add": False,
        "next_add_trigger_price": None,
        "max_sleeve_exposure_remaining_pct": NOT_EVALUATED,
        "hold_review_exit_reason": NOT_EVALUATED,
        "missing_data_warnings": [],
        "audit_rationale": (
            "Placeholder agent (no LLM connected). Recommendation forced to "
            "'needs_more_evidence' to avoid generating spurious trades from "
            "rule-based heuristics dressed up as agent reasoning."
        ),
        "rule_version": rule_version,
        "agent_prompt_version": agent_prompt_version,
        "schema_version": "v1.0",
    }


def mock_surge_short_agent_decision(
    *,
    ticker: str,
    decision_date: str,
    rule_version: str,
    agent_prompt_version: str,
    evidence_packet: dict[str, Any] | None = None,
    narrative_hint: dict[str, Any] | None = None,
    alt_data_signals: dict[str, Any] | None = None,
    fundamentals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce a structured decision for the surge-short pipeline.

    PLACEHOLDER. This function must NEVER recommend ``short`` — that is
    reserved for the real LLM agent.  Its job here is only to:
      1. Populate the schema fields with the upstream signals so the
         audit trail and dashboard reflect the intended structure.
      2. Surface missing-data warnings.
      3. Conclude ``needs_more_evidence`` so the rule engine maps the
         decision to ``no_trade``.
    """
    decision = empty_decision(ticker, decision_date, rule_version, agent_prompt_version)

    warnings: list[str] = []

    # Catalyst hint — comes from the keyword classifier upstream.
    # Treated as a HINT, not a classification: the future LLM is expected
    # to override or refine this from raw headlines + filings + filings.
    if narrative_hint:
        catalyst_hint = narrative_hint.get("event_type", "unclassified")
        decision["catalyst_type"] = (
            f"hint:{catalyst_hint}" if catalyst_hint and catalyst_hint != "unknown"
            else "unclassified_pending_agent"
        )
        headlines = narrative_hint.get("headlines", []) or []
        if headlines:
            decision["company_claim"] = headlines[0][:200]
        else:
            warnings.append("No news headlines available for catalyst inference.")
    else:
        warnings.append("Narrative hint missing — agent has no catalyst signal.")

    # Industry — from fundamentals if available.
    if fundamentals:
        sector = fundamentals.get("sector") or fundamentals.get("industry")
        decision["industry"] = sector or NOT_EVALUATED
        if not sector:
            warnings.append("Industry/sector unavailable in fundamentals.")
    else:
        warnings.append("Fundamentals unavailable — cannot infer industry.")

    # Alternative-data signals — surfaced as raw signals, NOT as a verdict.
    if alt_data_signals:
        # Translate the legacy verdict into the new schema's vocabulary.
        legacy_verdict = alt_data_signals.get("verdict")
        legacy_to_schema = {
            "narrative_supported":  "supports",
            "weakly_supported":     "mixed",
            "contradicted":         "contradicts",
            None:                   "unclear",
        }
        decision["alternative_data_assessment"] = legacy_to_schema.get(
            legacy_verdict, "unclear"
        )
        # signal counts are diagnostic only — they do NOT drive the action.
        decision["_alt_data_signal_counts"] = {
            "evidence_for":    len(alt_data_signals.get("evidence_for", [])),
            "evidence_against": len(alt_data_signals.get("evidence_against", [])),
            "data_sources_used": alt_data_signals.get("data_sources_used", []),
        }
    else:
        warnings.append("Alternative-data signals unavailable.")

    # Fundamental hint — surfaced, not turned into a trade signal.
    if fundamentals:
        fs = fundamentals.get("fundamental_score")
        if fs is None:
            decision["fundamental_assessment"] = "uncertain"
            warnings.append("Fundamental score unavailable.")
        else:
            decision["_fundamental_score"] = fs
            decision["fundamental_assessment"] = "uncertain"
    else:
        decision["fundamental_assessment"] = "uncertain"

    decision["missing_data_warnings"] = warnings
    decision["evidence_sufficiency"] = "insufficient"
    decision["confidence"] = "low"
    decision["short_thesis_status"] = "uncertain"
    decision["recommended_action"] = "needs_more_evidence"
    decision["hold_review_exit_reason"] = (
        "Agent placeholder cannot conclude — awaiting LLM agent integration."
    )
    return decision


def validate_decision(decision: dict[str, Any]) -> list[str]:
    """Return a list of validation errors (empty list if schema is OK)."""
    errors: list[str] = []
    required = [
        "ticker", "decision_date", "catalyst_type", "company_claim", "industry",
        "value_creation_assessment", "valuation_justification",
        "alternative_data_assessment", "fundamental_assessment",
        "momentum_vs_value_judgment", "short_thesis_status",
        "confidence", "evidence_sufficiency", "recommended_action",
        "initial_position_pct", "allow_add", "next_add_trigger_price",
        "max_sleeve_exposure_remaining_pct", "hold_review_exit_reason",
        "missing_data_warnings", "audit_rationale", "rule_version",
        "agent_prompt_version",
    ]
    for k in required:
        if k not in decision:
            errors.append(f"missing field: {k}")
    enum_checks = [
        ("short_thesis_status", SHORT_THESIS_STATUS),
        ("recommended_action", RECOMMENDED_ACTIONS),
        ("value_creation_assessment", VALUE_CREATION_ASSESSMENTS),
        ("valuation_justification", VALUATION_JUSTIFICATIONS),
        ("alternative_data_assessment", ALT_DATA_ASSESSMENTS),
        ("fundamental_assessment", FUNDAMENTAL_ASSESSMENTS),
        ("momentum_vs_value_judgment", MOMENTUM_VS_VALUE),
        ("confidence", CONFIDENCES),
        ("evidence_sufficiency", EVIDENCE_SUFFICIENCY),
    ]
    for field, allowed in enum_checks:
        v = decision.get(field)
        if v is not None and v not in allowed:
            errors.append(f"{field} value {v!r} not in {sorted(allowed)}")
    return errors
