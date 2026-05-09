"""
FMP Sentiment adapter — wraps 4 FMP Premium /stable endpoints with
tier-tagged, descriptor-only output.

Endpoints wrapped (tier classification per RULES.md §10.13):
  - press-releases                       (T1 — issuer-side legally-binding)
  - grades-historical                    (T2 — analyst expert opinion)
  - grades-news                          (T2 — analyst grade actions w/ news context)
  - news/stock-sentiments-rss            (T4 — aggregated institutional news sentiment)

All rows carry:
  - tier ∈ {T1, T2, T4}
  - descriptor_only=True, is_gate=False  (RULES.md §11.6)
  - source_endpoint  (which FMP path produced the row)
  - payload          (the raw FMP record, post-redaction)
  - as_of            (publishedDate parsed to UTC; PIT base class enforces ≤ cutoff)
  - fetched_at       (wall clock at fetch time)
  - adapter_version  ("v0.1")

Rate limiting / sticky pause / API key handling all share the existing
FMP infrastructure in src/data_adapters/fmp_adapter.py — there is no
parallel quota counter and no key duplication. We use a 10-second
HTTP timeout (tighter than fmp_adapter._api_call's 15s) per Step D8 spec.

Failure modes:
  - missing API key       -> _unavailable, error_class="missing_api_key"
  - sticky pause active   -> handled by base class (falls through to stub)
  - HTTP 429 / 402        -> trips shared FMP sticky pause via _set_sticky_pause
  - HTTP 404 / 403        -> error_class="not_available_on_current_plan"
                              (per spec: log clearly, do not crash adapter)
  - HTTP timeout          -> error_class="timeout", no silent retry
  - any other HTTP error  -> error_class="http_error"

block_target = "sentiment_community_ownership_evidence" — the block left
empty after Reddit was descoped 2026-04-28. FMP sentiment fills it.

RULES.md anchors:
  - §10.13 anti-pollution tier system (T1/T2/T4 enforced here)
  - §11.1 every row carries `as_of`
  - §11.2 missing data = `Data unavailable`, never zero
  - §11.5 PIT cutoff enforced by base class
  - §11.6 descriptors-not-rules — explicit `descriptor_only`/`is_gate` flags
  - §19.15 sticky pause on 429/402 — reused via _set_sticky_pause
  - §19.16 redact credentials in logs — reused via _redact
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from .base import AltDataAdapter, AltDataResult
from .registry import register_adapter

logger = logging.getLogger(__name__)

ADAPTER_VERSION = "v0.1"
HTTP_TIMEOUT_S = 10

# Endpoint catalogue. Order is the canonical order rows appear in.
# `tier` per RULES.md §10.13.  `path` is the /stable suffix.
ENDPOINTS = (
    {
        "key":  "press_releases",
        "path": "press-releases",
        "tier": "T1",
    },
    {
        "key":  "grades_summary",
        "path": "grades-historical",
        "tier": "T2",
    },
    {
        "key":  "stock_grade_latest_news",
        "path": "grades-news",
        "tier": "T2",
    },
    {
        "key":  "stock_news_sentiments_rss",
        "path": "news/stock-sentiments-rss",
        "tier": "T4",
    },
)


@register_adapter
class FMPSentimentAdapter(AltDataAdapter):
    source_id = "fmp_sentiment"
    block_target = "sentiment_community_ownership_evidence"

    # Set very high — the shared FMP _MinuteRateLimiter is the real
    # gate. The base-class self-throttle is a defensive backup only.
    rate_limit_per_second = 50.0
    sticky_pause_seconds_on_429 = 60

    def credentials_present(self) -> bool:
        from src.data_adapters.fmp_adapter import _get_api_key
        return _get_api_key() is not None

    # ── live ─────────────────────────────────────────────────────
    def _fetch_live(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        from src.data_adapters.fmp_adapter import _get_api_key

        if _get_api_key() is None:
            return self._unavailable(
                ticker=ticker,
                reason="missing_api_key",
                detail="FMP_API_KEY not set in environment",
            )

        rows: list[dict] = []
        per_endpoint_meta: list[dict] = []
        data_quality_flags: list[dict] = []

        fetched_at = datetime.now(timezone.utc).isoformat()

        for ep in ENDPOINTS:
            ep_rows, ep_meta = self._fetch_one_endpoint(
                ticker=ticker,
                endpoint=ep,
                fetched_at=fetched_at,
            )
            per_endpoint_meta.append(ep_meta)
            rows.extend(ep_rows)
            if ep_meta.get("error_class"):
                data_quality_flags.append({
                    "kind": "endpoint_unavailable",
                    "severity": "warning",
                    "detail": (
                        f"fmp_sentiment[{ep['key']}]: {ep_meta['error_class']}"
                        f" — {ep_meta.get('error_short', '')}"
                    ),
                })

        manifest = {
            "source_id": self.source_id,
            "adapter_version": ADAPTER_VERSION,
            "endpoints": [e["key"] for e in ENDPOINTS],
            "endpoint_results": per_endpoint_meta,
            "rows_returned": len(rows),
            "fetched_at": fetched_at,
        }

        if not rows:
            # Every endpoint failed or returned empty — treat as
            # unavailable so the caller doesn't see an empty success.
            return AltDataResult(
                source_id=self.source_id,
                block_target=self.block_target,
                rows=[],
                source_flag=self._failed_source_flag(),
                data_quality_flags=data_quality_flags or [{
                    "kind": "data_unavailable",
                    "severity": "warning",
                    "detail": f"{self.source_id}: all endpoints returned empty",
                }],
                manifest=manifest,
                extraction_status="failed",
                error_class="all_endpoints_empty",
            )

        return AltDataResult(
            source_id=self.source_id,
            block_target=self.block_target,
            rows=rows,
            source_flag=self._live_source_flag(),
            data_quality_flags=data_quality_flags,
            manifest=manifest,
            extraction_status="ok",
        )

    # ── stub ─────────────────────────────────────────────────────
    def _fetch_stub(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        """Deterministic stub: one synthetic row per endpoint, all
        flagged as mock so consumers know it's not ground truth."""
        fetched_at = datetime.now(timezone.utc).isoformat()
        as_of_iso = as_of.astimezone(timezone.utc).isoformat()
        rows: list[dict] = []
        for ep in ENDPOINTS:
            rows.append({
                "ticker": ticker,
                "as_of": as_of_iso,
                "source": self.source_id,
                "source_flag": "mock_fallback",
                "source_endpoint": ep["path"],
                "tier": ep["tier"],
                "payload": {
                    "_stub": True,
                    "endpoint_key": ep["key"],
                    "ticker": ticker,
                    "title": f"_STUB_ {ep['key']} for {ticker}",
                },
                "fetched_at": fetched_at,
                "adapter_version": ADAPTER_VERSION,
                "descriptor_only": True,
                "is_gate": False,
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
                "endpoints": [e["key"] for e in ENDPOINTS],
                "rows_returned": len(rows),
                "stub": True,
                "fetched_at": fetched_at,
            },
            extraction_status="stub",
        )

    # ── per-endpoint live fetch ──────────────────────────────────
    def _fetch_one_endpoint(
        self, *, ticker: str, endpoint: dict, fetched_at: str,
    ) -> tuple[list[dict], dict]:
        """Fetch one FMP endpoint. Returns (rows, meta).

        On any failure: returns ([], meta_with_error_class). NEVER raises
        — a single bad endpoint must not poison the others.
        """
        from src.data_adapters.fmp_adapter import (
            _rate_limiter,
            _is_sticky_paused,
            _set_sticky_pause,
            _get_api_key,
            _redact,
            FMP_BASE_URL,
            RATE_LIMIT_HARD_PAUSE_SECONDS,
        )

        path = endpoint["path"]
        ep_key = endpoint["key"]
        tier = endpoint["tier"]

        meta: dict[str, Any] = {
            "endpoint_key": ep_key,
            "path": path,
            "tier": tier,
            "http_status": None,
            "error_class": None,
            "error_short": None,
            "rows_returned": 0,
        }

        # Honour the FMP-wide sticky pause first.
        paused, reason, remaining = _is_sticky_paused()
        if paused:
            meta["error_class"] = "RateLimitPaused"
            meta["error_short"] = f"{reason} (resumes in {remaining}s)"
            meta["http_status"] = 429
            return [], meta

        api_key = _get_api_key()
        if api_key is None:
            meta["error_class"] = "missing_api_key"
            return [], meta

        # Reserve a slot in the shared rolling-minute window.
        wait_s = _rate_limiter.acquire()
        meta["wait_s"] = round(wait_s, 3)

        url = f"{FMP_BASE_URL}/{path}"
        params: dict[str, Any] = {"symbol": ticker, "apikey": api_key}
        safe_param_keys = sorted(k for k in params if k != "apikey")

        t0 = datetime.now(timezone.utc)
        try:
            resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT_S)
        except requests.Timeout as e:
            meta["error_class"] = "timeout"
            meta["error_short"] = _redact(str(e))[:200]
            logger.warning("fmp_sentiment %s timeout after %ss",
                           path, HTTP_TIMEOUT_S)
            return [], meta
        except requests.RequestException as e:
            meta["error_class"] = type(e).__name__
            meta["error_short"] = _redact(str(e))[:200]
            logger.warning("fmp_sentiment %s [%s] failed: %s",
                           path, safe_param_keys, meta["error_short"])
            return [], meta

        meta["http_status"] = resp.status_code
        meta["elapsed_ms"] = int(
            (datetime.now(timezone.utc) - t0).total_seconds() * 1000
        )

        # Manual handling — never call resp.raise_for_status() (leaks key).
        if resp.status_code != 200:
            body_short = ""
            try:
                j = resp.json()
                if isinstance(j, dict):
                    body_short = str(
                        j.get("Error Message") or j.get("error") or ""
                    )[:200]
            except ValueError:
                body_short = (resp.text or "")[:200]

            if resp.status_code in (404, 403):
                meta["error_class"] = "not_available_on_current_plan"
                meta["error_short"] = _redact(body_short) or f"HTTP {resp.status_code}"
                logger.warning(
                    "fmp_sentiment %s: not available on current FMP plan "
                    "(HTTP %s); continuing without this endpoint",
                    path, resp.status_code,
                )
                return [], meta

            if resp.status_code in (429, 402):
                _set_sticky_pause(
                    f"HTTP{resp.status_code}",
                    RATE_LIMIT_HARD_PAUSE_SECONDS,
                )
                meta["error_class"] = f"HTTP{resp.status_code}"
                meta["error_short"] = _redact(body_short)
                logger.warning(
                    "fmp_sentiment %s HTTP %s — tripped shared sticky pause",
                    path, resp.status_code,
                )
                return [], meta

            meta["error_class"] = "http_error"
            meta["error_short"] = f"HTTP{resp.status_code}: {_redact(body_short)}"
            logger.warning("fmp_sentiment %s HTTP %s: %s",
                           path, resp.status_code, meta["error_short"])
            return [], meta

        try:
            data = resp.json()
        except ValueError:
            meta["error_class"] = "invalid_json"
            meta["error_short"] = "non-JSON response"
            logger.warning("fmp_sentiment %s returned non-JSON body", path)
            return [], meta

        # FMP error-in-200 case
        if isinstance(data, dict) and ("Error Message" in data or "error" in data):
            msg = str(data.get("Error Message") or data.get("error") or "")[:200]
            meta["error_class"] = "fmp_error_in_body"
            meta["error_short"] = _redact(msg)
            logger.warning("fmp_sentiment %s error in body: %s",
                           path, meta["error_short"])
            return [], meta

        records = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        rows: list[dict] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            row_as_of = self._extract_record_as_of(rec)
            payload = self._sanitize_payload(rec)
            rows.append({
                "ticker": ticker,
                "as_of": row_as_of,
                "source": self.source_id,
                "source_flag": self._live_source_flag(),
                "source_endpoint": path,
                "tier": tier,
                "payload": payload,
                "fetched_at": fetched_at,
                "adapter_version": ADAPTER_VERSION,
                "descriptor_only": True,
                "is_gate": False,
            })

        meta["rows_returned"] = len(rows)
        return rows, meta

    # ── helpers ──────────────────────────────────────────────────
    def _extract_record_as_of(self, rec: dict) -> str:
        """FMP records use a few different timestamp keys depending on
        the endpoint; try them in priority order. Returns ISO UTC.
        Falls back to "now" if no timestamp present (caller will see
        the row pass PIT only if decision_timestamp >= now)."""
        for key in ("publishedDate", "date", "time", "filingDate", "acceptedDate"):
            v = rec.get(key)
            if isinstance(v, str) and v.strip():
                parsed = _parse_fmp_timestamp(v)
                if parsed is not None:
                    return parsed.isoformat()
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _sanitize_payload(rec: dict) -> dict:
        """Return a shallow copy of the record with any apikey-bearing
        strings redacted. Defensive — FMP responses don't normally
        echo the key, but never trust an upstream not to."""
        from src.data_adapters.fmp_adapter import _redact
        out: dict[str, Any] = {}
        for k, v in rec.items():
            if isinstance(v, str):
                out[k] = _redact(v)
            else:
                out[k] = v
        return out

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


# ── module-level helpers ──────────────────────────────────────────
def _parse_fmp_timestamp(s: str) -> datetime | None:
    """FMP timestamps come in several flavours:
        '2024-05-15 10:30:00'
        '2024-05-15T10:30:00.000Z'
        '2024-05-15'
    Return UTC datetime or None on parse failure."""
    s = s.strip()
    if not s:
        return None
    # Try ISO first (with Z)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    # Try 'YYYY-MM-DD HH:MM:SS'
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
