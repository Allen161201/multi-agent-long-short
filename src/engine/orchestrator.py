"""
Orchestrator — runs all 5 agents in sequence for a given date.
Now builds a point-in-time evidence packet BEFORE agents reason.
Agents receive ONLY the evidence packet — no free browsing.
"""
import sys
import json
from datetime import datetime

from src.agents import market_screener, narrative_event, alt_data_verify, fundamental_network, risk_pm
from src.agents.agent_decision_schema import mock_surge_short_agent_decision
from src.rules.allocation_policy import determine_regime
from src.engine.audit_log import write_audit_log, format_decisions_table
from src.engine.evidence_packet import (
    build_evidence_packet, validate_no_lookahead,
    RULE_VERSION, AGENT_PROMPT_VERSION,
)
from src.engine.backtest_integrity import compute_execution_timestamp
from src.data_adapters.mock_loader import (
    get_available_dates, load_news_events, load_reddit_data,
    load_github_data, load_h1b_data, load_sec_data,
)
from src.data_adapters.market_data import get_fundamentals
from src.data_adapters.fred_adapter import get_macro_indicators
from src.agents.macro_regime import classify_regime as classify_macro_regime

import os

DATA_UNAVAILABLE = "Data unavailable"


def _get_data_mode() -> str:
    """Get current data mode from environment."""
    return os.environ.get("DATA_MODE", "mock").strip().lower()


def run_pipeline(date: str, regime: str | None = None, skip_alt_data: bool = False) -> dict:
    """
    Run the full 5-agent pipeline for a given date.

    NOW with evidence packet:
    1. Build evidence packets for each ticker
    2. Validate no-lookahead
    3. Pass evidence packets (not raw data) to agents

    Args:
        date: The analysis date (e.g., "2025-03-15")
        regime: Override macro regime. If None, auto-determine.
        skip_alt_data: If True, skip the Alt Data Verification Agent (for ablation).

    Returns:
        dict with all agent outputs, evidence packets, and final decisions.
    """
    data_mode = _get_data_mode()

    if regime is None:
        regime = determine_regime()

    # ──── Macro Regime Module ────
    macro_indicators = get_macro_indicators(date)
    macro_regime_output = classify_macro_regime(macro_indicators)

    # Use macro-classified regime if no override was provided
    if regime == determine_regime():
        regime = macro_regime_output["macro_regime"]

    # ──── Agent 1: Market Screener ────
    screener_output = market_screener.run(date)
    surge_candidates = screener_output["surge_short_candidates"]
    quality_tickers = screener_output["quality_long_tickers"]

    # Collect all tickers that need analysis
    surge_tickers = [c["ticker"] for c in surge_candidates]
    all_tickers = list(set(surge_tickers + quality_tickers))

    # ──── Build Evidence Packets ────
    evidence_packets = {}
    integrity_results = {}
    for ticker in all_tickers:
        # Gather raw data for evidence packet
        news = load_news_events(ticker)
        fundamentals = get_fundamentals(ticker)
        reddit = load_reddit_data(ticker)
        github = load_github_data(ticker)
        h1b = load_h1b_data(ticker)
        sec = load_sec_data(ticker)

        # Build standardized evidence packet
        packet = build_evidence_packet(
            ticker=ticker,
            decision_date=date,
            data_mode=data_mode,
            market_data=_extract_market_data(ticker, screener_output),
            news_data=news if isinstance(news, list) else [],
            fundamentals=fundamentals if fundamentals else None,
            reddit_data=reddit if reddit else None,
            github_data=github if github else None,
            h1b_data=h1b if h1b else None,
            sec_data=sec if sec else None,
            macro_data={
                "status": "available",
                "macro_regime": macro_regime_output["macro_regime"],
                "macro_confidence": macro_regime_output["macro_confidence"],
                "data_available_as_of": macro_regime_output["data_available_as_of"],
                "lookahead_safe": macro_regime_output["lookahead_safe"],
                "indicators_available": macro_regime_output["indicators_available"],
                "source": next(iter(macro_indicators.values()), {}).get("source", "mock"),
            },
        )

        # Validate no-lookahead
        validation = validate_no_lookahead(packet)
        evidence_packets[ticker] = packet
        integrity_results[ticker] = validation

    # ──── Agent 2: Narrative / Event ────
    narrative_output = narrative_event.run(all_tickers)

    # ──── Agent 3: Alternative Data Verification (CORE) ────
    if skip_alt_data:
        # Ablation mode: produce neutral/empty alt-data results
        alt_data_output = {
            "agent": "alt_data_verification",
            "verifications": {
                ticker: {
                    "ticker": ticker,
                    "verdict": "weakly_supported",
                    "evidence_score": 50,
                    "evidence_for": [],
                    "evidence_against": [],
                    "data_sources_used": [],
                    "total_signals": 0,
                }
                for ticker in all_tickers
            },
        }
    else:
        alt_data_output = alt_data_verify.run(all_tickers, narrative_output)

    # ──── Agent 4: Fundamental / Network / Valuation ────
    fundamental_output = fundamental_network.run(
        surge_tickers, quality_tickers,
        regime=regime,
        alt_data_results=alt_data_output,
    )

    # ──── Agent 4.5: Surge-Short Agent Decision (LLM placeholder) ────
    # Produces a structured decision per surge candidate. Until the LLM API
    # is wired in, the placeholder always returns recommended_action =
    # "needs_more_evidence" so no spurious trades are generated. The Risk/PM
    # agent reads `_agent_decision` off the surge evaluation in the next step.
    surge_evals = fundamental_output.setdefault("surge_evaluations", {})
    for c in surge_candidates:
        ticker = c["ticker"]
        evaluation = surge_evals.setdefault(ticker, {})
        narr_hint = (narrative_output.get("classifications") or {}).get(ticker)
        alt_signals = (alt_data_output.get("verifications") or {}).get(ticker)
        agent_decision = mock_surge_short_agent_decision(
            ticker=ticker,
            decision_date=date,
            rule_version=RULE_VERSION,
            agent_prompt_version=AGENT_PROMPT_VERSION,
            evidence_packet=evidence_packets.get(ticker),
            narrative_hint=narr_hint,
            alt_data_signals=alt_signals,
            fundamentals=evaluation,
        )
        evaluation["_agent_decision"] = agent_decision
        evaluation["_candidate_meta"] = {
            "entry_price": c.get("close"),
            "prior_close": c.get("prior_close"),
            "change_pct": c.get("change_pct"),
            "sector": evaluation.get("sector"),
            # Placeholders for fields a future enrichment step would set.
            "is_confirmed_acquisition": False,
            "acquisition_arbitrage_realistic": False,
            "days_since_ipo": None,
            "is_fully_fda_approved_marketed_drug_event": False,
        }

    # ──── Agent 5: Risk / PM ────
    risk_output = risk_pm.run(
        surge_candidates=surge_candidates,
        quality_tickers=fundamental_output.get("quality_long_eligible", []),
        narrative_results=narrative_output,
        alt_data_results=alt_data_output,
        fundamental_results=fundamental_output,
        regime=regime,
    )

    # Compute execution timestamp
    execution_ts = compute_execution_timestamp(date)

    # Compile full result
    pipeline_result = {
        "date": date,
        "regime": regime,
        "skip_alt_data": skip_alt_data,
        "rule_version": RULE_VERSION,
        "agent_prompt_version": AGENT_PROMPT_VERSION,
        "data_mode": data_mode,
        "decision_timestamp": f"{date}T16:15:00-05:00",
        "execution_timestamp": execution_ts,
        "agent_outputs": {
            "market_screener": screener_output,
            "narrative_event": narrative_output,
            "alt_data_verification": alt_data_output,
            "fundamental_network": fundamental_output,
            "risk_pm": risk_output,
        },
        "evidence_packets": evidence_packets,
        "integrity_results": integrity_results,
        "macro_regime_output": macro_regime_output,
        "decisions": risk_output["decisions"],
        "summary": risk_output["summary"],
        "allocation": risk_output["allocation"],
    }

    # Write audit log
    log_path = write_audit_log(date, pipeline_result)
    pipeline_result["audit_log_path"] = log_path

    return pipeline_result


def _extract_market_data(ticker: str, screener_output: dict) -> dict | None:
    """Extract market data for a ticker from screener output."""
    # Check surge candidates
    for c in screener_output.get("surge_short_candidates", []):
        if c["ticker"] == ticker:
            return {
                "ticker": ticker,
                "close": c.get("close", 0),
                "prior_close": c.get("prior_close", 0),
                "change_pct": c.get("change_pct", 0),
                "volume": c.get("volume", 0),
                "source": c.get("source", "mock"),
                "timestamp": c.get("timestamp", ""),
                "available_as_of": c.get("available_as_of", ""),
            }
    # Check all gainers
    for g in screener_output.get("all_gainers", []):
        if g["ticker"] == ticker:
            return {
                "ticker": ticker,
                "close": g.get("close", g.get("change_pct", 0)),
                "change_pct": g.get("change_pct", 0),
                "volume": g.get("volume", 0),
                "source": g.get("source", "mock"),
            }
    # Quality-long tickers may not be in gainers
    return None


def print_summary(result: dict):
    """Print a clean terminal summary of the pipeline run."""
    print("\n" + "=" * 80)
    print(f"  ALT-DATA AGENTIC LONG-SHORT SYSTEM — Pipeline Run")
    print(f"  Date: {result['date']}  |  Regime: {result['regime']}  |  "
          f"Alt-Data: {'DISABLED' if result.get('skip_alt_data') else 'ENABLED'}")
    print(f"  Rule Version: {result.get('rule_version', 'unknown')}  |  "
          f"Data Mode: {result.get('data_mode', 'mock')}  |  "
          f"Execution: {result.get('execution_timestamp', 'N/A')}")
    print("=" * 80)

    # Integrity check summary
    integrity = result.get("integrity_results", {})
    if integrity:
        violations = sum(
            1 for v in integrity.values() if not v.get("valid", True)
        )
        total = len(integrity)
        icon = "OK" if violations == 0 else "FAIL"
        print(f"\n  [{icon}] INTEGRITY: {total - violations}/{total} evidence packets valid"
              f"  (no-lookahead check)")
        if violations > 0:
            for ticker, v in integrity.items():
                if not v.get("valid", True):
                    print(f"     [!] {ticker}: {v.get('violations', [])}")

    # Allocation
    alloc = result["allocation"]
    print(f"\n  [ALLOC] {alloc['label']}")
    print(f"     Fixed Income: {alloc['fixed_income_pct']}%  |  Equity: {alloc['equity_pct']}%")
    discipline = alloc.get("equity_discipline") or alloc.get("equity_restriction")
    if discipline:
        print(f"     [i] Equity discipline: {discipline}")

    # Screener summary
    screener = result["agent_outputs"]["market_screener"]
    print(f"\n  [SCREEN] {screener['total_gainers']} gainers -> "
          f"{len(screener['surge_short_candidates'])} surge candidates")

    # Surge candidates
    if screener["surge_short_candidates"]:
        print(f"\n  -- Surge-Short Candidates --")
        for c in screener["surge_short_candidates"][:10]:
            print(f"     {c['ticker']:<8} +{c['change_pct']:>6.1f}%  "
                  f"Vol: {c['volume']/1e6:>5.1f}M  ${c['close']:>8.2f}")

    # Alt-data summary
    if not result.get("skip_alt_data"):
        alt = result["agent_outputs"]["alt_data_verification"]["verifications"]
        print(f"\n  [ALT-DATA] Alternative Data Verification")
        for ticker, v in alt.items():
            icon = "[OK]" if v["verdict"] == "narrative_supported" else \
                   "[??]" if v["verdict"] == "weakly_supported" else "[XX]"
            print(f"     {icon} {ticker:<8} {v['verdict']:<20} "
                  f"(score={v['evidence_score']}, signals={v['total_signals']})")

    # Decisions
    print(f"\n  [DECISIONS] Final Decisions")
    print(format_decisions_table(result["decisions"]))

    # Summary
    s = result["summary"]
    print(f"\n  Summary: {s['buy']} BUY | {s['short']} SHORT | "
          f"{s['watch']} WATCH | {s['no_trade']} NO-TRADE | {s['veto']} VETO")

    print(f"\n  [LOG] Audit log: {result.get('audit_log_path', 'N/A')}")
    print("=" * 80 + "\n")


def main():
    """Main entry point — run pipeline for all available sample dates."""
    dates = get_available_dates()
    if not dates:
        print("No sample dates available.")
        return

    # Run for first date by default, or all if --all flag
    run_all = "--all" in sys.argv
    target_dates = dates if run_all else [dates[0]]

    for date in target_dates:
        result = run_pipeline(date)
        print_summary(result)


if __name__ == "__main__":
    main()
