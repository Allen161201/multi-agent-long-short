"""
Pre-13F-rule verify (per user 2026-05-01 directive).

Confirms SEC EDGAR direct (data.sec.gov submissions JSON) can pull
13F-HR filings filed BY major institutional holders inside the
2026-04-01..2026-04-15 window (Q1-2026 13F deadline = Apr 15).

Why this is the right test
--------------------------
13F is filed by HOLDERS not by issuers. To verify SEC EDGAR direct can
deliver 13F evidence at all, we scan a fixed set of well-known
institutional-holder CIKs and count 13F-HR filings with
acceptedDateTime in the Q1 deadline window. A side-probe also
confirms each filing's primary document is fetchable (via the SEC
Archives URL) — this is what an XML-parsing 13F adapter would consume.

Pass criterion (per user spec): >= 10 13F-HR filings across all
probed holders within the 2026-04-01 to 2026-04-15 window. If pass,
the §11.14.1/.2/.3 cadence rule is applied. If fail, the report
explains which path broke (CIK lookup / submissions fetch / form
filter / archives URL).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

# Major US institutional holders. CIKs verified at SEC EDGAR
# (sec.gov/cgi-bin/browse-edgar) prior to commit. These are
# top-N by 13F asset value and overlap heavily with AAPL/NVDA/TSLA
# holder lists (proxy for "13F filers that hold mega-caps").
PROBE_HOLDERS = [
    ("Vanguard Group Inc",    "0000102909"),
    ("BlackRock Inc",          "0001364742"),
    ("State Street Corp",      "0000093751"),
    ("FMR LLC (Fidelity)",     "0000315066"),
    ("T. Rowe Price",          "0001113169"),
    ("JPMorgan Chase & Co",    "0000019617"),
    ("Berkshire Hathaway",     "0001067983"),
    ("Capital World Investors", "0001454387"),
    ("Wellington Management",  "0000902219"),
    ("Geode Capital",          "0001572983"),
    ("Northern Trust",         "0000916052"),
    ("Bank of America",        "0000070858"),
    ("Citadel Advisors",       "0001423053"),
    ("Renaissance Technologies", "0001037389"),
    ("Two Sigma Investments",  "0001179392"),
]

# NOTE — cadence correction (2026-05-01 verify):
# Per SEC rules 13F filings are due 45 days AFTER quarter-end:
#   Q1 (Mar 31) -> due May 15;  Q2 (Jun 30) -> due Aug 14
#   Q3 (Sep 30) -> due Nov 14;  Q4 (Dec 31) -> due Feb 14
# The user's draft cadence rule listed "Q1: Apr 15" which is off-by-
# 30-days. The 2026-04-01..2026-04-15 window contains NO quarterly
# deadline; the most recent was Q4-2025 due 2026-02-14.
WINDOW_START = "2026-02-01"
WINDOW_END = "2026-02-28"
TARGET_FILINGS = 10
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"
HTTP_TIMEOUT_S = 20


def _ua() -> str:
    return os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()


def _probe_holder(name: str, cik: str) -> dict:
    cik_padded = cik.zfill(10)
    url = SUBMISSIONS_URL.format(cik=cik_padded)
    out: dict = {
        "name": name, "cik": cik_padded,
        "submissions_url": url,
        "ok": False, "http_status": None, "error": None,
        "filings_in_window": [], "n_in_window": 0,
        "all_form_types_seen": [],
    }
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT_S, headers={
            "User-Agent": _ua(), "Accept": "application/json",
        })
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["http_status"] = resp.status_code
    if not resp.ok:
        out["error"] = f"HTTP {resp.status_code}"
        return out
    try:
        payload = resp.json()
    except ValueError:
        out["error"] = "non-JSON body"
        return out
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    accepted = recent.get("acceptanceDateTime", [])
    primary_docs = recent.get("primaryDocument", [])
    out["ok"] = True
    out["all_form_types_seen"] = sorted(set(forms))[:30]

    for i, f in enumerate(forms):
        if not (isinstance(f, str) and f.startswith("13F-HR")):
            continue
        try:
            acc = accession[i]
            fd = filing_dates[i]
            ad = accepted[i] if i < len(accepted) else None
            primary = primary_docs[i] if i < len(primary_docs) else None
        except IndexError:
            continue
        # Filter by acceptedDateTime in [WINDOW_START, WINDOW_END]
        if ad and ad[:10] >= WINDOW_START and ad[:10] <= WINDOW_END:
            acc_no_dashes = (acc or "").replace("-", "")
            archive_url = (
                f"{ARCHIVE_BASE}/{int(cik_padded)}/{acc_no_dashes}/{primary}"
                if acc and primary else None
            )
            out["filings_in_window"].append({
                "form": f, "accession_number": acc,
                "filing_date": fd,
                "accepted_datetime": ad,
                "primary_document": primary,
                "archive_url": archive_url,
            })
    out["n_in_window"] = len(out["filings_in_window"])
    return out


def main() -> int:
    print("=== PRE-13F-RULE verify: SEC EDGAR direct 13F-HR pull ===")
    print(f"  window:  {WINDOW_START} .. {WINDOW_END}")
    print(f"  holders: {len(PROBE_HOLDERS)} institutional CIKs")
    if not _ua():
        print("  ABORT: SEC_EDGAR_USER_AGENT env var not set")
        return 2

    out: list[dict] = []
    total = 0
    for name, cik in PROBE_HOLDERS:
        r = _probe_holder(name, cik)
        out.append(r)
        flag = "OK   " if r["ok"] else "FAIL "
        print(f"  {flag} {name:32s} CIK={r['cik']}  "
              f"13F-HR_in_window={r['n_in_window']}  "
              f"http={r['http_status']}  err={r['error']}")
        total += r["n_in_window"]

    print(f"\n  Total 13F-HR filings in window: {total}")
    print(f"  Pass threshold (≥{TARGET_FILINGS}): {'PASS' if total >= TARGET_FILINGS else 'FAIL'}")

    out_path = ROOT / "data" / "altdata" / "_sec_13f_verify_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "window": [WINDOW_START, WINDOW_END],
        "holders_probed": [h[0] for h in PROBE_HOLDERS],
        "total_13f_hr_in_window": total,
        "target": TARGET_FILINGS,
        "verdict": "PASS" if total >= TARGET_FILINGS else "FAIL",
        "per_holder": out,
    }, indent=2, default=str), encoding="utf-8")
    print(f"  saved: {out_path}")

    if total < TARGET_FILINGS:
        # Diagnose
        print("\n  DIAGNOSIS (cause(s) of low count):")
        ok_count = sum(1 for r in out if r["ok"])
        if ok_count < len(PROBE_HOLDERS):
            print(f"    submissions JSON fetch failures: "
                  f"{len(PROBE_HOLDERS)-ok_count}/{len(PROBE_HOLDERS)}")
        # Did we see any 13F-HR at all (across the wider history)?
        any_13f_hr = any(
            any(f.startswith("13F-HR") for f in r["all_form_types_seen"])
            for r in out
        )
        if not any_13f_hr:
            print("    NO holder has any 13F-HR form in the recent submissions JSON")
        else:
            print("    13F-HR forms exist on each filer's recent list, but "
                  "few have acceptedDateTime in the chosen window")
        print("\n  form types observed (first holder for context):")
        if out and out[0]["ok"]:
            print(f"    {out[0]['name']}: {out[0]['all_form_types_seen']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
