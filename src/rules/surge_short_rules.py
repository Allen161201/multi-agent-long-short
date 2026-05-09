"""
Surge Short Rules — guardrails only.

This module enforces the non-negotiable, hard-coded portion of the surge-short
sleeve. Investment conclusions (catalyst meaning, thesis validity, etc.) are
NOT made here — they are produced by the agent decision module
(``src.agents.agent_decision_schema``) and consumed downstream by the Risk/PM
agent.

What lives here:
  1. Mechanical candidate screen (50% / 1M / $2 / common stock).
  2. Hard-coded baseline exclusions: confirmed M&A with no arb, recent IPO,
     fully FDA-approved marketed-drug events, network-effect companies.
     These BLOCK new short entry only — existing positions follow the
     agentic exit rules (RULES.md §2.6, §2.13). Anything NOT on this list
     goes to agent analysis, including all biotech trial-update events
     (Phase 1, Phase 2, breakthrough designation, etc.).
  3. Position sizing math (Step C v0.6): 0.5% initial; +0.5% on each
     additional 100% price rise from the ORIGINAL entry price; per-position
     cap 5%; sleeve cap 10% of portfolio.
  4. Position review/exit triggers — agent-driven plus a small set of hard
     structural triggers. NO mechanical mark-to-market P&L stop.

What does NOT live here (deliberately):
  - "AI pivot = short", "crypto pivot = short", "delisting relief = short".
  - "Suspicious narrative + alt-data weakly supported = shortable".
  - "P&L < -30% = automatic exit", "Day 2+ = automatic reduce".
  - "Poor fundamentals = automatically shortable".

Those decisions belong to the agent.
"""
from __future__ import annotations

from typing import Any

EXCLUDED_SECURITY_TYPES = {"ETF", "warrant", "unit", "preferred", "reverse_split"}

# Hard-coded baseline exclusion identifiers. The string IDs match
# config/frozen_rules_v0.6_agentic_allocation_5regime.yaml::baseline_short_exclusions
# Step C Decision 3 added "network_effect_company"; the prior three carried
# forward unchanged from v0.4_agentic_pre_llm.
BASELINE_EXCLUSION_IDS = {
    "confirmed_acquisition_no_arb",
    "recent_ipo_30d",
    "fully_fda_approved_marketed_drug",
    "network_effect_company",
}


# =============================================================
# 1. Hard-coded candidate screen
# =============================================================

def _enrich_with_pit_fundamentals(
    g: dict, decision_date,
) -> dict:
    """Pass 8 Step B1.7 (2026-05-04): fetch PIT-safe fundamentals for one
    candidate and merge eps_ttm + operating_margin_ttm + audit fields onto
    the gainer dict. Called by filter_surge_candidates when decision_date is
    provided. Lazy import to avoid pulling fmp_adapter into modules that
    don't need network access (e.g. unit tests with stubbed gainers).

    Adds these fields onto the returned dict (all may be None / 'unavailable'):
      - eps_ttm                   : float | None
      - operating_margin_ttm      : float | None
      - financial_health          : str | None  (from PIT fundamentals)
      - pit_safe                  : bool        (True iff fetch returned PIT data)
      - pit_quarters_used         : int
      - data_available_as_of      : str | None  (ISO acceptedDate)
      - data_quality_flag         : str | None  ("fundamental_unavailable_pit"
                                                  if pit_safe=False or quarters<4)

    Pass-through behavior: if the gainer dict ALREADY has eps_ttm /
    operating_margin_ttm (e.g. test stub or live adapter pre-attached
    them), those values win and we skip the FMP fetch — the function is
    idempotent so callers can pre-enrich for unit tests.
    """
    has_eps = "eps_ttm" in g and g.get("eps_ttm") is not None
    has_om  = "operating_margin_ttm" in g and g.get("operating_margin_ttm") is not None
    if has_eps and has_om:
        return g

    out = dict(g)
    try:
        from src.data_adapters.fmp_adapter import (
            get_fundamentals_for_scoring_pit,
        )
    except ImportError:
        out["pit_safe"] = False
        out["pit_quarters_used"] = 0
        out["data_quality_flag"] = "fundamental_unavailable_pit"
        return out

    fund = get_fundamentals_for_scoring_pit(g["ticker"], decision_date)
    pit_safe = bool(fund.get("pit_safe", False))
    pit_qtrs = int(fund.get("pit_quarters_used", 0) or 0)

    if not pit_safe or pit_qtrs < 4:
        # Fail-closed per FIX 7.4: do NOT pass and do NOT fail the §2.1
        # fundamental gate. Mark the candidate so downstream pool routing
        # can EXCLUDE from §31 mandatory-short and ROUTE to 60-90% pool
        # with the data_quality_flag.
        out["pit_safe"] = pit_safe
        out["pit_quarters_used"] = pit_qtrs
        out["data_available_as_of"] = fund.get("data_available_as_of")
        out["data_quality_flag"] = "fundamental_unavailable_pit"
        # Leave eps_ttm/om_ttm as None so the gate evaluates as
        # data-unavailable (None values pass the gate per existing
        # `is not None and >= 0` semantics).
        return out

    # Successful PIT fetch — surface eps_ttm and operating_margin_ttm.
    # Note: operating_margin_pct in the PIT function IS the trailing-4-
    # quarter operating margin (computed from filtered quarters); we
    # alias it as operating_margin_ttm for the §2.1 gate naming.
    out["eps_ttm"] = fund.get("eps_ttm")
    out["operating_margin_ttm"] = fund.get("operating_margin_ttm")
    if out["operating_margin_ttm"] is None:
        # Backward-compat alias — fmp_adapter pre-Step-B1.7 returned
        # only operating_margin_pct.
        out["operating_margin_ttm"] = fund.get("operating_margin_pct")
    out["financial_health"] = fund.get("financial_health")
    out["pit_safe"] = True
    out["pit_quarters_used"] = pit_qtrs
    out["data_available_as_of"] = fund.get("data_available_as_of")
    out["data_quality_flag"] = None
    return out


def filter_surge_candidates(
    gainers: list[dict], decision_date=None,
) -> list[dict]:
    """Filter daily top gainers to surge-short candidates.

    Pass 8 (2026-05-04) tightens §2.1 mechanical screen:
      - daily return >= 60% (was 50%)
      - volume >= 1M, prior close > $2 (unchanged)
      - common stock (EXCLUDED_SECURITY_TYPES — unchanged)
      - EPS_TTM < 0 AND operating_margin_TTM < 0 (NEW §2.1 fundamental gate;
        BOTH must be true; if EITHER >= 0 the candidate is excluded)
      - prior 10-day cumulative close-to-close decline >= 30% → EXCLUDE
        regardless of pool (NEW §2.15 technical-rebound exclusion;
        candidate.cumulative_10d_decline_pct expected as POSITIVE-MAGNITUDE
        percentage, e.g. 0.32 = 32% decline)
      - corporate_calendar earnings event within ±5 trading days → flag
        candidate as `earnings_proximate=True`. Earnings-proximate
        candidates are EXCLUDED from §31 mandatory-short pool (handled by
        is_mandatory_short_eligible) but REMAIN in the candidate list for
        §32 60-90% pool routing.

    Pass 8 Step B1.7 (2026-05-04) — `decision_date` parameter:
      - When provided (date | datetime | ISO string), each candidate that
        passes the OHLCV+security gates is enriched with PIT-safe
        fundamentals via get_fundamentals_for_scoring_pit(ticker,
        decision_date). The §2.1 EPS<0+OM<0 gate then runs against
        TRAILING-4-QUARTER PIT values (acceptedDate <= decision_date).
      - When None (default): legacy behavior — gate runs against
        whatever eps_ttm / operating_margin_ttm values the caller already
        attached to the gainer dict. Preserves regression hash for tests
        that pass pre-enriched stubs.
      - Fail-closed (FIX 7.4): if PIT fetch returns pit_safe=False OR
        pit_quarters_used < 4, the candidate is kept in the list with
        `data_quality_flag='fundamental_unavailable_pit'` for the
        agent-discretion 60-90% pool, but is structurally INELIGIBLE
        for the §31 mandatory-short trigger (handled by
        is_mandatory_short_eligible — see below).
    """
    candidates: list[dict] = []
    for g in gainers:
        if g.get("change_pct", 0) < 60:
            continue
        if g.get("volume", 0) < 1_000_000:
            continue
        if g.get("prior_close", 0) < 2.0:
            continue
        sec_type = (g.get("security_type") or "").lower()
        if any(ex in sec_type for ex in EXCLUDED_SECURITY_TYPES):
            continue

        # §2.1 Pass 8 fundamental gate (EPS_TTM < 0 AND OM_TTM < 0).
        # Pass 8 Step B1.7: if decision_date provided, fetch PIT-safe
        # fundamentals here. The enrichment is per-candidate AFTER the
        # OHLCV gates have already filtered the universe down to a tiny
        # set (typically 0–5 names per day), so the per-candidate fetch
        # cost is negligible.
        if decision_date is not None:
            g = _enrich_with_pit_fundamentals(g, decision_date)

        # Both must be present and both negative; missing fields treated as
        # gate failure — no fundamental snapshot means we cannot establish
        # surge-short eligibility, so the candidate drops to the 60-90%
        # agent-discretion pool by virtue of NOT being mandatory-short.
        # We DO NOT exclude on missing fundamentals here; we let the 60-90%
        # pool absorb it. Only a present EPS_TTM >= 0 OR OM_TTM >= 0 fully
        # excludes a candidate from the surge_short universe.
        eps_ttm = g.get("eps_ttm")
        om_ttm = g.get("operating_margin_ttm")
        if eps_ttm is not None and eps_ttm >= 0:
            continue
        if om_ttm is not None and om_ttm >= 0:
            continue

        # §2.15 Pass 8 technical-rebound exclusion. POSITIVE-magnitude
        # 10-day decline >= 0.30 means the stock crashed 30% over the
        # prior 10 trading days; the surge is mean-reversion, not
        # catalyst-driven. Excluded entirely.
        rebound_decline = g.get("cumulative_10d_decline_pct")
        if isinstance(rebound_decline, (int, float)) and rebound_decline >= 0.30:
            continue

        # §2.14 Pass 8 earnings-proximate flag. Does NOT exclude from the
        # candidate list — only from the §31 mandatory-short pool. The
        # 60-90% pool can still route earnings-proximate candidates if
        # narrative_event + alt_data_verification confirm the surge is
        # not principally attributable to earnings.
        earnings_proximate = bool(g.get("earnings_within_5_trading_days", False))
        g_out = dict(g)
        g_out["earnings_proximate"] = earnings_proximate

        candidates.append(g_out)
    return candidates


# =============================================================
# §32 Pass 8 — mandatory-short eligibility + §33 scoring
# =============================================================

def is_mandatory_short_eligible(
    candidate: dict,
    surge_pct: float,
    *,
    baseline_exclusion_blocked: bool = False,
    network_effect_evidence: str | None = None,
) -> tuple[bool, dict[str, bool]]:
    """Per §32.2 — return (eligible, gate_results) for §31 mandatory-short.

    surge_pct is the prior_close→today_open % gap (e.g. 0.95 = 95% surge).
    All §32.2 gates (a-i) must pass. Gate booleans are returned for audit.

    The caller is responsible for passing baseline_exclusion_blocked
    (from baseline_exclusion()), and network_effect_evidence ('strong'
    /'partial'/'weak'/'none'). Mandatory short requires:
      surge >= 0.90 AND
      §2.1 mechanical screen (already enforced by filter_surge_candidates) AND
      NOT baseline_exclusion (covers §2.9-§2.12) AND
      NOT earnings_proximate (covers §2.14) AND
      NOT technical_rebound (already excluded by filter_surge_candidates)
    """
    gates: dict[str, bool] = {
        "a_mechanical_screen_60pct_eps_neg_om_neg": True,  # filter_surge_candidates pre-applied
        "b_prior_trading_day_29_2": True,                  # caller computed surge_pct via §29.2
        "c_common_stock": True,                            # filter_surge_candidates pre-applied
        "d_not_confirmed_ma_2_9": not baseline_exclusion_blocked
            or candidate.get("baseline_exclusion_id") != "confirmed_acquisition_no_arb",
        "e_not_recent_ipo_2_10": not baseline_exclusion_blocked
            or candidate.get("baseline_exclusion_id") != "recent_ipo_30d",
        "f_not_fully_fda_2_11": not baseline_exclusion_blocked
            or candidate.get("baseline_exclusion_id") != "fully_fda_approved_marketed_drug",
        "g_not_network_effect_2_12": not baseline_exclusion_blocked
            or candidate.get("baseline_exclusion_id") != "network_effect_company",
        "h_not_earnings_proximate_2_14": not bool(candidate.get("earnings_proximate", False)),
        "i_not_technical_rebound_2_15": True,              # filter_surge_candidates pre-applied
    }
    surge_threshold_met = surge_pct >= 0.90
    eligible = surge_threshold_met and all(gates.values())
    gates["surge_pct_ge_90"] = surge_threshold_met
    return eligible, gates


# §33.2 score-component constants (single source of truth for the scorer
# and for any audit consumer that wants to interpret PM emits).
SCORE_NARRATIVE_NEGATIVE = +2   # vague / fabricated / unsupported_claim
SCORE_NARRATIVE_POSITIVE = -2   # supported / credible_catalyst
SCORE_ALT_CONTRADICTED   = +2   # narrative_contradicted
SCORE_ALT_WEAK_CONTRA    = +1   # weakly_contradicted
SCORE_ALT_CORROBORATED   = -2   # narrative_corroborated
SCORE_BLOCKS_ABSENT_PER  = +1   # per element in expected_blocks_absent
SCORE_BLOCKS_ABSENT_CAP  = +2   # cap on absent-block bonus

NARRATIVE_NEGATIVE_VERDICTS = {"vague", "fabricated", "unsupported_claim"}
NARRATIVE_POSITIVE_VERDICTS = {"supported", "credible_catalyst"}


def compute_short_conviction_score(prelude_outputs: dict) -> tuple[float, dict[str, float], str]:
    """§33.2 SHORT_CONVICTION_SCORE for the 60-90% surge pool.

    Inputs:
      prelude_outputs = {
          "narrative_event": {"verdict": <one of NARRATIVE_*_VERDICTS>, ...},
          "alt_data_verification": {
              "verdict": <one of {narrative_corroborated, narrative_contradicted,
                                  weakly_contradicted, insufficient_evidence_to_verify}>,
              "expected_blocks_absent": list[str],
              ...
          },
      }
    Returns: (score, components_dict, threshold_band)
      threshold_band ∈ {mandatory_short, strong_tilt, discretion, mandatory_no_trade}
    """
    components: dict[str, float] = {}

    ne = (prelude_outputs.get("narrative_event") or {})
    ne_verdict = (ne.get("verdict") or "").strip().lower()
    if ne_verdict in NARRATIVE_NEGATIVE_VERDICTS:
        components["narrative_event_negative"] = SCORE_NARRATIVE_NEGATIVE
    elif ne_verdict in NARRATIVE_POSITIVE_VERDICTS:
        components["narrative_event_positive"] = SCORE_NARRATIVE_POSITIVE

    av = (prelude_outputs.get("alt_data_verification") or {})
    av_verdict = (av.get("verdict") or "").strip().lower()
    if av_verdict == "narrative_contradicted":
        components["alt_data_contradicted"] = SCORE_ALT_CONTRADICTED
    elif av_verdict == "weakly_contradicted":
        components["alt_data_weakly_contradicted"] = SCORE_ALT_WEAK_CONTRA
    elif av_verdict == "narrative_corroborated":
        components["alt_data_corroborated"] = SCORE_ALT_CORROBORATED

    expected_absent = av.get("expected_blocks_absent") or []
    if isinstance(expected_absent, list) and expected_absent:
        absent_bonus = min(
            SCORE_BLOCKS_ABSENT_PER * len(expected_absent),
            SCORE_BLOCKS_ABSENT_CAP,
        )
        components["expected_blocks_absent_bonus"] = float(absent_bonus)

    score = float(sum(components.values()))

    if score > 5:
        band = "mandatory_short"
    elif 2 <= score <= 5:
        band = "strong_tilt"
    elif 0 <= score <= 1:
        band = "discretion"
    else:  # score <= -1
        band = "mandatory_no_trade"
    return score, components, band


# =============================================================
# 2. Hard-coded baseline exclusions
# =============================================================

def baseline_exclusion(candidate_meta: dict) -> dict:
    """Check whether a candidate is on the hard-coded baseline-exclusion list.

    Inputs (best-effort, all optional in the candidate metadata):
      - is_confirmed_acquisition: bool
      - acquisition_arbitrage_realistic: bool   (only used if confirmed=True)
      - days_since_ipo: int | None
      - is_fully_fda_approved_marketed_drug_event: bool
      - network_effect_evidence: str | None     (one of: strong | partial | weak | none)

    Returns:
      {
        "blocked": bool,
        "exclusion_id": str | None,
        "reason": str,
      }

    These are the ONLY hard-coded "do not short" rules in the system.
    Block applies to NEW short entry only — existing positions follow
    agentic exit rules (RULES.md §2.6, §2.13).
    Everything else (Phase 1/2 trial results, breakthrough therapy
    designation, AI pivots, crypto pivots, meme squeezes, delisting
    relief, etc.) is delegated to the agent.
    """
    if candidate_meta.get("is_confirmed_acquisition") and \
       not candidate_meta.get("acquisition_arbitrage_realistic", False):
        return {
            "blocked": True,
            "exclusion_id": "confirmed_acquisition_no_arb",
            "reason": "Confirmed M&A with no realistic short-arbitrage opportunity.",
        }

    days_since_ipo = candidate_meta.get("days_since_ipo")
    if isinstance(days_since_ipo, int) and 0 <= days_since_ipo <= 30:
        return {
            "blocked": True,
            "exclusion_id": "recent_ipo_30d",
            "reason": f"Recent IPO (days_since_ipo={days_since_ipo}).",
        }

    if candidate_meta.get("is_fully_fda_approved_marketed_drug_event"):
        return {
            "blocked": True,
            "exclusion_id": "fully_fda_approved_marketed_drug",
            "reason": "Full FDA approval for a marketed drug. Phase 1/2/breakthrough events are NOT covered here.",
        }

    network_effect = (candidate_meta.get("network_effect_evidence") or "").lower()
    if network_effect in {"strong", "partial"}:
        return {
            "blocked": True,
            "exclusion_id": "network_effect_company",
            "reason": (
                f"Network-effect business (network_effect_evidence={network_effect}). "
                "Reversion prior is unreliable for compounding network-effect names."
            ),
        }

    return {"blocked": False, "exclusion_id": None, "reason": "No baseline exclusion."}


# =============================================================
# 3. Position sizing — Step C v0.6 pyramid:
#    0.5% initial / +0.5% per +100% from ORIGINAL entry / 5% per-position
#    cap / 10% sleeve cap
# =============================================================

INITIAL_POSITION_PCT  = 0.5    # Step C v0.6 (was 1.0 in v0.4)
ADD_POSITION_PCT      = 0.5    # Step C v0.6 (was 1.0 in v0.4)
ADD_TRIGGER_MULTIPLE  = 2.0    # add when current ≥ original_entry × (1 + 100% × n)
MAX_POSITION_PCT      = 5.0    # Step C v0.6 single-position cap
MAX_SLEEVE_PCT        = 10.0   # unchanged sleeve cap


def compute_initial_short_size(portfolio_value: float) -> dict:
    """Initial short position. 0.5% of portfolio (Step C v0.6)."""
    pct = INITIAL_POSITION_PCT
    return {
        "position_pct": pct,
        "position_dollars": portfolio_value * pct / 100.0,
        "rule": f"initial short = {pct:.1f}% of portfolio (Step C v0.6)",
    }


def next_add_trigger_price(last_add_price: float) -> float:
    """Pass 8 §4.8 — next add when current price >= last_add_price * 2.0.

    User explicit authorization 2026-05-04 changed the pyramid trigger from
    "original_entry × (n+2)" to "last_add_price × 2.0". Example: entry @ $3
    → first add @ $6 → second add @ $12 → third add @ $24.

    last_add_price = entry_price on initial entry; updated to add_execution_price
    on each subsequent add. Position state must persist last_add_price across
    triggers.
    """
    return last_add_price * 2.0


def compute_add_eligibility(
    *,
    current_price: float,
    last_add_price: float,
    cumulative_position_pct: float,
    sleeve_exposure_pct: float,
    agent_short_thesis_status: str,
    agent_confidence: str,
) -> dict:
    """Pass 8 §4.8 — next +0.5% add when current_price >= last_add_price * 2.0.

    Hard rules (this module):
      - cumulative_position_pct + ADD_POSITION_PCT must be ≤ MAX_POSITION_PCT (5%)
      - sleeve_exposure_pct + ADD_POSITION_PCT must be ≤ MAX_SLEEVE_PCT (10%)
      - current_price ≥ last_add_price * 2.0

    Agent-decided inputs (this module trusts them, does not generate them):
      - short_thesis_status must be "valid"
      - confidence must be "high"

    NOTE: Pass 8 also requires §32 score still supports bear thesis at the
    add evaluation. That check is performed by the caller (runner.py) using
    the most recent §33 SHORT_CONVICTION_SCORE; this module does not have
    access to prelude outputs, so it does NOT enforce the §32 score gate
    here. The runner is responsible.
    """
    trigger = next_add_trigger_price(last_add_price)
    if cumulative_position_pct + ADD_POSITION_PCT > MAX_POSITION_PCT:
        return {
            "add_allowed": False,
            "reason": (
                f"Per-position cap reached: cumulative {cumulative_position_pct:.1f}% "
                f"+ add {ADD_POSITION_PCT:.1f}% > cap {MAX_POSITION_PCT:.1f}%."
            ),
            "next_trigger_price": trigger,
        }
    if sleeve_exposure_pct + ADD_POSITION_PCT > MAX_SLEEVE_PCT:
        return {
            "add_allowed": False,
            "reason": (
                f"Sleeve cap reached: sleeve {sleeve_exposure_pct:.1f}% + add "
                f"{ADD_POSITION_PCT:.1f}% > cap {MAX_SLEEVE_PCT:.1f}%."
            ),
            "next_trigger_price": trigger,
        }
    if current_price < trigger:
        return {
            "add_allowed": False,
            "reason": (
                f"Add trigger not reached. Current ${current_price:.2f} < trigger "
                f"${trigger:.2f} (= last_add_price ${last_add_price:.2f} × 2.0)."
            ),
            "next_trigger_price": trigger,
        }
    if agent_short_thesis_status != "valid":
        return {
            "add_allowed": False,
            "reason": f"Agent thesis status '{agent_short_thesis_status}' is not 'valid'.",
            "next_trigger_price": trigger,
        }
    if agent_confidence != "high":
        return {
            "add_allowed": False,
            "reason": f"Agent confidence '{agent_confidence}' is not 'high'.",
            "next_trigger_price": trigger,
        }
    # On a successful add, the new last_add_price becomes the current price;
    # the next trigger is therefore current_price * 2.0.
    return {
        "add_allowed": True,
        "reason": (
            f"All conditions met. Add +{ADD_POSITION_PCT:.1f}%; "
            f"new position = {cumulative_position_pct + ADD_POSITION_PCT:.1f}%; "
            f"new sleeve = {sleeve_exposure_pct + ADD_POSITION_PCT:.1f}%."
        ),
        "next_trigger_price": current_price * 2.0,
    }


# =============================================================
# 4. Position review / exit — agent-driven, with hard guardrails
#    NO mechanical P&L stop.
# =============================================================

def assemble_position_review(
    *,
    agent_short_thesis_status: str,
    agent_confidence: str,
    days_held: int,
    has_declined: bool,
    sleeve_exposure_pct: float,
    confirmed_baseline_exclusion_event_emerged: bool,
    missing_data_persists: bool,
) -> dict:
    """Decide hold / review / exit for an open surge-short position.

    Hard guardrails (this module):
      - confirmed real-value baseline-exclusion event after entry → exit
      - sleeve cap or persistent missing data → exit
      - 15-trading-day rule: no decline + agent says thesis no longer
        supported → exit

    Agent-driven (this module consumes, does not generate):
      - agent_short_thesis_status: "valid" | "invalidated" | "uncertain" | "needs_monitoring"
      - agent_confidence: "high" | "medium" | "low"
    """
    if confirmed_baseline_exclusion_event_emerged:
        return {
            "level": "critical",
            "action": "exit_position",
            "reason": "Confirmed baseline-exclusion event emerged AFTER entry "
                      "(e.g. acquisition announced, full FDA approval granted).",
        }
    if sleeve_exposure_pct > MAX_SLEEVE_PCT or missing_data_persists:
        return {
            "level": "critical",
            "action": "exit_position",
            "reason": "Hard risk limit breached (sleeve cap or missing-data tolerance).",
        }
    if agent_short_thesis_status == "invalidated":
        return {
            "level": "critical",
            "action": "exit_position",
            "reason": "Agent classified short thesis as INVALIDATED.",
        }
    if days_held >= 15 and not has_declined and agent_short_thesis_status != "valid":
        return {
            "level": "critical",
            "action": "exit_position",
            "reason": (
                f"15+ trading days held ({days_held}) without decline AND agent "
                f"thesis status is '{agent_short_thesis_status}'."
            ),
        }
    if agent_short_thesis_status == "uncertain" or agent_confidence == "low":
        return {
            "level": "elevated",
            "action": "review_and_consider_reduce",
            "reason": (
                f"Agent thesis status '{agent_short_thesis_status}' / "
                f"confidence '{agent_confidence}' — review thesis."
            ),
        }
    if agent_short_thesis_status == "needs_monitoring":
        return {
            "level": "warning",
            "action": "review_thesis",
            "reason": "Agent flagged 'needs_monitoring' — track new evidence.",
        }
    return {
        "level": "normal",
        "action": "hold",
        "reason": "Thesis valid, no hard limits breached, no exit triggers.",
    }
