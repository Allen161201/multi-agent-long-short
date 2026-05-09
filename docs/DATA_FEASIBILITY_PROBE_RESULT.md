# Data Feasibility Probe Result — FMP Premium for 7-year backtest

**Probe date:** 2026-04-27 21:45 CT (2026-04-28T01:45 UTC)
**Probe script:** `scripts/probe_fmp_historical_feasibility.py`
**Raw evidence sidecar:** `outputs/_inspector/probe_fmp_feasibility_raw.json`
**Active rule version (unchanged):** `v0.5_agentic_allocation_corrected`
**API key fingerprint (sha256[:8]):** `65edf7b5` (length 32)
**Budget:** ≤ 20 raw FMP `/stable/` calls — actual = **11 / 20**.
**Status:** Read-only diagnostic. No production code, rule, schema, adapter, fixture, or pipeline modified. No git commit. No dashboard change.

> All four capabilities probed via `fmp_adapter._api_call` directly, so the existing rate-limiter (600/min cap), sticky 429 / 402 pause, redaction filter, and key-redacting logger filter were all engaged. Sticky pause never triggered. Rolling-minute count peaked at 11.

---

## Capability 1 — Historical Top Gainers

### Question
Can FMP `/stable/biggest-gainers` return the top-gainers list **as of an arbitrary historical date** (so the surge-short universe can be reconstructed for any day in the 7-year backtest)?

### Method
Called `biggest-gainers` twice through the existing adapter:

| Label | Endpoint | Params |
|---|---|---|
| A | `biggest-gainers` | *(no params)* |
| B | `biggest-gainers` | `date=2024-06-17` |

Compared the top-5 symbols of A vs. B.

### Raw evidence
```
A) HTTP 200 ok=True rows=50  → top-5: ['HTCO', 'YAAS', 'FSHPR', 'EDSA', 'PAPL']
B) HTTP 200 ok=True rows=50  → top-5: ['HTCO', 'YAAS', 'FSHPR', 'EDSA', 'PAPL']
identical_payload = True

Row excerpt (A and B both):
  symbol="HTCO"  price=38.22  change=26.97  changesPercentage=239.7  exchange="NASDAQ"
  symbol="YAAS"  price=2.32   change=1.39   changesPercentage=149.6  exchange="NASDAQ"
```

### Verdict — **WORKAROUND_NEEDED**
The `date` parameter is **silently ignored**. The endpoint always returns the *current-day* top-gainers snapshot. There is no error, no warning, no schema field that says "this is current". Two calls with different date inputs returned byte-identical top-5 (and the magnitudes — +239 % intraday — match a current-day momentum scan, not a historical close-to-close move).

### Implication for 7-year backtest
This endpoint **cannot** drive the surge-short universe selection in a backtest. Using it as-is would inject look-ahead bias (the agent would see 2026-04 gainers while reasoning about 2019). This is a **blocking** issue for the 7-yr historical replay of the surge-short sleeve.

### Workaround draft (NOT implemented)
1. **Define a fixed universe up front** (e.g., Russell 3000 historical constituents, or all NYSE/NASDAQ tickers active in window `[T-7y, T]`).
2. For each historical decision date `T`, pull `historical-price-eod/full` for every universe member in `[T-1, T]`.
3. Compute close-to-close `change_pct` (or T-1 close → T open if pre-market mode).
4. Rank-sort and take the top N as the day's "biggest gainers".
5. Cache the reconstructed daily top-N list (keyed by `(date, universe_version)`).

This is heavier but gives a true point-in-time gainers list. Storage cost is the dominant constraint, not API cost (one daily price pull per ticker is cheap on the 750/min Premium plan; a 3000-ticker × ~1750-trading-day backtest is ~5.3M rows but only a few hundred-K calls if we use date-range pulls per ticker).

---

## Capability 2 — Delisted Ticker Historical Data (survivorship bias)

### Question
Can FMP return historical EOD prices for tickers that have been delisted? Does the `profile` endpoint flag them as inactive?

### Method
For each of three known bankrupt issuers, called `historical-price-eod/full` with `from=2022-01-01, to=2024-01-01`. When the post-bankruptcy "Q"-suffixed OTC symbol returned 0 rows, fell back to the legacy NASDAQ symbol. Also called `profile` once on the most-impacted ticker (`BBBY`) to inspect the active-trading flag and any delist-date field.

### Raw evidence
```
BBBYQ  : HTTP 200 rows=0    (post-bankruptcy OTC symbol — empty)
BBBY   : HTTP 200 rows=501  first_date=2023-12-29  last_date=2022-01-03
         last_row close=59.95 (2022-01-03)  →  highest pre-collapse value visible
SIVBQ  : HTTP 200 rows=501  first_date=2023-12-29  last_date=2022-01-03
         2022-01-03 close=688.17  →  2023-12-29 close=0.0331  (≈ -99.995 %)
WEWKQ  : HTTP 200 rows=501  first_date=2023-12-29  last_date=2022-01-03
         2022-01-03 close=369.20  →  2023-12-29 close=0.2769  (≈ -99.92 %)

profile(BBBY): HTTP 200
  isActivelyTrading = True             ← INCORRECT for delisted issuer
  delistedDate      = null              ← NOT POPULATED
  exchange          = "NYSE"
  ipoDate           = "1992-06-01"
  (35 fields total; delist information appears to be absent/unreliable)
```

### Verdict — **PASS** for prices, **PARTIAL** for active-trading metadata
- **Prices:** all three issuers return rich daily history covering the bankruptcy event. The post-bankruptcy "Q" symbols are inconsistent (BBBYQ empty, SIVBQ/WEWKQ populated) — the adapter should try both forms.
- **Profile flag:** `isActivelyTrading=True` and `delistedDate=null` for BBBY (a clearly delisted ticker) is **wrong**. The profile endpoint cannot be used as a survivorship filter today; the historical-price endpoint itself is more reliable as the survivorship signal (e.g., "no rows after date X" = effectively delisted).

### Implication for 7-year backtest
Survivorship-bias avoidance is **viable** because the price endpoint genuinely covers delisted issuers. Two practical consequences:
1. The universe loop must symbol-test both `<TICKER>` and `<TICKER>Q` forms when handling Ch.11 cases.
2. Do **not** rely on `profile.isActivelyTrading` or `profile.delistedDate` as a delisting filter. Detect delisting from the historical-price tail (last available trading day) instead.

This is the **make-or-break** for the surge-short backtest, and it passes.

### Workaround if PARTIAL applies
For 100 % survivorship coverage, supplement with a CRSP-style historical universe list (or `available-traded-list` / `delisted-companies` if such an FMP endpoint exists). Out of scope for this probe.

---

## Capability 3 — Historical News Depth

### Question
How far back can the FMP plan return news for a typical large-cap (AAPL)?

### Method
Three calls:
- A: `news/stock-latest?symbols=AAPL&limit=5` (sanity check).
- B: `news/stock?symbols=AAPL&from=2018-01-01&to=2018-03-01&limit=5`.
- C: `news/stock?symbols=AAPL&from=2015-01-01&to=2015-03-01&limit=5`.

### Raw evidence
```
A) news/stock-latest                HTTP 200 ok=True rows=5
   row keys = [symbol, publishedDate, publisher, title, image, site, text, url]
   newest excerpt: publishedDate="2026-04-27 21:35:00"  (RGC, GlobeNewsWire)

B) news/stock 2018-01..03 (AAPL)    HTTP 200 ok=True rows=5
   first_excerpt.publishedDate = "2018-02-22 04:30:00"  (Apple Austria store, Business Wire)
   last_excerpt.publishedDate  = "2018-01-23 08:30:00"  (HomePod launch, Business Wire)

C) news/stock 2015-01..03 (AAPL)    HTTP 200 ok=True rows=4
   first_excerpt.publishedDate = "2015-02-23 03:00:00"  (€1.7B EU data centres, Business Wire)
   last_excerpt.publishedDate  = "2015-01-08 11:00:00"  (App Store records, Business Wire)
```

### Verdict — **PASS**
News is retrievable as far back as **2015** for AAPL on the current key. A 7-year backtest window (≈ 2019-04 → 2026-04) is comfortably inside the available depth. Note that the test used `limit=5`; production pulls will need pagination — the row counts here only confirm the *floor* of FMP's coverage, not its volume.

### Implication for 7-year backtest
- Narrative agent can use FMP `news/stock` across the entire backtest window.
- No GDELT fallback needed for the AAPL-style mega-cap path.
- For small caps, depth may be thinner; recommend a separate spot-check before going live (out of probe scope).

### Caveats (not failures, but worth noting)
- 2015 query returned 4 rows even with `limit=5` — likely actual coverage limit for that 60-day window, not a silent cap. Confirm by paging in a future probe.
- Records are missing a publisher-tier field; the §10 information-integrity layer (T2/T3 source-tier weighting) will need an external publisher-→-tier mapping.

---

## Capability 4 — PIT-Correct Fundamentals

### Question
Do `income-statement` rows carry enough date metadata (`filingDate`, `acceptedDate` / `acceptedDatetime`) to do point-in-time reconstruction?

### Method
One call: `income-statement?symbol=AAPL&period=quarter&limit=8`. Inspected which date-related fields are present across all 8 rows.

### Raw evidence
```
income-statement(AAPL, q, 8)  HTTP 200 ok=True rows=8

PIT field presence across all rows:
  date              = True
  fiscalYear        = True
  period            = True
  filingDate        = True
  acceptedDate      = True   ← format "YYYY-MM-DD HH:MM:SS" (already a datetime string)
  acceptedDatetime  = False  ← not present as a separate key
  calendarYear      = False
  reportedCurrency  = True
  cik               = True
  symbol            = True

Per-row PIT excerpts:
  date=2025-12-27  fiscalYear=2026 Q1  filingDate=2026-01-30  acceptedDate=2026-01-30 06:01:32
  date=2025-09-27  fiscalYear=2025 Q4  filingDate=2025-10-31  acceptedDate=2025-10-31 06:01:26
  date=2025-06-28  fiscalYear=2025 Q3  filingDate=2025-08-01  acceptedDate=2025-08-01 06:00:42
  date=2025-03-29  fiscalYear=2025 Q2  filingDate=2025-05-02  acceptedDate=2025-05-02 06:00:46
  date=2024-12-28  fiscalYear=2025 Q1  filingDate=2025-01-31  acceptedDate=2025-01-31 06:01:27
  date=2024-09-28  fiscalYear=2024 Q4  filingDate=2024-11-01  acceptedDate=2024-11-01 06:01:36
  date=2024-06-29  fiscalYear=2024 Q3  filingDate=2024-08-02  acceptedDate=2024-08-01 18:03:34
  date=2024-03-30  fiscalYear=2024 Q2  filingDate=2024-05-03  acceptedDate=2024-05-02 18:04:25
```

### Verdict — **PASS**
Every quarterly row carries `date` (period end) + `filingDate` + `acceptedDate` (with a wall-clock timestamp). That is sufficient for the existing engine rule: *10-Q / 10-K usable only after `filing_date`* — and tighter discipline (intraday accept-time gating) is also possible because `acceptedDate` is timestamp-precision.

Note: the field is **`acceptedDate`** (already a "date + time" string), not a separately-named `acceptedDatetime`. The existing `_normalize_statement_row()` already maps it to `accepted_date`, so this is consistent with the current adapter contract.

### Implication for 7-year backtest
- The §8e `decision_time_discipline` `allowed_data_cutoff` rule can be enforced on filings exactly as drafted.
- `acceptedDate` semantics: in two cases above (2024-Q2, 2024-Q3) the filing was *accepted by EDGAR* one calendar day before the `filingDate` field's date. Decision-time discipline should compare the cutoff against `acceptedDate` (the more conservative truth-time), not just `filingDate`.

---

## Summary table

| # | Capability | Verdict | Blocking? | Backtest action |
|---|---|---|---|---|
| 1 | Historical top-gainers list (date param) | **WORKAROUND_NEEDED** | Yes for surge-short universe | Reconstruct top-gainers per day from a fixed universe + `historical-price-eod/full`; do **not** use `biggest-gainers` for backtest |
| 2 | Delisted ticker historical prices (BBBY/SIVB/WEWK) | **PASS** (prices) / **PARTIAL** (profile.isActivelyTrading wrong, delistedDate null) | No | Use price-tail to detect delisting; try both `<T>` and `<T>Q` symbol forms; do not rely on profile flags |
| 3 | News depth for backtest window | **PASS** (≥ ~10 yr for AAPL) | No | OK to wire `news/stock` for narrative agent across full 7-yr window; spot-check small-caps before live |
| 4 | PIT fundamentals (filingDate + acceptedDate) | **PASS** | No | Wire §8e cutoff against `acceptedDate` (timestamp), not `filingDate` (date-only) |

---

## Probe operational stats

- **Total raw FMP calls:** 11 / 20 (55 % of budget)
- **Per-group counts:** `probe_gainers_now=1`, `probe_gainers_dated=1`, `probe_delisted_hist=3`, `probe_delisted_hist_legacy=1`, `probe_delisted_profile=1`, `probe_news_latest=1`, `probe_news_2018=1`, `probe_news_2015=1`, `probe_pit_income=1`
- **Sticky pause activations:** 0
- **Rate-limit waits:** 0 ms (all 11 calls fit comfortably in the 600/min window)
- **TTL cache hits / misses / entries:** 0 / 0 / 0 — expected, because the probe used the low-level `_api_call` (the cached wrappers like `get_company_profile` go through `_TTLCache.get/set`, but `_api_call` does not, by design). No anomaly.

## Unexpected findings worth flagging

1. **`profile.isActivelyTrading` is unreliable for delisted issuers.** BBBY returns `True` and `delistedDate=null` despite being delisted in 2023. **Do not trust this flag** for survivorship-bias filtering. Use price-tail instead.
2. **`biggest-gainers` silently ignores `date`** — no error code, no warning, no audit field. A naive backtest harness that passes `date=` here will produce excellent-looking but completely lookahead-contaminated results.
3. **Post-bankruptcy "Q" symbols are inconsistent.** SIVBQ and WEWKQ return data; BBBYQ is empty (BBBY itself is the only path). Adapter should try both forms.
4. **`acceptedDate` is timestamp-precision** (e.g. `"2024-08-01 18:03:34"`) and can predate `filingDate` by a calendar day — meaningful for `allowed_data_cutoff` enforcement on intraday `decision_timestamp`s.
