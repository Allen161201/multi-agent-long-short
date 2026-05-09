"""
Tests for the dynamic macro regime classifier (v0.6 5-tier).

Uses synthetic indicator data to verify that the classifier changes dynamically
based on input data, not hard-coded labels.

5 scenarios (Step C v0.6 expectations):
  1. Big Fed Rate Cut + healthy UNRATE → Easing Shift + Strengthening
  2. Deep Yield Curve Inversion (no other stress) → Normal (mild stress only)
  3. Rising Unemployment → Poor (recession signal triggers)
  4. High Inflation + High Rate (no recession) → Overheat
  5. Full Crisis (all stress + recession signal) → Crisis

Run: python -m tests.test_macro_classifier
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.macro_regime import classify_regime, _compute_stress_score, _score_to_regime


# ═══════════════════════════════════════════════════════════════
# SYNTHETIC DATA BUILDER
# ═══════════════════════════════════════════════════════════════

def _make_indicators(
    yc_10y2y=0.50, yc_10y3m=0.60,
    dff=3.0, dff_prior=None,
    cpi_yoy=2.5,
    unemp=4.0, unemp_prior=None,
    source="synthetic",
):
    """Build a minimal indicators dict matching fred_adapter output schema."""
    date = "2026-04-25"

    def _ind(key, series_id, label, value, prior_value=None, prior_obs_date=None):
        d = {
            "series_id": series_id,
            "label": label,
            "value": value,
            "observation_date": date,
            "realtime_start": date,
            "realtime_end": date,
            "data_available_as_of": date,
            "conservative_lag_days": 1,
            "conservative_lag_applied": True,
            "vintage_date": date,
            "missing_data_flag": value is None,
            "source": source,
        }
        if prior_value is not None:
            d["prior_value"] = prior_value
            d["prior_observation_date"] = prior_obs_date or date
        return d

    return {
        "yield_curve_10y2y": _ind("yield_curve_10y2y", "T10Y2Y", "10Y-2Y Spread", yc_10y2y),
        "yield_curve_10y3m": _ind("yield_curve_10y3m", "T10Y3M", "10Y-3M Spread", yc_10y3m),
        "fed_funds_rate": _ind("fed_funds_rate", "DFF", "Effective Fed Funds Rate", dff,
                               prior_value=dff_prior),
        "cpi_yoy": _ind("cpi_yoy", "CPIAUCSL (YoY calc)", "CPI YoY %", cpi_yoy),
        "unemployment_rate": _ind("unemployment_rate", "UNRATE", "Unemployment Rate", unemp,
                                  prior_value=unemp_prior),
        "_api_success": True,
        "_series_retrieved": ["yield_curve_10y2y", "yield_curve_10y3m",
                              "fed_funds_rate", "cpi_yoy", "unemployment_rate"],
        "_series_missing": [],
    }


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════

def _run_test(name, indicators, expected_regime, expected_condition,
              min_score=None, max_score=None):
    """Run a single scenario and verify expectations."""
    result = classify_regime(indicators)
    regime = result["macro_regime"]
    condition = result["macro_condition"]
    score = result["stress_score"]
    label = result["regime_label"]
    fi = result["fixed_income_base_allocation"]
    eq = result["equity_allocation_cap"]
    triggers = result["stress_triggers"]

    errors = []
    if regime != expected_regime:
        errors.append(f"  REGIME: expected '{expected_regime}', got '{regime}'")
    if expected_condition and condition != expected_condition:
        errors.append(f"  CONDITION: expected '{expected_condition}', got '{condition}'")
    if min_score is not None and score < min_score:
        errors.append(f"  SCORE: expected >= {min_score}, got {score}")
    if max_score is not None and score > max_score:
        errors.append(f"  SCORE: expected <= {max_score}, got {score}")

    # Verify label is dynamically generated (not hard-coded)
    expected_label = f"{regime.capitalize()} / {condition}"
    if label != expected_label:
        errors.append(f"  LABEL: expected '{expected_label}', got '{label}'")

    status = "PASS" if not errors else "FAIL"
    print(f"\n{'='*70}")
    print(f"  [{status}] Scenario: {name}")
    print(f"{'='*70}")
    print(f"  Regime:       {regime}")
    print(f"  Condition:    {condition}")
    print(f"  Label:        {label}")
    print(f"  Stress Score: {score}")
    print(f"  Allocation:   FI {fi}% / EQ {eq}%")
    print(f"  Confidence:   {result['macro_confidence']}")
    print(f"  Condition Reason: {result['condition_reason']}")
    print(f"  Triggers ({len(triggers)}):")
    for t in triggers:
        print(f"    +{t['points']}  [{t['category']}]  {t['rule']}  (input: {t['input_value']}, threshold: {t['threshold']})")
    if not triggers:
        print(f"    (none)")
    print(f"  Evidence:")
    for e in result["macro_evidence_summary"]:
        print(f"    - {e}")
    if errors:
        print(f"\n  ERRORS:")
        for err in errors:
            print(f"    {err}")
    return status == "PASS"


def test_1_big_fed_rate_cut():
    """Scenario 1: Big Fed Rate Cut → Easing Shift, Strengthening regime.

    DFF dropped from 4.5% to 3.0% (-1.5pp).
    Yield curve positive, low unemployment, controlled inflation.
    Expected: score 0-1, Strengthening regime (low stress + healthy
    UNRATE + no recession trend), Easing Shift condition.
    """
    indicators = _make_indicators(
        yc_10y2y=0.80, yc_10y3m=1.20,
        dff=3.0, dff_prior=4.5,       # fell 1.5pp
        cpi_yoy=2.3,                   # below 3.0 → no stress
        unemp=3.8, unemp_prior=3.7,   # stable
    )
    return _run_test(
        "Big Fed Rate Cut (DFF 4.5 -> 3.0)",
        indicators,
        expected_regime="strengthening",
        expected_condition="Easing Shift",
        max_score=1,
    )


def test_2_deep_yield_curve_inversion():
    """Scenario 2: Deep Yield Curve Inversion (no other stress) → Normal.

    10Y-2Y spread = -1.00, 10Y-3M = -1.50.
    Both deeply inverted; no recession signal (unemp stable at 4.2),
    no overheat signal (cpi 2.8, dff 3.2 below thresholds).
    v0.6 mapping: score 2-3 with no confirming side signal → Normal.
    """
    indicators = _make_indicators(
        yc_10y2y=-1.00, yc_10y3m=-1.50,  # deeply inverted (+2)
        dff=3.2, dff_prior=3.2,           # stable, below 3.5 → no stress
        cpi_yoy=2.8,                      # below 3.0 → no stress
        unemp=4.2, unemp_prior=4.1,      # stable
    )
    return _run_test(
        "Deep Yield Curve Inversion (-1.00 / -1.50)",
        indicators,
        expected_regime="normal",
        expected_condition="Moderately Restrictive",
        min_score=2,
        max_score=3,
    )


def test_3_rising_unemployment():
    """Scenario 3: Rising Unemployment → Poor (recession signal).

    UNRATE jumps from 4.0 to 5.2 (+1.2pp in 3 months).
    v0.6 mapping: score 4-5 + recession_signal (unemp >= 5.0
    OR change >= 0.3pp) → Poor.
    """
    indicators = _make_indicators(
        yc_10y2y=0.30, yc_10y3m=0.40,
        dff=2.0, dff_prior=2.0,           # low, supportive
        cpi_yoy=2.5,
        unemp=5.2, unemp_prior=4.0,      # +1.2pp jump, level >= 5.0
    )
    return _run_test(
        "Rising Unemployment (4.0 -> 5.2, +1.2pp)",
        indicators,
        expected_regime="poor",
        expected_condition="Neutral",
        min_score=2,
        max_score=5,
    )


def test_4_high_inflation_high_rate():
    """Scenario 4: High Inflation + High Rate (no recession) → Overheat.

    CPI YoY = 6.5%, DFF = 5.25%.
    Both hit the +2 stress thresholds. UNRATE stable at 4.0 (no
    recession signal). v0.6 mapping: score 4+ + overheat_signal
    (CPI > 4.0 OR DFF >= 4.0, no recession) → Overheat.
    """
    indicators = _make_indicators(
        yc_10y2y=-0.20, yc_10y3m=-0.40,  # mildly inverted (+1)
        dff=5.25, dff_prior=5.25,         # >= 5.0 → +2
        cpi_yoy=6.5,                      # > 5.0 → +2
        unemp=4.0, unemp_prior=3.9,      # stable
    )
    return _run_test(
        "High Inflation (6.5%) + High Rate (5.25%) — no recession",
        indicators,
        expected_regime="overheat",
        expected_condition="Restrictive",
        min_score=4,
        max_score=6,
    )


def test_5_crisis():
    """Scenario 5: Full Crisis — all signals firing.

    Deep inversion, high unemployment rising, high inflation, very high rates.
    """
    indicators = _make_indicators(
        yc_10y2y=-1.20, yc_10y3m=-1.80,  # deeply inverted (+2)
        dff=5.50, dff_prior=5.00,         # >= 5.0 → +2, rising +0.50
        cpi_yoy=7.0,                      # > 5.0 → +2
        unemp=6.5, unemp_prior=5.8,      # >= 6.0 → +2, +0.7pp → +2
    )
    return _run_test(
        "Full Crisis (all signals)",
        indicators,
        expected_regime="crisis",
        expected_condition="Stress",
        min_score=8,
    )


def test_score_mapping():
    """Verify the score-to-regime mapping function (v0.6).

    The bare score-only path (no side-signal kwargs) falls back to the
    'no confirming signal' branch — score 4+ → poor, score 2-3 → normal,
    score 0-1 with no UNRATE → normal. Strengthening, Crisis, and
    Overheat all require side signals (UNRATE / CPI / DFF) to
    disambiguate.
    """
    print(f"\n{'='*70}")
    print(f"  Score-to-Regime Mapping Test (v0.6, bare score)")
    print(f"{'='*70}")
    # Bare score (no side signals) → fallback path
    mapping = {0: "normal", 1: "normal", 2: "normal", 3: "normal",
               4: "poor", 5: "poor", 6: "poor", 7: "poor", 10: "poor"}
    passed = True
    for score, expected in mapping.items():
        result = _score_to_regime(score)
        status = "OK" if result == expected else "FAIL"
        if result != expected:
            passed = False
        print(f"  Score {score:>2} -> {result:>10}  (expected: {expected})  [{status}]")

    # With side signals — disambiguation cases
    print(f"\n  Score-to-Regime with side signals:")
    side_cases = [
        # (score, kwargs, expected, label)
        (0,  dict(unemp=3.8, unemp_prior=3.7), "strengthening", "low stress + healthy UNRATE"),
        (5,  dict(unemp=5.5, unemp_prior=4.5), "poor",          "moderate stress + recession"),
        (5,  dict(cpi_yoy=5.0, dff=5.0, unemp=4.0), "overheat", "moderate stress + late-cycle"),
        (8,  dict(unemp=6.5, unemp_prior=5.5), "crisis",        "high stress + recession"),
        (8,  dict(cpi_yoy=6.0, dff=5.5, unemp=4.0), "overheat", "high stress + late-cycle"),
    ]
    for score, kw, expected, label in side_cases:
        result = _score_to_regime(score, **kw)
        status = "OK" if result == expected else "FAIL"
        if result != expected:
            passed = False
        print(f"  Score {score:>2} {label:<32} -> {result:>14}  (expected: {expected})  [{status}]")
    return passed


def test_labels_are_dynamic():
    """Verify labels change when inputs change (not hard-coded)."""
    print(f"\n{'='*70}")
    print(f"  Dynamic Label Verification")
    print(f"{'='*70}")

    # Same function, different inputs → different labels
    labels = set()
    scenarios = [
        ("Benign",  dict(yc_10y2y=1.0, dff=2.0, dff_prior=2.0, cpi_yoy=2.0, unemp=3.5)),
        ("Stressed", dict(yc_10y2y=-1.0, dff=5.5, dff_prior=5.5, cpi_yoy=6.0, unemp=6.5)),
        ("Easing",  dict(yc_10y2y=0.5, dff=2.0, dff_prior=4.0, cpi_yoy=2.0, unemp=3.8)),
    ]
    for name, kwargs in scenarios:
        indicators = _make_indicators(**kwargs)
        result = classify_regime(indicators)
        label = result["regime_label"]
        labels.add(label)
        print(f"  {name:>10}: {label}")

    if len(labels) >= 3:
        print(f"\n  [PASS] {len(labels)} unique labels generated — labels are dynamic")
        return True
    else:
        print(f"\n  [FAIL] Only {len(labels)} unique labels — labels may be hard-coded")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  DYNAMIC MACRO CLASSIFIER TEST SUITE")
    print("  v0.6_5regime_stress_overheat (Step C Decision 4)")
    print("=" * 70)

    results = [
        ("Score Mapping", test_score_mapping()),
        ("Dynamic Labels", test_labels_are_dynamic()),
        ("1: Big Fed Rate Cut", test_1_big_fed_rate_cut()),
        ("2: Deep YC Inversion", test_2_deep_yield_curve_inversion()),
        ("3: Rising Unemployment", test_3_rising_unemployment()),
        ("4: High Inflation + Rate", test_4_high_inflation_high_rate()),
        ("5: Full Crisis", test_5_crisis()),
    ]

    print(f"\n\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    passed = sum(1 for _, r in results if r)
    total = len(results)
    for name, r in results:
        print(f"  {'PASS' if r else 'FAIL':>4}  {name}")
    print(f"\n  {passed}/{total} tests passed")
    if passed == total:
        print(f"  ALL TESTS PASSED")
    else:
        print(f"  SOME TESTS FAILED")
    print(f"{'='*70}\n")

    sys.exit(0 if passed == total else 1)
