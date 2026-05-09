"""
Wikipedia pageviews adapter.

Endpoint:
  https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/
  en.wikipedia.org/all-access/user/{ARTICLE_TITLE}/daily/{YYYYMMDD}/{YYYYMMDD}

No credentials required. Wikimedia is permissive (no published hard
limit); we self-throttle defensively at 50 req/sec.

Output shape (one row per day in window):
  {
    "ticker": "AAPL",
    "article_title": "Apple_Inc.",
    "date": "2024-05-15",
    "views": 12345,
    "as_of": "2024-05-15T00:00:00+00:00",
    "source": "wikipedia_pageviews",
    "source_flag": "live_wikipedia_pageviews",
  }

RULES.md anchors:
  - §11.1 every row carries `as_of`
  - §11.2 missing pageviews = `Data unavailable`, never zero
  - §11.5 PIT cutoff enforced by base class
  - §11.6 descriptors-not-rules: this is an attention proxy, not a buy/sell signal
  - block_target = `alternative_data_features` (attention subsection)
"""
from __future__ import annotations

import hashlib
import json
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .base import AltDataAdapter, AltDataResult
from .registry import register_adapter

logger = logging.getLogger(__name__)

WIKIMEDIA_BASE = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia.org/all-access/user/{title}/daily/{start}/{end}"
)
MEDIAWIKI_SEARCH_BASE = (
    "https://en.wikipedia.org/w/api.php"
    "?action=query&list=search&srlimit=1&format=json&srsearch={q}"
)

DEFAULT_WINDOW_DAYS = 30
HTTP_TIMEOUT_S = 10


@register_adapter
class WikipediaPageviewsAdapter(AltDataAdapter):
    source_id = "wikipedia_pageviews"
    block_target = "alternative_data_features"

    rate_limit_per_second = 50.0
    sticky_pause_seconds_on_429 = 60

    def __init__(self, window_days: int = DEFAULT_WINDOW_DAYS) -> None:
        super().__init__()
        self.window_days = window_days

    def credentials_present(self) -> bool:
        # No credential needed.
        return True

    # ── live ─────────────────────────────────────────────────────
    def _fetch_live(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        # Lazy import so the module loads even if requests is missing
        # in some odd minimal environment (the package requires it).
        import requests

        article_title = self._resolve_article_title(ticker)
        if article_title is None:
            return self._unavailable(
                ticker=ticker,
                reason="article_title_unresolved",
                detail=f"could not resolve Wikipedia article for {ticker}",
            )

        end = as_of.date()
        start = end - timedelta(days=self.window_days - 1)
        url = WIKIMEDIA_BASE.format(
            title=urllib.parse.quote(article_title, safe=""),
            start=start.strftime("%Y%m%d"),
            end=end.strftime("%Y%m%d"),
        )

        resp = requests.get(url, timeout=HTTP_TIMEOUT_S, headers={
            "User-Agent": "alt_data_agentic_long_short/0.1 (educational research)",
            "Accept": "application/json",
        })
        if resp.status_code == 429:
            self._trip_sticky_pause("HTTP 429 from wikimedia")
            return self._unavailable(
                ticker=ticker,
                reason="rate_limited",
                detail="Wikimedia returned 429",
            )
        if resp.status_code == 404:
            return self._unavailable(
                ticker=ticker,
                reason="article_not_found",
                detail=f"article '{article_title}' not on Wikipedia",
            )
        if not resp.ok:
            return self._unavailable(
                ticker=ticker,
                reason="http_error",
                detail=f"Wikimedia HTTP {resp.status_code}",
            )

        try:
            payload = resp.json()
        except ValueError:
            return self._unavailable(
                ticker=ticker,
                reason="non_json_response",
                detail="Wikimedia returned non-JSON",
            )

        items = payload.get("items", [])
        rows: list[dict] = []
        for it in items:
            ts = it.get("timestamp", "")     # "YYYYMMDD00"
            views = it.get("views")
            if not ts or len(ts) < 8 or views is None:
                continue
            day = datetime.strptime(ts[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            rows.append({
                "ticker": ticker,
                "article_title": article_title,
                "date": day.date().isoformat(),
                "views": int(views),
                "as_of": day.isoformat(),
                "source": self.source_id,
                "source_flag": self._live_source_flag(),
            })

        manifest = {
            "source_id": self.source_id,
            "article_title": article_title,
            "window_days": self.window_days,
            "rows_returned": len(rows),
            "endpoint": "wikimedia.org/api/rest_v1",
        }
        if not rows:
            return self._unavailable(
                ticker=ticker,
                reason="empty_window",
                detail=(f"Wikimedia returned 0 days for "
                        f"{article_title} window {start}..{end}"),
                article_title=article_title,
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

    # ── stub ─────────────────────────────────────────────────────
    def _fetch_stub(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        """Deterministic stub seeded by ticker hash. Returns 30 days
        of plausible-looking pageview counts ending at as_of."""
        seed = int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8], 16)
        base_views = 500 + (seed % 9500)   # 500..10000
        rows: list[dict] = []
        for i in range(self.window_days):
            day = (as_of - timedelta(days=self.window_days - 1 - i)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            # Per-day deterministic perturbation
            perturb = ((seed + i * 31) % 200) - 100   # -100..+99
            views = max(0, base_views + perturb)
            rows.append({
                "ticker": ticker,
                "article_title": f"_STUB_{ticker}_Inc",
                "date": day.date().isoformat(),
                "views": views,
                "as_of": day.isoformat(),
                "source": self.source_id,
                "source_flag": "mock_fallback",
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
                "article_title": f"_STUB_{ticker}_Inc",
                "window_days": self.window_days,
                "rows_returned": len(rows),
                "stub": True,
            },
            extraction_status="stub",
        )

    # ── helpers ──────────────────────────────────────────────────
    def _unavailable(
        self, *, ticker: str, reason: str, detail: str,
        article_title: str | None = None,
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
                "ticker": ticker,
                "article_title": article_title,
                "rows_returned": 0,
                "reason": reason,
            },
            extraction_status="failed",
            error_class=reason,
        )

    def _resolve_article_title(self, ticker: str) -> str | None:
        """Try cached map first; then FMP /profile companyName; then
        MediaWiki search. Conservative — returns None on failure."""
        title_map_path = self._cache_root() / "_title_map.json"
        title_map = _load_title_map(title_map_path)

        if ticker in title_map:
            return title_map[ticker]

        # Try FMP companyName via the existing fmp_adapter (already
        # PIT-safe; uses on-disk cache; tolerates missing API key).
        company_name = _fmp_company_name(ticker)

        # Direct match attempt — common pattern: "Apple Inc." → "Apple_Inc."
        candidate = (
            company_name.replace(" ", "_") if company_name else None
        )
        resolved: str | None = None
        if candidate and _wikipedia_article_exists(candidate):
            resolved = candidate
        else:
            # MediaWiki search fallback
            resolved = _mediawiki_search(company_name or ticker)

        if resolved:
            title_map[ticker] = resolved
            _save_title_map(title_map_path, title_map)
        return resolved


# ── module-level helpers ──────────────────────────────────────────
def _load_title_map(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_title_map(path: Path, m: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _fmp_company_name(ticker: str) -> str | None:
    """Best-effort lookup via existing fmp_adapter; never raises."""
    try:
        from src.data_adapters.fmp_adapter import get_profile  # type: ignore
    except Exception:  # pragma: no cover
        return None
    try:
        prof = get_profile(ticker)
    except Exception:  # pragma: no cover
        return None
    if not isinstance(prof, dict):
        return None
    name = prof.get("companyName") or prof.get("data", {}).get("companyName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _wikipedia_article_exists(title: str) -> bool:
    try:
        import requests
        r = requests.head(
            f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title, safe='')}",
            timeout=HTTP_TIMEOUT_S, allow_redirects=True,
        )
        return r.status_code == 200
    except Exception:
        return False


def _mediawiki_search(q: str) -> str | None:
    try:
        import requests
        url = MEDIAWIKI_SEARCH_BASE.format(q=urllib.parse.quote(q, safe=""))
        r = requests.get(url, timeout=HTTP_TIMEOUT_S, headers={
            "User-Agent": "alt_data_agentic_long_short/0.1 (educational research)",
        })
        if not r.ok:
            return None
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return None
        # Wikipedia returns "Apple Inc." — convert to "Apple_Inc."
        return results[0]["title"].replace(" ", "_")
    except Exception:
        return None
