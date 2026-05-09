"""
Risk Agent — spec stub (Step C, frozen as v0.6).

This module is a SPEC STUB for the Risk agent's veto and advisory
contract. The runtime today wires the Risk agent through
`src/agents/risk_pm.py` (legacy pre-LLM path) and the v1.1 prompt
in `src/agents/prompts/risk_pm_v1.1.txt`. This stub captures the
Step C contract independently so it can be lifted into the runtime
during a future §19.8 promotion.

Per RULES.md §5.13 / §5.14 / §5.15:

  Veto authority (§5.13) — UNCONDITIONAL hard veto when:
    a) long entry would breach §4.7 equity cap
    b) short entry would breach §4.8 per-position 5% cap or 10% sleeve
    c) data_quality_flags[*].severity == "critical" (§5.10)
    d) §10 anti-pollution defense flips use_as_primary_signal_allowed=false
       without T1/T2 corroboration
    e) packet uses post-cutoff data (§5.4)
  Veto sets decision = "veto" AND position_size_pct = 0.0; cannot be
  overridden by sleeve agents or PM tier.

  Advisory format (§5.14) — 5-clause structure for soft cautions:
    "[Concern type]. [Magnitude]. [Historical context]. [Current regime]. [Recommendation]."
  Each clause is one sentence; total prose 200-400 chars.

  Veto-vs-advisory boundary (§5.15) — a single packet can carry BOTH
  (per-decision-row evaluation, not per-packet).

DO NOT WIRE THIS INTO THE RUNTIME PATH WITHOUT §19.8 PROMOTION.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


VetoReason = Literal[
    "equity_cap_breach",          # §5.13(a) — would exceed §4.7 equity max
    "per_position_cap_breach",    # §5.13(b) — would exceed §4.8 5% per-position cap
    "sleeve_cap_breach",          # §5.13(b) — would exceed §4.8 10% sleeve cap
    "data_quality_critical",      # §5.13(c) — §5.10 critical flag
    "pollution_no_t1_t2",         # §5.13(d) — §10 anti-pollution + no T1/T2
    "post_cutoff_data_used",      # §5.13(e) — §5.4 lookahead
]

ADVISORY_CLAUSE_FIELDS = (
    "concern_type",
    "magnitude",
    "historical_context",
    "current_regime",
    "recommendation",
)

ADVISORY_PROSE_MIN_CHARS = 200
ADVISORY_PROSE_MAX_CHARS = 400


@dataclass(frozen=True)
class VetoOutcome:
    tripped: bool
    reasons: tuple[VetoReason, ...] = ()
    detail: str = ""

    def to_decision_overrides(self) -> dict:
        """Returns the §5.2 enforcement payload."""
        if not self.tripped:
            return {}
        return {
            "decision": "veto",
            "position_size_pct": 0.0,
            "veto_reasons": list(self.reasons),
            "veto_detail": self.detail,
        }


@dataclass(frozen=True)
class AdvisoryNote:
    concern_type: str
    magnitude: str
    historical_context: str
    current_regime: str
    recommendation: str

    def to_prose(self) -> str:
        """Render the 5-clause advisory string per §5.14."""
        return (
            f"{self.concern_type.rstrip('.')}. "
            f"{self.magnitude.rstrip('.')}. "
            f"{self.historical_context.rstrip('.')}. "
            f"{self.current_regime.rstrip('.')}. "
            f"{self.recommendation.rstrip('.')}."
        )

    def is_well_formed(self) -> bool:
        prose = self.to_prose()
        return ADVISORY_PROSE_MIN_CHARS <= len(prose) <= ADVISORY_PROSE_MAX_CHARS


@dataclass(frozen=True)
class RiskAgentOutput:
    veto: VetoOutcome
    advisory: AdvisoryNote | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "VetoReason",
    "ADVISORY_CLAUSE_FIELDS",
    "ADVISORY_PROSE_MIN_CHARS",
    "ADVISORY_PROSE_MAX_CHARS",
    "VetoOutcome",
    "AdvisoryNote",
    "RiskAgentOutput",
]
