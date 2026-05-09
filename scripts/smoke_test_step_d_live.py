"""
D8 live-mode integration smoke test.

Generates an evidence packet for AAPL with decision_timestamp =
2024-01-15 and live_adapters=True, with STUB_MODE unset so the
adapters attempt real fetches. Verifies:
  - Wikipedia adapter returns real pageview data (no creds needed)
  - SEC EDGAR adapter returns real filings IF SEC_EDGAR_USER_AGENT set
  - GitHub adapter returns real data IF GITHUB_TOKEN configured (else falls through)
  - Reddit adapter falls through to stub (creds not yet set)
  - OpenCLI adapters operate per their use cases (stub on most machines)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Load .env so the SEC_EDGAR_USER_AGENT etc are visible to the adapters.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

# Don't force stub mode — we want to exercise the live path where creds exist.
os.environ.pop("STUB_MODE", None)

from src.evidence_packet.generator import generate_evidence_packet  # noqa: E402

DECISION_TS = "2024-01-15T16:00:00-05:00"


def main() -> int:
    print("\n=== D8 live-mode integration smoke test ===")
    print(f"  ticker=AAPL, decision_timestamp={DECISION_TS}")
    print(f"  STUB_MODE={os.environ.get('STUB_MODE', '<unset>')}")
    print()

    packet = generate_evidence_packet(
        ticker="AAPL",
        decision_timestamp=DECISION_TS,
        live_adapters=True,
    )
    manifest = packet.get("alt_data_manifest", {})
    print("  Adapter manifest:")
    print(f"  {'source_id':<24} {'called':<7} {'rows':<5} "
          f"{'flag':<28} {'status':<10} {'error':<24}")
    print("  " + "-" * 100)
    for c in manifest.get("calls", []):
        sid = c.get("source_id", "?")
        called = "YES" if c.get("called") else "NO"
        rows = c.get("returned_rows", "")
        flag = c.get("source_flag", c.get("skip_reason", ""))
        status = c.get("extraction_status", "")
        err = c.get("error_class") or ""
        print(f"  {sid:<24} {called:<7} {str(rows):<5} {str(flag)[:28]:<28} "
              f"{str(status):<10} {str(err)[:24]:<24}")

    print()
    print(f"  evidence_packet_hash: {packet['envelope']['evidence_packet_hash']}")
    print(f"  locked_decision_id:   {packet['envelope']['locked_decision_id']}")
    print()

    # Confirmations per the spec.
    calls = {c["source_id"]: c for c in manifest.get("calls", [])}

    def _verdict(sid: str, expected: str) -> str:
        c = calls.get(sid, {})
        flag = c.get("source_flag", "")
        # "cache" is a downstream of a prior live fetch — treat it as a
        # valid live-data outcome.
        if expected == "live" and flag == "cache":
            return "OK (cache hit from prior live fetch)"
        if expected in flag or (expected == "stub" and "fallback" in flag):
            return "OK"
        return f"UNEXPECTED ({flag})"

    print("  Per-source verdict:")
    print(f"    wikipedia_pageviews        : {_verdict('wikipedia_pageviews', 'live')}")
    sec_ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if sec_ua and sec_ua not in ("", "<TO BE FILLED>"):
        print(f"    sec_edgar (UA set)         : {_verdict('sec_edgar', 'live')}")
    else:
        print(f"    sec_edgar (UA not set)     : {_verdict('sec_edgar', 'fallback')}")
    gh_tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if gh_tok and gh_tok not in ("", "<TO BE FILLED>"):
        print(f"    github_public (token)      : {_verdict('github_public', 'live')}")
    else:
        print(f"    github_public (anon)       : either live (anon) or fallback")
    print(f"    sec_8k_fulltext (opencli)  : {_verdict('sec_8k_fulltext', 'fallback')}")
    print(f"    github_commit_messages     : {_verdict('github_commit_messages', 'fallback')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
