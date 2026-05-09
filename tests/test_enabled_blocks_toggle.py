"""
Unit test for D1 Step A3: per-block evidence packet toggle.

Asserts:
  1. With enabled_blocks={"price_snapshot", "fundamental_snapshot"},
     ONLY those two blocks are populated; the other ten toggleable
     blocks are null in the output.
  2. Hash differs from the full-packet (enabled_blocks=None) hash.
  3. Default behaviour (enabled_blocks=None) produces the same hash as
     a second default run — byte-identical regression.
  4. Default packet does NOT contain `enabled_blocks` in the envelope
     (preserves byte-identity with pre-A3 packets).
  5. Subset packet DOES contain `enabled_blocks` (sorted list) in the
     envelope.
  6. Unknown block ids in enabled_blocks raise ValueError.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evidence_packet.generator import (  # noqa: E402
    TOGGLEABLE_BLOCK_IDS,
    generate_evidence_packet,
)


TS = "2026-04-27T16:00:00-04:00"
TICKER = "AAPL"


def main() -> int:
    failures: list[str] = []

    def expect(name, ok, detail=""):
        mark = "OK" if ok else "FAIL"
        print(f"  [{mark}] {name}  {detail}")
        if not ok:
            failures.append(name)

    print("\n=== Step A3 enabled_blocks toggle test ===\n")

    # ── 1. Subset packet ────────────────────────────────────────────
    keep = {"price_snapshot", "fundamental_snapshot"}
    p_sub = generate_evidence_packet(
        ticker=TICKER, decision_timestamp=TS, enabled_blocks=keep,
    )
    for block_id in keep:
        expect(f"{block_id} populated in subset",
               isinstance(p_sub.get(block_id), dict))
    other = TOGGLEABLE_BLOCK_IDS - keep
    for block_id in other:
        expect(f"{block_id} is null in subset",
               p_sub.get(block_id, "MISSING") is None,
               f"got {type(p_sub.get(block_id)).__name__}")

    # ── 2. Hash differs ─────────────────────────────────────────────
    p_full_a = generate_evidence_packet(ticker=TICKER, decision_timestamp=TS)
    expect(
        "subset hash differs from full hash",
        p_sub["envelope"]["evidence_packet_hash"]
        != p_full_a["envelope"]["evidence_packet_hash"],
    )

    # ── 3. Default byte-identical regression ────────────────────────
    p_full_b = generate_evidence_packet(ticker=TICKER, decision_timestamp=TS)
    expect(
        "default packet hash stable across runs",
        p_full_a["envelope"]["evidence_packet_hash"]
        == p_full_b["envelope"]["evidence_packet_hash"],
    )

    # ── 4. Default has NO enabled_blocks field ──────────────────────
    expect(
        "default envelope omits enabled_blocks (byte-identity guarantee)",
        "enabled_blocks" not in p_full_a["envelope"],
    )

    # ── 5. Subset envelope records sorted enabled_blocks ────────────
    eb_field = p_sub["envelope"].get("enabled_blocks")
    expect(
        "subset envelope records sorted enabled_blocks",
        eb_field == sorted(keep),
        f"got {eb_field}",
    )

    # ── 6. Unknown id rejected ──────────────────────────────────────
    raised = False
    try:
        generate_evidence_packet(
            ticker=TICKER, decision_timestamp=TS,
            enabled_blocks={"price_snapshot", "no_such_block"},
        )
    except ValueError:
        raised = True
    expect("unknown block id raises ValueError", raised)

    print()
    if failures:
        print(f"  RESULT: {len(failures)} failure(s): {failures}")
        return 1
    print("  RESULT: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
