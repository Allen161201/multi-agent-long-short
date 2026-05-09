"""
Friday FI review — §27.16 weekly UST deployment evaluation (Pass 6, 2026-05-03).

PM-driven UST deployment based on macro_regime + FRED yield curve + portfolio
state. Reference: §27.14 (default 100% cash), §27.15 (PM owns FI decision),
§27.16 (Friday cadence), §30.4 (QL trim cash available next Friday).

Used by `scripts/portfolio_5day_*.py` daily loop on Fridays. The new runner
entry point `run_pm_for_fi_review` wraps a single PM call with
candidate_type="fi_review".
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime
from typing import Any

from src.agents.runner import run_pm_for_fi_review
from src.portfolio.ust_position import (
    ALLOWED_TENORS,
    UST_FACE_VALUE_USD,
    UST_TENOR_DAYS,
    build_ust_position,
    compute_ust_pv,
)


def run_fi_review(
    *,
    today: date,
    portfolio_state: dict,
    macro_regime: dict | None,
    live_adapters: Any = None,
    rule_version: str = "v0.9.0_pass8_hardrule",
    provider=None,
    cache=None,
) -> dict:
    """
    Generate the FI-review packet, call PM with candidate_type='fi_review',
    parse PM output into 0+ UST deployment actions.

    Returns:
        {
          "ust_decisions": [
            {"action": "deploy"|"rebalance"|"hold",
             "tenor": "1m"|...|"30y",
             "face_value": float,
             "rationale": str},
            ...
          ],
          "pm_rationale": str,
          "cost_usd": float,
          "pm_decision_raw": dict,
        }
    """
    packet = _build_fi_review_packet(
        today=today,
        portfolio_state=portfolio_state,
        macro_regime=macro_regime,
        rule_version=rule_version,
    )

    pm_record = run_pm_for_fi_review(
        evidence_packet=packet,
        provider=provider,
        cache=cache,
    )
    pm_parsed = pm_record.get("parsed_output") or pm_record.get("parsed") or {}
    ust_actions = _extract_ust_actions(pm_parsed)

    # Pass 6 robustness 2026-05-03: persist FI review forensic so the next
    # replay's decisions_count=0 outcome can be diagnosed (PM substantive
    # hold vs parser miss vs PM-emitted-under-different-key). Without this,
    # the prior replay's FI no-deploy was undebuggable. Lazy-import OUT_ROOT
    # to avoid circular dep with the replay script.
    try:
        from pathlib import Path
        forensic_root = Path("data/decisions/replay_5day") / today.isoformat()
        forensic_root.mkdir(parents=True, exist_ok=True)
        forensic = {
            "kind": "friday_fi_review",
            "as_of": today.isoformat(),
            "rule_version": rule_version,
            "ust_actions_extracted": ust_actions,
            "extraction_decisions_count": len(ust_actions),
            "pm_decision_raw": pm_parsed,
            "pm_packet_envelope": packet.get("envelope"),
            "yield_curve_summary": {
                "points_count": len(packet.get("fred_yield_curve", {}).get("points", [])),
                "shape": packet.get("fred_yield_curve", {}).get("curve_shape"),
            },
            "portfolio_state_summary": packet.get("portfolio_state_summary"),
        }
        forensic_path = forensic_root / "FI_REVIEW.json"
        forensic_path.write_text(
            json.dumps(forensic, indent=2, default=str)
        )
    except Exception:
        # Forensic write is non-critical; do NOT crash the FI review on
        # filesystem hiccup. Replay's other diagnostics still capture
        # ust_decisions count via the events log.
        pass

    return {
        "ust_decisions": ust_actions,
        "pm_rationale": (pm_parsed.get("reason")
                         or pm_parsed.get("decision_rationale")
                         or ""),
        "cost_usd": float(pm_record.get("__cost_usd__", 0.0) or 0.0),
        "pm_decision_raw": pm_parsed,
    }


def _build_fi_review_packet(
    *,
    today: date,
    portfolio_state: dict,
    macro_regime: dict | None,
    rule_version: str,
) -> dict:
    """
    Synthesize a minimal evidence-packet-shaped dict for FI review.

    The PM agent in FI REVIEW MODE expects:
      - portfolio_state_summary
      - macro_regime
      - FRED yield curve at all 8 tenors

    We build a packet that has the standard envelope shape plus these
    review-specific fields. The yield curve is fetched lazily here so the
    module can import without requiring a FRED key in test environments;
    on actual run the FRED adapter is invoked.
    """
    decision_ts = today.isoformat() + "T15:30:00-04:00"
    yield_curve = _fetch_yield_curve_safe(today)

    summary = {
        "cash_balance": float(portfolio_state.get("cash_balance",
                              portfolio_state.get("cash", 0.0))),
        "total_nav": float(portfolio_state.get("total_nav", 0.0)),
        "fixed_income_exposure": float(
            (portfolio_state.get("sleeve_exposure") or {}).get(
                "fixed_income", 0.0)
        ),
        "ust_positions": [
            p for p in portfolio_state.get("positions", []) or []
            if (p.get("kind") == "ust"
                or (p.get("sleeve") == "fixed_income"
                    and p.get("tenor") in ALLOWED_TENORS))
        ],
        # B5 + §31 v2 (2026-05-07): surface §31 SPY drawdown trigger state
        # into the FI packet so PM can reference it when sizing UST. v2
        # schema: trigger_history.events is the forensic log; trigger
        # active flag is the harness-level state['section_31_trigger_active_today'].
        "trigger_history": portfolio_state.get("trigger_history"),
        "section_31_trigger_active_today": bool(
            portfolio_state.get("section_31_trigger_active_today", False)
        ),
        "section_31_drawdown_pct_today": float(
            portfolio_state.get("section_31_drawdown_pct_today", 0.0) or 0.0
        ),
        # B5 fix (2026-05-06): surface realized weekly P&L for §4.9 awareness.
        # The harness Friday loop populates this on portfolio_state if §4.9
        # wiring is active; absent otherwise.
        "realized_weekly_short_pnl_usd":
            float(portfolio_state.get("realized_weekly_short_pnl_usd", 0.0)
                  or 0.0),
    }

    packet = {
        "envelope": {
            "schema_version": "evidence_packet_v1",
            "ticker": "__FI_REVIEW__",
            "decision_timestamp": decision_ts,
            "decision_mode": "live",
            "rule_version": rule_version,
            "evidence_packet_hash": _hash_review_packet(
                "fi_review", today, summary, macro_regime, yield_curve
            ),
        },
        "candidate_type": "fi_review",
        "review_context": {
            "mode": "fi_review",
            "as_of": today.isoformat(),
            "rule_version": rule_version,
        },
        "portfolio_state_summary": summary,
        "macro_regime": macro_regime or {"status": "data_unavailable"},
        "fred_yield_curve": yield_curve,
        "decision_time_discipline": {
            "status": "ok",
            "decision_mode": "live",
            "decision_timestamp": decision_ts,
            "allowed_data_cutoff": decision_ts,
            "data_after_cutoff_used": False,
            "lookahead_safe": True,
        },
    }
    return packet


def _fetch_yield_curve_safe(today: date) -> dict:
    """Best-effort FRED yield curve fetch; on failure return a minimal
    placeholder so the module remains importable in environments without
    FRED access."""
    try:
        from src.data_adapters.fred_adapter import get_yield_curve_data
        return get_yield_curve_data(decision_date=today.isoformat())
    except Exception as e:
        return {
            "status": "data_unavailable",
            "reason": f"{type(e).__name__}: {e}",
            "points": [],
            "shape": "insufficient_data",
        }


def _hash_review_packet(mode: str, today: date, *args) -> str:
    """Deterministic hash for cache_key uniqueness across review runs."""
    payload = json.dumps(
        {"mode": mode, "as_of": today.isoformat(), "args": _coerce(args)},
        sort_keys=True, default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _coerce(obj):
    if isinstance(obj, (list, tuple)):
        return [_coerce(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _coerce(v) for k, v in obj.items()}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def _extract_ust_actions(pm_parsed: dict) -> list[dict]:
    """PM emits ust_actions in decision_log or a free-text field. Try
    several locations defensively.

    Bug P fix (Pass 8 2026-05-04): PM emits tenor in uppercase ('1M',
    '3M', '1Y' …) per its FI block reasoning, but ALLOWED_TENORS in
    `src/portfolio/ust_position.py` is the lowercase tuple ('1m', '3m',
    '1y' …). Pre-fix the case-sensitive check silently dropped every PM
    deployment action, producing decisions_count=0 even when PM emitted
    5 deploy actions totaling $372K (XTLB 2026-05-03 forensic). Fix:
    normalize to lowercase before comparing, and rewrite action.tenor
    to the lowercase form so downstream consumers (apply_ust_decision_to_state)
    don't re-encounter the same case mismatch.
    """
    if not isinstance(pm_parsed, dict):
        return []

    def _normalize_tenor(action: dict) -> dict | None:
        if not isinstance(action, dict):
            return None
        if action.get("action") not in ("deploy", "rebalance", "hold"):
            return None
        tenor_raw = action.get("tenor", "")
        if not isinstance(tenor_raw, str):
            return None
        tenor_normalized = tenor_raw.strip().lower()
        if tenor_normalized not in ALLOWED_TENORS:
            return None
        out = dict(action)
        out["tenor"] = tenor_normalized
        return out

    for key in ("ust_actions", "ust_decisions"):
        v = pm_parsed.get(key)
        if isinstance(v, list):
            normalized = [n for n in (_normalize_tenor(a) for a in v) if n]
            if normalized:
                return normalized
    dl = pm_parsed.get("decision_log") or []
    if isinstance(dl, list):
        actions = []
        for entry in dl:
            n = _normalize_tenor(entry) if isinstance(entry, dict) else None
            if n:
                actions.append(n)
        if actions:
            return actions
    return []


def apply_ust_decision_to_state(state: dict, ust_decision: dict) -> None:
    """Mutate state in place: add a new UST position, decrease cash by PV."""
    action = ust_decision.get("action")
    if action != "deploy":
        return
    tenor = ust_decision.get("tenor")
    face_value = float(ust_decision.get("face_value") or UST_FACE_VALUE_USD)
    if tenor not in ALLOWED_TENORS:
        return
    yield_pct = _lookup_treasury_yield(tenor, state.get("as_of"))
    if yield_pct is None:
        return
    pos = build_ust_position(
        tenor=tenor,
        yield_pct=yield_pct,
        purchase_date=state.get("as_of") or datetime.now().date().isoformat(),
        face_value=face_value,
    )
    pv = float(pos.get("purchase_pv", compute_ust_pv(
        face_value, yield_pct, UST_TENOR_DAYS[tenor]
    )))
    pos["sleeve"] = "fixed_income"
    pos["kind"] = "ust"
    pos["status"] = "open"
    pos["entry_date"] = pos.get("purchase_date")
    pos["rationale"] = str(ust_decision.get("rationale", ""))[:500]
    state.setdefault("positions", []).append(pos)
    if "cash_balance" in state:
        state["cash_balance"] = float(state["cash_balance"]) - pv
    elif "cash" in state:
        state["cash"] = float(state["cash"]) - pv
    # §5.16 v2.15 (PART 4 wiring 2026-05-08): flat 15 bps tx cost on UST purchase.
    from src.portfolio.cost_helpers import apply_tx_cost
    ust_tx = apply_tx_cost(pv)
    if "cash_balance" in state:
        state["cash_balance"] = float(state["cash_balance"]) - ust_tx
    elif "cash" in state:
        state["cash"] = float(state["cash"]) - ust_tx
    state["tx_cost_today"] = float(state.get("tx_cost_today", 0.0)) + ust_tx
    state["tx_cost_cumulative"] = float(state.get("tx_cost_cumulative", 0.0)) + ust_tx
    pos["tx_cost_purchase_usd"] = ust_tx


def _lookup_treasury_yield(tenor: str, as_of: str | None) -> float | None:
    """Fetch yield-pct for a tenor from the FRED yield curve at as_of.

    Pass 6 robustness 2026-05-03: normalize case before comparison.
    FRED returns maturity labels in uppercase ('1M', '3M', '1Y', '10Y'...);
    UST_TENOR_DAYS keys are lowercase ('1m', '3m', '1y', '10y'...). The
    pre-fix loop never matched any tenor, silently returning None — which
    in turn caused apply_ust_decision_to_state to no-op even when the FI
    agent decided to deploy. Bug surfaced via the 2026-05-03 smoke test.
    """
    try:
        from src.data_adapters.fred_adapter import get_yield_curve_data
        yc = get_yield_curve_data(decision_date=(as_of or "")[:10] or None)
        tenor_lc = (tenor or "").lower()
        for pt in yc.get("points", []):
            mat = (pt.get("maturity", "") or "").lower()
            if mat == tenor_lc or mat.endswith(tenor_lc):
                return float(pt.get("yield_pct"))
    except Exception:
        return None
    return None


__all__ = [
    "run_fi_review",
    "apply_ust_decision_to_state",
]
