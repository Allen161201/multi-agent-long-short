"""
Build the frozen master universe artifact (`universe_master_v1.json`).

Read-only with respect to production code: imports `fmp_adapter._api_call`
to get the rate-limiter, sticky-pause, redaction filter, and key-redacting
logger filter, but does NOT modify any adapter / rule / dashboard file.

Hard budget for THIS script: ≤ 26 raw FMP calls (the parent task allots
≤ 30 total; the field-shape probe `_probe_list_endpoints.py` already
spent 4 calls).

Universe is a TICKER LIST. It is not bound to any backtest year window —
that's the backtest module's job downstream.

Filter rules **applied** (only what list-endpoint fields permit):
  * symbol must be non-empty.
  * For DELISTED rows: exchange ∈ {NYSE, NASDAQ, AMEX, NYSE Arca}.
  * For BOTH lists: drop rows whose companyName matches the existing
    fmp_adapter `_NAME_NON_EQUITY` regex (ETF / Fund / Trust / Notes /
    Preferred / Warrants / Rights / Leveraged / xN-Long-Short / etc.).
    This is a name-pattern heuristic, NOT a primary filter; it is best-
    effort because the list endpoints do not expose `type`.
  * Symbol-suffix flags (`.WS`, `.PR`, `.U`, `.W`, `-WS`, `-W`, `-U`,
    `-R`, trailing `W`/`R`/`U` after a punctuation mark): these rows
    are dropped. Plain trailing single letters with no separator
    (e.g. ASGNW) are NOT dropped, because they collide with legitimate
    common-stock symbols (e.g. WBD).

Filter rules **deferred** to decision-time PIT filter:
  * exchange filter for ACTIVE list — `stock-list` does not expose
    exchange. Cannot apply here without per-ticker profile calls
    (forbidden).
  * `type` filter — neither list endpoint exposes `type`. Cannot apply
    here without per-ticker profile calls (forbidden).
  * market-cap floor, price floor, listing-duration floor — require
    quote / profile and a decision-time stamp; defer to PIT filter.
  * is_actively_trading recheck — `profile.isActivelyTrading` was
    proven unreliable for delisted issuers in the feasibility probe
    (e.g. BBBY shows isActivelyTrading=True). Use the price-tail-based
    detector in `utils/delisting_detection.py` at decision time.

Output:
  data/universe/universe_master_v1.json   (frozen artifact)
  data/universe/universe_master_v1.meta.json (build telemetry sidecar)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

# Load .env without printing secrets
ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    import os
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from data_adapters import fmp_adapter as fmp  # noqa: E402

# ── Output paths ───────────────────────────────────────────────────
OUT_DIR = ROOT / "data" / "universe"
OUT_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT = OUT_DIR / "universe_master_v1.json"
META = OUT_DIR / "universe_master_v1.meta.json"

# ── Budget for this script (≤ 26 to stay inside ≤ 30 total) ────────
SCRIPT_CALL_BUDGET = 26
DELISTED_PAGE_HARD_CAP = 24   # leaves 2 calls headroom

ALLOWED_EXCHANGES = {
    # Canonical
    "NYSE", "NASDAQ", "AMEX", "NYSE Arca", "NYSE ARCA",
    # Variants FMP has been seen to emit
    "NYSEArca", "AMERICAN", "NYSE AMERICAN", "NYSEAMERICAN",
    "NASDAQGS", "NASDAQGM", "NASDAQCM", "NYQ",
    "BATS", "CBOE",
}

# Reuse the same name-pattern regex the FMP adapter uses for search-result
# filtering. This is a best-effort second-line filter, not a primary one.
_NAME_NON_EQUITY = re.compile(
    r"\b("
    r"ETF|"
    r"ETN|"
    r"Trust|"
    r"Fund|"
    r"Fd|"
    r"Income\s+Strategy|"
    r"Daily\s+Target|"
    r"Bull\s+\dX|"
    r"Bear\s+\dX|"
    r"\dX\s+(Long|Short|Inverse|Bull|Bear)|"
    r"(Long|Short|Inverse|Bull|Bear)\s+\dX|"
    r"Leveraged|"
    r"Note(s)?|"
    r"Preferred|"
    r"Warrants?|"
    r"Rights"
    r")\b",
    re.IGNORECASE,
)

# Symbol-suffix patterns that indicate non-common-stock instruments.
# We require an explicit separator (`.` or `-`) before the suffix letter,
# because plain trailing W / R / U / P collide with legitimate common-
# stock tickers (e.g. WBD, MMM final M, TSU, etc.).
_SYMBOL_SUFFIX_NON_EQUITY = re.compile(
    r"[.-](WS|PR[A-Z]?|U|W|R|UN|RT|PFD|PP|PQ|PA|PB|PC|PD|PE|PF|PG|PH|PI|PJ|PK|PL|PM|PN|PR|PS|PT|PU|PV|PW|PX|PY|PZ)$",
    re.IGNORECASE,
)


def call_count() -> int:
    return sum(fmp.get_call_group_summary().values())


# ── Step 1: pull active stock-list (1 call) ────────────────────────
def fetch_active_list() -> list[dict]:
    print("[1] Fetching active stock-list ...")
    data, meta = fmp._api_call("stock-list", group="universe_active")
    if not meta.get("ok"):
        raise SystemExit(f"stock-list failed: {meta}")
    if not isinstance(data, list):
        raise SystemExit(f"stock-list returned non-list: {type(data)}")
    print(f"    ok rows={len(data)}")
    return data


# ── Step 2: pull delisted-companies, paginated ─────────────────────
def fetch_delisted_list(page_cap: int) -> tuple[list[dict], dict]:
    print(f"[2] Fetching delisted-companies (page cap = {page_cap}) ...")
    rows: list[dict] = []
    pages_used = 0
    last_filled_page_size = None
    truncated = False
    earliest_delist_date_seen = None

    for page in range(page_cap):
        # Budget guard: leave at least 1 call so the script can finish cleanly.
        if call_count() >= SCRIPT_CALL_BUDGET:
            print(f"    budget reached at page {page}; stopping.")
            truncated = True
            break

        data, meta = fmp._api_call("delisted-companies", {"page": page},
                                     group=f"universe_delisted_p{page}")
        if not meta.get("ok"):
            print(f"    page={page} HTTP {meta.get('http_status')} "
                  f"err={meta.get('error_short')} → stop")
            break
        if not isinstance(data, list) or not data:
            print(f"    page={page} → empty, stopping")
            break
        rows.extend(data)
        pages_used += 1
        last_filled_page_size = len(data)
        if data:
            tail_date = data[-1].get("delistedDate")
            if tail_date:
                earliest_delist_date_seen = tail_date
        print(f"    page={page} rows={len(data)} tail_delistedDate={tail_date}")

        # Last page heuristic: if FMP returned fewer than 100, we're at the end.
        if len(data) < 100:
            print(f"    page={page} returned <100 → end of stream")
            break
    else:
        # Loop exhausted page_cap without breaking — coverage truncated.
        truncated = True

    return rows, {
        "pages_pulled": pages_used,
        "last_page_size": last_filled_page_size,
        "earliest_delist_date_seen": earliest_delist_date_seen,
        "truncated_by_budget_or_cap": truncated,
    }


# ── Filtering ──────────────────────────────────────────────────────
def is_symbol_clean(symbol: str | None) -> tuple[bool, str | None]:
    if not isinstance(symbol, str):
        return False, "not_a_string"
    s = symbol.strip()
    if not s:
        return False, "empty"
    # FMP sometimes emits suffixes like ".OL" / ".L" / ".PA" for foreign
    # listings. Plain "." with letters after it can also be class-share
    # markers (e.g. BRK.B). We KEEP class-share dots; we drop only
    # explicit non-equity suffixes (handled separately via regex).
    return True, None


def name_looks_non_equity(company_name: str | None) -> bool:
    if not company_name:
        return False
    return bool(_NAME_NON_EQUITY.search(company_name))


def symbol_looks_non_equity(symbol: str) -> bool:
    return bool(_SYMBOL_SUFFIX_NON_EQUITY.search(symbol))


def filter_active(raw_rows: list[dict]) -> tuple[list[dict], dict]:
    kept: list[dict] = []
    drop_reasons: dict[str, int] = {}

    def drop(reason: str):
        drop_reasons[reason] = drop_reasons.get(reason, 0) + 1

    for r in raw_rows:
        symbol = (r.get("symbol") or "").strip()
        company = (r.get("companyName") or "").strip()
        ok, why = is_symbol_clean(symbol)
        if not ok:
            drop(f"bad_symbol_{why}")
            continue
        if symbol_looks_non_equity(symbol):
            drop("symbol_suffix_non_equity")
            continue
        if name_looks_non_equity(company):
            drop("name_non_equity_pattern")
            continue
        kept.append({
            "symbol": symbol,
            "exchange": None,             # not exposed by stock-list
            "type": None,                 # not exposed by stock-list
            "status": "active",
            "list_endpoint_source": "stock-list",
            "company_name": company,
            "ipo_date": None,
            "delisted_date": None,
        })
    return kept, drop_reasons


def filter_delisted(raw_rows: list[dict]) -> tuple[list[dict], dict]:
    kept: list[dict] = []
    drop_reasons: dict[str, int] = {}

    def drop(reason: str):
        drop_reasons[reason] = drop_reasons.get(reason, 0) + 1

    for r in raw_rows:
        symbol = (r.get("symbol") or "").strip()
        company = (r.get("companyName") or "").strip()
        exchange = (r.get("exchange") or "").strip()
        ok, why = is_symbol_clean(symbol)
        if not ok:
            drop(f"bad_symbol_{why}")
            continue
        # Exchange filter — only delisted carries this field
        if exchange and exchange not in ALLOWED_EXCHANGES:
            drop(f"exchange_excluded:{exchange}")
            continue
        if symbol_looks_non_equity(symbol):
            drop("symbol_suffix_non_equity")
            continue
        if name_looks_non_equity(company):
            drop("name_non_equity_pattern")
            continue
        kept.append({
            "symbol": symbol,
            "exchange": exchange or None,
            "type": None,                 # not exposed
            "status": "delisted",
            "list_endpoint_source": "delisted-companies",
            "company_name": company,
            "ipo_date": r.get("ipoDate"),
            "delisted_date": r.get("delistedDate"),
        })
    return kept, drop_reasons


# ── Best-effort alias detection ────────────────────────────────────
# If a delisted issuer has the same companyName as an active row but a
# different symbol (e.g. BBBY ↔ BBBYQ), record the active alias on the
# delisted row. We do NOT synthesize symbols — we only annotate when a
# match is observed in the two list endpoints' raw output.
def annotate_aliases(active_kept: list[dict],
                       delisted_kept: list[dict]) -> int:
    by_name_active: dict[str, list[dict]] = {}
    for row in active_kept:
        nm = (row.get("company_name") or "").strip().lower()
        if nm:
            by_name_active.setdefault(nm, []).append(row)
    matched = 0
    for row in delisted_kept:
        nm = (row.get("company_name") or "").strip().lower()
        if not nm:
            continue
        candidates = by_name_active.get(nm, [])
        # Only annotate if there is exactly one active match AND the
        # symbols differ (otherwise the alias is the same row).
        if len(candidates) == 1 and candidates[0]["symbol"] != row["symbol"]:
            row["possible_alias_of"] = candidates[0]["symbol"]
            matched += 1
    return matched


# ── Main ───────────────────────────────────────────────────────────
def main():
    started_at = datetime.now(timezone.utc).isoformat()
    fp = fmp.get_key_fingerprint()
    print("=" * 60)
    print("Universe Builder v1 — frozen US common-stock master list")
    print("=" * 60)
    print(f"Started:   {started_at}")
    print(f"Key fp:    {fp.get('key_fingerprint')} (len={fp.get('key_length')})")
    print(f"Script budget: ≤ {SCRIPT_CALL_BUDGET} raw FMP calls")
    print(f"Calls already on counter: {call_count()}")

    # 1) Active list
    if call_count() >= SCRIPT_CALL_BUDGET:
        raise SystemExit("budget exhausted before active fetch")
    raw_active = fetch_active_list()

    # 2) Delisted list (paginated)
    raw_delisted, delisted_meta = fetch_delisted_list(DELISTED_PAGE_HARD_CAP)

    # 3) Filter
    print("[3] Filtering ...")
    active_kept, active_drops = filter_active(raw_active)
    delisted_kept, delisted_drops = filter_delisted(raw_delisted)
    print(f"    active   : kept {len(active_kept):>6}  drops {sum(active_drops.values()):>6}")
    print(f"    delisted : kept {len(delisted_kept):>6}  drops {sum(delisted_drops.values()):>6}")

    # 4) Alias annotation
    aliases = annotate_aliases(active_kept, delisted_kept)
    print(f"[4] Annotated {aliases} delisted rows with possible_alias_of (active match by company name)")

    tickers = active_kept + delisted_kept
    # Stable order: status (active first), then symbol asc.
    tickers.sort(key=lambda r: (0 if r["status"] == "active" else 1, r["symbol"]))

    artifact = {
        "universe_version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_endpoints": [
            "/stable/stock-list (active US)",
            "/stable/delisted-companies (paginated)",
        ],
        "active_count": len(active_kept),
        "delisted_count": len(delisted_kept),
        "filter_rules_applied": [
            "symbol non-empty",
            "exchange ∈ {NYSE, NASDAQ, AMEX, NYSE Arca, NYSE American, NASDAQ-GS/GM/CM, BATS, CBOE} — DELISTED LIST ONLY (active list does not expose exchange)",
            "drop rows whose companyName matches non-equity name regex (ETF/Fund/Trust/Notes/Preferred/Warrants/Rights/Leveraged/xN-Long-Short)",
            "drop rows whose symbol carries an explicit non-equity suffix (.WS/.PR/.U/.W/-WS/-W/-U/-R/-PFD)",
        ],
        "filter_rules_deferred": [
            "exchange filter on ACTIVE list — stock-list endpoint does not expose `exchange`; deferred to decision-time PIT filter (or per-ticker profile lookup, which is forbidden in this task)",
            "`type` filter (stock vs ETF/fund/trust/preferred at metadata level) — neither list endpoint exposes `type`; today only name + symbol heuristics applied; deferred to decision-time PIT filter",
            "market-cap floor — requires quote, deferred",
            "price floor — requires quote, deferred",
            "listing-duration floor — requires profile.ipoDate at decision time, deferred",
            "is_actively_trading recheck — profile flag is unreliable (BBBY case); use utils/delisting_detection.detect_delisting_from_price_tail at decision time instead",
        ],
        "delisted_pagination_meta": delisted_meta,
        "tickers": tickers,
    }

    ARTIFACT.write_text(json.dumps(artifact, indent=2, default=str))
    META.write_text(json.dumps({
        "started_at_utc": started_at,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "script_call_budget": SCRIPT_CALL_BUDGET,
        "total_calls_so_far": call_count(),
        "calls_by_group": fmp.get_call_group_summary(),
        "rate_status": fmp.get_rate_limit_status(),
        "active_drop_reasons": active_drops,
        "delisted_drop_reasons": delisted_drops,
        "raw_active_rows_received": len(raw_active),
        "raw_delisted_rows_received": len(raw_delisted),
        "alias_annotations": aliases,
    }, indent=2, default=str))

    print("=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"  active kept   : {len(active_kept)}")
    print(f"  delisted kept : {len(delisted_kept)}")
    print(f"  total tickers : {len(tickers)}")
    print(f"  delisted pages pulled: {delisted_meta['pages_pulled']}")
    print(f"  delisted earliest seen: {delisted_meta['earliest_delist_date_seen']}")
    print(f"  truncated by budget?:  {delisted_meta['truncated_by_budget_or_cap']}")
    print(f"  total raw FMP calls so far (this process): {call_count()}")
    print(f"  artifact: {ARTIFACT}")
    print(f"  meta:     {META}")


if __name__ == "__main__":
    main()
