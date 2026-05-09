"""
alternative_data_features block — schema §8 (placeholder for v1).

v1 emits the schema-shaped skeleton with `status = not_evaluated` for every
sub-section. No alt-data adapter is wired today (per
`docs/ALTERNATIVE_DATA_FRAMEWORK.md` and PROJECT_HANDOFF). The block is
present so downstream consumers don't crash on a missing key.
"""
from __future__ import annotations

from ..schema import BlockKey, BlockStatus, Source


# The list of future adapter slots from
# docs/ALTERNATIVE_DATA_FRAMEWORK.md §9 (industry-specific) + §9.7 (opencli).
# Each appears as a `not_connected` row in the placeholder.
FUTURE_ADAPTER_SLOTS = (
    {"adapter_id": "software_devtools_adapter",   "industry": "tech_software_devtools"},
    {"adapter_id": "semiconductor_patent_adapter","industry": "semiconductor_ai_hardware"},
    {"adapter_id": "biotech_clinical_adapter",    "industry": "biotech_pharma"},
    {"adapter_id": "consumer_traffic_adapter",    "industry": "retail_restaurants_consumer"},
    {"adapter_id": "bank_regulatory_adapter",     "industry": "banks_financials"},
    {"adapter_id": "airline_travel_adapter",      "industry": "airlines_travel"},
    {"adapter_id": "energy_commodity_adapter",    "industry": "energy_commodities"},
    {"adapter_id": "auto_mobility_adapter",       "industry": "auto_ev_mobility"},
    {"adapter_id": "default_sec_gdelt_adapter",   "industry": "default_fallback"},
    {"adapter_id": "opencli_public_web_adapter",  "industry": "auxiliary_web_evidence"},
)


def build() -> dict:
    block = {
        "status": BlockStatus.NOT_EVALUATED,
        "source": Source.NOT_CONNECTED,
        "as_of": None,
        "available_as_of": None,
        "reason": ("v1 evidence packet does not wire any alternative-data "
                   "adapter; schema-shaped skeleton emitted so downstream "
                   "consumers do not crash on missing key"),

        # §8 sub-blocks — all not_evaluated
        "retail_meme_attention": {
            "status": BlockStatus.NOT_EVALUATED,
            "source": Source.NOT_CONNECTED,
            "reddit_mention_spike": None,
            "wallstreetbets_flag": False,
            "meme_language_flag": False,
            "abnormal_retail_attention_score": None,
        },
        "valuation_context": {
            "status": BlockStatus.NOT_EVALUATED,
            "source": Source.NOT_CONNECTED,
            "valuation_support_score": None,
            "quality_score": None,
            "profitability_support": None,
            "leverage_risk": None,
            "DCF_gap_display_only": None,
            "current_snapshot_not_PIT_safe": True,
        },

        # §8b — adapter selection — schema-only placeholder
        "adapter_selection": {
            "status": BlockStatus.NOT_EVALUATED,
            "primary_industry": None,
            "selected_adapter_id": "default_sec_gdelt_adapter",
            "adapter_selection_reason": "no live adapter wired in v1; default placeholder",
            "adapter_data_available": False,
            "adapter_data_as_of": None,
            "adapter_confidence": BlockStatus.NOT_EVALUATED,
            "adapter_features_available": [],
            "adapter_features_missing": ["all"],
            "fallback_to_default_adapter": True,
            "adapter_pit_safety_notes": (
                "no adapter connected; pit-safety not applicable today"
            ),
            "adapter_source_list": [
                {"label": slot["adapter_id"], "industry": slot["industry"],
                 "source": Source.NOT_CONNECTED, "url": None, "as_of": None,
                 "rate_limited": None}
                for slot in FUTURE_ADAPTER_SLOTS
            ],
        },

        # §8c — opencli placeholder (omitted from required outputs but
        # included for forward-compat clarity)
        "opencli_evidence": {
            "data_available": False,
            "status": BlockStatus.NOT_EVALUATED,
            "source": Source.NOT_CONNECTED,
            "command_used": None,
            "source_url": None,
            "extraction_status": BlockStatus.NOT_EVALUATED,
            "PIT_safety_notes": (
                "OpenCLI not invoked in v1 generator; reads not point-in-time when wired"
            ),
            "human_authorization_required": False,
            "logged_in_session_used": False,
        },
    }

    return {
        "block": block,
        "source_list_entries": [],
        "quality_flags": [{
            "kind": "alt_data_placeholder",
            "severity": "info",
            "detail": "alternative-data adapters not wired in v1 — schema-shape placeholder only",
        }],
        "pit_flags": [],
        "agent_notes": [],
        "api_calls_made": 0,
    }


def block_key() -> str:
    return BlockKey.ALTERNATIVE_DATA_FEATURES
