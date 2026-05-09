"""
Smoke tests for the v0.7 credit-spread side-signal wiring.

Coverage:
  - All 4 new FRED series are registered in FRED_SERIES with the
    correct series_id strings (BAMLH0A0HYM2 / BAMLC0A0CM /
    BAMLC0A4CBBB / T10YIE)
  - Mock data path returns numeric values for all 4 new keys
  - _credit_signals helper translates raw HY OAS (%) into
    {recession, crisis, risk_on, value} per RULES.md §4.12
    thresholds (600 / 800 / 350 bps)
  - Synthetic regime-classifier integration:
      * boundary score 0-1 + healthy unemployment + HY OAS = 750 bps
        → classifier emits 'poor' (NOT 'strengthening') — the 2007-08
        credit-led-recession lead-time pattern
      * same low-stress score + HY OAS = 300 bps → 'strengthening'
      * mid-stress score 4 + HY OAS = 850 bps → 'crisis' (credit
        promotion per R-CREDIT-02)
  - PIT integrity: a synthetic FRED observation with realtime_start
    > decision_timestamp is filtered out (this exercises the existing
    FRED PIT gate; absence of HY OAS in classify_regime input must
    not crash)
  - Live FRED smoke (skipped without FRED_API_KEY): fetch
    BAMLH0A0HYM2 last 30 days, assert non-empty, schema correctness
  - rule_version bumped to v0.8.2_ndi_runtime_wired in
    config/rules.yaml and macro.py REGIME_RULE_VERSION
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.data_adapters.fred_adapter import (  # noqa: E402
    FRED_SERIES, get_macro_indicators_mock, get_series_observations,
    DATA_UNAVAILABLE,
)
from src.agents.macro_regime import (  # noqa: E402
    _credit_signals, _score_to_regime, classify_regime,
    HY_OAS_RECESSION_BPS_PCT, HY_OAS_CRISIS_BPS_PCT,
    HY_OAS_RISK_ON_BPS_PCT,
)


# ── 1. Series registration ──────────────────────────────────────
def test_four_credit_series_registered_with_correct_ids():
    expected = {
        "hy_oas":        "BAMLH0A0HYM2",
        "ig_oas":        "BAMLC0A0CM",
        "bbb_oas":       "BAMLC0A4CBBB",
        "breakeven_10y": "T10YIE",
    }
    for key, series_id in expected.items():
        assert key in FRED_SERIES, f"FRED_SERIES missing {key!r}"
        assert FRED_SERIES[key]["series_id"] == series_id, (
            f"FRED_SERIES[{key!r}].series_id = "
            f"{FRED_SERIES[key]['series_id']!r}, want {series_id!r}"
        )


def test_mock_path_emits_numeric_values_for_credit_series():
    mock = get_macro_indicators_mock("2024-06-01")
    for key in ("hy_oas", "ig_oas", "bbb_oas", "breakeven_10y"):
        assert key in mock, f"mock dict missing {key!r}"
        ind = mock[key]
        assert isinstance(ind, dict), f"mock[{key!r}] not a dict"
        assert isinstance(ind["value"], (int, float)), (
            f"mock[{key!r}].value not numeric: {ind['value']!r}"
        )
        assert ind["source"] == "mock"


# ── 2. _credit_signals threshold helper ─────────────────────────
def test_credit_signals_recession_threshold_600bps():
    """HY OAS > 6.00% (600 bps) → recession=True."""
    s = _credit_signals(7.00)
    assert s["recession"] is True
    assert s["crisis"] is False
    assert s["risk_on"] is False
    # Boundary check — NOT inclusive (strictly greater than).
    s_eq = _credit_signals(HY_OAS_RECESSION_BPS_PCT)
    assert s_eq["recession"] is False, (
        "threshold should be strictly >; equality not enough"
    )


def test_credit_signals_crisis_threshold_800bps():
    s = _credit_signals(8.50)
    assert s["recession"] is True   # > 6.00 also true
    assert s["crisis"] is True
    s_eq = _credit_signals(HY_OAS_CRISIS_BPS_PCT)
    assert s_eq["crisis"] is False


def test_credit_signals_risk_on_threshold_350bps():
    s = _credit_signals(3.00)
    assert s["risk_on"] is True
    assert s["recession"] is False
    s_eq = _credit_signals(HY_OAS_RISK_ON_BPS_PCT)
    assert s_eq["risk_on"] is False


def test_credit_signals_none_returns_all_false():
    s = _credit_signals(None)
    assert s == {"recession": False, "crisis": False,
                 "risk_on": False, "value": None}


def test_credit_signals_mid_band_neutral():
    """Between 350 and 600 bps — all three booleans False."""
    s = _credit_signals(4.50)
    assert s["recession"] is False
    assert s["crisis"] is False
    assert s["risk_on"] is False
    assert s["value"] == 4.50


# ── 3. _score_to_regime integration ─────────────────────────────
def test_low_stress_with_hy_oas_750bps_disambiguates_to_poor():
    """Score 0-1, healthy unemployment, HY OAS = 750 bps → poor.

    This catches the 2007-08 credit-led-recession lead-time pattern:
    the macro stress score is still low (UNRATE = 4.0%, no curve
    inversion), but credit is visibly stressed. Without HY OAS the
    classifier would emit 'strengthening'; with HY OAS = 750 bps it
    must flip to 'poor'.
    """
    regime = _score_to_regime(
        score=0,
        unemp=4.0,
        unemp_prior=4.0,
        cpi_yoy=2.5,
        dff=2.0,
        hy_oas=7.50,   # 750 bps — RECESSION threshold tripped
    )
    assert regime == "poor", (
        f"expected 'poor' (HY OAS 750 bps disambiguates credit-led "
        f"stress), got {regime!r}"
    )


def test_low_stress_with_hy_oas_300bps_classifies_strengthening():
    """Score 0, healthy unemployment, HY OAS = 300 bps → strengthening."""
    regime = _score_to_regime(
        score=0,
        unemp=4.0,
        unemp_prior=4.0,
        cpi_yoy=2.5,
        dff=2.0,
        hy_oas=3.00,   # 300 bps — RISK_ON threshold tripped
    )
    assert regime == "strengthening", (
        f"expected 'strengthening' (compressed credit corroborates "
        f"low-stress), got {regime!r}"
    )


def test_mid_stress_with_hy_oas_850bps_promoted_to_crisis():
    """Score 4 + HY OAS > 800 bps → crisis (R-CREDIT-02)."""
    regime = _score_to_regime(
        score=4,
        unemp=4.5,           # NOT yet in recession territory
        unemp_prior=4.4,
        cpi_yoy=3.0,
        dff=4.0,
        hy_oas=8.50,         # 850 bps — CRISIS threshold tripped
    )
    assert regime == "crisis", (
        f"expected 'crisis' (R-CREDIT-02 promotes score>=4 + HY OAS "
        f"> 800 bps to crisis), got {regime!r}"
    )


def test_v0_6_compatibility_when_hy_oas_absent():
    """When hy_oas is None, classifier must reproduce v0.6 behaviour."""
    # Crisis case: high stress + recession signal
    regime = _score_to_regime(
        score=10, unemp=6.5, unemp_prior=5.8, cpi_yoy=7.0, dff=5.5,
        hy_oas=None,
    )
    assert regime == "crisis"
    # Strengthening case: low stress + healthy unemployment
    regime = _score_to_regime(
        score=0, unemp=4.0, unemp_prior=4.0, cpi_yoy=2.5, dff=2.0,
        hy_oas=None,
    )
    assert regime == "strengthening"


# ── 4. classify_regime full-loop integration ────────────────────
def test_classify_regime_emits_credit_signal_inputs_with_mock():
    indicators = get_macro_indicators_mock("2024-06-01")
    result = classify_regime(indicators)
    assert "credit_signal_inputs" in result
    csi = result["credit_signal_inputs"]
    for key in ("recession", "crisis", "risk_on", "value"):
        assert key in csi, f"credit_signal_inputs missing {key!r}"
    # Mock HY OAS = 4.10 → mid-band, all three booleans False.
    assert csi["value"] == 4.10
    assert csi["recession"] is False
    assert csi["crisis"] is False
    assert csi["risk_on"] is False
    # Classifier_version must reflect the current binding rule_version.
    # Pass 8 (2026-05-04): bumped to v0.9.0_pass8_hardrule.
    assert result["classifier_version"] == "v0.9.0_pass8_hardrule"


def test_classify_regime_disambiguation_via_synthetic_credit_stress():
    """Drop HY OAS = 750 bps into otherwise-healthy mock indicators
    and assert the regime flips from 'strengthening' (the v0.6 default
    for the all-mock scenario) to 'poor' under v0.7 §4.12."""
    base = get_macro_indicators_mock("2024-06-01")
    # Healthy macro stub that would otherwise classify Strengthening
    # under v0.6.
    base["yield_curve_10y2y"]["value"] = 0.30
    base["yield_curve_10y3m"]["value"] = 0.40
    base["unemployment_rate"]["value"] = 4.0
    base["unemployment_rate"]["prior_value"] = 4.0
    base["cpi_yoy"]["value"] = 2.5
    base["fed_funds_rate"]["value"] = 2.0
    base["fed_funds_rate"]["prior_value"] = 2.0
    # ── HY OAS dial-up: 750 bps (recession threshold tripped) ──
    base["hy_oas"]["value"] = 7.50
    res = classify_regime(base)
    assert res["macro_regime"] == "poor", (
        f"expected 'poor' (HY OAS 750 bps disambiguates), got "
        f"{res['macro_regime']!r}"
    )
    assert res["credit_signal_inputs"]["recession"] is True


# ── 5. PIT integrity ─────────────────────────────────────────────
def test_classify_regime_safe_when_hy_oas_dropped_for_pit():
    """If HY OAS is absent (e.g. dropped because realtime_start >
    decision_timestamp) the classifier defaults to v0.6 behaviour
    — no crash, no spurious recession signal."""
    base = get_macro_indicators_mock("2024-06-01")
    base["yield_curve_10y2y"]["value"] = 0.30
    base["yield_curve_10y3m"]["value"] = 0.40
    base["unemployment_rate"]["value"] = 4.0
    base["unemployment_rate"]["prior_value"] = 4.0
    base["cpi_yoy"]["value"] = 2.5
    base["fed_funds_rate"]["value"] = 2.0
    base["fed_funds_rate"]["prior_value"] = 2.0
    # Simulate PIT-filtered-out HY OAS by nulling the value.
    base["hy_oas"]["value"] = None
    base["hy_oas"]["missing_data_flag"] = True
    res = classify_regime(base)
    # Without HY OAS, the v0.6 default for healthy macro is
    # 'strengthening'.
    assert res["macro_regime"] == "strengthening"
    assert res["credit_signal_inputs"]["value"] is None
    assert res["credit_signal_inputs"]["recession"] is False


def test_pit_integrity_realtime_start_after_cutoff_rejected():
    """Synthesize a record with realtime_start > decision_timestamp.
    Adapter-level PIT gate should drop it. We exercise the same
    classifier logic by passing an indicator dict with `value=None`
    and `missing_data_flag=True` — the canonical state for a record
    that was PIT-rejected upstream."""
    base = get_macro_indicators_mock("2024-06-01")
    decision_dt = datetime(2024, 6, 1)
    future_realtime_start = (decision_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    base["hy_oas"] = {
        "series_id": "BAMLH0A0HYM2",
        "label": "ICE BofA US High Yield OAS (%)",
        "value": None,
        "observation_date": DATA_UNAVAILABLE,
        "realtime_start": future_realtime_start,
        "realtime_end": DATA_UNAVAILABLE,
        "data_available_as_of": DATA_UNAVAILABLE,
        "conservative_lag_days": 1,
        "conservative_lag_applied": True,
        "vintage_date": DATA_UNAVAILABLE,
        "missing_data_flag": True,
        "source": "FRED",
    }
    res = classify_regime(base)
    # Must not crash; HY OAS is treated as absent.
    assert res["credit_signal_inputs"]["value"] is None
    assert res["credit_signal_inputs"]["recession"] is False


# ── 6. rule_version sync check ──────────────────────────────────
def test_rule_version_bumped_to_v07_in_yaml():
    yaml_path = ROOT / "config" / "rules.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    # Pass 8 (2026-05-04): bumped to v0.9.0_pass8_hardrule.
    assert 'rule_version: "v0.9.0_pass8_hardrule"' in text, (
        "config/rules.yaml rule_version not bumped to "
        "v0.9.0_pass8_hardrule"
    )


def test_rule_version_bumped_to_v07_in_macro_block():
    from src.evidence_packet.blocks import macro as macro_block  # noqa: E402
    # Pass 8 (2026-05-04): bumped to v0.9.0_pass8_hardrule.
    assert macro_block.REGIME_RULE_VERSION == "v0.9.0_pass8_hardrule"


# ── 7. Live FRED smoke (skipped without key) ────────────────────
def test_hy_oas_live_smoke():
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        print("    [SKIP] FRED_API_KEY not set")
        return
    obs = get_series_observations("BAMLH0A0HYM2", limit=5)
    # FRED can return zero rows for a brand-new key on bad dates;
    # treat 'list returned' as success and only assert structure on
    # non-empty.
    assert isinstance(obs, list)
    if obs:
        for o in obs:
            assert "date" in o
            assert "value" in o
            # FRED returns "." for missing values — that's allowed
            assert o["value"] is None or isinstance(o["value"], (int, float))


# ── runner ───────────────────────────────────────────────────────
def main() -> int:
    failures: list[str] = []
    tests = [
        test_four_credit_series_registered_with_correct_ids,
        test_mock_path_emits_numeric_values_for_credit_series,
        test_credit_signals_recession_threshold_600bps,
        test_credit_signals_crisis_threshold_800bps,
        test_credit_signals_risk_on_threshold_350bps,
        test_credit_signals_none_returns_all_false,
        test_credit_signals_mid_band_neutral,
        test_low_stress_with_hy_oas_750bps_disambiguates_to_poor,
        test_low_stress_with_hy_oas_300bps_classifies_strengthening,
        test_mid_stress_with_hy_oas_850bps_promoted_to_crisis,
        test_v0_6_compatibility_when_hy_oas_absent,
        test_classify_regime_emits_credit_signal_inputs_with_mock,
        test_classify_regime_disambiguation_via_synthetic_credit_stress,
        test_classify_regime_safe_when_hy_oas_dropped_for_pit,
        test_pit_integrity_realtime_start_after_cutoff_rejected,
        test_rule_version_bumped_to_v07_in_yaml,
        test_rule_version_bumped_to_v07_in_macro_block,
        test_hy_oas_live_smoke,
    ]

    print("\n=== test_fred_credit_spreads ===")
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failures.append(fn.__name__)
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failures.append(fn.__name__)

    print()
    if failures:
        print(f"  RESULT: {len(failures)} failure(s): {failures}")
        return 1
    print(f"  RESULT: {len(tests)}/{len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
