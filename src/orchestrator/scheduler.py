"""
Decision Cadence Scheduler — spec stub (Step C, frozen as v0.6).

This module is a SPEC STUB. It enumerates the canonical Step C decision
anchors per RULES.md §6.6 / §6.7. The runtime today does not run on a
schedule — `/api/run` is manual-only per §6.5.

Per RULES.md §6.6:

  Surge-Short hourly scans (America/New_York):
    09:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30

  End-of-day window:
    16:15 ET

  Friday profit reinvestment trigger:
    15:30 ET — DECIDED on Friday, EXECUTED at Monday 09:30 ET open

Quality-long:
    Trigger-driven, not on a fixed cadence. Runs on demand when a
    candidate enters the §3.7 universe AND the §3.8 gates pass.

DO NOT WIRE THIS INTO THE RUNTIME PATH WITHOUT §19.8 PROMOTION.
The runtime evidence-packet binding is still v0.5; bumping the
cadence semantics would change the decision flow and break the
byte-identical regression baseline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Literal


HOURLY_SURGE_SCAN_TIMES_ET = (
    time(9, 30),
    time(10, 30),
    time(11, 30),
    time(12, 30),
    time(13, 30),
    time(14, 30),
    time(15, 30),
)

END_OF_DAY_TIME_ET = time(16, 15)

FRIDAY_REINVESTMENT_DECISION_TIME_ET = time(15, 30)
MONDAY_REINVESTMENT_EXECUTION_TIME_ET = time(9, 30)

CadenceAnchor = Literal[
    "hourly_surge_scan",
    "end_of_day_surge",
    "friday_reinvestment_decision",
    "monday_reinvestment_execution",
    "quality_long_trigger_driven",
]


@dataclass(frozen=True)
class Anchor:
    name: CadenceAnchor
    et_time: time | None
    decision_mode: str
    notes: str


CANONICAL_ANCHORS: tuple[Anchor, ...] = tuple(
    Anchor(
        name="hourly_surge_scan",
        et_time=t,
        decision_mode="opening_window" if t == time(9, 30) else "intraday_surge",
        notes=f"Hourly surge-short scan at {t.strftime('%H:%M')} ET (RULES.md §6.6).",
    )
    for t in HOURLY_SURGE_SCAN_TIMES_ET
) + (
    Anchor(
        name="end_of_day_surge",
        et_time=END_OF_DAY_TIME_ET,
        decision_mode="end_of_day_surge",
        notes="EOD window scan at 16:15 ET (RULES.md §6.6).",
    ),
    Anchor(
        name="friday_reinvestment_decision",
        et_time=FRIDAY_REINVESTMENT_DECISION_TIME_ET,
        decision_mode="end_of_day_surge",
        notes=(
            "Friday 15:30 ET realized-P&L reinvestment decision; execution "
            "deferred to Monday 09:30 ET open per RULES.md §6.7."
        ),
    ),
    Anchor(
        name="monday_reinvestment_execution",
        et_time=MONDAY_REINVESTMENT_EXECUTION_TIME_ET,
        decision_mode="opening_window",
        notes=(
            "Monday 09:30 ET execution of the Friday reinvestment packet "
            "(RULES.md §6.7). NO re-derivation between Friday close and "
            "Monday open — the Friday packet is immutable per §5.7."
        ),
    ),
    Anchor(
        name="quality_long_trigger_driven",
        et_time=None,
        decision_mode="trigger_driven",
        notes=(
            "Quality-long runs on demand when a candidate enters the §3.7 "
            "universe AND the §3.8 hard gates pass. Not on a fixed cadence."
        ),
    ),
)


__all__ = [
    "HOURLY_SURGE_SCAN_TIMES_ET",
    "END_OF_DAY_TIME_ET",
    "FRIDAY_REINVESTMENT_DECISION_TIME_ET",
    "MONDAY_REINVESTMENT_EXECUTION_TIME_ET",
    "CanonicalAnchor",
    "Anchor",
    "CANONICAL_ANCHORS",
]


CanonicalAnchor = Anchor  # Backward-compatible alias if needed.
