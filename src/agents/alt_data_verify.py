"""
Agent 3: Alternative Data Verification Agent  *** CORE AGENT ***

TWO ROLES:

ROLE 1: Narrative / Fundamental Verification
Verifies whether company narrative is supported by external observable evidence.
This is NOT sentiment analysis — it checks real capability vs. claims.

ROLE 2: Market Structure / Thematic Research
Helps understand what the market actually needs (e.g., AI infrastructure beyond chips),
identifies bottlenecks, second-order beneficiaries, and valuation discipline.

Alt-data sources:
- Reddit discussion / squeeze attention
- SEC filing text / business description changes
- GitHub developer ecosystem proxy
- H-1B / LCA technical hiring intensity proxy
"""
from src.data_adapters.reddit_adapter import get_reddit_data
from src.data_adapters.github_adapter import get_github_data
from src.data_adapters.h1b_adapter import get_h1b_data
from src.data_adapters.sec_adapter import get_sec_filings
from src.data_adapters.mock_loader import load_thematic_research


def verify_narrative(ticker: str, event_type: str, is_real_value: bool) -> dict:
    """
    ROLE 1: Verify whether the company's narrative is supported by alternative data.

    This is NOT sentiment analysis. We check:
    - Does the company actually have technical capability for claimed pivot?
    - Is there developer ecosystem evidence for platform claims?
    - Is hiring consistent with the narrative?
    - Are SEC filings showing suspicious pivots?
    - Is social media showing squeeze/promotional patterns?

    Returns:
        dict with verdict, evidence_score, evidence_notes, data_sources_used
    """
    reddit = get_reddit_data(ticker)
    github = get_github_data(ticker)
    h1b = get_h1b_data(ticker)
    sec = get_sec_filings(ticker)

    evidence_points = []
    contradiction_points = []
    data_sources = []

    # ──── SEC Filing Analysis ────
    if sec:
        data_sources.append("sec_filings")
        if sec.get("business_description_changed"):
            change_type = sec.get("description_change_type", "unknown")
            if change_type in ("complete_rebrand", "serial_pivot"):
                contradiction_points.append(
                    f"SEC: Business description changed ({change_type}): "
                    f"'{sec.get('prior_description')}' -> '{sec.get('current_description')}'"
                )
            elif change_type == "industry_pivot":
                contradiction_points.append(
                    f"SEC: Industry pivot detected: '{sec.get('prior_description')}' -> '{sec.get('current_description')}'"
                )
        if sec.get("going_concern_language"):
            contradiction_points.append("SEC: Going concern language in filings")
        if sec.get("auditor_change_recent"):
            contradiction_points.append("SEC: Recent auditor change (red flag)")
        if sec.get("related_party_transactions", 0) >= 3:
            contradiction_points.append(
                f"SEC: High related-party transactions ({sec['related_party_transactions']})"
            )
        if not sec.get("business_description_changed") and not sec.get("going_concern_language"):
            evidence_points.append("SEC: Stable business description, no going concern")

    # ──── GitHub Developer Ecosystem ────
    if github:
        data_sources.append("github")
        dev_score = github.get("developer_ecosystem_score", 0)
        is_tech_claim = event_type in ("ai_pivot", "platform_story_no_evidence")
        if is_tech_claim and dev_score < 10:
            contradiction_points.append(
                f"GitHub: Claims tech/AI pivot but developer ecosystem score = {dev_score} "
                f"(repos={github.get('org_repos', 0)}, commits_90d={github.get('commits_90d', 0)})"
            )
        elif dev_score >= 50:
            evidence_points.append(
                f"GitHub: Strong developer ecosystem (score={dev_score}, "
                f"repos={github.get('org_repos', 0)}, stars={github.get('total_stars', 0)})"
            )
        if is_tech_claim and not github.get("has_ai_ml_repos", False):
            contradiction_points.append("GitHub: No AI/ML repositories despite AI narrative")

    # ──── H-1B / LCA Technical Hiring ────
    if h1b:
        data_sources.append("h1b_lca")
        tech_score = h1b.get("technical_intensity_score", 0)
        is_tech_claim = event_type in ("ai_pivot", "platform_story_no_evidence")
        if is_tech_claim and tech_score < 10:
            contradiction_points.append(
                f"H-1B: Claims tech transformation but tech intensity score = {tech_score} "
                f"(AI roles={h1b.get('ai_ml_roles', 0)}, eng roles={h1b.get('engineering_roles', 0)})"
            )
        elif tech_score >= 50:
            evidence_points.append(
                f"H-1B: Strong technical hiring (score={tech_score}, "
                f"STEM%={h1b.get('stem_roles_pct', 0)}%)"
            )
        hiring_trend = h1b.get("hiring_trend", "unknown")
        if hiring_trend == "declining":
            contradiction_points.append("H-1B: Hiring trend is declining")

    # ──── Reddit / Social Media ────
    if reddit:
        data_sources.append("reddit")
        attention = reddit.get("retail_attention_level", "normal")
        squeeze_mentions = reddit.get("squeeze_mentions", 0)
        bot_score = reddit.get("bot_suspicion_score", 0)
        dd_posts = reddit.get("dd_posts", 0)
        yolo_posts = reddit.get("yolo_posts", 0)

        if attention == "extreme" and squeeze_mentions > 20:
            contradiction_points.append(
                f"Reddit: Extreme retail attention (mentions={reddit.get('wsb_mentions_7d', 0)}, "
                f"squeeze_mentions={squeeze_mentions}, bot_score={bot_score:.0%})"
            )
        if yolo_posts > 5 and dd_posts <= 1:
            contradiction_points.append(
                f"Reddit: High YOLO posts ({yolo_posts}) vs low DD posts ({dd_posts}) -- speculative"
            )
        if bot_score >= 0.5:
            contradiction_points.append(
                f"Reddit: High bot suspicion score ({bot_score:.0%})"
            )
        if attention in ("normal",) and dd_posts >= 5:
            evidence_points.append(
                f"Reddit: Normal attention with substantive discussion (DD posts={dd_posts})"
            )

    # ──── Compute Final Verdict ────
    total_evidence = len(evidence_points)
    total_contradiction = len(contradiction_points)

    if total_contradiction >= 3:
        verdict = "contradicted"
        evidence_score = max(0, 20 - total_contradiction * 5)
    elif total_contradiction >= 2 and total_evidence <= 1:
        verdict = "contradicted"
        evidence_score = max(0, 30 - total_contradiction * 5)
    elif total_contradiction >= 1 and total_evidence <= 1:
        verdict = "weakly_supported"
        evidence_score = 35
    elif total_evidence >= 2 and total_contradiction == 0:
        verdict = "narrative_supported"
        evidence_score = min(90, 50 + total_evidence * 10)
    elif total_evidence >= 1:
        verdict = "weakly_supported"
        evidence_score = 45
    else:
        verdict = "weakly_supported"
        evidence_score = 30

    return {
        "ticker": ticker,
        "verdict": verdict,
        "evidence_score": evidence_score,
        "evidence_for": evidence_points,
        "evidence_against": contradiction_points,
        "data_sources_used": data_sources,
        "total_signals": total_evidence + total_contradiction,
    }


def get_thematic_research() -> dict:
    """
    ROLE 2: Market Structure / Thematic Research.

    Provides context on what the market actually needs:
    - Current dominant investment theme
    - Demand drivers and bottlenecks
    - Related industries and second-order beneficiaries
    - Valuation discipline guidance
    - Decision implications
    """
    return load_thematic_research()


def run(tickers: list[str], narrative_results: dict) -> dict:
    """Run alternative data verification for all tickers + thematic research."""
    results = {}
    for ticker in tickers:
        narr = narrative_results.get("classifications", {}).get(ticker, {})
        event_type = narr.get("event_type", "unknown")
        is_real = narr.get("is_real_value", False)
        results[ticker] = verify_narrative(ticker, event_type, is_real)

    thematic = get_thematic_research()

    return {
        "agent": "alt_data_verification",
        "verifications": results,
        "thematic_research": thematic,
    }

