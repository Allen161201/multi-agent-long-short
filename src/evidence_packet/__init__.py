"""
Evidence Packet Generator v1 — live mode.

Public API:
    generate_evidence_packet(ticker, decision_mode="live", decision_timestamp=None)
        -> dict

The generator produces a schema-valid evidence packet for ONE ticker, RIGHT
NOW (live mode), using FMP Premium + FRED. No LLM, no SEC ingestion, no
GDELT, no OpenCLI. The block-level structure follows
`docs/EVIDENCE_PACKET_V1_DRAFT.md`. The decision-time discipline follows
`docs/DECISION_TIME_AND_LOOKAHEAD_POLICY.md`.

This module does NOT modify the existing `src/engine/evidence_packet.py`,
which remains the legacy backtest packet builder. It is a forward-compatible
new generator that will eventually supersede the legacy one (gated on a
future frozen-rules version bump).
"""
from .generator import (
    generate_evidence_packet,
    GENERATOR_VERSION,
    SCHEMA_VERSION,
)
from .pit_violation import PITViolationError

__all__ = [
    "generate_evidence_packet",
    "GENERATOR_VERSION",
    "SCHEMA_VERSION",
    "PITViolationError",
]
