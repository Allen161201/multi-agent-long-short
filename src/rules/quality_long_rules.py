"""
Quality Long Rules — valuation-aware quality selection (Step C v0.6).

Core investment philosophy:
Buy GOOD companies at REASONABLE prices.
A high-quality company at an extreme valuation = WATCH, not BUY.
Quality + drawdown + margin of safety = BUY.

Step C v0.6 (RULES.md §3.8) replaced the legacy numeric quality gate
(`fundamental_score >= 55 AND combined_quality_score >= 50`) with hard
fundamental gates expressed as binary necessary conditions:

  passes_hard_fundamental_gates(f) ⇔
    f.eps_ttm > 0
    AND f.operating_margin_pct > 0
    AND financial_health(f) == "sound"
       (no going-concern flag, D/E ≤ 3.0, FCF > 0 as a positive op-CF proxy)

The 0-100 fundamental / network / valuation scores remain in this
module but they are DESCRIPTORS for the agent's reasoning, not the
gate. The gate is the hard rule above. See logic_audit.py for the
dashboard-facing flag that marks the score tables as INFORMATIONAL.

Per RULES.md §11.6 ("adapter outputs are descriptors, not trading
rules"), no scoring threshold can BY ITSELF produce a trade decision.
"""

# §3.9 v2 (2026-05-07 user authorization) — broad-market ETFs admitted
# under EITHER (a) regime=crisis OR (b) §31 SPY-drawdown trigger active.
# In all other contexts, ETFs are EXCLUDED from the quality-long universe.
# The set of admissible ETF tickers is defined here for runtime reference;
# the spec stub at src/universe/quality_long_universe.py carries an older
# Crisis-only signature and is NOT touched in this fix scope.
SECTION_3_9_ADMISSIBLE_ETFS = frozenset({
    "SPY", "QQQ", "TQQQ", "SPXL", "VOO", "IVV", "VTI", "DIA",
})


def is_etf_admissible_for_ql(
    *,
    ticker: str,
    regime: str | None = None,
    section_31_trigger_active: bool = False,
) -> bool:
    """§3.9 v2 ETF admissibility check.

    Returns True iff `ticker` is a recognized broad-market ETF AND
    either branch (a) Crisis regime OR branch (b) §31 trigger active
    today. False otherwise — caller MUST exclude non-admissible ETFs
    from the quality-long universe.

    Notes:
      - ticker matching is case-sensitive against SECTION_3_9_ADMISSIBLE_ETFS.
      - regime classification is the §4.7 5-label macro_regime classifier
        output ('crisis'|'poor'|'normal'|'strengthening'|'overheat').
      - section_31_trigger_active is the daily flag from
        evaluate_section_31_trigger() in the harness.
    """
    if ticker not in SECTION_3_9_ADMISSIBLE_ETFS:
        return False
    regime_lc = (regime or "").lower() if isinstance(regime, str) else ""
    if regime_lc == "crisis":
        return True
    if section_31_trigger_active:
        return True
    return False


def score_network_effect(fundamentals: dict, github: dict, h1b: dict, reddit: dict) -> dict:
    """
    Score network-effect quality based on fundamentals and alternative data.

    Network effect means:
    - Users attract more users AND suppliers/developers/partners
    - Suppliers/developers/partners improve the platform
    - The company gains pricing power and can extract surplus

    Returns dict with score (0-100) and evidence breakdown.
    """
    score = 0
    evidence = []

    # Developer ecosystem (from GitHub)
    dev_score = github.get("developer_ecosystem_score", 0)
    if dev_score >= 80:
        score += 25
        evidence.append(f"Strong developer ecosystem (score={dev_score})")
    elif dev_score >= 50:
        score += 15
        evidence.append(f"Moderate developer ecosystem (score={dev_score})")
    elif dev_score >= 20:
        score += 5
        evidence.append(f"Weak developer ecosystem (score={dev_score})")

    # Technical hiring intensity (from H-1B)
    tech_score = h1b.get("technical_intensity_score", 0)
    if tech_score >= 80:
        score += 20
        evidence.append(f"Strong technical hiring (score={tech_score})")
    elif tech_score >= 50:
        score += 10
        evidence.append(f"Moderate technical hiring (score={tech_score})")

    # Revenue scale and growth (proxy for user/supplier attraction)
    rev = fundamentals.get("revenue_ttm", 0)
    rev_growth = fundamentals.get("revenue_growth_pct", 0)
    if rev > 10_000_000_000 and rev_growth > 10:
        score += 20
        evidence.append(f"Strong revenue scale (${rev/1e9:.0f}B) with growth ({rev_growth}%)")
    elif rev > 1_000_000_000 and rev_growth > 5:
        score += 10
        evidence.append(f"Good revenue scale (${rev/1e9:.1f}B) with growth ({rev_growth}%)")

    # Margin quality (proxy for pricing power)
    gross_margin = fundamentals.get("gross_margin_pct", 0)
    op_margin = fundamentals.get("operating_margin_pct", 0)
    if gross_margin >= 50 and op_margin >= 20:
        score += 20
        evidence.append(f"Strong margins (GM={gross_margin}%, OM={op_margin}%)")
    elif gross_margin >= 30 and op_margin >= 0:
        score += 10
        evidence.append(f"Decent margins (GM={gross_margin}%, OM={op_margin}%)")

    # R&D investment (proxy for platform development)
    rd = fundamentals.get("rd_expense_ttm", 0)
    if rd > 1_000_000_000:
        score += 15
        evidence.append(f"Heavy R&D investment (${rd/1e9:.0f}B)")
    elif rd > 100_000_000:
        score += 8
        evidence.append(f"Moderate R&D investment (${rd/1e6:.0f}M)")

    return {
        "network_effect_score": min(score, 100),
        "evidence": evidence,
    }


def score_fundamentals(fundamentals: dict) -> dict:
    """Score fundamental quality (0-100)."""
    score = 0
    flags = []

    # Revenue growth
    rg = fundamentals.get("revenue_growth_pct", 0)
    if rg >= 20:
        score += 20
    elif rg >= 10:
        score += 15
    elif rg >= 5:
        score += 10
    elif rg >= 0:
        score += 5
    else:
        flags.append(f"Negative revenue growth ({rg}%)")

    # Gross margin
    gm = fundamentals.get("gross_margin_pct", 0)
    if gm >= 50:
        score += 20
    elif gm >= 30:
        score += 12
    elif gm >= 15:
        score += 5
    else:
        flags.append(f"Low gross margin ({gm}%)")

    # Free cash flow
    fcf = fundamentals.get("free_cash_flow_ttm", 0)
    if fcf > 0:
        score += 20
    else:
        flags.append("Negative free cash flow")

    # Debt
    dte = fundamentals.get("debt_to_equity", 0)
    if dte <= 0.5:
        score += 15
    elif dte <= 1.5:
        score += 10
    elif dte <= 3.0:
        score += 5
    else:
        flags.append(f"High leverage (D/E={dte:.1f})")

    # Going concern
    if fundamentals.get("going_concern", False):
        score -= 20
        flags.append("Going concern risk")

    # Dilution
    dilution = fundamentals.get("dilution_risk", "none")
    if dilution in ("extreme", "high"):
        score -= 10
        flags.append(f"Dilution risk: {dilution}")

    score = max(0, min(score, 100))
    return {
        "fundamental_score": score,
        "flags": flags,
    }


def score_valuation(fundamentals: dict, regime: str = "normal") -> dict:
    """
    Score valuation attractiveness (0-100).

    Higher score = more attractive (cheaper / better entry).
    Lower score = more expensive / less margin of safety.

    Key principle: Good companies at unreasonable prices = WATCH.
    """
    score = 50  # Start neutral
    notes = []

    pe = fundamentals.get("pe_ratio")
    fwd_pe = fundamentals.get("forward_pe")
    ps = fundamentals.get("price_to_sales")
    pfcf = fundamentals.get("price_to_fcf")
    ev_ebitda = fundamentals.get("ev_to_ebitda")
    peg = fundamentals.get("peg_ratio")
    drawdown = fundamentals.get("drawdown_from_ath_pct", 0)
    price_vs_52w = fundamentals.get("price_vs_52w_high_pct", 0)

    # ── P/E Ratio ──
    if pe is not None:
        if pe < 15:
            score += 15
            notes.append(f"Attractive P/E ({pe:.1f}x)")
        elif pe < 25:
            score += 8
            notes.append(f"Reasonable P/E ({pe:.1f}x)")
        elif pe < 40:
            score -= 5
            notes.append(f"Elevated P/E ({pe:.1f}x)")
        else:
            score -= 15
            notes.append(f"Expensive P/E ({pe:.1f}x)")
    elif fundamentals.get("net_income_ttm", 0) <= 0:
        score -= 10
        notes.append("No earnings (P/E not applicable)")

    # ── Forward P/E ──
    if fwd_pe is not None:
        if fwd_pe < 20:
            score += 8
        elif fwd_pe < 30:
            score += 3
        elif fwd_pe > 40:
            score -= 8

    # ── PEG Ratio (Growth-adjusted) ──
    if peg is not None:
        if peg < 1.0:
            score += 12
            notes.append(f"Growth-attractive PEG ({peg:.2f})")
        elif peg < 2.0:
            score += 5
            notes.append(f"Reasonable PEG ({peg:.2f})")
        elif peg > 3.0:
            score -= 8
            notes.append(f"Expensive PEG ({peg:.2f})")

    # ── Price vs FCF ──
    if pfcf is not None:
        if pfcf < 20:
            score += 8
            notes.append(f"Attractive P/FCF ({pfcf:.1f}x)")
        elif pfcf > 50:
            score -= 8
            notes.append(f"Expensive P/FCF ({pfcf:.1f}x)")

    # ── Drawdown from ATH (margin of safety) ──
    if drawdown >= 40:
        score += 20
        notes.append(f"Deep drawdown from ATH ({drawdown}%) -- potential margin of safety")
    elif drawdown >= 25:
        score += 12
        notes.append(f"Meaningful drawdown ({drawdown}%) -- improving entry")
    elif drawdown >= 15:
        score += 5
        notes.append(f"Moderate pullback ({drawdown}%)")
    elif drawdown <= 5:
        score -= 8
        notes.append(f"Near all-time highs ({drawdown}% from ATH)")

    # ── Regime adjustment ──
    if regime in ("poor", "crisis"):
        if drawdown >= 30:
            score += 10
            notes.append("Crisis drawdown bonus: quality at distressed prices")

    score = max(0, min(score, 100))

    # Classify
    if score >= 65:
        assessment = "attractive"
    elif score >= 45:
        assessment = "fair"
    elif score >= 30:
        assessment = "expensive"
    else:
        assessment = "very_expensive"

    return {
        "valuation_score": score,
        "valuation_assessment": assessment,
        "valuation_notes": notes,
    }


def passes_hard_fundamental_gates(fundamentals: dict) -> dict:
    """Step C v0.6 hard fundamental gates (RULES.md §3.8).

    Binary, NECESSARY-but-not-sufficient conditions for `recommended_action="buy"`.

    Returns:
      {"passes": bool, "failed_gates": list[str], "reason": str}

    Failed gates are reported in failure order so the dashboard can
    explain WHY a candidate did not pass without claiming a numeric
    score gated it out.

    Bug Z fix (2026-05-06): producer (src/evidence_packet/blocks/fundamental.py)
    emits PIT-safe quarterly metrics under fundamental_snapshot.snapshot_quarter,
    NOT at the top level. This consumer now reads from snapshot_quarter when
    available and falls back to top-level for backward compatibility (legacy
    callers / test stubs that pre-flatten). Gate (a) eps now reads
    snapshot_quarter.eps when no eps_ttm; operating margin computed as
    operating_income / revenue (both inside snapshot_quarter); fcf reads
    snapshot_quarter.free_cash_flow; debt_to_equity computed from
    total_debt / total_equity inside snapshot_quarter.
    """
    failed: list[str] = []

    sq = fundamentals.get("snapshot_quarter") or {}

    # eps gate: prefer eps_ttm if pre-flattened (e.g. by
    # _enrich_with_pit_fundamentals in surge_short_rules.py); otherwise read
    # snapshot_quarter.eps; otherwise fall back to net_income.
    eps_ttm = fundamentals.get("eps_ttm")
    if eps_ttm is None:
        eps_ttm = sq.get("eps")
    if eps_ttm is None:
        net_income = fundamentals.get("net_income_ttm")
        if net_income is None:
            net_income = sq.get("net_income")
        if net_income is None or net_income <= 0:
            failed.append("eps_ttm_or_net_income_ttm_not_positive")
    elif eps_ttm <= 0:
        failed.append("eps_ttm_not_positive")

    # operating margin gate: prefer operating_margin_pct if pre-flattened
    # (surge_short candidate enrichment populates this). Otherwise compute
    # from snapshot_quarter.operating_income / snapshot_quarter.revenue.
    op_margin = fundamentals.get("operating_margin_pct")
    if op_margin is None:
        op_inc = sq.get("operating_income")
        rev = sq.get("revenue")
        if op_inc is not None and rev is not None and rev > 0:
            op_margin = (op_inc / rev) * 100.0
    if op_margin is None:
        failed.append("operating_margin_not_available")
    elif op_margin <= 0:
        failed.append("operating_margin_not_positive")

    if fundamentals.get("going_concern", False):
        failed.append("going_concern_flag")

    # debt_to_equity: prefer flattened; otherwise compute from snapshot_quarter.
    dte = fundamentals.get("debt_to_equity")
    if dte is None:
        td = sq.get("total_debt")
        te = sq.get("total_equity")
        if td is not None and te is not None and te > 0:
            dte = td / te
    if dte is not None and dte > 3.0:
        failed.append("debt_to_equity_above_3.0")

    # fcf gate: prefer flattened free_cash_flow_ttm; otherwise read
    # snapshot_quarter.free_cash_flow.
    fcf = fundamentals.get("free_cash_flow_ttm")
    if fcf is None:
        fcf = sq.get("free_cash_flow")
    if fcf is not None and fcf <= 0:
        failed.append("free_cash_flow_not_positive")

    if not failed:
        return {
            "passes": True,
            "failed_gates": [],
            "reason": "All hard fundamental gates pass (eps>0, op_margin>0, financial_health=sound).",
        }
    return {
        "passes": False,
        "failed_gates": failed,
        "reason": f"Hard fundamental gate failure: {', '.join(failed)}",
    }


def is_quality_long_candidate(
    fundamentals: dict,
    fundamental_score: float,
    network_score: float,
    valuation_score: float,
    alt_data_score: float = 50,
) -> dict:
    """
    Determine if a ticker qualifies for quality-long, and whether to BUY or WATCH.

    Step C v0.6 gating (RULES.md §3.8 hard fundamental gates):
      - Hard gates (binary): eps_ttm > 0, operating_margin_pct > 0,
        no going concern, D/E <= 3.0, FCF > 0.
      - Failure of any gate ⇒ NO TRADE (gate is necessary).
      - Pass + reasonable valuation ⇒ BUY.
      - Pass + expensive valuation ⇒ WATCH.

    The 0-100 scores (fundamental_score, network_score, valuation_score,
    alt_data_score, combined_quality_score) are DESCRIPTORS retained on
    the output for agent reasoning and dashboard display, NOT gates.
    Per RULES.md §11.6, no numeric score by itself can produce a trade
    decision.
    """
    # Combined score is a DESCRIPTOR (not a gate). Retained for agent
    # reasoning + dashboard display only.
    quality_combined = (fundamental_score * 0.4) + (network_score * 0.3) + (alt_data_score * 0.3)

    gates = passes_hard_fundamental_gates(fundamentals)

    descriptors = {
        "fundamental_score": fundamental_score,
        "network_effect_score": network_score,
        "valuation_score": valuation_score,
        "alt_data_score": alt_data_score,
        "combined_quality_score": round(quality_combined, 1),
        "score_meaning": "informational_descriptors_only_per_rules_md_§11.6",
        "hard_gates_passed": gates["passes"],
        "failed_gates": gates["failed_gates"],
    }

    if not gates["passes"]:
        return {
            "qualifies": False,
            "decision_hint": "no_trade",
            **descriptors,
            "reason": gates["reason"],
        }

    # Hard gates pass — valuation determines BUY vs WATCH.
    if valuation_score >= 55:
        return {
            "qualifies": True,
            "decision_hint": "buy",
            **descriptors,
            "reason": "Hard fundamental gates pass + valuation reasonable/attractive.",
        }
    if valuation_score >= 35:
        return {
            "qualifies": True,
            "decision_hint": "watch",
            **descriptors,
            "reason": "Hard fundamental gates pass but valuation not yet attractive. Wait for pullback.",
        }
    return {
        "qualifies": True,
        "decision_hint": "watch",
        **descriptors,
        "reason": "Hard fundamental gates pass but valuation expensive. Wait for significant drawdown.",
    }
