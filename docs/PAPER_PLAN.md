# Paper Plan — Thesis + 3 Killer Charts

**Status**: DRAFT — Step C Decision 10 codification, frozen 2026-04-28.
**Audience**: FIN 580 final paper readers (instructor + classmates).
**Length target**: 8–12 pages incl. charts.

---

## 1. Thesis (one paragraph)

> **Alternative-data agentic long-short outperforms naive sleeves precisely
> because it constrains the agent's decision domain rather than expanding it.**
> The system separates GUARDRAILS (hard-coded rule engine: surge screen,
> baseline exclusions, allocation caps, T+1 execution) from JUDGMENT
> (LLM agents: catalyst meaning, valuation, thesis validity, exit timing).
> The result is a strategy that is reproducible (byte-identical evidence
> packets at fixed `decision_timestamp`), defensible (audit trail per
> §19.7) and resilient to information pollution (§10 anti-pollution
> defense). A 5-regime maximum-cap allocation policy with profit
> reinvestment from realized short P&L expresses the user's >2-year
> real-money calibration; the surge-short pyramid sizing (0.5% / +0.5% /
> 5% per-position / 10% sleeve, no margin) makes a 1000% squeeze
> survivable by construction. The agent's role is to refuse trades when
> evidence is insufficient — not to find more reasons to act.

**Falsifiable claim**: against a 4-regime fixed-target baseline (v0.5)
and a flat 80/20 baseline, the v0.6 5-regime maximum-cap policy with
agentic discretion produces (a) lower max drawdown in Overheat-labeled
periods and (b) higher capture in Crisis-labeled periods, with overall
Sharpe ≥ either baseline over the 2018–2024 backtest window.

---

## 2. Three Killer Charts

### Chart 1 — Regime-Conditional Equity Cap vs Realized Equity Allocation

**X-axis**: monthly date, 2018-01 through 2024-12.
**Y-axis (left)**: equity allocation %. Two lines:
  - solid: actual equity allocation as a % of portfolio (agent decision)
  - dashed: regime cap from §4.7 (5-regime maximum table)
**Y-axis (right, secondary panel below)**: macro regime label per
`src/agents/macro_regime.py` 5-label v0.6 spec, color-banded (Crisis/Poor
/Normal/Strengthening/Overheat).

**What it proves**: The agent USES discretion. In Overheat periods the
solid line sits well below the 20% cap (cash residual), confirming that
caps are upper bounds, not fixed targets. In Crisis periods the solid
line approaches but does not always touch the 70% cap, reflecting
quality-availability constraints.

**Counter-test**: A flat-allocation baseline that always sits at the cap
should underperform during Overheat (over-exposure to late-cycle beta).

---

### Chart 2 — Surge-Short Pyramid Capacity Curve (1000% Stress Test)

**X-axis**: simulated price multiple from entry (1× to 50×).
**Y-axis (left)**: cumulative position size as % of portfolio.
**Y-axis (right)**: marked horizontal lines at 5% per-position cap and
10% sleeve cap.

Two overlays:
  - **v0.4 ladder** (1% initial / +1% per +100% from prior level / 10% cap)
  - **v0.6 pyramid** (0.5% initial / +0.5% per +100% from ORIGINAL / 5% cap)

**What it proves**: At a 32× squeeze (5 successive +100% moves from
original entry under v0.6), the v0.6 position is at exactly 3.0%
(0.5 + 5 × 0.5), still under the 5% cap. Under v0.4 the same path —
1×, 2×, 4×, 8×, 16× from prior level — would have sized to 5% across
just 4 levels and 6% by level 5. v0.6 is structurally more conservative
while preserving meaningful upside if the squeeze drags the position
higher.

**Counter-test**: Plot the dollar P&L per simulated path; v0.6's lower
notional means lower realized profit per reversion BUT also markedly
lower drawdown if the reversion takes longer than expected.

---

### Chart 3 — Information-Integrity Defense in Action

**X-axis**: ticker × `decision_timestamp` pair (small panel, e.g. 6 case
studies of high-attention surges).
**Y-axis**: stacked bar showing source contribution by tier:
  - T1 (SEC filings) — green
  - T2 (GitHub commits) — blue
  - T3 (Wikipedia pageviews) — yellow
  - T4 (FMP institutional news) — orange
  - T5 (social — synthetic / blocked) — red

Overlaid as a horizontal hash mark: agent's `recommendation_to_pm`
(short / no_trade / needs_more_evidence / pollution_risk_high).

**What it proves**: Names with T3-only or T4-only attention spikes
(yellow + orange tall, green + blue absent) trip the §10 anti-pollution
defense and resolve to `needs_more_evidence` or `pollution_risk_high`.
Names with green + blue corroboration (real SEC events with concurrent
GitHub activity) are the ones the system actually shorts. The chart
frames the defensive posture as a SHAPE — the system's decisions
correlate with the source-mix tier composition, not raw attention
volume.

**Counter-test**: A naive "high-attention = shortable" baseline would
size into the T3-only spikes and incur the simulated coordinated-attack
loss documented in §10.13 (R-INTEGRITY-POLLUTE-01).

---

## 3. What the paper does NOT claim

- Out-of-sample superiority over GS / Citadel / Renaissance equivalents.
  The strategy is calibrated to a single retail account's >2-year
  history; cross-account robustness is unproven.
- That the LLM agents always make better decisions than a hand-coded
  ruleset. The thesis is that the AGENT + RULE-ENGINE COMBINATION beats
  either alone, by leveraging structured guardrails AND contextual
  judgment.
- That the 15% borrow cost assumption is conservative for ALL
  hard-to-borrow names. The ~3-day median holding period makes most
  borrow-cost variation immaterial; the paper acknowledges 5–1000%
  empirical range as a robustness consideration in §5.16.

---

## 4. Backtest scope and limitations

- Window: 2018-01-01 through 2024-12-31 (7 years).
- Universe: §3.7 quality-long pool + §2.1 surge-short candidate pool.
- Costs: §5.16 model (15% borrow daily + tiered transaction bps).
- Anti-hindsight: §8 R1–R7 enforced; replay raises if universe-PIT
  filter not applied (§8.R5).
- Excluded from performance summary: any decision row with
  `data_after_cutoff_used=true` or `lookahead_safe=false` (§5.4).
- Known gap: borrow availability not modeled (assume free unlimited
  borrow conditional on the 15% rate).

---

## 5. Reproducibility checklist

- [ ] Frozen rule version: `v0.6_agentic_allocation_5regime_stepc`
      (config/frozen_rules_v0.6_agentic_allocation_5regime.yaml)
- [ ] Frozen agent prompt version: `v1.1_2026_04_28`
- [ ] Backtest evidence-packet hashes published alongside results
- [ ] Regression matrix (`tests/test_regression_matrix.py`) green
      against the documented baseline hash
- [ ] All three charts rebuilt from the published evidence-packet
      log (no spreadsheet hand-massaging)

---

**SOURCE**: User directive 2026-04-28 (Step C Decision 10).
**STATUS**: DRAFT — to be revised during paper writing; structure frozen.
