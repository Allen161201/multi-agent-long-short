"""
information_integrity_assessment block — schema §8d (placeholder for v1).

v1 emits the schema's full field skeleton with `status = not_evaluated`.
No pollution-defense adapter is wired today.
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

        # Claim-first verification
        "claim_summary": None,
        "claim_source_type": "not_evaluated",
        "source_tier_distribution": {"T1": 0, "T2": 0, "T3": 0, "T4": 0, "T5": 0},

        "primary_source_found": False,
        "primary_source_details": [],
        "reputable_confirmation_found": False,
        "official_disclosure_confirmation": False,

        # §10.5 pollution-risk descriptors — concept handles only
        "social_attention_spike": "not_evaluated",
        "post_burstiness": None,
        "near_duplicate_text_ratio": None,
        "abnormal_post_velocity": None,
        "low_reputation_source_share": None,
        "claim_without_primary_source_ratio": None,
        "cross_platform_copy_paste_similarity": None,
        "coordinated_campaign_risk": "unknown",
        "bot_like_pattern_risk": "unknown",
        "rumor_without_evidence_flag": False,
        "pollution_risk_level": "unknown",
        "evidence_credibility_assessment": "not_evaluated",

        "corroboration_status": "not_evaluated",

        # Governance gate (schema doc v1.1, decision #8: FAIL-CLOSED).
        # Until pollution-defense is implemented and verified, the
        # placeholder block must NOT permit social/community evidence
        # to be used as a primary decision input.
        "use_as_primary_signal_allowed": False,
        "reason_use_as_primary_signal_disallowed": (
            "pollution-defense layer not implemented in v1; "
            "fail-closed per schema doc v1.1 decision #8"
        ),

        "source_list": [],
        "PIT_safety_notes": None,
        "data_quality_flags": [],

        "reason": ("v1 generator does not wire pollution-defense; "
                   "schema skeleton emitted as not_evaluated"),
    }
    return {
        "block": block,
        "source_list_entries": [],
        "quality_flags": [{
            "kind": "info_integrity_placeholder",
            "severity": "info",
            "detail": "information_integrity_assessment is a placeholder in v1",
        }],
        "pit_flags": [],
        "agent_notes": [],
        "api_calls_made": 0,
    }


def block_key() -> str:
    return BlockKey.INFORMATION_INTEGRITY
