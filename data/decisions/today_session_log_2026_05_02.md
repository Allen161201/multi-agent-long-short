# Session work log — 2026-05-02

Comprehensive log of everything shipped today, organised in shipping order. The bitter-sweet wiring failure that opened the day is treated first because it shaped everything that came after.

---

## 0. Bitter-sweet wiring failure narrative — `presentation must-discuss; paper TBD`

Opened the day to investigate why the 5-day replay 2026-04-27..2026-05-01 (cost ≈ $15) produced **0 trades** — 4 surge_short candidates all vetoed (HTCO/AKAN/RDAC and one other), 0 quality_long triggers fired beyond watch/no_trade for AVB/ARE/BRO/CINF. The PM rationale across all 8 misses cited two recurring causes:

1. `information_integrity_assessment.use_as_primary_signal_allowed = false` (T1=0, T2=0, "pollution-defense unimplemented")
2. R7 null-block handling: `news_event_summary`, `filing_confirmation`, `alternative_data_features`, `sentiment_community_ownership_evidence` all reported empty / not_evaluated

**Root cause was a wiring failure, not a model failure.** Two specific bugs:

- **The 5 alt-data adapters were live** — `wikipedia_pageviews`, `sec_edgar`, `github_public`, OpenCLI `sec_8k_fulltext`, OpenCLI `github_commit_messages` (plus opt-in `fmp_sentiment`, `sec_form4`, `polygon_news`). The block builders for `information_integrity_assessment`, `sentiment_community_ownership_evidence`, `filing_confirmation`, `narrative_price_gap_assessment` were placeholder skeletons emitting `status="not_evaluated"` with `use_as_primary_signal_allowed=false` and no path to populate from manifest.
- **The replay script never passed `live_adapters=True`** to `generate_evidence_packet(...)`. Even if the block builders had been wired, the adapter overlay layer (`apply_adapters` in `src/evidence_packet/adapter_wiring.py`) only fires when `live_adapters` is non-None. So the adapters NEVER ran during replay; the agents always saw empty placeholders; the 8 miss/veto conclusions were dictated by the framework's fail-closed contract, not the agents' judgment.

Spent ~$15 + ~$0.97 verification (≈ $16 total today) before the audit caught the wiring gap. The adapters had been registered, tested, and verified live — but downstream consumers were not wired to read them.

**Why this is presentation-worthy:** AI-system wiring is fragile *because nothing lights up when a part fails silently*. Block placeholders that quietly emit `not_evaluated` don't break tests, don't fail packet validation, and don't trigger any monitoring — they just tell the LLM "no data here" and the LLM correctly fails closed. The audit-first methodology (Gate 1 audit before Group A/B/C work) is what saved the rest of the day from compounding the spend; without that 5-minute audit step, a Chunk-final-style multi-agent rerun would have looked exactly the same as the original replay. Worth documenting as a case study on AI plumbing failures in the paper, but the writing is TBD.

---

## 1. What shipped today (chunked timeline)

### Chunk A — §5.17 surge-short integrity-veto exception (additive)
- v0.8.4_borrow_cost_cover_cadence → **v0.8.5_surge_short_integrity_exception**
- Regression hash: `ea828dc9...` → `64f34919...`
- 9 src/ literals + yaml + replay script bumped
- risk_agent + pm_agent prompts bumped to `v0.4_2026_05_02_surge_short_exception`
- Standing Rule 4 user-approval recorded in chat 2026-05-02

### Chunk B — Pipeline split by candidate_type
- surge_short prelude (3 agents): narrative_event + alt_data_verify + fundamental → 6 agents total
- quality_long prelude (4 agents): narrative_event + alt_data_verify + network_effect + valuation → 7 agents total
- src/agents/runner.py: `PIPELINE_PRELUDE_SURGE`, `PIPELINE_PRELUDE_QUALITY`, `_select_prelude()`
- No rule_version impact; no hash impact

### Chunk C — Weekly QL event-driven trigger
- Monday-only `ql_weekly_scan(...)` in replay
- SP500 ∪ NDX100 from FMP `sp500-constituent` + `nasdaq-constituent`
- Earnings in [today, today+14], capped at 5
- HARD_COST_USD: 10 → 15 (raised for QL 7-agent budget)

### Chunk-final — Block-wiring finalizers + LIVE_ADAPTERS_TUPLE
- 4 finalizers added to `src/evidence_packet/adapter_wiring.py`:
  - `_finalize_filing_confirmation`
  - `_finalize_sentiment_ownership`
  - `_finalize_information_integrity` (governance-gate preserved: `use_as_primary_signal_allowed` stays FAIL-CLOSED)
  - `_finalize_narrative_price_gap`
- `LIVE_ADAPTERS_TUPLE` defined in replay script (9 sources)
- 8-ticker live-adapter coverage verify: 4 STRONG (ARE/AVB/BRO/CINF), 2 MODERATE (RDAC/AIOS), 2 WEAK (HTCO/AKAN — micro-caps inherently lacking T1 evidence)

### v0.8.6 — §13.6.SOFT_REASONING soft-reasoning framework (additive)
- v0.8.5 → **v0.8.6_soft_reasoning_framework**
- Regression hash: `64f34919...` → `760d0764...`
- 9 src/ literals + yaml + replay script bumped
- 5 prompts bumped: narrative_event/alt_data_verify/fundamental → `v0.4_..._soft_reasoning`, risk/pm → `v0.5_..._soft_reasoning`
- BIL §27 fix shipped (~45 LOC): `_bootstrap_bil_position()` + sleeve routing in `write_eod_state` (later SUPERSEDED by v0.8.7 §27.14)
- Verification on AVB QL + AKAN SS: AVB rationale shifted from R7-mechanical to substantive valuation reasoning (soft-reasoning IS active in PM); AKAN unchanged (Risk Agent still fires R7-class hard veto)

### v0.8.7 — Direct UST + dividend + expense ratio + Risk soft-veto extension (additive, this session)
- v0.8.6 → **v0.8.7_ust_fixed_income**
- Regression hash: `760d0764...` → **`6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095`** (locked 2026-05-02 in `tests/test_regression_matrix.py::EXPECTED_AAPL_PACKET_HASH`)
- 9 src/ literals + yaml + replay script bumped
- §27.10–§27.18 added (additive, §27.1–§27.9 marked SUPERSEDED_BY_§27.10+ with content preserved verbatim)
- New module `src/portfolio/ust_position.py` (~250 LOC): `UST_TENOR_DAYS`, `compute_ust_pv`, `accrue_ust_interest`, `mark_to_market_ust`, `build_ust_position`, `USTPosition` dataclass
- New module `src/portfolio/dividends_and_expenses.py` (~210 LOC): `accrue_dividends_for_today`, `accrue_etf_expense_drag_for_today`
- `src/data_adapters/fmp_adapter.py::get_company_profile` extended with `expense_ratio` field (None when FMP omits)
- `scripts/portfolio_5day_2026_04_27_to_05_01.py`: hard-exclude BIL/SHY/IEF/TLT/IEI/SHV in `rank_surge_candidates` + `load_quality_long_universe`; SUPERSEDED `_bootstrap_bil_position` call site (start state now §27.14 100% cash)
- `src/engine/pnl_backtest.py`: dividend + expense ratio hooks added in step 5b of `_process_one_day`
- risk_agent prompt bumped `v0.5_..._soft_reasoning` → `v0.6_2026_05_02_surge_short_softveto`; SOFT-REASONING EXTENSION FOR SURGE_SHORT clause added; FIXED INCOME SLEEVE VETO CONDITIONS section retagged SUPERSEDED by §27.10+

---

## 2. Step 1 audit findings (recorded for the paper)

| # | check | finding |
|---|---|---|
| (a) | FRED treasury data continuity | All 8 required maturities present in `outputs/fred_cache/` (1m/3m/6m/1y/2y/5y/10y/30y) plus bonus 7y/20y. Disk cache sparse (24 of 348 business days in 2025-01-01..2026-05-01 pre-populated) but `fred_adapter.py` fetches on demand. |
| (b) | FOMC meeting dates | NO adapter exists. **Paper limitation.** §27.15 PM reasoning must work without explicit FOMC schedule. |
| (c) | Fed-funds futures / CME FedWatch | NO adapter exists. **Paper limitation.** Fed-path read deferred to a future paper version. |
| (d) | Dividend handling | Zero matches in `pnl_backtest.py` and `scripts/portfolio_5day_*.py`. **Confirmed bug — fixed today via §27.17 + `dividends_and_expenses.py`.** |
| (e) | Expense ratio handling | Zero matches across `src/`. **Confirmed bug — fixed today via §27.18 + `dividends_and_expenses.py` + `fmp_adapter.get_company_profile` extension.** |

---

## 3. Hash history (today)

| version | hash | predecessor | drift driver |
|---|---|---|---|
| v0.8.4_borrow_cost_cover_cadence | `ea828dc9...` | (start of day) | — |
| v0.8.5_surge_short_integrity_exception | `64f34919b91ac686b66fa071cb18f39322cffe53313be00fc5ef4e3fd0381d61` | v0.8.4 | rule_version literal at envelope.rule_version + macro_regime.rule_version |
| v0.8.6_soft_reasoning_framework | `760d0764c6b1816df32a5a42f2b69508ad05382cffb57180a4fe288cd903bcdc` | v0.8.5 | rule_version literal (same 2 paths) |
| **v0.8.7_ust_fixed_income** | **`6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095`** | v0.8.6 | rule_version literal (same 2 paths) |

Diff verified clean at every bump: only the 2 rule_version-bound packet paths changed; no other content drift.

---

## 4. LOC counts (today)

| module | new / changed | LOC |
|---|---|---|
| `src/evidence_packet/adapter_wiring.py` | 4 finalizers + helper + tier map (Chunk-final) | ~262 |
| `scripts/portfolio_5day_2026_04_27_to_05_01.py` | Chunk B + C + BIL + UST exclusion + dividend/expense wiring | ~400 (cumulative since chunks A/B/C) |
| `src/agents/prompts/{narrative_event,alt_data_verify,fundamental,risk,pm}_agent.py` | SOFT_REASONING section + version bumps | ~16 LOC × 5 |
| `src/portfolio/ust_position.py` | NEW (Step 4) | ~250 |
| `src/portfolio/dividends_and_expenses.py` | NEW (Step 5) | ~210 |
| `src/engine/pnl_backtest.py` | Step 5b dividend/expense hooks | ~32 |
| `src/data_adapters/fmp_adapter.py` | `get_company_profile` expense_ratio field | ~5 |
| `src/agents/prompts/risk_agent.py` | SOFT-REASONING EXTENSION FOR SURGE_SHORT + FI veto SUPERSEDED note + version bump | ~15 |
| `docs/RULES.md` | §27.10–§27.18 (9 new rules) + §27.1–§27.9 SUPERSEDED tags + §0/§22/changelog | ~85 |

---

## 5. Pending issues (carry forward)

1. **FOMC meeting schedule adapter** — paper limitation; PM reasoning per §27.15 currently works without it. Add to a future paper version's data-source roadmap.
2. **Fed-funds futures / CME FedWatch adapter** — paper limitation; same as above.
3. **CUE missing from 2026-05-01 surge candidates (read-only diagnostic)** — `rank_surge_candidates` reads FMP-supplied `change_pct` field which is intraday open-to-close, NOT close-vs-prior-close. CUE crashed mid-day (open $33.75 → close $30.42, FMP `change_pct=-9.87%`) and was filtered out despite +106% close-to-close + +129% prior-close-to-open + +158% prior-close-to-high move. Fix is one-line (recompute `change_pct` against prior-day's close). NOT shipped today per user's "no edits" instruction on the diagnostic.
4. **AKAN-class veto behaviour under v0.8.7 Risk soft-veto** — needs LLM verify (single-ticker AKAN SS at 2026-04-28 09:30) to confirm Risk Agent now passes advisory-only on §5.13(b)/(e) when missing data is structurally consistent with bear thesis. Estimated cost ~$0.30. Skipped today per spec ("NO LLM call. NO replay.").
5. **Full 5-day replay rerun** — held pending #4 verification. Estimated cost ~$3 (with 9-source live tuple, ~$0.30/ticker × ~10 tickers).

---

## 6. Cost ledger (today)

| activity | cost |
|---|---|
| Original 5-day replay (0 trades, $15 spent — bitter-sweet) | ~$15.00 |
| AVB QL + AKAN SS soft-reasoning verification (Step 6 of v0.8.6) | ~$0.97 (incl. ~$0.36 wasted on first run with 9-source live tuple that exceeded 50k input cap) |
| Today's UST refactor (Steps 1-7, no LLM) | $0.00 |
| **TOTAL TODAY** | **~$16.00** |

---

## 7. Backups created today

Pre-edit baselines preserved:
- `docs/RULES.md.bak_pre_chunk_a_20260502` (pre Chunk A §5.17)
- `docs/RULES.md.bak_v1.7_pre_cleanup_20260502` (pre v2.0 cleanup pass)
- `config/rules.yaml.bak_pre_chunk_a_20260502`, `.bak_pre_v2_1_reconcile_20260502`
- `scripts/portfolio_5day_2026_04_27_to_05_01.py.bak_pre_chunk_c_20260502`, `.bak_pre_block_wiring_20260502`, `.bak_pre_bil_fix_20260502`
- `src/agents/macro_regime.py.bak_pre_chunk_a_20260502`
- `src/agents/prompts/{narrative_event,alt_data_verification,fundamental,pm,risk}_agent.py.bak_pre_soft_reasoning_20260502` (pre v0.8.6 soft-reasoning section + version bump)
- `src/agents/prompts/{pm,risk}_agent.py.bak_pre_chunk_a_20260502` (pre Chunk A §5.17 prompt bump)
- `src/agents/runner.py.bak_pre_chunk_a_20260502`, `.bak_pre_chunk_b_20260502`, `.bak_pre_pm_retry_20260502`
- `src/engine/pnl_backtest.py.bak_pre_chunk_a_20260502`
- `src/evidence_packet/adapter_wiring.py.bak_pre_block_wiring_20260502`
- `src/evidence_packet/blocks/{alt_data,integrity,macro,sentiment_ownership}.py.bak_pre_block_wiring_20260502`
- `src/evidence_packet/blocks/macro.py.bak_pre_chunk_a_20260502`
- `src/evidence_packet/generator.py.bak_pre_block_wiring_20260502`, `.bak_pre_chunk_a_20260502`
- `src/llm/deterministic_stub.py.bak_pre_chunk_a_20260502`
- `src/rules/logic_audit.py.bak_pre_chunk_a_20260502`
- `tests/test_regression_matrix.py.bak_pre_chunk_a_20260502`

Post-UST snapshots taken at end of session as recoverable points (these are POST-UST state, not pre-UST — true pre-UST state for `risk_agent.py` is in `risk_agent.py.bak_pre_soft_reasoning_20260502`):
- `scripts/portfolio_5day_2026_04_27_to_05_01.py.bak_pre_ust_refactor_20260502`
- `src/engine/pnl_backtest.py.bak_pre_ust_refactor_20260502`
- `src/agents/prompts/risk_agent.py.bak_pre_ust_refactor_20260502`
- `src/data_adapters/fmp_adapter.py.bak_pre_ust_refactor_20260502`
- `docs/RULES.md.bak_pre_ust_refactor_20260502`
- `tests/test_regression_matrix.py.bak_pre_ust_refactor_20260502`
- `config/rules.yaml.bak_pre_ust_refactor_20260502`

---

## 8. MUST-FIX 1 RANKING FORMULA FIX (2026-05-03)

### Bug
`scripts/portfolio_5day_2026_04_27_to_05_01.py::rank_surge_candidates` was reading `row.get("change_pct")`, which is the FMP-supplied **intraday open-to-close** delta — NOT the prior-close-to-current-day metric the user's surge-short strategy depends on. Catches:

- CUE 2026-05-01: open $33.75 from $14.74 prior_close = **+128.97% gap-up**, but crashed intraday to $30.42 close → FMP `change_pct = -9.87%`. Failed the ≥50% gate under the OLD formula. The user actually shorted CUE per real strategy.
- LABT 2026-05-01: open $4.22 from $2.66 prior_close = +58.65% gap, intraday `change_pct = +32.13%` → also missed.

The strategy targets gap-up-then-crash patterns where short alpha is highest. Filtering on intraday open-to-close inverts the signal.

### Fix
1-line replacement (with surrounding edge-case handling). OLD:
```python
change_pct = float(row.get("change_pct") or 0.0)
```
NEW:
```python
change_pct = (today_open - prior_close) / prior_close * 100.0
```
where `today_open = today_row["open"]` and `prior_close = rows[today_idx - 1]["close"]` from the same range_key list, sorted by date. Edge cases:
- prior_close ≤ 0 / missing → skip ticker, log `data_quality` (kind="rank_surge_data_quality")
- today_open missing / non-numeric → skip ticker, log
- today_idx == 0 (first day of cache window) → skip ticker (no prior in cache), log `gap_calc_no_prior_in_window`

The §2.1 gate (≥50% / vol ≥1M / prior_close > $2) is preserved verbatim; it now applies against the real prior-close-to-today-open gap. The single-source `prior_close` is used in BOTH the ranking calc AND the §2.1 prior_close filter — no double fetch, no inconsistency.

### Hash verification
`python tests/test_regression_matrix.py` → **30/30 schema-pass, 30/30 cache-hit, hash stable across reruns. Hash UNCHANGED at v0.8.7 baseline `sha256:6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095`.** Confirms the ranking formula is purely script-level and never enters the evidence packet — the assumption in the task brief is correct.

### 5-day OLD vs NEW trigger inventory

| day | OLD #pass §2.1 | NEW #pass §2.1 | OLD top-5 | NEW top-5 |
|---|---:|---:|---|---|
| 2026-04-27 | 2 | 0 | HTCO, EDSA | (empty — first day of cache window, no prior in cache; `_fetch_full_universe_5day.py` would need to widen the window to fix) |
| 2026-04-28 | 1 | 0 | AKAN | (empty — AKAN was intraday surge open $11.29 → close $17.49, no morning gap from prior $11.29 close) |
| 2026-04-29 | 1 | 1 | RDAC | XTLB (gap +63.91% open $3.77 vs prior $2.30) |
| 2026-04-30 | 1 | 1 | AKAN | HCAI (gap +102.38% open $11.07 vs prior $5.47) |
| 2026-05-01 | 1 | 2 | AIOS | **CUE (+128.97%), LABT (+58.65%)** |

**Presence checks (2026-05-01):** CUE PASS ✓ LABT PASS ✓.

**Selection delta:** OLD and NEW pick disjoint sets across 4/29 / 4/30 / 5/1. OLD captures intraday-surge tickers; NEW captures gap-up tickers. These are two structurally different short-target archetypes. The NEW formula matches the user's stated strategy (gap-up-then-crash). Tickers like AIOS / AKAN / RDAC / HTCO that fired under OLD but not NEW remain available to a future intraday-surge sleeve if/when one is defined; they are NOT eligible under the gap-up surge_short strategy.

### Backup
`scripts/portfolio_5day_2026_04_27_to_05_01.py.bak_pre_ranking_fix_20260503` (pre-fix snapshot, 34,901 bytes).

### Pending next session
- **MUST-FIX 2/3 — AKAN-class soft-veto LLM verify** under v0.8.7 risk_agent prompt (`v0.6_2026_05_02_surge_short_softveto`). Single-ticker AKAN SS at 2026-04-28 09:30 ET to confirm Risk Agent now passes advisory-only on §5.13(b)/(e) when missing data is structurally consistent with bear thesis. Estimated cost ~$0.30. Skipped today per spec.
- After MUST-FIX 2/3 passes, full 5-day replay rerun ≈ $3 (with `live_adapters=True` 5-source default tuple, ~$0.30/ticker × ~10 tickers across XTLB / HCAI / CUE / LABT + QL events).
- Cache-window widening (re-run `_fetch_full_universe_5day.py` with start=2026-04-24 instead of 2026-04-27) would let MUST-FIX 1 surface a 2026-04-27 morning-gap candidate too. Currently 2026-04-27 ranking is empty under NEW formula due to cache-window limit. Optional / next session.

---

## 9. CALENDAR + TOKEN CAP FIX (2026-05-03)

Follow-up to the D6 STATE VERIFICATION audit which surfaced one FAIL row: `ANTHROPIC_MAX_INPUT_TOKENS=80000` had not been wired despite the D6 handoff claiming user accepted (iii). Plus a new ADDITIVE rule (§29 trading calendar) the user pre-approved 2026-05-03 to harden the replay loop against the upcoming 16-month rerun (which crosses 17 NYSE holidays + ~104 weekend days vs. the current 5-day all-trading-days window).

### 9.1 .env token cap (completed in prior session)

Append to `.env` (line 40): `ANTHROPIC_MAX_INPUT_TOKENS=80000`. Backup: `.env.bak_pre_token_cap_20260503` (1,339 bytes pre-edit). Provider read sites at `src/llm/anthropic_provider.py:480` and `:717` unchanged — both call `os.environ.get("ANTHROPIC_MAX_INPUT_TOKENS", DEFAULT_MAX_INPUT_TOKENS)`, so the new env var is picked up automatically when `python-dotenv` loads `.env` at script start. Effective per-call input cap is now 80_000 tokens (raised from default 50_000).

### 9.2 pandas-market-calendars install

`pandas-market-calendars==5.3.2` installed via pip (was missing). Pulled in `exchange-calendars==4.13.2`, `korean-lunar-calendar==0.3.1`, `pyluach==2.3.0`, `toolz==1.1.0` as transitives. `requirements.txt` not updated yet — out of scope for this task; flag for next cleanup pass.

### 9.3 src/utils/trading_calendar.py creation

New module, ~80 LOC. Surface (all `lru_cache`-wrapped via cached `_valid_days_index`):
- `is_trading_day(d) -> bool`
- `previous_trading_day(d) -> date` (walks back up to 14 calendar days; raises if no trading day in window)
- `next_trading_day(d) -> date` (walks forward up to 14)
- `trading_days_between(start, end) -> list[date]`
- `is_early_close(d) -> bool` (**KNOWN BUG** — see 9.7 below)

`src/utils/__init__.py` created as empty marker (directory was new).

Smoke test results (2026-05-03):

| input | result | expected | status |
|---|---|---|---|
| `is_trading_day(2026-04-27)` | True | True (Monday) | OK |
| `is_trading_day(2026-04-25)` | False | False (Saturday) | OK |
| `is_trading_day(2026-01-01)` | False | False (New Year) | OK |
| `previous_trading_day(2026-04-27)` | 2026-04-24 | 2026-04-24 (Friday) | OK |
| `previous_trading_day(2026-01-02)` | 2025-12-31 | 2025-12-31 (Wed; 1/1 is holiday) | OK |
| `trading_days_between(4/27, 5/1)` | [4/27, 4/28, 4/29, 4/30, 5/1] | 5 dates | OK |
| `is_early_close(2026-07-03)` | False | (depends on schedule; 7/3 is observed July 4 holiday → fully closed → False is correct here) | OK |

### 9.4 Replay script integration

`scripts/portfolio_5day_2026_04_27_to_05_01.py` modified at 4 sites (backup `bak_pre_calendar_20260503`, 37,399 bytes pre-edit):

1. **Line 64**: import `is_trading_day, previous_trading_day, trading_days_between` from `src.utils.trading_calendar`.
2. **Line ~727 (daily loop start)**: `if not is_trading_day(today_d): continue` skip guard with `[non-trading day, skipping]` log message. The 4/27..5/1 window is all-trading-days so this is a no-op for the current run; the guard is defensive for the 16-month rerun.
3. **`rank_surge_candidates` ~line 188–205**: replaced `rows[today_idx-1]` lookup with `previous_trading_day(today_d)`-keyed search through the cached rows list. Existing data-quality fail path (`gap_calc_no_prior_in_window` → `skipped_no_prior` counter) preserved for cases where the previous trading day's bar is outside the cache window. This also fixes a latent bug: the old `today_idx-1` logic broke if the cache window had any internal gap; the new logic is robust to window boundaries.
4. **QL weekly scan ~line 793–810**: replaced `if day_iso == WINDOW_DAYS[0]` (window-relative first-day check) with first-trading-day-of-ISO-week check via `previous_trading_day().isocalendar().week != today.isocalendar().week`. For 4/27..5/1, 4/27 Monday remains the first-trading-day-of-week so behavior is unchanged in this window. For the 16-month rerun, scan slides to Tuesday automatically on MLK / Presidents / Memorial / Labor Mondays.

AST parses cleanly post-edit.

### 9.5 §29 RULES.md additive rule (5 sub-rules)

Inserted after §28 (line ~749) as a new ## §29 section with W-CAL-01 through W-CAL-05 IDs. Backup: `docs/RULES.md.bak_pre_calendar_20260503` (149,779 bytes pre-edit).

- §29.1 CRITICAL — NYSE calendar via `pandas-market-calendars`; hardcoded holiday lists FORBIDDEN.
- §29.2 CRITICAL — `prior_close` MUST come from `previous_trading_day(today)`.
- §29.3 CRITICAL — Replay daily loops MUST skip non-trading days.
- §29.4 IMPORTANT — QL scan fires on FIRST TRADING DAY of each ISO week.
- §29.5 ADVISORY — `is_early_close` informational; no execution-timing logic depends on it as of v0.8.7.

§22 metadata: `document version` bumped 2.5 → 2.6; `created` line appended with "doc v2.6 added 2026-05-03 (§29 trading calendar additive)". `### Change log` gained the doc v2.6 entry above the existing doc v2.5 entry (reverse chronological order preserved). Section index updated with `| 29 | Trading Calendar | 5 |` row before the `| 22 |` row.

### 9.6 Hash verify

`python tests/test_regression_matrix.py` PASS:
- 30/30 schema-pass cells
- 30/30 cache-hit-on-2nd-run cells
- hash A: `sha256:6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095`
- expected (v0.8.7 baseline): `sha256:6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095`
- byte-identical hash regression PASSES.

§29 + utility module + replay-script edits do NOT enter the packet path. Hash unchanged as predicted.

### 9.7 FLAG — `is_early_close` UTC-vs-ET bug

The user-supplied implementation reads `close_time.hour < 16` against the `market_close` Timestamp returned by `pandas-market-calendars`. That Timestamp is tz-aware UTC, so `.hour` returns the UTC hour: 21 for a regular 16:00 ET close, 18 for a 13:00 ET early close. Both `21 < 16` and `18 < 16` are False, so the function returns False on every NYSE early-close day.

Verified by hand: `is_early_close(2026-12-24)` returns False even though NYSE schedule for 2026-12-24 shows market_close at `18:00:00+00:00` (a real 13:00 ET early close).

Not fixed: §29.5 is ADVISORY-only and the §29.5 rule explicitly says "no execution-timing logic depends on it as of v0.8.7." Defer fix to a future task that needs early-close timing (e.g. shifting the 12:30 ET cover evaluation earlier on early-close days).

Suggested one-line fix when needed: `close_time.tz_convert("America/New_York").hour < 16` instead of `close_time.hour < 16`.

### 9.8 Backups created

| file | size | note |
|---|---:|---|
| `.env.bak_pre_token_cap_20260503` | 1,339 | from prior session |
| `scripts/portfolio_5day_2026_04_27_to_05_01.py.bak_pre_calendar_20260503` | 37,399 | pre-§29 integration |
| `docs/RULES.md.bak_pre_calendar_20260503` | 149,779 | pre-§29 insertion |

### 9.9 Pending next session (carried forward)

- **MUST-FIX 2/3 — AKAN-class soft-veto LLM verify** (carried from §8). Single-ticker AKAN SS at 2026-04-28 09:30 ET, ~$0.30. Still skipped today per spec.
- Cache window widening (`_fetch_full_universe_5day.py` start=2026-04-24) — surface a 2026-04-27 morning-gap candidate.
- ~~`requirements.txt` add `pandas-market-calendars==5.3.2`.~~ DONE in §10.2.
- ~~`is_early_close` UTC-vs-ET fix when needed by a real consumer (not blocking).~~ DONE in §10.1.

---

## 10. POST-CALENDAR CLEANUP (2026-05-03)

Three flagged items from the §9 calendar refactor closed out in this short follow-up. NO LLM cost. Pure infrastructure cleanup. User pre-approved 2026-05-03.

### 10.1 is_early_close timezone fix

**Bug.** `pandas-market-calendars` returns the `market_close` column as a tz-aware UTC `pandas.Timestamp`. The original `close_time.hour < 16` reads the UTC hour: 21 for the regular 16:00 ET close, 18 for the 13:00 ET early close. Both `21 < 16` and `18 < 16` are False, so the function silently returned False on every NYSE early-close day. Detected during §9.3 smoke test against 2026-12-24.

**Fix.** Convert the Timestamp to America/New_York before reading the hour:

```python
return close_time.tz_convert("America/New_York").hour < 16
```

Implemented at `src/utils/trading_calendar.py:75–80` (the comparison line plus a 4-line comment explaining the UTC-vs-ET pitfall).

**Smoke test (5/5 pass):**

| input | result | expected | status |
|---|---|---|---|
| `is_early_close(2026-12-24)` | True | True (1pm ET, day before Christmas) | OK |
| `is_early_close(2026-07-02)` | False | False (July 4 2026 is Saturday → observed Friday 7/3 = full closure; no "day before" early close on Thursday 7/2) | OK |
| `is_early_close(2026-04-27)` | False | False (regular full-day Monday) | OK |
| `is_early_close(2026-04-25)` | False | False (Saturday non-trading) | OK |
| `is_early_close(2026-11-27)` | True | True (Black Friday, day after Thanksgiving) | OK |

Verified against the underlying NYSE schedule: 2026-12-24 `market_close` = `18:00:00+00:00` UTC = `13:00:00` ET (.hour = 13 in ET, < 16 → True). 2026-07-02 `market_close` = `20:00:00+00:00` UTC = `16:00:00` ET (.hour = 16 in ET, NOT < 16 → False, correct).

**Backup.** `src/utils/trading_calendar.py.bak_pre_tz_fix_20260503` (2,906 bytes).

### 10.2 requirements.txt update

`pandas-market-calendars` was missing from the dependency manifest (installed via direct pip during §9.2 but never declared). Added pin + the major transitive (`exchange-calendars`, also pulled in by §9.2 install).

```
# Trading calendar (NYSE holiday-aware, see RULES.md §29)
pandas-market-calendars==5.3.2
exchange-calendars==4.13.2
```

Appended at lines 8–10 of `requirements.txt`. Pre-edit file was 6 packages / 94 bytes; post-edit is 8 packages plus header comment / ~190 bytes.

Other transitive deps from the §9.2 install (`korean-lunar-calendar==0.3.1`, `pyluach==2.3.0`, `toolz==1.1.0`) are NOT pinned — they are second-order transitives and pip will resolve them when the two declared deps are installed; pinning them would over-constrain the lock without value. If a future paper-submission lockfile is generated via `pip-compile` or similar, those second-order deps will get pinned automatically.

**Backup.** `requirements.txt.bak_pre_calendar_20260503` (94 bytes).

### 10.3 RULES.md header version drift fix

**Drift state.** Header table at line 5 said `**Version** | **2.3 — 2026-05-02 (§5.17 surge-short integrity-veto exception)**` while §22 metadata table at line 762 already said `document version | 2.6`. The header was last touched at v2.3 on 2026-05-02 and never resynced through v2.4 (§13.6.SOFT_REASONING), v2.5 (§27.10–§27.18 UST refactor), or v2.6 (§29 trading calendar). Three doc versions of cumulative drift.

**Fix.** Updated line 5 to:

```
| **Version** | **2.6 — 2026-05-03 (§29 trading calendar additive; cumulative includes v2.4 §13.6.SOFT_REASONING, v2.5 §27.10–§27.18 UST refactor, v2.6 §29 trading calendar)** |
```

This is METADATA, not a rule. Standing Rule 4 governs RULE additions/modifications; the header version field is documentation-only and does not enter any binding state. User pre-approved 2026-05-03 the cosmetic-drift fix.

**Hash verify after all 3 edits in this session.** `python tests/test_regression_matrix.py`:
- 30/30 schema-pass cells
- 30/30 cache-hit-on-2nd-run cells
- hash A: `sha256:6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095`
- expected (v0.8.7 baseline): `sha256:6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095`
- byte-identical hash regression PASSES.

`is_early_close` lives in `src/utils/`, `requirements.txt` is build-only, and the RULES.md header is documentation — none enter the packet path. Hash unchanged as predicted.

**Backup.** `docs/RULES.md.bak_pre_header_fix_20260503` (154,667 bytes).

### 10.4 Backups created (this section)

| file | size | purpose |
|---|---:|---|
| `src/utils/trading_calendar.py.bak_pre_tz_fix_20260503` | 2,906 | pre-tz-fix snapshot |
| `requirements.txt.bak_pre_calendar_20260503` | 94 | pre-calendar-deps snapshot |
| `docs/RULES.md.bak_pre_header_fix_20260503` | 154,667 | pre-header-resync snapshot |

### 10.5 Pending next session (refreshed)

- **MUST-FIX 2/3 — AKAN-class soft-veto LLM verify** (still carried). ~$0.30. Skipped today per spec.
- Cache window widening (`_fetch_full_universe_5day.py` start=2026-04-24).

---

## 11. PACKET DRY-RUN AKAN 2026-04-28 (2026-05-03)

Wiring verification BEFORE spending any LLM money. The D6 0-trade replay was caused by a silent wiring failure — block builders emitting `not_evaluated` placeholders, replay script never passing `live_adapters=True`. This dry-run rebuilds the AKAN 2026-04-28 packet using the current `LIVE_ADAPTERS_TUPLE` (9 adapters) under v0.8.7 and inspects every block's status enum to confirm real content flows through. Cost: $0 (no LLM, no agent calls).

### 11.1 Inputs

- ticker: `AKAN`
- decision_timestamp: `2026-04-28T09:30:00-04:00`
- candidate intent: surge_short (mirrors what the replay script would pass)
- live_adapters: `LIVE_ADAPTERS_TUPLE` = (`wikipedia_pageviews`, `sec_edgar`, `github_public`, `sec_8k_fulltext`, `github_commit_messages`, `sec_form4`, `sec_13f`, `polygon_news`, `fmp_sentiment`) — 9 adapters, single source of truth at `scripts/portfolio_5day_2026_04_27_to_05_01.py:88-98`
- generator: `src/evidence_packet/generator.py::generate_evidence_packet` (signature: `ticker, decision_mode, decision_timestamp, decision_sub_mode, enabled_blocks, live_adapters, strict_pit_mode`)

### 11.2 Output artifacts

- Full packet dump: `data/decisions/dryrun_akan_20260428_packet.json` (48,856 bytes / 35,931 chars)
- Dry-run script archived for audit: `data/decisions/dryrun_script_archive_20260503.py` (3,820 bytes)

### 11.3 Status counts

| status | count |
|---|---:|
| `ok` | 12 |
| `not_evaluated` | 9 |
| `data_unavailable` | 4 |
| `insufficient_evidence` | 1 |
| **Total blocks with status field** | **26** |

Token estimate: **~8,982 tokens** (35,931 chars / 4 chars-per-token rule of thumb). Well under the 80,000 cap; would also have fit under the legacy 50,000 default. (`tiktoken` not installed; rough estimate per task spec — pulling tiktoken just for an estimate would over-engineer.)

### 11.4 Per-block diagnosis

| Block path | Status | Diagnosis |
|---|---|---|
| `price_snapshot` | ok | EXPECTED |
| `macro_regime` | ok | EXPECTED |
| `fundamental_snapshot` | ok | EXPECTED |
| `valuation_snapshot` | ok | EXPECTED |
| `corporate_calendar` | ok | EXPECTED |
| `decision_time_discipline` | ok | EXPECTED |
| `alternative_data_features` (root) | ok | EXPECTED — `alt-data adapters wired and delivering` |
| `alternative_data_features.attention.wikipedia_pageviews` | ok | EXPECTED — 30 rows from cache |
| `alternative_data_features.adapter_selection` | ok | EXPECTED |
| `alternative_data_features.tech_activity.github_public` | data_unavailable | **EXPECTED + ALIGNED WITH BEAR THESIS** — no GitHub presence for AKAN, structurally consistent with self-proclaimed AI shell company |
| `alternative_data_features.retail_meme_attention` | not_evaluated | EXPECTED — Reddit excluded from scope per D1 Step D |
| `alternative_data_features.valuation_context` | not_evaluated | EXPECTED — sub-feature stub |
| `alternative_data_features.opencli_evidence` | not_evaluated | EXPECTED — opencli rows summarized at adapter manifest level |
| `sentiment_community_ownership_evidence.sec_form4` | ok | EXPECTED — no_recent_form4 info flag (call succeeded; no recent insider transactions) |
| `sentiment_community_ownership_evidence.sec_13f` | data_unavailable | EXPECTED — no 13F filers for AKAN micro-cap |
| `sentiment_community_ownership_evidence.fmp_sentiment` | data_unavailable | EXPECTED — FMP plan 404s on press-releases + stock-sentiments-rss endpoints (logged at top of run) |
| `sentiment_community_ownership_evidence.market_sentiment` / `community_size` / `ownership_positioning` | not_evaluated | EXPECTED — feeds empty for AKAN, sub-blocks correctly show `data_available: false` |
| `sentiment_community_ownership_evidence` (root) | not_evaluated | **COSMETIC FLAG** — root says `source: "not_connected"` and `PIT_safety_notes: "v1 generator does not wire any ownership feed"` but children ARE wired and carrying real adapter rows. Stale legacy message; not blocking. |
| `information_integrity_assessment` | insufficient_evidence | EXPECTED — finalizer ran (`source: alt_data_manifest`, `data_available: true`); T1=0 T2=0 T3=1 T4=0 T5=0 distribution. `use_as_primary_signal_allowed: false` per §10.4 fail-closed. AKAN micro-cap legitimately has only low-tier sources. Risk soft-veto extension (v0.8.7 §13.6) handles this case. |
| `news_event_summary` (root) | data_unavailable | EXPECTED — no news in window |
| `news_event_summary.live_adapter_rows.polygon_news` | ok | EXPECTED — no_news_in_window info |
| `filing_confirmation.filing_index.sec_edgar` | ok | EXPECTED — manifest carries `cik: 0001888014, lookback_days: 90, rows_returned: 0` + `no_recent_8k` info flag |
| `filing_confirmation` (root) | not_evaluated | **COSMETIC FLAG** — root says `reason: "SEC adapter not connected in v1"` but `filing_index.sec_edgar` IS connected and ok. Stale legacy message; not blocking. |
| `narrative_price_gap_assessment` | not_evaluated | EXPECTED BY DESIGN — `source: agent_internal_pending`, `evidence_used: [news_event_summary.items, filing_confirmation, price_snapshot.return_5d_pct, price_snapshot.relative_volume_vs_20d, alternative_data_features.attention.wikipedia_pageviews]`, `evidence_missing: [agent_LLM_reasoning]`. Packet provides pointers; agent computes verdict downstream. |

Zero genuine wiring failures. Adapter manifest confirms all 9 sources called: wikipedia (30 rows), sec_edgar (0 rows ok), github_public (0 rows / no presence), polygon_news (0 rows / no news), sec_form4 (info no_recent), sec_13f (no filers), fmp_sentiment (404 expected), sec_8k_fulltext + github_commit_messages (rolled into respective filing/tech blocks).

### 11.5 Cosmetic-drift items (non-blocking)

Two finalizers carry stale top-level status messages that don't reflect that the live_adapters pipe NOW DOES populate their child rows:

1. `sentiment_community_ownership_evidence` root message: `"v1 generator does not wire any ownership feed"`. Should be updated to: `"populated from alt_data_manifest sec_form4 + sec_13f + fmp_sentiment delivery; child rows authoritative when present"`.
2. `filing_confirmation` root message: `"SEC adapter not connected in v1"`. Should be updated to: `"populated from alt_data_manifest sec_edgar + sec_8k_fulltext delivery; filing_index authoritative"`.

Agents see both root + child statuses, so the agents can read the actual adapter rows. This is a documentation-of-state issue, not a wiring issue. Defer to a future no-LLM cleanup pass; not blocking the LLM verify.

### 11.6 PASS criteria check

| criterion | threshold | actual | status |
|---|---|---|---|
| `ok` block count | ≥ 5 | 12 | PASS |
| Token estimate | ≤ 80,000 | ~8,982 | PASS |
| No exceptions / tracebacks | required | clean run | PASS |
| No UNEXPECTED non-ok blocks | required | 0 (the 4 `not_evaluated` in finalizers are all expected-by-design or cosmetic-only) | PASS |

### 11.7 Verdict

**PASS — proceed to LLM verify (MUST-FIX 2/3) when next budgeted.**

Single-ticker AKAN 2026-04-28 09:30 ET surge_short Risk Agent verify under `v0.6_2026_05_02_surge_short_softveto`. Estimated cost ~$0.30 per the §8 estimate. The packet has the bear-thesis-aligned evidence floor (no GitHub, no 13F, no 8-K, low-tier sources only) that the soft-veto extension was designed to handle: when missing data IS the bear thesis itself, Risk should pass advisory-only on §5.13(b)/(e), NOT trip a hard veto.

### 11.8 Files written / archived (this section)

| file | size | purpose |
|---|---:|---|
| `data/decisions/dryrun_akan_20260428_packet.json` | 48,856 | full packet dump for audit |
| `data/decisions/dryrun_script_archive_20260503.py` | 3,820 | dry-run script archived (was `scripts/_dry_run_*.py`) |

No backups created in this section — read-only against existing source code; only new dump artifacts produced. Cost: $0.

---

## 12. FINALIZER ROOT TEXT FIX (2026-05-03)

D7 dry-run §11 surfaced two finalizer root messages that carried stale legacy text despite their child rows being live-wired by the `live_adapters` overlay. Risk: an LLM agent reading the root text first might mechanical-veto on perceived missing data when the children are actually populated. Identified by §11.5 as "first place to look if MUST-FIX 2/3 LLM verify produces unexpected veto." Fixed before spending any LLM money. Cost: $0.

### 12.1 Stale strings located

| string | file:line | block |
|---|---|---|
| `"v1 generator does not wire any sentiment / community / ownership feed; schema skeleton emitted as not_evaluated"` | `src/evidence_packet/blocks/sentiment_ownership.py:95-96` | `sentiment_community_ownership_evidence.reason` |
| `"13F-style ownership data is filed with delay; PIT-safety requires accepted_datetime <= allowed_data_cutoff. v1 generator does not wire any ownership feed."` | `src/evidence_packet/blocks/sentiment_ownership.py:71-75` | `sentiment_community_ownership_evidence.ownership_positioning.PIT_safety_notes` |
| `"ownership feed not connected in v1; staleness warning will be populated when an FMP ownership / SEC 13F adapter is wired"` | `src/evidence_packet/blocks/sentiment_ownership.py:89-92` | `sentiment_community_ownership_evidence.ownership_positioning.data_staleness_warning` |
| `"SEC adapter not connected in v1"` | `src/evidence_packet/generator.py:114` | `filing_confirmation.reason` (via `_build_filing_confirmation_placeholder`) |

### 12.2 Architecture confirmed

The 4 strings live in SKELETON builders (`blocks/sentiment_ownership.py::build()` and `generator.py::_build_filing_confirmation_placeholder()`). The `adapter_wiring.py` finalizers (`_finalize_sentiment_ownership` at line 728, `_finalize_filing_confirmation` at line 673) correctly OVERWRITE root `status` / `source` / `reason` when child rows arrive — but for AKAN the children all returned 0 actionable rows (sec_edgar 0 8-Ks in 90 days; fmp_sentiment 404 on FMP plan; sec_13f no filers; sec_form4 no recent insider transactions), so the early-return branches kept the skeleton text. The bug is in the SKELETON's default text describing the wiring as "not connected", which is no longer true post-§9 / D6 chunk-final.

### 12.3 Replacements applied

`src/evidence_packet/blocks/sentiment_ownership.py:95-102` — top-level `reason`:
```
"v0.8.7 wires fmp_sentiment / sec_13f / sec_form4 / sec_def14a /
github_public via the live_adapters overlay; this skeleton text
remains when no child rows were delivered for this ticker — see
sibling sub-blocks (fmp_sentiment, sec_13f, sec_form4) for actual
adapter status. status/source/reason are overwritten by
_finalize_sentiment_ownership when ANY sub-section delivers."
```

`src/evidence_packet/blocks/sentiment_ownership.py:71-77` — `ownership_positioning.PIT_safety_notes`:
```
"13F-style ownership data is filed with delay; PIT-safety requires
accepted_datetime <= allowed_data_cutoff. v0.8.7 wires sec_13f /
sec_form4 / sec_def14a via live_adapters; this skeleton text
remains when no rows were delivered for this ticker — see
sibling sub-blocks (sec_13f, sec_form4, sec_def14a) for actual
adapter status."
```

`src/evidence_packet/blocks/sentiment_ownership.py:89-93` — `ownership_positioning.data_staleness_warning`:
```
"ownership feed wired (sec_13f / sec_form4 / sec_def14a);
staleness will be populated by _finalize_sentiment_ownership
when adapter rows arrive"
```

`src/evidence_packet/generator.py:114-120` — `filing_confirmation.reason`:
```
"v0.8.7 wires sec_edgar (filing_index) + sec_8k_fulltext
(full_text) via the live_adapters overlay; this skeleton text
remains when no 8-K rows were delivered for this ticker — see
filing_index.sec_edgar and full_text.sec_8k_fulltext sub-blocks
for actual adapter status. status/source/reason are overwritten
by _finalize_filing_confirmation when sec_edgar delivers rows."
```

The KEY principle held: skeleton text now describes the **wiring architecture** (which adapters are connected, what triggers the finalizer overwrite) rather than asserting "not wired." Runtime data state remains the child blocks' authoritative source. Agents reading the root will no longer be told the adapters are absent when they are actually present-and-empty.

### 12.4 Hash impact + relock

Pre-fix expected: `sha256:6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095` (v0.8.7 initial baseline from §9).

The 4 prose strings flow into the AAPL packet's hashed region (no `live_adapters` kwarg → finalizers don't promote → skeleton text retained → text IS in hash path; verified via `HASH_EXCLUDED_LEAF_KEYS` / `HASH_EXCLUDED_TOP_LEVEL_PATHS` in `src/evidence_packet/schema.py` — `reason`, `PIT_safety_notes`, `data_staleness_warning` are NOT excluded; only wall-clock leaves and `source_list` / `build_telemetry` top-level paths are excluded).

Post-fix observed: `sha256:04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc`. Hash stable across reruns confirmed (`hash stable across reruns: True`). Confined-change verified by direct packet inspection: only `filing_confirmation.reason`, `sentiment_community_ownership_evidence.reason`, `sentiment.ownership_positioning.PIT_safety_notes`, `sentiment.ownership_positioning.data_staleness_warning` carry new content; `price_snapshot`, `macro_regime`, etc. unchanged.

Relock applied at `tests/test_regression_matrix.py:56-58` with predecessor v0.8.7 (initial) hash retained one line above for audit, and a 9-line block comment documenting the cosmetic-only nature of the relock (no rule_version change, no semantic agent-consumed-field change beyond the 4 prose strings).

Re-run after relock: 30/30 schema-pass, 30/30 cache-hit-on-2nd-run, byte-identical hash regression PASSES.

### 12.5 Re-run AKAN dry-run vs §11 baseline

Re-ran `dryrun_script_archive_20260503.py` (temp-copied to `scripts/_dry_run_akan_recheck_20260503.py`, executed, deleted). Status counts identical to §11.3:

| status | §11 baseline | post-fix | match |
|---|---:|---:|---|
| `ok` | 12 | 12 | ✓ |
| `not_evaluated` | 9 | 9 | ✓ |
| `data_unavailable` | 4 | 4 | ✓ |
| `insufficient_evidence` | 1 | 1 | ✓ |
| Total | 26 | 26 | ✓ |

Notes column (the 50-char preview) for the 2 fixed blocks now shows the new prose:

- `sentiment_community_ownership_evidence` → `v0.8.7 wires fmp_sentiment / sec_13f / sec_form4 /...` (was `v1 generator does not wire any sentiment / communi...`)
- `filing_confirmation` → `v0.8.7 wires sec_edgar (filing_index) + sec_8k_ful...` (was `SEC adapter not connected in v1`)

No new exceptions. Token count unchanged (~9k). All 12 ok counts preserved.

### 12.6 Backups created

| file | size | purpose |
|---|---:|---|
| `src/evidence_packet/blocks/sentiment_ownership.py.bak_pre_root_text_fix_20260503` | 4,234 | pre-fix snapshot of the skeleton builder |
| `src/evidence_packet/generator.py.bak_pre_root_text_fix_20260503` | 26,319 | pre-fix snapshot of `_build_filing_confirmation_placeholder` |
| `tests/test_regression_matrix.py.bak_pre_root_text_fix_20260503` | 8,999 | pre-relock snapshot of `EXPECTED_AAPL_PACKET_HASH` |

### 12.7 Verdict + handoff

Cosmetic / agent-readability fix complete. The dry-run no longer flags any non-blocking issues; both finalizer root messages now accurately describe the v0.8.7-wired state. MUST-FIX 2/3 LLM verify (~$0.30) is unblocked from this angle: agents reading the root text will not be misled into thinking the SEC / sentiment / ownership adapters are absent.

Hash transition for audit log:
- v0.8.7 (initial, 2026-05-02 §27 UST refactor): `6278036bd42b9681a3dd8c65d145fc1abe5f28b465b909bba09a80401f947095`
- v0.8.7 (post 2026-05-03 finalizer-text fix): **`04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc`**

No `rule_version` literal changed in any of the 9 src/ authoritative sites (checked: still `v0.8.7_ust_fixed_income`). The relock is purely a documentation/UX hash drift, not a rule promotion.

---

## 13. PASS 1 — PM AGENT UNIVERSAL SOFT-VETO REFACTOR (2026-05-03)

Pass 1 of 5 in the prompt-refactor sequence proposed in the §audit (Step 8). PM is the keystone — final decision authority and the central mechanical-veto inheritance point. Refactor follows the user's 2026-05-03 universal-soft-veto principle: hard veto applies ONLY to (sleeve_cap_breach, position_size_cap, lookahead_data_used, hindsight_violation, §3.8 QL gates when fields present); ALL other §5.13 conditions become advisory across both candidate types. Cost: $0 (no LLM, no replay).

### 13.1 KILL targets identified + neutralised

Three keystone mechanical-veto clauses replaced; one pre-existing labeling drift fixed.

| target | old (`bak_pre_universal_softveto_20260503` line) | new |
|---|---|---|
| L33 keystone ANY-veto inheritance | `"If ANY veto_conditions_evaluated entry has tripped=true (whether from your evaluation or inherited from Risk), decision MUST be 'veto' and position_size_pct MUST be 0.0."` | Split into HARD VETO CONDITIONS (4 clauses (i)-(iv) — sleeve cap, position cap, lookahead, hindsight) and ADVISORY CONDITIONS (§5.13(b)/(d)/(e) reasoning across both candidate types). Pre-existing FAIL-CLOSED block restructured into ENTRY-DECISION GATES with hard-veto cross-references. |
| L34 alt-data mechanical collapse | `"If Alt-Data Verification's recommendation_to_pm is 'needs_more_evidence' or 'pollution_risk_high', decision cannot be 'buy' or 'short' — collapse to 'watch' or 'no_trade'."` | Per-candidate-type advisory framing: QL weighs against §3.8 + prelude rationale; SS may treat pollution_risk_high as confirming bear thesis per §5.17. Document synthesis in reason/decision_log. NOT a mechanical gate. |
| L57-58 R7 NULL-BLOCK fail-close | `"If a block essential to your role is absent, trip missing_data_persists_beyond_tolerance, set thesis_confirmation_allowed=false ..., and force decision to 'veto' or 'no_trade'."` | Soft-encoded R7: anti-hallucination spirit retained as CRITICAL dead rule; mechanical fail-close action removed. 5-step reasoning protocol: acknowledge absence, reduce confidence, flag in evidence_missing, set thesis_confirmation_allowed=false (audit continuity), reason from what is available — do NOT mechanically force decision. |
| L39 §5.13 sub-letter labeling drift | `"Risk conditions (a)/(b)/(c)/(e) — sleeve cap, per-position cap, critical data_quality_flags, post-cutoff data — STILL veto as written."` (mislabeled — canonical §5.13 has (a)=sleeve, (c)=lookahead; (b)=missing-data and (e)=insufficient-evidence are now advisory) | Re-anchored to the new (i)-(iv) hard-veto numbering; clarifies that mechanical inheritance applies only to those 4 conditions, not to Risk's §5.13(b)/(d)/(e) advisory entries. |

### 13.2 KEEP lines preserved (verified post-edit)

| section | line in new file | content |
|---|---:|---|
| COVER DECISION AUTHORITY (§10.14) | L12-19 | unchanged |
| ANTI-HINDSIGHT CLAUSE (CRITICAL) | L36-37 | unchanged (R1-R6 dead rule) |
| §4.8 sleeve / §3.8 fundamental gates | L64-65 | preserved as hard-veto conditions, with the §3.8-fields-missing carve-out per the new universal principle |
| SURGE-SHORT INTEGRITY-VETO EXCEPTION (§5.17) | L71-76 | preserved verbatim except the L39 sub-letter relabeling fix |
| HARD INVARIANTS (validator-checked) | L78-85 | unchanged: PIT field equality, immutable_decision_flag, social_signal_weight forbidden, descriptor-only flags, lookahead → veto, no "whale", T+1 next-open |
| REASON / AUDIT | L87-90 | unchanged |
| ADAS ADVISORY INPUT (§24, §5) | L100-101 | unchanged |
| FRIDAY FI SLEEVE REVIEW (§27.5) | L103-108 | unchanged |
| REASONING UNDER INCOMPLETE EVIDENCE (§13.6.SOFT_REASONING) | L109-120 | unchanged — already aligned with new principle |
| USER_PROMPT_TEMPLATE | L124-130 | unchanged |

### 13.3 New top-block: UNIVERSAL SOFT-VETO PRINCIPLE

Inserted at L21-34 immediately after the COVER DECISION AUTHORITY block, framing all subsequent FAIL-CLOSED / hard-invariant clauses. Cites the 4 mechanical hard-veto conditions, the ADVISORY scope across §5.13(b)/(d)/(e), the §3.8 QL fundamental gates carve-out (mechanical only when fields present), and the universal "missing data is a SIGNAL, not a fail-trigger" principle.

### 13.4 New hard-veto-vs-advisory split structure

Replaces the single-clause FAIL-CLOSED block at L30-34 (old). New structure spans L42-69 (new file):

- **L42-48 HARD VETO CONDITIONS** — the 4 (i)-(iv) mechanical conditions: sleeve_cap_breach (§5.13(a)), position_size_cap_breach (§4.7 / §4.8), lookahead_data_used (§5.13(c)), hindsight_violation (§8 R1-R7). "These are dead rules. NO override under any circumstance."
- **L50-61 ADVISORY CONDITIONS** — §5.13(b)/(d)/(e) across both candidate types. 4-step reasoning protocol: read prelude rationale → apply candidate-type-aware reasoning (QL: missing data ≠ cannot decide; SS: missing data may BE bear thesis) → decide watch/no_trade/sized-down based on synthesis → document gaps in reason/decision_log.
- **L63-69 ENTRY-DECISION GATES** — preserves the structural buy/short gates from the old FAIL-CLOSED block, but rewords "no Risk veto tripped" → "no hard-veto condition (i)-(iv) tripped" so the gate is anchored to the mechanical-only set, not to Risk's full evaluation list. Alt-Data Verification's recommendation_to_pm is explicitly demoted from mechanical gate to advisory input with per-candidate-type reasoning guidance.

### 13.5 PROMPT_VERSION bump

`v0.5_2026_05_02_soft_reasoning` → `v0.6_2026_05_03_universal_softveto`. Per `src/agents/prompts/__init__.py:4` ("PROMPT_VERSION — bump this string to invalidate this agent's cache"), all existing PM cached outputs are invalidated by design. Cold-cache PM call cost on next replay: ~$0.05-0.10/ticker × N tickers — within budget for the AKAN single-ticker MUST-FIX 2/3 verify.

### 13.6 Hash regression result

`python tests/test_regression_matrix.py`:
- 30/30 schema-pass cells
- 30/30 cache-hit-on-2nd-run cells
- hash A: `sha256:04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc`
- expected (v0.8 baseline): `sha256:04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc`
- byte-identical hash regression PASSES.

PM prompt is consumed by the runner agent calls (cache-key input), NOT embedded in the packet hash path. Hash unchanged as predicted at the start of this pass.

### 13.7 AKAN dry-run packet integrity re-verify

Re-ran `dryrun_script_archive_20260503.py` (temp-copied to `scripts/_dry_run_pm_pass1_20260503.py`, executed, deleted). Status counts identical to §11.3 / §12.5 baseline:

| status | §11/§12 baseline | post-pass-1 | match |
|---|---:|---:|---|
| `ok` | 12 | 12 | ✓ |
| `not_evaluated` | 9 | 9 | ✓ |
| `data_unavailable` | 4 | 4 | ✓ |
| `insufficient_evidence` | 1 | 1 | ✓ |
| Total blocks with status | 26 | 26 | ✓ |
| Packet size | 35,931 chars | 36,693 chars | within 2% (microsecond timestamps differ between runs) |
| Token estimate | ~8,982 | ~9,173 | within cap (80k) |

No new exceptions. PM-prompt changes not exercised at dry-run level (no LLM call); this verify confirms only that packet generation itself is unaffected — which it is, as PM prompt is downstream of generator.

### 13.8 Backups created

| file | size | purpose |
|---|---:|---|
| `src/agents/prompts/pm_agent.py.bak_pre_universal_softveto_20260503` | 9,282 | pre-pass-1 PM prompt snapshot |

### 13.9 File-level diff summary

| metric | pre-pass-1 | post-pass-1 |
|---|---:|---:|
| LOC | 90 | 130 |
| SYSTEM_PROMPT length | ~9,000 chars | 13,820 chars |
| Mechanical fail-close clauses | 3 (L33, L34, L57-58) | 0 |
| Universal-soft-veto principle citations | 0 | 5+ (top-block + multiple cross-references) |
| Hard-veto enumeration clarity | 1 inline list, mislabeled drift at L39 | dedicated (i)-(iv) numbered list, cross-referenced 3× |

### 13.10 Pending Pass 2-5 (carried forward)

- **Pass 2 — sleeve agents.** `surge_short_agent.py` v1.1 → v1.2_2026_05_04_universal_softveto: add §13.6 SOFT-REASONING block (currently absent), rewrite L22 FAIL-CLOSED + L36 R7 NULL-BLOCK. Estimated 30 min, ~20 lines.
- **Pass 3 — prelude agents.** `narrative_event_agent.py` v0.4 → v0.5; `alt_data_verification_agent.py` v0.4 → v0.5; `fundamental_agent.py` v0.4 → v0.5. Neutralise mechanical fail-closes (L27/L43, L22/L25/L33/L41, L22/L34). Estimated 60 min total, ~24 lines net.
- **Pass 4 — descriptor agents.** `network_effect_agent.py` v0.2 → v0.3; `valuation_agent.py` v0.1 → v0.2. Add §13.6 blocks (currently absent in both); soften descriptor caps; coordinate valuation_agent + pm L67 buy-gate. Estimated 50 min, ~30 lines.
- **Pass 5 — risk fine-tune.** `risk_agent.py` v0.6 → v0.7. Scope L52-53 R7 NULL-BLOCK to candidate_type=quality_long only OR replace with advisory acknowledge-and-reduce-confidence pattern. Estimated 10 min, ~5 lines.

Each pass independently testable via packet dry-run (zero cost) before any LLM verify. Hash regression preserved across all passes (no PM/agent prompt enters packet path).

After all 5 passes, single-ticker AKAN MUST-FIX 2/3 LLM verify (~$0.30) becomes the integration test for the whole sequence — confirming agent reasoning under the universal-soft-veto principle does not produce mechanical vetoes when missing data is structurally consistent with bear thesis. Then full 5-day replay rerun (~$3) under the new prompts.

### 13.11 Standing Rule 4 + RULES.md status

- No RULES.md modification in this pass. The universal-soft-veto principle is approved per user chat 2026-05-03 (referenced in §audit and §11 dry-run as "first place to look if MUST-FIX 2/3 LLM verify produces unexpected veto").
- §13.6.SOFT_REASONING (RULES.md §13.6, codified 2026-05-02 at v0.8.6 → v0.8.7) provides the underlying framework; this pass updates the prompt-encoded implementation to extend its scope from surge_short-only to both candidate types.
- §8 R7 (RULES.md §8, "graceful absence of toggleable evidence-packet blocks") spirit retained as CRITICAL anti-hallucination dead rule. The mechanical fail-close action that the prior PM prompt imposed on R7 was a 2026-04-28 implementation choice that pre-dated §13.6 and the universal principle; loosened in this pass without modifying the §8 rule itself.
- `rule_version` literal unchanged across all 9 src/ authoritative sites: still `v0.8.7_ust_fixed_income`.

### 13.12 Verdict

**Pass 1 complete.** PM prompt encodes the 4-condition mechanical hard-veto set + advisory framing for §5.13(b)/(d)/(e) across both candidate types. Hash unchanged. Dry-run packet integrity preserved. Cache invalidated by PROMPT_VERSION bump (intentional). Ready for Pass 2 next session.

---

## 14. PASS 2 — SURGE_SHORT AGENT UNIVERSAL SOFT-VETO REFACTOR (2026-05-03)

Pass 2 of 5. Sleeve agent for surge_short candidates. Pre-refactor state had NO §13.6 / §5.17 references and TWO mechanical fail-closes (L22 FAIL-CLOSED, L36 R7 NULL-BLOCK) that directly blocked the bear-thesis-by-absence pattern §13.6 + §5.17 + 2026-05-03 universal-soft-veto principle were designed for. Cost: $0 (no LLM, no replay).

### 14.1 KILL targets identified + neutralised

| target | old (`bak_pre_universal_softveto_20260503` line) | new |
|---|---|---|
| L21-22 FAIL-CLOSED | `"recommended_action='short' requires evidence_sufficiency='sufficient' AND short_thesis_status='valid' AND confidence='high'. Otherwise the recommendation MUST collapse to 'watch' or 'no_trade'. When in doubt, 'needs_more_evidence' with non-empty evidence_missing is the right answer."` | New "RECOMMENDATION GUIDANCE (advisory, NOT a mechanical gate)" block. evidence_sufficiency / short_thesis_status / confidence reframed as REASONING OUTPUTS not gates. Three explicit bear-thesis paths enumerated: (a) explicit positive evidence of weakness, (b) structural-absence evidence, (c) inconsistency between narrative and verifiable evidence. "high" confidence does NOT require complete evidence; it requires defensible reasoning given evidence in hand. Mechanical collapse on null/missing blocks explicitly forbidden. |
| L35-36 R7 NULL-BLOCK | `"If a block essential to your role is absent (...alternative_data_features OR information_integrity_assessment OR price_snapshot), you MUST set evidence_sufficiency='insufficient' AND recommended_action='needs_more_evidence' AND list the missing block in missing_data_warnings."` | Soft-encoded R7. Anti-hallucination spirit retained as CRITICAL dead rule; mechanical fail-close action removed. 5-step reasoning protocol: acknowledge absence → reason about WHY (with examples for tech-narrative ticker / micro-cap cohort baseline / adapter-wired-vs-unavailable distinction) → list in missing_data_warnings → integrate absence pattern into evidence_sufficiency / short_thesis_status / confidence → do NOT mechanically collapse to needs_more_evidence. Explicit price_snapshot CARVE-OUT preserved as the one genuine hard requirement (§2.1 trigger filter requires today_open + prior_close). |

### 14.2 KEEP lines preserved (verified post-edit)

| section | location in new file | content |
|---|---:|---|
| READ-ONLY CONSTRAINT | L31-32 | unchanged |
| ANTI-HINDSIGHT CLAUSE (CRITICAL) | L34-35 | unchanged (R1-R6 dead rule) |
| OUTPUT FORMAT | L37-38 | unchanged |
| THRESHOLDS | L52-53 | unchanged (TBD reference + cautious defaults) |
| INVARIANTS — sleeve cap math | L55-58 | preserved verbatim: candidate_type=surge_short check, max_sleeve_exposure_remaining_pct = v0.4 sleeve cap math, audit_rationale 200-400 char requirement |

### 14.3 New top-block: UNIVERSAL SOFT-VETO + §13.6.SOFT_REASONING + §5.17

Inserted at L12-26 (after ROLE, before READ-ONLY CONSTRAINT). Sections:
- Universal soft-veto principle citation
- Surge_short universe scoping (US-listed equities under mandatory SEC reporting)
- 4 illustrative bear-thesis-by-absence patterns (tech narrative without R&D / GitHub / patents; self-proclaimed leader without 13F / Form 4; recent narrative pivot inconsistent with prior filings; high social-media buzz without 8-K)
- Explicit "REASONING PATTERN, not keyword pattern-match" caveat
- 3-condition mechanical hard-veto enumeration: §5.13(a) sleeve_cap_breach, position_size_cap math (§4.7/§4.8), §5.13(c) lookahead + §8 R1-R7 hindsight
- "ALL OTHER conditions are ADVISORY" closing line

The §3.8 quality_long fundamental gates carve-out (Pass-1 PM agent's hard-veto condition (iv)) is intentionally NOT listed here — surge_short_agent only runs when candidate_type=surge_short, so §3.8 is structurally inapplicable. The 3-condition vs Pass-1's 4-condition list is the correct scoping.

### 14.4 ALTERNATIVE DATA EMPHASIS (L40-43) contextualized

Old single-paragraph clause expanded with the "two forms of external corroboration" distinction:
- (1) explicit contradicting evidence in alt-data that IS present
- (2) structural-absence evidence (alt-data feed wired and verifiable yet returned no rows)

New paragraph instructs the agent to read `alt_data_manifest.calls[].source_flag` to distinguish "feed wired and returned 0 rows for this ticker" (informative bear evidence) from "endpoint unavailable / 404" (genuinely silent). This addresses the §11 / §12 dry-run finding that the AKAN packet's `fmp_sentiment` was `data_unavailable` due to FMP plan 404 (silent), while `github_public` was `data_unavailable` due to no presence (informative bear evidence) — both use the same `data_unavailable` enum but carry DIFFERENT signal under §13.6 reasoning.

### 14.5 PROMPT_VERSION bump

`v1.1_2026_04_28` → `v1.2_2026_05_03_universal_softveto`. Per `src/agents/prompts/__init__.py:4` cache-key contract, all existing surge_short cached outputs are invalidated by design. Cold-cache cost on next replay: ~$0.05/ticker × N surge candidates. Within MUST-FIX 2/3 budget.

### 14.6 Hash regression result

`python tests/test_regression_matrix.py`:
- 30/30 schema-pass cells
- 30/30 cache-hit-on-2nd-run cells
- hash A: `sha256:04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc`
- expected (v0.8 baseline): `sha256:04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc`
- byte-identical hash regression PASSES.

Sleeve agent prompts confirmed cache-key-only (not in packet hash path) per Pass-1 prediction.

### 14.7 AKAN dry-run packet integrity re-verify

| status | §11/§12/§13 baseline | post-pass-2 | match |
|---|---:|---:|---|
| `ok` | 12 | 12 | ✓ |
| `not_evaluated` | 9 | 9 | ✓ |
| `data_unavailable` | 4 | 4 | ✓ |
| `insufficient_evidence` | 1 | 1 | ✓ |
| Total blocks with status | 26 | 26 | ✓ |
| Packet size | 35,931 / 36,693 chars | 36,695 chars | within 2% (timestamp microsecond drift) |
| Token estimate | ~9k | ~9,173 | within 80k cap |

No new exceptions. Surge_short prompt changes not exercised at dry-run level (no LLM call); this verify confirms only that packet generation is unaffected.

### 14.8 Backups created

| file | size | purpose |
|---|---:|---|
| `src/agents/prompts/surge_short_agent.py.bak_pre_universal_softveto_20260503` | 4,043 | pre-pass-2 surge_short prompt snapshot |

### 14.9 File-level diff summary

| metric | pre-pass-2 | post-pass-2 |
|---|---:|---:|
| LOC | 45 | 84 |
| SYSTEM_PROMPT length | ~3,800 chars | 10,166 chars |
| Mechanical fail-close clauses | 2 (L22 FAIL-CLOSED, L36 R7) | 0 |
| §13.6 / §5.17 / universal-soft-veto citations | 0 | 5+ (top-block + cross-references) |
| Bear-thesis-by-absence example patterns | 0 | 4 illustrative + 3 reasoning paths (a)/(b)/(c) |

### 14.10 Pending Pass 3-5 (carried forward)

- **Pass 3 — prelude agents (3 files, ~60 min total):**
  - `narrative_event_agent.py` v0.4 → v0.5: neutralise L27 FAIL-CLOSED + L43 R7 NULL-BLOCK; §13.6 already present at L44-55, just remove the contradicting upstream commands.
  - `alt_data_verification_agent.py` v0.4 → v0.5: neutralise L22 FAIL-CLOSED + L25 verdict-cap + L33 pollution-collapse + L41 R7. Keep L34 lookahead intact (PIT dead rule). §13.6 already present at L42-53.
  - `fundamental_agent.py` v0.4 → v0.5: neutralise L22 (only the auto-decision-to-needs_more_evidence portion; "hard_gates_pass=false on missing fields" stays — gate literally cannot evaluate) + L34 R7. §13.6 already present at L35-46. §3.8 hard gates remain mechanical when fields ARE present (per universal principle Pass-1 condition (iv)).
- **Pass 4 — descriptor agents (~50 min):**
  - `network_effect_agent.py` v0.2 → v0.3: ADD §13.6 block (currently absent); soften L80 / L94-95 / L104-105 caps (descriptor not gate but currently mechanical).
  - `valuation_agent.py` v0.1 → v0.2: ADD §13.6 block (currently absent); soften L22 / L28. Coordinate with PM L67 buy-gate dependency on `valuation_assessment ∈ {attractive, fair}` — that dependency is now scoped to "when fields present" per Pass-1 hard-veto (iv).
- **Pass 5 — risk fine-tune (~10 min):**
  - `risk_agent.py` v0.6 → v0.7: scope L52-53 R7 NULL-BLOCK to candidate_type=quality_long ONLY, OR replace with advisory acknowledge-and-reduce-confidence pattern. §13.6 already present (3 hits).

After all 5 passes, single-ticker AKAN MUST-FIX 2/3 LLM verify (~$0.30) acts as integration test. Each pass independently testable via packet dry-run (zero cost) before any LLM verify.

### 14.11 Standing Rule 4 + RULES.md status

- No RULES.md modification in this pass.
- §13.6.SOFT_REASONING (RULES.md §13.6, codified 2026-05-02 v0.8.6 → v0.8.7) provides the framework; Pass-2 extends its in-prompt scope to surge_short_agent.py which previously had no §13.6 reference at all.
- §5.17 (RULES.md §5.17, codified 2026-05-02 v0.8.5) already lifted §5.13(d) information_integrity_veto for surge_short. Pass-2 applies the same advisory framing to §5.13(b) and §5.13(e) per the universal soft-veto principle.
- §8 R7 spirit (don't fabricate values for missing blocks) retained; mechanical fail-close action removed (same pattern as Pass-1 PM agent).
- `rule_version` literal unchanged: still `v0.8.7_ust_fixed_income` at all 9 src/ authoritative sites.

### 14.12 Verdict

**Pass 2 complete.** Surge_short agent now encodes the bear-thesis-by-absence reasoning pattern explicitly via the new top-block, with structural absence promoted from "fail-close trigger" to "potential bear-thesis support." 3-condition mechanical hard-veto set (sleeve_cap, position_cap, lookahead+hindsight) clearly delimited. Hash unchanged. Dry-run packet integrity preserved. Ready for Pass 3 (prelude agents).

## §15 Combined Passes 3+4+5 — prelude + descriptor + risk fine-tune (2026-05-03)

### 15.1 Scope

Combined refactor of 5 remaining agents to extend the universal soft-veto principle:
- Pass 3a: `narrative_event_agent.py` (v0.4 → v0.5_2026_05_03_universal_softveto)
- Pass 3b: `alt_data_verification_agent.py` (v0.4 → v0.5_2026_05_03_universal_softveto)
- Pass 3c: `fundamental_agent.py` (v0.4 → v0.5_2026_05_03_universal_softveto)
- Pass 4a: `network_effect_agent.py` (v0.2 → v0.3_2026_05_03_universal_softveto)
- Pass 4b: `valuation_agent.py` (v0.1 → v0.2_2026_05_03_universal_softveto)
- Pass 5: `risk_agent.py` (v0.6 → v0.7_2026_05_03_universal_softveto)

`quality_long_agent.py` deliberately NOT modified — already aligned per audit; long-side mechanical fail-close on missing fundamentals is intentional per §3.8 carve-out scope.

### 15.2 Per-pass change log

**Pass 3a — narrative_event_agent.py:** added top-block UNIVERSAL SOFT-VETO PRINCIPLE + 1-condition hard-veto list; replaced FAIL-CLOSED block with reasoning-permitting ASSESSMENT GUIDANCE; replaced R7 NULL-BLOCK clause with 5-step soft protocol distinguishing wired-empty vs adapter-unavailable absence. Anti-hindsight, alt-data emphasis, NDI input, §13.6 block all retained.

**Pass 3b — alt_data_verification_agent.py:** added top-block + §10.7 coordinated_campaign_warning carve-out preserved as advisory descriptor; replaced FAIL-CLOSED with VERDICT GUIDANCE (narrative_contradicted permitted on structural absence for surge_short); rewrote ALTERNATIVE DATA EMPHASIS with case-1/case-2 source_flag distinction; loosened data_quality_warning + pollution_risk_level invariants from mechanical collapse to advisory descriptors; replaced R7 with 5-step soft protocol.

**Pass 3c — fundamental_agent.py:** added top-block with §3.8 PRESERVED-WHEN-PRESENT carve-out (§3.8 mechanical when fields PRESENT, agent reasons when absent) AND REPORT NUMERIC VALUES FAITHFULLY guidance (negative/zero/null are real findings); replaced FAIL-CLOSED with §3.8 GATE EVALUATION block (mechanical-when-present, soft-on-absence, candidate-type-aware); replaced R7 with 5-step protocol.

**Pass 4a — network_effect_agent.py:** added §13.6 + universal-soft-veto top-block (was absent — §13.6 not previously referenced); reframed ANTI-POLLUTION DISCIPLINE (R-NETWORK-09) and CLASSIFICATION CEILINGS as SEMANTIC anchors of label-meaning, not mechanical vetoes; replaced FAIL-CLOSED with semantic-ceiling guidance; replaced R7 with 5-step protocol. The descriptor framing (R-NETWORK-04) preserved — agent still does NOT gate buy decisions.

**Pass 4b — valuation_agent.py:** added §13.6 + universal-soft-veto top-block; **CRITICAL** insertion of RATIO-UNDEFINED vs FIELD-ABSENT semantic distinction — case (a) underlying input present with negative/zero value (real "very_expensive" finding) vs case (b) field genuinely absent (data gap); replaced FAIL-CLOSED with VALUATION ASSESSMENT GUIDANCE; replaced R7 with case-aware 5-step protocol. This is the highest-leverage prelude change for surge_short pool — preserves the bear-thesis signal of negative-earnings tickers.

**Pass 5 — risk_agent.py:** EXTENDED §13.6.SOFT_REASONING from surge_short-only to UNIVERSAL across both candidate types — same EXECUTION-RISK-vs-THESIS-CONSISTENT axis applied to both surge_short AND quality_long. Replaced unconditional R7 NULL-BLOCK trip with per-block reasoning protocol: decision_time_discipline absent → trip (PIT non-negotiable); price_snapshot absent → trip (sizing); information_integrity / fundamental_snapshot absent → advisory only.

### 15.3 Verification matrix

| Check                                                | Expected                                       | Actual                                         | Status |
|------------------------------------------------------|------------------------------------------------|------------------------------------------------|--------|
| Regression matrix 30 cells (3 tickers × 2 cands × 5) | 30/30 schema-pass, 30/30 cache-hit-on-2nd      | 30/30, 30/30                                   | PASS   |
| Byte-identical packet hash regression                | hash stable + matches frozen baseline          | both true                                      | PASS   |
| AAPL frozen-baseline hash                            | `sha256:04f45e2c...4e14bc`                     | `sha256:04f45e2c...4e14bc`                     | PASS   |
| AKAN dry-run status counts                           | 12 ok / 9 not_evaluated / 4 data_unavailable / 1 insufficient_evidence (26 total) | 12 / 9 / 4 / 1 (26)        | PASS   |
| PROMPT_VERSION bump — narrative_event                | `v0.5_2026_05_03_universal_softveto`           | confirmed                                      | PASS   |
| PROMPT_VERSION bump — alt_data_verification          | `v0.5_2026_05_03_universal_softveto`           | confirmed                                      | PASS   |
| PROMPT_VERSION bump — fundamental                    | `v0.5_2026_05_03_universal_softveto`           | confirmed                                      | PASS   |
| PROMPT_VERSION bump — network_effect                 | `v0.3_2026_05_03_universal_softveto`           | confirmed                                      | PASS   |
| PROMPT_VERSION bump — valuation                      | `v0.2_2026_05_03_universal_softveto`           | confirmed                                      | PASS   |
| PROMPT_VERSION bump — risk                           | `v0.7_2026_05_03_universal_softveto`           | confirmed                                      | PASS   |
| `rule_version` literal at 9 src/ sites               | unchanged at `v0.8.7_ust_fixed_income`         | 9/9 unchanged                                  | PASS   |
| 7 backups created (.bak_pre_universal_softveto_20260503) | 7 files                                    | 7 files (incl. PM + surge_short from prior passes) | PASS |

### 15.4 Hash invariance proof

Prompts are cache-key inputs only (not in packet hash path). Verified: hash `sha256:04f45e2c...` unchanged across 6 prompt edits this pass (cumulative 7 across all 3 days' work since the finalizer-text fix relock).

### 15.5 Standing Rule 4 + RULES.md status

- No RULES.md modification this pass.
- §13.6.SOFT_REASONING (codified 2026-05-02 v0.8.6→v0.8.7) provides the framework; this pass extends its in-prompt scope to ALL agents.
- §3.8 hard fundamental gates retained as the ONE structural mechanical gate for fundamental_agent + PM (when fields PRESENT).
- §5.17 surge-short integrity-veto exception remains; Risk's universal-soft-veto extension subsumes the prior surge-short-only carve-out for §5.13(b) and §5.13(e) by extending the same advisory framing to quality_long.
- §8 R7 spirit (don't fabricate values) retained as anti-hallucination dead rule across all 6 refactored agents; mechanical fail-close action removed.
- `rule_version` literal unchanged: still `v0.8.7_ust_fixed_income` at all 9 src/ authoritative sites.

### 15.6 Verdict

**Combined Passes 3+4+5 complete.** All 7 multi-agent prompts (PM, surge_short, narrative_event, alt_data_verify, fundamental, network_effect, valuation, risk) now encode the 2026-05-03 universal soft-veto principle. Anti-hallucination R7 spirit preserved across the board; mechanical fail-close action removed wherever it was creating false-negative collapse on null-block absence. Bear-thesis-by-absence reasoning pattern (the surge_short sleeve's primary alpha source) now fires consistently across the prelude. Hash unchanged. AKAN dry-run integrity preserved. Cache invalidation triggered via 7 PROMPT_VERSION bumps; old entries remain on disk for audit/replay. End-state ready for D7 finalizer integration testing.

## 16. LAYER 2 — AKAN SINGLE-TICKER LLM VERIFY (2026-05-03)

### 16.1 Scope

Real-LLM verification (Layer 2 of 4-layer risk-limited validation) of the post-2026-05-03 prompt refactors against AKAN 2026-04-28 surge_short. Goal: prove the 6 prompt refactors hold under real Haiku 4.5 calls — specifically that the universal soft-veto principle eliminates mechanical-veto-on-null-block collapse and surfaces substantive bear-thesis reasoning.

### 16.2 Pipeline entry point

`run_all_agents_for_candidate(evidence_packet, candidate_type="surge_short", provider, cache, agent_mode="multi", topology="pipeline")` from `src/agents/runner.py:1026` — the SAME entry point used by `scripts/portfolio_5day_2026_04_27_to_05_01.py:349` (the 5-day replay's surge path). LIVE_ADAPTERS_TUPLE imported from the replay script (single source of truth, 9 adapters).

Provider: `AnthropicProvider(default_model=HAIKU_MODEL, timeout_s=300)` — direct construction with extended 300s per-call timeout to give PM headroom on long generations (default 60s tripped on first attempt).

Script: `scripts/_llm_verify_akan_20260503.py` (archived after run to `data/decisions/llm_verify_script_archive_20260503.py`).

### 16.3 Cost actual vs budget

| Metric            | Expected | Hard cap | Actual            | Status |
|-------------------|----------|----------|-------------------|--------|
| Total LLM cost    | $0.30    | $0.50    | **$0.5181**       | BREACH (+$0.018, +3.6%) |
| Total input tok   | n/a      | n/a      | 286,336 (8 calls) | over expected; not capped |
| Total output tok  | n/a      | n/a      | 47,466            | inflated by 3 PM retries × 8192 |
| Wall time (run)   | <300s    | <300s    | 439.6s            | over (PM × 3 retries) |
| Packet gen        | <15s     | <15s     | 9.9s              | OK |

Cost overage entirely attributable to the 3 PM retry attempts (each ~$0.085-0.092). Single PM call would have been ~$0.09 → total ~$0.34, comfortably within $0.50.

### 16.4 Per-agent decisions + veto status (5 of 6 succeeded)

| Agent | Prompt version | Output | Decision/verdict |
|-------|---------------|--------|------------------|
| narrative_event | v0.5_2026_05_03_universal_softveto | OK | value_creation_assessment="unclear", evidence_sufficiency="insufficient", catalyst_specificity_score=0.0, vague_narrative_flag=true |
| alt_data_verify | v0.5_2026_05_03_universal_softveto | OK | verdict="contradicted", recommendation_to_pm="challenge_thesis", can_use_as_primary_signal=false |
| fundamental | v0.5_2026_05_03_universal_softveto | OK | hard_gates_pass=false, fundamental_assessment="argues_against_long", failed_gates=[6 specific] |
| surge_short | v1.2_2026_05_03_universal_softveto | OK | recommended_action="short", short_thesis_status="valid", evidence_sufficiency="sufficient", initial_position_pct=0.75 |
| risk | v0.7_2026_05_03_universal_softveto | OK | information_integrity_veto=false, NO veto tripped, comprehensive rule_engine_path |
| pm | v0.6_2026_05_03_universal_softveto | **FAIL** | JSON parse error all 3 attempts; runner returned stub fallback `decision="veto"` |

### 16.5 PM regression diagnostic

Failure mode: PM output truncated mid-JSON on every retry. Each PM call hit `output_tokens=8192` exactly (the hardcoded `max_tokens=8192` cap at `src/agents/runner.py:549`). This cap was already known marginal — the inline comment at runner.py:556-559 acknowledges "PM observed a ~20% JSON-parse failure rate during the D4 5-day mini-smoke (long output occasionally produced a missing comma at ~26KB)". The universal-soft-veto refactor lengthened the v0.6 PM prompt's expected output (more rationale fields, more advisory descriptors to surface, 60+ schema fields) — pushing failure rate from ~20% to **100% on this packet**.

Per-attempt evidence:
- Attempt 1: parse error at line 238 col 4 (char 27,112), out=8192 tokens
- Attempt 2: parse error at line 239 col 4 (char 25,012), out=8192 tokens (cache_read=5791 → retry clarifier added)
- Attempt 3: parse error at line 189 col 6 (char 19,653), out=8192 tokens

Final PM record: `_pm_json_retry.recovered=False`, `decision="veto"` (deterministic-stub fallback marker: `reason="Deterministic stub. Real LLM not yet connected."`). The decision="veto" outcome is NOT a real PM verdict — it's a fail-safe placeholder when all retries exhausted.

### 16.6 §13.6 / soft-veto reasoning patterns visible (qualitative excerpts)

**narrative_event reasoning_summary** (excerpt): "AKAN exhibits a 253.8% 5-day price surge ... with no identifiable news catalyst, SEC filing, or public disclosure within the allowed_data_cutoff window. **STRUCTURAL ABSENCE PATTERN**: (1) news_event_summary.items is empty — 20 items dropped because publishedDate > cutoff; polygon_news returned 0 rows. (2) filing_confirmation.filing_index.sec_edgar returned 0 8-K filings in last 90 days. (3) No press release or earn[ings]..."

**alt_data_verify reasoning_summary** (excerpt): "AKAN exhibits extreme price volatility ... coupled with multiple recent stock splits and deeply negative fundamentals (net_margin_pct_ttm=-619.52%, eps=-41.24, revenue=0). ... All industry-specific alternative-data adapters are unwired or unavailable. No news coverage. No insider activity. No sentiment data. **The structural absence of corroborating evidence acro[ss]**..." → verdict="contradicted" (NOT collapsed to "unverifiable" + "needs_more_evidence")

**fundamental reasoning_summary** (excerpt): "AKAN presents a critical fundamental assessment challenge due to stale and incomplete data combined with severe operational distress signals. ... revenue=0, net_income=-815,000, operating_income=-1,490,756, eps=-41.24, free_cash_flow=-375,604 ... **The §3.8 hard gates CANNOT be mechanically evaluated because the required TTM fields are ABSENT**" → still emitted hard_gates_pass=false on present nonpositive evidence (case-aware reasoning per Pass 3c §3.8-when-present).

**surge_short audit_rationale** (excerpt): "AKAN surged 253.8% in 5 days with zero revenue, negative cash flow, multiple reverse splits, no 8-K filing, no insider activity, no news within cutoff, and no analyst coverage. **The structural absence of SEC disclosures, insider activity, and news relative to the extreme price move is itself a bear-thesis signal under §13.6 soft-veto reasoning.** Valuation is distorted (PE=-0.889, net margin=-619.52%) and not justified..." → recommended_action="short", initial_position_pct=0.75 (within v1.2 surge-short threshold defaults of ≤1.0).

**risk rule_engine_path** (excerpt — exemplary): "candidate_type=surge_short → §5.17 integrity-veto exception applies", "information_integrity_assessment.use_as_primary_signal_allowed=false → NOT a veto for surge_short; absence of T1/T2 corroboration is bear-thesis evidence", "**missing_data (borrow_cost, shares_available, short_interest, liquidity) → §13.6 soft-veto reasoning: execution-risk inputs for PM, not sizing gates; do NOT trip veto**", "**structural_absence (news, 8-K, insider, sentiment) → §13.6 soft-veto reasoning: for surge_short, absence is informative (bear-thesis evidence), not evidence-insufficiency veto**", "no hard-veto conditions breached → advisory output with cover_decision_inputs for PM". The Risk v0.7 universal-soft-veto extension performed exactly as intended.

### 16.7 PASS criteria evaluation

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| A | No mechanical veto on null/missing blocks (Risk §5.13(b)/(e)) | **PASS** | Risk veto_conditions_evaluated all tripped=false; rule_engine_path explicitly cites §13.6 soft-veto reasoning for missing data |
| B.1 | §13.6 reasoning visible in ≥1 prelude agent | **PASS** | All 4 prelude+sleeve agents cite structural-absence reasoning explicitly |
| B.2 | Risk advisory discusses what AKAN's structural gaps mean | **PASS** | advisory_notes + rule_engine_path comprehensive (~1100 chars) |
| B.3 | PM aggregates substantively (not mechanical inheritance) | **FAIL** | PM output never produced — JSON truncated at 8192 max_tokens cap on all 3 retries; stub fallback "veto" returned |
| C.1 | Total tokens < 80k input cap | **PASS** | Per-call max in_uncached=44,459 (PM); well under 80k. Total cumulative inflated by retries but never per-call cap-hit. |
| C.2 | Total cost < $0.50 hard cap | **FAIL** | $0.5181 vs $0.50 cap; +$0.018 overage attributable entirely to 3 PM retries × ~$0.086 each |
| C.3 | Wall time < 5 min | **FAIL** | 439.6s (~7.3 min) — PM retries × 75-77s each |
| D.1 | Pipeline completes end-to-end | PARTIAL | Envelope returned; PM is a stub fallback, not a real LLM output |
| D.2 | All 5 (real) agents return non-error outputs | **PASS** | narrative + alt_data + fundamental + surge_short + risk all returned full schema-valid outputs |
| D.3 | Final PM decision is legal enum value | TECHNICALLY PASS | "veto" is a legal value but it's the stub-fallback default, not real PM reasoning |
| E | AKAN-specific reasoning quality | **PASS** | All 5 agents cite specific AKAN evidence (253.8% surge, eps=-41.24, revenue=0, multiple stock splits, no GitHub/insider/news) |

### 16.8 Verdict

**LAYER 2 — PARTIAL PASS / PM REGRESSION FLAGGED.** The 5 prompt refactors that landed in Passes 2-5 (surge_short v1.2, narrative v0.5, alt_data v0.5, fundamental v0.5, risk v0.7) ALL performed exactly as designed under real LLM call. The bear-thesis-by-absence reasoning pattern fires correctly across the prelude+sleeve+risk; no mechanical veto-on-null-block collapse anywhere; structural absence is treated as informative bear-thesis evidence; risk emits advisory output with NO hard-veto trip (§5.17 + universal-soft-veto extension working as intended).

**HOWEVER**: Pass 1 PM v0.6 regressed at the JSON serialization layer. The PM prompt's expected output now exceeds the runner-hardcoded `max_tokens=8192` cap on at least some packets (100% failure rate observed on AKAN 2026-04-28 across 3 retry attempts). The known ~20% baseline failure rate from D4 has escalated to total failure for the AKAN packet. Root cause is NOT a logical regression in the v0.6 prompt — the PM is reasoning correctly and trying to emit a comprehensive 60+ field JSON; it simply runs out of tokens before closing the structure.

### 16.9 Recommended fix (DIAGNOSTIC ONLY — DO NOT proceed to Layer 3 without resolving)

Two non-mutually-exclusive options:

1. **Increase `max_tokens` at `src/agents/runner.py:549`** from 8192 → 16384 (or 12000). Cost impact: PM output cost is ~$5/MTok at Haiku 4.5; doubling output cap roughly doubles per-call output cost from ~$0.04 to ~$0.08 on long generations. Cleanest fix; preserves prompt verbosity.

2. **Tighten PM v0.6 prompt** to instruct PM to keep `audit_rationale` ≤ 400 chars (already advised but apparently violated), `decision_log` ≤ 300 chars, etc. Prompt-level concision discipline. Risk: discards information PM was generating because the universal-soft-veto refactor explicitly asked it to surface advisory reasoning.

Recommend (1) as immediate fix; (2) as longer-term hygiene. After the fix, re-run AKAN single-ticker verify; if PM produces parseable JSON with substantive aggregation reasoning, declare Layer 2 PASS and proceed to Layer 3 (CUE single-ticker, ~$0.25).

### 16.10 Result artifacts

- Dumped JSON: `data/decisions/llm_verify_akan_20260428.json` (211,658 bytes; full evidence packet + agent_run_envelope + ledger calls)
- Archived script: `data/decisions/llm_verify_script_archive_20260503.py`
- Stdout dump retained in conversation transcript (74 lines)

### 16.11 Standing Rule 4 + cost ledger

- No RULES.md modification; no prompt edits in this layer (verify-only).
- Layer 2 actual: $0.5181 (vs $0.30 expected, $0.50 hard cap → BREACHED by $0.018).
- Cumulative session cost (running tally per user's $400 annual budget): ~$16.52 (was ~$16.00 per current memory record). Comfortably under annual cap.
- Standing instruction "STOP if cost exceeds $0.50" honored — script exited code 2 on the budget check; no Layer 3 auto-proceed.

## 17. PM MAX_TOKENS BUMP (2026-05-03)

### 17.1 Root cause

Layer 2 AKAN single-ticker LLM verify (§16) revealed PM v0.6 universal-soft-veto refactor produces JSON output that exceeds the runner-hardcoded `max_tokens=8192` cap on at least the AKAN packet. All 3 PM retry attempts hit `output_tokens=8192` exactly → output truncated mid-JSON → `json.loads` failed → final PM record was a deterministic-stub fallback (`decision="veto"`, `_pm_json_retry.recovered=False`).

Per-attempt cost waste at 8192 cap (Haiku 4.5 output @ $5/MTok):
- Attempt 1: 8192 out tok × $5/MTok = ~$0.041 (plus input cost ~$0.05) = ~$0.092
- Attempt 2: same → ~$0.086 (cache_read=5791 reduced input cost slightly)
- Attempt 3: same → ~$0.086
- Total wasted: **~$0.26** on attempts that produced unparseable output

This was a known marginal failure mode (runner.py:556-559 inline comment cites ~20% PM JSON-parse failure rate from D4); the universal-soft-veto refactor escalated it to 100% on AKAN.

### 17.2 Fix

Single-line config edit:
- File: `src/agents/runner.py`
- Line: 549 (single occurrence in live tree; .bak files retained at the historical value for audit)
- OLD: `max_tokens=8192,`
- NEW: `max_tokens=16384,`

Rationale: max_tokens is a runtime config, NOT packet content (no hash impact). Doubling the output cap gives PM headroom to close its JSON structure under the universal-soft-veto refactor's verbose schema requirements (60+ fields, advisory descriptors, rule_engine_path, decision_log, audit_record, etc.).

### 17.3 Hash verify

Regression matrix `tests/test_regression_matrix.py`:
- 30/30 schema-pass
- 30/30 cache-hit-on-2nd-run
- AAPL baseline hash: `sha256:04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc` UNCHANGED

Confirms max_tokens change is invisible to packet hash, as expected.

### 17.4 Backup path

`src/agents/runner.py.bak_pre_max_tokens_bump_20260503` (single-line diff vs live tree).

### 17.5 Cost projection (per-ticker single LLM verify)

| Scenario | Input cost | Output cost | Total | Notes |
|----------|-----------|-------------|-------|-------|
| Pre-fix, all 3 PM retries fail (Layer 2 actual) | ~$0.26 | ~$0.26 | **$0.52** | $0.26 wasted on truncated output |
| Post-fix, single PM call succeeds (target) | ~$0.21 | ~$0.10 | **~$0.31** | One PM call at ≤16384 out tok ($0.08), 5 prelude/sleeve/risk same as before |
| Post-fix, single PM call but lengthy output | ~$0.21 | ~$0.14 | **~$0.35** | PM uses ~12-14k out tok of new headroom |

Net savings vs Layer 2 actual: ~$0.17-$0.21 per single-ticker verify when PM converges on first attempt.

### 17.6 Pending

- Re-run AKAN single-ticker LLM verify (rebuild `_llm_verify_akan_20260503.py` from archive `data/decisions/llm_verify_script_archive_20260503.py` or invoke directly from archive). Cost expected ~$0.31, hard cap unchanged at $0.50.
- IF PM produces parseable JSON with substantive aggregation reasoning → declare Layer 2 PASS, proceed to Layer 3 (CUE single-ticker, ~$0.25).
- IF PM still truncates → escalate to v0.6 PM prompt concision pass (Pass 1.1).

### 17.7 Standing Rule 4 + ledger

- No RULES.md modification.
- No prompt edits.
- $0 LLM cost (config edit + regression matrix only; matrix uses deterministic stub).
- Cumulative session cost unchanged from §16: ~$16.52.

## 18. LAYER 2 RERUN — AKAN AFTER MAX_TOKENS BUMP (2026-05-03)

### 18.1 Setup

- Restored archive `data/decisions/llm_verify_script_archive_20260503.py` → `scripts/_llm_verify_akan_rerun_20260503.py`
- Single edit: output path `llm_verify_akan_20260428.json` → `llm_verify_akan_20260428_rerun.json` (preserve prior dump for audit)
- Launched in background mode (no Bash 7-min hard timeout); polled log file every 15s for completion marker
- runner.py:549 max_tokens=16384 active; ANTHROPIC_HARD_STOP_USD=0.50 enforced; AnthropicProvider timeout_s=300

### 18.2 Result vs prior run

| Metric                   | Layer 2 (§16, pre-fix) | Layer 2 RERUN (post-fix) | Delta |
|--------------------------|------------------------|--------------------------|-------|
| Total cost               | $0.5181 (BREACH)       | **$0.2964**              | -$0.2217 (-43%) |
| Wall time (run)          | 439.6s                 | **237.0s**               | -202.6s (-46%) |
| LLM calls made           | 8 (5 prelude + 3 PM)   | **6 (5 prelude + 1 PM)** | -2 retries |
| PM JSON parse status     | FAIL all 3 attempts    | **PASS first attempt**   | regression resolved |
| PM output tokens         | 8192 × 3 (capped)      | **6589 (under 16384)**   | -1603 vs cap |
| PM `_pm_json_retry`      | retried, recovered=False | **NOT PRESENT** (no retry) | -- |
| PM `is_stub_fallback`    | True ("Deterministic stub") | **False** (real LLM)    | -- |
| Final PM decision        | "veto" (stub default)  | **"short"** (substantive) | -- |

### 18.3 Per-agent token + cost breakdown

| Agent | in_uncached | out | cost | latency |
|-------|-------------|-----|------|---------|
| NarrativeEventAgentOutput | 22,301 | 3,283 | $0.0387 | 32.9s |
| AltDataVerificationAgentOutput | 21,986 | 4,585 | $0.0505 | 41.3s |
| FundamentalAgentOutput | 22,327 | 2,398 | $0.0343 | 22.1s |
| SurgeShortAgentOutput | 18,986 | 2,952 | $0.0394 | 30.0s |
| RiskAgentOutput | 28,190 | 4,814 | $0.0582 | 46.3s |
| **PMAgentOutput** | **35,097** | **6,589** | **$0.0753** | **64.4s** |
| **TOTAL** | **148,887** in_unc | **24,621** out | **$0.2964** | -- |

All 6 agents fresh (no cache hits): packet hash `7dd63052fca96c2353db37153edd08c32e34a05bf7ce1d95e76202cea8fd1219` differs from §16's hash `4e7af16fcc91baa9ea3970cedd664a687a84158e3d1728980af2201083e903a3`. AKAN packet generation pulls live data (FMP/FRED) and is non-deterministic across runs — cache-replay strategy doesn't apply for AKAN. (FRED 500'd on DGS20/T10Y3M/BAMLH0A0HYM2/T10YIE this run; macro_regime fell back gracefully.)

### 18.4 PM substantive output (the keystone validation)

`pm.decision = "short"` (matches surge_short sleeve agent's recommended_action)
`pm.position_size_pct = 0.75` (matches surge_short.initial_position_pct=0.75 — substantive aggregation, not arbitrary)
`pm.immutable_decision_flag = True`
`pm.is_stub_fallback = False`

**`pm.reason` (full text):**
> "AKAN surged 253.8% (5d) on zero revenue, -$815k net income, -41.24 EPS, three reverse splits in 6 months. No disclosed catalyst (zero 8-K, zero pre-cutoff news, zero insider Form 4, no GitHub). Absence of corroboration IS bear thesis. Recommend short 0.75% with strict monitoring of 2026-04-29 earnings and Form 4 activity."

**`pm.rule_engine_path` (exemplary, first 5 steps):**
1. "§5.17 SURGE-SHORT INTEGRITY-VETO EXCEPTION applied: absence of T1/T2/T3 corroboration is NOT grounds for information_integrity_veto."
2. "§13.6 SOFT_REASONING applied: structural absence of corroborating evidence (8-K, insider activity, alt-data) is internally consistent with bear thesis; do NOT auto-trip veto."
3. "§4.8 surge-short position_size_cap: 0.75% initial position < 5% per-position cap; PASS."
4. "§4.8 surge-short sleeve_cap: 0.75% < 10% sleeve cap; PASS."
5. "§5.13(c) lookahead_data_used: data_after_cutoff_used=false; lookahead_safe=true; PASS."

PM is now correctly synthesizing the universal-soft-veto reasoning from the prelude + sleeve + risk agents into a substantive aggregated decision with explicit rule-citation trail. This was the core regression flagged in §16; resolved.

### 18.5 PASS criteria evaluation (rerun-specific)

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| A | PM produced parseable JSON this run | **PASS** | `_pm_json_retry` field absent; first-attempt success |
| B | PM decision substantive, not stub fallback | **PASS** | decision="short", position=0.75% (matches sleeve), reason cites AKAN-specific evidence; is_stub_fallback=False |
| C | PM rationale cites §13.6 / universal-soft-veto / structural-absence | **PASS** | rule_engine_path explicitly cites §5.17, §13.6, §4.8, §5.13(c); reason text uses "Absence of corroboration IS bear thesis" |
| D | Total cost < $0.50 hard cap | **PASS** | $0.2964 (well under) |
| E | No exceptions / tracebacks | **PASS** | clean exit code 0; only advisory FMP/FRED 404/500 (graceful fallback) |
| F | PM output tokens < 16384 cap | **PASS** | 6589 / 16384 = 40% utilization; comfortable headroom |

### 18.6 Verdict

**LAYER 2 — PASS DECLARED.** The max_tokens 8192→16384 fix at runner.py:549 fully resolves the PM v0.6 JSON-truncation regression flagged in §16. PM now produces substantive JSON on first attempt with output token utilization at 40% of the new cap. The universal-soft-veto refactor (Passes 1-5) is end-to-end-verified under real Haiku 4.5 calls: prelude+sleeve+risk reason about structural absence as bear-thesis evidence, and PM aggregates substantively with explicit §13.6 + §5.17 + §4.8 + §5.13 rule citations. Cost dropped from $0.5181 to $0.2964 (-43%); wall time dropped from 439.6s to 237.0s (-46%).

### 18.7 Carry-forward (DO NOT auto-proceed)

User decision required:
- **Layer 3 (CUE single-ticker, ~$0.25, ~5 min)**: verifies CUE 5/1 +128.97% gap-up case (the trade with known ground truth where user shorted). Adds confidence specifically on the trade we have outcome data for. Tradeoff: extra $0.25 + 5 min before Layer 4.
- **Skip to Layer 4 (5-day full replay, ~$3-5, ~70 min)**: AKAN already proves universal soft-veto end-to-end across the full 6-agent chain. Risk: if 5-day produces 0 CUE trade or unexpected behavior, ambiguity between data-side issue vs PM-specific issue.

Recommend: proceed to Layer 3 (CUE single-ticker) — extra $0.25 buys ground-truth verification on a known trade before committing $3-5 to full replay. But user choice.

### 18.8 Result artifacts

- Dumped JSON: `data/decisions/llm_verify_akan_20260428_rerun.json` (full evidence packet + agent_run_envelope + ledger)
- Stdout log: `data/decisions/akan_rerun_stdout_20260503.log` (75 lines)
- Archived script: `data/decisions/llm_verify_akan_rerun_script_archive_20260503.py`
- Prior run dump retained: `data/decisions/llm_verify_akan_20260428.json` (for §16 vs §18 audit)

### 18.9 Standing Rule 4 + ledger

- No RULES.md modification.
- No prompt edits this layer (rerun-only validation).
- Layer 2 RERUN actual: $0.2964 (vs $0.35 expected, $0.50 hard cap → comfortably within).
- Cumulative session cost: ~$16.82 (was ~$16.52 per §17.7).
- Annual budget remaining: ~$383 of $400.

## 19. PASS 6 — COMPREHENSIVE PRE-REPLAY FIX (2026-05-03)

### 19.1 Scope

Comprehensive pre-replay fix bundle closing 5 wiring/prompt gaps identified in the pre-flight audit. UNATTENDED. $0 LLM cost (verification + dry-run only). All 5 gaps must land cleanly.

### 19.2 Per-gap deliverables

**Gap 1 — §4.8 fixed 0.5% sizing enforcement.** Authored `docs/SURGE_SHORT_THRESHOLDS.md` (~30 LOC; doc-only, not in packet hash path). Bumped `surge_short_agent.py` v1.2 → v1.3_2026_05_03_pass6_sizing_sector with explicit 0.005 mandate replacing the TBD-threshold reference. Bumped `pm_agent.py` v0.6 → v0.7_2026_05_03_pass6_comprehensive with POSITION SIZE VALIDATION block enforcing PM mechanical override to 0.005 for new surge_short entries. Added defensive runtime clamp at `update_position_from_trigger` (replay script L599-area) — clamps any non-0.005 emit to 0.005 with audit log entry. Triple enforcement (prompt + PM aggregator + runtime clamp).

**Gap 2 — Friday FI review wiring.** New module `src/portfolio/fi_review.py` (~210 LOC) with `run_fi_review` + `apply_ust_decision_to_state` + minimal packet builder + FRED yield-curve safe lookup. Uses real `build_ust_position` / `compute_ust_pv` / `UST_TENOR_DAYS` from `src/portfolio/ust_position.py`. New runner entry point `run_pm_for_fi_review` wraps single PM call with candidate_type='fi_review'. PM prompt extended with FI REVIEW MODE section (no hardcoded duration ceilings, advisory reasoning only, hard veto only on §5.13(c) lookahead + §5.13(a) sleeve-cap). Replay daily-loop wiring fires Friday + is_trading_day check; calls run_fi_review; applies each ust_decision to state.

**Gap 3 — §10.14.R-COVER-07 cover decision wiring.** New module `src/portfolio/cover_eval.py` (~170 LOC) with `run_cover_evaluation` + `apply_cover_to_state` + position summarizer + valuation-strip discipline (`COVER_STRIPPED_KEYS = ('valuation_snapshot', 'valuation_assessment')` defensively removed before LLM call). New runner entry point `run_cover_pipeline` wraps the abbreviated 3-agent sequence (alt_data_verify + risk + pm) all with candidate_type='surge_short_cover' for cache-key isolation. Three prompts extended with COVER MODE sections: alt_data_verify (R-COVER-02 catalyst/volume/staleness focus + §10.15 R-NETWORK-07 cross-reference); risk (R-COVER-02 inputs + explicit "borrow trajectory unobservable in v0.8.7" / "shares-available trend unobservable" / "R-COVER-04 unobservable in v0.8.7" honesty per user 2026-05-03 directive); pm (substantive cover reasoning framework with NO hardcoded thresholds; valuation + macro + fundamental + network_effect + narrative_event explicitly excluded; §10.14.R-COVER-03 empirical reference numbers REMOVED from prompt text). Replay daily-loop wiring fires daily 12:30 ET for held shorts.

**Gap 4 — §30 QL exit/rebalance framework.** Added §30 (6 sub-rules) to `docs/RULES.md` per user 2026-05-03 pre-approval (additive, Standing Rule 4 satisfied). doc v2.6 → v2.7. Section index updated. Comprehensive Pass-6 changelog entry added. Two QL hold paradigms: long_term (§10.15 network-effect + reasonable valuation) vs event_triggered (PM-decided profit-take timing). NO hardcoded thresholds, NO consecutive-week triggers, NO sleeve-cap-percentage triggers. New module `src/portfolio/ql_review.py` (~210 LOC) with `run_ql_friday_review` + `apply_ql_action_to_state` (handles trim partial vs exit full with proper P&L realization). New runner entry point `run_ql_review_pipeline` (4-agent: fundamental + valuation + risk + pm). PM prompt extended with QL REVIEW MODE section enforcing hold/trim/exit decision + entry_type_recognized + trim_pct + exit_trigger emit fields. Replay daily-loop wiring fires Friday + is_trading_day for held QL positions.

**Gap 5 — Sector-aware reasoning.** Replaced sector-specific bear-thesis examples in 6 agent prompts with the SECTOR-AWARE REASONING SEQUENCE: identify candidate sector → reason about sector-expected evidence footprint → COMPARE to actual packet → structural absence relative to SECTOR-EXPECTED is bear-thesis evidence (relative to UNRELATED is NOT signal). Examples list (Software/Tech, Pharma/Biotech, Industrial, Financial Services, Consumer/Retail, Energy/Mining, REIT) is illustrative reasoning patterns, NOT a hardcoded checklist. Per-agent customization: surge_short / pm / narrative_event focus on narrative + footprint match; alt_data_verify focuses on alt-data corroboration; fundamental focuses on §3.8 gates with sector-baseline awareness (pre-revenue biotech is sector-baseline, not always bear); network_effect frames the §10.15 three-layer framework as sector-agnostic with sector-weighted Layer 1 evidence types; valuation acknowledges sector-structural multiple differences (P/B + ROE for banks, P/FFO for REITs, pre-commercial pipeline for biotech). Eliminates the AKAN-pharma-flagged-for-no-GitHub failure mode.

### 19.3 Files touched

| file | category | LOC delta | status |
|---|---|---|---|
| docs/SURGE_SHORT_THRESHOLDS.md | NEW doc | +30 | OK |
| docs/RULES.md | additive §30 + changelog v2.7 | +25 | OK |
| src/agents/prompts/surge_short_agent.py | bump + L17-23 sector-aware + L56 sizing | +18 net | OK |
| src/agents/prompts/pm_agent.py | bump + L16 reference removal + 4 MODE sections + sector-aware | +95 net | OK |
| src/agents/prompts/risk_agent.py | bump + COVER MODE + sector-aware | +30 net | OK |
| src/agents/prompts/alt_data_verification_agent.py | bump + COVER MODE + sector-aware | +25 net | OK |
| src/agents/prompts/narrative_event_agent.py | bump + sector-aware | +15 net | OK |
| src/agents/prompts/fundamental_agent.py | bump + sector-aware | +18 net | OK |
| src/agents/prompts/network_effect_agent.py | bump + sector-aware (light touch) | +13 net | OK |
| src/agents/prompts/valuation_agent.py | bump + sector-aware (light touch) | +13 net | OK |
| src/portfolio/fi_review.py | NEW module | +210 | OK |
| src/portfolio/cover_eval.py | NEW module | +170 | OK |
| src/portfolio/ql_review.py | NEW module | +210 | OK |
| src/agents/runner.py | 3 new pipeline entry points | +160 | OK |
| scripts/portfolio_5day_2026_04_27_to_05_01.py | clamp + 3 wiring sections | +130 | OK |

### 19.4 Verification results

| check | status | evidence |
|---|---|---|
| Regression matrix 30/30 schema-pass | PASS | tests/test_regression_matrix.py output |
| Hash sha256:04f45e2c... unchanged | PASS | matches frozen baseline |
| AKAN dry-run packet integrity | PASS | 12 ok / 9 not_evaluated / 4 data_unavailable / 1 insufficient_evidence (26 total, baseline match) |
| 8 PROMPT_VERSION bumps | PASS | grep confirms all 8 at *_pass6_* |
| 3 new modules importable | PASS | python -c "from src.portfolio.{fi_review,cover_eval,ql_review} import ..." all clean |
| SURGE_SHORT_THRESHOLDS.md exists | PASS | docs/SURGE_SHORT_THRESHOLDS.md present |
| §30 in RULES.md | PASS | 13 §30.* hits including header + 6 sub-rules + cross-references |
| Replay daily loop new wirings | PASS | 10 hits for run_fi_review/run_cover_evaluation/run_ql_friday_review/§30.2/R-COVER-07/§27.16 |
| 9 src/ rule_version literals at v0.8.7 | PASS | macro_regime + runner + generator + macro.py + logic_audit + pnl_backtest + deterministic_stub (×3) all at v0.8.7; new modules carry it as default kwarg (advisory) |
| RULES.md doc v2.7 changelog | PASS | RULES.md L760+ document version updated; changelog entry added |
| Standing Rule 4 status | OK | §30 additive (6 new rules); user pre-approved 2026-05-03; no existing rule modified |
| NO HARDCODED THRESHOLDS in PM | PASS | 1 hit on PM L155 is NEGATIVE instruction ("DO NOT apply...") with quoted phrase as anti-pattern, not a positive encoding |
| NO HARDCODED THRESHOLDS in Risk | PASS | 0 hits |
| §10.14.R-COVER-03 empirical numbers in any prompt | PASS (REMOVED) | "1-2 days holding"/"10-30% retracement"/"2-4 weeks" zero hits in any live agent prompt |

### 19.5 Standing Rule 4 + ledger

- Additive §30 (6 sub-rules) — user pre-approved 2026-05-03 chat, recorded in v2.7 changelog row.
- §4.9 hold-forever language NOT modified — §30.1 supersedes its exit semantics for QL but the §4.9 reinvestment rule remains.
- §20.7 stop-loss placeholder NOT modified — §30.5 clarifies the gap remains for QL exits (drawdown is reasoning input only).
- No other RULES.md edits.
- $0 LLM cost (file edits + regression matrix + AKAN dry-run only; matrix uses deterministic stub; AKAN dry-run is no-LLM).
- Cumulative session cost: ~$16.82 unchanged from §18.9.

### 19.6 Backups created (11 files)

`*.bak_pre_pass6_20260503` for: surge_short_agent.py, pm_agent.py, risk_agent.py, alt_data_verification_agent.py, narrative_event_agent.py, fundamental_agent.py, network_effect_agent.py, valuation_agent.py, runner.py, scripts/portfolio_5day_2026_04_27_to_05_01.py, docs/RULES.md.

### 19.7 Cost projection for 5-day replay

D6 baseline was ~$2.66 across 8 surge_short triggers (~$0.33 / trigger entry pipeline). Pass 6 adds:
  - Daily cover-eval per held short × 4 days × 3-agent = ~$0.20-0.30/eval × N held shorts ≈ ~$3-6 over the 5-day window if 5-8 shorts open and persist
  - Friday FI review × 1 day × single PM call ≈ ~$0.10
  - Friday QL review per held QL × 1 day × 4-agent = ~$0.30/review × N held QL ≈ ~$1-2 if 5 QL positions opened on Monday

**Estimated 5-day replay total: ~$7-12** (vs D6 $2.66 entry-only; ~3-4× increase from cover/review activity). Hard-cap remains $15.00 in replay script. If cost spikes during run, `cost_check_or_abort` will trip cleanly.

### 19.8 Pending

- Layer 2.1 RERUN AKAN with Pass 6 prompts (verify PM cover-mode + sector-aware fires correctly under real LLM; ~$0.30 expected)
- Then Layer 4: 5-day full replay (~$7-12 expected, $15 hard cap)


## 20. LAYER 4 5-DAY REPLAY RESULT (2026-05-03)

First end-to-end real-money backtest after Pass 6.

**Wall-clock**: 4201.5s (~70 min). **Cost**: $3.8867 / $15 cap (26%). **Exit**: 0.
**Hash**: `6278036b...` (matches Pass 6 baseline). **NAV**: $1M flat all 5 days.

### 20.1 Per-day execution

| Day | Wall | Triggers | NAV | Notes |
|---|---|---|---|---|
| 04-27 (Mon) | 2432.8s | 5 QL pipelines fired | $1.0M | Only ARE completed; AVB/BRO/CDNS PM-timed-out; CINF input-cap-breached |
| 04-28 (Tue) | 3.4s | none | $1.0M | No surge candidates |
| 04-29 (Wed) | 389.7s | XTLB surge-short | $1.0M | PM-timed-out |
| 04-30 (Thu) | 415.0s | HCAI surge-short | $1.0M | PM-timed-out |
| 05-01 (Fri) | 959.6s | CUE+LABT surge + FI review | $1.0M | Both surge PM-timed-out; FI review fired with `decisions_count=0` (no forensic, undebuggable) |

### 20.2 LLM execution: 8 of 9 pipelines FAILED

errors.csv pattern:
- 7× `APITimeoutError` at ~181s (= 60s × 3 SDK retries on default `timeout_s`)
- 1× CINF: `Per-call input cap exceeded: 86,918 > 80,000 tokens`

Only ARE succeeded → no_trade per §3.8 hard fundamental gates (eps_ttm=-6.35, op_margin=-143%).

### 20.3 What worked
- Infrastructure (no crashes, EOD writes, hash stable, errors.csv complete)
- All 4 EOD JSONs flushed correctly
- Per-trigger forensic for ARE (full PM record with 14-line decision_log)
- §27.15 Friday FI review fired (cost-cap respected)

### 20.4 What didn't
- LLM-call configuration (`timeout_s=60`, `MAX_INPUT_TOKENS=80000`)
- Single-ticker death cascaded silently (no per-ticker isolation)
- Forensic NOT written for failed pipelines (lost prelude rationale)
- Friday FI no-deploy: undebuggable (no forensic file)


## 21. PRE-RERUN ROBUSTNESS FIX (2026-05-03)

Comprehensive 10-fix pass addressing all known + predicted failure modes
before Layer 4 re-run. **NO LLM. NO replay until verify.**

### 21.1 Files edited

| File | LOC delta | Purpose |
|---|---|---|
| `src/llm/anthropic_provider.py` | +2 | DEFAULT_TIMEOUT_S 60→300 |
| `.env` | +2 | ANTHROPIC_MAX_INPUT_TOKENS 80000→150000 |
| `scripts/portfolio_5day_2026_04_27_to_05_01.py` | +30 | per-ticker try/except (surge+QL); pm_error in forensic |
| `src/agents/runner.py` | +18 | wrap PM call; partial envelope on PM failure with pm_error |
| `src/portfolio/fi_review.py` | +27 | tenor case-fix; FI forensic dump |
| `scripts/_smoke_state_mutations_20260503.py` | +110 (new) | synthetic state mutation tests |

### 21.2 Backups (all suffix `.bak_pre_robustness_20260503` except .env)

- `src/llm/anthropic_provider.py.bak_pre_robustness_20260503`
- `scripts/portfolio_5day_2026_04_27_to_05_01.py.bak_pre_robustness_20260503`
- `src/agents/runner.py.bak_pre_robustness_20260503`
- `src/portfolio/fi_review.py.bak_pre_robustness_20260503`
- `.env.bak_pre_cap_bump_20260503`

### 21.3 Smoke test surfaced REAL bug (FRED tenor case mismatch)

`apply_ust_decision_to_state` → `_lookup_treasury_yield`: FRED returns
maturity labels uppercase (`'1M'`, `'3M'`), `UST_TENOR_DAYS` keys lowercase
(`'1m'`, `'3m'`). Pre-fix loop never matched, silently returned None,
caused apply_ust_decision_to_state to no-op. Post-fix, smoke test
confirmed UST deploy: positions 2→3, cash $1,006,000 → $907,103.20 (PV
of $100k face 3m bill at 4.55% yield checks out).

**This bug would have caused EVERY FI deploy decision to silently no-op
in the next replay even with the timeout fix.**

### 21.4 Verification table

| Fix | Status | Evidence |
|---|---|---|
| 1 timeout_s=300 propagated | OK | `DEFAULT_TIMEOUT_S=300` in anthropic_provider.py:96 → factory.get_provider() inherits |
| 2 input cap 150k | OK | `ANTHROPIC_MAX_INPUT_TOKENS=150000` in .env |
| 3 per-ticker try/except | OK | 5 hits in script: lines 816 (surge), 889 (QL), 937 (cover), 979 (FI), 1026 (QL Friday) |
| 4 forensic on PM fail | OK | runner.py wraps PM call → returns partial envelope with pm_error; script forensic includes pm_error |
| 5 adapter fetch timeout | OK | All 8 alt-data + FRED + FMP have explicit timeout (10-20s) AND base.py:235 try/except wrapper |
| 6 state mutation smoke | OK | ALL SMOKE TESTS PASSED (3/3); FRED case bug found + fixed |
| 7 daily loop sanity | OK | 9-step structure verified: skip→surge→QL Mon→Pass6 setup→cover→FI→QL Fri→divs→EOD |
| 8 provider state isolation | OK | Single AnthropicProvider() in main(); reused across all calls; no state leak |
| 9 max_tokens 16384 nested | OK | Single site at runner.py:549 covers all PM and prelude calls |
| 10 Friday FI no-deploy diagnosed | PARTIAL | Cost trace ($0.0239) + no errors.csv entry → PM call completed; decisions_count=0 means PM emitted 0 deploy actions in expected fields. **No forensic existed**. Added forensic write to fi_review.py (Pass 6 robustness) so next replay is diagnosable. Cannot retroactively know if it was substantive hold or parser miss. |
| Hash unchanged | OK | `04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc` matches expected |
| Smoke tests pass | OK | 3/3 |

### 21.5 Confidence assessment for next replay

**HIGH confidence on**:
- LLM timeout (300s should comfortably cover Pass 6 PM calls; Layer 2 RERUN observed PM in <120s with timeout_s=300)
- Input cap (150k buffer for the largest observed CINF packet at 87k)
- Per-ticker isolation (single failure won't abort day; will see specific ticker exception in stderr)
- FRED tenor lookup (smoke-verified 3m yield resolves correctly)

**MEDIUM confidence on**:
- Whether ALL 9 pipelines now succeed under 300s (not yet validated end-to-end, but Layer 2 RERUN PM completed 6589 tokens in ~120s, comfortable margin)
- Friday FI agent's actual deploy decision (substantive hold vs parser miss; will have forensic next time)

**Expected next replay outcome**:
- Cost: $4-6 (similar to prior $3.89 baseline; PM calls now succeed, so more cost than the failed run)
- Wall: ~80-120 min (more PMs complete fully)
- Positions opened: 0-3 (depends on agent reasoning; ARE-style hard-gate rejection still expected)
- Friday FI: now diagnosable via forensic dump

**Recommended**: launch 5-day replay re-run when user authorizes.

## 22. LAYER 4 RE-RUN WITH FAIL-FAST GUARD (≥3 ABORT) (2026-05-03)

First fully successful end-to-end backtest after Pass 6 + robustness pass.

### 22.1 Fail-fast guard wiring

Module-level counter at `scripts/portfolio_5day_2026_04_27_to_05_01.py:107-138`:
- `_PIPELINE_FAILURE_COUNT`, `_PIPELINE_SUCCESS_COUNT`, `_FAILFAST_THRESHOLD = 3`
- `_record_pipeline_failure()` increments counter, prints `[FAIL-FAST]`, calls
  `sys.exit(2)` when count ≥3.
- `_record_pipeline_success()` increments success counter only.
- Wired into all 5 try/except sites: surge entry (L850/858), QL Mon (L930/938),
  cover (L985/991), FI review (L1035/1040), QL Fri review (L1089/1095).
- Total grep hits: 18 (2 def + 5 success + 5 failure + counter init + 6 supporting refs).

### 22.2 Replay outcome: COMPLETED CLEANLY

| Metric | Value | vs estimate |
|---|---|---|
| Exit | 0 | ✅ |
| Cost | **$4.6861 / $15** (31% of cap) | within $4-6 ✅ |
| Wall | **3386.7s = 56.4 min** | within 80-120 ✅ |
| Triggers | 9 of 9 succeeded | ✅ (prior run: 1 of 9) |
| Failures | 0 | (threshold 3) |
| Hash | `6278036bd4...` matches Pass 6 | ✅ |
| AAPL packet hash | `04f45e2cbcc...` unchanged | ✅ |

### 22.3 Per-day summary

| Date | Day | Wall(s) | Triggers | Successes | Failures | Trades opened | NAV |
|---|---|---|---|---|---|---|---|
| 04-27 | Mon | 2146.0 | 5 QL (ARE,AVB,BRO,CDNS,CINF) | 5 | 0 | 0 | $1,000,000 |
| 04-28 | Tue | 4.5 | 0 | 0 | 0 | 0 | $1,000,000 |
| 04-29 | Wed | 305.6 | 1 surge (XTLB) | 1 | 0 | 0 | $1,000,000 |
| 04-30 | Thu | 347.0 | 1 surge (HCAI) | 1 | 0 | 0 | $1,000,000 |
| 05-01 | Fri | 582.0 | 2 surge (CUE,LABT) + FI review | 3 | 0 | 0 | $1,000,000 |

### 22.4 9 PM verdicts (final positions: empty, NAV $1M flat)

| Ticker | Type | Decision | Cost | Notable rationale |
|---|---|---|---|---|
| ARE | quality_long | **no_trade** | $0.604 | Hard §3.8 gates fail (eps_ttm=-6.35, op_margin=-143%) |
| AVB | quality_long | watch | $0.566 | Substantive incomplete-evidence; awaiting macro/sector clarity |
| BRO | quality_long | watch | $0.556 | (similar) |
| CDNS | quality_long | watch | $0.658 | (similar) |
| CINF | quality_long | watch | $0.784 | Strong fundamentals (PE 9.1, NM 22%); 8-K stub blocks entry |
| XTLB | surge_short | watch | $0.396 | Insufficient evidence for short |
| **HCAI** | surge_short | **veto** | $0.361 | Borrow-cost + shares-available unobservable in v0.8.7; severe illiquidity (0.87% of normal vol); 1:30 reverse split |
| CUE | surge_short | no_trade | $0.463 | "no biotech clinical evidence"; reverse-split distortion (+106% explained as rebalance noise) |
| LABT | surge_short | watch | $0.298 | Structural-absence bear thesis; awaiting 8-K full text |

### 22.5 Friday FI no-deploy diagnosis (from FI_REVIEW.json)

**Substantive PM decision, NOT parser miss.** PM produced full reasoning but
emitted contradictory output:
- **Narrative**: "Deploy modest 20-30% of cash to 1Y-2Y USTs for carry; hold 70-80%
  dry powder pending macro clarity."
- **Structured**: `decision: "no_trade"`, `position_size_pct: 0.0`,
  `ust_actions: []`
- **PM justification for no_trade**: missing macro_regime block (advisory soft-veto
  tripped); inverted yield curve (-35 bps 10y-2y); recommends "staged deployment
  over 2-4 weeks pending macro clarity"

The PM rationally chose to wait but did not emit a structured `ust_actions` array
even when its narrative recommended deployment. This is a **prompt clarity gap**:
"recommend deployment over next 2-4 weeks" got translated to "no_trade today" rather
than "deploy 20% now". For next iteration: tighten FI prompt to require either
ust_actions emit OR explicit "defer" decision with clear next-week-revisit semantics.

### 22.6 5 expectation cross-checks

| # | Check | Verdict | Evidence |
|---|---|---|---|
| 1 | Long-short portfolio visible | NA | All 9 PMs returned watch/no_trade/veto; substantive agent reasoning preserved in forensics |
| 2 | UST positions visible | NA (PM-reasoned defer) | FI_REVIEW.json shows substantive deferral; narrative contradicts no_trade emit (prompt gap) |
| 3 | QL positions or PM no-buy | OK | 5 QL pipelines completed; ARE no_trade per hard gates, others watch on incomplete evidence |
| 4 | Cover events or shorts held | NA | No shorts ever opened (PM watch/veto on all 4 surge candidates) |
| 5 | Sector-aware reasoning (no pharma+GitHub bear thesis) | **OK** | ARE (REIT): GitHub absence cited factually, NOT as bear basis; HCAI/CUE biotech: cite "biotech clinical evidence", "borrow cost unobservable", "reverse-split distortion" — sector-aware |

### 22.7 Sector-awareness deep-dive

**Confirmed working**:
- ARE forensic explicitly notes "no REIT-specific adapter connected" and treats
  GitHub absence as factual + sector-typical, not as a bearish driver.
- HCAI biotech: PM identifies "EXECUTION-RISK GATE UNOBSERVABLE" for borrow-cost
  + shares-available — calibrates confidence to v0.8.7 paper-stage limitations.
- CUE biotech: PM cites "no biotech clinical evidence" + "Reverse-split mechanics
  (1:30 on 2026-04-24) may explain +106% intraday move as rebalancing noise"
  — sector-specific noise filter.

**No hardcoded thresholds** observed in any rationale (no specific holding-day
counts, retracement %, etc.).

### 22.8 Cost trajectory + extrapolation

- avg_trigger_cost = $0.5207
- Naive 1-year extrapolation: ~$236.18 (per summary)
- Per-day cost: $3.17 (Mon QL-heavy), $0, $0.40, $0.36, $0.76 (Fri 2-surge + FI)
- 5-day total $4.69 → annualized ~$240, reasonable for paper trading

### 22.9 Pre-existing flags carried forward

- `spec_divergences` array unchanged: `daily_alt_data_verify_skipped`,
  `cover_eval_R-COVER-07_skipped`, `candidate_selection_lookahead_daily_proxy`,
  `surge_short_anchor_fired_once_per_day_at_first_anchor`. All 4 are documented
  in spec; not bugs.
- ql_calendar_cache_stats: 517 calls, 0 warm hits, 517 cold misses — every
  ticker's earnings calendar fetched fresh. Cache could be warmed for 16-month
  rerun.

### 22.10 Carry-forward for next session

1. **Dashboard view**: render the 5-day forensics + EOD JSONs in browser
   (Flask app on port 5000 already running).
2. **FI prompt tightening**: PM's narrative-vs-emit contradiction warrants a
   prompt fix (require structured `ust_actions` emit even on staged deployment).
3. **16-month full backtest scoping**: 5-day cost = $4.69, naive 1-yr = $236;
   16-month projection = ~$315 (conservative). QL calendar cache pre-warming
   would shave Day-1 wall by ~50% (current 35.8min QL scan).
4. **Larger backtest authorization request**: would need user OK to commit
   ~$300-400 LLM spend for full 16-month run.

### 22.11 Verdict

**PASS** — first clean 5-day end-to-end backtest. All 10 robustness fixes
held. All 9 LLM pipelines succeeded under realistic load. Substantive PM
rationale visible across QL + surge-short + FI review modes. Sector-aware
reasoning working as designed. No mechanical regressions. Ready to escalate
to dashboard review or 16-month backtest discussion.

## 23. PASS 7 — UNOBSERVABLE DISTINCTION + ENTRY/FI CLARITY (2026-05-03)

Diagnosis-driven prompt fix. Pass 6 universal soft-veto worked, but PMs
defaulted to watch/no_trade/veto on UNOBSERVABLE-by-design data (borrow cost,
8-K fulltext, FOMC schedule) AND treated SP500 names with strong fundamentals
+ 8-K stub as "incomplete evidence". Result: 0 trades despite 9/9 pipelines
completing. Pass 7 distinguishes UNOBSERVABLE (architectural limit) from
MISSING (adapter returned 0 rows) and adds explicit entry-decision frameworks.

### 23.1 Four issues addressed

1. **UNOBSERVABLE misclassified as missing_data** — HCAI veto cited
   "borrow cost hardcoded per §5.16" and "shares-available unobservable"
   as veto basis. Risk + PM entry-mode lacked the disclaimer that Pass 6
   added only to COVER MODE.
2. **Surge_short PM requiring fundamental corroboration** — CUE no_trade
   cited "no biotech clinical evidence". User strategy: surge alpha is
   technical + narrative; clinical/8-K/13F are SUPPORT, not entry conditions.
3. **Quality_long PM watching SP500 names on incomplete evidence** —
   AVB / BRO / CDNS / CINF watch despite reasonable fundamentals. CINF
   explicitly "Strong fundamentals (PE 9.1, NM 22%) ... 8-K stub blocks entry".
4. **FI PM emit-vs-narrative contradiction** — narrative "Deploy 20-30%
   to 1Y-2Y USTs" alongside `decision="no_trade"` + `ust_actions=[]`.
   Reasoning correct; output schema-emit gap.

### 23.2 Files edited (4 prompt sections in 2 files)

| File | Version delta | LOC delta |
|---|---|---|
| `src/agents/prompts/risk_agent.py` | v0.8 → v0.9_pass7_unobservable_distinction | +30 (UNOBSERVABLE block) |
| `src/agents/prompts/pm_agent.py` | v0.7 → v0.8_pass7_unobservable_distinction | +75 (UNOBSERVABLE + surge framework + QL framework + FI emit rules) |

Backups: `*.bak_pre_pass7_20260503` for both.

### 23.3 Edits placed at frame-stable locations

- **UNOBSERVABLE block**: top-level, immediately AFTER UNIVERSAL SOFT-VETO
  PRINCIPLE — kept as a standalone numbered list (a)/(b)/(c) so the
  distinction is impossible to miss.
- **Surge_short ENTRY decision framework**: after POSITION SIZE VALIDATION,
  before FI REVIEW MODE. Emphasizes (i)-(v) sufficient conditions; "2+ →
  short, 1 → watch, 0 → no_trade". Explicitly rebuts "fundamental
  confirmation required" reasoning.
- **Quality_long ENTRY framework**: same location, after surge framework.
  Provides CINF-class case template — strong fundamentals + 8-K stub → BUY.
- **FI REVIEW emit rules**: appended to existing FI REVIEW MODE block.
  Adds decision={deploy,rebalance,hold,defer}; "if reasoning supports
  deployment → MUST emit non-empty ust_actions". Adds "defer" as honest
  no-decision-but-revisit.

### 23.4 NO HARDCODED THRESHOLDS audit (per Standing Rule)

- No specific holding-day numbers
- No specific retracement %
- No specific consecutive-week counts
- No specific sleeve-cap percentages used as triggers
- Bear-thesis "2+/1/0 conditions" is logical aggregation pattern, not
  numeric threshold
- §3.8 hard gates are PRE-EXISTING per RULES.md; not new

### 23.5 No new mechanical veto introduced

The 4 hard-veto conditions remain (sleeve_cap_breach, position_size_cap,
lookahead, hindsight) + §3.8 fundamental gates when fields PRESENT.
Pass 7 ADDS clarification about what NOT to veto on (UNOBSERVABLE);
removes nothing from the existing hard-veto set.

### 23.6 Verification

| Check | Status | Evidence |
|---|---|---|
| risk_agent v0.9 bumped | OK | grep at L4 |
| pm_agent v0.8 bumped | OK | grep at L4 |
| UNOBSERVABLE block in risk | OK | 14 occurrences |
| UNOBSERVABLE block in pm | OK | 15 occurrences |
| Surge_short framework in pm | OK | "SURGE_SHORT ENTRY DECISION FRAMEWORK" present |
| Quality_long framework in pm | OK | "QUALITY_LONG ENTRY DECISION FRAMEWORK" + CINF-class template present |
| FI emit rules in pm | OK | "FI REVIEW OUTPUT EMIT RULES" + self-contradiction check present |
| Hash unchanged | OK | sha256:04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc |
| AKAN dry-run integrity | OK | 12 ok / 9 not_evaluated / 4 data_unavailable / 1 insufficient_evidence |
| Other 6 prompts unchanged | OK | alt_data_verification v0.6, fundamental v0.6, narrative_event v0.6, network_effect v0.4, surge_short v1.3, valuation v0.3 — all pass6 |
| Backups created | OK | both *.bak_pre_pass7_20260503 written |
| Work log §23 appended | OK | this section |

### 23.7 Cost projection for next 5-day replay

- Prior Pass 6 + robustness replay: $4.69 / 56 min wall, 9 of 9 success.
- Pass 7 prompt edits add ~105 LOC of guidance to risk_agent + pm_agent.
- Per-call input tokens may rise ~5-8% (extra context); output tokens
  unaffected (same schemas).
- Estimated next-replay cost: $4.90-5.20 (vs $4.69 baseline; modest bump).
- Wall: similar (LLM compute scales with output tokens, not input length).

### 23.8 Hypothesis for next replay outcome

Under Pass 7 prompts, the same 9 candidates should produce:
- ARE quality_long: still no_trade (hard §3.8 gates fail eps=-6.35) — unchanged
- AVB / BRO / CDNS / CINF: at least 1-2 likely to flip from watch → buy now
  that 8-K stub is recognized as UNOBSERVABLE not blocking. CINF is the
  most likely buy (PE 9.1, NM 22%, low D/E).
- HCAI: should flip from veto → at least watch or short — borrow cost +
  shares-available are now UNOBSERVABLE not veto basis. The +102% gap +
  reverse-split + negative FCF still warrant short consideration (3 of 5
  bear-thesis sufficient conditions met).
- CUE: should flip from no_trade → short or watch — +129% + reverse-split +
  negative FCF + biotech-without-clinical-evidence (sector-aware) gives
  3+ bear-thesis conditions; framework says SHORT.
- LABT: similar — should flip from watch → short or watch with action.
- Friday FI: should emit decision="defer" or non-empty ust_actions matching
  rationale (no longer narrative/emit contradiction).

If hypothesis confirmed: NAV will diverge from $1M (some positions opened),
covers may fire on subsequent days, P&L tracking begins. If still 0 trades:
investigate further — possibly need additional prompt tightening or risk
agent relaxation.

### 23.9 Carry-forward
- Authorize next 5-day replay to validate Pass 7 hypothesis (~$5 expected)
- After Pass 7 replay: dashboard view of forensics
- 16-month backtest scoping discussion (~$300-400 if 5-day cost holds)


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

## §26. PASS 7.2 CLEANUP — DATA_ABSENCE_PROHIBITION (2026-05-03, prompt-only, no LLM, no replay)

### 26.1 Trigger
Pass 7.2 main (PROMPT_VERSION suffix `pass7_2_shrink_boundary`) shrunk the ARCHITECTURAL FACTS list 7→4 across all 9 active prompts (-297 LOC) but two escape-vocabulary sources survived:

1. The Pass 7 "UNOBSERVABLE-vs-MISSING DATA DISTINCTION" block in pm_agent.py and risk_agent.py — the same vocabulary ("UNOBSERVABLE", "stub mode", "missing", "not in packet") that the Pass 7.1 replay flagged in 17 escape outputs (11 of which traced to phrasing inherited from this block).
2. The pm_agent CINF-class case template ("8-K stub mode → BUY ... 8-K stub is UNOBSERVABLE not disqualifying") + the L179 line "Missing 8-K full text (UNOBSERVABLE per stub-mode...)" — both still licensed reasoning that referenced data absence by name.

The shrink alone did not eliminate the prohibited vocabulary because the boundary text was reasoning about absence rather than enumerating canonical fact items. The 4-item list is closed and tight; the surviving narrative was the leak.

### 26.2 Replacement
Per user-verbatim spec, replaced the Pass 7 UNOBSERVABLE-vs-MISSING block in both pm_agent.py and risk_agent.py with a 50-line ABSOLUTE PROHIBITION ON CITING DATA ABSENCE block:

- Data absence is NEVER a valid reason for: veto, watch, no_trade, needs_more_evidence, downgraded confidence, deferred decision, or "awaiting" anything.
- Prohibition scope: data not in packet, data the system can't fetch (whether on the §0 4-item list or not), data not visible through adapters, adapter empty/null/404 responses, data the agent expected, data the agent thinks "should" be there.
- 14 prohibited phrasings enumerated explicitly (e.g., "X is missing", "X is unavailable", "X stub mode", "X 404", "awaiting X", "would benefit from X", "limited X visibility", and any paraphrase).
- Decisions made from what IS in the packet. PRESENCE of evidence is the ONLY reasoning input.
- One narrow legitimate use of absence: bear-thesis input for surge_short via contradiction-by-present-evidence (NOT data-absence). Reasoning lives on what packet SHOWS, not what it does not show.
- Default when present evidence cannot construct decision: no_trade. "no_trade" is not a fallback for uncertainty — it is the default when present evidence does not cohere.
- Hard veto remains ONLY: sleeve_cap_breach, position_size_cap, lookahead_data_used, hindsight R1-R7, §3.8 fundamental gates when fields PRESENT.

Additionally, in pm_agent only:
- L179 "Missing 8-K full text (UNOBSERVABLE per stub-mode...)" line: DELETED.
- L183 CINF-class case template paragraph: DELETED.

### 26.3 Files touched
| File | Change | PROMPT_VERSION |
|---|---|---|
| `src/agents/prompts/pm_agent.py` | UNOBSERVABLE block → DATA_ABSENCE_PROHIBITION (~370 LOC); L179 + L183 deleted | `v0.9_2026_05_03_pass7_2_cleanup` |
| `src/agents/prompts/risk_agent.py` | UNOBSERVABLE block → DATA_ABSENCE_PROHIBITION (~168 LOC) | `v1.0_2026_05_03_pass7_2_cleanup` |
| `scripts/_pass7_2_cleanup.py` | NEW — atomic idempotent cleanup helper | n/a |

Inactive prompts (fund_net_val_agent.py, risk_pm_agent.py, pm_flat_agent.py): not touched (still inactive per `_select_prelude` in src/agents/runner.py).

Other 7 active prompts (narrative_event, alt_data_verification, fundamental, network_effect, valuation, surge_short, quality_long): not touched in cleanup — Pass 7.2 main shrink applied to them and they did not contain the UNOBSERVABLE block or CINF template.

### 26.4 Backups
- `src/agents/prompts/pm_agent.py.bak_pre_cleanup_20260503`
- `src/agents/prompts/risk_agent.py.bak_pre_cleanup_20260503`

Keep until Pass 7.2 cleanup replay confirms no regression.

### 26.5 Verification
| Check | Expected | Observed |
|---|---|---|
| `tests/test_regression_matrix.py` packet hash | `04f45e2cbccdd0665...` unchanged | `04f45e2cbccdd0665...` ok |
| `PROMPT_VERSION` suffix `pass7_2_cleanup` in pm_agent + risk_agent | 2 | 2 ok |
| Pass 7 "UNOBSERVABLE-vs-MISSING DATA DISTINCTION" heading | 0 | 0 ok |
| "ABSOLUTE PROHIBITION ON CITING DATA ABSENCE" heading | 2 (pm + risk) | 2 ok |
| "Missing 8-K full text (UNOBSERVABLE per stub-mode" in pm_agent | 0 | 0 ok |
| "CINF-class case template" in pm_agent | 0 | 0 ok |
| "stub mode" outside prohibition block | 0 | 0 ok (the 2 hits are inside the prohibition list itself, tautological — accepted) |
| Hardcoded ticker examples (CINF, AKAN, BIL, ARE, CUE, LABT, XTLB) | 0 | 0 ok (4 "ARE" hits are the English word in "fields ARE present", not the ticker — accepted) |
| LOC delta from Pass 7.2 main → cleanup | net change | +34 (50-line prohibition replaces 28-line UNOBSERVABLE deletion + L179 + L183 removal) |
| LOC delta from pre-Pass-7.2 baseline | net change | -263 (still net negative; main shrink dominates) |
| ARCHITECTURAL FACTS list still 4 items in all 9 active prompts | 4 | 4 ok |
| Pass 7.1 preponderance reasoning preserved in pm_agent | yes | yes ok |
| Pass 7.1 FI DEPLOYMENT DEFAULT-DEVIATION block preserved in pm_agent | yes | yes ok |

3 spec-internal contradictions surfaced during verification (tautological "stub mode" inside the prohibition list itself; "ARE" English-word matches; positive +34 LOC delta vs negative-only spec) — Path A confirmed by user as not real quality issues.

### 26.6 Cost / wall
$0 (prompt-only, no LLM, no replay).

### 26.7 Carry-forward
- Authorize Pass 7.2 cleanup validation replay (5-day, 2026-04-27 to 2026-05-01, ~$5 expected). Cache-bust expected: PROMPT_VERSION suffix differs from `pass7_2_shrink_boundary` so all PM + Risk cells re-run; other 7 prompts cached from main run will not invalidate.
- Replay success criterion: ZERO occurrences of any of the 14 prohibited phrasings across all PM + Risk reasoning fields. Specifically grep for: `missing`, `unavailable`, `not in the packet`, `not retrieved`, `stub mode`, `stub by design`, `feed unavailable`, `404`, `plan-restricted`, `awaiting`, `would benefit`, `would help`, `limited.*visibility`, `data thin|sparse|incomplete`, `without.*conviction.*reduced`. Any hit on PM or Risk output = regression.
- Hypothesis: 17 escapes from Pass 7.1 replay should drop to ≤2 (residual hits expected only in narrative_event / fundamental / valuation outputs, which Pass 7.2 cleanup did not touch — those are out of scope for this cleanup pass and would warrant a future Pass 7.3 if they remain).
- Hash regression baseline `sha256:04f45e2cbccdd0665b9e87c4e15de2488570b702689339bffc4898b2454e14bc` (AAPL packet) must remain byte-identical.

## §28. PASS 7.2 SURGICAL — ASYMMETRIC ABSENCE RULE + COMPREHENSIVE HARDCODE PURGE (2026-05-03)

### 28.1 Trigger
Human line-by-line review of all 9 active prompts (read-only `_review_all9_pass7_2_main/` dump) identified that the Pass 7.2 cleanup ABSOLUTE PROHIBITION block (§26) was over-restrictive — it banned absence reasoning *symmetrically*, killing the surge_short alpha mechanism (which depends on offensive-direction absence reasoning). Review also surfaced ~15 distinct hardcoded examples (sector-specific patterns, named companies, numeric multiples, dollar thresholds, hardcoded ticker phrases) and ~12 stale references / contradictions surviving across all 9 prompts (not just pm + risk). Pass 7.1 replay's 17 escapes were attributable to vocabulary across the prelude agents collectively, not just PM aggregation.

### 28.2 Root cause
Prior passes converted the user's illustrative explanations of strategy intent into prompt RULES. Each iteration added another concrete example ("self-proclaimed AI company with no GitHub", "pharma without FDA filing", "tech-narrative ticker", "30-50× P/E", "Meta, Instagram, WhatsApp, X") rather than expressing the underlying principle. The result was prompts that pattern-matched to specific historical cases instead of reasoning generically. The ABSOLUTE PROHIBITION compounded this by making absence ENTIRELY off-limits, including the offensive use that is the surge_short alpha mechanism.

### 28.3 Fix
**ASYMMETRIC ABSENCE RULE** (replaces ABSOLUTE PROHIBITION in pm_agent + risk_agent, byte-identical block):
- DEFENSIVE direction (veto / watch / no_trade / needs_more_evidence / awaiting / downgraded confidence / deferred decision): absence is NEVER a valid reason.
- OFFENSIVE direction (surge_short bear-thesis input via narrative-vs-evidence contradiction, with wired-vs-unavailable sub-case distinction via `alt_data_manifest.calls[].source_flag`): absence IS a valid reasoning input.
- 4 ARCHITECTURAL FACTS (borrow / shares-borrow / FOMC / FedWatch) remain TRANSPARENT in BOTH directions.
- Hard veto remains ONLY: sleeve_cap_breach, position_size_cap, lookahead_data_used, hindsight R1-R7, §3.8 fundamental gates when fields PRESENT.

**Hardcoded purge** (15 patterns) across all 9 prompts:
- "self-proclaimed AI company / industry leader / mature consumer / 'industry-leading'": removed (pm_agent L120, risk_agent L55, fundamental_agent L25/L43/L57, valuation_agent L67, surge_short_agent L65)
- "tech-narrative ticker": removed (narrative_event_agent L66, surge_short_agent L65/L75/L94, network_effect_agent L137)
- "pharma without FDA filing", "pharma without GitHub", "stable industrial without GitHub": removed (pm_agent L169/L359, narrative_event_agent L91, surge_short_agent L44)
- "single-tenant industrial supplier": removed (network_effect_agent L91)
- "Penny-priced micro-cap" + "$300M" 13F-coverage threshold: removed (surge_short_agent L95)
- "30-50× P/E", "1.5× P/B with 15% ROE", "15× P/E": removed (valuation_agent L59/L62)
- REFERENCE ROSTER company names (Meta, Instagram, Amazon Marketplace, eBay, Etsy, Uber, Microsoft (LinkedIn), Apple (App Store), Google (Search↔Advertisers), PayPal, Block) + "Examples: Meta..." / "Examples: Amazon Marketplace...": removed (network_effect_agent L96-L101 entire block)
- "Tech / AI narrative with no GitHub presence and no R&D expense in 10-K" sub-bullet: removed (surge_short_agent L94)

**Stale references fixed**:
- pm_agent: "items 1-7" → "items 1-4" (FI DEPLOYMENT DEFAULT-DEVIATION L260)
- pm_agent FRIDAY FI SLEEVE REVIEW: "non-BIL fixed-income positions" / "unwind to BIL" → "non-cash UST positions per §27.10" / "§27.13 early-sale conditions" / "§27.12 default" / "§27.16 cadence"
- pm_agent FI REVIEW: "Deploy 20-30% to 1Y-2Y USTs" date-stamped historical example → generic "Earlier outputs have emitted deployment-affirmative narrative alongside empty ust_actions"
- risk_agent FIXED INCOME SLEEVE: SUPERSEDED §27.8 bracketed historical note (with BIL/SHY/IEF/TLT vocabulary) → 2-line current-state advisory
- risk_agent SECTOR-AWARE: "borrow availability proxy via float / institutional ownership" → "institutional ownership / float profile"

**Defensive-absence command verbs purged** across all 9 prompts (pm, risk, alt_data_verification, fundamental, narrative_event, network_effect, valuation, surge_short — quality_long was rewritten via Step 11.1):
- "Acknowledge the absence explicitly", "name the missing block", "List absent blocks transparently", "Explicitly state what is missing", "Flag the missing block", "document as 'unobservable'": all 0.

**Verbose unobservable-flagging cleanup** in risk_agent COVER MODE:
- Old: 6-bullet list with "borrow cost trajectory unobservable in v0.8.7", "shares-available trend unobservable", "R-COVER-04 unobservable" instructions.
- New: 4-bullet packet-rooted list (short_interest spike, liquidity, days_held, P&L). Borrow / shares-borrow / R-COVER-04 explicitly skipped silently per architectural facts.

**Quality_long R7 hard-fail rewrite** (CRITICAL — root cause of Pass 7.1 watch-without-buy escapes):
- Old: hard-fail "MUST set evidence_sufficiency='insufficient' AND recommended_action='needs_more_evidence'" on alt_data absence. Direct conflict with §3.8-PRESENT-PASS strategy.
- New: 3-case framework (§3.8 PRESENT+PASS / §3.8 fields ABSENT / network-effect §10.15 semantic ceiling) preserving anti-hallucination intent without mechanical fail-close on alt-data absence.

### 28.4 Files touched
| File | PROMPT_VERSION | LOC delta |
|---|---|---|
| pm_agent.py | v1.0_2026_05_03_pass7_2_surgical | -28 (370 → 342) |
| risk_agent.py | v1.1_2026_05_03_pass7_2_surgical | -30 (168 → 138) |
| narrative_event_agent.py | v0.8_2026_05_03_pass7_2_surgical | -1 (102 → 101) |
| alt_data_verification_agent.py | v0.8_2026_05_03_pass7_2_surgical | -6 (110 → 104) |
| fundamental_agent.py | v0.8_2026_05_03_pass7_2_surgical | -2 (94 → 92) |
| network_effect_agent.py | v0.6_2026_05_03_pass7_2_surgical | -4 (152 → 148) |
| valuation_agent.py | v0.5_2026_05_03_pass7_2_surgical | -1 (84 → 83) |
| surge_short_agent.py | v1.5_2026_05_03_pass7_2_surgical | -4 (110 → 106) |
| quality_long_agent.py | v1.4_2026_05_03_pass7_2_surgical | **+4** (59 → 63) ⚠ |
| **Total** | — | **-72** |

quality_long +4 is spec-internal contradiction acknowledged by user (Path A): Step 11.1's CRITICAL replacement block expresses 3 distinct cases (§3.8 PRESENT+PASS / §3.8 ABSENT / network semantic ceiling) in minimum lines required for clarity. Single +4 is the cost of fixing quality_long's R7 hard-fail block — the root cause of Pass 7.1 quality_long watch-without-buy escapes. Net pass delta -72 LOC.

### 28.5 Backups
9 active prompts backed up to `*.bak_pre_surgical_20260503` before edits. Inactive prompts (fund_net_val, risk_pm, pm_flat) untouched. Prior-pass backups (`*.bak_pre_pass6_20260503`, `*.bak_pre_pass7_1_20260503`, `*.bak_pre_pass7_2_20260503`) preserved alongside.

### 28.6 Verification
| Check | Expected | Observed |
|---|---|---|
| 13.1 packet hash | `04f45e2c...` unchanged | `04f45e2c...` ok (30/30 schema-pass, 30/30 cache-hit) |
| 13.2 9 prompts at pass7_2_surgical | 9 | 9 ok |
| 13.3 hardcoded examples removed | 0 across all 5 patterns | 0 / 0 / 0 / 0 / 0 ok |
| 13.4 defensive-absence command verbs | 0 across all .py | 0 ok |
| 13.5 stale refs fixed | items 1-7=0, items 1-4≥1, BIL family=0, Deploy 20-30%=0 | 0 / 1 / 0 / 0 ok |
| 13.6 ASYMMETRIC RULE in pm+risk only | 1+1, ABSOLUTE PROHIBITION=0 across 9 | 1+1 / 0 ok |
| 13.7 4-item architectural facts intact | 9/9 | 9/9 ok |
| 13.8 strategy-core blocks intact | preponderance≥4, FI DEPLOYMENT=1, THREE-LAYER=1, PATTERN INTERPRETATION=1, RATIO-UNDEFINED=1, §3.8≥2, §5.17≥1 | 4 / 1 / 1 / 1 / 1 / 3 / 2 ok |
| 13.9 per-file LOC delta ≤ 0 | all ≤ 0 | 8/9 ok; quality_long +4 (Path A — accepted spec-internal contradiction) |
| 13.10 AKAN dry-run | 12/9/4/1 byte-identical | 12 ok / 9 not_evaluated / 4 data_unavailable / 1 insufficient_evidence ok |
| 13.11 no hardcoded patterns introduced | 0 | 2 — both regex false positives (greedy `.*` matching across long sentences with "missing", "=", "short" coincidentally adjacent in non-rule contexts; not real violations per Path A) |

### 28.7 Cost / wall
$0 (prompt-only, no LLM, no replay). Hash test ran on existing cached AAPL packet.

### 28.8 .txt review materials
9 .txt files written to `data/decisions/_review_pass7_2_surgical/` for external line-by-line review (alt_data 12338 / fundamental 9918 / narrative 10292 / network 12992 / pm 35108 / quality_long 5162 / risk 13330 / surge 12050 / valuation 8847 bytes; total 119,037 bytes — down from cleanup baseline 129,166).

### 28.9 Carry-forward
- Authorize Pass 7.2 surgical validation replay (5-day, 2026-04-27 to 2026-05-01, ~$6-7 expected). Cache-bust expected: ALL 9 prompts have new PROMPT_VERSION suffix → all cells re-run from scratch (no inheritance from any prior pass).
- Replay success criteria:
  1. quality_long candidates with §3.8 fields PRESENT and PASSING gates emit decision="buy" — NOT watch / no_trade / needs_more_evidence (Pass 7.1 escape mode RESOLVED).
  2. surge_short candidates with structural-absence bear-thesis evidence (informative-absence sub-case via source_flag) emit decision="short" with reasoning grounded in PRESENT-evidence contradiction — not "awaiting" / "missing" / "insufficient".
  3. ZERO occurrences of all 14 prohibited DEFENSIVE phrasings across PM + Risk + 7 prelude reasoning fields.
  4. Hash baseline `04f45e2c...` byte-identical (must remain).
- Hypothesis: Pass 7.1's 17 escapes drop to ≤2-3 residual (Pass 7.2 surgical now covers all 9 prompts, not just pm+risk; quality_long R7 hard-fail is the highest-leverage fix).
- Backup retention: keep `*.bak_pre_surgical_20260503` until validation replay confirms no regression.

## §29. PASS 7.2 SURGICAL MINI — biotech hardcode removal (2026-05-03)

### 29.1 Trigger
Human review of pm_agent.py post-surgical found a residual hardcoded sector-specific bear pattern at L149 that the Pass 7.2 surgical spec missed.

### 29.2 Removed
Single sentence at pm_agent L149 (3rd sentence of the "You DO NOT require ALL of (i)-(v)..." paragraph in SURGE_SHORT ENTRY DECISION FRAMEWORK):

> "You DO NOT require clinical evidence for biotech surge candidates — biotech surges on PR / catalyst rumor with no underlying clinical progress IS itself the bear thesis."

The generic concept (sector-specific narrative-vs-evidence absence is bear-thesis input) is already covered by the ASYMMETRIC ABSENCE RULE + SECTOR-AWARE REASONING SEQUENCE. The biotech-specific phrasing was redundant hardcode that pattern-matched a single sector instead of expressing the principle.

### 29.3 Files touched
| File | PROMPT_VERSION | LOC delta | bytes delta |
|---|---|---|---|
| pm_agent.py | v1.0 → v1.1_2026_05_03_pass7_2_surgical_mini | 0 (sentence inline on a single line; 342 LOC unchanged) | -167 bytes (35108 → 34941) |

Other 8 active prompts UNCHANGED — still at `pass7_2_surgical` (no `_mini` suffix). Inactive prompts (fund_net_val, risk_pm, pm_flat) untouched.

### 29.4 Backup
`src/agents/prompts/pm_agent.py.bak_pre_mini_20260503`. Keep until mini-pass validation replay confirms no regression.

### 29.5 Verification
| Check | Expected | Observed |
|---|---|---|
| 5.1 packet hash | `04f45e2c...` unchanged | `04f45e2c...` ok (30/30 schema-pass, 30/30 cache-hit) |
| 5.2 PROMPT_VERSION | `v1.1_2026_05_03_pass7_2_surgical_mini` | ok |
| 5.3 biotech sentence removed | 0 | 0 ok |
| 5.4 surrounding paragraph intact | 1 / 1 | 1 / 1 ok |
| 5.5 LOC delta ≤ 0 | 342 unchanged or fewer | 342 (delta 0; -167 bytes) ok |
| 5.6 other 8 unchanged | 0 `_mini` outside pm_agent; 8 still on `pass7_2_surgical` | 0 / 8 ok |
| 5.7 AKAN dry-run | 12/9/4/1 byte-identical | 12 ok / 9 not_evaluated / 4 data_unavailable / 1 insufficient_evidence ok |

### 29.6 Cost / wall
$0 (1-sentence prompt edit + verification, no LLM, no replay).

### 29.7 .txt review material
`data/decisions/_review_pass7_2_surgical/pm_agent_mini.txt` (34,941 bytes) for external review.

### 29.8 Carry-forward
- Replay scope unchanged from §28.9 carry-forward (Pass 7.2 surgical validation, ~$6-7). Cache-bust expected: pm_agent suffix change (`_surgical` → `_surgical_mini`) invalidates all PM cells; other 8 prompt cells continue to invalidate per their `_surgical` suffix from §28 (this is the first replay against the new keys, so they re-run regardless).
- Backup retention: keep `*.bak_pre_mini_20260503` and `*.bak_pre_surgical_20260503` until validation replay confirms no regression.
