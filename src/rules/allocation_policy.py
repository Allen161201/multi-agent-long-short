"""
Allocation Policy — regime-based MAXIMUM CAPS for fixed-income/equity.

v0.6 philosophy (Step C Decision 1, frozen as
v0.6_agentic_allocation_5regime_stepc):
  - The macro regime sets SLEEVE MAXIMUM CAPS (FI cap, equity cap).
    Caps are upper bounds, not fixed targets — the agent has discretion
    to allocate below the equity cap and hold the residual as cash
    (Overheat 70/20/10 fixed/equity/cash is permissible).
  - Regime does NOT hard-block equity buying based on a numeric quality
    score. Quality / valuation / fraud filters live in the quality_long
    rules and agent reasoning.
  - 5 regimes (Crisis / Poor / Normal / Strengthening / Overheat)
    span the cycle from recession dislocation to late-cycle peak.
"""


REGIMES = {
    "crisis": {
        "label": "Crisis Environment",
        "fi_pct": 30,
        "eq_pct": 70,
        "discipline": "dislocation_opportunity_review",
        "discipline_note": "Active hunt for fundamentally good companies at distressed prices. Leveraged broad-market ETFs (SPY/TQQQ/SPXL) permitted as long instruments in Crisis only.",
    },
    "poor": {
        "label": "Poor Environment",
        "fi_pct": 40,
        "eq_pct": 60,
        "discipline": "dislocation_review",
        "discipline_note": "Look for good companies whose prices have dislocated below fair value.",
    },
    "normal": {
        "label": "Normal / Strong Economy",
        "fi_pct": 60,
        "eq_pct": 40,
        "discipline": "strict_quality_and_valuation",
        "discipline_note": "Buy good companies at acceptable prices. Pass on overpriced names.",
    },
    "strengthening": {
        "label": "Strengthening Environment",
        "fi_pct": 70,
        "eq_pct": 30,
        "discipline": "strict_quality_and_valuation",
        "discipline_note": "Same selection bar as Normal. Smaller equity cap reflects approaching late-cycle conditions; agent biases toward already-held network-effect names.",
    },
    "overheat": {
        "label": "Overheat / Late-Cycle",
        "fi_pct": 80,
        "eq_pct": 20,
        "discipline": "strict_quality_and_valuation_caution",
        "discipline_note": "Cap equity exposure aggressively. Cash residual common (e.g. 70/20/10 fixed/equity/cash). Prefer T-bill yield over chasing late-cycle equity beta.",
    },
}


def get_allocation(regime: str) -> dict:
    """Return allocation policy (maximum caps) for the given regime."""
    r = REGIMES.get(regime, REGIMES["normal"])
    return {
        "regime": regime,
        "label": r["label"],
        # Cap semantics (Step C v0.6): upper bound, not fixed target.
        "fixed_income_max_pct": r["fi_pct"],
        "equity_max_pct": r["eq_pct"],
        # Legacy field aliases retained for backward-compat with audit
        # logs and dashboard JS that still read fixed_income_pct/equity_pct.
        # The value is the SAME number — only the semantic interpretation
        # changes (cap, not target). Callers may allocate below.
        "fixed_income_pct": r["fi_pct"],
        "equity_pct": r["eq_pct"],
        "policy_kind": "maximum_caps_with_discretion",
        "discretion_clause": (
            "Agent may hold cash or allocate below the equity max based on "
            "macro signals, valuation, and yield curve."
        ),
        "equity_discipline": r["discipline"],
        "equity_discipline_note": r["discipline_note"],
        # Legacy field name kept for backward-compat. Value is the
        # discipline label, never "none".
        "equity_restriction": r["discipline"],
        "no_margin": True,
        "no_derivatives": True,
    }


def is_equity_allowed(regime: str, quality_score: float = 0) -> bool:
    """
    v0.5+: macro regime no longer hard-blocks equity entry based on a
    numeric quality score. Quality / valuation / fraud filters are
    enforced by the quality_long rules, agent reasoning, and the
    baseline exclusions in the surge-short sleeve.

    Function signature retained for backward compatibility with
    callers; always returns True.
    """
    return True


def determine_regime(macro_indicators: dict | None = None) -> str:
    """
    Determine current macro regime. In MVP, use a simple mock.
    Future: use FRED data, yield curve, VIX, credit spreads, etc.
    """
    if macro_indicators is None:
        return "normal"  # default demo regime (60/40 max cap)
    score = macro_indicators.get("composite_score", 50)
    if score >= 80:
        return "strengthening"
    elif score >= 60:
        return "normal"
    elif score >= 40:
        return "poor"
    elif score >= 20:
        return "crisis"
    else:
        return "overheat"
