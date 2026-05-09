"""Script-side veto-scope filter (RULES.md §5.13 + §5.17 + §4.7 + §3.8 + §13.6).

§4.7 equity_max_cap and §3.8 fundamental hard gates are quality_long-only
mechanics; surge_short candidates should not be vetoed on those bases.
§5.17 surge-short integrity-veto exception + §13.6 universal soft-veto
principle treat absence-based advisories as advisory (not veto) for
surge_short, since structural absence of corroboration IS the bear-thesis
input under the asymmetric absence rule.

Risk-agent prompts encode this scope, but agents apply it inconsistently
at runtime. This filter enforces the scope deterministically when called
between Risk and PM, so PM sees the corrected veto state at decision time
(not just post-hoc in the forensic JSON).

Wired into:
  - src/agents/runner.py (between Risk run and PM run, runtime enforcement)
  - scripts/portfolio_5day_*.py (defensive post-runner call, audit only)

NO LLM. NO RULES.md edits. NO prompt edits.
"""
from __future__ import annotations

# Names that prefix-match here are quality_long-only mechanics. For
# surge_short, the trip is downgraded to advisory.
QL_ONLY_PREFIXES = (
    "long_entry_breaches_equity_max_cap",
    "long_entry_breaches_equity_cap",
    "fundamental_hard_gates",
    "equity_max_cap",
    "equity_cap_4_7",
    "equity_cap",
    # NOTE 2026-05-04: "hard_risk_limit_breached" deliberately OMITTED
    # pending human review — XTLB context shows it was a derivative of
    # §3.8 + regime incompatibility, but the name is generic and could
    # legitimately reference position-size/sleeve-cap arithmetic on
    # other candidates. Add only after explicit ack.
)

# Names that prefix-match here are absence-based advisories. For
# surge_short under §5.17 + §13.6, these are advisory only — structural
# absence of corroboration IS the bear-thesis evidence, per the
# asymmetric-absence rule.
SURGE_ADVISORY_NOT_VETO = (
    "missing_data_persists_beyond_tolerance",
    "missing_data_persists",
    "critical_data_quality_flag",
    "data_quality_critical",
    "insufficient_evidence",
)


def filter_risk_veto_by_candidate_type(risk_output, candidate_type):
    """Enforce RULES.md veto scoping per candidate_type.

    For surge_short, downgrade matching veto trips in-place:
      - sets entry["tripped"] = False
      - adds entry["downgraded_reason"] explaining the rule citation
      - appends a [SCRIPT-FILTER §5.17: …] tag to advisory_notes

    No-op for any other candidate_type. Mutates `risk_output` and
    returns it for chaining convenience.
    """
    if candidate_type != "surge_short":
        return risk_output

    veto_evals = risk_output.get("veto_conditions_evaluated", [])

    downgraded = []
    for v in veto_evals:
        name = v.get("name", "")
        if any(name.startswith(p) for p in QL_ONLY_PREFIXES):
            if v.get("tripped"):
                v["tripped"] = False
                v["downgraded_reason"] = (
                    f"§5.17 carve-out: {name} is quality_long-scoped, "
                    f"not applicable to surge_short"
                )
                downgraded.append(name)
        elif any(name.startswith(p) for p in SURGE_ADVISORY_NOT_VETO):
            if v.get("tripped"):
                v["tripped"] = False
                v["downgraded_reason"] = (
                    "Universal soft-veto §13.6 + §5.17: advisory only "
                    "for surge_short"
                )
                downgraded.append(name)

    if downgraded:
        existing_notes = risk_output.get("advisory_notes", "")
        downgrade_note = (
            f" [SCRIPT-FILTER §5.17: downgraded {len(downgraded)} veto "
            f"trips to advisory: {', '.join(downgraded)}]"
        )
        risk_output["advisory_notes"] = (existing_notes or "") + downgrade_note

    return risk_output


__all__ = [
    "QL_ONLY_PREFIXES",
    "SURGE_ADVISORY_NOT_VETO",
    "filter_risk_veto_by_candidate_type",
]
