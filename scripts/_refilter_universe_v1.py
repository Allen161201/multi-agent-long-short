"""
Post-pass refilter for universe_master_v1.json.

Applies stricter symbol filters to the EXISTING artifact's survivors —
no new FMP calls. Drops:
  * `-WT` warrants (missed by the original regex, which had WS/W/R/U
    but not WT).
  * dotted symbols on FMP /stable (which uses dashes for US class
    shares, so dotted symbols on this surface are foreign listings:
    `.L`, `.PA`, `.OL`, `.HK`, `.DE`, `.TO`, etc.).

Rewrites `data/universe/universe_master_v1.json` in place with the
post-filter applied. Bumps `filter_rules_applied` to record the new
rules. universe_version stays "v1" because the task treats v1 as the
first frozen artifact and this is a cleanup pass *before* final
freezing — see UNIVERSE_V1_NOTES for the version-bump policy that
applies to any future change.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "data" / "universe" / "universe_master_v1.json"
META = ROOT / "data" / "universe" / "universe_master_v1.meta.json"


def main():
    art = json.loads(ART.read_text())
    meta = json.loads(META.read_text()) if META.exists() else {}

    before_active = art.get("active_count")
    before_delisted = art.get("delisted_count")

    new_drops = {"warrant_WT": 0, "foreign_dotted_symbol": 0}
    kept: list[dict] = []
    for row in art["tickers"]:
        sym = row.get("symbol", "")
        # Drop -WT warrants
        if sym.endswith("-WT"):
            new_drops["warrant_WT"] += 1
            continue
        # Drop foreign dotted symbols (FMP /stable uses dashes for US class shares)
        if "." in sym:
            new_drops["foreign_dotted_symbol"] += 1
            continue
        kept.append(row)

    art["tickers"] = kept
    art["active_count"] = sum(1 for r in kept if r["status"] == "active")
    art["delisted_count"] = sum(1 for r in kept if r["status"] == "delisted")
    art.setdefault("filter_rules_applied", []).extend([
        "drop symbol ending in `-WT` (warrants — missed by initial -W regex)",
        "drop dotted symbols on /stable (FMP uses dashes for US class shares, so a dotted symbol indicates a foreign listing: .L, .PA, .OL, .HK, .DE, .TO, etc.)",
    ])
    art["post_filter_pass_at"] = datetime.now(timezone.utc).isoformat()

    ART.write_text(json.dumps(art, indent=2, default=str))

    if meta:
        meta.setdefault("post_filter_pass", {})
        meta["post_filter_pass"] = {
            "applied_at_utc": datetime.now(timezone.utc).isoformat(),
            "active_before": before_active,
            "delisted_before": before_delisted,
            "active_after": art["active_count"],
            "delisted_after": art["delisted_count"],
            "drops": new_drops,
            "api_calls_made": 0,
        }
        META.write_text(json.dumps(meta, indent=2, default=str))

    print("Refilter pass complete (0 API calls).")
    print(f"  active   : {before_active} → {art['active_count']}")
    print(f"  delisted : {before_delisted} → {art['delisted_count']}")
    print(f"  drops    : {new_drops}")


if __name__ == "__main__":
    main()
