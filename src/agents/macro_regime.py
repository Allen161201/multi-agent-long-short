"""
Macro Regime Module — rule-based dynamic regime classifier using FRED data.

This module ONLY classifies the macro regime and sets allocation limits.
It does NOT select individual stocks.
It does NOT override frozen trading thresholds.

The LLM agent may later EXPLAIN the rule-based result, but the regime
and allocation MUST remain rule-based and reproducible.

Rule version: v0.8.2_ndi_runtime_wired (D4 2026-05-01: wires
compute_ndi() into runner.py before narrative_event runs, additive per
Standing Rule 4. Predecessors: v0.8.1 reverted hardcoded NDI/ADaS
thresholds; v0.8 added §23-§28 NDI/ADaS framework, BIL fixed-income
sleeve, and agent isolation rules; v0.7 credit-stress disambiguation).

Two separate outputs:
  1. macro_regime  (controls allocation MAXIMUM CAPS — FROZEN table)
     Crisis / Poor / Normal / Strengthening / Overheat
  2. macro_condition  (descriptive, does NOT override allocation)
     Expansionary / Neutral / Moderately Restrictive / Restrictive /
     Easing Shift / Tightening Shift / Stress

Stress score components (unchanged from v0.5):
  Yield Curve:
    10Y-2Y < 0  OR  10Y-3M < 0           → +1
    10Y-2Y < -0.75  OR  10Y-3M < -1.00   → +2
  Unemployment:
    UNRATE >= 5.0                         → +1
    UNRATE >= 6.0                         → +2
    UNRATE 3-month change >= 0.3          → +1
    UNRATE 3-month change >= 0.5          → +2
  Inflation:
    CPI YoY > 3.0                         → +1
    CPI YoY > 5.0                         → +2
  Policy Rate:
    DFF >= 3.5                            → +1
    DFF >= 5.0                            → +2

Regime Mapping — v0.6 5-tier (Step C Decision 4):
  Crisis and Overheat both correspond to high stress, but they are
  different ends of the cycle:
    - Crisis      = high stress + recession signal (UNRATE >= 5.0 OR
                    UNRATE 3-month change >= 0.3pp)
    - Overheat    = high stress + late-cycle signal (CPI YoY > 4.0 OR
                    DFF >= 4.0) AND no recession signal
  Strengthening sits between Normal and the late-cycle peak (low
  stress + healthy unemployment + no recession trend).

  recession_signal  = UNRATE >= 5.0  OR  UNRATE_3mo_change >= 0.3  OR
                      hy_oas_recession (HY OAS > 6.00%)
  overheat_signal   = (CPI YoY > 4.0  OR  DFF >= 4.0)  AND  not recession_signal
  hy_oas_recession  = HY OAS > 6.00%   (600 bps — credit-stress side-signal)
  hy_oas_crisis     = HY OAS > 8.00%   (800 bps — promotes crisis at score>=4)
  hy_oas_risk_on    = HY OAS < 3.50%   (350 bps — corroborates strengthening)

  if score >= 6 and recession_signal              → Crisis
  if score >= 4 and hy_oas_crisis                 → Crisis  (credit stress dominates)
  if score >= 6 and overheat_signal               → Overheat
  if score in (4, 5) and recession_signal         → Poor
  if score in (4, 5) and overheat_signal          → Overheat
  if score in (4, 5)                              → Poor   (stress without confirming side)
  if score in (2, 3) and recession_signal         → Poor
  if score in (2, 3) and overheat_signal          → Overheat
  if score in (2, 3)                              → Normal (mild stress, no clear signal)
  if score <= 1 and hy_oas_recession              → Poor   (hidden credit stress)
  if score <= 1 and unemp < 4.5 and not rising
       and not hy_oas_recession                   → Strengthening
  else                                            → Normal

This 5-bucket cutoff is the initial Step C calibration. Cutoffs are
documented here so a future polish pass can refine them against
historical FRED replays without changing the rule API.

Allocation (FROZEN — matches config/frozen_rules_v0.6_agentic_allocation_5regime.yaml,
RULES.md §4.7.table). Values are MAXIMUM CAPS, agent has discretion
below the cap (including holding the residual as cash):
  Crisis:        30% FI / 70% equity (max)
  Poor:          40% FI / 60% equity (max)
  Normal:        60% FI / 40% equity (max)
  Strengthening: 70% FI / 30% equity (max)
  Overheat:      80% FI / 20% equity (max)
"""
from datetime import datetime

DATA_UNAVAILABLE = "Data unavailable"

# ═══════════════════════════════════════════════════════════════
# REGIME ALLOCATION TABLE — v0.6 5-regime maximum-cap (Step C
# Decision 1, frozen as v0.6_agentic_allocation_5regime_stepc).
# Each value is an UPPER BOUND, not a fixed target. Agent has
# discretion to allocate below the equity cap and hold the residual
# as cash.
# ═══════════════════════════════════════════════════════════════
REGIME_ALLOCATION = {
    "crisis": {
        "fixed_income_pct": 30,
        "equity_pct": 70,
        "equity_discipline": "dislocation_opportunity_review",
        "equity_restriction": "dislocation_opportunity_review",
    },
    "poor": {
        "fixed_income_pct": 40,
        "equity_pct": 60,
        "equity_discipline": "dislocation_review",
        "equity_restriction": "dislocation_review",
    },
    "normal": {
        "fixed_income_pct": 60,
        "equity_pct": 40,
        "equity_discipline": "strict_quality_and_valuation",
        "equity_restriction": "strict_quality_and_valuation",
    },
    "strengthening": {
        "fixed_income_pct": 70,
        "equity_pct": 30,
        "equity_discipline": "strict_quality_and_valuation",
        "equity_restriction": "strict_quality_and_valuation",
    },
    "overheat": {
        "fixed_income_pct": 80,
        "equity_pct": 20,
        "equity_discipline": "strict_quality_and_valuation_caution",
        "equity_restriction": "strict_quality_and_valuation_caution",
    },
}

# ═══════════════════════════════════════════════════════════════
# STRESS SCORE RULES (cannot be overridden by LLM)
# ═══════════════════════════════════════════════════════════════

def _compute_stress_score(
    yc_10y2y: float | None,
    yc_10y3m: float | None,
    unemp: float | None,
    unemp_prior: float | None,
    cpi_yoy: float | None,
    dff: float | None,
) -> tuple[int, list[dict]]:
    """
    Compute total stress score and return individual rule triggers.

    Returns:
        (total_score, list_of_trigger_dicts)
        Each trigger dict:
          {"rule": str, "input_value": ..., "threshold": str,
           "points": int, "category": str}
    """
    score = 0
    triggers = []

    def add(rule, input_val, threshold, pts, category):
        nonlocal score
        score += pts
        triggers.append({
            "rule": rule,
            "input_value": input_val,
            "threshold": threshold,
            "points": pts,
            "category": category,
        })

    # ── Yield Curve ──
    yc_inverted_mild = False
    yc_inverted_deep = False

    if yc_10y2y is not None and yc_10y2y < -0.75:
        yc_inverted_deep = True
    elif yc_10y3m is not None and yc_10y3m < -1.00:
        yc_inverted_deep = True

    if not yc_inverted_deep:
        # Check mild inversion
        if yc_10y2y is not None and yc_10y2y < 0:
            yc_inverted_mild = True
        elif yc_10y3m is not None and yc_10y3m < 0:
            yc_inverted_mild = True

    if yc_inverted_deep:
        spread_shown = yc_10y2y if yc_10y2y is not None else yc_10y3m
        add("Yield curve deeply inverted", f"{spread_shown:+.2f}%",
            "10Y-2Y < -0.75 or 10Y-3M < -1.00", 2, "yield_curve")
    elif yc_inverted_mild:
        spread_shown = yc_10y2y if yc_10y2y is not None else yc_10y3m
        add("Yield curve inverted", f"{spread_shown:+.2f}%",
            "10Y-2Y < 0 or 10Y-3M < 0", 1, "yield_curve")

    # ── Unemployment Level ──
    if unemp is not None:
        if unemp >= 6.0:
            add("High unemployment", f"{unemp}%", "UNRATE >= 6.0", 2, "unemployment")
        elif unemp >= 5.0:
            add("Elevated unemployment", f"{unemp}%", "UNRATE >= 5.0", 1, "unemployment")

    # ── Unemployment 3-Month Change ──
    if unemp is not None and unemp_prior is not None:
        delta = round(unemp - unemp_prior, 2)
        if delta >= 0.5:
            add("Unemployment surging", f"+{delta}pp (3mo)",
                "3-month change >= 0.5", 2, "unemployment_change")
        elif delta >= 0.3:
            add("Unemployment rising", f"+{delta}pp (3mo)",
                "3-month change >= 0.3", 1, "unemployment_change")

    # ── Inflation ──
    if cpi_yoy is not None:
        if cpi_yoy > 5.0:
            add("Very high inflation", f"{cpi_yoy}%", "CPI YoY > 5.0", 2, "inflation")
        elif cpi_yoy > 3.0:
            add("Above-target inflation", f"{cpi_yoy}%", "CPI YoY > 3.0", 1, "inflation")

    # ── Policy Rate ──
    if dff is not None:
        if dff >= 5.0:
            add("Very restrictive policy rate", f"{dff}%", "DFF >= 5.0", 2, "policy_rate")
        elif dff >= 3.5:
            add("Elevated policy rate", f"{dff}%", "DFF >= 3.5", 1, "policy_rate")

    return score, triggers


# ═══════════════════════════════════════════════════════════════
# REGIME MAPPING (5-tier — Step C v0.6 Decision 4 + v0.7 credit-spread
# side-signal per RULES.md §4.12)
# ═══════════════════════════════════════════════════════════════

# Credit-spread side-signal thresholds (RULES.md §4.12, v0.7 binding).
# All values in % (e.g. 6.00 == 600 bps). Descriptors only — they are
# inputs to the rule-based classifier, NOT independent trading rules
# (per §11.6).
HY_OAS_RECESSION_BPS_PCT = 6.00   # 600 bps  — recession_signal contribution
HY_OAS_CRISIS_BPS_PCT = 8.00      # 800 bps  — crisis-level contribution
HY_OAS_RISK_ON_BPS_PCT = 3.50     # 350 bps  — risk-on corroboration


def _credit_signals(hy_oas: float | None) -> dict:
    """Translate raw HY OAS (%) into the three boolean side-signals
    documented in §4.12. Returns a dict so the call site stays readable."""
    if hy_oas is None:
        return {"recession": False, "crisis": False, "risk_on": False,
                 "value": None}
    return {
        "recession": hy_oas > HY_OAS_RECESSION_BPS_PCT,
        "crisis":    hy_oas > HY_OAS_CRISIS_BPS_PCT,
        "risk_on":   hy_oas < HY_OAS_RISK_ON_BPS_PCT,
        "value":     hy_oas,
    }


def _score_to_regime(
    score: int,
    *,
    unemp: float | None = None,
    unemp_prior: float | None = None,
    cpi_yoy: float | None = None,
    dff: float | None = None,
    hy_oas: float | None = None,
) -> str:
    """Map stress score to one of the 5 v0.6/v0.7 regimes.

    Rule-based, reproducible. Distinguishes Crisis (recession) from
    Overheat (late-cycle) using unemployment vs inflation/policy rate
    side-signals. v0.7 adds HY OAS credit-stress as a third side-signal
    that ORs into recession_signal and (via the crisis threshold) can
    promote a high-but-not-top stress score to crisis when credit is
    visibly distressed (§4.12).
    """
    unemp_high = unemp is not None and unemp >= 5.0
    unemp_rising = (
        unemp is not None and unemp_prior is not None
        and (unemp - unemp_prior) >= 0.3
    )
    credit = _credit_signals(hy_oas)
    recession_signal = unemp_high or unemp_rising or credit["recession"]

    inflation_hot = cpi_yoy is not None and cpi_yoy > 4.0
    rates_restrictive = dff is not None and dff >= 4.0
    overheat_signal = (not recession_signal) and (inflation_hot or rates_restrictive)

    if score >= 6 and recession_signal:
        return "crisis"
    # Credit-stress crisis promotion: HY OAS > 800 bps lifts a score>=4
    # regime to crisis even when unemployment hasn't caught up yet
    # (credit dislocation often leads UNRATE by 1–2 quarters).
    if score >= 4 and credit["crisis"]:
        return "crisis"
    if score >= 6 and overheat_signal:
        return "overheat"
    if score >= 4 and recession_signal:
        return "poor"
    if score >= 4 and overheat_signal:
        return "overheat"
    if score >= 4:
        return "poor"
    # Moderate stress (2-3) — defer to side signals when present so that
    # recession-flavored stress is "poor" and late-cycle-flavored stress
    # is "overheat" even before reaching the high-stress score band.
    if score >= 2 and recession_signal:
        return "poor"
    if score >= 2 and overheat_signal:
        return "overheat"
    if score >= 2:
        return "normal"
    # Low stress (0-1). HY OAS recession threshold can demote
    # strengthening to poor when credit is stressed even though the
    # macro stress score is still low (the canonical 2007–08 lead-time
    # case: credit cracks before UNRATE moves).
    if credit["recession"]:
        return "poor"
    if unemp is not None and unemp < 4.5 and not unemp_rising:
        return "strengthening"
    return "normal"


# ═══════════════════════════════════════════════════════════════
# POLICY CONDITION CLASSIFIER
# ═══════════════════════════════════════════════════════════════

def _classify_condition(
    dff: float | None,
    dff_prior: float | None,
    cpi_yoy: float | None,
    stress_score: int,
) -> tuple[str, str]:
    """
    Classify the descriptive macro_condition.

    Returns:
        (condition_label, condition_reason)

    Priority order (first match wins):
      1. Stress (score >= 6)
      2. Easing Shift (DFF fell >= 0.50)
      3. Tightening Shift (DFF rose >= 0.50)
      4. Restrictive (DFF >= 4.5)
      5. Moderately Restrictive (DFF >= 3.0 and CPI > 2.5)
      6. Neutral / Supportive (DFF < 2.5 and CPI <= 3.0)
      7. Expansionary (fallback)
    """
    if stress_score >= 6:
        return "Stress", f"Stress score = {stress_score} (>= 6)"

    if dff is not None and dff_prior is not None:
        dff_delta = round(dff - dff_prior, 2)
        if dff_delta <= -0.50:
            return "Easing Shift", f"DFF fell {dff_delta:+.2f}pp (from {dff_prior}% to {dff}%)"
        if dff_delta >= 0.50:
            return "Tightening Shift", f"DFF rose {dff_delta:+.2f}pp (from {dff_prior}% to {dff}%)"

    if dff is not None and dff >= 4.5:
        return "Restrictive", f"DFF = {dff}% (>= 4.5)"

    cpi_val = cpi_yoy if cpi_yoy is not None else 0
    if dff is not None and dff >= 3.0 and cpi_val > 2.5:
        return "Moderately Restrictive", f"DFF = {dff}% (>= 3.0) and CPI YoY = {cpi_val}% (> 2.5)"

    if dff is not None and dff < 2.5 and cpi_val <= 3.0:
        return "Neutral", f"DFF = {dff}% (< 2.5) and CPI YoY = {cpi_val}% (<= 3.0)"

    return "Expansionary", "Default: no restrictive conditions met"


# ═══════════════════════════════════════════════════════════════
# MAIN CLASSIFIER
# ═══════════════════════════════════════════════════════════════

def classify_regime(indicators: dict) -> dict:
    """
    Classify macro regime from FRED indicators using the stress-score
    rule system. Outputs both macro_regime (controls allocation) and
    macro_condition (descriptive only).

    Args:
        indicators: dict from fred_adapter.get_macro_indicators()

    Returns:
        dict with macro_regime, macro_condition, stress_score,
        triggers, allocation, confidence, evidence, and safety info.
    """
    now = datetime.now().isoformat()

    # ── Extract values ──
    yc_10y2y = _val(indicators, "yield_curve_10y2y")
    yc_10y3m = _val(indicators, "yield_curve_10y3m")
    dff = _val(indicators, "fed_funds_rate")
    cpi_yoy = _val(indicators, "cpi_yoy")
    unemp = _val(indicators, "unemployment_rate")
    # v0.7 credit side-signal: HY OAS (BAMLH0A0HYM2). Optional input —
    # absence does not break classification; the regime mapping defaults
    # to the v0.6 behaviour when hy_oas is None.
    hy_oas = _val(indicators, "hy_oas")

    # Prior observations (for shift detection)
    dff_prior = _prior_val(indicators, "fed_funds_rate")
    unemp_prior = _prior_val(indicators, "unemployment_rate")

    # Count available indicators (HY OAS is intentionally NOT counted in
    # the core 5 used for confidence — it is a side-signal, not a core
    # input; see §4.12 / §11.6 descriptors-not-rules).
    core_vals = [yc_10y2y, yc_10y3m, dff, cpi_yoy, unemp]
    n_available = sum(1 for v in core_vals if v is not None)
    n_total = 5

    # ── No-data fallback ──
    if n_available == 0:
        return _build_output(
            regime="normal",
            condition="Neutral",
            condition_reason="No macro data — defaulting to neutral 60/40 cap",
            stress_score=0,
            triggers=[],
            confidence="low",
            evidence=["No macro data available — defaulting to normal (60% FI / 40% equity max cap)"],
            missing_warnings=["All FRED indicators unavailable"],
            indicators=indicators,
            n_available=0,
            n_total=n_total,
            credit_signals=_credit_signals(hy_oas),
        )

    # ── Compute stress score ──
    stress_score, triggers = _compute_stress_score(
        yc_10y2y=yc_10y2y,
        yc_10y3m=yc_10y3m,
        unemp=unemp,
        unemp_prior=unemp_prior,
        cpi_yoy=cpi_yoy,
        dff=dff,
    )

    # ── Map to regime (5-tier, with side-signal disambiguation) ──
    regime = _score_to_regime(
        stress_score,
        unemp=unemp,
        unemp_prior=unemp_prior,
        cpi_yoy=cpi_yoy,
        dff=dff,
        hy_oas=hy_oas,
    )
    credit_signals = _credit_signals(hy_oas)

    # ── Classify condition ──
    condition, condition_reason = _classify_condition(
        dff=dff,
        dff_prior=dff_prior,
        cpi_yoy=cpi_yoy,
        stress_score=stress_score,
    )

    # ── Confidence ──
    if n_available >= 4:
        confidence = "high"
    elif n_available >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    # ── Evidence summary ──
    evidence = []
    if yc_10y2y is not None:
        sign = "positive" if yc_10y2y >= 0 else "inverted"
        evidence.append(f"Yield curve 10Y-2Y {sign} ({yc_10y2y:+.2f}%)")
    if yc_10y3m is not None:
        sign = "positive" if yc_10y3m >= 0 else "inverted"
        evidence.append(f"Yield curve 10Y-3M {sign} ({yc_10y3m:+.2f}%)")
    if unemp is not None:
        level = "Low" if unemp < 4.5 else "Elevated" if unemp < 5.5 else "High"
        evidence.append(f"{level} unemployment ({unemp}%)")
    if unemp_prior is not None and unemp is not None:
        delta = round(unemp - unemp_prior, 2)
        evidence.append(f"Unemployment 3-month change: {delta:+.2f}pp")
    if cpi_yoy is not None:
        level = "near target" if cpi_yoy <= 3.0 else "above target" if cpi_yoy <= 5.0 else "very high"
        evidence.append(f"Inflation {level} ({cpi_yoy}%)")
    if dff is not None:
        evidence.append(f"Effective Fed Funds Rate (DFF) = {dff}%")
    if dff_prior is not None and dff is not None:
        delta = round(dff - dff_prior, 2)
        evidence.append(f"DFF change from prior obs: {delta:+.2f}pp")
    if hy_oas is not None:
        if credit_signals["crisis"]:
            tag = "crisis-level credit stress (>800 bps)"
        elif credit_signals["recession"]:
            tag = "elevated credit stress (>600 bps)"
        elif credit_signals["risk_on"]:
            tag = "compressed / risk-on (<350 bps)"
        else:
            tag = "mid-cycle"
        evidence.append(f"HY OAS = {hy_oas:.2f}% — {tag}")

    # ── Missing data warnings ──
    missing = []
    for key in ["yield_curve_10y2y", "yield_curve_10y3m",
                 "fed_funds_rate", "cpi_yoy", "unemployment_rate"]:
        ind = indicators.get(key, {})
        if not isinstance(ind, dict):
            missing.append(f"{key}: {DATA_UNAVAILABLE}")
        elif ind.get("missing_data_flag") or ind.get("value") is None:
            missing.append(f"{key}: {DATA_UNAVAILABLE}")

    return _build_output(
        regime=regime,
        condition=condition,
        condition_reason=condition_reason,
        stress_score=stress_score,
        triggers=triggers,
        confidence=confidence,
        evidence=evidence,
        missing_warnings=missing,
        indicators=indicators,
        n_available=n_available,
        n_total=n_total,
        credit_signals=credit_signals,
    )


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _val(indicators: dict, key: str):
    """Extract numeric value from an indicator, returning None if missing."""
    ind = indicators.get(key, {})
    if not isinstance(ind, dict):
        return None
    v = ind.get("value")
    if v is None or v == DATA_UNAVAILABLE:
        return None
    return v


def _prior_val(indicators: dict, key: str):
    """
    Extract the prior observation value for shift detection.
    The FRED adapter stores this as 'prior_value' when it fetches
    multiple observations for a series.
    """
    ind = indicators.get(key, {})
    if not isinstance(ind, dict):
        return None
    v = ind.get("prior_value")
    if v is None or v == DATA_UNAVAILABLE:
        return None
    return v


def _build_output(
    regime: str,
    condition: str,
    condition_reason: str,
    stress_score: int,
    triggers: list[dict],
    confidence: str,
    evidence: list[str],
    missing_warnings: list[str],
    indicators: dict,
    n_available: int,
    n_total: int,
    credit_signals: dict | None = None,
) -> dict:
    """Build the standard macro regime output dict."""
    alloc = REGIME_ALLOCATION[regime]

    # Dynamic label: "{Regime} / {Condition}"
    regime_display = regime.capitalize()
    regime_label = f"{regime_display} / {condition}"

    # Compute data_available_as_of (earliest across all indicators)
    available_dates = []
    for key, ind in indicators.items():
        if key.startswith("_") or not isinstance(ind, dict):
            continue
        aao = ind.get("data_available_as_of")
        if aao and aao != DATA_UNAVAILABLE:
            available_dates.append(aao)

    data_available_as_of = min(available_dates) if available_dates else DATA_UNAVAILABLE

    # Lookahead safe = True if all non-missing indicators have timestamps
    all_have_timestamps = all(
        ind.get("data_available_as_of") not in (None, DATA_UNAVAILABLE)
        for key, ind in indicators.items()
        if not key.startswith("_") and isinstance(ind, dict) and not ind.get("missing_data_flag")
    )

    # Collect FRED values used for transparency
    fred_values_used = {}
    for key in ["yield_curve_10y2y", "yield_curve_10y3m",
                 "fed_funds_rate", "cpi_yoy", "unemployment_rate"]:
        ind = indicators.get(key, {})
        if isinstance(ind, dict) and ind.get("value") is not None:
            fred_values_used[key] = {
                "value": ind["value"],
                "observation_date": ind.get("observation_date", DATA_UNAVAILABLE),
                "source": ind.get("source", "unknown"),
                "series_id": ind.get("series_id", ""),
            }
            if ind.get("prior_value") is not None:
                fred_values_used[key]["prior_value"] = ind["prior_value"]
                fred_values_used[key]["prior_observation_date"] = ind.get(
                    "prior_observation_date", DATA_UNAVAILABLE)

    return {
        # Core outputs
        "macro_regime": regime,
        "macro_condition": condition,
        "condition_reason": condition_reason,
        "regime_label": regime_label,
        "stress_score": stress_score,
        "stress_triggers": triggers,

        # Allocation (regime sets sleeve sizes + discipline label)
        "equity_allocation_cap": alloc["equity_pct"],
        "fixed_income_base_allocation": alloc["fixed_income_pct"],
        "equity_discipline": alloc.get("equity_discipline", alloc["equity_restriction"]),
        "equity_restriction": alloc["equity_restriction"],

        # Confidence & evidence
        "macro_confidence": confidence,
        "macro_evidence_summary": evidence,

        # Point-in-time safety
        "data_available_as_of": data_available_as_of,
        "lookahead_safe": all_have_timestamps,
        "missing_data_warnings": missing_warnings,
        "indicators_available": f"{n_available}/{n_total}",

        # Transparency
        "fred_values_used": fred_values_used,

        # Credit-spread side-signals (v0.7, §4.12). Always present;
        # `credit_signal_inputs.value=None` when HY OAS unavailable.
        "credit_signal_inputs": (
            credit_signals
            if credit_signals is not None
            else {"recession": False, "crisis": False,
                   "risk_on": False, "value": None}
        ),

        # Metadata
        "classified_at": datetime.now().isoformat(),
        "classifier_version": "v0.9.0_pass8_hardrule",
        "indicators": indicators,
    }
