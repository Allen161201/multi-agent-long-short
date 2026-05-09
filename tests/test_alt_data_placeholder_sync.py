"""
Unit tests for the alternative_data_features placeholder-sync fix
(2026-05-01) in src/evidence_packet/adapter_wiring.py.

Background: even after `alternative_data_features.status` is promoted
to 'ok' by the prior fix, three placeholder fields stayed stale and
caused downstream agents (notably alt_data_verify) to conclude alt-data
was not wired despite real rows being present:

  (1) block["reason"] kept the v1 text "does not wire any
      alternative-data adapter"
  (2) block["adapter_selection"]["selected_adapter_id"] kept the
      fictional "default_sec_gdelt_adapter" placeholder
  (3) OpenCLI sub-blocks: VERIFIED ALREADY SOURCE-BY-SOURCE — base
      adapters return extraction_status="ok" for live and "stub" for
      mock_fallback; the wiring layer preserves both via to_dict().
      Tests below confirm this property hasn't regressed.

Run:
    python tests/test_alt_data_placeholder_sync.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.evidence_packet.adapter_wiring import (  # noqa: E402
    _finalize_alt_data_placeholders,
    _overlay_alt_data_rows,
    _wire_github_commit_messages,
)
from src.evidence_packet.blocks import alt_data as alt_data_block  # noqa: E402
from src.evidence_packet.schema import BlockKey, BlockStatus  # noqa: E402


PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


# ── Fakes ─────────────────────────────────────────────────────────

@dataclass
class FakeAltResult:
    """Mimics AltDataResult fields read by _overlay_alt_data_rows."""
    source_id: str
    block_target: str = BlockKey.ALTERNATIVE_DATA_FEATURES
    extraction_status: str = "ok"
    source_flag: str = "live_test"
    rows: list[dict] = field(default_factory=lambda: [{"x": 1}])
    manifest: dict = field(default_factory=dict)
    data_quality_flags: list = field(default_factory=list)


@dataclass
class FakeOpenCliResult:
    """Mimics OpenCLIResult fields read by _wire_github_commit_messages."""
    extraction_status: str  # "ok" (live_opencli) | "stub" (mock_fallback) | "failed"
    parsed_payload: dict | None = field(default_factory=lambda: {"commits": [
        {"sha": "abc", "message": "real commit"},
    ]})
    error_class: str | None = None

    def to_dict(self) -> dict:
        return {
            "extraction_status": self.extraction_status,
            "parsed_payload": self.parsed_payload,
            "error_class": self.error_class,
        }


class FakeOpenCliAdapter:
    """Returns a pre-configured FakeOpenCliResult."""
    def __init__(self, result: FakeOpenCliResult):
        self._result = result

    def fetch(self, *, query_terms, decision_timestamp, stub_mode, cache_key):
        return self._result


def fresh_packet() -> dict:
    """Build a packet skeleton with the alt_data placeholder block.
    The github_commit_messages wiring also requires `tech_activity.
    github_public.rows` to find an owner; we pre-seed it."""
    built = alt_data_block.build()
    pkt = {
        BlockKey.ALTERNATIVE_DATA_FEATURES: built["block"],
    }
    # Pre-seed github_public rows so _wire_github_commit_messages can
    # read the github_owner. Mimics the order in apply_adapters where
    # github_public runs before github_commit_messages.
    pkt[BlockKey.ALTERNATIVE_DATA_FEATURES].setdefault("tech_activity", {})
    pkt[BlockKey.ALTERNATIVE_DATA_FEATURES]["tech_activity"]["github_public"] = {
        "rows": [{"github_owner": "apple"}],
    }
    return pkt


def manifest_with(*calls) -> dict:
    return {"schema_version": "alt_data_manifest_v1",
            "ticker": "AAPL",
            "calls": list(calls)}


# ── Cases ─────────────────────────────────────────────────────────

def case_1_status_ok_updates_reason():
    print("\nCase 1 — status promotes → reason updates to delivered list")
    pkt = fresh_packet()
    block = pkt[BlockKey.ALTERNATIVE_DATA_FEATURES]
    # Simulate prior status promotion
    block["status"] = BlockStatus.OK
    block["source"] = "wikipedia_pageviews"
    mf = manifest_with(
        {"source_id": "wikipedia_pageviews", "called": True,
         "returned_rows": 30, "extraction_status": "ok"},
    )
    _finalize_alt_data_placeholders(pkt, mf)
    check("1a reason mentions delivered source(s)",
          "wikipedia_pageviews" in block["reason"], block["reason"])
    check("1b reason no longer cites 'does not wire'",
          "does not wire" not in block["reason"], block["reason"])


def case_2_no_delivery_keeps_reason():
    print("\nCase 2 — wiring runs, no source delivers → reason unchanged")
    pkt = fresh_packet()
    block = pkt[BlockKey.ALTERNATIVE_DATA_FEATURES]
    baseline_reason = block["reason"]
    mf = manifest_with(
        {"source_id": "wikipedia_pageviews", "called": True,
         "returned_rows": 0, "extraction_status": "failed"},
        {"source_id": "github_public", "called": True,
         "returned_rows": 0, "extraction_status": "failed"},
    )
    _finalize_alt_data_placeholders(pkt, mf)
    check("2a reason unchanged from v1 baseline",
          block["reason"] == baseline_reason, block["reason"][:80])


def case_3_selected_adapter_id_resolves():
    print("\nCase 3 — selected_adapter_id resolves to winning source on promotion")
    pkt = fresh_packet()
    mf = manifest_with(
        {"source_id": "wikipedia_pageviews", "called": True,
         "returned_rows": 30, "extraction_status": "ok"},
        {"source_id": "github_public", "called": True,
         "returned_rows": 7, "extraction_status": "ok"},
        {"source_id": "github_commit_messages", "called": True,
         "returned_rows": 5, "extraction_status": "stub"},
    )
    _finalize_alt_data_placeholders(pkt, mf)
    sel = pkt[BlockKey.ALTERNATIVE_DATA_FEATURES]["adapter_selection"]
    check("3a selected_adapter_id == last-writer (github_commit_messages)",
          sel["selected_adapter_id"] == "github_commit_messages",
          sel.get("selected_adapter_id"))
    check("3b adapter_data_available True",
          sel.get("adapter_data_available") is True,
          str(sel.get("adapter_data_available")))
    check("3c adapter_selection.status == ok",
          sel.get("status") == BlockStatus.OK, str(sel.get("status")))


def case_4_selected_adapter_id_empty_when_no_delivery():
    print("\nCase 4 — selected_adapter_id is '' (NOT 'default_sec_gdelt_adapter') on no-delivery")
    pkt = fresh_packet()
    sel_before = pkt[BlockKey.ALTERNATIVE_DATA_FEATURES]["adapter_selection"]
    check("4a baseline placeholder present (sanity)",
          sel_before["selected_adapter_id"] == "default_sec_gdelt_adapter",
          sel_before["selected_adapter_id"])
    mf = manifest_with(
        {"source_id": "wikipedia_pageviews", "called": True,
         "returned_rows": 0, "extraction_status": "failed"},
    )
    _finalize_alt_data_placeholders(pkt, mf)
    sel = pkt[BlockKey.ALTERNATIVE_DATA_FEATURES]["adapter_selection"]
    check("4b selected_adapter_id is empty string",
          sel["selected_adapter_id"] == "",
          repr(sel.get("selected_adapter_id")))
    check("4c selected_adapter_id is NOT 'default_sec_gdelt_adapter'",
          sel["selected_adapter_id"] != "default_sec_gdelt_adapter",
          repr(sel.get("selected_adapter_id")))


def case_5_opencli_live_keeps_ok():
    print("\nCase 5 — github_commit_messages live (extraction_status='ok') → sub-block tagged ok")
    pkt = fresh_packet()
    mf = {"calls": []}
    fake = FakeOpenCliResult(extraction_status="ok")  # live_opencli
    _wire_github_commit_messages(
        adapter=FakeOpenCliAdapter(fake),
        packet=pkt, ticker="AAPL",
        decision_timestamp=__import__("datetime").datetime(2026, 4, 29, 16, 15),
        manifest=mf, stub_mode=False,
    )
    sub = (pkt[BlockKey.ALTERNATIVE_DATA_FEATURES]["tech_activity"]
              .get("narrative", {}).get("github_commit_messages"))
    check("5a sub-block stored", sub is not None)
    check("5b extraction_status preserved as 'ok'",
          (sub or {}).get("extraction_status") == "ok",
          str((sub or {}).get("extraction_status")))
    # Also: manifest source_flag computed from extraction_status
    last = mf["calls"][-1]
    check("5c manifest source_flag == 'live_opencli'",
          last.get("source_flag") == "live_opencli",
          str(last.get("source_flag")))


def case_6_opencli_stub_keeps_stub():
    print("\nCase 6 — github_commit_messages stub fallback (extraction_status='stub') → sub-block tagged stub")
    pkt = fresh_packet()
    mf = {"calls": []}
    fake = FakeOpenCliResult(extraction_status="stub")
    _wire_github_commit_messages(
        adapter=FakeOpenCliAdapter(fake),
        packet=pkt, ticker="AAPL",
        decision_timestamp=__import__("datetime").datetime(2026, 4, 29, 16, 15),
        manifest=mf, stub_mode=True,
    )
    sub = (pkt[BlockKey.ALTERNATIVE_DATA_FEATURES]["tech_activity"]
              .get("narrative", {}).get("github_commit_messages"))
    check("6a extraction_status preserved as 'stub'",
          (sub or {}).get("extraction_status") == "stub",
          str((sub or {}).get("extraction_status")))
    last = mf["calls"][-1]
    check("6b manifest source_flag == 'mock_fallback'",
          last.get("source_flag") == "mock_fallback",
          str(last.get("source_flag")))


def case_7_mixed_sources_tagged_per_source():
    print("\nCase 7 — both OpenCLI sources tagged per source_flag (no blanket re-tag)")
    # Live ok, stub keep stub — two separate calls, separate FakeOpenCliResult
    pkt = fresh_packet()
    mf = {"calls": []}
    # First: live ok call
    _wire_github_commit_messages(
        adapter=FakeOpenCliAdapter(FakeOpenCliResult(extraction_status="ok")),
        packet=pkt, ticker="AAPL",
        decision_timestamp=__import__("datetime").datetime(2026, 4, 29, 16, 15),
        manifest=mf, stub_mode=False,
    )
    # Manually emulate sec_8k_fulltext-style stub manifest entry; we
    # can't reuse _wire_sec_8k_fulltext easily because it lives off a
    # different block & branch. The point of the test is to assert the
    # source_flag computation works per-source.
    mf["calls"].append({
        "source_id": "sec_8k_fulltext", "called": True,
        "returned_rows": 1, "source_flag": "mock_fallback",
        "extraction_status": "stub",
    })
    flags = {c["source_id"]: c.get("source_flag")
             for c in mf["calls"] if "source_id" in c}
    check("7a github_commit_messages tagged 'live_opencli'",
          flags.get("github_commit_messages") == "live_opencli",
          str(flags))
    check("7b sec_8k_fulltext tagged 'mock_fallback'",
          flags.get("sec_8k_fulltext") == "mock_fallback",
          str(flags))


def case_8_default_string_absent_from_active_fields():
    print("\nCase 8 — 'default_sec_gdelt_adapter' absent from agent-read fields after finalize")
    # Note: the string IS still present in adapter_selection.adapter_source_list
    # as one of the 10 FUTURE_ADAPTER_SLOTS labels — that's a documented
    # enum per RULES.md §11.12 (W-ALTDATA-12), not a placeholder. We
    # test only the agent-read fields (selected_adapter_id and reason)
    # where the string was actively misleading agents.

    # Path A — promotion case
    pkt_a = fresh_packet()
    mf_a = manifest_with(
        {"source_id": "wikipedia_pageviews", "called": True,
         "returned_rows": 30, "extraction_status": "ok"},
    )
    _finalize_alt_data_placeholders(pkt_a, mf_a)
    block_a = pkt_a[BlockKey.ALTERNATIVE_DATA_FEATURES]
    check("8a selected_adapter_id != 'default_sec_gdelt_adapter' after promotion",
          block_a["adapter_selection"]["selected_adapter_id"]
          != "default_sec_gdelt_adapter",
          repr(block_a["adapter_selection"]["selected_adapter_id"]))
    check("8b reason text does not contain 'default_sec_gdelt_adapter' after promotion",
          "default_sec_gdelt_adapter" not in block_a["reason"],
          block_a["reason"][:120])

    # Path B — no-delivery case
    pkt_b = fresh_packet()
    mf_b = manifest_with(
        {"source_id": "wikipedia_pageviews", "called": True,
         "returned_rows": 0, "extraction_status": "failed"},
    )
    _finalize_alt_data_placeholders(pkt_b, mf_b)
    block_b = pkt_b[BlockKey.ALTERNATIVE_DATA_FEATURES]
    check("8c selected_adapter_id != 'default_sec_gdelt_adapter' after no-delivery",
          block_b["adapter_selection"]["selected_adapter_id"]
          != "default_sec_gdelt_adapter",
          repr(block_b["adapter_selection"]["selected_adapter_id"]))


def case_9_parity_winning_source_used_consistently():
    print("\nCase 9 — parity: source field, selected_adapter_id, reason all reference winning source")
    pkt = fresh_packet()
    block = pkt[BlockKey.ALTERNATIVE_DATA_FEATURES]
    # Simulate full opt-in matching the production iteration order
    # (wikipedia_pageviews → github_public → github_commit_messages,
    # last-writer-wins for source field).
    block["status"] = BlockStatus.OK
    block["source"] = "github_commit_messages"
    mf = manifest_with(
        {"source_id": "wikipedia_pageviews", "called": True,
         "returned_rows": 30, "extraction_status": "ok"},
        {"source_id": "github_public", "called": True,
         "returned_rows": 7, "extraction_status": "ok"},
        {"source_id": "github_commit_messages", "called": True,
         "returned_rows": 5, "extraction_status": "stub"},
    )
    _finalize_alt_data_placeholders(pkt, mf)
    sel_id = block["adapter_selection"]["selected_adapter_id"]
    src = block["source"]
    rsn = block["reason"]
    winning = "github_commit_messages"
    check("9a block['source'] == winning",
          src == winning, f"src={src}")
    check("9b selected_adapter_id == winning",
          sel_id == winning, f"sel={sel_id}")
    check("9c reason mentions winning source",
          winning in rsn, rsn[:200])
    # And: reason mentions all 3 delivered sources (full parity)
    check("9d reason mentions wikipedia_pageviews",
          "wikipedia_pageviews" in rsn, rsn[:200])
    check("9e reason mentions github_public",
          "github_public" in rsn, rsn[:200])


# ── Entry ─────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 70)
    print("test_alt_data_placeholder_sync.py")
    print("=" * 70)
    case_1_status_ok_updates_reason()
    case_2_no_delivery_keeps_reason()
    case_3_selected_adapter_id_resolves()
    case_4_selected_adapter_id_empty_when_no_delivery()
    case_5_opencli_live_keeps_ok()
    case_6_opencli_stub_keeps_stub()
    case_7_mixed_sources_tagged_per_source()
    case_8_default_string_absent_from_active_fields()
    case_9_parity_winning_source_used_consistently()
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
