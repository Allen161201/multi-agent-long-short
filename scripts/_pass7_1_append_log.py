"""Append Pass 7.1 §24 to session log."""
from __future__ import annotations
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "data" / "decisions" / "today_session_log_2026_05_02.md"

APPEND = """

---

## §24. PASS 7.1 — ARCHITECTURAL_FACTS + preponderance + FI default-deviation (2026-05-03, prompt-only, no LLM, no replay)

### 24.1 Motivation — three issues from Pass 7 5-day replay aftermath
1. **UNOBSERVABLE distinction was too soft.** Pass 7 introduced the (a) UNOBSERVABLE / (b) MISSING-FROM-PACKET / (c) UNDEFINED RATIO distinction in pm_agent + risk_agent entry mode, but agents still cited UNOBSERVABLE-by-design data (borrow cost, shares-available-to-borrow, FOMC schedule, CME FedWatch, fmp_sentiment, sec_8k_fulltext body, 13F holdings outside FMP-plan-supported set) in evidence_missing / risk_notes / advisory_notes / cover_decision_rationale fields. The distinction was guidance, not prohibition. Hardening required: a hard-prohibition CLOSED-LIST shared block in every active LLM prompt.
2. **PM surge_short used a 2+/1/0 counting heuristic.** Pass 7's SURGE_SHORT ENTRY DECISION FRAMEWORK said "2+ bear-thesis sufficient conditions met -> SHORT, 1 -> watch, 0 -> no_trade". Counting qualitative dimensions and looking up a decision IS a quantitative threshold dressed up as reasoning, which violates §13.6.SOFT_REASONING. Replace with preponderance-of-evidence reasoning.
3. **PM Friday FI emitted 0 UST deployment with regime FI cap > 0.** Pass 7 added FI emit rules requiring decision/ust_actions consistency, but did not address the underlying default expectation. §27.14's "100% cash default" is a v0.7-era starting posture; under §4.7 the regime FI cap is positive (Crisis 30% / Poor 40% / Normal 60% / Strengthening 70% / Overheat 80%) precisely so idle cash earns curve carry. Need to explicitly invert: partial deployment is the DEFAULT; 0 deployment is the DEVIATION requiring concrete observable-rooted justification.

### 24.2 Three additive prompt-only fixes (all 9 active LLM agents bumped per §14.1 cache-key rule)

**Fix (a) — Shared ARCHITECTURAL_FACTS block (CLOSED LIST of 7 items)** inserted byte-identical near top of all 9 active LLM agent prompts:
- narrative_event_agent.py
- alt_data_verification_agent.py
- fundamental_agent.py
- network_effect_agent.py
- valuation_agent.py
- surge_short_agent.py
- quality_long_agent.py
- risk_agent.py
- pm_agent.py

The 7 items in the CLOSED list:
1. Borrow cost trajectory (fixed at 100% per §5.16 user directive)
2. Shares-available-to-borrow (no adapter, none planned)
3. sec_8k_fulltext body content (stub by design; sec_edgar INDEX is observable)
4. FOMC schedule (no adapter, paper limitation)
5. CME FedWatch / Fed funds futures (no adapter, paper limitation)
6. fmp_sentiment press-releases / news-sentiments-rss (FMP plan returns 404)
7. 13F holdings outside the FMP-plan-supported set (returns 404)

Hard prohibitions: items 1-7 MUST NOT appear in evidence_missing / risk_notes / advisory_notes / cover_decision_rationale / audit_rationale / pm_rationale, neither as veto reasons nor as long/short signals nor as watch reasons. Items 1-7 must be TRANSPARENT to agent reasoning. Data NOT on this list is treated under §13.6.SOFT_REASONING + Pass 6 universal soft-veto (reasoning input, not veto trigger).

Insertion was via `scripts/_pass7_1_insert_architectural_facts.py` (idempotent — checks BLOCK_SENTINEL "ARCHITECTURAL FACTS — DATA THAT DOES NOT EXIST IN v0.8.7" before insertion). All 9 inserts succeeded.

**Fix (b) — Replace 2+/1/0 counting heuristic in pm_agent.py SURGE_SHORT ENTRY DECISION FRAMEWORK** with PREPONDERANCE-OF-EVIDENCE reasoning. Agent now reasons about WEIGHT and COHERENCE of bear thesis across prelude agents' rationale + sector-expected footprint + narrative-vs-evidence pattern. The bear thesis can be carried by a single overwhelming dimension OR by synthesis across multiple weaker signals pointing same direction. Decision documented as integrated synthesis, not a count.

**Fix (c) — Append FI DEPLOYMENT DEFAULT-DEVIATION REASONING block** to pm_agent.py FI REVIEW MODE section. Default expectation by regime (anchor, not hardcoded ladder): Crisis short-end / Poor short-to-mid / Normal balanced-up-to-cap / Strengthening mid / Overheat short-bias. 0 deployment with regime FI cap > 0 requires CONCRETE OBSERVABLE-ROOTED reason naming a specific FRED yield-curve / Fed-path-from-FRED / regime-classifier / HY-OAS / breakeven-inflation observation IN THE PACKET. Items 1-7 from ARCHITECTURAL FACTS list MUST NOT be cited as 0-deployment justification. Generic uncertainty MUST NOT be cited. Honest sizing-down is decision="deploy" with smaller face_value, not decision="hold". decision="hold" reserved for sleeve-already-at-cap case; decision="defer" reserved for packet-essential-block-missing case.

### 24.3 Cache-invalidation footprint (§14.1)
All 9 active LLM agent PROMPT_VERSIONs bumped to suffix `_2026_05_03_pass7_1_architectural_facts`:
- narrative_event_agent.py: `v0.6_pass6` -> `v0.7_2026_05_03_pass7_1_architectural_facts`
- alt_data_verification_agent.py: `v0.6_pass6` -> `v0.7_2026_05_03_pass7_1_architectural_facts`
- fundamental_agent.py: `v0.6_pass6` -> `v0.7_2026_05_03_pass7_1_architectural_facts`
- network_effect_agent.py: `v0.4_pass6` -> `v0.5_2026_05_03_pass7_1_architectural_facts`
- valuation_agent.py: `v0.3_pass6` -> `v0.4_2026_05_03_pass7_1_architectural_facts`
- surge_short_agent.py: `v1.3_pass6` -> `v1.4_2026_05_03_pass7_1_architectural_facts`
- quality_long_agent.py: `v1.2_2026_04_29` -> `v1.3_2026_05_03_pass7_1_architectural_facts`
- risk_agent.py: `v0.9_pass7` -> `v1.0_2026_05_03_pass7_1_architectural_facts`
- pm_agent.py: `v0.8_pass7` -> `v0.9_2026_05_03_pass7_1_architectural_facts`

Inactive prompts (fund_net_val_agent.py, risk_pm_agent.py, pm_flat_agent.py) not bumped — confirmed inactive via `_select_prelude` in src/agents/runner.py.

### 24.4 Backups
All 9 prompt files backed up to `*.bak_pre_pass7_1_20260503` before edits (project is not a git repo).

### 24.5 Verification (all green)
| Check | Expected | Observed |
|---|---|---|
| `tests/test_regression_matrix.py` packet hash | `04f45e2cbccdd0665...` unchanged | `04f45e2cbccdd0665...` ok |
| Schema-pass cells | 30/30 | 30/30 ok |
| Cache-hit-on-2nd cells | 30/30 | 30/30 ok (cache key invalidated correctly — first run miss, second hit) |
| `PROMPT_VERSION` suffix `pass7_1_architectural_facts` | 9 active prompts | 9 ok |
| `ARCHITECTURAL FACTS — DATA THAT DOES NOT EXIST` count | 9 (1 per file) | 9 ok |
| `Borrow cost trajectory` count | 9 (1 per file) | 9 ok |
| `2+ bear-thesis conditions met` in pm_agent.py | 0 | 0 ok (counting heuristic removed) |
| `preponderance` in pm_agent.py | >= 2 | 4 ok |
| `FI DEPLOYMENT DEFAULT-DEVIATION REASONING` in pm_agent.py | 1 | 1 ok |
| `macro_regime` references in pm_agent.py | preserved (5-regime classifier wiring untouched) | 2 occurrences ok |
| Example-leaked-into-code patterns (`missing.*github.*=.*short`, `no.*sec.*=.*signal`, `if.*architectural`) | 0 | 0 ok |
| AKAN dry-run block-status counts | 12 ok / 9 not_evaluated / 4 data_unavailable / 1 insufficient_evidence | exact match ok |

### 24.6 Cost / wall
$0 (prompt-only, no LLM, no replay). Verification ran against existing cached responses (regression matrix is byte-stable on the AAPL packet).

### 24.7 Files touched
- src/agents/prompts/narrative_event_agent.py (PROMPT_VERSION + ARCHITECTURAL_FACTS block)
- src/agents/prompts/alt_data_verification_agent.py (same)
- src/agents/prompts/fundamental_agent.py (same)
- src/agents/prompts/network_effect_agent.py (same)
- src/agents/prompts/valuation_agent.py (same)
- src/agents/prompts/surge_short_agent.py (same)
- src/agents/prompts/quality_long_agent.py (same)
- src/agents/prompts/risk_agent.py (same)
- src/agents/prompts/pm_agent.py (same + Step 4 preponderance replacement + Step 5 FI default-deviation append)
- scripts/_pass7_1_insert_architectural_facts.py (NEW — idempotent inserter helper)
- data/decisions/today_session_log_2026_05_02.md (this §24 append)

### 24.8 Carry-forward
- Authorize next 5-day replay to validate Pass 7.1 hypothesis (~$5 expected).
- Expected behavior shifts from Pass 7 baseline:
  - Surge_short PM should reason about preponderance, not count to 2+ before emitting short. CUE / LABT-class candidates with reverse-split + negative FCF + sector-aware narrative gap should now have a clearer path to "short" via preponderance synthesis even when some prelude dimensions are silent (silent != contradicting).
  - Risk + PM should NOT cite items 1-7 from CLOSED list anywhere. If audit grep on next replay's outputs surfaces phrases like "borrow cost unobservable" / "FOMC schedule unknown" / "Fed-path data unavailable" / "13F not retrieved" / "awaiting 8-K full text" / "sentiment data missing" / "shares-available-to-borrow not in packet" — that is a Pass 7.1 prohibition violation and warrants further tightening.
  - Friday FI PM should emit decision="deploy" with non-empty ust_actions in regimes where the FI cap is non-zero, unless a packet-rooted observable explicitly warrants 0 deployment. decision="hold" or decision="defer" with empty ust_actions on a regime with cap > 0 must cite a SPECIFIC FRED / regime / HY-OAS observation; generic "uncertain conditions" wording will be flagged.
- Backup retention: keep `*.bak_pre_pass7_1_20260503` until Pass 7.1 replay confirms no regression.
"""

text = LOG.read_text(encoding="utf-8")
if "## §24. PASS 7.1" in text:
    print("SKIP: §24 already present")
else:
    LOG.write_text(text + APPEND, encoding="utf-8")
    print(f"OK: appended {len(APPEND)} chars")
print(f"Final line count: {len(LOG.read_text(encoding='utf-8').splitlines())}")
