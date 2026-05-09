import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
d = json.loads((ROOT / "data" / "altdata" / "_d4_availability_diagnostic.json").read_text(encoding="utf-8"))
agg = d["aggregate_by_source"]
print("=== Aggregate by source ===")
for s, a in agg.items():
    print(f"  {s:30s} live={a['days_with_data']:3d} empty={a['days_empty']:3d} "
          f"failed={a['days_failed']:3d} items={a['total_items']}")
print()
print("=== news_event_summary first_probe ===")
fp = agg.get("01_news_event_summary", {}).get("first_probe")
print(json.dumps(fp, indent=2))
print()
print("=== Failure modes ===")
for s, a in agg.items():
    if a.get("failure_modes"):
        print(f"  {s}:")
        for m in a["failure_modes"]:
            print(f"    {m[:240]}")
