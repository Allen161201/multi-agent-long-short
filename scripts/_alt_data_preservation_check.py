"""
Alt-data preservation guard (D4 standing user instruction).

Generates a stub-based evidence packet and audits whether the 11
named alt-data sources from the user's verification list still appear
in their expected locations. Outputs a JSON diff vs the pre-D4 baseline.

Run BEFORE and AFTER each phase. Pre-D4 baseline at
data/altdata/_alt_data_baseline_pre_d4.json.

Stub-based (no LLM cost). Uses default DECISION_TS to match the
regression matrix.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Force stub provider so this is free.
os.environ.pop("LLM_PROVIDER", None)

from evidence_packet.generator import generate_evidence_packet  # noqa: E402

# User's 11-name verification list. Names map to multiple packet locations:
#   - top-level block keys
#   - source_list[*].label entries
#   - alternative_data_features.adapter_selection.adapter_source_list[*].label
ELEVEN_NAMES = [
    "news_event_summary",
    "fmp_news",
    "fmp_sentiment",
    "sec_form4",
    "sec_13f",
    "sec_8k_fulltext",
    "corporate_calendar",
    "alternative_data_features",
    "wikipedia",
    "github_commit_messages",
    "macro_regime",
]

# Search aliases — some names appear under different labels in the packet.
ALIASES: dict[str, list[str]] = {
    "fmp_news": ["fmp_news_stock_latest", "fmp_news"],
    "sec_8k_fulltext": ["sec_8k", "sec_8k_fulltext"],
    "macro_regime": ["macro_regime", "fred_macro_indicators"],
    "corporate_calendar": ["corporate_calendar", "fmp_corporate_calendar"],
}


def _find_in_packet(name: str, packet: dict) -> dict:
    """Return where each candidate alias for `name` appears."""
    candidates = ALIASES.get(name, [name])
    found_locations: list[str] = []

    # 1. Top-level block keys
    for c in candidates:
        if c in packet:
            found_locations.append(f"top_level_block[{c}]")

    # 2. source_list entries
    sl = packet.get("source_list") or []
    for entry in sl:
        label = entry.get("label", "")
        for c in candidates:
            if c == label:
                found_locations.append(
                    f"source_list[label={label}, source={entry.get('source')}, "
                    f"block={entry.get('block')}]"
                )

    # 3. alternative_data_features.adapter_selection.adapter_source_list
    adf = packet.get("alternative_data_features") or {}
    adapt_list = (
        (adf.get("adapter_selection") or {}).get("adapter_source_list") or []
    )
    for entry in adapt_list:
        label = entry.get("label", "")
        for c in candidates:
            if c == label or c in label:
                found_locations.append(
                    f"alt_data_adapter[label={label}, source={entry.get('source')}]"
                )

    # 4. Substring scan in any nested key (last-resort)
    if not found_locations:
        text = json.dumps(packet, default=str).lower()
        for c in candidates:
            if c.lower() in text:
                found_locations.append(f"substring_match_only[{c}]")
                break

    return {
        "name": name,
        "candidates_searched": candidates,
        "found_locations": found_locations,
        "present": bool(found_locations),
    }


def main() -> int:
    print("=== Alt-Data Preservation Check (D4 Phase 1 post-edit) ===")
    print(f"  cwd                 : {ROOT}")
    print(f"  stub provider       : forced (LLM_PROVIDER unset)")

    decision_ts = "2026-04-27T16:00:00-04:00"
    print(f"  decision_timestamp  : {decision_ts}")

    print("\n  generating evidence packet (stub)…")
    packet = generate_evidence_packet(
        ticker="AAPL", decision_timestamp=decision_ts,
    )
    envelope = packet.get("envelope", {})
    print(f"  packet hash         : {envelope.get('evidence_packet_hash')}")
    print(f"  rule_version        : {envelope.get('rule_version')}")

    audit_rows = [_find_in_packet(name, packet) for name in ELEVEN_NAMES]

    print("\n  ── Per-name audit ──")
    for r in audit_rows:
        status = "PRESENT" if r["present"] else "ABSENT"
        loc_count = len(r["found_locations"])
        print(f"    {r['name']:30s}  {status:8s}  ({loc_count} loc(s))")

    # Diff vs pre-D4 baseline.
    baseline_path = (
        ROOT / "data" / "altdata" / "_alt_data_baseline_pre_d4.json"
    )
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_audit = baseline.get("alt_data_source_audit", {})
        # Map baseline name (e.g. "1_news_event_summary") back to bare name.
        baseline_by_name: dict[str, dict] = {}
        for k, v in baseline_audit.items():
            if k.startswith("_"):
                continue
            bare = k.split("_", 1)[1] if "_" in k else k
            baseline_by_name[bare] = v
        print("\n  ── Diff vs pre-D4 baseline ──")
        any_regression = False
        for r in audit_rows:
            base = baseline_by_name.get(r["name"], {})
            base_status = base.get("status", "unknown")
            new_status = "present" if r["present"] else "absent"
            # Allowed transitions: same; not_wired/stub_present->present (improvement);
            # present->present (kept).
            # Regression: present->absent or stub_present->absent.
            regressed = (
                base_status in ("present", "stub_present")
                and new_status == "absent"
            )
            tag = "OK"
            if regressed:
                tag = "REGRESSION"
                any_regression = True
            print(
                f"    {r['name']:30s}  baseline={base_status:14s} "
                f"now={new_status:7s}  {tag}"
            )
        if any_regression:
            print("\n  RESULT: ALT-DATA REGRESSION DETECTED")
            out_path = (
                ROOT / "data" / "altdata"
                / "_alt_data_postcheck_phase1_REGRESSION.json"
            )
        else:
            print("\n  RESULT: 11/11 sources preserved (no regression)")
            out_path = (
                ROOT / "data" / "altdata" / "_alt_data_postcheck_phase1.json"
            )
    else:
        any_regression = False
        out_path = ROOT / "data" / "altdata" / "_alt_data_postcheck_phase1.json"
        print("\n  (no baseline file — first run)")

    out = {
        "phase": "phase_1_post_edits",
        "rule_version": envelope.get("rule_version"),
        "evidence_packet_hash": envelope.get("evidence_packet_hash"),
        "decision_timestamp": decision_ts,
        "ticker": "AAPL",
        "audit_rows": audit_rows,
    }
    out_path.write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n  saved: {out_path}")
    return 1 if any_regression else 0


if __name__ == "__main__":
    sys.exit(main())
