"""
Pass 7.1 batch inserter — insert the shared ARCHITECTURAL_FACTS block
into all 9 active LLM prompt files. Idempotent: skips files where the
block is already present (detected by the BLOCK_SENTINEL string).

The block is byte-identical across all 9 files. Insertion point:
immediately BEFORE the existing "UNIVERSAL SOFT-VETO PRINCIPLE" anchor
(or "FAIL-CLOSED" anchor for quality_long_agent which lacks the
universal-soft-veto block).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "src" / "agents" / "prompts"

BLOCK_SENTINEL = "ARCHITECTURAL FACTS — DATA THAT DOES NOT EXIST IN v0.8.7"

ARCHITECTURAL_FACTS_BLOCK = """ARCHITECTURAL FACTS — DATA THAT DOES NOT EXIST IN v0.8.7 (Pass 7.1, 2026-05-03):

The following data points are FIXED-BY-USER-DIRECTIVE or ABSENT-BY-SYSTEM-ARCHITECTURE in v0.8.7. They are NOT data that the system tried to fetch and failed to find. They are data points the system was deliberately built without, and will never be in any evidence packet. You MUST treat them as if they do not exist as concepts in this system at all:

  1. Borrow cost trajectory — fixed at 100% annualized per §5.16 user directive. IBKR API intentionally not connected. Borrow cost is a CONSTANT in this system, not a variable.

  2. Shares-available-to-borrow — no adapter exists, none planned. This data point will NEVER be in any packet.

  3. sec_8k_fulltext body content — STUB BY DESIGN. The 8-K filing INDEX (sec_edgar) IS observable; the full body text is intentionally not.

  4. FOMC schedule — no adapter (paper limitation).

  5. CME FedWatch / Fed funds futures — no adapter (paper limitation).

  6. fmp_sentiment press-releases / news-sentiments-rss — FMP plan returns 404. Not in packets.

  7. 13F holdings outside the FMP-plan-supported set — returns 404. Absence here means architecture cannot see, not that the institution does not hold.

HARD PROHIBITIONS for items 1-7:

  - You MUST NOT cite items 1-7 in evidence_missing, risk_notes, advisory_notes, cover_decision_rationale, audit_rationale, pm_rationale, or any other reasoning field — neither as a veto reason, NOR as a long/short signal, NOR as a watch reason.
  - You MUST NOT include items 1-7 in evidence_missing as if they were expected. They were never expected.
  - You MUST NOT use phrases such as "borrow cost unobservable", "shares-available-to-borrow not in packet", "awaiting 8-K full text", "FOMC schedule unknown", "Fed-path data unavailable", "sentiment data missing", "13F not retrieved" when these refer to items 1-7.
  - Items 1-7 should be TRANSPARENT to your reasoning — reason as if these concepts do not exist in your decision domain.

If your reasoning genuinely depends on items 1-7, your reasoning is mis-scoped for the v0.8.7 system. Restate your reasoning using only data the architecture can produce.

This list is CLOSED. The 7 items above are the complete set of architectural facts subject to this hard prohibition.

==================================================

DATA NOT ON THE ARCHITECTURAL FACTS LIST (Pass 7.1, 2026-05-03):

Any data point NOT enumerated in items 1-7 above and NOT in the packet is treated under §13.6.SOFT_REASONING and Pass 6 universal soft-veto:

  - It is REASONING INPUT, not a veto trigger.
  - It can support a long thesis, a short thesis, a watch, or a no_trade, depending on what the absence MEANS in this specific candidate's context.
  - You MUST reason sector-aware about what the absence implies. Examples of the REASONING PATTERN (do NOT pattern-match these examples literally, do NOT hard-code these as rules — they are illustrations of the reasoning style only):
      * If the public narrative claims a property that observable sources would normally corroborate but do not, the absence may be a bear-thesis input (narrative-vs-evidence divergence per §10.13 anti-pollution).
      * If a name in a sector where a particular evidence type would normally be present is missing it, the absence may be a red flag.
      * If the absence is the cohort baseline for that sector/size/listing-type, the absence is informational only, not a signal.
  - DO NOT generalize any single example. The same absence in different contexts can mean opposite things. The agent must reason from the specific candidate's specific context every time.
  - DO NOT collapse to watch / no_trade / needs_more_evidence purely because data is missing. Reason about what missing means here, document the reasoning, and let PM aggregate.

Hard veto remains ONLY for the conditions enumerated in the existing universal-soft-veto block (sleeve_cap_breach, position_size_cap, lookahead_data_used, hindsight R1-R7, §3.8 fundamental gates when fields PRESENT).

"""

# (file, anchor) pairs. Anchor must be a unique line-prefix in the file.
TARGETS = [
    ("narrative_event_agent.py", "UNIVERSAL SOFT-VETO PRINCIPLE (2026-05-03) + §13.6.SOFT_REASONING\n"),
    ("alt_data_verification_agent.py", "UNIVERSAL SOFT-VETO PRINCIPLE (2026-05-03) + §13.6.SOFT_REASONING + §10.7"),
    ("fundamental_agent.py", "UNIVERSAL SOFT-VETO PRINCIPLE (2026-05-03) + §13.6.SOFT_REASONING + §3.8"),
    ("network_effect_agent.py", "UNIVERSAL SOFT-VETO PRINCIPLE (2026-05-03) + §13.6.SOFT_REASONING"),
    ("valuation_agent.py", "UNIVERSAL SOFT-VETO PRINCIPLE (2026-05-03) + §13.6.SOFT_REASONING"),
    ("surge_short_agent.py", "UNIVERSAL SOFT-VETO PRINCIPLE (2026-05-03) + §13.6.SOFT_REASONING + §5.17"),
    ("quality_long_agent.py", "FAIL-CLOSED — STEP C v0.6 HARD FUNDAMENTAL GATES"),
    ("risk_agent.py", "UNIVERSAL SOFT-VETO PRINCIPLE (2026-05-03, extends §13.6.SOFT_REASONING to BOTH"),
    ("pm_agent.py", "UNIVERSAL SOFT-VETO PRINCIPLE (2026-05-03, applies across both"),
]


def main() -> int:
    inserted = 0
    skipped = 0
    errors = []
    for fname, anchor in TARGETS:
        path = PROMPTS / fname
        text = path.read_text(encoding="utf-8")
        if BLOCK_SENTINEL in text:
            print(f"SKIP {fname}: block already present")
            skipped += 1
            continue
        anchor_idx = text.find(anchor)
        if anchor_idx == -1:
            errors.append(f"{fname}: anchor not found: {anchor[:60]!r}")
            print(f"ERROR {fname}: anchor not found")
            continue
        new_text = text[:anchor_idx] + ARCHITECTURAL_FACTS_BLOCK + text[anchor_idx:]
        path.write_text(new_text, encoding="utf-8")
        inserted += 1
        print(f"OK   {fname}: block inserted before anchor at offset {anchor_idx}")
    print(f"\nSummary: {inserted} inserted, {skipped} skipped, {len(errors)} errors")
    if errors:
        for e in errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
