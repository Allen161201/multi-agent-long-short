"""
Agent 4: Fundamental / Network Effect / Valuation Agent
Evaluates fundamentals, network-effect quality, AND valuation attractiveness.

Core principle: Buy good companies at reasonable prices.
"""
from src.data_adapters.market_data import get_fundamentals
from src.data_adapters.github_adapter import get_github_data
from src.data_adapters.h1b_adapter import get_h1b_data
from src.data_adapters.reddit_adapter import get_reddit_data
from src.rules.quality_long_rules import (
    score_fundamentals, score_network_effect, score_valuation, is_quality_long_candidate,
)


def evaluate(ticker: str, regime: str = "normal", alt_data_score: float = 50) -> dict:
    """
    Full fundamental + network-effect + valuation evaluation for one ticker.

    For long candidates: quality + valuation = BUY or WATCH.
    For short candidates: poor fundamentals identification.
    """
    fundamentals = get_fundamentals(ticker)
    github = get_github_data(ticker)
    h1b = get_h1b_data(ticker)
    reddit = get_reddit_data(ticker)

    if not fundamentals:
        return {
            "ticker": ticker,
            "fundamental_score": 0,
            "network_effect_score": 0,
            "valuation_score": 0,
            "valuation_assessment": "not_evaluated",
            "alt_data_score": alt_data_score,
            "combined_quality_score": 0,
            "quality_long_eligible": False,
            "decision_hint": "no_trade",
            "flags": ["No fundamental data available"],
            "network_evidence": [],
            "valuation_notes": [],
        }

    fund_result = score_fundamentals(fundamentals)
    network_result = score_network_effect(fundamentals, github, h1b, reddit)
    val_result = score_valuation(fundamentals, regime)

    quality = is_quality_long_candidate(
        fundamentals,
        fund_result["fundamental_score"],
        network_result["network_effect_score"],
        val_result["valuation_score"],
        alt_data_score,
    )

    return {
        "ticker": ticker,
        "name": fundamentals.get("name", ticker),
        "sector": fundamentals.get("sector", "Unknown"),
        "fundamental_score": fund_result["fundamental_score"],
        "fundamental_flags": fund_result["flags"],
        "network_effect_score": network_result["network_effect_score"],
        "network_evidence": network_result["evidence"],
        "valuation_score": val_result["valuation_score"],
        "valuation_assessment": val_result["valuation_assessment"],
        "valuation_notes": val_result["valuation_notes"],
        "alt_data_score": alt_data_score,
        "combined_quality_score": quality["combined_quality_score"],
        "quality_long_eligible": quality["qualifies"],
        "decision_hint": quality["decision_hint"],
        "decision_reason": quality["reason"],
        "key_metrics": {
            "revenue_ttm": fundamentals.get("revenue_ttm", 0),
            "revenue_growth_pct": fundamentals.get("revenue_growth_pct", 0),
            "gross_margin_pct": fundamentals.get("gross_margin_pct", 0),
            "operating_margin_pct": fundamentals.get("operating_margin_pct", 0),
            "free_cash_flow_ttm": fundamentals.get("free_cash_flow_ttm", 0),
            "debt_to_equity": fundamentals.get("debt_to_equity", 0),
            "dilution_risk": fundamentals.get("dilution_risk", "unknown"),
            "going_concern": fundamentals.get("going_concern", False),
            "pe_ratio": fundamentals.get("pe_ratio"),
            "forward_pe": fundamentals.get("forward_pe"),
            "peg_ratio": fundamentals.get("peg_ratio"),
            "price_to_fcf": fundamentals.get("price_to_fcf"),
            "price_to_sales": fundamentals.get("price_to_sales"),
            "drawdown_from_ath_pct": fundamentals.get("drawdown_from_ath_pct", 0),
            "valuation_note": fundamentals.get("valuation_note", ""),
        },
    }


def run(
    surge_tickers: list[str],
    quality_tickers: list[str],
    regime: str = "normal",
    alt_data_results: dict | None = None,
) -> dict:
    """Run fundamental/network/valuation evaluation for surge-short and quality-long tickers."""
    surge_evals = {}
    quality_evals = {}

    for ticker in surge_tickers:
        alt_score = 50
        if alt_data_results:
            v = alt_data_results.get("verifications", {}).get(ticker, {})
            alt_score = v.get("evidence_score", 50)
        surge_evals[ticker] = evaluate(ticker, regime, alt_score)

    for ticker in quality_tickers:
        alt_score = 50
        if alt_data_results:
            v = alt_data_results.get("verifications", {}).get(ticker, {})
            alt_score = v.get("evidence_score", 50)
        quality_evals[ticker] = evaluate(ticker, regime, alt_score)

    return {
        "agent": "fundamental_network",
        "surge_evaluations": surge_evals,
        "quality_evaluations": quality_evals,
        "quality_long_eligible": [
            t for t, e in quality_evals.items() if e.get("quality_long_eligible")
        ],
    }
