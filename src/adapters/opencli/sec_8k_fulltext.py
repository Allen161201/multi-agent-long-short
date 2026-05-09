"""
OpenCLI use case: SEC 8-K full-text retrieval.

Given an 8-K filing URL (from src/adapters/alt_data/sec_edgar.py), fetch
and extract clean text via OpenCLI. Complements the metadata-only SEC
EDGAR adapter per RULES.md §17.B.4 (OpenCLI as fallback / corroboration).

LIVE status (Task 4, 2026-04-29 PM): STUB.
  Upstream OpenCLI v1.7.8 ships ~200 site adapters but does NOT include
  a `sec.gov/8k` site adapter. The two viable LIVE paths are:
    1. Install a community plugin that adds the sec.gov adapter
       (`opencli plugin install <name>` — none currently in the
       upstream registry); OR
    2. Use `opencli browser open <filing_url>` + `extract` against the
       running browser bridge — requires the browser-extension stack
       to be active and is fragile against SEC's static HTML format.
  Neither (1) nor (2) is in scope today. Until one is wired, this
  adapter sets `live_unavailable_reason` so the base class
  short-circuits to STUB with a `binary_present_verb_unsupported`
  flag — distinct from the `opencli_stub_or_not_installed` flag, so
  the dashboard and audit log can tell users exactly why STUB fired.

Design decisions:
  - Source URL = primary_document_url from the SEC filing index
  - 30-day cache TTL keyed by accession_number — once published, an
    8-K's text is immutable
  - Per-call timeout: 30 s (large filings; spec target)
  - Stub mode produces deterministic fake 8-K text per accession

Block target: filing_confirmation (full-text section).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .base import OpenCLIAdapter, OpenCLIResult


class SEC8KFulltextAdapter(OpenCLIAdapter):
    source_id = "sec_8k_fulltext"
    block_target = "filing_confirmation"
    allowed_hosts = frozenset({"sec.gov"})
    live_call_timeout_s = 30
    live_unavailable_reason = (
        "upstream_opencli_v1.7.8_lacks_sec_gov_adapter; "
        "community_plugin_or_browser_extract_required"
    )

    def _build_command(self, *, query_terms: dict) -> list[str]:
        url = query_terms["filing_url"]
        return ["opencli", "get", "sec.gov/8k", url, "-f", "json"]

    def _parse_stdout(self, stdout: str) -> dict:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise ValueError(f"opencli stdout not JSON: {e}")
        if not isinstance(payload, dict):
            raise ValueError("opencli stdout did not deserialise to dict")
        # Conservative extraction: try common keys, but never fabricate.
        text = payload.get("text") or payload.get("content") or ""
        items = payload.get("items") or []
        return {
            "filing_url": payload.get("url"),
            "text": text,
            "char_length": len(text),
            "items": items,
            "_data_available_as_of": payload.get("filing_datetime")
                                      or payload.get("accepted_datetime"),
        }

    def _fetch_stub(self, *, query_terms: dict,
                     decision_timestamp: datetime) -> OpenCLIResult:
        accession = query_terms.get("accession_number", "_STUB_8K_")
        url = query_terms.get("filing_url", f"https://_stub_/{accession}.htm")
        # Deterministic fake text — same accession always returns same body.
        text = (
            f"Item 8.01 Other Events.\n\n"
            f"On the date specified in this filing (accession {accession}), "
            f"the Company entered into a material agreement described in the "
            f"attached exhibit. This filing represents stub data emitted by "
            f"sec_8k_fulltext OpenCLI adapter while the binary is not "
            f"installed on PATH or while the test environment forces stub "
            f"mode (STUB_MODE=true). Treat this content as descriptor-only; "
            f"it does NOT represent ground-truth filing text."
        )
        payload = {
            "filing_url": url,
            "text": text,
            "char_length": len(text),
            "items": query_terms.get("items", []),
        }
        return OpenCLIResult.from_payload(
            source_url=url,
            command_used=self._stub_command_str(query_terms),
            query_terms=dict(query_terms),
            data_available_as_of=decision_timestamp.isoformat(),
            extraction_status="stub",
            source_reliability=self._source_reliability(),
            terms_or_access_notes=self._terms_or_access_notes(),
            PIT_safety_notes=self._PIT_safety_notes(),
            payload=payload,
            block_target=self.block_target,
            data_quality_flags=[{
                "kind": "stub_data",
                "severity": "info",
                "detail": (
                    "sec_8k_fulltext: stub mode (deterministic, "
                    "not ground truth)"
                ),
            }],
        )

    def _terms_or_access_notes(self) -> str:
        return (
            "SEC EDGAR public filings are accessible without authentication. "
            "Per SEC fair-access policy, requests carry a User-Agent identifying "
            "the consumer (set in alt-data layer's SEC_EDGAR_USER_AGENT, not "
            "logged here). Public domain — no scraping ToS conflict."
        )

    def _PIT_safety_notes(self) -> str:
        return (
            "8-K text is anchored to the filing's filing_date / accepted_datetime. "
            "PIT-safety holds when filing_date <= allowed_data_cutoff. The "
            "OpenCLI fetch itself is current-time but the underlying document "
            "is timestamped, so the row is replay-safe when the metadata "
            "(filing_date) is."
        )

    def _source_reliability(self) -> str:
        return "T1"   # SEC filings are primary regulator output

    def _source_url_for(self, query_terms: dict) -> str:
        return query_terms.get("filing_url", "https://www.sec.gov/")
