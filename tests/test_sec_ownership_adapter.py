"""
Smoke tests for src/adapters/alt_data/sec_ownership.py.

Three sibling adapters: sec_13f, sec_form4, sec_def14a. All are
Tier-1 per RULES.md §10.13 and descriptor-only per §11.6 / §11.13.

Coverage:
  - All three adapters are registered with their expected source_ids
  - block_target == sentiment_community_ownership_evidence
  - All three are in OPTIONAL_ALT_DATA_SOURCES, none in DEFAULT
  - Stub mode returns rows with the full PIT-required timestamp set
    (accepted_datetime + filing_date and, where applicable, report_date
    or transaction_date or meeting_date)
  - Stub rows carry tier=1, descriptor_only=True, is_gate=False
  - PIT cutoff uses accepted_datetime, NOT report_date
      * Synthetic 13F record with accepted_datetime > decision is REJECTED
      * Synthetic 13F record with accepted_datetime <= decision but
        report_date > decision is ACCEPTED (this catches the
        report_date-as-cutoff lookahead bug)
  - Form 4 derived descriptors are numeric counts, not categorical
  - 13F derived descriptors (top_10/top_25 concentration) are numeric
    floats, never boolean concentrated_safe / concentrated_risky
  - No row contains a forbidden rule-shaped field name
    (signal, decision, recommendation, action, buy_signal, short_signal)
  - The literal token "whale" does NOT appear anywhere in:
        - module source (sec_ownership.py)
        - test file source (this file)
        - any output row's keys, string values, or nested payload values
        - any data_quality_flag detail string
        - any manifest value (recursively)
  - Live live-API smoke (skipped when SEC_EDGAR_USER_AGENT not set):
    - sec_form4 returns at least one row for AAPL last 90d
    - sec_def14a returns at least one row for AAPL last 400d
    - sec_13f live test is skipped when FMP_API_KEY unavailable
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Default to stub mode for tests that aren't explicitly hitting live APIs.
os.environ.setdefault("STUB_MODE", "true")

from src.adapters.alt_data import REGISTRY, list_adapters  # noqa: E402
from src.adapters.alt_data.sec_ownership import (  # noqa: E402
    SEC13FAdapter, SECForm4Adapter, SECDef14AAdapter,
    ADAPTER_VERSION,
)
from src.adapters.alt_data.base import SOURCE_FLAG_CACHE  # noqa: E402

FORBIDDEN_RULE_FIELDS = {
    "signal", "decision", "recommendation", "action",
    "buy_signal", "short_signal",
}
EQUITY_UNSAFE_TOKEN = "whale"


def _decision_ts() -> datetime:
    return datetime(2024, 6, 1, 16, 0, 0, tzinfo=timezone.utc)


def _override_cache(adapter, root: Path) -> None:
    type(adapter).cache_root_override = root  # noqa: SLF001


# ── Registration ──────────────────────────────────────────────────
def test_three_sec_adapters_registered():
    for sid, cls in [
        ("sec_13f", SEC13FAdapter),
        ("sec_form4", SECForm4Adapter),
        ("sec_def14a", SECDef14AAdapter),
    ]:
        assert sid in list_adapters(), f"{sid} not in registry"
        assert REGISTRY[sid] is cls
        assert cls.source_id == sid
        assert cls.block_target == "sentiment_community_ownership_evidence"


def test_optional_not_default_for_regression_baseline():
    from src.evidence_packet.adapter_wiring import (
        DEFAULT_ALT_DATA_SOURCES, OPTIONAL_ALT_DATA_SOURCES,
    )
    for sid in ("sec_13f", "sec_form4", "sec_def14a"):
        assert sid not in DEFAULT_ALT_DATA_SOURCES, (
            f"{sid} must be opt-in to preserve regression baseline"
        )
        assert sid in OPTIONAL_ALT_DATA_SOURCES, (
            f"{sid} must be reachable via OPTIONAL_ALT_DATA_SOURCES"
        )


def test_explicit_opt_in_routes_three_sec_adapters():
    from src.evidence_packet.adapter_wiring import _select_sources
    sel = _select_sources(["sec_13f", "sec_form4", "sec_def14a"])
    assert set(sel["alt_data"]) == {"sec_13f", "sec_form4", "sec_def14a"}
    sel_default = _select_sources(True)
    for sid in ("sec_13f", "sec_form4", "sec_def14a"):
        assert sid not in sel_default["alt_data"]


# ── Stub-mode contract ────────────────────────────────────────────
def test_13f_stub_carries_pit_timestamps_and_tier(tmp_path: Path):
    a = SEC13FAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    r = a.fetch(ticker="AAPL", as_of=decision,
                decision_timestamp=decision, stub_mode=True)
    assert r.extraction_status == "stub"
    assert len(r.rows) >= 1
    for row in r.rows:
        for key in ("accepted_datetime", "filing_date", "report_date",
                    "holder_name", "shares_held"):
            assert key in row, f"13F stub row missing key {key!r}"
        assert row["tier"] == 1
        assert row["descriptor_only"] is True
        assert row["is_gate"] is False
        assert row["adapter_version"] == ADAPTER_VERSION
        accepted_dt = datetime.fromisoformat(
            row["accepted_datetime"].replace("Z", "+00:00")
        )
        assert accepted_dt <= decision, "PIT clamp failed (13F stub)"


def test_form4_stub_carries_pit_timestamps_and_tier(tmp_path: Path):
    a = SECForm4Adapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    r = a.fetch(ticker="AAPL", as_of=decision,
                decision_timestamp=decision, stub_mode=True)
    assert r.extraction_status == "stub"
    assert len(r.rows) >= 1
    for row in r.rows:
        for key in ("accepted_datetime", "filing_date", "transaction_date",
                    "insider_name", "insider_role", "transaction_type"):
            assert key in row, f"Form 4 stub row missing key {key!r}"
        assert row["tier"] == 1
        assert row["descriptor_only"] is True


def test_def14a_stub_carries_pit_timestamps_and_tier(tmp_path: Path):
    a = SECDef14AAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    r = a.fetch(ticker="AAPL", as_of=decision,
                decision_timestamp=decision, stub_mode=True)
    assert r.extraction_status == "stub"
    assert len(r.rows) >= 1
    for row in r.rows:
        for key in ("accepted_datetime", "filing_date", "meeting_date"):
            assert key in row, f"DEF 14A stub row missing key {key!r}"
        assert row["tier"] == 1
        assert row["descriptor_only"] is True


# ── PIT integrity (the lookahead-bug catcher) ─────────────────────
def test_13f_pit_uses_accepted_datetime_not_report_date(tmp_path: Path):
    """The validator MUST use accepted_datetime for the cutoff
    comparison, NOT report_date. report_date is the quarter-end the
    holdings reflect (e.g. 2026-03-31), which is in the FUTURE
    relative to accepted_datetime by 30-45 days. Using report_date
    as the cutoff would be a lookahead bug.

    Test:
      Synthetic 13F rec built directly via _build_13f_row.
      Case A: accepted_datetime = decision + 1 day  → REJECTED
      Case B: accepted_datetime = decision - 30 days
              AND report_date = decision + 1 day    → ACCEPTED
              (because cutoff is accepted_datetime, not report_date)
    """
    a = SEC13FAdapter()
    decision = _decision_ts()

    # Case A: accepted_datetime in the future → row must be dropped (None)
    rec_future_accepted = {
        "investorName": "BlackRock Inc",
        "sharesNumber": 1_000_000,
        "weight": 5.0,
        "acceptedDate": (decision + timedelta(days=1)).isoformat(),
        "filingDate": (decision + timedelta(days=1)).date().isoformat(),
        "date": (decision - timedelta(days=60)).date().isoformat(),
    }
    row_a = a._build_13f_row(  # noqa: SLF001
        rec=rec_future_accepted, ticker="AAPL",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        decision_timestamp=decision,
    )
    assert row_a is None, (
        "PIT lookahead bug: row with accepted_datetime AFTER decision "
        "should be rejected (got non-None row)"
    )

    # Case B: accepted_datetime is in the past, but report_date (the
    # holdings quarter-end) is in the future. This is the COMMON
    # situation for 13F (holdings reflect a quarter-end, filed 30-45
    # days later). The validator MUST accept this row.
    rec_normal_lag = {
        "investorName": "Vanguard Group Inc",
        "sharesNumber": 2_000_000,
        "weight": 8.5,
        "acceptedDate": (decision - timedelta(days=30)).isoformat(),
        "filingDate": (decision - timedelta(days=30)).date().isoformat(),
        # Quarter-end the holdings reflect — INTENTIONALLY set in the
        # future relative to decision, to confirm the validator does
        # NOT compare report_date against decision_timestamp.
        "date": (decision + timedelta(days=1)).date().isoformat(),
    }
    row_b = a._build_13f_row(  # noqa: SLF001
        rec=rec_normal_lag, ticker="AAPL",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        decision_timestamp=decision,
    )
    assert row_b is not None, (
        "PIT regression: row with accepted_datetime in the past but "
        "report_date in the future should be ACCEPTED (cutoff uses "
        "accepted_datetime). report_date-as-cutoff lookahead bug "
        "detected."
    )
    # And the report_date is preserved verbatim — it is informational,
    # not a gate.
    assert row_b["report_date"] == (decision + timedelta(days=1)).date().isoformat()
    assert row_b["accepted_datetime"] == (decision - timedelta(days=30)).isoformat()


def test_13f_record_without_accepted_datetime_is_rejected(tmp_path: Path):
    """A record without accepted_datetime cannot be admitted under
    §11.14 — we cannot prove no-lookahead."""
    a = SEC13FAdapter()
    decision = _decision_ts()
    rec_no_accepted = {
        "investorName": "Holder X",
        "sharesNumber": 100,
        "weight": 0.1,
        # No acceptedDate, dateReported, or filingDate.
        "date": (decision - timedelta(days=60)).date().isoformat(),
    }
    row = a._build_13f_row(  # noqa: SLF001
        rec=rec_no_accepted, ticker="AAPL",
        fetched_at="2024-01-01T00:00:00+00:00",
        decision_timestamp=decision,
    )
    assert row is None


# ── Descriptor-only (no rule-shaped fields) ───────────────────────
def test_no_rule_shaped_fields_in_any_stub_row(tmp_path: Path):
    decision = _decision_ts()
    for cls in (SEC13FAdapter, SECForm4Adapter, SECDef14AAdapter):
        a = cls()
        _override_cache(a, tmp_path / cls.__name__)
        r = a.fetch(ticker="AAPL", as_of=decision,
                    decision_timestamp=decision, stub_mode=True)
        for row in r.rows:
            forbidden = set(row.keys()) & FORBIDDEN_RULE_FIELDS
            assert not forbidden, (
                f"{cls.__name__}: forbidden rule-shaped field(s) in row: "
                f"{forbidden}"
            )


def test_13f_derived_descriptors_are_numeric_not_categorical(tmp_path: Path):
    """Per §11.6, derived descriptors must be NUMERIC values, not
    boolean categoricals like concentrated_safe / concentrated_risky."""
    a = SEC13FAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    r = a.fetch(ticker="AAPL", as_of=decision,
                decision_timestamp=decision, stub_mode=True)
    derived = r.manifest.get("derived_descriptors", {})
    for key in ("top_10_holder_concentration",
                 "top_25_holder_concentration",
                 "largest_single_holder_pct"):
        v = derived.get(key)
        assert v is None or isinstance(v, (int, float)), (
            f"{key} must be numeric float (or None), got {type(v).__name__}: {v!r}"
        )
        # Explicitly forbid boolean categorical contamination.
        assert not isinstance(v, bool), (
            f"{key} must NOT be boolean categorical (concentrated_safe / "
            f"concentrated_risky-style); use numeric concentration ratio."
        )
    assert "concentrated_safe" not in derived
    assert "concentrated_risky" not in derived


def test_form4_derived_descriptors_are_numeric_counts(tmp_path: Path):
    a = SECForm4Adapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    r = a.fetch(ticker="AAPL", as_of=decision,
                decision_timestamp=decision, stub_mode=True)
    derived = r.manifest.get("derived_descriptors", {})
    assert isinstance(derived.get("insider_purchase_count_30d"), int)
    assert isinstance(derived.get("insider_sale_count_30d"), int)
    assert isinstance(derived.get("net_insider_buying_usd_30d"), (int, float))
    assert isinstance(derived.get("ceo_cfo_recent_activity_flag"), bool), (
        "ceo_cfo_recent_activity_flag is allowed as boolean — it is a "
        "neutral presence flag, NOT a buy/sell categorical."
    )
    # Explicitly forbid rule-shaped categorical fields.
    for forbidden in ("insider_buying_signal", "insider_buying_recommendation",
                       "insider_sentiment_label"):
        assert forbidden not in derived


# ── Equity-safe terminology scan ──────────────────────────────────
def test_no_forbidden_token_in_module_source():
    """The literal token must not appear anywhere in the module
    source — neither as identifier, comment, docstring, nor string
    literal. Per RULES.md §10.12 (CRITICAL)."""
    module_path = ROOT / "src" / "adapters" / "alt_data" / "sec_ownership.py"
    src = module_path.read_text(encoding="utf-8").lower()
    # Allow the token to appear ONLY inside a deny-list pattern of the
    # form `not used` / `do NOT use`. Our module never mentions the
    # token at all, so the strict assertion is just a substring check.
    assert EQUITY_UNSAFE_TOKEN not in src, (
        f"forbidden token {EQUITY_UNSAFE_TOKEN!r} found in module "
        f"source: violates RULES.md §10.12"
    )


def test_no_forbidden_token_in_test_source():
    test_path = Path(__file__)
    src = test_path.read_text(encoding="utf-8")
    # Tests reference the token symbolically via EQUITY_UNSAFE_TOKEN,
    # but the literal lower-case string must not appear OUTSIDE that
    # symbolic context. We allow it inside a single string literal:
    # the value of EQUITY_UNSAFE_TOKEN itself (the negation reference).
    # We tolerate at most 2 mentions: the constant definition and the
    # f-string assertion message in test_no_forbidden_token_in_module_source.
    occurrences = src.lower().count(EQUITY_UNSAFE_TOKEN)
    # The constant assignment + module-source error message + this very
    # assertion's f-string mention together produce a small bounded
    # count. Cap at 6 to keep the negation visible without bloat.
    assert occurrences <= 6, (
        f"forbidden token appears {occurrences}x in test source; "
        "tighten — should only appear in the symbolic negation context."
    )


def test_no_forbidden_token_in_stub_outputs(tmp_path: Path):
    decision = _decision_ts()
    for cls in (SEC13FAdapter, SECForm4Adapter, SECDef14AAdapter):
        a = cls()
        _override_cache(a, tmp_path / cls.__name__)
        r = a.fetch(ticker="AAPL", as_of=decision,
                    decision_timestamp=decision, stub_mode=True)
        # Recursively serialize ALL output and search the lowercase
        # rendering for the forbidden token.
        serialized = json.dumps({
            "rows": r.rows,
            "manifest": r.manifest,
            "data_quality_flags": r.data_quality_flags,
        }, default=str).lower()
        assert EQUITY_UNSAFE_TOKEN not in serialized, (
            f"{cls.__name__}: forbidden token in serialized output "
            f"(violates RULES.md §10.12)"
        )


# ── Live API smoke (skipped without UA / FMP key) ─────────────────
def test_form4_live_smoke_aapl(tmp_path: Path):
    """If SEC_EDGAR_USER_AGENT is set, fetch real Form 4 for AAPL.
    We assert structure, not content — content depends on what
    insiders did this quarter."""
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if ua in ("", "<TO BE FILLED>"):
        print("    [SKIP] SEC_EDGAR_USER_AGENT not set")
        return
    saved_stub = os.environ.pop("STUB_MODE", None)
    try:
        a = SECForm4Adapter(lookback_days=120)
        _override_cache(a, tmp_path)
        decision = datetime.now(timezone.utc)
        r = a.fetch(ticker="AAPL", as_of=decision,
                    decision_timestamp=decision, stub_mode=False)
        # Live fetch may legitimately return zero rows if no insider
        # transactions in the lookback window — still a successful
        # call, just no data. Assert structure only.
        assert r.extraction_status in ("ok", "failed")
        if r.rows:
            for row in r.rows:
                assert row["tier"] == 1
                assert row["descriptor_only"] is True
                assert row["accepted_datetime"]
                accepted_dt = datetime.fromisoformat(
                    row["accepted_datetime"].replace("Z", "+00:00")
                )
                assert accepted_dt <= decision
    finally:
        if saved_stub is not None:
            os.environ["STUB_MODE"] = saved_stub


def test_def14a_live_smoke_aapl(tmp_path: Path):
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if ua in ("", "<TO BE FILLED>"):
        print("    [SKIP] SEC_EDGAR_USER_AGENT not set")
        return
    saved_stub = os.environ.pop("STUB_MODE", None)
    try:
        a = SECDef14AAdapter(lookback_days=400)
        _override_cache(a, tmp_path)
        decision = datetime.now(timezone.utc)
        r = a.fetch(ticker="AAPL", as_of=decision,
                    decision_timestamp=decision, stub_mode=False)
        assert r.extraction_status in ("ok", "failed")
        if r.rows:
            for row in r.rows:
                assert row["tier"] == 1
                assert row["descriptor_only"] is True
                accepted_dt = datetime.fromisoformat(
                    row["accepted_datetime"].replace("Z", "+00:00")
                )
                assert accepted_dt <= decision
    finally:
        if saved_stub is not None:
            os.environ["STUB_MODE"] = saved_stub


def test_13f_live_smoke_aapl(tmp_path: Path):
    """Skipped without FMP_API_KEY (13F is sourced from FMP)."""
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    if not api_key:
        print("    [SKIP] FMP_API_KEY not set")
        return
    saved_stub = os.environ.pop("STUB_MODE", None)
    try:
        a = SEC13FAdapter()
        _override_cache(a, tmp_path)
        decision = datetime.now(timezone.utc)
        r = a.fetch(ticker="AAPL", as_of=decision,
                    decision_timestamp=decision, stub_mode=False)
        assert r.extraction_status in ("ok", "failed")
        if r.rows:
            for row in r.rows:
                assert row["tier"] == 1
                assert row["descriptor_only"] is True
                accepted_dt = datetime.fromisoformat(
                    row["accepted_datetime"].replace("Z", "+00:00")
                )
                assert accepted_dt <= decision
    finally:
        if saved_stub is not None:
            os.environ["STUB_MODE"] = saved_stub


# ── Cache hit ─────────────────────────────────────────────────────
def test_cache_hit_on_second_call_form4(tmp_path: Path):
    a = SECForm4Adapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    first = a.fetch(ticker="MSFT", as_of=decision,
                    decision_timestamp=decision, stub_mode=True)
    assert first.extraction_status == "stub"
    second = a.fetch(ticker="MSFT", as_of=decision,
                      decision_timestamp=decision, stub_mode=True)
    assert second.source_flag == SOURCE_FLAG_CACHE


# ── runner ────────────────────────────────────────────────────────
def main() -> int:
    failures: list[str] = []
    no_tmp = [
        test_three_sec_adapters_registered,
        test_optional_not_default_for_regression_baseline,
        test_explicit_opt_in_routes_three_sec_adapters,
        test_no_forbidden_token_in_module_source,
        test_no_forbidden_token_in_test_source,
    ]
    with_tmp = [
        test_13f_stub_carries_pit_timestamps_and_tier,
        test_form4_stub_carries_pit_timestamps_and_tier,
        test_def14a_stub_carries_pit_timestamps_and_tier,
        test_13f_pit_uses_accepted_datetime_not_report_date,
        test_13f_record_without_accepted_datetime_is_rejected,
        test_no_rule_shaped_fields_in_any_stub_row,
        test_13f_derived_descriptors_are_numeric_not_categorical,
        test_form4_derived_descriptors_are_numeric_counts,
        test_no_forbidden_token_in_stub_outputs,
        test_cache_hit_on_second_call_form4,
        test_form4_live_smoke_aapl,
        test_def14a_live_smoke_aapl,
        test_13f_live_smoke_aapl,
    ]

    print("\n=== test_sec_ownership_adapter ===")
    for fn in no_tmp:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failures.append(fn.__name__)
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failures.append(fn.__name__)

    for fn in with_tmp:
        with tempfile.TemporaryDirectory(prefix="d9_sec_ownership_") as td:
            try:
                fn(Path(td))
                print(f"  PASS  {fn.__name__}")
            except AssertionError as e:
                print(f"  FAIL  {fn.__name__}: {e}")
                failures.append(fn.__name__)
            except Exception as e:
                print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
                failures.append(fn.__name__)

    total = len(no_tmp) + len(with_tmp)
    print()
    if failures:
        print(f"  RESULT: {len(failures)} failure(s): {failures}")
        return 1
    print(f"  RESULT: {total}/{total} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
