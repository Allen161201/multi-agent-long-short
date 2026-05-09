"""Per-block builders for evidence_packet_v1.

Every block builder returns a `BlockResult` dict shaped:

    {
        "block": dict,                        # the block payload itself
        "source_list_entries": list[dict],    # rows for the meta source_list
        "quality_flags": list[dict],          # rows for data_quality_flags
        "pit_flags": list[dict],              # rows for PIT_safety_flags
        "agent_notes": list[str],             # one-liners for agent_ready_notes
        "api_calls_made": int,                # FMP/FRED calls used by this block
    }

The orchestrator (`generator.py`) merges these results into the final
packet. Block builders never mutate global state and never write files.
"""
from typing import TypedDict


class BlockResult(TypedDict, total=False):
    block: dict
    source_list_entries: list[dict]
    quality_flags: list[dict]
    pit_flags: list[dict]
    agent_notes: list[str]
    api_calls_made: int
