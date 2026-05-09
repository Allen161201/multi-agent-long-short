"""
Unit tests for src/adapters/alt_data/base.py and the three adapters
(wikipedia_pageviews, sec_edgar, github_public).

Reddit was scoped initially and excluded 2026-04-28 per RULES.md §10.13
(anti-pollution rationale). Tests below cover only the in-scope adapters.

Covers:
  - Registry self-registration
  - PIT filter drops post-cutoff rows
  - Cache hit on 2nd call (same disk root)
  - Stub mode is deterministic across reruns
  - source_flag honesty per RULES.md §11.3
  - Failed adapter returns Data unavailable, never zero
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Force stub mode for ALL tests so they don't reach the network.
os.environ["STUB_MODE"] = "true"

from src.adapters.alt_data import (  # noqa: E402
    REGISTRY, get_adapter, list_adapters,
)
from src.adapters.alt_data.base import (  # noqa: E402
    AltDataAdapter, AltDataResult, SOURCE_FLAG_CACHE,
)
from src.adapters.alt_data.wikipedia_pageviews import (  # noqa: E402
    WikipediaPageviewsAdapter,
)
from src.adapters.alt_data.sec_edgar import SECEdgarAdapter  # noqa: E402
from src.adapters.alt_data.github_public import GitHubPublicAdapter  # noqa: E402


def _override_cache(adapter, root: Path) -> None:
    """Point an adapter's cache at a temp dir."""
    type(adapter).cache_root_override = root  # noqa: SLF001


def _decision_ts() -> datetime:
    return datetime(2024, 6, 1, 16, 0, 0, tzinfo=timezone.utc)


def test_registry_has_three_adapters():
    expected = {
        "wikipedia_pageviews",
        "sec_edgar",
        "github_public",
    }
    assert expected.issubset(set(list_adapters())), (
        f"missing from registry: {expected - set(list_adapters())}"
    )
    # Reddit excluded 2026-04-28 — must NOT be in registry.
    assert "reddit_public" not in list_adapters(), (
        "reddit_public should be excluded per RULES.md §10.13"
    )
    for sid in expected:
        cls = get_adapter(sid)
        # Class attrs honoured
        assert cls.source_id == sid
        assert cls.block_target


def test_as_of_clamped_to_decision_timestamp(tmp_path: Path):
    """When caller passes as_of > decision_timestamp, base class
    clamps as_of to decision_timestamp so no post-cutoff data is
    generated in the first place. The synthetic FakeAdapter test
    below covers downstream PIT filtering for adapters whose live
    payload genuinely straddles the cutoff."""
    adapter = WikipediaPageviewsAdapter()
    _override_cache(adapter, tmp_path)

    decision = _decision_ts()
    far_future = decision + timedelta(days=30)
    result = adapter.fetch(
        ticker="AAPL", as_of=far_future, decision_timestamp=decision,
        stub_mode=True,
    )
    assert result.extraction_status in ("stub", "ok")
    assert len(result.rows) > 0
    for row in result.rows:
        row_dt = datetime.fromisoformat(row["as_of"].replace("Z", "+00:00"))
        assert row_dt <= decision, (
            f"clamping failed: row as_of={row_dt} > decision={decision}"
        )


def test_cache_hit_on_second_call(tmp_path: Path):
    """Second call with same (ticker, as_of date) should serve from
    cache and return source_flag=cache."""
    adapter = WikipediaPageviewsAdapter()
    _override_cache(adapter, tmp_path)

    decision = _decision_ts()

    first = adapter.fetch(
        ticker="MSFT", as_of=decision, decision_timestamp=decision,
        stub_mode=True,
    )
    assert first.extraction_status == "stub"
    assert first.source_flag == "mock_fallback"

    second = adapter.fetch(
        ticker="MSFT", as_of=decision, decision_timestamp=decision,
        stub_mode=True,
    )
    assert second.source_flag == SOURCE_FLAG_CACHE, (
        f"expected source_flag=cache on 2nd call, got {second.source_flag!r}"
    )


def test_stub_mode_is_deterministic_across_reruns(tmp_path: Path):
    """Two stub fetches into two different empty caches must produce
    identical row content (deterministic by ticker hash)."""
    a = WikipediaPageviewsAdapter()
    b = WikipediaPageviewsAdapter()
    cache_a = tmp_path / "a"; cache_a.mkdir()
    cache_b = tmp_path / "b"; cache_b.mkdir()
    a.cache_root_override = cache_a
    b.cache_root_override = cache_b

    decision = _decision_ts()
    r1 = a.fetch(ticker="NVDA", as_of=decision,
                  decision_timestamp=decision, stub_mode=True)
    r2 = b.fetch(ticker="NVDA", as_of=decision,
                  decision_timestamp=decision, stub_mode=True)

    assert [r["views"] for r in r1.rows] == [r["views"] for r in r2.rows]
    assert [r["date"] for r in r1.rows] == [r["date"] for r in r2.rows]


def test_unmapped_ticker_github_returns_data_unavailable(tmp_path: Path):
    """A ticker not in the seed CSV must return data_unavailable, not
    zero (per RULES.md §11.2 / §11.8)."""
    adapter = GitHubPublicAdapter()
    _override_cache(adapter, tmp_path)
    decision = _decision_ts()

    # Live-mode path, but the resolver fails before any HTTP call.
    result = adapter.fetch(
        ticker="XYZ_NONEXISTENT", as_of=decision,
        decision_timestamp=decision, stub_mode=False,
    )
    assert result.extraction_status == "failed"
    assert result.error_class == "unmapped_ticker"
    assert result.rows == []
    assert result.source_flag == "live_github_public_failed"
    # Quality flag must be present so downstream agents see a warning.
    kinds = {f.get("kind") for f in result.data_quality_flags}
    assert "data_unavailable" in kinds


def test_sec_edgar_credentials_present_gates_correctly():
    """When User-Agent is unset or placeholder, credentials_present()
    returns False so the base class auto-falls-through to stub."""
    a = SECEdgarAdapter()
    saved = os.environ.get("SEC_EDGAR_USER_AGENT")
    try:
        os.environ["SEC_EDGAR_USER_AGENT"] = ""
        assert a.credentials_present() is False
        os.environ["SEC_EDGAR_USER_AGENT"] = "<TO BE FILLED>"
        assert a.credentials_present() is False
        os.environ["SEC_EDGAR_USER_AGENT"] = "MyApp/0.1 contact@example.com"
        assert a.credentials_present() is True
    finally:
        if saved is None:
            os.environ.pop("SEC_EDGAR_USER_AGENT", None)
        else:
            os.environ["SEC_EDGAR_USER_AGENT"] = saved


def test_pit_filter_drops_rows_with_as_of_after_decision(tmp_path: Path):
    """Direct base-class PIT-filter test using a synthetic subclass."""

    class FakeAdapter(AltDataAdapter):
        source_id = "_fake_unit_test"
        block_target = "alternative_data_features"

        def credentials_present(self) -> bool:
            return True

        def _fetch_live(self, ticker, as_of, decision_timestamp):
            # Emit rows straddling the decision_timestamp; base class
            # is responsible for dropping the post-cutoff ones.
            rows = []
            for offset in range(-5, 6):
                ts = decision_timestamp + timedelta(days=offset)
                rows.append({
                    "ticker": ticker,
                    "as_of": ts.isoformat(),
                    "value": offset,
                    "source": self.source_id,
                    "source_flag": self._live_source_flag(),
                })
            return AltDataResult(
                source_id=self.source_id,
                block_target=self.block_target,
                rows=rows,
                source_flag=self._live_source_flag(),
                manifest={"source_id": self.source_id, "ticker": ticker,
                          "rows_returned": len(rows)},
                extraction_status="ok",
            )

        def _fetch_stub(self, ticker, as_of, decision_timestamp):
            return self._fetch_live(ticker, as_of, decision_timestamp)

    a = FakeAdapter()
    a.cache_root_override = tmp_path
    decision = _decision_ts()
    r = a.fetch(ticker="ABC", as_of=decision,
                decision_timestamp=decision, stub_mode=True)
    # Original rows: -5..+5 = 11 rows. After PIT filter at decision: 6 rows.
    assert len(r.rows) == 6, f"expected 6 rows after PIT filter, got {len(r.rows)}"
    for row in r.rows:
        row_dt = datetime.fromisoformat(row["as_of"].replace("Z", "+00:00"))
        assert row_dt <= decision


def main() -> int:
    failures: list[str] = []
    test_funcs = [
        test_registry_has_three_adapters,
        test_sec_edgar_credentials_present_gates_correctly,
    ]
    # Run tests that need tmp_path
    tmp_funcs = [
        test_as_of_clamped_to_decision_timestamp,
        test_cache_hit_on_second_call,
        test_stub_mode_is_deterministic_across_reruns,
        test_unmapped_ticker_github_returns_data_unavailable,
        test_pit_filter_drops_rows_with_as_of_after_decision,
    ]

    print("\n=== test_alt_data_base ===")
    for fn in test_funcs:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failures.append(fn.__name__)
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failures.append(fn.__name__)

    for fn in tmp_funcs:
        with tempfile.TemporaryDirectory(prefix="d1_step_d_") as td:
            try:
                fn(Path(td))
                print(f"  PASS  {fn.__name__}")
            except AssertionError as e:
                print(f"  FAIL  {fn.__name__}: {e}")
                failures.append(fn.__name__)
            except Exception as e:
                print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
                failures.append(fn.__name__)

    print()
    if failures:
        print(f"  RESULT: {len(failures)} failure(s): {failures}")
        return 1
    print(f"  RESULT: {len(test_funcs) + len(tmp_funcs)}/"
          f"{len(test_funcs) + len(tmp_funcs)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
