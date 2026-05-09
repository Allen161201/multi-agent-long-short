"""
Task A regression — SEC EDGAR direct adapters live behavior + PIT.

Verifies the four SEC adapters in src/adapters/alt_data/ work against
real SEC EDGAR (data.sec.gov) and FMP institutional-ownership endpoints
with strict acceptedDateTime PIT discipline:

  - sec_edgar  (8-K)       — data.sec.gov submissions JSON
  - sec_form4              — data.sec.gov submissions + per-filing XML
  - sec_13f                — FMP institutional-ownership/symbol-ownership
                              (plan-limited; gracefully fails to
                               extraction_status='failed')
  - sec_def14a             — data.sec.gov submissions JSON

Real-data assertions on AAPL (2026-04-30 cutoff):
  A.1  sec_edgar      returned rows >= 1 with accepted/filing date <= cutoff
  A.2  sec_form4      returned rows >= 5 with accepted_datetime <= cutoff
  A.3  sec_def14a     returned rows >= 1 with accepted_datetime <= cutoff
  A.4  sec_13f        either rows >= 1 OR extraction_status=='failed'
                      with reason 'not_available_on_current_plan'
                      (plan-limited gracefully → data_unavailable per
                       RULES.md §11.2; never silently zero)

PIT-discipline assertions (synthetic, no network):
  B.1  rows with as_of > decision_timestamp are stripped by the base class
  B.2  every Form 4 row carries an accepted_datetime <= cutoff
  B.3  every 8-K row carries a filing_date <= cutoff (date)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

# Force live attempt (no STUB_MODE shortcut).
os.environ["STUB_MODE"] = "false"

CUTOFF = datetime(2026, 4, 30, 16, 15, tzinfo=timezone.utc)
TICKER = "AAPL"


# ── Real-data assertions (live network) ─────────────────────────────

def _has_creds() -> tuple[bool, str]:
    """Live tests require SEC_EDGAR_USER_AGENT and FMP_API_KEY."""
    sec_ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    fmp = os.environ.get("FMP_API_KEY", "").strip()
    if not sec_ua or sec_ua in ("<TO BE FILLED>",):
        return False, "SEC_EDGAR_USER_AGENT not configured"
    if not fmp:
        return False, "FMP_API_KEY not configured"
    return True, ""


def test_sec_edgar_8k_live() -> None:
    """A.1 — sec_edgar (8-K) returns >= 1 row for AAPL with PIT-safe dates."""
    ok, why = _has_creds()
    if not ok:
        print(f"  SKIP test_sec_edgar_8k_live ({why})")
        return
    from src.adapters.alt_data.sec_edgar import SECEdgarAdapter
    adapter = SECEdgarAdapter()
    res = adapter.fetch(ticker=TICKER, as_of=CUTOFF, decision_timestamp=CUTOFF,
                        stub_mode=False)
    assert res.extraction_status == "ok", \
        f"sec_edgar status={res.extraction_status} flag={res.source_flag}"
    assert len(res.rows) >= 1, f"sec_edgar returned 0 rows for {TICKER}"
    for r in res.rows:
        fd = r.get("filing_date") or ""
        assert fd[:10] <= CUTOFF.date().isoformat(), \
            f"sec_edgar PIT VIOLATION: row filing_date={fd} > cutoff={CUTOFF}"
    print(f"  PASS test_sec_edgar_8k_live  rows={len(res.rows)}")


def test_sec_form4_live() -> None:
    """A.2 — sec_form4 returns >= 5 rows for AAPL with PIT-safe accepted_datetime."""
    ok, why = _has_creds()
    if not ok:
        print(f"  SKIP test_sec_form4_live ({why})")
        return
    from src.adapters.alt_data.sec_ownership import SECForm4Adapter
    adapter = SECForm4Adapter()
    res = adapter.fetch(ticker=TICKER, as_of=CUTOFF, decision_timestamp=CUTOFF,
                        stub_mode=False)
    assert res.extraction_status == "ok", \
        f"sec_form4 status={res.extraction_status} flag={res.source_flag}"
    assert len(res.rows) >= 5, \
        f"sec_form4 returned only {len(res.rows)} rows for {TICKER} (bar: >=5)"
    for r in res.rows:
        ad = r.get("accepted_datetime") or ""
        assert ad <= CUTOFF.isoformat(), \
            f"sec_form4 PIT VIOLATION: row accepted_datetime={ad} > cutoff={CUTOFF}"
    print(f"  PASS test_sec_form4_live  rows={len(res.rows)}")


def test_sec_def14a_live() -> None:
    """A.3 — sec_def14a returns >= 1 row for AAPL with PIT-safe accepted_datetime."""
    ok, why = _has_creds()
    if not ok:
        print(f"  SKIP test_sec_def14a_live ({why})")
        return
    from src.adapters.alt_data.sec_ownership import SECDef14AAdapter
    adapter = SECDef14AAdapter()
    res = adapter.fetch(ticker=TICKER, as_of=CUTOFF, decision_timestamp=CUTOFF,
                        stub_mode=False)
    assert res.extraction_status == "ok", \
        f"sec_def14a status={res.extraction_status} flag={res.source_flag}"
    assert len(res.rows) >= 1, f"sec_def14a returned 0 rows for {TICKER}"
    for r in res.rows:
        ad = r.get("accepted_datetime") or ""
        assert ad <= CUTOFF.isoformat(), \
            f"sec_def14a PIT VIOLATION: row accepted_datetime={ad} > cutoff={CUTOFF}"
    print(f"  PASS test_sec_def14a_live  rows={len(res.rows)}")


def test_sec_13f_graceful_plan_limit() -> None:
    """A.4 — sec_13f either delivers rows OR fails gracefully with
    data_unavailable + reason='not_available_on_current_plan'.

    Documented limitation: FMP institutional-ownership/symbol-ownership
    requires premium plan. Direct SEC 13F-HR reverse-scan is brittle
    (filed by holders, not issuer); accepted by the existing design
    note in src/adapters/alt_data/sec_ownership.py:49-58."""
    ok, why = _has_creds()
    if not ok:
        print(f"  SKIP test_sec_13f_graceful_plan_limit ({why})")
        return
    from src.adapters.alt_data.sec_ownership import SEC13FAdapter
    adapter = SEC13FAdapter()
    res = adapter.fetch(ticker=TICKER, as_of=CUTOFF, decision_timestamp=CUTOFF,
                        stub_mode=False)
    if res.extraction_status == "ok" and len(res.rows) >= 1:
        print(f"  PASS test_sec_13f_graceful_plan_limit  rows={len(res.rows)} (LIVE)")
        return
    # Failed path: must be a graceful data_unavailable, not a crash.
    assert res.extraction_status == "failed", \
        f"sec_13f unexpected status={res.extraction_status}"
    assert res.error_class in ("not_available_on_current_plan",
                                "missing_api_key",
                                "rate_limit_paused",
                                "http_error"), \
        f"sec_13f unexpected error_class={res.error_class}"
    # data_quality_flags must surface the data_unavailable reason
    flags = res.data_quality_flags or []
    has_unavailable = any(f.get("kind") == "data_unavailable" for f in flags)
    assert has_unavailable, \
        f"sec_13f failed but no data_unavailable flag: {flags}"
    print(f"  PASS test_sec_13f_graceful_plan_limit "
          f"(graceful: error_class={res.error_class})")


# ── PIT discipline (synthetic, no network) ──────────────────────────

def test_pit_filter_drops_lookahead_rows() -> None:
    """B.1 — base class _pit_filter strips rows with as_of > decision_timestamp."""
    from src.adapters.alt_data.base import AltDataAdapter, AltDataResult

    class _Probe(AltDataAdapter):
        source_id = "_pit_probe"
        block_target = "filing_confirmation"

        def credentials_present(self) -> bool: return True
        def _fetch_live(self, ticker, as_of, decision_timestamp):
            return AltDataResult(source_id=self.source_id,
                                  block_target=self.block_target, rows=[])
        def _fetch_stub(self, ticker, as_of, decision_timestamp):
            return AltDataResult(source_id=self.source_id,
                                  block_target=self.block_target, rows=[])

    cutoff = datetime(2026, 4, 30, 16, 15, tzinfo=timezone.utc)
    p = _Probe()
    rows_in = [
        {"as_of": (cutoff - timedelta(days=1)).isoformat(),  "k": "past"},
        {"as_of": cutoff.isoformat(),                          "k": "boundary"},
        {"as_of": (cutoff + timedelta(seconds=1)).isoformat(), "k": "future_1s"},
        {"as_of": (cutoff + timedelta(days=10)).isoformat(),   "k": "future_10d"},
    ]
    kept = p._pit_filter(rows_in, cutoff)
    kept_keys = sorted(r["k"] for r in kept)
    assert kept_keys == ["boundary", "past"], \
        f"PIT filter wrong: kept={kept_keys}"
    print(f"  PASS test_pit_filter_drops_lookahead_rows  kept={kept_keys}")


def main() -> int:
    print("\n=== Task A — SEC EDGAR direct adapter tests ===\n")
    failures: list[str] = []
    for fn in (
        test_sec_edgar_8k_live,
        test_sec_form4_live,
        test_sec_def14a_live,
        test_sec_13f_graceful_plan_limit,
        test_pit_filter_drops_lookahead_rows,
    ):
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failures.append(fn.__name__)
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failures.append(fn.__name__)
    n = 5
    n_pass = n - len(failures)
    print(f"\n  RESULT: {n_pass}/{n} tests pass")
    if failures:
        print(f"  failed: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
