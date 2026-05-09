"""One-shot patcher: add SOFT_REASONING section to 5 reasoning agent
prompts and bump PROMPT_VERSION. Idempotent — does not double-insert.
Run once during 2026-05-02 v0.8.6 promotion. Safe to delete after."""
from __future__ import annotations

import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SECTION = """
REASONING UNDER INCOMPLETE EVIDENCE (RULES.md §13.6.SOFT_REASONING)
When evidence packet has null or incomplete blocks: do NOT automatically conclude needs_more_evidence or trip a veto. Reason from available evidence. Explicitly state what is missing, what you can still infer from what is present, and what level of confidence is warranted.

For surge_short candidates: absence of expected corroboration can itself be a signal. Consider whether the public narrative structurally contradicts what the available evidence shows. Use industry context to judge what corroboration would be expected.

Illustrative (non-exhaustive, non-thresholded) examples to guide your reasoning — adapt to each candidate's industry:
- A tech/AI/biotech company claims active product development, but R&D investment is unusually low relative to its asset base or operating expenses, or has no GitHub activity / patent filings / clinical trial registrations.
- A company announces large contracts or order growth, but accounts receivable, revenue, or cash flow show no matching uptick.
- A company claims a network-effect platform, but user metrics, transaction volumes, or platform-side activity data are absent or stagnant.
- A public company has unusually sparse SEC filing history relative to its claimed scale of operations — compliance gap is itself a red flag.

Industries differ. Apply judgment based on what would be expected given the company's stated activity. Do not rely on hard thresholds. Synthesize and write your reasoning.
"""

TARGETS = [
    # (file, new PROMPT_VERSION)
    ("src/agents/prompts/narrative_event_agent.py",
     "v0.4_2026_05_02_soft_reasoning"),
    ("src/agents/prompts/alt_data_verification_agent.py",
     "v0.4_2026_05_02_soft_reasoning"),
    ("src/agents/prompts/fundamental_agent.py",
     "v0.4_2026_05_02_soft_reasoning"),
    ("src/agents/prompts/risk_agent.py",
     "v0.5_2026_05_02_soft_reasoning"),
    ("src/agents/prompts/pm_agent.py",
     "v0.5_2026_05_02_soft_reasoning"),
]


def patch_file(path: Path, new_version: str) -> dict:
    src = path.read_text(encoding="utf-8")
    out = {"path": str(path), "before_version": None,
           "after_version": new_version, "section_inserted": False,
           "version_bumped": False}

    m = re.search(r'PROMPT_VERSION\s*=\s*"([^"]+)"', src)
    if m:
        out["before_version"] = m.group(1)
        if out["before_version"] != new_version:
            src = src[:m.start()] + f'PROMPT_VERSION = "{new_version}"' + src[m.end():]
            out["version_bumped"] = True
    else:
        raise RuntimeError(f"no PROMPT_VERSION in {path}")

    if "§13.6.SOFT_REASONING" in src:
        out["section_inserted"] = False
        out["section_already_present"] = True
    else:
        # Find the SYSTEM_PROMPT triple-quoted block and append SECTION
        # before its closing """.
        # Strategy: find SYSTEM_PROMPT = """  ...  """  pattern.
        m2 = re.search(
            r'(SYSTEM_PROMPT\s*=\s*""".*?)(\n""")',
            src, flags=re.DOTALL,
        )
        if not m2:
            raise RuntimeError(f"no SYSTEM_PROMPT triple-quoted block in {path}")
        body = m2.group(1)
        closer = m2.group(2)
        new_body = body + SECTION + closer
        src = src[:m2.start()] + new_body + src[m2.end():]
        out["section_inserted"] = True

    path.write_text(src, encoding="utf-8")
    return out


def main() -> int:
    for rel, new_v in TARGETS:
        p = ROOT / rel
        if not p.exists():
            print(f"MISSING: {rel}")
            continue
        # Backup before patching (one-shot).
        bak = p.with_suffix(p.suffix + ".bak_pre_soft_reasoning_20260502")
        if not bak.exists():
            bak.write_bytes(p.read_bytes())
        result = patch_file(p, new_v)
        print(f"  {rel}")
        print(f"    {result['before_version']} -> {result['after_version']}  "
              f"version_bumped={result['version_bumped']}  "
              f"section_inserted={result['section_inserted']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
