# SURGE_SHORT_THRESHOLDS.md

## Purpose
Operationalizes RULES.md §4.8 surge-short pyramid sizing with explicit numerical thresholds. Authored 2026-05-03 to satisfy §4.8 + the surge_short_agent prompt's TBD-threshold reference.

## §4.8 Fixed-Sizing Enforcement (binding 2026-05-03)

### Initial entry
- `initial_position_pct = 0.005` EXACTLY (0.5% of total portfolio)
- This is FIXED, not discretionary
- Defensive runtime clamp in `scripts/portfolio_5day_2026_04_27_to_05_01.py::update_position_from_trigger` enforces 0.005 for new surge_short entries regardless of agent emit
- PM prompt also enforces this mechanically (PM overrides any non-0.005 emit and documents the discrepancy)

### Add-on (pyramid)
- `allow_add = true` ONLY when ALL of the following hold:
  (a) underlying surged an additional ≥+100% from the ORIGINAL entry price
  (b) bear thesis still supported under §13.6 + §5.17 reasoning
  (c) cumulative position size after add-on ≤ 5% per-position cap
- Add-on size = +0.005 (another 0.5%) per trigger crossing
- `next_add_trigger_price = entry_price × 2.0` when allow_add becomes true (i.e., 100% above original entry)
- Maximum cumulative single-position size = 5% (10 add-ons max from initial)

### Hard caps (existing §4.8, unchanged)
- Single position ≤ 5% of portfolio (Risk hard veto above this)
- Surge-short total sleeve ≤ 10% of portfolio
- Margin: NEVER used

## Implementation
- `surge_short_agent.py` prompt: emit exactly `initial_position_pct=0.005` for new entries
- `pm_agent.py` prompt: validate PM-emitted `position_size_pct == 0.005` for new surge_short entries; mechanically override and document deviation
- `risk_agent.py`: §4.8 ceiling caps remain hard veto unchanged (5% per-position, 10% sleeve)
- `update_position_from_trigger` runtime clamp: `position_size_pct = 0.005` for new surge_short entries (defensive double-enforcement); add-ons use ladder math

## Status
ACTIVE 2026-05-03. Authored to satisfy §4.8 + the surge_short_agent prompt's TBD-threshold reference. Doc-only file (not in packet hash path).
