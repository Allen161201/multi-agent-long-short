"""
SEC EDGAR institutional ownership / insider / proxy adapters.

Three sibling adapters in this module — kept together because they
share the SEC EDGAR User-Agent, rate limiter (10 req/s ceiling
inherited from the base class), CIK resolution helper (reused from
`sec_edgar.py`), and the same `block_target =
sentiment_community_ownership_evidence`.

Adapters:
  - SEC13FAdapter   (source_id="sec_13f")    — institutional ownership (quarterly)
  - SECForm4Adapter (source_id="sec_form4")  — insider transactions (semi-real-time)
  - SECDef14AAdapter(source_id="sec_def14a") — proxy / governance (annual)

Tier-1 designation per RULES.md §10.13: all three carry
`tier=1` because the underlying data is legally-binding regulatory
filings (13F = §13(f) of the Exchange Act, Form 4 = §16(a), DEF 14A
= proxy under §14(a)). Faking these is securities fraud.

PIT discipline (§11.5 / §11.14):
  - Records carry `accepted_datetime` (the SEC acceptance timestamp
    on the filing). The base-class PIT filter rejects rows with
    `as_of > decision_timestamp`, where `as_of := accepted_datetime`.
  - 13F: NEVER use `report_date` (the quarter-end the holdings reflect)
    for the cutoff comparison. `report_date` is in the FUTURE relative
    to the accepted_datetime by 30-45 days; using it as the cutoff
    ground truth would be a lookahead bug.
  - Form 4: cutoff is `accepted_datetime`, not `transaction_date`
    (Form 4 must file within 2 business days but the legal exposure
    starts at acceptance, not transaction).
  - DEF 14A: cutoff is `accepted_datetime`.

Descriptors-only contract (§11.6 / §11.13):
  - Output rows carry `descriptor_only=True`, `is_gate=False`.
  - Derived descriptors (concentration ratios, insider 30-day counts)
    are NUMERIC not categorical. We emit `top_10_holder_concentration`
    as a float, NEVER as boolean `concentrated_safe` / `concentrated_risky`.
  - There is no "high institutional ownership = safe", no
    "insider buying = buy", no "insider selling = short" logic.
    Trading interpretation is the agent's job.

Equity-safe terminology (§10.12, CRITICAL):
  - The crypto-derived shorthand banned by §10.12 does NOT appear in
    any field name, log message, comment, or test assertion in this
    module. Translations used: "large institutional holders" /
    "concentrated 13F holders" / "ownership concentration" /
    "smart-money positioning context".

13F source pragmatism: SEC's 13F-HR XML is filed by each institutional
HOLDER (different CIK from the issuer); reverse-indexing thousands
of filers to recover the holders OF a given issuer is genuinely
brittle for a student project. The task spec explicitly permits FMP
`/stable/institutional-ownership/symbol-ownership` as the primary
source for 13F when SEC parsing is too brittle. We do that here, and
still tag rows `tier=1` because the upstream data is 13F filings.
Form 4 and DEF 14A are filed by the issuer's own CIK and are pulled
directly from SEC EDGAR submissions JSON (same pattern as 8-K).
"""
from __future__ import annotations

import logging
import os
import re as _re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .base import AltDataAdapter, AltDataResult
from .registry import register_adapter
from .sec_edgar import (
    ENV_USER_AGENT,
    PLACEHOLDER_VALUES,
    SUBMISSIONS_URL,
    ARCHIVE_BASE,
    SECEdgarAdapter,
)

logger = logging.getLogger(__name__)

ADAPTER_VERSION = "v0.1"
HTTP_TIMEOUT_S = 15  # SEC EDGAR is slower than FMP; spec requirement

# Form 4 lookback default
FORM4_LOOKBACK_DAYS = 90
DEF14A_LOOKBACK_DAYS = 400
INSIDER_30D_WINDOW_DAYS = 30


# ── shared helpers ────────────────────────────────────────────────
def _user_agent() -> str | None:
    ua = os.environ.get(ENV_USER_AGENT, "").strip()
    if ua in PLACEHOLDER_VALUES:
        return None
    return ua


def _parse_iso_or_date(s: str | None) -> datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _failed_result(
    *, source_id: str, block_target: str, ticker: str,
    reason: str, detail: str,
) -> AltDataResult:
    return AltDataResult(
        source_id=source_id,
        block_target=block_target,
        rows=[],
        source_flag=f"live_{source_id}_failed",
        data_quality_flags=[{
            "kind": "data_unavailable",
            "severity": "warning",
            "detail": f"{source_id}: {reason} — {detail}",
        }],
        manifest={
            "source_id": source_id,
            "adapter_version": ADAPTER_VERSION,
            "ticker": ticker,
            "rows_returned": 0,
            "reason": reason,
        },
        extraction_status="failed",
        error_class=reason,
    )


_XSL_FORM4_PREFIX_RE = _re.compile(r"^xslF345X\d{2}/")


def _strip_xsl_prefix(primary_document: str) -> str:
    """SEC submissions JSON returns Form 4 `primaryDocument` paths with
    an XSL-rendering wrapper directory prefix (e.g. `xslF345X06/form4.xml`).
    The prefixed URL fetches the HTML viewer; the un-prefixed URL fetches
    the raw machine-readable XML. Strip exactly one leading `xslF345X*/`
    segment when present; pass anything else through unchanged."""
    if not isinstance(primary_document, str):
        return primary_document
    return _XSL_FORM4_PREFIX_RE.sub("", primary_document, count=1)


def _resolve_cik_via_sec_edgar(ticker: str) -> str | None:
    """Reuse SECEdgarAdapter's CIK map cache — keep one map on disk."""
    ua = _user_agent()
    if ua is None:
        return None
    helper = SECEdgarAdapter()
    return helper._resolve_cik(ticker, ua)  # noqa: SLF001


def _fetch_submissions_json(*, cik_padded: str, user_agent: str) -> dict | None:
    """Fetch SEC submissions JSON for the given CIK. Returns None on
    any HTTP / parse failure. The caller is responsible for trip-pause
    handling (we surface the response code via logging only)."""
    url = SUBMISSIONS_URL.format(cik=cik_padded)
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT_S, headers={
            "User-Agent": user_agent, "Accept": "application/json",
        })
    except requests.RequestException as e:
        logger.warning("sec_ownership: SEC submissions fetch raised %s",
                       type(e).__name__)
        return None
    if resp.status_code == 429:
        logger.warning("sec_ownership: SEC submissions HTTP 429")
        return None
    if not resp.ok:
        logger.warning("sec_ownership: SEC submissions HTTP %s", resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        logger.warning("sec_ownership: SEC submissions returned non-JSON")
        return None


# ────────────────────────────────────────────────────────────────────
# 13F — institutional ownership concentration
# ────────────────────────────────────────────────────────────────────
@register_adapter
class SEC13FAdapter(AltDataAdapter):
    """13F institutional ownership.

    Source: FMP `/stable/institutional-ownership/symbol-ownership`
    (aggregates SEC 13F-HR filings by issuer; SEC's raw 13F XML
    is filed by HOLDERS not issuers — reverse-indexing is brittle).
    Tier remains 1 because the underlying data is 13F filings.
    """
    source_id = "sec_13f"
    block_target = "sentiment_community_ownership_evidence"

    rate_limit_per_second = 10.0
    sticky_pause_seconds_on_429 = 60

    def credentials_present(self) -> bool:
        # Uses FMP key, not SEC UA — but the SEC UA may also be needed
        # for any fallback we add later. For now require the FMP key.
        from src.data_adapters.fmp_adapter import _get_api_key
        return _get_api_key() is not None

    def _fetch_live(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        from src.data_adapters.fmp_adapter import (
            _rate_limiter, _is_sticky_paused, _set_sticky_pause,
            _get_api_key, _redact, FMP_BASE_URL,
            RATE_LIMIT_HARD_PAUSE_SECONDS,
        )
        api_key = _get_api_key()
        if api_key is None:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="missing_api_key",
                detail="FMP_API_KEY not set",
            )

        paused, reason, remaining = _is_sticky_paused()
        if paused:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="rate_limit_paused",
                detail=f"{reason} (resumes in {remaining}s)",
            )

        _rate_limiter.acquire()

        url = f"{FMP_BASE_URL}/institutional-ownership/symbol-ownership"
        params = {"symbol": ticker, "apikey": api_key}
        try:
            resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT_S)
        except requests.Timeout:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="timeout",
                detail=f"FMP institutional-ownership timeout after {HTTP_TIMEOUT_S}s",
            )
        except requests.RequestException as e:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="request_exception",
                detail=_redact(str(e))[:200],
            )

        if resp.status_code in (404, 403):
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="not_available_on_current_plan",
                detail=f"FMP HTTP {resp.status_code}",
            )
        if resp.status_code in (429, 402):
            _set_sticky_pause(f"HTTP{resp.status_code}",
                               RATE_LIMIT_HARD_PAUSE_SECONDS)
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason=f"http_{resp.status_code}",
                detail="tripped shared FMP sticky pause",
            )
        if not resp.ok:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="http_error",
                detail=f"FMP HTTP {resp.status_code}",
            )
        try:
            data = resp.json()
        except ValueError:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="invalid_json",
                detail="non-JSON body",
            )
        if isinstance(data, dict) and ("Error Message" in data or "error" in data):
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="fmp_error_in_body",
                detail=_redact(str(data.get("Error Message")
                                    or data.get("error", "")))[:200],
            )
        if not isinstance(data, list):
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="unexpected_shape",
                detail=f"expected list, got {type(data).__name__}",
            )

        fetched_at = datetime.now(timezone.utc).isoformat()
        rows: list[dict] = []
        for rec in data:
            if not isinstance(rec, dict):
                continue
            row = self._build_13f_row(
                rec=rec, ticker=ticker, fetched_at=fetched_at,
                decision_timestamp=decision_timestamp,
            )
            if row is not None:
                rows.append(row)

        if not rows:
            return AltDataResult(
                source_id=self.source_id,
                block_target=self.block_target,
                rows=[],
                source_flag=self._live_source_flag(),
                data_quality_flags=[{
                    "kind": "no_recent_13f",
                    "severity": "info",
                    "detail": (f"sec_13f: no 13F holders accepted on or "
                               f"before {decision_timestamp.date()} "
                               f"for {ticker}"),
                }],
                manifest=self._manifest(ticker=ticker, rows=[]),
                extraction_status="ok",
            )

        derived = self._derive_descriptors(rows=rows)
        manifest = self._manifest(ticker=ticker, rows=rows)
        manifest["derived_descriptors"] = derived

        return AltDataResult(
            source_id=self.source_id,
            block_target=self.block_target,
            rows=rows,
            source_flag=self._live_source_flag(),
            data_quality_flags=[],
            manifest=manifest,
            extraction_status="ok",
        )

    def _fetch_stub(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        """Deterministic stub: 3 synthetic 13F holders."""
        accepted_dt = (as_of - timedelta(days=20)).astimezone(timezone.utc)
        report_dt = (as_of - timedelta(days=60)).astimezone(timezone.utc)
        filing_dt = (as_of - timedelta(days=20)).astimezone(timezone.utc)
        fetched_at = datetime.now(timezone.utc).isoformat()
        rows: list[dict] = []
        for i, (holder, pct, shares) in enumerate([
            ("Vanguard Group Inc (stub)", 8.5, 1_300_000_000),
            ("BlackRock Inc (stub)", 7.1, 1_100_000_000),
            ("State Street Corp (stub)", 4.0, 620_000_000),
        ]):
            rows.append({
                "ticker": ticker,
                "as_of": accepted_dt.isoformat(),
                "report_date": report_dt.date().isoformat(),
                "filing_date": filing_dt.date().isoformat(),
                "accepted_datetime": accepted_dt.isoformat(),
                "holder_name": holder,
                "shares_held": shares,
                "shares_change_pct": 0.5 - i,
                "pct_of_portfolio": pct,
                "tier": 1,
                "source": self.source_id,
                "source_flag": "mock_fallback",
                "source_endpoint": "_STUB_/institutional-ownership",
                "payload": {"_stub": True, "rank": i + 1},
                "fetched_at": fetched_at,
                "adapter_version": ADAPTER_VERSION,
                "descriptor_only": True,
                "is_gate": False,
            })
        derived = self._derive_descriptors(rows=rows)
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
            manifest={**self._manifest(ticker=ticker, rows=rows),
                       "stub": True, "derived_descriptors": derived},
            extraction_status="stub",
        )

    def _build_13f_row(
        self, *, rec: dict, ticker: str, fetched_at: str,
        decision_timestamp: datetime,
    ) -> dict | None:
        # FMP fields commonly present:
        #   investorName / holderName, sharesNumber, weight or weightPercent,
        #   ownership (pct), date (= report quarter-end),
        #   filingDate, dateReported, acceptedDate, change
        accepted_raw = (
            rec.get("acceptedDate")
            or rec.get("dateReported")
            or rec.get("filingDate")
        )
        accepted_dt = _parse_iso_or_date(accepted_raw)
        if accepted_dt is None:
            # PIT discipline: a record without an accepted_datetime
            # cannot be admitted — we cannot prove no-lookahead.
            return None
        # Hard cutoff: PIT enforcement uses ACCEPTED_DATETIME (§11.14).
        # report_date is the quarter-end the holdings reflect; using it
        # as the cutoff would be a lookahead bug.
        if accepted_dt > decision_timestamp:
            return None
        report_dt = _parse_iso_or_date(rec.get("date"))
        filing_dt = _parse_iso_or_date(rec.get("filingDate"))

        return {
            "ticker": ticker,
            "as_of": accepted_dt.isoformat(),  # PIT key
            "report_date": (report_dt.date().isoformat() if report_dt
                              else None),
            "filing_date": (filing_dt.date().isoformat() if filing_dt
                              else None),
            "accepted_datetime": accepted_dt.isoformat(),
            "holder_name": str(
                rec.get("investorName") or rec.get("holderName") or ""
            ),
            "shares_held": int(rec.get("sharesNumber") or 0),
            "shares_change_pct": _safe_float(
                rec.get("changeInSharesPercentage")
                or rec.get("changeInShares")
            ),
            "pct_of_portfolio": _safe_float(
                rec.get("weight") or rec.get("weightPercent")
                or rec.get("ownership")
            ),
            "tier": 1,
            "source": self.source_id,
            "source_flag": self._live_source_flag(),
            "source_endpoint": "institutional-ownership/symbol-ownership",
            "payload": {k: v for k, v in rec.items()
                          if isinstance(v, (str, int, float, bool, type(None)))},
            "fetched_at": fetched_at,
            "adapter_version": ADAPTER_VERSION,
            "descriptor_only": True,
            "is_gate": False,
        }

    def _derive_descriptors(self, *, rows: list[dict]) -> dict:
        """Numeric ownership-concentration descriptors per §11.6 /
        §11.13 — these are NEVER categorical and never gates."""
        if not rows:
            return {
                "top_10_holder_concentration": None,
                "top_25_holder_concentration": None,
                "largest_single_holder_pct": None,
                "quarter_over_quarter_holder_count_change": None,
                "row_count": 0,
            }
        sorted_rows = sorted(
            rows,
            key=lambda r: (r.get("pct_of_portfolio") or 0.0),
            reverse=True,
        )
        top10 = sum((r.get("pct_of_portfolio") or 0.0) for r in sorted_rows[:10])
        top25 = sum((r.get("pct_of_portfolio") or 0.0) for r in sorted_rows[:25])
        largest = sorted_rows[0].get("pct_of_portfolio") if sorted_rows else None
        # Quarter-over-quarter holder count change is approximated as
        # the count of rows whose shares_change_pct is non-null and
        # non-zero — a defensible proxy when only one quarter of FMP
        # data is in hand. The PM agent reads this as a NUMERIC
        # descriptor and weighs it; it is NOT categorical.
        changes = [r for r in rows
                    if r.get("shares_change_pct") not in (None, 0)]
        return {
            "top_10_holder_concentration": round(top10, 4),
            "top_25_holder_concentration": round(top25, 4),
            "largest_single_holder_pct": round(largest, 4) if largest else None,
            "quarter_over_quarter_holder_count_change": len(changes),
            "row_count": len(rows),
        }

    def _manifest(self, *, ticker: str, rows: list[dict]) -> dict:
        return {
            "source_id": self.source_id,
            "adapter_version": ADAPTER_VERSION,
            "ticker": ticker,
            "rows_returned": len(rows),
            "endpoint": "fmp:institutional-ownership/symbol-ownership",
            "tier": 1,
        }


# ────────────────────────────────────────────────────────────────────
# Form 4 — insider transactions
# ────────────────────────────────────────────────────────────────────
@register_adapter
class SECForm4Adapter(AltDataAdapter):
    """SEC Form 4 — insider transactions.

    Source: SEC EDGAR submissions JSON (filing index) + per-filing
    XML primary document (transaction details).
    """
    source_id = "sec_form4"
    block_target = "sentiment_community_ownership_evidence"

    rate_limit_per_second = 10.0
    sticky_pause_seconds_on_429 = 60

    def __init__(self, lookback_days: int = FORM4_LOOKBACK_DAYS) -> None:
        super().__init__()
        self.lookback_days = lookback_days

    def credentials_present(self) -> bool:
        return _user_agent() is not None

    def _fetch_live(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        ua = _user_agent()
        if ua is None:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="missing_user_agent",
                detail=f"{ENV_USER_AGENT} not set",
            )
        cik = _resolve_cik_via_sec_edgar(ticker)
        if cik is None:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="cik_not_found",
                detail=f"no CIK for {ticker}",
            )
        cik_padded = cik.zfill(10)
        payload = _fetch_submissions_json(cik_padded=cik_padded, user_agent=ua)
        if payload is None:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="submissions_fetch_failed",
                detail="see logs",
            )

        index_rows = self._extract_form4_index(
            payload=payload, ticker=ticker, cik_padded=cik_padded,
            decision_timestamp=decision_timestamp,
        )

        # For each form 4 filing in the window, fetch the XML primary
        # document and extract structured transaction details. We cap
        # the fetch budget at 25 most-recent filings to respect SEC
        # rate limits during a single decision.
        fetched_at = datetime.now(timezone.utc).isoformat()
        rows: list[dict] = []
        for idx in index_rows[:25]:
            txns = self._fetch_and_parse_form4_xml(
                user_agent=ua,
                primary_document_url=idx["primary_document_url"],
            )
            for txn in txns:
                rows.append(self._build_form4_row(
                    idx=idx, txn=txn, ticker=ticker,
                    fetched_at=fetched_at,
                    decision_timestamp=decision_timestamp,
                ))
        rows = [r for r in rows if r is not None]

        derived = self._derive_descriptors(rows=rows,
                                              decision_timestamp=decision_timestamp)
        manifest = {
            "source_id": self.source_id,
            "adapter_version": ADAPTER_VERSION,
            "ticker": ticker,
            "cik": cik_padded,
            "rows_returned": len(rows),
            "endpoint": "data.sec.gov/submissions + Archives/.../form4.xml",
            "tier": 1,
            "lookback_days": self.lookback_days,
            "derived_descriptors": derived,
        }

        if not rows:
            return AltDataResult(
                source_id=self.source_id,
                block_target=self.block_target,
                rows=[],
                source_flag=self._live_source_flag(),
                data_quality_flags=[{
                    "kind": "no_recent_form4",
                    "severity": "info",
                    "detail": (f"sec_form4: no Form 4 transactions "
                               f"accepted on or before "
                               f"{decision_timestamp.date()} "
                               f"in last {self.lookback_days} days "
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

    def _fetch_stub(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        accepted_dt = (as_of - timedelta(days=10)).astimezone(timezone.utc)
        txn_dt = (as_of - timedelta(days=12)).date()
        filing_dt = accepted_dt.date()
        fetched_at = datetime.now(timezone.utc).isoformat()
        rows = [
            {
                "ticker": ticker,
                "as_of": accepted_dt.isoformat(),
                "transaction_date": txn_dt.isoformat(),
                "filing_date": filing_dt.isoformat(),
                "accepted_datetime": accepted_dt.isoformat(),
                "insider_name": f"_STUB_INSIDER_{ticker}_CEO",
                "insider_role": "CEO",
                "transaction_type": "purchase",
                "shares_transacted": 5000,
                "transaction_value_usd": 750_000.0,
                "post_transaction_holdings": 12_345,
                "tier": 1,
                "source": self.source_id,
                "source_flag": "mock_fallback",
                "source_endpoint": "_STUB_/sec_edgar/form4",
                "payload": {"_stub": True},
                "fetched_at": fetched_at,
                "adapter_version": ADAPTER_VERSION,
                "descriptor_only": True,
                "is_gate": False,
            },
        ]
        derived = self._derive_descriptors(rows=rows,
                                              decision_timestamp=decision_timestamp)
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
                "tier": 1,
                "derived_descriptors": derived,
            },
            extraction_status="stub",
        )

    def _extract_form4_index(
        self, *, payload: dict, ticker: str, cik_padded: str,
        decision_timestamp: datetime,
    ) -> list[dict]:
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accession = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        accepted = recent.get("acceptanceDateTime", [])

        cutoff_lo = (decision_timestamp - timedelta(days=self.lookback_days)).date()
        cutoff_hi_dt = decision_timestamp

        out: list[dict] = []
        for i, form in enumerate(forms):
            if form != "4":
                continue
            try:
                acc = accession[i]
                fd_str = filing_dates[i]
                primary = primary_docs[i]
            except IndexError:
                continue
            try:
                fd = datetime.strptime(fd_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if fd < cutoff_lo:
                continue
            accepted_dt = None
            if i < len(accepted):
                accepted_dt = _parse_iso_or_date(accepted[i])
            if accepted_dt is None:
                # Fall back to filing_date end-of-day; record will still
                # be PIT-checked by the base class.
                accepted_dt = datetime(fd.year, fd.month, fd.day,
                                         23, 59, 59, tzinfo=timezone.utc)
            if accepted_dt > cutoff_hi_dt:
                continue
            acc_no_dashes = acc.replace("-", "")
            # Form 4 fix (2026-04-30): submissions JSON returns
            # `primaryDocument` with an XSL-rendering wrapper prefix
            # (e.g. `xslF345X06/form4.xml` or `xslF345X05/wk-form4_*.xml`).
            # Building the URL from that path fetches the HTML viewer
            # instead of the raw XML, and ET.fromstring then silently
            # returns []. Strip the leading xslF345X*/ segment so the
            # URL points at the machine-readable XML at the accession
            # folder root.
            primary_xml = _strip_xsl_prefix(primary)
            url = f"{ARCHIVE_BASE}/{int(cik_padded)}/{acc_no_dashes}/{primary_xml}"
            out.append({
                "ticker": ticker,
                "cik": cik_padded,
                "accession_number": acc,
                "filing_date": fd.isoformat(),
                "accepted_datetime": accepted_dt.isoformat(),
                "primary_document_url": url,
            })
        return out

    def _fetch_and_parse_form4_xml(
        self, *, user_agent: str, primary_document_url: str,
    ) -> list[dict]:
        """Fetch the Form 4 primary document. If it's an XML doc, parse
        the transaction blocks. If it's HTML or any other shape, return
        an empty list (no silent retries)."""
        if not primary_document_url.endswith(".xml"):
            # SEC requires the form4 XML; HTML wrappers are not
            # structured. Skip; downstream code will see one filing
            # with zero transactions, surfaced via manifest.
            return []
        try:
            resp = requests.get(
                primary_document_url, timeout=HTTP_TIMEOUT_S,
                headers={"User-Agent": user_agent, "Accept": "text/xml"},
            )
        except requests.RequestException:
            return []
        if resp.status_code == 429 or not resp.ok:
            return []
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return []
        return _parse_form4_transactions(root)

    def _build_form4_row(
        self, *, idx: dict, txn: dict, ticker: str, fetched_at: str,
        decision_timestamp: datetime,
    ) -> dict | None:
        accepted_dt = _parse_iso_or_date(idx["accepted_datetime"])
        if accepted_dt is None or accepted_dt > decision_timestamp:
            return None
        return {
            "ticker": ticker,
            "as_of": accepted_dt.isoformat(),
            "transaction_date": txn.get("transaction_date"),
            "filing_date": idx["filing_date"],
            "accepted_datetime": accepted_dt.isoformat(),
            "insider_name": txn.get("insider_name") or "",
            "insider_role": txn.get("insider_role") or "",
            "transaction_type": txn.get("transaction_type") or "",
            "shares_transacted": txn.get("shares_transacted"),
            "transaction_value_usd": txn.get("transaction_value_usd"),
            "post_transaction_holdings": txn.get("post_transaction_holdings"),
            "tier": 1,
            "source": self.source_id,
            "source_flag": self._live_source_flag(),
            "source_endpoint": idx["primary_document_url"],
            "payload": {k: v for k, v in txn.items()
                          if isinstance(v, (str, int, float, bool, type(None)))},
            "fetched_at": fetched_at,
            "adapter_version": ADAPTER_VERSION,
            "descriptor_only": True,
            "is_gate": False,
        }

    def _derive_descriptors(
        self, *, rows: list[dict], decision_timestamp: datetime,
    ) -> dict:
        cutoff_30d = decision_timestamp - timedelta(days=INSIDER_30D_WINDOW_DAYS)
        purchases_30d = 0
        sales_30d = 0
        net_usd_30d = 0.0
        ceo_cfo_recent = False
        for r in rows:
            accepted_dt = _parse_iso_or_date(r.get("accepted_datetime"))
            if accepted_dt is None or accepted_dt < cutoff_30d:
                continue
            txn_type = (r.get("transaction_type") or "").lower()
            value = _safe_float(r.get("transaction_value_usd")) or 0.0
            if txn_type == "purchase":
                purchases_30d += 1
                net_usd_30d += value
            elif txn_type == "sale":
                sales_30d += 1
                net_usd_30d -= value
            role = (r.get("insider_role") or "").upper()
            if "CEO" in role or "CFO" in role:
                ceo_cfo_recent = True
        return {
            "insider_purchase_count_30d": purchases_30d,
            "insider_sale_count_30d": sales_30d,
            "net_insider_buying_usd_30d": round(net_usd_30d, 2),
            "ceo_cfo_recent_activity_flag": ceo_cfo_recent,
            "row_count": len(rows),
        }


def _parse_form4_transactions(root: ET.Element) -> list[dict]:
    """Parse a SEC Form 4 XML document. The schema is:

      <ownershipDocument>
        <reportingOwner>
          <reportingOwnerId><rptOwnerName>Name</rptOwnerName></reportingOwnerId>
          <reportingOwnerRelationship>
            <isDirector>0|1</isDirector>
            <isOfficer>0|1</isOfficer>
            <officerTitle>Chief Executive Officer</officerTitle>
            <isTenPercentOwner>0|1</isTenPercentOwner>
          </reportingOwnerRelationship>
        </reportingOwner>
        <nonDerivativeTable>
          <nonDerivativeTransaction>
            <transactionDate><value>2024-05-15</value></transactionDate>
            <transactionCoding>
              <transactionCode>P</transactionCode>  (P=purchase, S=sale, ...)
            </transactionCoding>
            <transactionAmounts>
              <transactionShares><value>5000</value></transactionShares>
              <transactionPricePerShare><value>150.00</value></transactionPricePerShare>
            </transactionAmounts>
            <postTransactionAmounts>
              <sharesOwnedFollowingTransaction><value>...</value></sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
          </nonDerivativeTransaction>
          ...
        </nonDerivativeTable>
      </ownershipDocument>
    """
    insider_name = ""
    insider_role = ""
    owner = root.find("reportingOwner")
    if owner is not None:
        oid = owner.find("reportingOwnerId")
        if oid is not None:
            name_el = oid.find("rptOwnerName")
            if name_el is not None and name_el.text:
                insider_name = name_el.text.strip()
        rel = owner.find("reportingOwnerRelationship")
        if rel is not None:
            roles: list[str] = []
            if _xml_text_eq(rel, "isDirector", "1"):
                roles.append("Director")
            if _xml_text_eq(rel, "isOfficer", "1"):
                title_el = rel.find("officerTitle")
                title = (title_el.text.strip() if title_el is not None
                          and title_el.text else "Officer")
                roles.append(title)
            if _xml_text_eq(rel, "isTenPercentOwner", "1"):
                roles.append("10%-Owner")
            insider_role = " / ".join(roles)

    out: list[dict] = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        date_val = _xml_value(txn, "transactionDate/value")
        code_val = _xml_value(txn, "transactionCoding/transactionCode")
        shares_val = _xml_value(txn, "transactionAmounts/transactionShares/value")
        price_val = _xml_value(
            txn, "transactionAmounts/transactionPricePerShare/value",
        )
        post_val = _xml_value(
            txn,
            "postTransactionAmounts/sharesOwnedFollowingTransaction/value",
        )
        shares = _safe_float(shares_val)
        price = _safe_float(price_val)
        value_usd = (shares * price) if (shares is not None and price is not None) else None
        out.append({
            "transaction_date": date_val,
            "transaction_type": _form4_code_to_label(code_val),
            "transaction_code": code_val,
            "shares_transacted": int(shares) if shares is not None else None,
            "transaction_price_per_share": price,
            "transaction_value_usd": (round(value_usd, 2)
                                       if value_usd is not None else None),
            "post_transaction_holdings": (int(_safe_float(post_val))
                                            if _safe_float(post_val) is not None
                                            else None),
            "insider_name": insider_name,
            "insider_role": insider_role,
        })
    return out


def _form4_code_to_label(code: str | None) -> str:
    if not code:
        return "unknown"
    table = {
        "P": "purchase",
        "S": "sale",
        "M": "option_exercise",
        "A": "grant",
        "F": "tax_withholding",
        "G": "gift",
        "D": "disposition",
        "X": "option_exercise",
    }
    return table.get(code.upper(), f"other:{code}")


def _xml_value(parent: ET.Element, path: str) -> str | None:
    el = parent.find(path)
    if el is None or el.text is None:
        return None
    s = el.text.strip()
    return s or None


def _xml_text_eq(parent: ET.Element, tag: str, want: str) -> bool:
    el = parent.find(tag)
    if el is None or el.text is None:
        return False
    return el.text.strip() == want


# ────────────────────────────────────────────────────────────────────
# DEF 14A — proxy / governance
# ────────────────────────────────────────────────────────────────────
@register_adapter
class SECDef14AAdapter(AltDataAdapter):
    """SEC DEF 14A proxy statement metadata.

    Source: SEC EDGAR submissions JSON. Per-filing detail extraction
    (executive comp top-5, say-on-pay flag, board changes flag) is
    metadata-only in this version — proxy HTML/XBRL parsing for
    structured fields is genuinely heavy and is flagged as a future
    refinement via `detail_extraction="metadata_only"` in the manifest.
    """
    source_id = "sec_def14a"
    block_target = "sentiment_community_ownership_evidence"

    rate_limit_per_second = 10.0
    sticky_pause_seconds_on_429 = 60

    def __init__(self, lookback_days: int = DEF14A_LOOKBACK_DAYS) -> None:
        super().__init__()
        self.lookback_days = lookback_days

    def credentials_present(self) -> bool:
        return _user_agent() is not None

    def _fetch_live(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        ua = _user_agent()
        if ua is None:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="missing_user_agent",
                detail=f"{ENV_USER_AGENT} not set",
            )
        cik = _resolve_cik_via_sec_edgar(ticker)
        if cik is None:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="cik_not_found",
                detail=f"no CIK for {ticker}",
            )
        cik_padded = cik.zfill(10)
        payload = _fetch_submissions_json(cik_padded=cik_padded, user_agent=ua)
        if payload is None:
            return _failed_result(
                source_id=self.source_id, block_target=self.block_target,
                ticker=ticker, reason="submissions_fetch_failed",
                detail="see logs",
            )

        rows = self._extract_def14a_rows(
            payload=payload, ticker=ticker, cik_padded=cik_padded,
            decision_timestamp=decision_timestamp,
        )
        manifest = {
            "source_id": self.source_id,
            "adapter_version": ADAPTER_VERSION,
            "ticker": ticker,
            "cik": cik_padded,
            "rows_returned": len(rows),
            "endpoint": "data.sec.gov/submissions",
            "tier": 1,
            "detail_extraction": "metadata_only",
            "detail_extraction_note": (
                "DEF 14A proxy detail tables (top-5 executive comp, "
                "say-on-pay text, board nominee table) are HTML/XBRL "
                "structured filings; structured field extraction is "
                "future scope. Current rows expose filing metadata only."
            ),
        }

        if not rows:
            return AltDataResult(
                source_id=self.source_id,
                block_target=self.block_target,
                rows=[],
                source_flag=self._live_source_flag(),
                data_quality_flags=[{
                    "kind": "no_recent_def14a",
                    "severity": "info",
                    "detail": (f"sec_def14a: no DEF 14A filings accepted on "
                               f"or before {decision_timestamp.date()} "
                               f"in last {self.lookback_days} days "
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
            data_quality_flags=[{
                "kind": "metadata_only_extraction",
                "severity": "info",
                "detail": (f"sec_def14a: rows expose filing metadata only "
                           f"(no top-5 comp / say-on-pay parsing yet)"),
            }],
            manifest=manifest,
            extraction_status="ok",
        )

    def _fetch_stub(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        accepted_dt = (as_of - timedelta(days=120)).astimezone(timezone.utc)
        meeting_dt = (as_of - timedelta(days=90)).date()
        filing_dt = accepted_dt.date()
        fetched_at = datetime.now(timezone.utc).isoformat()
        rows = [
            {
                "ticker": ticker,
                "as_of": accepted_dt.isoformat(),
                "filing_date": filing_dt.isoformat(),
                "accepted_datetime": accepted_dt.isoformat(),
                "meeting_date": meeting_dt.isoformat(),
                "executive_compensation_total_top5": None,
                "say_on_pay_proposal_flag": None,
                "board_changes_proposed_flag": None,
                "tier": 1,
                "source": self.source_id,
                "source_flag": "mock_fallback",
                "source_endpoint": "_STUB_/sec_edgar/def14a",
                "payload": {"_stub": True, "filing_type": "DEF 14A"},
                "fetched_at": fetched_at,
                "adapter_version": ADAPTER_VERSION,
                "descriptor_only": True,
                "is_gate": False,
            },
        ]
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
                "tier": 1,
                "detail_extraction": "metadata_only",
            },
            extraction_status="stub",
        )

    def _extract_def14a_rows(
        self, *, payload: dict, ticker: str, cik_padded: str,
        decision_timestamp: datetime,
    ) -> list[dict]:
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accession = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        accepted = recent.get("acceptanceDateTime", [])

        cutoff_lo = (decision_timestamp - timedelta(days=self.lookback_days)).date()
        cutoff_hi = decision_timestamp
        fetched_at = datetime.now(timezone.utc).isoformat()

        out: list[dict] = []
        for i, form in enumerate(forms):
            if form != "DEF 14A":
                continue
            try:
                acc = accession[i]
                fd_str = filing_dates[i]
                primary = primary_docs[i]
            except IndexError:
                continue
            try:
                fd = datetime.strptime(fd_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if fd < cutoff_lo:
                continue
            accepted_dt = None
            if i < len(accepted):
                accepted_dt = _parse_iso_or_date(accepted[i])
            if accepted_dt is None:
                accepted_dt = datetime(fd.year, fd.month, fd.day,
                                         23, 59, 59, tzinfo=timezone.utc)
            if accepted_dt > cutoff_hi:
                continue
            acc_no_dashes = acc.replace("-", "")
            url = f"{ARCHIVE_BASE}/{int(cik_padded)}/{acc_no_dashes}/{primary}"
            out.append({
                "ticker": ticker,
                "as_of": accepted_dt.isoformat(),
                "filing_date": fd.isoformat(),
                "accepted_datetime": accepted_dt.isoformat(),
                "meeting_date": None,        # not present in submissions JSON
                "executive_compensation_total_top5": None,
                "say_on_pay_proposal_flag": None,
                "board_changes_proposed_flag": None,
                "tier": 1,
                "source": self.source_id,
                "source_flag": self._live_source_flag(),
                "source_endpoint": url,
                "payload": {
                    "accession_number": acc,
                    "filing_type": "DEF 14A",
                    "primary_document_url": url,
                },
                "fetched_at": fetched_at,
                "adapter_version": ADAPTER_VERSION,
                "descriptor_only": True,
                "is_gate": False,
            })
        return out


# ── safe-cast helper ──────────────────────────────────────────────
def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
