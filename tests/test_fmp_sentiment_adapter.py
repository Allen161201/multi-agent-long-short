"""
Smoke tests for src/adapters/alt_data/fmp_sentiment.py.

Covers:
  - Adapter is registered with source_id="fmp_sentiment"
  - block_target == sentiment_community_ownership_evidence
  - Stub mode returns one row per endpoint, all with descriptor_only=True,
    is_gate=False, and the correct tier (T1/T2/T4) per RULES.md §10.13
  - PIT clamp: rows respect decision_timestamp
  - Cache hit on 2nd call (source_flag flips to "cache")
  - Live path with no FMP_API_KEY -> credentials_present()=False ->
    base class auto-falls-through to stub (no network call attempted)
  - Live path with HTTP 404 / 403 -> error_class="not_available_on_current_plan",
    rows=[], adapter does NOT raise
  - Live path with timeout -> error_class="timeout", no silent retry
  - Endpoint-level isolation: one bad endpoint doesn't kill the others
  - Adapter is NOT in DEFAULT_ALT_DATA_SOURCES (regression baseline guard)
  - Adapter IS reachable via OPTIONAL_ALT_DATA_SOURCES when explicitly named
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Force stub mode for tests that aren't explicitly testing live behaviour.
os.environ.setdefault("STUB_MODE", "true")

from src.adapters.alt_data import REGISTRY, list_adapters  # noqa: E402
from src.adapters.alt_data.fmp_sentiment import (  # noqa: E402
    FMPSentimentAdapter,
    ENDPOINTS,
    ADAPTER_VERSION,
)
from src.adapters.alt_data.base import SOURCE_FLAG_CACHE  # noqa: E402


def _decision_ts() -> datetime:
    return datetime(2024, 6, 1, 16, 0, 0, tzinfo=timezone.utc)


def _override_cache(adapter: FMPSentimentAdapter, root: Path) -> None:
    type(adapter).cache_root_override = root  # noqa: SLF001


# ── Registration ──────────────────────────────────────────────────
def test_registry_contains_fmp_sentiment():
    assert "fmp_sentiment" in list_adapters()
    cls = REGISTRY["fmp_sentiment"]
    assert cls is FMPSentimentAdapter
    assert cls.source_id == "fmp_sentiment"
    assert cls.block_target == "sentiment_community_ownership_evidence"


def test_default_alt_data_sources_does_not_include_fmp_sentiment():
    """Regression baseline guard: the byte-identical 30/30 hash must
    not shift just because the new adapter was registered."""
    from src.evidence_packet.adapter_wiring import (
        DEFAULT_ALT_DATA_SOURCES, OPTIONAL_ALT_DATA_SOURCES,
    )
    assert "fmp_sentiment" not in DEFAULT_ALT_DATA_SOURCES, (
        "fmp_sentiment must NOT be in DEFAULT_ALT_DATA_SOURCES — it is "
        "opt-in until the regression baseline is re-pinned."
    )
    assert "fmp_sentiment" in OPTIONAL_ALT_DATA_SOURCES


def test_explicit_opt_in_routes_fmp_sentiment():
    """When a caller explicitly passes 'fmp_sentiment' in live_adapters,
    it should be routed (not silently dropped)."""
    from src.evidence_packet.adapter_wiring import _select_sources
    sel = _select_sources(["fmp_sentiment"])
    assert "fmp_sentiment" in sel["alt_data"]
    # And live_adapters=True (the bulk default) does NOT include it
    sel_all = _select_sources(True)
    assert "fmp_sentiment" not in sel_all["alt_data"]


# ── Stub mode ─────────────────────────────────────────────────────
def test_stub_mode_returns_one_row_per_endpoint(tmp_path: Path):
    a = FMPSentimentAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    r = a.fetch(ticker="AAPL", as_of=decision,
                decision_timestamp=decision, stub_mode=True)
    assert r.extraction_status == "stub"
    assert r.source_flag == "mock_fallback"
    assert len(r.rows) == len(ENDPOINTS)


def test_stub_rows_carry_descriptor_only_and_correct_tiers(tmp_path: Path):
    a = FMPSentimentAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    r = a.fetch(ticker="AAPL", as_of=decision,
                decision_timestamp=decision, stub_mode=True)
    expected_tiers = {ep["key"]: ep["tier"] for ep in ENDPOINTS}
    expected_paths = {ep["path"] for ep in ENDPOINTS}
    seen_paths: set[str] = set()
    for row in r.rows:
        assert row["descriptor_only"] is True, "RULES.md §11.6 violated"
        assert row["is_gate"] is False, "RULES.md §11.6 violated"
        assert row["tier"] in {"T1", "T2", "T4"}
        assert row["adapter_version"] == ADAPTER_VERSION
        assert row["source"] == "fmp_sentiment"
        assert row["ticker"] == "AAPL"
        assert "as_of" in row and row["as_of"]
        assert "fetched_at" in row and row["fetched_at"]
        assert "payload" in row and isinstance(row["payload"], dict)
        seen_paths.add(row["source_endpoint"])
        # Tier consistency with endpoint key
        ep_key = row["payload"].get("endpoint_key")
        if ep_key in expected_tiers:
            assert row["tier"] == expected_tiers[ep_key]
    assert seen_paths == expected_paths, (
        f"endpoint coverage gap: missing {expected_paths - seen_paths}, "
        f"unexpected {seen_paths - expected_paths}"
    )


def test_stub_pit_clamp_respects_decision_timestamp(tmp_path: Path):
    a = FMPSentimentAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    far_future = decision + timedelta(days=30)
    r = a.fetch(ticker="MSFT", as_of=far_future,
                decision_timestamp=decision, stub_mode=True)
    for row in r.rows:
        row_dt = datetime.fromisoformat(row["as_of"].replace("Z", "+00:00"))
        assert row_dt <= decision, (
            f"PIT clamp failed: row as_of={row_dt} > decision={decision}"
        )


def test_cache_hit_on_second_call(tmp_path: Path):
    a = FMPSentimentAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    first = a.fetch(ticker="NVDA", as_of=decision,
                    decision_timestamp=decision, stub_mode=True)
    assert first.extraction_status == "stub"
    second = a.fetch(ticker="NVDA", as_of=decision,
                      decision_timestamp=decision, stub_mode=True)
    assert second.source_flag == SOURCE_FLAG_CACHE, (
        f"expected source_flag=cache on 2nd call, got {second.source_flag!r}"
    )


# ── credentials_present gating ────────────────────────────────────
def test_missing_api_key_drives_stub_fallthrough(tmp_path: Path):
    a = FMPSentimentAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    saved = os.environ.pop("FMP_API_KEY", None)
    saved_stub = os.environ.pop("STUB_MODE", None)
    try:
        # No api key, no STUB_MODE -> credentials_present()=False ->
        # base class auto-falls-through to stub.
        r = a.fetch(ticker="AAPL", as_of=decision,
                    decision_timestamp=decision)
        assert r.extraction_status == "stub"
        assert r.source_flag == "mock_fallback"
    finally:
        if saved is not None:
            os.environ["FMP_API_KEY"] = saved
        if saved_stub is not None:
            os.environ["STUB_MODE"] = saved_stub


# ── Live-path mocking ─────────────────────────────────────────────
def _make_response(*, status: int, json_body=None, text: str = "",
                    raise_exc: Exception | None = None):
    r = MagicMock()
    r.status_code = status
    r.ok = (200 <= status < 300)
    r.text = text
    if raise_exc is not None:
        r.json.side_effect = raise_exc
    else:
        r.json.return_value = json_body if json_body is not None else []
    return r


def test_404_marks_endpoint_not_available_on_current_plan(tmp_path: Path):
    """A 404 from an endpoint must not crash the adapter; it should
    record error_class='not_available_on_current_plan' and continue."""
    a = FMPSentimentAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    saved = os.environ.get("FMP_API_KEY")
    saved_stub = os.environ.pop("STUB_MODE", None)
    os.environ["FMP_API_KEY"] = "test_dummy_key_FAKE"
    try:
        with patch(
            "src.adapters.alt_data.fmp_sentiment.requests.get",
            return_value=_make_response(status=404, json_body={"error": "no plan"}),
        ):
            r = a.fetch(ticker="AAPL", as_of=decision,
                        decision_timestamp=decision, stub_mode=False)
        # All endpoints 404'd -> result is failed (no rows), but
        # adapter did NOT raise.
        assert r.extraction_status == "failed"
        assert r.rows == []
        endpoint_results = r.manifest.get("endpoint_results", [])
        assert len(endpoint_results) == len(ENDPOINTS)
        for ep_meta in endpoint_results:
            assert ep_meta["error_class"] == "not_available_on_current_plan"
    finally:
        if saved is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = saved
        if saved_stub is not None:
            os.environ["STUB_MODE"] = saved_stub


def test_timeout_marks_endpoint_timeout_no_retry(tmp_path: Path):
    """Timeout on requests.get -> error_class='timeout', no silent retry."""
    import requests
    a = FMPSentimentAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    saved = os.environ.get("FMP_API_KEY")
    saved_stub = os.environ.pop("STUB_MODE", None)
    os.environ["FMP_API_KEY"] = "test_dummy_key_FAKE"
    call_count = {"n": 0}
    def _raise_timeout(*args, **kwargs):
        call_count["n"] += 1
        raise requests.Timeout("simulated timeout")
    try:
        with patch(
            "src.adapters.alt_data.fmp_sentiment.requests.get",
            side_effect=_raise_timeout,
        ):
            r = a.fetch(ticker="AAPL", as_of=decision,
                        decision_timestamp=decision, stub_mode=False)
        # Exactly 4 calls — one per endpoint — and zero retries.
        assert call_count["n"] == len(ENDPOINTS), (
            f"expected {len(ENDPOINTS)} HTTP attempts, got {call_count['n']} "
            "(silent retry detected)"
        )
        assert r.extraction_status == "failed"
        endpoint_results = r.manifest.get("endpoint_results", [])
        for ep_meta in endpoint_results:
            assert ep_meta["error_class"] == "timeout", (
                f"got error_class={ep_meta['error_class']!r}"
            )
    finally:
        if saved is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = saved
        if saved_stub is not None:
            os.environ["STUB_MODE"] = saved_stub


def test_partial_success_one_bad_endpoint(tmp_path: Path):
    """If 1 endpoint 404s but the other 3 return data, the adapter
    yields rows for the 3 working endpoints + records the 404 in the
    manifest (+data_quality_flags). One bad endpoint must not poison
    the others."""
    a = FMPSentimentAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    saved = os.environ.get("FMP_API_KEY")
    saved_stub = os.environ.pop("STUB_MODE", None)
    os.environ["FMP_API_KEY"] = "test_dummy_key_FAKE"

    good_body = [{
        "symbol": "AAPL",
        "publishedDate": "2024-05-30 10:00:00",
        "title": "Some news",
        "text": "Text body",
    }]

    call_count = {"n": 0}
    def _selective_response(url, *args, **kwargs):
        call_count["n"] += 1
        # Fail only the press-releases endpoint
        if "press-releases" in url:
            return _make_response(status=404, json_body={"error": "plan"})
        return _make_response(status=200, json_body=good_body)

    try:
        with patch(
            "src.adapters.alt_data.fmp_sentiment.requests.get",
            side_effect=_selective_response,
        ):
            r = a.fetch(ticker="AAPL", as_of=decision,
                        decision_timestamp=decision, stub_mode=False)
        # 3 working endpoints * 1 row each = 3 rows
        assert r.extraction_status == "ok"
        assert len(r.rows) == 3
        endpoint_results = r.manifest.get("endpoint_results", [])
        # Exactly one endpoint should have error_class set
        errored = [m for m in endpoint_results if m.get("error_class")]
        assert len(errored) == 1
        assert errored[0]["path"] == "press-releases"
        assert errored[0]["error_class"] == "not_available_on_current_plan"
        # And data_quality_flags should record the partial failure
        kinds = {f.get("kind") for f in r.data_quality_flags}
        assert "endpoint_unavailable" in kinds
    finally:
        if saved is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = saved
        if saved_stub is not None:
            os.environ["STUB_MODE"] = saved_stub


def test_429_trips_shared_fmp_sticky_pause(tmp_path: Path):
    """A 429 from any endpoint must trip the SHARED FMP sticky pause
    (not a per-adapter one) — that is the whole point of reusing the
    fmp_adapter rate limiter."""
    from src.data_adapters import fmp_adapter as fmp_mod
    a = FMPSentimentAdapter()
    _override_cache(a, tmp_path)
    decision = _decision_ts()
    saved = os.environ.get("FMP_API_KEY")
    saved_stub = os.environ.pop("STUB_MODE", None)
    os.environ["FMP_API_KEY"] = "test_dummy_key_FAKE"

    # Reset shared sticky pause to a known state before the test.
    fmp_mod._sticky_pause_until = 0.0
    fmp_mod._sticky_pause_reason = ""

    try:
        with patch(
            "src.adapters.alt_data.fmp_sentiment.requests.get",
            return_value=_make_response(status=429, json_body={"error": "rate limit"}),
        ):
            r = a.fetch(ticker="AAPL", as_of=decision,
                        decision_timestamp=decision, stub_mode=False)
        # Adapter must have tripped the shared sticky pause.
        paused, reason, _remaining = fmp_mod._is_sticky_paused()
        assert paused, "expected shared FMP sticky pause to be tripped on 429"
        # Result is failed (no rows).
        assert r.extraction_status == "failed"
        assert r.rows == []
    finally:
        # Clean up so other tests don't see a bogus pause.
        fmp_mod._sticky_pause_until = 0.0
        fmp_mod._sticky_pause_reason = ""
        if saved is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = saved
        if saved_stub is not None:
            os.environ["STUB_MODE"] = saved_stub


def test_credentials_present_reflects_api_key_env():
    a = FMPSentimentAdapter()
    saved = os.environ.get("FMP_API_KEY")
    try:
        os.environ.pop("FMP_API_KEY", None)
        assert a.credentials_present() is False
        os.environ["FMP_API_KEY"] = "x"
        assert a.credentials_present() is True
    finally:
        if saved is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = saved


# ── runner ────────────────────────────────────────────────────────
def main() -> int:
    failures: list[str] = []
    no_tmp = [
        test_registry_contains_fmp_sentiment,
        test_default_alt_data_sources_does_not_include_fmp_sentiment,
        test_explicit_opt_in_routes_fmp_sentiment,
        test_credentials_present_reflects_api_key_env,
    ]
    with_tmp = [
        test_stub_mode_returns_one_row_per_endpoint,
        test_stub_rows_carry_descriptor_only_and_correct_tiers,
        test_stub_pit_clamp_respects_decision_timestamp,
        test_cache_hit_on_second_call,
        test_missing_api_key_drives_stub_fallthrough,
        test_404_marks_endpoint_not_available_on_current_plan,
        test_timeout_marks_endpoint_timeout_no_retry,
        test_partial_success_one_bad_endpoint,
        test_429_trips_shared_fmp_sticky_pause,
    ]

    print("\n=== test_fmp_sentiment_adapter ===")
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
        with tempfile.TemporaryDirectory(prefix="d8_fmp_sentiment_") as td:
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
