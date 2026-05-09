"""
sentiment_community_ownership_evidence block — schema §8f (placeholder for v1).

v1 emits the schema's full field skeleton with `status = not_evaluated`.
No live sentiment / community-size / ownership feed is wired today.
"""
from __future__ import annotations

from ..schema import BlockKey, BlockStatus, Source


def build() -> dict:
    block = {
        "status": BlockStatus.NOT_EVALUATED,
        "source": Source.NOT_CONNECTED,
        "as_of": None,
        "available_as_of": None,
        "data_available": False,
        "as_of_date": None,
        "source_list": [],

        # ── A. Market sentiment ──
        "market_sentiment": {
            "status": BlockStatus.NOT_EVALUATED,
            "data_available": False,
            "sentiment_score": None,
            "sentiment_window": "not_evaluated",
            "sentiment_change_1d": None,
            "sentiment_change_3d": None,
            "sentiment_change_7d": None,
            "negative_news_intensity": None,
            "positive_news_intensity": None,
            "sentiment_source_count": None,
            "source_tier_weighted_sentiment": None,
            "sentiment_pollution_risk": "unknown",
            "credibility_adjusted_sentiment": None,
            "sentiment_credibility_adjustment": "not_evaluated",
            "sentiment_data_quality_flags": [],
        },

        # ── B. Community size ──
        "community_size": {
            "status": BlockStatus.NOT_EVALUATED,
            "data_available": False,
            "community_source": "not_evaluated",
            "community_member_count": None,
            "mention_count_1d": None,
            "mention_count_7d": None,
            "mention_count_30d": None,
            "mention_zscore_60d": None,
            "developer_community_metrics": {
                "github_stars": None,
                "github_contributors_30d": None,
                "github_forks": None,
                "npm_weekly_downloads": None,
                "pypi_weekly_downloads": None,
                "package_download_growth": None,
            },
            "abnormal_attention_flag": False,
            "meme_attention_flag": False,
            "community_data_quality_flags": [],
        },

        # ── C. Ownership / smart-money positioning ──
        "ownership_positioning": {
            "status": BlockStatus.NOT_EVALUATED,
            "data_available": False,
            "report_date": None,
            "filing_date": None,
            "accepted_datetime": None,
            "PIT_safety_notes": (
                "13F-style ownership data is filed with delay; PIT-safety requires "
                "accepted_datetime <= allowed_data_cutoff. v0.8.7 wires sec_13f / "
                "sec_form4 / sec_def14a via live_adapters; this skeleton text "
                "remains when no rows were delivered for this ticker — see "
                "sibling sub-blocks (sec_13f, sec_form4, sec_def14a) for actual "
                "adapter status."
            ),
            "institutional_ownership_pct": None,
            "top_10_holder_concentration": None,
            "top_25_holder_concentration": None,
            "ETF_MF_exposure": None,
            "insider_ownership_pct": None,
            "hedge_fund_holder_count": None,
            "hedge_fund_ownership_change": None,
            "top_holders": [],
            "ownership_change_summary": "not_evaluated",
            "new_positions_count": None,
            "sold_out_positions_count": None,
            "ownership_crowding_risk": "unknown",
            "smart_money_support_assessment": "not_evaluated",
            "data_staleness_warning": (
                "ownership feed wired (sec_13f / sec_form4 / sec_def14a); "
                "staleness will be populated by _finalize_sentiment_ownership "
                "when adapter rows arrive"
            ),
        },

        "reason": (
            "v0.8.7 wires fmp_sentiment / sec_13f / sec_form4 / sec_def14a / "
            "github_public via the live_adapters overlay; this skeleton text "
            "remains when no child rows were delivered for this ticker — see "
            "sibling sub-blocks (fmp_sentiment, sec_13f, sec_form4) for actual "
            "adapter status. status/source/reason are overwritten by "
            "_finalize_sentiment_ownership when ANY sub-section delivers."
        ),
    }
    return {
        "block": block,
        "source_list_entries": [],
        "quality_flags": [{
            "kind": "sentiment_ownership_placeholder",
            "severity": "info",
            "detail": "sentiment_community_ownership_evidence is a placeholder in v1",
        }],
        "pit_flags": [],
        "agent_notes": [],
        "api_calls_made": 0,
    }


def block_key() -> str:
    return BlockKey.SENTIMENT_OWNERSHIP
