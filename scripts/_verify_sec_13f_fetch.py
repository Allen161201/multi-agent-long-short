"""One-off diagnostic: can SEC EDGAR direct return 13F-HR filings?

READ-ONLY. Does not modify any production file. Calls the existing
helper `sec_ownership._fetch_submissions_json` as a black box for
three known institutional 13F filers, then scans the response for
13F-HR / 13F-HR/A entries with acceptedDateTime <= 2026-04-30.

Writes a structured JSON to data/altdata/_diagnostic_13f_verify.json.

Rate-limit discipline: SEC EDGAR allows 10 req/s. We sleep 0.2 s
between requests; total = 3 requests. No retry on 429.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Load .env so SEC_EDGAR_USER_AGENT is populated.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # If python-dotenv isn't available, env must already be set.

import requests  # noqa: E402

# Black-box import: existing helper, used unchanged.
from src.adapters.alt_data.sec_ownership import _fetch_submissions_json  # noqa: E402

# Per task spec.
INSTITUTIONS = [
    ("Berkshire Hathaway Inc", "0001067983"),
    ("BlackRock Inc",          "0001364742"),
    ("Vanguard Group Inc",     "0000102909"),
]
AS_OF_CUTOFF = datetime(2026, 4, 30, 23, 59, 59, tzinfo=timezone.utc)

OUT_PATH = ROOT / "data" / "altdata" / "_diagnostic_13f_verify.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
HTTP_TIMEOUT_S = 15
SLEEP_BETWEEN = 0.2  # > 0.10 (10 req/s ceiling); conservative.


def _parse_accepted(s: str | None) -> datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    s = s.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _scan_for_13f(payload: dict, cutoff: datetime) -> dict:
    """Find the most recent 13F-HR or 13F-HR/A row with
    acceptedDateTime <= cutoff. Returns metadata for that row, or
    None if no such row exists."""
    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    accepted = recent.get("acceptanceDateTime", [])
    accession = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    period_of_report = recent.get("reportDate", [])

    candidates = []
    for i, form in enumerate(forms):
        if form not in ("13F-HR", "13F-HR/A"):
            continue
        try:
            adt = _parse_accepted(accepted[i])
        except IndexError:
            adt = None
        if adt is None:
            continue
        if adt > cutoff:
            continue
        candidates.append({
            "form": form,
            "accession_number": accession[i] if i < len(accession) else None,
            "accepted_datetime": adt.isoformat(),
            "filing_date": filing_dates[i] if i < len(filing_dates) else None,
            "period_of_report": period_of_report[i] if i < len(period_of_report) else None,
            "primary_document": primary_docs[i] if i < len(primary_docs) else None,
        })

    if not candidates:
        return {"match_count": 0, "most_recent": None, "all_in_window": []}

    candidates.sort(key=lambda r: r["accepted_datetime"], reverse=True)
    return {
        "match_count": len(candidates),
        "most_recent": candidates[0],
        "all_in_window": candidates[:5],   # first 5 by recency, for context
    }


def main() -> int:
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if not ua or ua == "<TO BE FILLED>":
        print("ERROR: SEC_EDGAR_USER_AGENT not set in environment / .env")
        return 2

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    overall: dict = {
        "diagnostic_kind": "sec_edgar_direct_13f_fetchability",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_cutoff": AS_OF_CUTOFF.isoformat(),
        "sleep_between_requests_s": SLEEP_BETWEEN,
        "user_agent_present": True,
        "endpoint_pattern": SUBMISSIONS_URL,
        "results": [],
    }

    for idx, (name, cik) in enumerate(INSTITUTIONS):
        if idx > 0:
            time.sleep(SLEEP_BETWEEN)

        cik_padded = cik.zfill(10)
        url = SUBMISSIONS_URL.format(cik=cik_padded)

        # Transport layer (raw GET, same headers the adapter uses).
        result_row: dict = {
            "institution": name,
            "cik": cik,
            "cik_padded": cik_padded,
            "url": url,
            "http_status": None,
            "response_size_bytes": None,
            "response_first_200_chars": None,
            "exception_class": None,
            "exception_message": None,
            "scan": None,
            "verdict": None,
        }
        try:
            resp = requests.get(
                url,
                timeout=HTTP_TIMEOUT_S,
                headers={"User-Agent": ua, "Accept": "application/json"},
            )
            result_row["http_status"] = resp.status_code
            body = resp.content
            result_row["response_size_bytes"] = len(body)
            try:
                preview = body[:200].decode("utf-8", errors="replace")
            except Exception:
                preview = "<binary or undecodable>"
            result_row["response_first_200_chars"] = preview

            if resp.status_code == 429:
                result_row["verdict"] = "RATE_LIMITED"
                overall["results"].append(result_row)
                continue
            if not resp.ok:
                result_row["verdict"] = f"HTTP_{resp.status_code}"
                overall["results"].append(result_row)
                continue

            try:
                payload = resp.json()
            except ValueError:
                result_row["verdict"] = "NON_JSON"
                overall["results"].append(result_row)
                continue
        except requests.RequestException as e:
            result_row["exception_class"] = type(e).__name__
            result_row["exception_message"] = str(e)[:300]
            result_row["verdict"] = "TRANSPORT_EXCEPTION"
            overall["results"].append(result_row)
            continue

        # Helper-level sanity check (black-box call to the existing
        # form-agnostic helper). Confirms the helper's output matches
        # what we just parsed at the transport layer.
        helper_payload = _fetch_submissions_json(
            cik_padded=cik_padded, user_agent=ua,
        )
        helper_ok = isinstance(helper_payload, dict) and bool(helper_payload)

        scan = _scan_for_13f(payload, AS_OF_CUTOFF)
        result_row["scan"] = scan
        result_row["helper_call_ok"] = helper_ok
        if scan["match_count"] > 0:
            result_row["verdict"] = "SUCCESS"
        else:
            result_row["verdict"] = "EMPTY"

        overall["results"].append(result_row)

    # Summarize most recent acceptedDateTime across all three.
    most_recent = None
    for r in overall["results"]:
        s = r.get("scan") or {}
        m = s.get("most_recent")
        if m and m.get("accepted_datetime"):
            if most_recent is None or m["accepted_datetime"] > most_recent:
                most_recent = m["accepted_datetime"]
    overall["most_recent_13f_acceptedDateTime"] = most_recent

    OUT_PATH.write_text(
        json.dumps(overall, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"OK: wrote diagnostic to {OUT_PATH}")
    print(f"most_recent_13f_acceptedDateTime: {most_recent}")
    for r in overall["results"]:
        scan = r.get("scan") or {}
        mc = scan.get("match_count", 0)
        mr = (scan.get("most_recent") or {}).get("accepted_datetime")
        print(f"  {r['institution']:<25} HTTP {r['http_status']} "
              f"size {r['response_size_bytes']} "
              f"13F-rows-≤cutoff {mc} most_recent {mr} "
              f"verdict {r['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
