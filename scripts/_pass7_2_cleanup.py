"""Pass 7.2 cleanup: delete Pass 7 UNOBSERVABLE block + L179 'Missing 8-K full text'
+ L183 CINF template; replace with DATA_ABSENCE_PROHIBITION block. Idempotent."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "src" / "agents" / "prompts"

OLD_VERSION_RE = re.compile(
    r'PROMPT_VERSION = "([^"]*?)_2026_05_03_pass7_2_shrink_boundary"'
)
NEW_VERSION_SUFFIX = "_2026_05_03_pass7_2_cleanup"

# UNOBSERVABLE-vs-MISSING block boundary in pm_agent + risk_agent.
# Starts at heading "UNOBSERVABLE-vs-MISSING DATA DISTINCTION (2026-05-03 Pass 7)"
# Ends at "Distinguish UNOBSERVABLE (architectural) from MISSING (adapter returned 0 rows for this ticker)..." sentence
# (full sentence varies slightly between pm_agent and risk_agent — match flexibly).
UNOBSERVABLE_BLOCK_RE = re.compile(
    r"UNOBSERVABLE-vs-MISSING DATA DISTINCTION \(2026-05-03 Pass 7\).*?"
    r"Distinguish UNOBSERVABLE \(architectural\) from MISSING \(adapter returned 0 rows for this ticker\)[^\n]*\n\n",
    re.DOTALL,
)

DATA_ABSENCE_PROHIBITION_BLOCK = """ABSOLUTE PROHIBITION ON CITING DATA ABSENCE AS A REASON (Pass 7.2 cleanup, 2026-05-03):

Data absence — in ANY form, from ANY source, by ANY phrasing — is NEVER a valid reason for:

  - veto
  - watch
  - no_trade
  - needs_more_evidence
  - downgraded confidence
  - deferred decision
  - "awaiting" anything

This prohibition is ABSOLUTE. It applies to:

  - data not in the packet
  - data the system architecturally cannot fetch (whether on the §0 list of 4 architectural facts or not)
  - data that exists in concept but is not visible through the available adapters
  - data that adapters returned empty / null / 404 / incomplete
  - data the agent expected and did not find
  - data the agent thinks would be helpful
  - data the agent thinks "should" be there

You will NEVER write any of the following classes of phrasing in any reasoning field:

  - "X is missing"
  - "X is unavailable"
  - "X is not in the packet"
  - "X data not retrieved"
  - "X stub mode" / "X stub by design"
  - "X feed unavailable"
  - "X 404" / "X plan-restricted"
  - "awaiting X"
  - "would benefit from X"
  - "X would help"
  - "limited X visibility"
  - "X data thin / sparse / incomplete"
  - "without X, conviction is reduced"
  - any paraphrase or hedged variant of the above
  - any reference to specific adapter / endpoint / source names as "missing" or "unavailable"

Decisions are made from what IS in the packet. Period. If the packet contains evidence supporting a thesis, you act on that thesis. If the packet contains evidence contradicting a thesis, you act on the contradiction. The PRESENCE of evidence is the ONLY reasoning input.

Absence reasoning has ONE narrow legitimate use: as bear-thesis input for surge_short candidates per §13.6.SOFT_REASONING and §10.13 anti-pollution. This means: when a public narrative claims a property AND the packet's observable evidence shows that property to be FALSE (not "missing" — actually visibly contradicted by what IS present), the contradiction supports a bear thesis. This is NOT data-absence reasoning. This is contradiction-by-present-evidence reasoning. The reasoning lives on what the packet SHOWS, not on what it does not show.

If you cannot construct a decision from PRESENT packet evidence alone, the decision is no_trade — never watch, never needs_more_evidence. "no_trade" means: present evidence does not support either a long or a short thesis. "no_trade" is not a fallback for uncertainty; it is the default when present evidence does not cohere into either direction.

Hard veto remains ONLY: sleeve_cap_breach, position_size_cap, lookahead_data_used, hindsight R1-R7, §3.8 fundamental gates when fields PRESENT.

"""

ABSENCE_PROHIBITION_SENTINEL = "ABSOLUTE PROHIBITION ON CITING DATA ABSENCE"

# pm_agent-only cleanups outside the UNOBSERVABLE block:
# L179 "Missing 8-K full text (UNOBSERVABLE per stub-mode...)" line (with leading "  - ")
PM_L179_RE = re.compile(
    r"^  - Missing 8-K full text \(UNOBSERVABLE per stub-mode; the 8-K filing index from sec_edgar IS observable — reason from filing list\)\.\n",
    re.MULTILINE,
)

# L183 CINF-class case template paragraph + trailing blank line
PM_CINF_RE = re.compile(
    r"^CINF-class case template: Strong §3\.8 fundamentals \(e\.g\., PE 9\.1, NM 22%, low D/E\) \+ reasonable valuation \+ 8-K stub mode → BUY\. The 8-K stub is UNOBSERVABLE not disqualifying\. The strong fundamentals \+ valuation are the signal\.\n\n",
    re.MULTILINE,
)


def process_file(fname: str, do_pm_extras: bool = False) -> tuple[bool, str]:
    path = PROMPTS / fname
    text = path.read_text(encoding="utf-8")

    # Idempotency check
    if ABSENCE_PROHIBITION_SENTINEL in text:
        return False, f"SKIP {fname}: cleanup already applied"

    original_lines = text.count("\n")

    # Step 2: bump PROMPT_VERSION
    text2, n_ver = OLD_VERSION_RE.subn(
        lambda m: f'PROMPT_VERSION = "{m.group(1)}{NEW_VERSION_SUFFIX}"',
        text,
    )
    if n_ver != 1:
        return False, f"FAIL {fname}: PROMPT_VERSION subs = {n_ver}, expected 1"

    # Step 3+5: replace UNOBSERVABLE block with DATA_ABSENCE_PROHIBITION
    text3, n_unobs = UNOBSERVABLE_BLOCK_RE.subn(DATA_ABSENCE_PROHIBITION_BLOCK, text2)
    if n_unobs != 1:
        return False, f"FAIL {fname}: UNOBSERVABLE block subs = {n_unobs}, expected 1"

    if do_pm_extras:
        # Step 4 prep: also remove L179 "Missing 8-K full text" + L183 CINF template
        text4, n_l179 = PM_L179_RE.subn("", text3)
        if n_l179 != 1:
            return False, f"FAIL {fname}: L179 'Missing 8-K' subs = {n_l179}, expected 1"
        text5, n_cinf = PM_CINF_RE.subn("", text4)
        if n_cinf != 1:
            return False, f"FAIL {fname}: CINF template subs = {n_cinf}, expected 1"
        text_final = text5
    else:
        text_final = text3

    final_lines = text_final.count("\n")
    delta = final_lines - original_lines
    path.write_text(text_final, encoding="utf-8")
    return True, f"OK {fname}: PROMPT_VERSION bumped, UNOBSERVABLE→DATA_ABSENCE_PROHIBITION, " \
                 f"{'L179+CINF removed, ' if do_pm_extras else ''}delta={delta} lines"


def main() -> int:
    targets = [
        ("pm_agent.py", True),    # also do L179 + CINF cleanup
        ("risk_agent.py", False),  # only block swap
    ]
    errors = []
    for fname, do_extras in targets:
        ok, msg = process_file(fname, do_extras)
        print(msg)
        if not ok and msg.startswith("FAIL"):
            errors.append(msg)
    if errors:
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
