"""
Quality-Long Candidate Universe — spec stub (Step C, frozen as v0.6).

This module is a SPEC STUB. The runtime universe today is whatever the
candidate scan returns; Step D (or later) will wire this in.

Per RULES.md §3.7 / §3.8 / §3.9:

  - Universe = S&P 500 ∪ Nasdaq 100 (≈550 distinct tickers, ~80 overlap)
  - PIT-validated per §8.R5 in replay mode
  - Excludes: ADRs, SPACs, OTC, dual-class B/C low-float
  - Hard gates for buy: eps_ttm > 0 AND operating_margin_ttm > 0 AND
    financial_health == "sound" (no going concern, D/E ≤ 3.0,
    positive op CF last 4q)
  - ETF exception: broad-market ETFs (SPY/QQQ/TQQQ/SPXL/...) admitted
    as long instruments ONLY when regime == "crisis"

DO NOT WIRE THIS INTO THE RUNTIME PATH WITHOUT §19.8 PROMOTION.
The runtime evidence-packet binding is still v0.5; bumping the
universe semantics would change the packet hash and break the
byte-identical regression baseline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


HARD_GATE_FIELDS = (
    "eps_ttm",
    "operating_margin_ttm",
    "financial_health",
)

EXCLUDED_INSTRUMENT_TYPES = (
    "ADR",
    "SPAC",
    "OTC",
    "dual_class_low_float",
)

ETF_EXCEPTION_REGIMES = ("crisis",)

ETF_EXCEPTION_TICKERS = (
    "SPY",
    "QQQ",
    "TQQQ",
    "SPXL",
)


@dataclass(frozen=True)
class HardGateResult:
    passed: bool
    failed_gates: tuple[str, ...]
    notes: str


def evaluate_hard_gates(
    *,
    eps_ttm: float | None,
    operating_margin_ttm: float | None,
    financial_health: str | None,
) -> HardGateResult:
    """Spec-only evaluator. Returns which gates failed.

    Missing values (None) FAIL the gate — never silently passed as zero.
    """
    failed: list[str] = []
    if eps_ttm is None or eps_ttm <= 0.0:
        failed.append("eps_ttm")
    if operating_margin_ttm is None or operating_margin_ttm <= 0.0:
        failed.append("operating_margin_ttm")
    if financial_health != "sound":
        failed.append("financial_health")
    return HardGateResult(
        passed=len(failed) == 0,
        failed_gates=tuple(failed),
        notes=(
            "All gates pass (necessary, not sufficient — agent retains "
            "discretion to recommend WATCH)."
            if not failed
            else f"Failed gates: {', '.join(failed)} — downgrade to watch / no_trade."
        ),
    )


def is_etf_admitted(*, ticker: str, regime: str) -> bool:
    """ETFs admitted ONLY in Crisis regime (RULES.md §3.9)."""
    return regime in ETF_EXCEPTION_REGIMES and ticker in ETF_EXCEPTION_TICKERS


def build_universe_spec(
    *,
    sp500_constituents: Iterable[str],
    nasdaq100_constituents: Iterable[str],
    instrument_type_lookup: dict[str, str],
) -> set[str]:
    """Spec-only universe builder. Caller supplies PIT-correct constituent lists.

    Returns the set difference: (S&P500 ∪ Nasdaq100) minus excluded types.
    """
    union = set(sp500_constituents) | set(nasdaq100_constituents)
    return {
        t for t in union
        if instrument_type_lookup.get(t, "common_stock") not in EXCLUDED_INSTRUMENT_TYPES
    }


__all__ = [
    "HARD_GATE_FIELDS",
    "EXCLUDED_INSTRUMENT_TYPES",
    "ETF_EXCEPTION_REGIMES",
    "ETF_EXCEPTION_TICKERS",
    "HardGateResult",
    "evaluate_hard_gates",
    "is_etf_admitted",
    "build_universe_spec",
]
