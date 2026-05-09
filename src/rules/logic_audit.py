"""
Logic Audit Module — exports ALL decision rules, thresholds, and scoring formulas
as structured data for transparency and reproducibility.

This module does NOT execute any pipeline logic.
It only describes the rules the system uses.
"""


def get_all_rules() -> dict:
    """Return the complete rule set used by the system."""
    return {
        "system_version": "0.6.0",
        "rule_version": "v0.9.0_pass8_hardrule",
        "investment_philosophy": {
            "long_side": "Buy GOOD companies at REASONABLE prices. A high-quality company at an extreme valuation = WATCH, not BUY. Quality + drawdown + margin of safety = BUY.",
            "short_side": (
                "Extreme surge is the SCREEN, not a conclusion. The agent evaluates "
                "whether the surge reflects real value creation or temporary momentum. "
                "The rule engine enforces guardrails (50% / 1M / $2 screen, baseline "
                "exclusions, 0.5% initial / +0.5% per +100% rise from ORIGINAL entry "
                "/ 5% per-position cap / 10% sleeve cap, T+1 execution). Investment "
                "conclusions are agent-decided; the rule engine never hard-codes "
                "'AI pivot = short' or 'weak fundamentals = short'."
            ),
            "regime_aware": (
                "Allocation shifts by macro regime across 5 labels — Crisis / Poor / "
                "Normal / Strengthening / Overheat — using MAXIMUM caps (not fixed "
                "targets). Crisis allows up to 70% equity to buy quality at distressed "
                "prices; Overheat caps equity at 20% with cash residual common (e.g. "
                "70/20/10 fixed/equity/cash). Agent has discretion to allocate below "
                "the equity max and hold the residual as cash."
            ),
            "decision_domain_boundary": (
                "AGENT decides: catalyst type, value-creation vs momentum, valuation "
                "justification, alt-data assessment, short thesis status, confidence, "
                "evidence sufficiency, recommended action. RULE ENGINE enforces: screen "
                "filters, baseline exclusions, position sizing, sleeve cap, execution "
                "timing, audit logging, missing-data treatment, allocation table."
            ),
        },
        "surge_short_rules": get_surge_short_rules(),
        "quality_long_rules": get_quality_long_rules(),
        "alt_data_verification_logic": get_alt_data_logic(),
        "narrative_event_rules": get_narrative_rules(),
        "fundamental_scoring": get_fundamental_scoring(),
        "network_effect_scoring": get_network_scoring(),
        "valuation_scoring": get_valuation_scoring(),
        "combined_quality_logic": get_combined_quality_logic(),
        "risk_pm_rules": get_risk_pm_rules(),
        "allocation_policy": get_allocation_rules(),
    }


def get_surge_short_rules() -> dict:
    """Surge-short rule description for the dashboard.

    v0.6 philosophy (Step C Decision 6): extreme surge is the screen, not
    a conclusion. All investment judgment is agent-decided. This
    dashboard table describes the GUARDRAILS only — it does NOT publish
    a 'narrative X = short' decision table because no such hard-coded
    conclusions exist.
    """
    return {
        "description": (
            "Hard-coded rules below are guardrails on a candidate set produced by "
            "the daily-gainer scan. Investment conclusions about each candidate "
            "(catalyst meaning, valuation, thesis validity) are produced by the "
            "agent decision module, not by this rule engine."
        ),
        "scan_parameters": {
            "daily_gainers_scanned": "Top 3-5 (high-conviction, rare-event strategy)",
            "min_daily_return_pct": 50,
            "min_volume": 1_000_000,
            "min_prior_close": 2.00,
            "excluded_security_types": ["ETF", "Warrant", "Unit", "Preferred"],
            "note": "Many days will have zero valid candidates. No trade is a valid output.",
        },
        "baseline_short_exclusions": [
            {
                "id": "confirmed_acquisition_no_arb",
                "description": "Confirmed M&A / acquisition with no realistic short-arbitrage opportunity.",
                "block_rule": "Block NEW short entry.",
            },
            {
                "id": "recent_ipo_30d",
                "description": "IPO completed within the last 30 calendar days.",
                "block_rule": "Block NEW short entry.",
            },
            {
                "id": "fully_fda_approved_marketed_drug",
                "description": "Full FDA approval (NDA / BLA / label expansion) for a marketed drug. Phase 1 / Phase 2 / breakthrough therapy designation / fast-track / trial-update events are NOT covered here — those go to the agent.",
                "block_rule": "Block NEW short entry.",
            },
        ],
        "agent_decision_table": [
            {
                "condition": "agent thesis VALID + evidence SUFFICIENT + risk OK",
                "result": "ELIGIBLE FOR SHORT",
                "guardrails_applied": "0.5% initial position; +0.5% per +100% rise from ORIGINAL entry; 5% per-position cap; 10% sleeve cap.",
            },
            {
                "condition": "evidence INSUFFICIENT or thesis UNCERTAIN",
                "result": "WATCH or NO TRADE",
                "guardrails_applied": "No position taken.",
            },
            {
                "condition": "agent classifies thesis as INVALIDATED",
                "result": "EXIT existing position",
                "guardrails_applied": "Critical exit trigger.",
            },
            {
                "condition": "agent thesis VALID but squeeze continues",
                "result": "REVIEW; controlled +0.5% add allowed only after each +100% rise from ORIGINAL entry",
                "guardrails_applied": "Per-position cap 5%; sleeve cap 10%; never on a downtick.",
            },
            {
                "condition": "confirmed baseline-exclusion event emerges AFTER entry",
                "result": "EXIT",
                "guardrails_applied": "Critical exit trigger.",
            },
            {
                "condition": "15+ trading days held, no decline, agent says thesis no longer supported",
                "result": "EXIT",
                "guardrails_applied": "Time-based agent-confirmed exit.",
            },
        ],
        "position_sizing": {
            "max_initial_short_pct": "0.5% of portfolio (Step C v0.6)",
            "add_size_pct": "0.5% per add (Step C v0.6)",
            "add_trigger": "each successive +100% rise from ORIGINAL entry price (NOT from prior add level)",
            "max_per_position_pct": "5.0% of portfolio (Step C v0.6 hard cap)",
            "max_sleeve_exposure_pct": "10.0% of portfolio total",
            "averaging_up_for_this_sleeve": True,
            "averaging_up_note": "By design — adds happen ONLY on continued surge, never on a downtick.",
            "supersedes": "v0.4 ladder (1% initial / +1% per 100% from prior level / 10% sleeve cap)",
        },
        "risk_monitoring": {
            "exit_when_any_of": [
                "agent classifies short_thesis_status as INVALIDATED",
                "confirmed real-value baseline-exclusion event emerges after entry",
                "hard risk limit breached (sleeve cap, persistent missing data)",
                "after 15 trading days position has not declined AND agent says thesis no longer supported",
            ],
            "review_when_any_of": [
                "squeeze pattern continues with rising retail attention",
                "borrow / liquidity risk becomes problematic when observable",
                "agent confidence drops",
                "agent classifies short_thesis_status as UNCERTAIN",
            ],
            "hold_when_all_of": [
                "price has stopped rising",
                "agent classifies short_thesis_status as VALID",
                "evidence still supports eventual reversal",
                "no hard risk limit breached",
            ],
            "do_not_use": [
                "stop-loss based solely on mark-to-market P&L",
                "automatic exit at fixed day count (Day 1, Day 2, etc.)",
                "automatic exit at -15% or -30% P&L",
            ],
        },
        "narrative_taxonomy_examples": [
            "ai_pivot", "crypto_blockchain_pivot", "delisting_relief",
            "meme_squeeze", "phase_1_or_2_biotech_result", "earnings_surprise",
            "major_contract", "vague_partnership", "platform_story",
            "retail_traffic_growth", "turnaround_story", "debt_restructuring",
            "litigation_court_update", "financing_dilution_event",
            "foreign_listing_compliance_update",
        ],
        "narrative_taxonomy_note": (
            "Provided to the agent as prompt-context examples only. NOT trade rules. "
            "The agent must handle catalysts not on this list and may emit new categories."
        ),
    }


def get_quality_long_rules() -> dict:
    return {
        "description": "Step C v0.6 — selects high-quality companies at reasonable valuations using HARD FUNDAMENTAL GATES (binary, necessary conditions). The legacy 0-100 quality-score gate was removed on 2026-04-29 per RULES.md §3.8 + §11.6.",
        "quality_gate": {
            "policy_kind": "hard_fundamental_gates",
            "gates": [
                "eps_ttm > 0",
                "operating_margin_pct > 0",
                "no going-concern flag",
                "debt_to_equity <= 3.0",
                "free_cash_flow_ttm > 0 (positive op CF proxy)",
            ],
            "necessary_not_sufficient": True,
            "below_gate": "NO TRADE (any single gate failure ⇒ candidate excluded)",
            "informational_combined_score": {
                "formula": "informational = (fundamental * 0.40) + (network * 0.30) + (alt_data * 0.30)",
                "purpose": "Descriptor for agent reasoning + dashboard. NOT a gate.",
                "rules_md_reference": "§11.6 — adapter outputs are descriptors, not trading rules.",
            },
        },
        "valuation_gate": {
            "purpose": "Maps valuation_score (DESCRIPTOR) into categorical for agent reasoning.",
            "valuation_score_gte_55": "Eligible to be BUY (gates pass + valuation reasonable/attractive)",
            "valuation_score_35_to_54": "WATCH (gates pass, valuation not yet attractive)",
            "valuation_score_lt_35": "WATCH (gates pass, valuation expensive -- wait for drawdown)",
            "note": "valuation_score does NOT produce a decision by itself; agent reasons over evidence.",
        },
        "position_sizing": {
            "normal_max_single_pct": "5% of portfolio",
            "crisis_attractive_max": "8% of portfolio (crisis regime + attractive valuation)",
            "max_per_position_of_equity_budget": "25% of equity allocation",
            "no_margin": True,
        },
    }


def get_alt_data_logic() -> dict:
    return {
        "description": "Verifies whether a company's narrative is supported by external observable evidence. This is NOT sentiment analysis.",
        "role_1_narrative_verification": {
            "purpose": "Check real capability vs. claims using alternative data sources",
            "data_sources": {
                "sec_filings": {
                    "signals_checked": [
                        "Business description changed (complete_rebrand, serial_pivot, industry_pivot)",
                        "Going concern language in filings",
                        "Recent auditor change",
                        "Related-party transactions >= 3",
                        "Stable description + no going concern (positive signal)",
                    ],
                },
                "github": {
                    "signals_checked": [
                        "Developer ecosystem score < 10 when claiming tech/AI (contradiction)",
                        "Developer ecosystem score >= 50 (positive evidence)",
                        "No AI/ML repos despite AI narrative (contradiction)",
                    ],
                },
                "h1b_lca": {
                    "signals_checked": [
                        "Technical intensity score < 10 when claiming tech transformation (contradiction)",
                        "Technical intensity score >= 50 (positive evidence)",
                        "Hiring trend declining (contradiction)",
                    ],
                },
                "reddit": {
                    "signals_checked": [
                        "Extreme attention + squeeze mentions > 20 (contradiction)",
                        "High YOLO posts (>5) vs low DD posts (<=1) (contradiction)",
                        "Bot suspicion score >= 50% (contradiction)",
                        "Normal attention + DD posts >= 5 (positive evidence)",
                    ],
                },
            },
        },
        "verdict_logic": {
            "contradicted": {
                "rule": "3+ contradiction signals, OR 2+ contradictions with <= 1 positive",
                "score_formula": "max(0, 20 - contradictions * 5) or max(0, 30 - contradictions * 5)",
            },
            "weakly_supported": {
                "rule": "1 contradiction with <= 1 positive, OR 1 positive signal only, OR no signals",
                "score_formula": "35, 45, or 30 depending on case",
            },
            "narrative_supported": {
                "rule": "2+ positive signals with 0 contradictions",
                "score_formula": "min(90, 50 + positive_count * 10)",
            },
        },
        "role_2_thematic_research": {
            "purpose": "Understand what the market needs. Map bottlenecks, second-order beneficiaries, and valuation discipline.",
            "outputs": [
                "Current dominant investment theme",
                "Demand drivers and bottlenecks",
                "Related industries and example tickers",
                "Valuation caution / bubble risk assessment",
                "Decision implications for the portfolio",
            ],
        },
    }


def get_narrative_rules() -> dict:
    return {
        "description": "Classifies the event driving a stock's movement using keyword matching against news headlines.",
        "event_types": {
            "real_value_creation": [
                "confirmed_acquisition",
                "fda_approval",
                "major_real_contract",
                "earnings_surprise_real",
            ],
            "promotional_narratives": [
                "ai_pivot",
                "crypto_pivot",
                "vague_partnership",
                "meme_squeeze",
                "platform_story_no_evidence",
            ],
        },
        "classification_logic": {
            "method": "Keyword matching across all news headlines and snippets",
            "primary_event": "Event type with highest keyword match count",
            "confidence_rules": [
                "Real + promotional mixed = LOW confidence",
                "Real value event = HIGH confidence",
                "Promotional with 2+ matches = HIGH confidence",
                "Promotional with 1 match = MEDIUM confidence",
            ],
        },
        "is_suspicious": "True if event type is in promotional narratives list",
        "is_real_value": "True if event is real value AND NOT also promotional",
    }


def get_fundamental_scoring() -> dict:
    return {
        "description": "Scores fundamental quality 0-100 based on growth, margins, cash flow, leverage, and risk flags. INFORMATIONAL DESCRIPTOR ONLY (Step C v0.6, RULES.md §11.6) — does NOT gate the buy decision; the gate lives in `hard_fundamental_gates` (eps>0, op_margin>0, financial_health=sound). Agent reads this score while reasoning over the fundamental snapshot.",
        "purpose": "informational_descriptor_for_agent_reasoning",
        "is_gate": False,
        "rules_md_reference": "§3.8 (hard gates), §11.6 (descriptors-not-rules)",
        "components": [
            {
                "factor": "Revenue Growth",
                "max_points": 20,
                "thresholds": [
                    {"condition": ">= 20%", "points": 20},
                    {"condition": ">= 10%", "points": 15},
                    {"condition": ">= 5%", "points": 10},
                    {"condition": ">= 0%", "points": 5},
                    {"condition": "< 0%", "points": 0, "flag": "Negative revenue growth"},
                ],
            },
            {
                "factor": "Gross Margin",
                "max_points": 20,
                "thresholds": [
                    {"condition": ">= 50%", "points": 20},
                    {"condition": ">= 30%", "points": 12},
                    {"condition": ">= 15%", "points": 5},
                    {"condition": "< 15%", "points": 0, "flag": "Low gross margin"},
                ],
            },
            {
                "factor": "Free Cash Flow",
                "max_points": 20,
                "thresholds": [
                    {"condition": "> 0", "points": 20},
                    {"condition": "<= 0", "points": 0, "flag": "Negative free cash flow"},
                ],
            },
            {
                "factor": "Debt-to-Equity",
                "max_points": 15,
                "thresholds": [
                    {"condition": "<= 0.5", "points": 15},
                    {"condition": "<= 1.5", "points": 10},
                    {"condition": "<= 3.0", "points": 5},
                    {"condition": "> 3.0", "points": 0, "flag": "High leverage"},
                ],
            },
            {
                "factor": "Going Concern",
                "penalty": -20,
                "condition": "If going_concern = true",
            },
            {
                "factor": "Dilution Risk",
                "penalty": -10,
                "condition": "If dilution_risk in (extreme, high)",
            },
        ],
        "range": "0 to 100 (after clamping)",
    }


def get_network_scoring() -> dict:
    return {
        "description": "Scores network-effect strength 0-100 using alternative data + fundamentals. INFORMATIONAL DESCRIPTOR ONLY (Step C v0.6, RULES.md §11.6) — does NOT gate decisions; supports agent reasoning about long-term franchise quality.",
        "purpose": "informational_descriptor_for_agent_reasoning",
        "is_gate": False,
        "rules_md_reference": "§11.6 (descriptors-not-rules)",
        "components": [
            {
                "factor": "Developer Ecosystem (GitHub)",
                "max_points": 25,
                "thresholds": [
                    {"condition": "score >= 80", "points": 25},
                    {"condition": "score >= 50", "points": 15},
                    {"condition": "score >= 20", "points": 5},
                ],
            },
            {
                "factor": "Technical Hiring (H-1B/LCA)",
                "max_points": 20,
                "thresholds": [
                    {"condition": "score >= 80", "points": 20},
                    {"condition": "score >= 50", "points": 10},
                ],
            },
            {
                "factor": "Revenue Scale + Growth",
                "max_points": 20,
                "thresholds": [
                    {"condition": "rev > $10B AND growth > 10%", "points": 20},
                    {"condition": "rev > $1B AND growth > 5%", "points": 10},
                ],
            },
            {
                "factor": "Margin Quality (Pricing Power)",
                "max_points": 20,
                "thresholds": [
                    {"condition": "GM >= 50% AND OM >= 20%", "points": 20},
                    {"condition": "GM >= 30% AND OM >= 0%", "points": 10},
                ],
            },
            {
                "factor": "R&D Investment",
                "max_points": 15,
                "thresholds": [
                    {"condition": "R&D > $1B", "points": 15},
                    {"condition": "R&D > $100M", "points": 8},
                ],
            },
        ],
        "range": "0 to 100 (capped)",
    }


def get_valuation_scoring() -> dict:
    return {
        "description": "Scores valuation attractiveness 0-100. Higher = cheaper / better entry. Starts at 50 (neutral). INFORMATIONAL DESCRIPTOR ONLY (Step C v0.6, RULES.md §11.6) — agent decides; the score maps to a categorical (attractive/fair/expensive) used as ONE input to the agent's reasoning, never as a standalone gate.",
        "purpose": "informational_descriptor_for_agent_reasoning",
        "is_gate": False,
        "rules_md_reference": "§3.8 (hard gates), §11.6 (descriptors-not-rules)",
        "starting_score": 50,
        "components": [
            {
                "factor": "P/E Ratio",
                "adjustments": [
                    {"condition": "P/E < 15", "adjustment": "+15"},
                    {"condition": "P/E 15-25", "adjustment": "+8"},
                    {"condition": "P/E 25-40", "adjustment": "-5"},
                    {"condition": "P/E > 40", "adjustment": "-15"},
                    {"condition": "No earnings (P/E N/A)", "adjustment": "-10"},
                ],
            },
            {
                "factor": "Forward P/E",
                "adjustments": [
                    {"condition": "Fwd P/E < 20", "adjustment": "+8"},
                    {"condition": "Fwd P/E 20-30", "adjustment": "+3"},
                    {"condition": "Fwd P/E > 40", "adjustment": "-8"},
                ],
            },
            {
                "factor": "PEG Ratio",
                "adjustments": [
                    {"condition": "PEG < 1.0", "adjustment": "+12"},
                    {"condition": "PEG 1.0-2.0", "adjustment": "+5"},
                    {"condition": "PEG > 3.0", "adjustment": "-8"},
                ],
            },
            {
                "factor": "Price / FCF",
                "adjustments": [
                    {"condition": "P/FCF < 20", "adjustment": "+8"},
                    {"condition": "P/FCF > 50", "adjustment": "-8"},
                ],
            },
            {
                "factor": "Drawdown from ATH",
                "adjustments": [
                    {"condition": ">= 40% drawdown", "adjustment": "+20"},
                    {"condition": ">= 25% drawdown", "adjustment": "+12"},
                    {"condition": ">= 15% drawdown", "adjustment": "+5"},
                    {"condition": "<= 5% from ATH", "adjustment": "-8"},
                ],
                "note": "This is the margin-of-safety measure",
            },
            {
                "factor": "Crisis Regime Bonus",
                "condition": "Regime = poor or crisis AND drawdown >= 30%",
                "adjustment": "+10",
            },
        ],
        "assessment_classification": [
            {"range": ">= 65", "assessment": "attractive"},
            {"range": "45-64", "assessment": "fair"},
            {"range": "30-44", "assessment": "expensive"},
            {"range": "< 30", "assessment": "very_expensive"},
        ],
        "range": "0 to 100 (clamped)",
    }


def get_combined_quality_logic() -> dict:
    return {
        "description": "Combined quality logic — Step C v0.6 (RULES.md §3.8 + §11.6). The HARD GATE is binary fundamental: eps_ttm > 0 AND operating_margin_pct > 0 AND financial_health = sound. The 0-100 combined score is an INFORMATIONAL descriptor only; it does NOT gate decisions.",
        "policy_kind": "hard_fundamental_gates_with_informational_scores",
        "informational_combined_score": {
            "formula": "informational = (fundamental * 0.40) + (network * 0.30) + (alt_data * 0.30)",
            "purpose": "descriptor_for_agent_reasoning_and_dashboard_display",
            "is_gate": False,
        },
        "hard_fundamental_gates": {
            "rule_1": "eps_ttm > 0",
            "rule_2": "operating_margin_pct > 0",
            "rule_3": "no going-concern flag",
            "rule_4": "debt_to_equity <= 3.0",
            "rule_5": "free_cash_flow_ttm > 0",
            "all_must_pass": True,
            "if_fail": "NO TRADE (gate failure ⇒ candidate excluded; agent retains discretion to recommend WATCH on names that pass)",
            "necessary_not_sufficient": True,
        },
        "valuation_after_gates": {
            "purpose": "categorical descriptor agent consumes as one input among many",
            "valuation >= 55": "BUY-eligible (gates pass + valuation reasonable/attractive)",
            "valuation 35-54": "WATCH (gates pass, valuation not yet attractive)",
            "valuation < 35": "WATCH (gates pass, valuation expensive)",
        },
        "removed_legacy": {
            "description": "The legacy gate `fundamental_score >= 55 AND combined_quality_score >= 50` was REMOVED on 2026-04-29 per Fix 2 of the Top-5 critical-conflict pass.",
            "rules_md_reference": "§3.1 collapsed into §3.8.",
        },
    }


def get_risk_pm_rules() -> dict:
    return {
        "description": (
            "Final guardrail enforcement. Maps the agent's recommended_action onto "
            "BUY / SHORT / WATCH / NO TRADE / VETO subject to hard limits. The "
            "Risk/PM module never invents an investment conclusion — it only "
            "constrains what the agent decided."
        ),
        "surge_short_decisions": {
            "baseline_exclusion_blocked": "-> NO TRADE (M&A no-arb / recent IPO / fully FDA-approved marketed drug)",
            "sleeve_at_cap": "-> VETO (surge-short exposure >= 10%)",
            "agent_recommends_short_with_sufficient_evidence_and_valid_thesis": "-> SHORT (0.5% initial; +0.5% per +100% rise from ORIGINAL entry; 5% per-position cap; 10% sleeve cap)",
            "agent_recommends_short_but_evidence_insufficient_or_thesis_uncertain": "-> NO TRADE (guardrails block entry)",
            "agent_recommends_watch": "-> WATCH",
            "agent_recommends_no_trade_or_needs_more_evidence": "-> NO TRADE",
        },
        "quality_long_decisions": {
            "below_quality_gate": "-> NO TRADE",
            "quality_pass_valuation_watch": "-> WATCH (expensive valuation, wait for pullback)",
            "quality_pass_valuation_buy": "-> BUY (quality + reasonable valuation)",
        },
        "position_limits": {
            "max_single_long": "5% of portfolio (8% in crisis+attractive)",
            "max_initial_short": "0.5% of portfolio (Step C v0.6)",
            "surge_short_add_size": "+0.5% per add (Step C v0.6)",
            "surge_short_add_trigger": "each successive +100% price rise from ORIGINAL entry price",
            "surge_short_per_position_max": "5% of portfolio (Step C v0.6 hard cap)",
            "surge_short_sleeve_max": "10% of portfolio",
            "max_per_position_of_equity": "25% of equity allocation",
            "no_margin": True,
            "no_derivatives": True,
        },
    }


def get_allocation_rules() -> dict:
    return {
        "description": (
            "Regime sets MAXIMUM caps on fixed-income / equity sleeve sizes and a "
            "discipline label. Caps are upper bounds, not fixed targets — agent "
            "may hold cash and allocate below the equity max based on macro signals, "
            "valuation, and yield curve. The regime does NOT hard-block equity entry "
            "based on a numeric quality score. 'Buy good companies at acceptable "
            "prices' applies in all regimes; junk/meme/fraud exclusions live in the "
            "agent + rule layer."
        ),
        "policy_kind": "maximum_caps_with_discretion",
        "regimes": [
            {
                "regime": "crisis",
                "label": "Crisis Environment",
                "fixed_income_max_pct": 30,
                "equity_max_pct": 70,
                "equity_discipline": "dislocation_opportunity_review",
                "equity_restriction": "dislocation_opportunity_review",
                "discipline_note": "Active hunt for fundamentally good companies at distressed prices. Leveraged broad-market ETFs (SPY/TQQQ/SPXL) permitted as long instruments in Crisis only.",
            },
            {
                "regime": "poor",
                "label": "Poor Environment",
                "fixed_income_max_pct": 40,
                "equity_max_pct": 60,
                "equity_discipline": "dislocation_review",
                "equity_restriction": "dislocation_review",
                "discipline_note": "Look for good companies whose prices have dislocated below fair value.",
            },
            {
                "regime": "normal",
                "label": "Normal / Strong Economy",
                "fixed_income_max_pct": 60,
                "equity_max_pct": 40,
                "equity_discipline": "strict_quality_and_valuation",
                "equity_restriction": "strict_quality_and_valuation",
                "discipline_note": "Buy good companies at acceptable prices. Pass on overpriced names. Network-effect names accumulated long-term, valuation permitting.",
            },
            {
                "regime": "strengthening",
                "label": "Strengthening Environment",
                "fixed_income_max_pct": 70,
                "equity_max_pct": 30,
                "equity_discipline": "strict_quality_and_valuation",
                "equity_restriction": "strict_quality_and_valuation",
                "discipline_note": "Same selection bar as Normal. Smaller equity cap reflects approaching late-cycle conditions; agent biases toward already-held network-effect names.",
            },
            {
                "regime": "overheat",
                "label": "Overheat / Late-Cycle",
                "fixed_income_max_pct": 80,
                "equity_max_pct": 20,
                "equity_discipline": "strict_quality_and_valuation_caution",
                "equity_restriction": "strict_quality_and_valuation_caution",
                "discipline_note": "Cap equity exposure aggressively. Cash residual common (e.g. 70/20/10 fixed/equity/cash). Prefer T-bill yield over chasing late-cycle equity beta.",
            },
        ],
        "discretion_clause": (
            "These percentages are upper bounds. Agent may hold cash or allocate "
            "below the equity max based on macro signals, valuation, and yield curve. "
            "Example: in Overheat, 70% fixed / 20% equity / 10% cash is permissible."
        ),
        "global_constraints": [
            "No margin allowed",
            "No derivatives allowed",
            "No crypto / Bitcoin",
            "If no attractive companies exist, unused equity budget stays in fixed income or cash",
            "v0.6 (Step C): 5-regime maximum caps with discretion; supersedes v0.5 4-regime fixed-target table",
        ],
    }


def build_decision_trace(ticker: str, pipeline_result: dict) -> dict:
    """
    Build a complete decision trace for one ticker showing every agent's contribution.
    Returns structured data with per-agent inputs and outputs.
    """
    agents = pipeline_result.get("agent_outputs", {})
    decisions = pipeline_result.get("decisions", [])
    decision = next((d for d in decisions if d["ticker"] == ticker), None)
    if not decision:
        return {"ticker": ticker, "error": "No decision found for this ticker"}

    candidate_type = decision.get("candidate_type", "unknown")

    # Agent 1: Market Screener
    screener = agents.get("market_screener", {})
    surge_match = next(
        (c for c in screener.get("surge_short_candidates", []) if c["ticker"] == ticker),
        None,
    )
    in_quality = ticker in screener.get("quality_long_tickers", [])
    all_gainer = next(
        (g for g in screener.get("all_gainers", []) if g["ticker"] == ticker),
        None,
    )

    a1 = {
        "agent": "01 Market Screener",
        "inputs": {
            "total_gainers_scanned": screener.get("total_gainers", "Data unavailable"),
        },
        "outputs": {},
    }
    if surge_match:
        a1["outputs"] = {
            "selected_as": "surge-short candidate",
            "daily_change_pct": f"+{surge_match['change_pct']:.1f}%",
            "volume": f"{surge_match['volume']/1e6:.1f}M",
            "prior_close": f"${surge_match.get('prior_close', 0):.2f}",
            "passed_threshold": "daily return > 50%, volume > 1M, prior close > $2",
        }
    elif in_quality:
        a1["outputs"] = {
            "selected_as": "quality-long universe",
            "reason": "Pre-defined quality universe list",
        }
    else:
        a1["outputs"] = {"selected_as": "Not evaluated", "reason": "Not in scan or universe"}

    # Agent 2: Narrative / Event
    narr = agents.get("narrative_event", {}).get("classifications", {}).get(ticker, {})
    a2 = {
        "agent": "02 Narrative / Event",
        "inputs": {
            "headlines_analyzed": len(narr.get("headlines", [])),
        },
        "outputs": {
            "event_type": narr.get("event_type", "Data unavailable"),
            "is_real_value": narr.get("is_real_value", "Data unavailable"),
            "is_suspicious": narr.get("event_type", "") in {
                "ai_pivot", "crypto_pivot", "vague_partnership",
                "meme_squeeze", "platform_story_no_evidence", "blockchain_pivot",
            },
            "confidence": narr.get("confidence", "Data unavailable"),
            "evidence": narr.get("evidence", []),
        },
    }

    # Agent 3: Alt-Data Verification
    alt = agents.get("alt_data_verification", {}).get("verifications", {}).get(ticker, {})
    a3 = {
        "agent": "03 Alt-Data Verify (CORE)",
        "inputs": {
            "data_sources_queried": alt.get("data_sources_used", []),
        },
        "outputs": {
            "verdict": alt.get("verdict", "Data unavailable"),
            "evidence_score": alt.get("evidence_score", "Data unavailable"),
            "total_signals": alt.get("total_signals", "Data unavailable"),
            "positive_signals": alt.get("evidence_for", []),
            "contradiction_signals": alt.get("evidence_against", []),
        },
        "signal_breakdown": {
            "sec_signals": [s for s in (alt.get("evidence_for", []) + alt.get("evidence_against", []))
                           if s.startswith("SEC:")],
            "github_signals": [s for s in (alt.get("evidence_for", []) + alt.get("evidence_against", []))
                               if s.startswith("GitHub:")],
            "h1b_signals": [s for s in (alt.get("evidence_for", []) + alt.get("evidence_against", []))
                            if s.startswith("H-1B:")],
            "reddit_signals": [s for s in (alt.get("evidence_for", []) + alt.get("evidence_against", []))
                               if s.startswith("Reddit:")],
        },
    }

    # Agent 4: Fundamental / Network / Valuation
    fund_key = "surge_evaluations" if candidate_type == "surge_short" else "quality_evaluations"
    fund = agents.get("fundamental_network", {}).get(fund_key, {}).get(ticker, {})
    km = fund.get("key_metrics", {})
    a4 = {
        "agent": "04 Fund / Network / Valuation",
        "inputs": {
            "data_source": "fundamentals.json (mock)" if not km else "fundamentals loaded",
        },
        "outputs": {
            "fundamental_score": fund.get("fundamental_score", "Data unavailable"),
            "fundamental_flags": fund.get("fundamental_flags", []),
            "network_effect_score": fund.get("network_effect_score", "Data unavailable"),
            "network_evidence": fund.get("network_evidence", []),
            "valuation_score": fund.get("valuation_score", "Data unavailable"),
            "valuation_assessment": fund.get("valuation_assessment", "Data unavailable"),
            "valuation_notes": fund.get("valuation_notes", []),
            "combined_quality_score": fund.get("combined_quality_score", "Data unavailable"),
            "quality_long_eligible": fund.get("quality_long_eligible", "Data unavailable"),
            "decision_hint": fund.get("decision_hint", "Data unavailable"),
            "decision_reason": fund.get("decision_reason", "Data unavailable"),
        },
        "key_metrics": {
            "pe_ratio": km.get("pe_ratio") if km.get("pe_ratio") is not None else "Not evaluated",
            "forward_pe": km.get("forward_pe") if km.get("forward_pe") is not None else "Not evaluated",
            "peg_ratio": km.get("peg_ratio") if km.get("peg_ratio") is not None else "Not evaluated",
            "price_to_fcf": km.get("price_to_fcf") if km.get("price_to_fcf") is not None else "Not evaluated",
            "drawdown_from_ath_pct": km.get("drawdown_from_ath_pct", "Not evaluated"),
            "revenue_growth_pct": km.get("revenue_growth_pct", "Not evaluated"),
            "gross_margin_pct": km.get("gross_margin_pct", "Not evaluated"),
            "operating_margin_pct": km.get("operating_margin_pct", "Not evaluated"),
            "debt_to_equity": km.get("debt_to_equity", "Not evaluated"),
            "going_concern": km.get("going_concern", "Not evaluated"),
            "dilution_risk": km.get("dilution_risk", "Not evaluated"),
        },
    }

    # Agent 5: Risk / PM
    a5 = {
        "agent": "05 Risk / PM",
        "inputs": {
            "regime": pipeline_result.get("regime", "Data unavailable"),
            "allocation": pipeline_result.get("allocation", {}),
        },
        "outputs": {
            "final_decision": decision.get("decision", "Data unavailable"),
            "candidate_type": candidate_type,
            "confidence": decision.get("confidence", "Data unavailable"),
            "position_size": decision.get("position_size", 0),
            "reason": decision.get("reason", "Data unavailable"),
            "risk_notes": decision.get("risk_notes", []),
            "decision_log": decision.get("decision_log", []),
        },
    }

    return {
        "ticker": ticker,
        "candidate_type": candidate_type,
        "agent_trace": [a1, a2, a3, a4, a5],
    }
