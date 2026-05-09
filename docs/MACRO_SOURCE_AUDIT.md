# Macro Block Source-Label Audit

**Date:** 2026-04-28
**Investigator:** evidence-packet v1 self-review
**Scope:** read-only. No production code, no rule files, no `.env` modified.

## Root cause (one paragraph)

The v1 evidence-packet macro block (`src/evidence_packet/blocks/macro.py`) reads the **wrong meta-key name** when interpreting the FRED adapter's response. It reads `_cache_data_mode` (the field emitted by the legacy date-keyed `get_macro_indicators(decision_date)` path) but the call we actually make goes through `get_macro_indicators_for_dashboard()` (the live dashboard path), which emits `_data_mode` and `_source` instead. Because `_cache_data_mode` is therefore `None`, neither the `cache_data_mode == "live"` branch nor the `cache_data_mode == "mock"` branch fires; control falls through to the trailing `else: Source.LIVE_FRED if not came_from_cache else Source.CACHE`, and on a normal cache-hit day this yields `Source.CACHE`. My v1 report's finding #5 — claiming `.env` had `DATA_MODE=mock` — was wrong; `.env` is correct.

## Is the data actually live?

**Yes — the macro block is genuinely live FRED data.** Evidence: `outputs/fred_cache/fred_2026-04-27.json` carries `"data_mode": "live"`, `"api_success": true`, and 16 indicators with real FRED values (e.g. `treasury_1m.value = 3.69`, `source: "FRED"`). The dashboard variant `get_macro_indicators_for_dashboard()` writes that file with `data_mode_label="live"` only after a successful FRED API call (`fred_adapter.py:558`); a mock fallback would have written `data_mode_label="mock"` (line 572).

## Is the source label "cache" correct or a mislabel?

**Technically correct, semantically misleading.** The FRED daily cache is the legitimate persistence layer (one cache file per ET-date, cleared and re-populated each new day with live FRED data). When the dashboard variant returns a payload with `_from_cache: True` AND `_data_mode: "live"`, the data IS live FRED, served through the daily cache. So `source: cache` is true, but it understates the lineage — a downstream consumer would reasonably interpret "cache" as "stale / not live", which is wrong.

## Recommended fix (NOT implemented)

Change `blocks/macro.py` to:
1. Read **both** `_data_mode` (dashboard variant) and `_cache_data_mode` (legacy) — accept either.
2. When `_data_mode == "live"` (or fallback `_cache_data_mode == "live"`):
   - if `_from_cache` is True and `_source == "daily_cache"`: emit `source = "live_fred"` AND a sub-flag `served_from_daily_cache: true` so the lineage is visible without misclassifying the upstream as stale.
   - if `_from_cache` is False (`_source == "fresh_api"`): emit `source = "live_fred"` plain.
3. When `_data_mode == "mock"` or `_source == "mock_fallback"`: emit `source = "mock_fallback"`.
4. Update the indicator-level secondary check to recognize `source: "FRED"` (the actual value emitted per indicator) in addition to the previously-assumed `live_fred`.

This is a one-block fix in `blocks/macro.py`. The FRED adapter does not need to change.

## Side note

The macro block also currently double-pulls FRED on cold cache (it calls `get_macro_indicators_for_dashboard()` and the dashboard had probably already pulled it earlier). Not a correctness bug, but the `api_calls_made: 0` it self-reports is honest only because the adapter pull doesn't go through `_api_call`. Worth noting for capacity planning if any future caller bypasses the daily cache.
