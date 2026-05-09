"""
GitHub public-repo activity adapter.

Endpoint base: https://api.github.com

Auth:
  - If GITHUB_TOKEN env var is set and not <TO BE FILLED>, send
    "Authorization: Bearer <token>" (5000 req/hour rate limit).
  - Else anonymous (60 req/hour) — log a warning at first use.
  - If token is invalid, GitHub returns 401 → fall through to stub
    (NEVER silently retry per RULES.md §5.12).

Ticker → org/user resolution:
  data/altdata/seeds/ticker_to_github.csv (curated, ~30 obvious cases).
  Unknown tickers return data_unavailable / not_evaluated with
  source_flag=live_github_public_failed (per §11.2 / §11.8).

Output rows (one aggregate per (ticker, week-ending-date)):
  {
    "ticker": "MSFT",
    "github_owner": "microsoft",
    "week_ending": "2024-05-19",
    "as_of": "2024-05-19T23:59:59+00:00",
    "commits": 423,
    "new_repos": 2,
    "repos_updated": 17,
    "top_languages": ["TypeScript", "C#", "Python"],
    "stars_total_observed": 854321,
    "source": "github_public",
    "source_flag": "live_github_public",
  }

block_target: alternative_data_features (tech_activity subsection).

RULES.md anchors:
  - §11.6 descriptors-not-rules — these are activity proxies
  - §10.4 T1/T2 hierarchy — GitHub = T2 (industry primary, public)
  - §19.16 redact tokens; we never log Authorization header contents
"""
from __future__ import annotations

import csv
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import AltDataAdapter, AltDataResult
from .registry import register_adapter

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
ENV_TOKEN = "GITHUB_TOKEN"
PLACEHOLDER_VALUES = {"", "<TO BE FILLED>"}

DEFAULT_LOOKBACK_DAYS = 90
HTTP_TIMEOUT_S = 15

# Project-root relative seed CSV
def _seed_csv_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "altdata" / "seeds" / "ticker_to_github.csv"


@register_adapter
class GitHubPublicAdapter(AltDataAdapter):
    source_id = "github_public"
    block_target = "alternative_data_features"

    rate_limit_per_second = 1.25   # 4500/hour leaves headroom under 5000/hr
    sticky_pause_seconds_on_429 = 60

    def __init__(self, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> None:
        super().__init__()
        self.lookback_days = lookback_days
        self._ticker_map: dict[str, str] | None = None

    def credentials_present(self) -> bool:
        # Anonymous works; we don't gate on the token. The token only
        # determines the rate-limit class.
        return True

    # ── live ─────────────────────────────────────────────────────
    def _fetch_live(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        import requests

        owner = self._resolve_owner(ticker)
        if owner is None:
            return self._unavailable(
                ticker=ticker, reason="unmapped_ticker",
                detail=(f"github_public: ticker {ticker} not in "
                        f"data/altdata/seeds/ticker_to_github.csv"),
            )

        token = os.environ.get(ENV_TOKEN, "").strip()
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token and token not in PLACEHOLDER_VALUES:
            headers["Authorization"] = f"Bearer {token}"
        else:
            logger.warning(
                "%s: GITHUB_TOKEN not set; using anonymous 60 req/hour",
                self.source_id,
            )

        # 1. List public repos for the owner.
        repos_url = f"{GITHUB_API}/users/{owner}/repos?per_page=100&type=public&sort=updated"
        resp = requests.get(repos_url, timeout=HTTP_TIMEOUT_S, headers=headers)
        if resp.status_code == 401:
            return self._unavailable(
                ticker=ticker, reason="auth_failed",
                detail="GITHUB_TOKEN rejected by api.github.com",
            )
        if resp.status_code == 429 or (
            resp.status_code == 403 and "rate limit" in (resp.text or "").lower()
        ):
            self._trip_sticky_pause("HTTP 429/403 rate limited from api.github.com")
            return self._unavailable(
                ticker=ticker, reason="rate_limited",
                detail="GitHub returned rate-limit response",
            )
        if not resp.ok:
            return self._unavailable(
                ticker=ticker, reason="http_error",
                detail=f"GitHub HTTP {resp.status_code}",
            )

        try:
            repos = resp.json()
        except ValueError:
            return self._unavailable(
                ticker=ticker, reason="non_json_response",
                detail="GitHub returned non-JSON",
            )

        if not isinstance(repos, list):
            return self._unavailable(
                ticker=ticker, reason="unexpected_payload",
                detail=f"GitHub /users/{owner}/repos returned non-list",
            )

        # 2. Aggregate by week within window.
        cutoff_lo = as_of - timedelta(days=self.lookback_days)
        languages: dict[str, int] = {}
        new_repos_by_week: dict[str, int] = {}
        updated_repos_by_week: dict[str, int] = {}
        stars_total = 0

        for r in repos:
            stars_total += int(r.get("stargazers_count") or 0)
            lang = r.get("language")
            if isinstance(lang, str) and lang:
                languages[lang] = languages.get(lang, 0) + 1
            for k, bucket in (
                ("created_at", new_repos_by_week),
                ("updated_at", updated_repos_by_week),
            ):
                ts = r.get(k)
                if not isinstance(ts, str):
                    continue
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if dt < cutoff_lo or dt > decision_timestamp:
                    continue
                week_end = _week_ending(dt)
                bucket[week_end] = bucket.get(week_end, 0) + 1

        # Per-week aggregate rows. Commits proxy: count repos updated
        # that week (real per-week commit counts would require pulling
        # /commits per repo per page, which blows the rate budget; this
        # is a defensible aggregate for the descriptor purpose).
        weeks: list[str] = sorted(set(new_repos_by_week) | set(updated_repos_by_week))
        if not weeks:
            return AltDataResult(
                source_id=self.source_id,
                block_target=self.block_target,
                rows=[],
                source_flag=self._live_source_flag(),
                data_quality_flags=[{
                    "kind": "no_recent_activity",
                    "severity": "info",
                    "detail": (f"github_public: no public-repo activity "
                               f"for {owner} in last {self.lookback_days} days"),
                }],
                manifest={
                    "source_id": self.source_id, "ticker": ticker,
                    "github_owner": owner, "rows_returned": 0,
                    "stars_total_observed": stars_total,
                },
                extraction_status="ok",
            )

        top_languages = sorted(languages, key=lambda k: -languages[k])[:5]

        rows: list[dict] = []
        for w in weeks:
            wd = datetime.fromisoformat(w + "T23:59:59+00:00")
            rows.append({
                "ticker": ticker,
                "github_owner": owner,
                "week_ending": w,
                "as_of": wd.isoformat(),
                "commits": updated_repos_by_week.get(w, 0)
                            + new_repos_by_week.get(w, 0),
                "new_repos": new_repos_by_week.get(w, 0),
                "repos_updated": updated_repos_by_week.get(w, 0),
                "top_languages": top_languages,
                "stars_total_observed": stars_total,
                "source": self.source_id,
                "source_flag": self._live_source_flag(),
            })

        return AltDataResult(
            source_id=self.source_id,
            block_target=self.block_target,
            rows=rows,
            source_flag=self._live_source_flag(),
            data_quality_flags=[],
            manifest={
                "source_id": self.source_id, "ticker": ticker,
                "github_owner": owner, "lookback_days": self.lookback_days,
                "rows_returned": len(rows),
                "stars_total_observed": stars_total,
                "endpoint": f"api.github.com/users/{owner}/repos",
            },
            extraction_status="ok",
        )

    # ── stub ─────────────────────────────────────────────────────
    def _fetch_stub(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        owner = self._resolve_owner(ticker) or f"_STUB_{ticker.lower()}"
        seed = int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8], 16)
        n_weeks = max(1, self.lookback_days // 7)
        rows: list[dict] = []
        for i in range(n_weeks):
            week_end_dt = (as_of - timedelta(days=7 * (n_weeks - 1 - i))).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
            commits = (seed + i * 17) % 200
            rows.append({
                "ticker": ticker,
                "github_owner": owner,
                "week_ending": week_end_dt.date().isoformat(),
                "as_of": week_end_dt.isoformat(),
                "commits": commits,
                "new_repos": (seed + i) % 3,
                "repos_updated": commits // 3,
                "top_languages": ["Python", "TypeScript", "Go"],
                "stars_total_observed": 1000 + (seed % 50000),
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
                "source_id": self.source_id, "ticker": ticker,
                "github_owner": owner, "lookback_days": self.lookback_days,
                "rows_returned": len(rows), "stub": True,
            },
            extraction_status="stub",
        )

    # ── helpers ──────────────────────────────────────────────────
    def _unavailable(self, *, ticker: str, reason: str, detail: str,
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
                "source_id": self.source_id, "ticker": ticker,
                "rows_returned": 0, "reason": reason,
            },
            extraction_status="failed",
            error_class=reason,
        )

    def _resolve_owner(self, ticker: str) -> str | None:
        if self._ticker_map is None:
            self._ticker_map = _load_ticker_map(_seed_csv_path())
        return self._ticker_map.get(ticker.upper())


def _load_ticker_map(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        logger.warning("github_public: seed map missing at %s", path)
        return out
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t = (row.get("ticker") or "").strip().upper()
                owner = (row.get("github_owner") or "").strip()
                if t and owner:
                    out[t] = owner
    except OSError as e:
        logger.warning("github_public: failed to read seed map %s (%s)", path, e)
    return out


def _week_ending(dt: datetime) -> str:
    """Saturday-ending week, ISO date string. Monotonic with calendar."""
    # weekday(): Monday=0 .. Sunday=6. Saturday=5. Days until Saturday:
    days_to_sat = (5 - dt.weekday()) % 7
    sat = (dt + timedelta(days=days_to_sat)).date()
    return sat.isoformat()
