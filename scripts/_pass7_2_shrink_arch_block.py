"""Pass 7.2 atomic shrink: PROMPT_VERSION bump + replace v1 ARCHITECTURAL_FACTS_BLOCK
with v3 (4 items, no concept-vs-fetch tutorial). Idempotent."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "src" / "agents" / "prompts"

ACTIVE_PROMPTS = [
    "narrative_event_agent.py",
    "alt_data_verification_agent.py",
    "fundamental_agent.py",
    "network_effect_agent.py",
    "valuation_agent.py",
    "surge_short_agent.py",
    "quality_long_agent.py",
    "risk_agent.py",
    "pm_agent.py",
]

OLD_VERSION_RE = re.compile(
    r'PROMPT_VERSION = "([^"]*?)_2026_05_03_pass7_1_architectural_facts"'
)
NEW_VERSION_SUFFIX = "_2026_05_03_pass7_2_shrink_boundary"

# v1 block boundary: starts at "ARCHITECTURAL FACTS — DATA THAT DOES NOT EXIST IN v0.8.7"
# and ends with the closing newline after the existing-soft-veto-block sentence.
# Then there is a blank line and the existing prompt content resumes.
V1_BLOCK_RE = re.compile(
    r"ARCHITECTURAL FACTS — DATA THAT DOES NOT EXIST IN v0\.8\.7.*?"
    r"Hard veto remains ONLY for the conditions enumerated in the existing universal-soft-veto block "
    r"\(sleeve_cap_breach, position_size_cap, lookahead_data_used, hindsight R1-R7, §3\.8 fundamental gates when fields PRESENT\)\.\n\n",
    re.DOTALL,
)

V3_BLOCK = """ARCHITECTURAL FACTS — DATA THE SYSTEM CANNOT FETCH (Pass 7.2):

The following is a CLOSED list of four data points the v0.8.7 architecture cannot retrieve. Treat as if these concepts do not exist in your decision domain:

  1. Borrow cost trajectory — fixed at 100% per §5.16.
  2. Shares-available-to-borrow — no adapter.
  3. FOMC schedule — no adapter.
  4. CME FedWatch / Fed funds futures positioning — no adapter.

Hard prohibition: do NOT cite items 1–4 in any reasoning field in any form (canonical, paraphrase, hedged, or implied). They are TRANSPARENT to your reasoning.

All other data not in the packet is reasoning input per §13.6.SOFT_REASONING. Reason from what is observable. Do not fabricate expectation about data not in this list.

"""

V3_SENTINEL = "ARCHITECTURAL FACTS — DATA THE SYSTEM CANNOT FETCH"


def main() -> int:
    bumped = 0
    replaced = 0
    skipped = 0
    errors: list[str] = []

    for fname in ACTIVE_PROMPTS:
        path = PROMPTS / fname
        text = path.read_text(encoding="utf-8")
        original = text

        # Idempotency: if v3 sentinel already present, skip.
        if V3_SENTINEL in text:
            print(f"SKIP {fname}: v3 already present")
            skipped += 1
            continue

        # Step 2: bump PROMPT_VERSION.
        new_text, n_sub = OLD_VERSION_RE.subn(
            lambda m: f'PROMPT_VERSION = "{m.group(1)}{NEW_VERSION_SUFFIX}"',
            text,
        )
        if n_sub != 1:
            errors.append(f"{fname}: PROMPT_VERSION regex substitutions = {n_sub}, expected 1")
            continue
        bumped += 1

        # Step 3: replace v1 block with v3.
        new_text2, n_block = V1_BLOCK_RE.subn(V3_BLOCK, new_text)
        if n_block != 1:
            errors.append(f"{fname}: v1 architectural block substitutions = {n_block}, expected 1")
            continue
        replaced += 1

        path.write_text(new_text2, encoding="utf-8")
        print(f"OK {fname}: PROMPT_VERSION bumped + v1→v3 block replaced")

    print(f"\nSummary: bumped={bumped} replaced={replaced} skipped={skipped} errors={len(errors)}")
    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
