"""
Polygon.io news adapter (D5 Option C, 2026-05-01).

Endpoint:    https://api.polygon.io/v2/reference/news
Auth:        apiKey query param (POLYGON_API_KEY env var)
Free tier:   5 req/min, 2-year historical lookback
PIT:         filter by published_utc <= decision_timestamp

Why this adapter exists
-----------------------
FMP `news/stock-latest` is a wall-clock-now endpoint — it returns the
most-recent items regardless of any from/to params. That makes it
useless for historical backtesting (D5 alt-data diagnostic confirmed:
0 items returned when running the 2026-04 window probes).

Polygon.io v2/reference/news supports the published_utc.gte /
published_utc.lte filters cleanly with 2-year lookback on the free
tier. This makes it the historical news source. The legacy FMP path
(`fmp_news`) remains in `blocks/news.py` as the live-only fallback.

Output rows
-----------
Each row carries the canonical PIT field `as_of` (= published_utc),
plus `published_at_utc` (the ISO string downstream blocks expect),
title, publisher, url, tickers, description, and any per-ticker
sentiment insights Polygon provides.

PIT discipline (Rule 5)
-----------------------
- Filter is `published_utc <= decision_timestamp` (NEVER inserted_utc
  or fetched_at).
- Base class `_pit_filter` enforces a second cutoff using `as_of`.
- Window: [decision_timestamp - 7d, decision_timestamp].

Rate limit (5 req/min)
----------------------
Base class self-throttles via rate_limit_per_second. At 5 req/min that
is 5/60 = 0.0833 req/sec, ≈ 12 seconds between calls. The base class
caches per (ticker, as_of_date), so a backtest that re-runs the same
ticker-day pair hits cache and does not consume the budget. A 1-month
backtest at full universe (~500 tickers × 23 days) will accumulate
≈11 500 cache misses; at 12s/call that is ~38 hours wall clock for
the first warm-up. Practical mitigations:
  - Backtest authors warm cache off-hours
  - Backtest can run 1 ticker per minute in parallel via the cache layer
  - Free tier upgrade if budget permits

Schema notes (verified by direct probe 2026-05-01)
--------------------------------------------------
GET /v2/reference/news?ticker=AAPL&apiKey=...&limit=2 returns:
  {
    "results": [
      {
        "id": "<sha256-like>",
        "publisher": {"name": "...", "homepage_url": "...", ...},
        "title": "...",
        "author": "...",
        "published_utc": "2026-05-02T01:32:00Z",  # ISO-8601 UTC
        "article_url": "...",
        "tickers": ["AAPL", "GOOG", ...],
        "description": "...",
        "keywords": [...],
        "insights": [
          {"ticker": "AAPL", "sentiment": "positive",
           "sentiment_reasoning": "..."},
          ...
        ]
      },
      ...
    ],
    "status": "OK",
    "next_url": "https://api.polygon.io/v2/reference/news?cursor=...",
    "count": <int>
  }

RULES.md anchors
----------------
  - §11.1 every row carries `as_of`
  - §11.2 missing data is `Data unavailable`, never zero
  - §11.3 source_flag ∈ {live_polygon_news, cache, ...}
  - §11.5 PIT: published_utc <= decision_timestamp
  - §19.15 sticky pause on 429; never silent retry
  - §19.16 redact apiKey in logs
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .base import AltDataAdapter, AltDataResult
from .registry import register_adapter

logger = logging.getLogger(__name__)

ENV_API_KEY = "POLYGON_API_KEY"
POLYGON_NEWS_URL = "https://api.polygon.io/v2/reference/news"
HTTP_TIMEOUT_S = 20
LOOKBACK_DAYS = 7
DEFAULT_LIMIT = 20
ADAPTER_VERSION = "v0.1"


@register_adapter
class PolygonNewsAdapter(AltDataAdapter):
    """Polygon.io v2/reference/news — historical news with PIT filter."""

    source_id = "polygon_news"
    block_target = "news_event_summary"

    # Free tier: 5 req/min = 0.0833 req/sec.
    rate_limit_per_second = 5.0 / 60.0
    sticky_pause_seconds_on_429 = 60

    def __init__(
        self,
        lookback_days: int = LOOKBACK_DAYS,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        super().__init__()
        self.lookback_days = lookback_days
        self.limit = limit

    def credentials_present(self) -> bool:
        key = os.environ.get(ENV_API_KEY, "").strip()
        return bool(key) and key != "<TO BE FILLED>"

    # ── live ─────────────────────────────────────────────────────────
    def _fetch_live(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        api_key = os.environ.get(ENV_API_KEY, "").strip()
        if not api_key:
            return self._unavailable(
                ticker=ticker, reason="missing_api_key",
                detail=f"{ENV_API_KEY} not set",
            )

        # Window: [cutoff - lookback_days, cutoff]
        window_lo = decision_timestamp - timedelta(days=self.lookback_days)
        params = {
            "ticker": ticker.upper(),
            "published_utc.gte": window_lo.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "published_utc.lte": decision_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": self.limit,
            "order": "descending",
            "sort": "published_utc",
            "apiKey": api_key,
        }
        try:
            resp = requests.get(
                POLYGON_NEWS_URL, params=params, timeout=HTTP_TIMEOUT_S,
                headers={"User-Agent": "altdata-polygon-news/0.1",
                          "Accept": "application/json"},
            )
        except requests.Timeout:
            return self._unavailable(
                ticker=ticker, reason="timeout",
                detail=f"polygon news timeout after {HTTP_TIMEOUT_S}s",
            )
        except requests.RequestException as e:
            return self._unavailable(
                ticker=ticker, reason="request_exception",
                detail=_redact(str(e), api_key)[:200],
            )

        if resp.status_code == 429:
            self._trip_sticky_pause("HTTP 429 from Polygon")
            return self._unavailable(
                ticker=ticker, reason="rate_limited",
                detail="polygon returned 429; sticky pause armed",
            )
        if resp.status_code in (401, 403):
            return self._unavailable(
                ticker=ticker, reason="auth_failed",
                detail=f"polygon HTTP {resp.status_code}",
            )
        if not resp.ok:
            return self._unavailable(
                ticker=ticker, reason="http_error",
                detail=f"polygon HTTP {resp.status_code}",
            )

        try:
            payload = resp.json()
        except ValueError:
            return self._unavailable(
                ticker=ticker, reason="non_json_response",
                detail="polygon returned non-JSON body",
            )

        if not isinstance(payload, dict):
            return self._unavailable(
                ticker=ticker, reason="unexpected_shape",
                detail=f"expected dict, got {type(payload).__name__}",
            )

        results = payload.get("results") or []
        if not isinstance(results, list):
            return self._unavailable(
                ticker=ticker, reason="results_not_list",
                detail=f"results was {type(results).__name__}",
            )

        rows: list[dict] = []
        fetched_at = datetime.now(timezone.utc).isoformat()
        for rec in results:
            if not isinstance(rec, dict):
                continue
            row = self._build_row(
                rec=rec, ticker=ticker, fetched_at=fetched_at,
                decision_timestamp=decision_timestamp,
            )
            if row is not None:
                rows.append(row)

        manifest = {
            "source_id": self.source_id,
            "adapter_version": ADAPTER_VERSION,
            "ticker": ticker,
            "endpoint": "polygon:v2/reference/news",
            "lookback_days": self.lookback_days,
            "limit_requested": self.limit,
            "rows_returned": len(rows),
            "polygon_status": payload.get("status"),
            "polygon_count": payload.get("count"),
            "polygon_request_id": payload.get("request_id"),
        }

        if not rows:
            return AltDataResult(
                source_id=self.source_id,
                block_target=self.block_target,
                rows=[],
                source_flag=self._live_source_flag(),
                data_quality_flags=[{
                    "kind": "no_news_in_window",
                    "severity": "info",
                    "detail": (f"polygon_news: 0 items in "
                               f"[{window_lo.date().isoformat()}, "
                               f"{decision_timestamp.date().isoformat()}] "
                               f"for {ticker}"),
                }],
                manifest=manifest,
                extraction_status="ok",
            )
        return AltDataResult(
            source_id=self.source_id,
            block_target=self.block_target,
            rows=rows,
            source_flag=self._live_source_flag(),
            data_quality_flags=[],
            manifest=manifest,
            extraction_status="ok",
        )

    # ── stub ─────────────────────────────────────────────────────────
    def _fetch_stub(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        """Two canned items per ticker, deterministic dates relative to
        decision_timestamp."""
        fetched_at = datetime.now(timezone.utc).isoformat()
        rows: list[dict] = []
        for offset_hours, publisher in [(2, "Reuters (stub)"),
                                          (8, "Bloomberg (stub)")]:
            pub_dt = decision_timestamp - timedelta(hours=offset_hours)
            rows.append({
                "as_of": pub_dt.isoformat(),
                "published_at_utc": pub_dt.isoformat(),
                "published_at": pub_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "title": f"_STUB_ {ticker} headline at -{offset_hours}h",
                "publisher": publisher,
                "site": "stub.example.com",
                "url": f"https://stub.example.com/{ticker}/{offset_hours}h",
                "symbol": ticker,
                "tickers": [ticker],
                "text_excerpt": f"_STUB_ description for {ticker}",
                "insights": [],
                "source": self.source_id,
                "source_flag": "mock_fallback",
                "fetched_at": fetched_at,
                "adapter_version": ADAPTER_VERSION,
            })
        return AltDataResult(
            source_id=self.source_id,
            block_target=self.block_target,
            rows=rows,
            source_flag="mock_fallback",
            data_quality_flags=[{
                "kind": "stub_data",
                "severity": "info",
                "detail": f"{self.source_id}: stub mode (deterministic, not ground truth)",
            }],
            manifest={
                "source_id": self.source_id,
                "adapter_version": ADAPTER_VERSION,
                "ticker": ticker,
                "rows_returned": len(rows),
                "stub": True,
            },
            extraction_status="stub",
        )

    # ── helpers ──────────────────────────────────────────────────────
    def _build_row(
        self, *, rec: dict, ticker: str, fetched_at: str,
        decision_timestamp: datetime,
    ) -> dict | None:
        pub_raw = rec.get("published_utc")
        pub_dt = _parse_polygon_iso(pub_raw)
        if pub_dt is None:
            return None
        # PIT discipline at adapter level (base class also enforces, this
        # is a defense-in-depth check).
        if pub_dt > decision_timestamp:
            return None

        publisher_obj = rec.get("publisher") or {}
        publisher_name = (
            publisher_obj.get("name") if isinstance(publisher_obj, dict)
            else str(publisher_obj or "")
        )
        # Per-ticker sentiment insight (if Polygon provides one for this row).
        my_insight = None
        for ins in (rec.get("insights") or []):
            if isinstance(ins, dict) and (ins.get("ticker") or "").upper() == ticker.upper():
                my_insight = {
                    "sentiment": ins.get("sentiment"),
                    "sentiment_reasoning": ins.get("sentiment_reasoning"),
                }
                break

        return {
            # Canonical PIT field for the base-class _pit_filter.
            "as_of": pub_dt.isoformat(),
            # Block-aligned fields (matches blocks/news.py kept-row shape).
            "published_at_utc": pub_dt.isoformat(),
            "published_at": pub_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "title": rec.get("title") or "",
            "publisher": publisher_name,
            "site": _site_from_url(rec.get("article_url")),
            "url": rec.get("article_url"),
            "symbol": ticker,
            "tickers": rec.get("tickers") or [],
            "text_excerpt": (rec.get("description") or "")[:600],
            # Polygon-specific extras (not in FMP shape; useful for NDI).
            "id": rec.get("id"),
            "author": rec.get("author"),
            "keywords": rec.get("keywords") or [],
            "insights_for_ticker": my_insight,
            "source": self.source_id,
            "source_flag": self._live_source_flag(),
            "source_endpoint": POLYGON_NEWS_URL,
            "fetched_at": fetched_at,
            "adapter_version": ADAPTER_VERSION,
        }

    def _unavailable(
        self, *, ticker: str, reason: str, detail: str,
    ) -> AltDataResult:
        return AltDataResult(
            source_id=self.source_id,
            block_target=self.block_target,
            rows=[],
            source_flag=self._failed_source_flag(),
            data_quality_flags=[{
                "kind": "data_unavailable",
                "severity": "warning",
                "detail": f"{self.source_id}: {reason} — {detail}",
            }],
            manifest={
                "source_id": self.source_id,
                "adapter_version": ADAPTER_VERSION,
                "ticker": ticker,
                "rows_returned": 0,
                "reason": reason,
            },
            extraction_status="failed",
            error_class=reason,
        )


def _parse_polygon_iso(s: Any) -> datetime | None:
    """Parse Polygon's '2026-05-02T01:32:00Z' ISO format into UTC datetime."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _site_from_url(url: Any) -> str:
    if not isinstance(url, str):
        return ""
    s = url.strip()
    if not s.startswith(("http://", "https://")):
        return ""
    rest = s.split("://", 1)[1]
    return rest.split("/", 1)[0]


def _redact(s: str, api_key: str) -> str:
    if not api_key:
        return s
    return s.replace(api_key, "<REDACTED_POLYGON_KEY>")
