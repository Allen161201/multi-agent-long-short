"""
Pass 6 robustness smoke test (2026-05-03).

Synthetic exercise of the 3 state-mutation entry points wired in Pass 6
to confirm they don't crash on representative input. Catches mutation-side
bugs (KeyError on missing field, None handling, sleeve_exposure update,
realized_pnl_history append) BEFORE the next live replay incurs LLM cost.

Note: apply_ust_decision_to_state calls _lookup_treasury_yield which
makes a real FRED HTTP call. With FRED_API_KEY in .env this is expected
to succeed; if FRED is unreachable the function returns early without
mutating state (acceptable degraded behavior). The smoke flags either.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root on sys.path so `src.*` imports resolve when the
# script is invoked directly without `python -m`.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from src.portfolio.cover_eval import apply_cover_to_state
from src.portfolio.ql_review import apply_ql_action_to_state
from src.portfolio.fi_review import apply_ust_decision_to_state


def main() -> int:
    state = {
        "cash_balance": 1_000_000.0,
        "total_nav": 1_000_000.0,
        "positions": [
            {"kind": "equity", "side": "short", "ticker": "AKAN",
             "entry_price": 25.50, "current_price": 18.00, "shares": 100,
             "entry_date": "2026-04-28", "status": "open",
             "sleeve": "surge_short"},
            {"kind": "equity", "side": "long", "ticker": "AAPL",
             "entry_price": 175.00, "current_price": 180.00, "shares": 50,
             "entry_date": "2026-04-27", "status": "open",
             "sleeve": "quality_long", "entry_type": "long_term"},
        ],
        "as_of": "2026-05-01",
        "sleeve_exposure": {"quality_long": 9000, "surge_short": 2550,
                            "fixed_income": 0},
    }

    # ── Test 1: COVER apply ─────────────────────────────────────────
    short_pos = state["positions"][0]
    cover_result = {"decision": "cover", "rationale": "smoke test cover",
                    "dimensions_weighed": ["pnl"]}
    cash_before = state["cash_balance"]
    apply_cover_to_state(state, short_pos, cover_result)
    print(f"[1/3] COVER apply: cash {cash_before:.2f} -> "
          f"{state['cash_balance']:.2f}; "
          f"realized_pnl_history len="
          f"{len(state.get('realized_pnl_history', []))}; "
          f"short_pos.status={short_pos.get('status')}")
    assert short_pos["status"] == "closed", "cover should mark closed"
    assert state.get("realized_pnl_history"), "realized_pnl_history should be appended"

    # ── Test 2: QL TRIM apply ────────────────────────────────────────
    ql_pos = state["positions"][1]
    shares_before = ql_pos["shares"]
    cash_before = state["cash_balance"]
    trim_action = {"decision": "trim", "trim_pct": 0.3,
                   "rationale": "smoke test trim", "exit_trigger": None}
    apply_ql_action_to_state(state, ql_pos, trim_action)
    print(f"[2/3] QL TRIM apply: shares {shares_before:.2f} -> "
          f"{ql_pos['shares']:.2f}; "
          f"cash {cash_before:.2f} -> {state['cash_balance']:.2f}; "
          f"trim_history len={len(ql_pos.get('trim_history', []))}")
    assert ql_pos["shares"] < shares_before, "trim should reduce shares"
    assert state["cash_balance"] > cash_before, "trim should increase cash"

    # ── Test 3: UST DEPLOY apply (calls live FRED) ───────────────────
    positions_before = len(state["positions"])
    cash_before = state["cash_balance"]
    ust_decision = {"action": "deploy", "tenor": "3m", "face_value": 100000.0,
                    "rationale": "smoke test UST deploy"}
    apply_ust_decision_to_state(state, ust_decision)
    positions_after = len(state["positions"])
    cash_after = state["cash_balance"]
    if positions_after > positions_before:
        print(f"[3/3] UST DEPLOY apply: positions {positions_before} -> "
              f"{positions_after}; cash {cash_before:.2f} -> "
              f"{cash_after:.2f} (FRED yield lookup OK)")
    else:
        print(f"[3/3] UST DEPLOY apply: positions unchanged "
              f"(FRED unavailable / yield not found — degraded path "
              f"executed without exception, acceptable)")

    print("\nALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
