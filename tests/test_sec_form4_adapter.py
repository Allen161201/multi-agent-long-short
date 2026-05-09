"""
Unit tests for src/adapters/alt_data/sec_ownership.py::SECForm4Adapter

Direct-execution / __main__-style. Run:
    python tests/test_sec_form4_adapter.py

NO live HTTP. Uses unittest.mock.patch to substitute the module-level
helpers `_user_agent`, `_resolve_cik_via_sec_edgar`,
`_fetch_submissions_json`, and `requests.get` (for the per-filing XML
fetch) with deterministic stubs.

Cases A-G + the regression case for the 2026-04-30 xsl-prefix bug.
"""
from __future__ import annotations

import sys
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.adapters.alt_data import sec_ownership  # noqa: E402
from src.adapters.alt_data.sec_ownership import (  # noqa: E402
    SECForm4Adapter, _strip_xsl_prefix,
)


PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


# ── synthetic fixtures ────────────────────────────────────────────────

def make_submissions_payload(filings: list[dict]) -> dict:
    """Build a SEC-shaped submissions JSON. Each `filings` entry is
    {form, filingDate, acceptanceDateTime, accessionNumber, primaryDocument}."""
    return {
        "filings": {
            "recent": {
                "form": [f["form"] for f in filings],
                "filingDate": [f["filingDate"] for f in filings],
                "acceptanceDateTime": [f["acceptanceDateTime"] for f in filings],
                "accessionNumber": [f["accessionNumber"] for f in filings],
                "primaryDocument": [f["primaryDocument"] for f in filings],
            }
        }
    }


def make_form4_xml(*, owner: str = "Tim Cook", title: str = "CEO",
                   txn_date: str = "2026-04-15", code: str = "S",
                   shares: int = 5000, price: float = 200.0,
                   post_holdings: int = 100000) -> str:
    """Minimal Form 4 XML with one nonDerivativeTransaction."""
    return f"""<?xml version="1.0"?>
<ownershipDocument>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerName>{owner}</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>0</isDirector>
            <isOfficer>1</isOfficer>
            <officerTitle>{title}</officerTitle>
            <isTenPercentOwner>0</isTenPercentOwner>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionDate><value>{txn_date}</value></transactionDate>
            <transactionCoding>
                <transactionCode>{code}</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares><value>{shares}</value></transactionShares>
                <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>{post_holdings}</value></sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>
"""


class FakeResponse:
    """Minimal requests.Response stand-in for monkeypatching."""
    def __init__(self, *, status_code: int = 200, text: str = "",
                 ok: bool | None = None):
        self.status_code = status_code
        self.text = text
        self.ok = ok if ok is not None else (200 <= status_code < 400)


def run_adapter(*, submissions_payload: dict,
                xml_responses_by_url: dict[str, FakeResponse],
                decision_iso: str = "2026-04-29T16:15:00+00:00",
                ticker: str = "AAPL"):
    """Drive SECForm4Adapter._fetch_live with mocked dependencies. Returns
    the AltDataResult."""
    adapter = SECForm4Adapter(lookback_days=90)
    decision_dt = datetime.fromisoformat(decision_iso)

    def fake_requests_get(url, **kwargs):
        # Per-filing XML fetch path; submissions JSON goes through the
        # _fetch_submissions_json mock instead.
        return xml_responses_by_url.get(url, FakeResponse(status_code=404, text=""))

    with mock.patch.object(sec_ownership, "_user_agent",
                            return_value="test-agent test@example.com"), \
         mock.patch.object(sec_ownership, "_resolve_cik_via_sec_edgar",
                            return_value="320193"), \
         mock.patch.object(sec_ownership, "_fetch_submissions_json",
                            return_value=submissions_payload), \
         mock.patch.object(sec_ownership.requests, "get",
                            side_effect=fake_requests_get):
        return adapter._fetch_live(
            ticker=ticker, as_of=decision_dt, decision_timestamp=decision_dt,
        )


# ── tests ─────────────────────────────────────────────────────────────

def case_A_helper_strips_xsl_prefix():
    print("\nCase A — _strip_xsl_prefix helper")
    check("A1 xslF345X06/form4.xml → form4.xml",
          _strip_xsl_prefix("xslF345X06/form4.xml") == "form4.xml")
    check("A2 xslF345X05/wk-form4_xxx.xml → wk-form4_xxx.xml",
          _strip_xsl_prefix("xslF345X05/wk-form4_999.xml") == "wk-form4_999.xml")
    check("A3 unprefixed path passes through",
          _strip_xsl_prefix("form4.xml") == "form4.xml")
    check("A4 unrelated subdir untouched",
          _strip_xsl_prefix("subdir/form4.xml") == "subdir/form4.xml")
    check("A5 missing slash → unchanged",
          _strip_xsl_prefix("xslF345X06form4.xml") == "xslF345X06form4.xml")


def case_B_single_form4_in_window():
    print("\nCase B — single Form 4 in lookback window → 1 row")
    filings = [{
        "form": "4", "filingDate": "2026-04-15",
        "acceptanceDateTime": "2026-04-15T22:30:00.000Z",
        "accessionNumber": "0001140361-26-000001",
        "primaryDocument": "xslF345X06/form4.xml",
    }]
    sub = make_submissions_payload(filings)
    # The adapter (after fix) should request the XSL-stripped URL:
    fixed_url = (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000114036126000001/form4.xml"
    )
    xml_resp = {fixed_url: FakeResponse(text=make_form4_xml())}
    result = run_adapter(submissions_payload=sub,
                          xml_responses_by_url=xml_resp)
    check("B1 extraction_status == ok",
          result.extraction_status == "ok",
          str(result.extraction_status))
    check("B2 rows count == 1",
          len(result.rows) == 1, f"got {len(result.rows)}")
    if result.rows:
        r = result.rows[0]
        check("B3 insider_name parsed",
              r.get("insider_name") == "Tim Cook",
              str(r.get("insider_name")))
        check("B4 transaction_type == sale (code S)",
              r.get("transaction_type") == "sale",
              str(r.get("transaction_type")))
        check("B5 shares_transacted parsed",
              r.get("shares_transacted") == 5000,
              str(r.get("shares_transacted")))


def case_C_form4_amendment():
    print("\nCase C — Form 4/A (amendment) currently EXCLUDED (form != '4')")
    filings = [
        {"form": "4/A", "filingDate": "2026-04-10",
         "acceptanceDateTime": "2026-04-10T22:30:00.000Z",
         "accessionNumber": "0001140361-26-000002",
         "primaryDocument": "xslF345X06/form4.xml"},
    ]
    sub = make_submissions_payload(filings)
    fixed_url = (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000114036126000002/form4.xml"
    )
    xml_resp = {fixed_url: FakeResponse(text=make_form4_xml(owner="A. Mendment"))}
    result = run_adapter(submissions_payload=sub,
                          xml_responses_by_url=xml_resp)
    # Documenting current behaviour: the strict `form != "4"` filter
    # excludes amendments. Out of scope for this bug-fix task; covered
    # by a backlog item. Test asserts the current behaviour so we notice
    # if intent shifts.
    check("C1 4/A excluded (current behaviour: 0 rows)",
          len(result.rows) == 0,
          f"got {len(result.rows)} rows; if amendments are now wanted, "
          f"update SECForm4Adapter._extract_form4_index filter")


def case_D_outside_window():
    print("\nCase D — Form 4 OUTSIDE lookback window → 0 rows")
    # 91 days before cutoff (2026-04-29 - 91d = 2026-01-28)
    filings = [{
        "form": "4", "filingDate": "2026-01-28",
        "acceptanceDateTime": "2026-01-28T22:30:00.000Z",
        "accessionNumber": "0001140361-26-000003",
        "primaryDocument": "xslF345X06/form4.xml",
    }]
    sub = make_submissions_payload(filings)
    result = run_adapter(submissions_payload=sub, xml_responses_by_url={})
    check("D1 0 rows when filing predates lookback",
          len(result.rows) == 0, f"got {len(result.rows)}")
    check("D2 status still ok with no_recent_form4 quality flag",
          result.extraction_status == "ok"
          and any(f.get("kind") == "no_recent_form4"
                  for f in result.data_quality_flags),
          str(result.data_quality_flags))


def case_E_mixed_forms():
    print("\nCase E — mixed forms (4, 8-K, 10-K, 4/A) → only Form 4 family rows")
    filings = [
        {"form": "8-K", "filingDate": "2026-04-20",
         "acceptanceDateTime": "2026-04-20T08:00:00.000Z",
         "accessionNumber": "0001140361-26-000010",
         "primaryDocument": "aapl-8k.htm"},
        {"form": "4", "filingDate": "2026-04-15",
         "acceptanceDateTime": "2026-04-15T22:30:00.000Z",
         "accessionNumber": "0001140361-26-000011",
         "primaryDocument": "xslF345X06/form4.xml"},
        {"form": "10-K", "filingDate": "2026-04-10",
         "acceptanceDateTime": "2026-04-10T20:30:00.000Z",
         "accessionNumber": "0001140361-26-000012",
         "primaryDocument": "aapl-10k.htm"},
        {"form": "4/A", "filingDate": "2026-04-05",
         "acceptanceDateTime": "2026-04-05T22:30:00.000Z",
         "accessionNumber": "0001140361-26-000013",
         "primaryDocument": "xslF345X06/form4.xml"},
    ]
    sub = make_submissions_payload(filings)
    form4_url = (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000114036126000011/form4.xml"
    )
    xml_resp = {form4_url: FakeResponse(text=make_form4_xml())}
    result = run_adapter(submissions_payload=sub,
                          xml_responses_by_url=xml_resp)
    # Only the strict-"4" filing should produce rows in current behaviour.
    check("E1 1 row (only the form='4' filing is included)",
          len(result.rows) == 1, f"got {len(result.rows)}")


def case_F_empty_response():
    print("\nCase F — empty SEC response → 0 rows, graceful")
    sub = make_submissions_payload([])
    result = run_adapter(submissions_payload=sub, xml_responses_by_url={})
    check("F1 extraction_status == ok",
          result.extraction_status == "ok",
          str(result.extraction_status))
    check("F2 rows == []", len(result.rows) == 0)
    check("F3 no_recent_form4 flag",
          any(f.get("kind") == "no_recent_form4"
              for f in result.data_quality_flags))


def case_G_missing_fields():
    print("\nCase G — missing fields in SEC response → graceful, no crash")
    # Truncated arrays: 'form' has 3 entries but other arrays have only 2
    payload = {
        "filings": {
            "recent": {
                "form": ["4", "4", "4"],
                "filingDate": ["2026-04-15", "2026-04-10"],
                "acceptanceDateTime": ["2026-04-15T22:30:00.000Z",
                                          "2026-04-10T22:30:00.000Z"],
                "accessionNumber": ["0001140361-26-000020",
                                       "0001140361-26-000021"],
                "primaryDocument": ["xslF345X06/form4.xml",
                                       "xslF345X06/form4.xml"],
            }
        }
    }
    fixed_urls = [
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000114036126000020/form4.xml",
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000114036126000021/form4.xml",
    ]
    xml_resp = {u: FakeResponse(text=make_form4_xml()) for u in fixed_urls}
    # Should not crash; the third 'form' entry (no matching index) is skipped.
    result = run_adapter(submissions_payload=payload,
                          xml_responses_by_url=xml_resp)
    check("G1 no crash — got result back",
          result is not None and result.extraction_status == "ok",
          str(result and result.extraction_status))
    check("G2 only the 2 well-formed filings produce rows",
          len(result.rows) == 2, f"got {len(result.rows)}")


def case_H_xsl_prefix_regression():
    print("\nCase H — REGRESSION: with XSL prefix, adapter returns rows (the bug)")
    # Without the fix the adapter would build a URL with `xslF345X06/`
    # included, the response would be HTML, ET.fromstring raises,
    # _fetch_and_parse_form4_xml returns []. After the fix we serve XML
    # at the FIXED url and verify rows come through.
    filings = [{
        "form": "4", "filingDate": "2026-04-15",
        "acceptanceDateTime": "2026-04-15T22:30:00.000Z",
        "accessionNumber": "0001140361-26-000099",
        "primaryDocument": "xslF345X06/form4.xml",   # the prefixed shape
    }]
    sub = make_submissions_payload(filings)
    fixed_url = (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000114036126000099/form4.xml"
    )
    buggy_url = (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000114036126000099/xslF345X06/form4.xml"
    )
    # Mock both URLs:
    #   - buggy: returns HTML wrapper (would parse-error in old code)
    #   - fixed: returns valid XML
    xml_resp = {
        buggy_url: FakeResponse(text="<!DOCTYPE html><html>nope</html>"),
        fixed_url: FakeResponse(text=make_form4_xml(
            owner="Regression Insider", code="P", shares=1000,
        )),
    }
    result = run_adapter(submissions_payload=sub,
                          xml_responses_by_url=xml_resp)
    check("H1 fix routes to fixed URL → rows > 0",
          len(result.rows) > 0,
          f"got {len(result.rows)}; if 0, the xsl-strip fix is not effective")
    if result.rows:
        check("H2 first row's insider_name matches XML owner",
              result.rows[0].get("insider_name") == "Regression Insider",
              str(result.rows[0].get("insider_name")))
        check("H3 transaction_type == purchase (code P)",
              result.rows[0].get("transaction_type") == "purchase",
              str(result.rows[0].get("transaction_type")))


def main() -> int:
    print("=" * 70)
    print("test_sec_form4_adapter.py")
    print("=" * 70)
    case_A_helper_strips_xsl_prefix()
    case_B_single_form4_in_window()
    case_C_form4_amendment()
    case_D_outside_window()
    case_E_mixed_forms()
    case_F_empty_response()
    case_G_missing_fields()
    case_H_xsl_prefix_regression()
    print()
    print("=" * 70)
    print(f"PASS: {len(PASS)}    FAIL: {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("All cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
