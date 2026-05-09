"""
SEC EDGAR adapter — 8-K filing index (metadata only).

Endpoints:
  https://www.sec.gov/files/company_tickers.json     (ticker → CIK map)
  https://data.sec.gov/submissions/CIK{CIK_PADDED}.json (filings list)

This adapter intentionally does NOT extract filing full text. The 8-K
text retrieval path is owned by the OpenCLI sec_8k_fulltext use case
per RULES.md §17.B.4 (OpenCLI as fallback / corroboration). Splitting
metadata from full text keeps the rate-limit budgets independent and
matches SEC's preferred access pattern (one submissions JSON, then
optional per-filing fetches).

Required header: User-Agent. SEC mandates a User-Agent that identifies
the consumer with contact info; we read it from SEC_EDGAR_USER_AGENT
env var. If unset / empty / "<TO BE FILLED>", the adapter falls
through to stub mode and logs the reason. NEVER prints the User-Agent
contents (in case it contains an email).

Output rows:
  {
    "ticker": "AAPL",
    "cik": "0000320193",
    "accession_number": "0000320193-24-000123",
    "filing_date": "2024-05-15",
    "filing_type": "8-K",
    "primary_document_url": "https://www.sec.gov/Archives/edgar/data/320193/...",
    "items": ["1.01", "8.01"],
    "as_of": "2024-05-15T00:00:00+00:00",
    "source": "sec_edgar",
    "source_flag": "live_sec_edgar",
  }

RULES.md anchors:
  - §11.5 + §6.4 filing usable only after `filing_date`
  - §11.3 source_flag honesty
  - §17.B.4 OpenCLI is fallback / corroboration of trusted sources
  - §19.16 redact credentials in logs (we never log User-Agent contents)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import AltDataAdapter, AltDataResult
from .registry import register_adapter

logger = logging.getLogger(__name__)

CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"
ENV_USER_AGENT = "SEC_EDGAR_USER_AGENT"
PLACEHOLDER_VALUES = {"", "<TO BE FILLED>"}

HTTP_TIMEOUT_S = 15
DEFAULT_LOOKBACK_DAYS = 90
CIK_MAP_TTL_SECONDS = 7 * 24 * 3600


@register_adapter
class SECEdgarAdapter(AltDataAdapter):
    source_id = "sec_edgar"
    block_target = "filing_confirmation"

    rate_limit_per_second = 10.0   # per SEC guidance
    sticky_pause_seconds_on_429 = 60

    def __init__(self, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> None:
        super().__init__()
        self.lookback_days = lookback_days

    def credentials_present(self) -> bool:
        ua = os.environ.get(ENV_USER_AGENT, "").strip()
        if ua in PLACEHOLDER_VALUES:
            return False
        return True

    # ── live ─────────────────────────────────────────────────────
    def _fetch_live(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        import requests

        ua = os.environ.get(ENV_USER_AGENT, "").strip()
        # We've already screened in credentials_present(); double-check.
        if ua in PLACEHOLDER_VALUES:
            logger.info(
                "%s: SEC_EDGAR_USER_AGENT not configured, using stub mode",
                self.source_id,
            )
            return self._fetch_stub(ticker, as_of, decision_timestamp)

        cik = self._resolve_cik(ticker, ua)
        if cik is None:
            return self._unavailable(
                ticker=ticker,
                reason="cik_not_found",
                detail=f"no CIK match for ticker {ticker}",
            )

        cik_padded = cik.zfill(10)
        url = SUBMISSIONS_URL.format(cik=cik_padded)
        resp = requests.get(url, timeout=HTTP_TIMEOUT_S, headers={
            "User-Agent": ua, "Accept": "application/json",
        })
        if resp.status_code == 429:
            self._trip_sticky_pause("HTTP 429 from data.sec.gov")
            return self._unavailable(
                ticker=ticker, reason="rate_limited",
                detail="SEC EDGAR returned 429",
            )
        if not resp.ok:
            return self._unavailable(
                ticker=ticker, reason="http_error",
                detail=f"SEC EDGAR HTTP {resp.status_code}",
            )

        try:
            payload = resp.json()
        except ValueError:
            return self._unavailable(
                ticker=ticker, reason="non_json_response",
                detail="SEC EDGAR returned non-JSON",
            )

        rows = self._extract_8k_rows(
            payload=payload, ticker=ticker, cik=cik_padded,
            as_of=as_of, decision_timestamp=decision_timestamp,
        )
        manifest = {
            "source_id": self.source_id,
            "ticker": ticker,
            "cik": cik_padded,
            "lookback_days": self.lookback_days,
            "rows_returned": len(rows),
            "endpoint": "data.sec.gov/submissions",
        }

        if not rows:
            return AltDataResult(
                source_id=self.source_id,
                block_target=self.block_target,
                rows=[],
                source_flag=self._live_source_flag(),
                data_quality_flags=[{
                    "kind": "no_recent_8k",
                    "severity": "info",
                    "detail": (f"sec_edgar: no 8-K filings in last "
                               f"{self.lookback_days} days for {ticker}"),
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

    def _extract_8k_rows(
        self, *, payload: dict, ticker: str, cik: str,
        as_of: datetime, decision_timestamp: datetime,
    ) -> list[dict]:
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accession = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        items_arr = recent.get("items", [])

        cutoff_lo = (as_of - timedelta(days=self.lookback_days)).date()
        cutoff_hi = decision_timestamp.date()

        rows: list[dict] = []
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            try:
                fd = datetime.strptime(filing_dates[i], "%Y-%m-%d").date()
            except (ValueError, IndexError):
                continue
            if fd < cutoff_lo or fd > cutoff_hi:
                continue
            try:
                acc = accession[i]
                primary = primary_docs[i]
            except IndexError:
                continue
            items_str = items_arr[i] if i < len(items_arr) else ""
            items = [s.strip() for s in (items_str or "").split(",") if s.strip()]
            acc_no_dashes = acc.replace("-", "")
            url = (
                f"{ARCHIVE_BASE}/{int(cik)}/{acc_no_dashes}/{primary}"
            )
            rows.append({
                "ticker": ticker,
                "cik": cik,
                "accession_number": acc,
                "filing_date": fd.isoformat(),
                "filing_type": "8-K",
                "primary_document_url": url,
                "items": items,
                "as_of": datetime(fd.year, fd.month, fd.day,
                                  tzinfo=timezone.utc).isoformat(),
                "source": self.source_id,
                "source_flag": self._live_source_flag(),
            })
        return rows

    # ── stub ─────────────────────────────────────────────────────
    def _fetch_stub(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        """Two canned 8-K filings per ticker, deterministic dates
        relative to as_of."""
        # Stub filings: 14 days ago and 60 days ago.
        rows: list[dict] = []
        for offset_days, items in [(14, ["1.01", "9.01"]),
                                    (60, ["8.01"])]:
            fd = (as_of - timedelta(days=offset_days)).date()
            acc = f"_STUB_{ticker}_{fd.strftime('%Y%m%d')}_8K"
            rows.append({
                "ticker": ticker,
                "cik": "_STUB_CIK_",
                "accession_number": acc,
                "filing_date": fd.isoformat(),
                "filing_type": "8-K",
                "primary_document_url": (
                    f"https://_stub_/edgar/data/_STUB_/{acc}.htm"
                ),
                "items": items,
                "as_of": datetime(fd.year, fd.month, fd.day,
                                  tzinfo=timezone.utc).isoformat(),
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
                "ticker": ticker,
                "lookback_days": self.lookback_days,
                "rows_returned": len(rows),
                "stub": True,
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
                "source_id": self.source_id,
                "ticker": ticker,
                "rows_returned": 0,
                "reason": reason,
            },
            extraction_status="failed",
            error_class=reason,
        )

    def _resolve_cik(self, ticker: str, user_agent: str) -> str | None:
        """Cached map at data/cache/altdata/sec_edgar/_cik_map.json
        (7-day TTL). Returns the CIK as string (no leading zeros)."""
        cache_path = self._cache_root() / "_cik_map.json"
        cik_map = _load_cik_map(cache_path)
        if cik_map is not None and ticker in cik_map:
            return str(cik_map[ticker])

        # Need to fetch the map.
        m = _fetch_cik_map(user_agent)
        if m is not None:
            _save_cik_map(cache_path, m)
            cik_map = m

        if cik_map and ticker in cik_map:
            return str(cik_map[ticker])
        return None


# ── CIK-map persistence helpers ───────────────────────────────────
def _load_cik_map(path: Path) -> dict | None:
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > CIK_MAP_TTL_SECONDS:
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_cik_map(path: Path, m: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _fetch_cik_map(user_agent: str) -> dict | None:
    """Fetch SEC's company_tickers.json and reduce to {ticker: cik}."""
    try:
        import requests
        r = requests.get(CIK_MAP_URL, timeout=HTTP_TIMEOUT_S, headers={
            "User-Agent": user_agent, "Accept": "application/json",
        })
        if not r.ok:
            return None
        raw = r.json()
        # SEC format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": ...}, ...}
        out: dict[str, str] = {}
        for v in raw.values():
            t = v.get("ticker")
            cik = v.get("cik_str")
            if isinstance(t, str) and cik is not None:
                out[t.upper()] = str(cik)
        return out
    except Exception:
        return None
