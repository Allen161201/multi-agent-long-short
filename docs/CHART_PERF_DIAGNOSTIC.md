# Market Data Tab — Performance Diagnostic (read-only)

**Date:** 2026-04-27. **Ticker:** AAPL. **Total FMP calls used:** 4 (within ≤5 budget).
**Method:** (A) in-process adapter timing with `_api_call` monkey-patched to split FMP HTTP from normalization; (B) HTTP timing via `curl -w` against a freshly-booted dashboard; (C) static code analysis (no calls) for the inspector fan-out and the frontend SVG render path.

## Cold-cache stage breakdown (ms)

| stage                            | intraday | daily |
|---|---:|---:|
| FMP HTTP `_api_call`             | 350     | 508   |
| normalize + dict build + cache.set | 0.3   | 1.9   |
| `json.dumps` (server-side)       | 1.1     | 5.1   |
| Flask handler total over the wire (curl) | 347 | 499 |

## Warm-cache stage breakdown (ms)

| stage                            | intraday | daily |
|---|---:|---:|
| wrapper total (cache lookup only)| 0.01    | 0.01  |
| `json.dumps` (server-side)       | 1.0     | 5.0   |
| Flask handler total over the wire (curl) | 18  | 11    |

## Payload size

| | rows | `json.dumps` | wire (Flask debug=on) |
|---|---:|---:|---:|
| intraday (5min, ~10 trading days)  | 780  | 93 KB  | 129 KB |
| daily (25 yr, full history)        | 5000 | 522 KB | 753 KB |

The wire payload is ~40% larger than `json.dumps` because Flask's debug mode pretty-prints JSON (`JSONIFY_PRETTYPRINT_REGULAR=True`); production would match the dumps size.

## Other endpoints on the inspector load (`/api/fmp/ticker_inspector`)

The inspector handler runs **sequentially**, no parallelism: `get_quote` (1) → `get_chart_intraday` (1) → 4× `get_technical_indicator` (4) → `get_fundamentals_snapshot` which fans out to `income/balance/cashflow/ratios_ttm/key_metrics` (5) → `get_corporate_calendar` which fans out to `earnings/dividends/splits` (3) → `get_dcf_valuation` (1) → `get_company_profile` (1) = **16 cold FMP calls in series**. At the measured median 350 ms/call, that's an estimated **~5.6 s** for a cold inspector load — and that fan-out is what the user feels as "Market Data tab slow", not the chart itself.

## Lazy-daily check

Verified by reading `app.js`: `_ensureDailyLoaded()` is only called from the TF button handler when `!INTRADAY_TFS.has(tf) && !_chartState.dailyBars`. Default page load is `1D`, which never triggers daily. Daily fetches on the first `1M+` click only. **No PREFETCH_BUG.**

## Frontend render (code analysis, not measured)

`_renderChartForActiveTf` builds the polyline `points` string in O(n) and replaces `wrap.innerHTML` once per TF click. Bar counts: 1D ≈ 78, 3D ≈ 234, 1W ≈ 390, 1M ≈ 21, 3M ≈ 63, 1Y ≈ 252, 5Y ≈ 1260, ALL ≈ 5000. Even at `ALL` (~5000 points, ~70 KB attribute) modern browsers render an SVG `polyline` in <150 ms. The hover handler only mutates a few SVG attributes and the tooltip's text/position — O(1) per `mousemove`. **Not the bottleneck.**

## Redundant-call check

TF clicks call only `_renderChartForActiveTf` (pure client) and at most one `_ensureDailyLoaded` (one-shot, guarded by `dailyLoading` flag). No profile/quote/inspector re-fetch on TF click. ✓

## Bottleneck verdict

| label | applies? | notes |
|---|---|---|
| **FMP_LATENCY**         | YES (primary) | Every cold endpoint is ~300–500 ms; the inspector serializes 16 of them. |
| **SERVER_SERIALIZATION**| minor | `json.dumps` of the daily payload is 5 ms — not material. Debug-mode pretty-printing inflates wire by ~40%, but this is a dev-only artefact. |
| **CLIENT_RENDER**       | no | Even at 5000 points, render is well under user-perceptible latency. |
| **PREFETCH_BUG**        | no | Daily is correctly lazy. |

## Recommended fix (do NOT implement yet)

The chart endpoints are not the problem; the inspector handler is. Parallelize `/api/fmp/ticker_inspector`'s 16 sequential FMP calls with `concurrent.futures.ThreadPoolExecutor` (FMP requests are I/O-bound, the rate-limiter and `_TTLCache` are already thread-safe). With ~8 workers, the cold load drops from ≈5.6 s to ≈0.7 s — a single FMP round trip plus the longest fan-out. As a separate, smaller win, disable Flask's `JSONIFY_PRETTYPRINT_REGULAR` (or set `app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False`) so the daily payload sheds ~230 KB on the wire. The chart-specific code (`get_chart_intraday`, `get_chart_daily`, the SVG renderer) needs no changes.
