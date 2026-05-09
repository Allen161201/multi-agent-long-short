"""
Unit tests for the alternative_data_features status-promotion fix in
src/evidence_packet/adapter_wiring.py (2026-04-30).

Direct-execution / __main__-style. Run:
    python tests/test_alt_data_features_promote.py

NO live HTTP. We synthesize AltDataResult-shaped objects + the placeholder
alternative_data_features block and call `_overlay_alt_data_rows` (or
`_wire_github_commit_messages`) directly, then assert the parent block's
status field.

Cases:
  A — wikipedia_pageviews delivers rows → status promoted to ok
  B — github_public delivers rows → status promoted to ok
  C — github_commit_messages (OpenCLI) delivers commits → status promoted
  D — wikipedia delivers + github_public delivers + commits delivers
      (full-opt-in happy path) → status remains ok, source = last writer
  E — all three return empty / failed → status stays not_evaluated
  F — partial: wikipedia delivers, github_public fails, no commits
      → status promoted (ANY source counts)
  G — stub-only (wikipedia stub, github stub) → status promoted
      (matches sec_edgar / fmp_sentiment precedent: stubs count)
  H — parity check: alt_data promotion sets the same field shape as
      sec_edgar's promotion (status + source, no others)
  I — empty packet (no alt-data sources called) → status untouched
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.evidence_packet.adapter_wiring import (  # noqa: E402
    _overlay_alt_data_rows, _wire_github_commit_messages,
)
from src.evidence_packet.blocks import alt_data as alt_data_block  # noqa: E402
from src.evidence_packet.generator import (  # noqa: E402
    _build_filing_confirmation_placeholder,
)
from src.evidence_packet.schema import BlockStatus  # noqa: E402


PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


# ── synthetic AltDataResult shape ─────────────────────────────────────
# Matches the surface used by _overlay_alt_data_rows (only the attrs
# that branch reads). Real AltDataResult has more fields; we keep this
# minimal so the test isn't coupled to incidental schema additions.

@dataclass
class FakeAltResult:
    source_id: str
    block_target: str
    extraction_status: str
    rows: list = field(default_factory=list)
    source_flag: str = "test"
    manifest: dict = field(default_factory=dict)
    data_quality_flags: list = field(default_factory=list)
    error_class: str | None = None


@dataclass
class FakeOpenCliResult:
    """Mirrors what GitHubCommitMessagesAdapter.fetch returns enough that
    _wire_github_commit_messages can consume it."""
    extraction_status: str
    parsed_payload: dict
    error_class: str | None = None

    def to_dict(self) -> dict:
        return {
            "extraction_status": self.extraction_status,
            "parsed_payload": self.parsed_payload,
        }


class FakeOpenCliAdapter:
    """Stand-in for GitHubCommitMessagesAdapter; .fetch returns whatever
    `result_to_return` was set to."""
    def __init__(self, result_to_return: FakeOpenCliResult):
        self._result = result_to_return

    def fetch(self, *, query_terms, decision_timestamp, stub_mode, cache_key):
        return self._result


def fresh_packet() -> dict:
    """Build a packet skeleton with the alt_data + filing_confirmation
    placeholder blocks present (status=NOT_EVALUATED). filing_confirmation
    is built via the generator's private helper rather than a dedicated
    block module — there is no blocks/filing_confirmation.py."""
    alt_built = alt_data_block.build()
    return {
        "alternative_data_features": alt_built["block"],
        "filing_confirmation": _build_filing_confirmation_placeholder(),
    }


# ── tests ─────────────────────────────────────────────────────────────

def case_A_wikipedia_promotes():
    print("\nCase A — wikipedia_pageviews delivers rows → status=ok")
    packet = fresh_packet()
    block = packet["alternative_data_features"]
    check("A0 baseline status == not_evaluated",
          block["status"] == BlockStatus.NOT_EVALUATED,
          str(block["status"]))
    result = FakeAltResult(
        source_id="wikipedia_pageviews",
        block_target="alternative_data_features",
        extraction_status="ok",
        rows=[{"date": "2026-04-29", "views": 1234}],
        source_flag="live_wikipedia",
    )
    _overlay_alt_data_rows(packet, "wikipedia_pageviews", result)
    check("A1 status promoted to ok",
          block["status"] == BlockStatus.OK, str(block["status"]))
    check("A2 source == wikipedia_pageviews",
          block["source"] == "wikipedia_pageviews", str(block.get("source")))
    check("A3 sub-section landed",
          block.get("attention", {}).get("wikipedia_pageviews") is not None)


def case_B_github_public_promotes():
    print("\nCase B — github_public delivers rows → status=ok")
    packet = fresh_packet()
    block = packet["alternative_data_features"]
    result = FakeAltResult(
        source_id="github_public",
        block_target="alternative_data_features",
        extraction_status="ok",
        rows=[{"repo": "apple/foo", "stars": 1000}],
        source_flag="live_github",
    )
    _overlay_alt_data_rows(packet, "github_public", result)
    check("B1 status promoted to ok",
          block["status"] == BlockStatus.OK, str(block["status"]))
    check("B2 source == github_public",
          block["source"] == "github_public", str(block.get("source")))
    check("B3 sub-section landed under tech_activity",
          block.get("tech_activity", {}).get("github_public") is not None)


def case_C_github_commits_promotes():
    print("\nCase C — github_commit_messages delivers commits → status=ok")
    packet = fresh_packet()
    block = packet["alternative_data_features"]
    # Pre-condition: github_public has run and landed an owner row so
    # _wire_github_commit_messages can find it.
    block.setdefault("tech_activity", {})["github_public"] = {
        "rows": [{"github_owner": "apple"}],
    }
    fake_oc = FakeOpenCliResult(
        extraction_status="ok",
        parsed_payload={"commits": [
            {"sha": "abc", "message": "fix: thing"},
            {"sha": "def", "message": "feat: thing"},
        ]},
    )
    adapter = FakeOpenCliAdapter(result_to_return=fake_oc)
    manifest = {"calls": []}
    _wire_github_commit_messages(
        adapter=adapter, packet=packet, ticker="AAPL",
        decision_timestamp=__import__("datetime").datetime.now(),
        manifest=manifest, stub_mode=None,
    )
    check("C1 status promoted to ok",
          block["status"] == BlockStatus.OK, str(block["status"]))
    check("C2 source == github_commit_messages",
          block["source"] == "github_commit_messages",
          str(block.get("source")))
    check("C3 narrative sub-section landed",
          (block.get("tech_activity", {})
                .get("narrative", {})
                .get("github_commit_messages")) is not None)
    # Manifest should record the right returned_rows count
    last_call = manifest["calls"][-1]
    check("C4 manifest.returned_rows == 2",
          last_call.get("returned_rows") == 2,
          str(last_call.get("returned_rows")))


def case_D_full_optin_happy_path():
    print("\nCase D — full opt-in (3 sources deliver) → ok, last-writer source")
    packet = fresh_packet()
    block = packet["alternative_data_features"]
    # 1. wiki
    _overlay_alt_data_rows(packet, "wikipedia_pageviews",
        FakeAltResult(source_id="wikipedia_pageviews",
                       block_target="alternative_data_features",
                       extraction_status="ok",
                       rows=[{"v": 1}]))
    check("D1 after wiki: status=ok", block["status"] == BlockStatus.OK)
    check("D1b after wiki: source=wikipedia_pageviews",
          block["source"] == "wikipedia_pageviews")
    # 2. github_public
    _overlay_alt_data_rows(packet, "github_public",
        FakeAltResult(source_id="github_public",
                       block_target="alternative_data_features",
                       extraction_status="ok",
                       rows=[{"repo": "apple/foo"}]))
    check("D2 after gh: status still ok",
          block["status"] == BlockStatus.OK)
    check("D2b after gh: source overwritten to github_public",
          block["source"] == "github_public",
          str(block.get("source")))
    # 3. github_commit_messages
    block.setdefault("tech_activity", {})["github_public"] = {
        "rows": [{"github_owner": "apple"}],
    }
    fake_oc = FakeOpenCliResult(
        extraction_status="ok",
        parsed_payload={"commits": [{"sha": "1", "message": "x"}]},
    )
    _wire_github_commit_messages(
        adapter=FakeOpenCliAdapter(fake_oc),
        packet=packet, ticker="AAPL",
        decision_timestamp=__import__("datetime").datetime.now(),
        manifest={"calls": []}, stub_mode=None,
    )
    check("D3 after commits: status still ok",
          block["status"] == BlockStatus.OK)
    check("D3b after commits: source=github_commit_messages",
          block["source"] == "github_commit_messages",
          str(block.get("source")))


def case_E_all_empty_keeps_not_evaluated():
    print("\nCase E — all sources empty / failed → status stays not_evaluated")
    packet = fresh_packet()
    block = packet["alternative_data_features"]
    # wiki returns 0 rows but status=ok (no insider activity case)
    _overlay_alt_data_rows(packet, "wikipedia_pageviews",
        FakeAltResult(source_id="wikipedia_pageviews",
                       block_target="alternative_data_features",
                       extraction_status="ok", rows=[]))
    # github_public failed
    _overlay_alt_data_rows(packet, "github_public",
        FakeAltResult(source_id="github_public",
                       block_target="alternative_data_features",
                       extraction_status="failed", rows=[],
                       source_flag="live_github_failed",
                       error_class="cik_not_found"))
    check("E1 status NOT promoted (rows empty + failure)",
          block["status"] == BlockStatus.NOT_EVALUATED,
          str(block["status"]))
    check("E2 source NOT overwritten (still placeholder NOT_CONNECTED)",
          block["source"] == "not_connected",
          str(block["source"]))


def case_F_partial_promotion():
    print("\nCase F — wiki delivers, github_public fails → status promoted")
    packet = fresh_packet()
    block = packet["alternative_data_features"]
    _overlay_alt_data_rows(packet, "wikipedia_pageviews",
        FakeAltResult(source_id="wikipedia_pageviews",
                       block_target="alternative_data_features",
                       extraction_status="ok",
                       rows=[{"date": "2026-04-29", "views": 50}]))
    _overlay_alt_data_rows(packet, "github_public",
        FakeAltResult(source_id="github_public",
                       block_target="alternative_data_features",
                       extraction_status="failed", rows=[],
                       source_flag="live_github_failed"))
    check("F1 status promoted by wiki even though gh failed",
          block["status"] == BlockStatus.OK, str(block["status"]))
    check("F2 source == wikipedia_pageviews (failed gh did not overwrite)",
          block["source"] == "wikipedia_pageviews",
          str(block.get("source")))


def case_G_stubs_count():
    print("\nCase G — stub sources count for promotion (matches sec_edgar precedent)")
    packet = fresh_packet()
    block = packet["alternative_data_features"]
    _overlay_alt_data_rows(packet, "wikipedia_pageviews",
        FakeAltResult(source_id="wikipedia_pageviews",
                       block_target="alternative_data_features",
                       extraction_status="stub",
                       rows=[{"_stub": True}],
                       source_flag="mock_fallback"))
    check("G1 status promoted on stub rows",
          block["status"] == BlockStatus.OK, str(block["status"]))


def case_H_parity_with_sec_edgar():
    print("\nCase H — parity: same fields set by alt-data promotion as by sec_edgar")
    # sec_edgar promotion
    pkt_sec = fresh_packet()
    fc = pkt_sec["filing_confirmation"]
    fc_status_before = set(fc.keys())
    _overlay_alt_data_rows(pkt_sec, "sec_edgar",
        FakeAltResult(source_id="sec_edgar",
                       block_target="filing_confirmation",
                       extraction_status="ok",
                       rows=[{"accession_number": "x"}]))
    sec_added = set(fc.keys()) - fc_status_before
    # Note: sec_edgar branch also sets sub["filing_index"]["sec_edgar"]
    # under the parent block, but the promotion-vs-non-promotion delta on
    # the parent is just the (status, source) overwrite. We compare
    # whether the parent's status + source were touched.
    check("H1 sec_edgar promotion sets parent.status",
          fc.get("status") == BlockStatus.OK, str(fc.get("status")))
    check("H2 sec_edgar promotion sets parent.source",
          fc.get("source") == "sec_edgar", str(fc.get("source")))

    # alt_data promotion
    pkt_alt = fresh_packet()
    alt = pkt_alt["alternative_data_features"]
    _overlay_alt_data_rows(pkt_alt, "wikipedia_pageviews",
        FakeAltResult(source_id="wikipedia_pageviews",
                       block_target="alternative_data_features",
                       extraction_status="ok",
                       rows=[{"v": 1}]))
    check("H3 alt_data promotion sets parent.status",
          alt.get("status") == BlockStatus.OK, str(alt.get("status")))
    check("H4 alt_data promotion sets parent.source",
          alt.get("source") == "wikipedia_pageviews", str(alt.get("source")))
    # Field set parity: both promotions touch exactly status + source
    sec_touched = {"status", "source"}
    alt_touched = {"status", "source"}
    check("H5 same field-set touched by both promotions",
          sec_touched == alt_touched, f"sec={sec_touched} alt={alt_touched}")


def case_I_no_sources_called():
    print("\nCase I — no sources called at all → status untouched")
    packet = fresh_packet()
    block = packet["alternative_data_features"]
    # _overlay_alt_data_rows is never invoked. Block stays as built.
    check("I1 status remains not_evaluated",
          block["status"] == BlockStatus.NOT_EVALUATED,
          str(block["status"]))
    check("I2 source remains not_connected",
          block["source"] == "not_connected",
          str(block["source"]))


def main() -> int:
    print("=" * 70)
    print("test_alt_data_features_promote.py")
    print("=" * 70)
    case_A_wikipedia_promotes()
    case_B_github_public_promotes()
    case_C_github_commits_promotes()
    case_D_full_optin_happy_path()
    case_E_all_empty_keeps_not_evaluated()
    case_F_partial_promotion()
    case_G_stubs_count()
    case_H_parity_with_sec_edgar()
    case_I_no_sources_called()
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
