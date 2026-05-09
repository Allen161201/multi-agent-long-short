# Historical Daily-Gainers Reconstruction — Strategy Comparison

**Date:** 2026-04-28
**Status:** Strategy decision document. **No reconstruction built yet** — gated on human approval.
**Inputs:**
- Universe: `data/universe/universe_master_v1.json` (26,265 active + 585 delisted = **26,850 tickers**)
- Backtest window: ~2019-04 → 2026-04 ≈ **1,765 trading days**
- API cap: FMP Premium `/stable/`, 750 calls/min (we hold ourselves to 600)

---

## B.1 endpoint reconnaissance — what we found

10 raw FMP calls used. Sidecars: `outputs/_inspector/probe_batch_historical_endpoints.json` and `..._pt2.json`.

| Endpoint probed | Path | Verdict | Notes |
|---|---|---|---|
| Per-symbol historical EOD, multi-symbol | `historical-price-eod/full?symbol=AAPL,MSFT,NVDA&from=…&to=…` | **NOT_USABLE** | HTTP 200 but `rows=0`. Comma list silently treated as one literal "symbol" string. |
| Light variant of above | `historical-price-eod/light?symbol=AAPL,MSFT,NVDA&…` | **NOT_USABLE** | Same EMPTY behaviour. |
| Batch quote, current snapshot | `batch-quote?symbols=AAPL,MSFT,NVDA` | USABLE for **today only** | Returns 3 rows. No `date` parameter; current-only. Useful for live universe refresh, not backfill. |
| Bulk EOD by date (speculative) | `historical-price-eod-bulk?date=2024-06-17` | **NOT_USABLE** | HTTP 404. |
| Batch historical price (speculative) | `batch-historical-price?symbols=…&from=…&to=…` | **NOT_USABLE** | HTTP 404. |
| Sector-level history | `historical-sectors-performance?from=…&to=…` | NOT_USABLE | HTTP 404. |
| S&P 500 historical change log | `historical-sp500-constituent` | PARTIAL | 1,518 add/remove events — reconstructs PIT-correct S&P 500 membership for any date, but S&P 500 is large-cap only, irrelevant for surge candidates. |
| NASDAQ index historical change log | `historical-nasdaq-constituent` | PARTIAL | 436 add/remove events — same usefulness limit (NDX is large-cap). |
| Russell 3000 historical constituents | `historical-russell-3000-constituent` | **NOT_USABLE** | HTTP 404. The one large-mid-cap index that *would* have helped — not available on `/stable/`. |
| Most-actives with `date` parameter | `most-actives?date=2024-06-17` | **WORKAROUND_NEEDED** | HTTP 200, 50 rows, but first row `CHSN  changesPercentage=-70.71%` (a current snapshot). Date param appears ignored, same as `biggest-gainers`. All FMP "of the day" leaderboards are current-only. |

**Bottom line:** no batch / bulk / cross-sectional historical EOD endpoint exists on this surface. The only PIT-correct path is **per-ticker `historical-price-eod/full`**, which (verified in feasibility probe Capability 2) returns full multi-year history per call.

---

## B.2 strategy comparison

### Strategy A — full-universe per-ticker history pull

**Idea:** for every one of the 26,850 universe tickers, call `historical-price-eod/full?symbol=<T>&from=<window_start>&to=<window_end>` once. Persist locally. Daily top-gainers for any decision date are then computed in-memory by:
1. Filtering each ticker's history to rows with `date <= decision_date`,
2. Joining T-1 close → T close (or T-1 close → T open for pre-market mode),
3. Ranking.

| Dimension | Value |
|---|---|
| Total expected FMP calls | **~26,850 one-time** (then incrementally ~250-300/day for new EOD bars) |
| Wall-clock at 600/min | ~45 minutes (one-time) |
| Storage footprint | JSON: ~9-12 GB · Parquet (compressed columnar): ~1-2 GB |
| Survivorship bias | **LOW** — universe v1 includes 585 delisted-bucket rows + the bankrupt issuers FMP misclassifies as active (BBBY, SIVBQ, WEWKQ, WE). Use `utils/delisting_detection.detect_delisting_from_price_tail` at decision time to override the active flag. |
| Look-ahead bias | **LOW** — every per-day ranking step filters `date <= decision_date` strictly. Multi-year cache is content-addressable (history is monotonic) so it doesn't decay. |
| Implementation complexity | **M** — fetch loop + JSON cache (already designed in `utils/delisting_detection.py`) + per-day in-memory ranker + light dedup. |
| Coverage gap | Pre-2025-05-27 delistings are missing from universe v1 (see `UNIVERSE_V1_NOTES.md` #4). Tickers active in 2019 but delisted before 2025-05-27 are NOT in the universe. This is a **survivorship leakage** — independent of strategy choice — and is fixed only by the v1.1 delisted-pagination extension. |

### Strategy B — narrowed universe per-ticker pull

**Idea:** apply heuristics that need only fields we already have (universe v1 + symbol patterns) to shrink the universe, then run the same per-ticker pull.

What we **can** narrow on without API calls:

| Narrowing rule | Estimated drop | Effect on surge-short signal |
|---|---|---|
| Drop class-share duplicates by `companyName` match (e.g. keep BRK-A, drop BRK-B) | ~150-300 names | Minor — class shares almost never independently surge-rank. Safe. |
| Drop active-bucket SPAC pre-merger units/warrants the symbol regex didn't catch (e.g. residual `Acquisition Corp` names that lack typical suffixes) | ~500-1000 names | Mostly safe; SPACs that surge usually do so as the post-merger common, which we keep. |
| Drop bankrupt-Q residue (`*Q` symbols) | ~30-100 names | **UNSAFE** — these are precisely the issuers whose collapse the short side wants to capture. Do NOT apply. |
| Index-narrow to S&P 500 historical members | 26,850 → ~500 | **UNSAFE** — surge candidates are micro/small caps, almost never S&P 500 names. Loses ~95% of surge-short signal. |

**Realistic narrowing yields ~25,000-26,000 tickers** — a ~5% reduction. Not material at 600/min.

| Dimension | Value |
|---|---|
| Total expected FMP calls | ~25,500 one-time |
| Storage footprint | ~95% of Strategy A |
| Survivorship bias | **MEDIUM** — same coverage gap as A (pre-2025-05-27 delistings), plus any over-aggressive narrowing rule introduces additional bias. |
| Look-ahead bias | LOW — same as A. |
| Implementation complexity | **S/M** — Strategy A plus a thin pre-filter pass. |
| Verdict | Marginal cost saving; not worth dedicated engineering. **Skip.** |

### Strategy C — cross-sectional batch endpoint (per day)

**Idea:** call one endpoint per trading day and get all 26,850 tickers' EOD in a single response.

| Dimension | Value |
|---|---|
| Total expected FMP calls | If endpoint existed: 1,765 trading days × 1 = **1,765** |
| Survivorship bias | LOW (in theory) |
| Look-ahead bias | LOW (in theory) |
| Implementation complexity | S |
| **Status** | **INFEASIBLE — no such endpoint.** B.1 confirmed `historical-price-eod-bulk` and `batch-historical-price` are 404 on /stable/, multi-symbol comma queries return 0 rows, and all "of the day" leaderboards are current-only. |

### Strategy D — hybrid (daily-shortlist + per-ticker fan-out)

**Idea:** call a cross-sectional shortlist endpoint per day to get the top ~100 movers, then per-ticker calls only on those. Cost: 1,765 × (1 shortlist + ~30 unique new tickers per day after dedup) ≈ a few thousand calls.

| Dimension | Value |
|---|---|
| Status | **INFEASIBLE** — same reason as C. The candidate-shortlist endpoint we'd need is `biggest-gainers?date=…` or `most-actives?date=…`, both of which silently ignore the date param (B.1 + feasibility-probe Capability 1). |
| Verdict | **Reduce to A** — without a working dated leaderboard, hybrid degenerates into A. |

---

## B.3 recommendation

### Recommended strategy

**Strategy A** — pull full per-ticker history once for all 26,850 universe-v1 tickers, cache to disk in Parquet (one file per ticker or one wide partitioned dataset), then compute per-day gainers in memory at decision time.

This is the **only credible** path: B.1 ruled out C and D outright (no batch/bulk endpoints exist), and B's narrowing rules don't move the needle without introducing bias.

### Why A is acceptable

1. **Cost is bounded.** ~26,850 one-time calls ≈ 45 minutes wall-clock at 600/min. Then ~250-300 incremental calls per day for new EOD bars (one daily refresh). Total 7-year ingestion fits comfortably under any reasonable monthly quota.
2. **History is monotonic.** Once a ticker's EOD is cached, it doesn't decay. The same cache serves every decision date in the backtest window without re-pulls.
3. **Survivorship-correct (modulo the v1 coverage gap).** All delisted-bucket tickers are in scope, and the bankrupt issuers FMP misclassifies as active (BBBY, SIVBQ, WEWKQ, WE) are also in scope. The price-tail detector handles the active-flag override at decision time.
4. **PIT-correct by construction.** Each per-day ranking pass filters `date <= decision_date`. The cache itself contains future rows (relative to a historical decision date), but the consumer never reads them.

### Phased rollout

I'd recommend a two-phase implementation:

- **Phase 1 (MVP):** Strategy A, single-process fetcher, JSON-per-ticker cache (same shape as `utils/delisting_detection.py` already uses), in-memory per-day ranker. ~3-5 days of engineering. Goal is to unblock the 7-year backtest.
- **Phase 2 (perf upgrade, optional):** convert the JSON cache to partitioned Parquet, add a thin `pyarrow`/`duckdb` query layer so the per-day ranker can run a single SQL across the full universe instead of looping ticker-by-ticker. Only worth it if Phase-1 ranking turns out to be slow.

### Risks / uncertainties for human review

1. **Universe-v1 delisted coverage gap.** Pre-2025-05-27 delistings are missing. A 7-yr backtest from 2019 will silently exclude any ticker that delisted before that date. Decide before kickoff: ship Phase 1 on universe-v1 (acknowledged survivorship leakage on pre-2025-05 delistings) or block Phase 1 on a v1.1 delisted extension first (~85 more API calls, ~15 minutes).
2. **Per-ticker history depth.** Feasibility probe verified that `historical-price-eod/full` covers 2022 → present for the bankrupt-issuer cases. We did **not** verify that all 26,850 tickers have ≥7 yr of history. Some recently-IPO'd or SPAC-merged tickers naturally won't. The fetcher should record per-ticker actual coverage and flag tickers whose first-available-date is later than our backtest start.
3. **Storage location.** ~10 GB JSON or ~1-2 GB Parquet. Confirm the project root volume has headroom.
4. **Refresh schedule.** Once Phase 1 is built, ~250-300 daily incremental pulls should run nightly. Confirm whether to schedule via cron-like routine or wire into the existing dashboard's manual-refresh path.
5. **Class-share dedup policy.** Keep both BRK-A and BRK-B, or dedup to one? Today the universe keeps both. A single-row-per-issuer dedup may be cleaner for ranking but would lose any class-specific dislocation. Default = keep both; flag for human decision.
6. **Source flag in cached payloads.** Each cached row should be tagged with the existing `live_fmp` source flag and an `available_as_of` timestamp for downstream PIT validation. The fetcher must write these inline; deferring this to the consumer breaks the engine's source-flag invariant.

None of these are blockers. They're inputs to the Phase-1 spec.
