"""
Agent 5: Risk / PM Agent
Final rule engine -- produces buy/short/watch/no-trade/allocation-shift/veto decisions.
Enforces position limits, allocation policy, and generates audit log entries.

Core philosophy (v0.4):
- Long side: Buy GOOD companies at REASONABLE prices. Quality + drawdown +
  margin of safety = BUY. (unchanged from v0.3)
- Short side: The 50% / 1M / $2 screen + baseline exclusions are enforced
  here. The investment conclusion (catalyst meaning, valuation justification,
  thesis validity) is delegated to the agent decision schema. This Risk/PM
  agent does NOT hard-code "AI pivot = short" or "weak fundamentals = short"
  — it only enforces guardrails on what the agent decided.
- Regime-aware: allocation shifts by macro regime (unchanged).
"""
from src.rules.allocation_policy import get_allocation, is_equity_allowed, determine_regime
from src.rules.surge_short_rules import (
    baseline_exclusion,
    compute_initial_short_size,
    next_add_trigger_price,
    MAX_SLEEVE_PCT,
)


def make_decision(
    ticker: str,
    candidate_type: str,  # "surge_short" or "quality_long"
    narrative: dict,
    alt_data: dict,
    fundamentals: dict,
    regime: str,
    portfolio_state: dict | None = None,
) -> dict:
    """
    Apply final rule engine to produce a trading decision.

    Returns:
        dict with decision, reasoning, position_size, risk_notes
    """
    if portfolio_state is None:
        portfolio_state = {
            "portfolio_value": 1_000_000,
            "current_positions": {},
            "surge_short_exposure_pct": 0,
        }

    allocation = get_allocation(regime)
    decision_log = []

    # ──── SURGE SHORT CANDIDATE ────
    if candidate_type == "surge_short":
        # The agent decision packet (mock placeholder pre-LLM) is the only
        # source of investment conclusions. This block only enforces
        # guardrails on what the agent decided.
        agent_decision = (fundamentals or {}).get("_agent_decision") or {}
        recommended = agent_decision.get("recommended_action", "needs_more_evidence")
        agent_confidence = agent_decision.get("confidence", "low")
        evidence_sufficiency = agent_decision.get("evidence_sufficiency", "insufficient")
        thesis_status = agent_decision.get("short_thesis_status", "uncertain")
        agent_warnings = agent_decision.get("missing_data_warnings", []) or []

        # Hard-coded baseline exclusions (M&A no-arb, recent IPO, fully FDA-approved)
        candidate_meta = (fundamentals or {}).get("_candidate_meta", {})
        excl = baseline_exclusion(candidate_meta)
        decision_log.append(f"Baseline exclusion check: {excl['reason']}")
        if excl["blocked"]:
            return {
                "ticker": ticker,
                "decision": "no_trade",
                "candidate_type": candidate_type,
                "reason": f"Baseline exclusion ({excl['exclusion_id']}): {excl['reason']}",
                "confidence": "high",
                "position_size": 0,
                "risk_notes": [f"Excluded by hard rule: {excl['exclusion_id']}"],
                "decision_log": decision_log,
                "agent_decision": agent_decision,
            }

        # Sleeve cap guardrail
        current_exposure = portfolio_state.get("surge_short_exposure_pct", 0)
        if current_exposure >= MAX_SLEEVE_PCT:
            return {
                "ticker": ticker,
                "decision": "veto",
                "candidate_type": candidate_type,
                "reason": f"Surge-short sleeve at cap ({current_exposure:.1f}% >= {MAX_SLEEVE_PCT:.1f}%)",
                "confidence": "high",
                "position_size": 0,
                "risk_notes": ["Sleeve exposure cap reached"],
                "decision_log": decision_log + ["Vetoed by sleeve cap"],
                "agent_decision": agent_decision,
            }

        # Map the agent's recommended_action onto the rule-engine decision.
        # The placeholder agent (no LLM yet) ALWAYS emits "needs_more_evidence",
        # which the rule engine surfaces as no_trade with a clear rationale.
        decision_log.append(
            f"Agent recommendation: {recommended} (confidence={agent_confidence}, "
            f"thesis={thesis_status}, evidence={evidence_sufficiency})"
        )

        if recommended == "short":
            # Hard guardrails on the agent's "short" recommendation:
            #  - evidence must be sufficient
            #  - thesis must be valid
            if evidence_sufficiency != "sufficient" or thesis_status != "valid":
                return {
                    "ticker": ticker,
                    "decision": "no_trade",
                    "candidate_type": candidate_type,
                    "reason": (
                        f"Agent recommended SHORT but guardrails not met "
                        f"(evidence={evidence_sufficiency}, thesis={thesis_status})."
                    ),
                    "confidence": agent_confidence,
                    "position_size": 0,
                    "risk_notes": ["Agent guardrails block short entry"],
                    "decision_log": decision_log,
                    "agent_decision": agent_decision,
                }
            sizing = compute_initial_short_size(portfolio_state["portfolio_value"])
            return {
                "ticker": ticker,
                "decision": "short",
                "candidate_type": candidate_type,
                "reason": agent_decision.get("audit_rationale", "Agent recommended short."),
                "confidence": agent_confidence,
                "position_size": sizing["position_dollars"],
                "risk_notes": [
                    f"Initial position: {sizing['position_pct']:.1f}% of portfolio "
                    f"(${sizing['position_dollars']:,.0f}).",
                    f"Adds permitted only after price doubles from original entry "
                    f"(next trigger ≈ "
                    f"${next_add_trigger_price(candidate_meta.get('entry_price', 0) or 0, 0):.2f} "
                    f"if entry_price known).",
                    f"Sleeve cap: {MAX_SLEEVE_PCT:.1f}% portfolio.",
                    "Exit/review is agent-driven; no mechanical P&L stop.",
                ],
                "decision_log": decision_log,
                "agent_decision": agent_decision,
            }

        if recommended == "watch":
            return {
                "ticker": ticker,
                "decision": "watch",
                "candidate_type": candidate_type,
                "reason": agent_decision.get("audit_rationale", "Agent recommended watch."),
                "confidence": agent_confidence,
                "position_size": 0,
                "risk_notes": agent_warnings,
                "decision_log": decision_log,
                "agent_decision": agent_decision,
            }

        # "no_trade" or "needs_more_evidence" → no_trade
        return {
            "ticker": ticker,
            "decision": "no_trade",
            "candidate_type": candidate_type,
            "reason": (
                agent_decision.get("audit_rationale")
                or "Insufficient evidence; agent did not recommend a trade."
            ),
            "confidence": agent_confidence,
            "position_size": 0,
            "risk_notes": agent_warnings,
            "decision_log": decision_log,
            "agent_decision": agent_decision,
        }

    # ──── QUALITY LONG CANDIDATE ────
    elif candidate_type == "quality_long":
        fund_score = fundamentals.get("fundamental_score", 0)
        network_score = fundamentals.get("network_effect_score", 0)
        val_score = fundamentals.get("valuation_score", 50)
        val_assessment = fundamentals.get("valuation_assessment", "not_evaluated")
        alt_data_score = fundamentals.get("alt_data_score", 50)
        combined = fundamentals.get("combined_quality_score", 0)
        eligible = fundamentals.get("quality_long_eligible", False)
        decision_hint = fundamentals.get("decision_hint", "no_trade")

        if not eligible:
            return {
                "ticker": ticker,
                "decision": "no_trade",
                "candidate_type": candidate_type,
                "reason": f"Below quality threshold (quality={combined})",
                "confidence": "medium",
                "position_size": 0,
                "risk_notes": [],
                "decision_log": [
                    f"Fund={fund_score}, Network={network_score}, "
                    f"Valuation={val_score} ({val_assessment}), Combined={combined}",
                ],
            }

        # v0.5: macro regime no longer hard-blocks equity entry on a
        # numeric quality score. Regime sets sleeve sizes only.
        # is_equity_allowed() always returns True; call retained for
        # backward-compat in case future regimes introduce structural
        # blocks (e.g. trading halts, exchange-wide circuit breakers).
        _ = is_equity_allowed(regime, combined)

        # ── KEY CHANGE: Valuation-aware decision ──
        # decision_hint from quality rules already encodes valuation logic
        if decision_hint == "watch":
            val_reason = fundamentals.get("decision_reason", "Valuation unattractive")
            return {
                "ticker": ticker,
                "decision": "watch",
                "candidate_type": candidate_type,
                "reason": f"Quality confirmed but valuation unattractive ({val_assessment}, score={val_score}). {val_reason}",
                "confidence": "medium",
                "position_size": 0,
                "risk_notes": [
                    f"Valuation: {val_assessment}",
                    "Will become BUY candidate after drawdown/valuation improvement",
                ],
                "decision_log": [
                    f"Fund={fund_score}, Network={network_score}, "
                    f"Valuation={val_score} ({val_assessment}), Combined={combined}",
                    f"Decision hint: WATCH -- {val_reason}",
                ],
            }

        # decision_hint == "buy" -- valuation is reasonable/attractive
        max_single_pct = 0.05  # 5%
        pos_size = portfolio_state["portfolio_value"] * max_single_pct
        equity_budget = portfolio_state["portfolio_value"] * allocation["equity_pct"] / 100

        # Crisis regime: larger positions allowed
        if regime in ("poor", "crisis") and val_assessment == "attractive":
            max_single_pct = 0.08
            pos_size = portfolio_state["portfolio_value"] * max_single_pct

        decision_log.append(f"Regime: {regime}")
        decision_log.append(f"Equity budget: {equity_budget:,.0f}")
        decision_log.append(
            f"Fund={fund_score}, Network={network_score}, "
            f"Valuation={val_score} ({val_assessment}), Combined={combined}"
        )

        return {
            "ticker": ticker,
            "decision": "buy",
            "candidate_type": candidate_type,
            "reason": f"Quality + reasonable valuation ({val_assessment}, score={val_score}). Good entry point.",
            "confidence": "high" if val_score >= 60 else "medium",
            "position_size": min(pos_size, equity_budget * 0.25),
            "risk_notes": [
                f"Regime: {allocation['label']}",
                f"Equity allocation: {allocation['equity_pct']}%",
                f"Valuation: {val_assessment}",
                "No margin",
            ],
            "decision_log": decision_log,
        }

    return {
        "ticker": ticker,
        "decision": "no_trade",
        "candidate_type": candidate_type,
        "reason": "Unknown candidate type",
        "confidence": "low",
        "position_size": 0,
        "risk_notes": [],
        "decision_log": [],
    }


def run(
    surge_candidates: list[dict],
    quality_tickers: list[str],
    narrative_results: dict,
    alt_data_results: dict,
    fundamental_results: dict,
    regime: str = "weakening",
) -> dict:
    """Run the Risk/PM agent across all candidates."""
    decisions = []
    portfolio_state = {
        "portfolio_value": 1_000_000,
        "current_positions": {},
        "surge_short_exposure_pct": 0,
    }

    # Surge-short decisions
    for candidate in surge_candidates:
        ticker = candidate["ticker"]
        narr = narrative_results.get("classifications", {}).get(ticker, {})
        alt = alt_data_results.get("verifications", {}).get(ticker, {})
        fund = fundamental_results.get("surge_evaluations", {}).get(ticker, {})

        decision = make_decision(ticker, "surge_short", narr, alt, fund, regime, portfolio_state)
        decisions.append(decision)

    # Quality-long decisions
    for ticker in quality_tickers:
        fund = fundamental_results.get("quality_evaluations", {}).get(ticker, {})
        narr = narrative_results.get("classifications", {}).get(ticker, {})
        alt = alt_data_results.get("verifications", {}).get(ticker, {})

        decision = make_decision(ticker, "quality_long", narr, alt, fund, regime, portfolio_state)
        decisions.append(decision)

    allocation = get_allocation(regime)

    return {
        "agent": "risk_pm",
        "regime": regime,
        "allocation": allocation,
        "decisions": decisions,
        "summary": {
            "total_candidates": len(decisions),
            "buy": len([d for d in decisions if d["decision"] == "buy"]),
            "short": len([d for d in decisions if d["decision"] == "short"]),
            "watch": len([d for d in decisions if d["decision"] == "watch"]),
            "no_trade": len([d for d in decisions if d["decision"] == "no_trade"]),
            "veto": len([d for d in decisions if d["decision"] == "veto"]),
        },
    }
