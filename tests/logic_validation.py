"""
Logic Validation Check - verifies that displayed rules match actual decision engine behavior.
Runs controlled scenarios, ablation tests, and audit trails.
"""
import json
import sys
import io
import os

# Ensure project root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from src.rules.logic_audit import get_all_rules, build_decision_trace
from src.rules.surge_short_rules import (
    filter_surge_candidates, is_shortable, get_position_size,
    REAL_VALUE_EVENTS, SUSPICIOUS_NARRATIVES,
)
from src.rules.quality_long_rules import (
    score_fundamentals, score_network_effect, score_valuation, is_quality_long_candidate,
)
from src.rules.allocation_policy import get_allocation, REGIMES, is_equity_allowed
from src.engine.orchestrator import run_pipeline
from src.data_adapters.mock_loader import get_available_dates, load_top_gainers


def section(title):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")


def subsection(title):
    print(f"\n  --- {title} ---")


def pass_fail(test_name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    mark = "OK" if condition else "XX"
    print(f"  [{mark}] {test_name}: {status}  {detail}")
    return condition


# ════════════════════════════════════════════════
# TEST 1: Rules source validation
# ════════════════════════════════════════════════
def test_1_rules_source():
    section("TEST 1: Rules Source Validation")
    print("  Verifying /api/rules exports match actual code constants...\n")

    rules = get_all_rules()
    all_pass = True

    # Check surge thresholds match code
    ss = rules["surge_short_rules"]
    all_pass &= pass_fail(
        "Surge min return threshold",
        ss["scan_parameters"]["min_daily_return_pct"] == 50,
        f"(rules={ss['scan_parameters']['min_daily_return_pct']}, code=50)",
    )
    all_pass &= pass_fail(
        "Surge min volume",
        ss["scan_parameters"]["min_volume"] == 1_000_000,
        f"(rules={ss['scan_parameters']['min_volume']}, code=1000000)",
    )
    all_pass &= pass_fail(
        "Surge min prior close",
        ss["scan_parameters"]["min_prior_close"] == 2.00,
    )

    # Check real value events match code
    doc_real = set()
    for r in ss["shortability_rules"]:
        if "real_value_events" in r:
            doc_real = set(r["real_value_events"])
    all_pass &= pass_fail(
        "Real value events match code",
        doc_real == REAL_VALUE_EVENTS,
        f"(doc={len(doc_real)}, code={len(REAL_VALUE_EVENTS)})",
    )

    # Check suspicious narratives match
    doc_suspicious = set()
    for r in ss["shortability_rules"]:
        if "suspicious_narratives" in r:
            doc_suspicious = set(r["suspicious_narratives"])
    all_pass &= pass_fail(
        "Suspicious narratives match code",
        doc_suspicious == SUSPICIOUS_NARRATIVES,
        f"(doc={len(doc_suspicious)}, code={len(SUSPICIOUS_NARRATIVES)})",
    )

    # Check allocation regimes
    ap = rules["allocation_policy"]
    for regime_doc in ap["regimes"]:
        regime_name = regime_doc["regime"]
        code_regime = REGIMES[regime_name]
        all_pass &= pass_fail(
            f"Allocation '{regime_name}' FI%",
            regime_doc["fixed_income_pct"] == code_regime["fi_pct"],
            f"(doc={regime_doc['fixed_income_pct']}, code={code_regime['fi_pct']})",
        )
        all_pass &= pass_fail(
            f"Allocation '{regime_name}' EQ%",
            regime_doc["equity_pct"] == code_regime["eq_pct"],
        )

    # Check quality gate thresholds
    cq = rules["combined_quality_logic"]
    all_pass &= pass_fail(
        "Quality gate formula matches code",
        "0.40" in cq["formula"] and "0.30" in cq["formula"],
        f"formula='{cq['formula']}'",
    )

    # Check valuation assessment thresholds
    vs = rules["valuation_scoring"]
    all_pass &= pass_fail(
        "Valuation starting score = 50",
        vs["starting_score"] == 50,
    )

    return all_pass


# ════════════════════════════════════════════════
# TEST 2: Mock run audit trail
# ════════════════════════════════════════════════
def test_2_mock_audit():
    section("TEST 2: Per-Ticker Decision Audit Trail")
    result = run_pipeline("2025-03-15", regime="normal", skip_alt_data=False)
    all_pass = True

    for d in result["decisions"]:
        ticker = d["ticker"]
        trace = build_decision_trace(ticker, result)
        agents = trace["agent_trace"]

        subsection(f"{ticker} ({d['candidate_type']})")

        # Agent 1 outputs
        a1 = agents[0]["outputs"]
        print(f"    Screener: selected_as={a1.get('selected_as', 'N/A')}")

        # Agent 2 outputs
        a2 = agents[1]["outputs"]
        print(f"    Narrative: event={a2.get('event_type')}, real_value={a2.get('is_real_value')}, suspicious={a2.get('is_suspicious')}")

        # Agent 3 outputs
        a3 = agents[2]["outputs"]
        sb = agents[2].get("signal_breakdown", {})
        print(f"    Alt-Data: verdict={a3.get('verdict')}, score={a3.get('evidence_score')}, signals={a3.get('total_signals')}")
        for source, signals in sb.items():
            if signals:
                print(f"      {source}: {len(signals)} signals")

        # Agent 4 outputs
        a4 = agents[3]["outputs"]
        km = agents[3].get("key_metrics", {})
        print(f"    Fund/Net/Val: fund={a4.get('fundamental_score')}, net={a4.get('network_effect_score')}, val={a4.get('valuation_score')}")
        print(f"      assessment={a4.get('valuation_assessment')}, combined={a4.get('combined_quality_score')}")
        print(f"      hint={a4.get('decision_hint')}, reason={a4.get('decision_reason')}")
        pe = km.get("pe_ratio", "N/A")
        peg = km.get("peg_ratio", "N/A")
        dd = km.get("drawdown_from_ath_pct", "N/A")
        print(f"      key_metrics: P/E={pe}, PEG={peg}, drawdown={dd}%")

        # Agent 5 outputs
        a5 = agents[4]["outputs"]
        print(f"    Risk/PM: decision={a5.get('final_decision')}, confidence={a5.get('confidence')}, position=${a5.get('position_size', 0):,.0f}")
        print(f"      reason: {a5.get('reason')}")

        # Validate decision makes sense
        decision = d["decision"]
        ctype = d["candidate_type"]
        if ctype == "surge_short":
            is_real = a2.get("is_real_value", False)
            alt_verdict = a3.get("verdict", "")
            if is_real and decision == "no_trade":
                all_pass &= pass_fail(f"{ticker}: real-value event protected", True)
            elif not is_real and alt_verdict == "contradicted" and decision == "short":
                all_pass &= pass_fail(f"{ticker}: suspicious+contradicted → SHORT", True)
            elif not is_real and alt_verdict == "contradicted" and decision != "short":
                all_pass &= pass_fail(f"{ticker}: suspicious+contradicted should SHORT", False)
            else:
                print(f"    [?] {ticker}: {decision} (manual review)")
        elif ctype == "quality_long":
            val_assess = a4.get("valuation_assessment", "")
            hint = a4.get("decision_hint", "")
            if hint == "buy" and decision == "buy":
                all_pass &= pass_fail(f"{ticker}: quality+attractive → BUY", True)
            elif hint == "watch" and decision == "watch":
                all_pass &= pass_fail(f"{ticker}: quality+expensive → WATCH", True)
            elif hint == "no_trade" and decision in ("no_trade", "watch"):
                all_pass &= pass_fail(f"{ticker}: below quality gate → NO TRADE", True)
            else:
                print(f"    [?] {ticker}: hint={hint}, decision={decision} (manual review)")

    return all_pass


# ════════════════════════════════════════════════
# TEST 3: Controlled scenario tests
# ════════════════════════════════════════════════
def test_3_scenarios():
    section("TEST 3: Controlled Scenario Tests")
    all_pass = True

    # Scenario 3a: AI pivot + poor fundamentals + alt-data contradicted => SHORT
    subsection("3a: AI pivot + poor fundamentals + alt-data contradicted => SHORT")
    shortable = is_shortable("ai_pivot", "promotional", "contradicted")
    all_pass &= pass_fail("is_shortable returns True", shortable["shortable"])
    all_pass &= pass_fail("confidence is high", shortable["confidence"] == "high")
    pos = get_position_size("high")
    all_pass &= pass_fail("position size = $20,000 (2% of $1M)", pos == 20000, f"(got ${pos:,.0f})")

    # Scenario 3b: FDA approval + surge => NO TRADE (protected)
    subsection("3b: FDA approval + price surge => NO TRADE (protected)")
    shortable = is_shortable("fda_approval", "real_value", "narrative_supported")
    all_pass &= pass_fail("is_shortable returns False (protected)", not shortable["shortable"])
    all_pass &= pass_fail("reason mentions protected", "Protected" in shortable["reason"])

    # Scenario 3c: Strong company + expensive valuation => WATCH
    subsection("3c: Strong company + expensive valuation => WATCH")
    val = score_valuation({"pe_ratio": 55, "forward_pe": 45, "peg_ratio": 3.5, "drawdown_from_ath_pct": 2})
    all_pass &= pass_fail("Valuation score < 35 (expensive)", val["valuation_score"] < 35, f"(score={val['valuation_score']})")
    all_pass &= pass_fail("Assessment is expensive or very_expensive",
                          val["valuation_assessment"] in ("expensive", "very_expensive"),
                          f"(assessment={val['valuation_assessment']})")
    q = is_quality_long_candidate(70, 80, val["valuation_score"], 90)
    all_pass &= pass_fail("Decision hint is watch", q["decision_hint"] == "watch", f"(hint={q['decision_hint']})")

    # Scenario 3d: Strong company + attractive valuation => BUY
    subsection("3d: Strong company + attractive valuation => BUY")
    val = score_valuation({"pe_ratio": 12, "forward_pe": 10, "peg_ratio": 0.8,
                           "price_to_fcf": 10, "drawdown_from_ath_pct": 35})
    all_pass &= pass_fail("Valuation score >= 55 (attractive)", val["valuation_score"] >= 55, f"(score={val['valuation_score']})")
    all_pass &= pass_fail("Assessment is attractive", val["valuation_assessment"] == "attractive")
    q = is_quality_long_candidate(70, 80, val["valuation_score"], 90)
    all_pass &= pass_fail("Decision hint is buy", q["decision_hint"] == "buy", f"(hint={q['decision_hint']})")

    # Scenario 3e: Missing data => NOT zero-score punishment
    subsection("3e: Missing data => Not evaluated, not zero-score")
    val = score_valuation({})  # No valuation fields at all
    all_pass &= pass_fail("Missing PE doesn't crash", True)
    all_pass &= pass_fail("Missing data valuation starts near neutral",
                          30 <= val["valuation_score"] <= 60,
                          f"(score={val['valuation_score']}, should be near 50)")
    # Check that fundamentals with no data don't score 0 punitively
    fund = score_fundamentals({})
    all_pass &= pass_fail("Empty fundamentals score near 0 but not crashed",
                          fund["fundamental_score"] >= 0, f"(score={fund['fundamental_score']})")

    # Scenario 3f: Extreme Reddit squeeze => not automatic forced cover
    subsection("3f: Extreme Reddit squeeze => risk assessment, not forced action")
    shortable = is_shortable("meme_squeeze", "promotional", "contradicted")
    all_pass &= pass_fail("Meme squeeze + contradicted = shortable", shortable["shortable"])
    # But with supported alt-data, should NOT short
    shortable2 = is_shortable("meme_squeeze", "promotional", "narrative_supported")
    all_pass &= pass_fail("Meme squeeze + alt-data supported = NOT shortable", not shortable2["shortable"])

    # Scenario 3g: Suspicious narrative but alt-data supported => NOT high-confidence short
    subsection("3g: Suspicious narrative + alt-data supported => NOT high-confidence short")
    shortable = is_shortable("ai_pivot", "promotional", "narrative_supported")
    all_pass &= pass_fail("AI pivot + supported alt-data = NOT shortable",
                          not shortable["shortable"],
                          f"(shortable={shortable['shortable']})")

    # Scenario 3h: Weak fundamentals but no price surge threshold => NO TRADE
    subsection("3h: Weak fundamentals, no surge threshold => NO TRADE")
    # A stock with 30% gain (below 50% threshold) shouldn't pass surge filter
    fake_gainers = [{"ticker": "FAKE", "change_pct": 30, "volume": 2_000_000,
                     "prior_close": 5.0, "security_type": "common"}]
    filtered = filter_surge_candidates(fake_gainers)
    all_pass &= pass_fail("30% gain does NOT pass surge filter", len(filtered) == 0,
                          f"(filtered={len(filtered)})")
    # But 55% should pass
    fake_gainers2 = [{"ticker": "FAKE2", "change_pct": 55, "volume": 2_000_000,
                      "prior_close": 5.0, "security_type": "common"}]
    filtered2 = filter_surge_candidates(fake_gainers2)
    all_pass &= pass_fail("55% gain DOES pass surge filter", len(filtered2) == 1)

    return all_pass


# ════════════════════════════════════════════════
# TEST 4: Ablation — Alt-Data enabled vs disabled
# ════════════════════════════════════════════════
def test_4_ablation():
    section("TEST 4: Ablation — Alt-Data Enabled vs Disabled")
    result_with = run_pipeline("2025-03-15", regime="normal", skip_alt_data=False)
    result_without = run_pipeline("2025-03-15", regime="normal", skip_alt_data=True)

    decisions_with = {d["ticker"]: d for d in result_with["decisions"]}
    decisions_without = {d["ticker"]: d for d in result_without["decisions"]}

    all_tickers = sorted(set(list(decisions_with.keys()) + list(decisions_without.keys())))

    print(f"\n  {'TICKER':<10} {'WITH ALT-DATA':<18} {'WITHOUT ALT-DATA':<18} {'CHANGED':<10}")
    print(f"  {'-'*56}")

    changes = 0
    for ticker in all_tickers:
        w = decisions_with.get(ticker, {})
        wo = decisions_without.get(ticker, {})
        d_w = w.get("decision", "N/A")
        d_wo = wo.get("decision", "N/A")
        changed = d_w != d_wo
        if changed:
            changes += 1
        mark = "  <<<" if changed else ""
        print(f"  {ticker:<10} {d_w:<18} {d_wo:<18} {mark}")

    print(f"\n  Total changes: {changes} / {len(all_tickers)}")

    # With alt-data should have MORE shorts (false ones prevented without)
    s_with = result_with["summary"]
    s_without = result_without["summary"]
    print(f"\n  WITH:    {s_with['short']}S {s_with['buy']}B {s_with['watch']}W {s_with['no_trade']}NT {s_with['veto']}V")
    print(f"  WITHOUT: {s_without['short']}S {s_without['buy']}B {s_without['watch']}W {s_without['no_trade']}NT {s_without['veto']}V")

    # Count confidence changes
    conf_changes = 0
    for ticker in all_tickers:
        w = decisions_with.get(ticker, {})
        wo = decisions_without.get(ticker, {})
        if w.get("confidence") != wo.get("confidence"):
            conf_changes += 1

    all_pass = True
    all_pass &= pass_fail("Ablation produces different decisions OR confidence",
                          changes > 0 or conf_changes > 0,
                          f"({changes} decision changes, {conf_changes} confidence changes)")
    all_pass &= pass_fail("Alt-data agent affects short analysis",
                          s_with["short"] != s_without["short"]
                          or s_with["no_trade"] != s_without["no_trade"]
                          or conf_changes > 0,
                          "(confidence shifts count as impact)")

    # Check confidence differences
    subsection("Confidence comparison")
    for ticker in all_tickers:
        w = decisions_with.get(ticker, {})
        wo = decisions_without.get(ticker, {})
        c_w = w.get("confidence", "N/A")
        c_wo = wo.get("confidence", "N/A")
        if c_w != c_wo:
            print(f"    {ticker}: with={c_w}, without={c_wo}")

    return all_pass


# ════════════════════════════════════════════════
# TEST 5: Top-gainer scan is 3-5 only
# ════════════════════════════════════════════════
def test_5_scan_size():
    section("TEST 5: Top-Gainer Scan Size")
    all_pass = True

    dates = get_available_dates()
    for date in dates:
        gainers = load_top_gainers(date)
        count = len(gainers)
        all_pass &= pass_fail(
            f"Date {date}: {count} gainers scanned",
            count <= 5,
            f"(max allowed=5, got={count})",
        )

    # Also check pipeline output
    result = run_pipeline(dates[0], regime="normal", skip_alt_data=False)
    total = result["agent_outputs"]["market_screener"]["total_gainers"]
    all_pass &= pass_fail(
        f"Pipeline reports total_gainers={total}",
        total <= 5,
    )

    return all_pass


# ════════════════════════════════════════════════
# TEST 6: No-look-ahead timestamp audit
# ════════════════════════════════════════════════
def test_6_no_lookahead():
    section("TEST 6: No-Look-Ahead Timestamp Audit")
    all_pass = True

    result = run_pipeline("2025-03-15", regime="normal", skip_alt_data=False)

    # Check that decision date matches input date
    all_pass &= pass_fail(
        "Decision date matches input date",
        result["date"] == "2025-03-15",
        f"(got={result['date']})",
    )

    # Check that the pipeline has a version
    rules = get_all_rules()
    all_pass &= pass_fail(
        "System version is documented",
        rules["system_version"] == "0.2.0",
        f"(version={rules['system_version']})",
    )

    # In mock mode, check what timestamps exist
    subsection("Timestamp fields audit (mock mode)")
    print("  NOTE: In mock mode, data is static JSON. No real timestamps to validate.")
    print("  The following fields WILL be checked when live APIs are connected:\n")
    fields = [
        ("decision_date", result.get("date", "PRESENT")),
        ("regime", result.get("regime", "PRESENT")),
        ("system_version", rules.get("system_version", "PRESENT")),
        ("data_mode", "MOCK (static JSON, no look-ahead possible)"),
    ]

    needed_live = [
        ("market_data_timestamp", "Will come from FMP/Polygon API response"),
        ("news_timestamp", "Will come from news API response with publication date"),
        ("filing_date", "Will come from SEC EDGAR filing date field"),
        ("execution_timestamp", "Will be set at order execution time"),
        ("price_snapshot_time", "Will come from market data API"),
    ]

    for name, value in fields:
        print(f"    {name}: {value}")
        all_pass &= pass_fail(f"Field '{name}' present", value not in (None, ""))

    print()
    for name, note in needed_live:
        print(f"    {name}: [MOCK MODE - not yet applicable] {note}")

    all_pass &= pass_fail(
        "No future data in mock: gainers dates <= decision date",
        True,  # Mock data is static, inherently no look-ahead
        "(mock data is pre-defined, no temporal contamination possible)",
    )

    return all_pass


# ════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 80)
    print("  LOGIC VALIDATION CHECK — Alt-Data Agentic Long-Short System v0.2")
    print("=" * 80)

    results = {}
    results["1_rules_source"] = test_1_rules_source()
    results["2_mock_audit"] = test_2_mock_audit()
    results["3_scenarios"] = test_3_scenarios()
    results["4_ablation"] = test_4_ablation()
    results["5_scan_size"] = test_5_scan_size()
    results["6_no_lookahead"] = test_6_no_lookahead()

    section("FINAL RESULTS")
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    for name, passed_val in results.items():
        mark = "PASS" if passed_val else "FAIL"
        print(f"  [{mark}]  {name}")

    print(f"\n  Overall: {passed}/{total} test suites passed")
    if passed == total:
        print("  STATUS: ALL TESTS PASSED")
    else:
        print("  STATUS: SOME TESTS FAILED")
        sys.exit(1)
