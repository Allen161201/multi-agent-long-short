"""
Task B regression — Polygon.io news adapter live + PIT.

Verifies polygon_news at src/adapters/alt_data/polygon_news.py:

  - Real-data fetch on AAPL/NVDA/TSLA over 2026-04-01..2026-05-01
    returns >= 1 row each (not 0; not failed).
  - Every returned row carries published_utc <= cutoff (PIT discipline
    at adapter level — base class also enforces).
  - Synthetic test: a stub row with as_of > cutoff is rejected by
    base class _pit_filter.
  - Overlay test: when polygon delivers >= 1 rows for a packet at a
    cutoff inside a covered window, news_event_summary.items is
    replaced with polygon items (block.source = "polygon_news",
    items[0].source = "polygon_news").

Cost: $0 (HTTP only, no LLM).

Note on rate limit: free tier is 5 req/min. Tests intentionally limit
themselves to <=4 polygon HTTP calls total (1 per ticker + 1 overlay)
to stay below the per-minute ceiling without sleeping.
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

os.environ["STUB_MODE"] = "false"

CUTOFF_15 = datetime(2026, 4, 15, 16, 15, tzinfo=timezone.utc)
CUTOFF_30 = datetime(2026, 4, 30, 16, 15, tzinfo=timezone.utc)
TICKERS = ("AAPL", "NVDA", "TSLA")


def _has_creds() -> tuple[bool, str]:
    key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not key or key == "<TO BE FILLED>":
        return False, "POLYGON_API_KEY not set"
    return True, ""


# ── Real-data assertions (live network) ─────────────────────────────

def test_polygon_news_aapl_live() -> None:
    """B.4-AAPL — Polygon news for AAPL at 2026-04-30 cutoff returns >= 1 row,
    every published_utc <= cutoff."""
    ok, why = _has_creds()
    if not ok:
        print(f"  SKIP test_polygon_news_aapl_live ({why})")
        return
    from src.adapters.alt_data.polygon_news import PolygonNewsAdapter
    a = PolygonNewsAdapter(lookback_days=14, limit=20)
    res = a.fetch(ticker="AAPL", as_of=CUTOFF_30,
                  decision_timestamp=CUTOFF_30, stub_mode=False)
    assert res.extraction_status == "ok", \
        f"AAPL polygon status={res.extraction_status} flag={res.source_flag} " \
        f"err={res.error_class}"
    assert len(res.rows) >= 1, f"AAPL polygon returned 0 rows"
    for r in res.rows:
        ad = r.get("as_of") or ""
        assert ad <= CUTOFF_30.isoformat(), \
            f"AAPL polygon PIT VIOLATION: as_of={ad} > cutoff={CUTOFF_30}"
    print(f"  PASS test_polygon_news_aapl_live  rows={len(res.rows)}  "
          f"first_pub={res.rows[0].get('published_at_utc')}")


def test_polygon_news_nvda_live() -> None:
    """B.4-NVDA — same checks for NVDA."""
    ok, why = _has_creds()
    if not ok:
        print(f"  SKIP test_polygon_news_nvda_live ({why})")
        return
    from src.adapters.alt_data.polygon_news import PolygonNewsAdapter
    a = PolygonNewsAdapter(lookback_days=14, limit=20)
    res = a.fetch(ticker="NVDA", as_of=CUTOFF_30,
                  decision_timestamp=CUTOFF_30, stub_mode=False)
    assert res.extraction_status == "ok", \
        f"NVDA polygon status={res.extraction_status} flag={res.source_flag}"
    assert len(res.rows) >= 1, f"NVDA polygon returned 0 rows"
    for r in res.rows:
        ad = r.get("as_of") or ""
        assert ad <= CUTOFF_30.isoformat(), \
            f"NVDA polygon PIT VIOLATION: as_of={ad} > cutoff={CUTOFF_30}"
    print(f"  PASS test_polygon_news_nvda_live  rows={len(res.rows)}")


def test_polygon_news_tsla_live() -> None:
    """B.4-TSLA — same checks for TSLA."""
    ok, why = _has_creds()
    if not ok:
        print(f"  SKIP test_polygon_news_tsla_live ({why})")
        return
    from src.adapters.alt_data.polygon_news import PolygonNewsAdapter
    a = PolygonNewsAdapter(lookback_days=14, limit=20)
    res = a.fetch(ticker="TSLA", as_of=CUTOFF_30,
                  decision_timestamp=CUTOFF_30, stub_mode=False)
    assert res.extraction_status == "ok", \
        f"TSLA polygon status={res.extraction_status} flag={res.source_flag}"
    assert len(res.rows) >= 1, f"TSLA polygon returned 0 rows"
    for r in res.rows:
        ad = r.get("as_of") or ""
        assert ad <= CUTOFF_30.isoformat(), \
            f"TSLA polygon PIT VIOLATION: as_of={ad} > cutoff={CUTOFF_30}"
    print(f"  PASS test_polygon_news_tsla_live  rows={len(res.rows)}")


def test_polygon_news_pit_strict_cutoff() -> None:
    """B.3 — at cutoff 2026-04-15T16:15Z, no row with published_utc >
    cutoff may survive (defense-in-depth: adapter-level + base PIT)."""
    ok, why = _has_creds()
    if not ok:
        print(f"  SKIP test_polygon_news_pit_strict_cutoff ({why})")
        return
    from src.adapters.alt_data.polygon_news import PolygonNewsAdapter
    a = PolygonNewsAdapter(lookback_days=7, limit=20)
    res = a.fetch(ticker="AAPL", as_of=CUTOFF_15,
                  decision_timestamp=CUTOFF_15, stub_mode=False)
    assert res.extraction_status == "ok", \
        f"AAPL@2026-04-15 polygon status={res.extraction_status}"
    for r in res.rows:
        ad = r.get("as_of") or ""
        assert ad <= CUTOFF_15.isoformat(), \
            f"PIT FAIL: row as_of={ad} > cutoff={CUTOFF_15.isoformat()}"
    print(f"  PASS test_polygon_news_pit_strict_cutoff  "
          f"rows={len(res.rows)} all<=cutoff")


def test_polygon_synthetic_future_row_rejected() -> None:
    """B.3-synthetic — a row with as_of > cutoff is dropped by base
    class _pit_filter (zero-network unit check)."""
    from src.adapters.alt_data.polygon_news import PolygonNewsAdapter
    a = PolygonNewsAdapter()
    cutoff = datetime(2026, 4, 15, 16, 15, tzinfo=timezone.utc)
    rows_in = [
        {"as_of": (cutoff - timedelta(hours=2)).isoformat(),
         "title": "past_news", "id": "p1"},
        {"as_of": cutoff.isoformat(),
         "title": "boundary_news", "id": "b1"},
        {"as_of": (cutoff + timedelta(seconds=1)).isoformat(),
         "title": "future_1s_news", "id": "f1"},
        {"as_of": (cutoff + timedelta(days=2)).isoformat(),
         "title": "future_2d_news", "id": "f2"},
    ]
    kept = a._pit_filter(rows_in, cutoff)  # noqa: SLF001
    titles = sorted(r["title"] for r in kept)
    assert titles == ["boundary_news", "past_news"], \
        f"PIT filter wrong: kept titles={titles}"
    print(f"  PASS test_polygon_synthetic_future_row_rejected  kept={titles}")


# ── Overlay test (adapter_wiring.py integration) ────────────────────

def test_overlay_replaces_news_items_when_polygon_delivers() -> None:
    """B.2 — when polygon_news returns rows, overlay swaps
    news_event_summary.items for polygon items and tags block.source.
    Pure-data unit test: builds a minimal AltDataResult and applies the
    overlay function directly (no live HTTP)."""
    from src.evidence_packet.adapter_wiring import _overlay_alt_data_rows
    from src.adapters.alt_data.base import AltDataResult

    # Pre-existing FMP-populated block (default builder output shape).
    pre_existing_fmp_item = {
        "published_at": "2026-05-02 10:00:00",
        "published_at_utc": "2026-05-02T14:00:00+00:00",
        "title": "fmp_live_news_post_cutoff_should_be_replaced",
        "publisher": "FMP",
        "source": "fmp_news",
    }
    packet = {
        "news_event_summary": {
            "status": "ok",
            "ticker": "AAPL",
            "items": [pre_existing_fmp_item],
            "items_count": 1,
        },
    }

    polygon_row = {
        "as_of": "2026-04-30T15:00:00+00:00",
        "published_at_utc": "2026-04-30T15:00:00+00:00",
        "published_at": "2026-04-30 11:00:00",
        "title": "polygon_historical_news_should_replace",
        "publisher": "Reuters",
        "site": "reuters.com",
        "url": "https://reuters.com/x",
        "symbol": "AAPL",
        "tickers": ["AAPL"],
        "text_excerpt": "ex",
        "id": "p_overlay_1",
        "author": "Bot",
        "keywords": ["k1"],
        "insights_for_ticker": {"sentiment": "neutral",
                                  "sentiment_reasoning": "x"},
        "source": "polygon_news",
        "source_flag": "live_polygon_news",
    }
    result = AltDataResult(
        source_id="polygon_news",
        block_target="news_event_summary",
        rows=[polygon_row],
        source_flag="live_polygon_news",
        manifest={"source_id": "polygon_news", "rows_returned": 1},
        extraction_status="ok",
    )
    _overlay_alt_data_rows(packet, "polygon_news", result)

    block = packet["news_event_summary"]
    items = block["items"]
    assert len(items) == 1, f"overlay wrong row count: {len(items)}"
    assert items[0]["title"] == "polygon_historical_news_should_replace", \
        f"overlay did not replace FMP item: {items[0]['title']}"
    assert items[0]["source"] == "polygon_news", \
        f"item source not tagged: {items[0]['source']}"
    assert block["source"] == "polygon_news", \
        f"block.source not promoted to polygon_news: {block.get('source')}"
    assert "source_replacement_log" in block, \
        "source_replacement_log not recorded"
    print(f"  PASS test_overlay_replaces_news_items_when_polygon_delivers")


def test_overlay_keeps_fmp_items_when_polygon_empty() -> None:
    """B.2-empty — when polygon returns 0 rows, FMP items remain in
    place (live-only fallback). Polygon call still recorded in
    live_adapter_rows for forensics."""
    from src.evidence_packet.adapter_wiring import _overlay_alt_data_rows
    from src.adapters.alt_data.base import AltDataResult

    packet = {
        "news_event_summary": {
            "status": "ok",
            "ticker": "AAPL",
            "items": [{"title": "fmp_live_kept", "source": "fmp_news"}],
            "items_count": 1,
            "source": "live_fmp",
        },
    }
    empty_result = AltDataResult(
        source_id="polygon_news",
        block_target="news_event_summary",
        rows=[],
        source_flag="live_polygon_news",
        manifest={"source_id": "polygon_news", "rows_returned": 0},
        extraction_status="ok",
    )
    _overlay_alt_data_rows(packet, "polygon_news", empty_result)

    block = packet["news_event_summary"]
    assert len(block["items"]) == 1, "FMP items dropped when polygon empty"
    assert block["items"][0]["title"] == "fmp_live_kept"
    assert block["items"][0]["source"] == "fmp_news"
    assert block["source"] == "live_fmp", \
        f"block.source incorrectly promoted: {block.get('source')}"
    assert "live_adapter_rows" in block
    assert "polygon_news" in block["live_adapter_rows"]
    print(f"  PASS test_overlay_keeps_fmp_items_when_polygon_empty")


def main() -> int:
    print("\n=== Task B — Polygon news adapter tests ===\n")
    failures: list[str] = []
    for fn in (
        test_polygon_synthetic_future_row_rejected,
        test_overlay_replaces_news_items_when_polygon_delivers,
        test_overlay_keeps_fmp_items_when_polygon_empty,
        test_polygon_news_aapl_live,
        test_polygon_news_nvda_live,
        test_polygon_news_tsla_live,
        test_polygon_news_pit_strict_cutoff,
    ):
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failures.append(fn.__name__)
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failures.append(fn.__name__)
    n = 7
    n_pass = n - len(failures)
    print(f"\n  RESULT: {n_pass}/{n} tests pass")
    if failures:
        print(f"  failed: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
