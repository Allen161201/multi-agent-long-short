"""
B.1 follow-up — two more probes to lock down Strategy B narrowing.

Budget: 2 more raw FMP calls (within the ≤15 task-level cap).

(1) historical-russell-3000-constituent: if exists, Strategy B can narrow
    the 26,850-ticker universe to ~3,000 PIT-correct names per date.
    NOTE: Russell 2000 + Russell 1000 = Russell 3000, so a single check
    here covers both small and large caps.

(2) most-actives with date param: biggest-gainers ignored its date param
    in feasibility probe Capability 1. If most-actives behaves the same,
    we have a clean negative confirmation that ALL FMP "of the day"
    leaderboards are current-only. If it accepts date, we have a
    backdoor for daily candidate shortlists.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    import os
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from data_adapters import fmp_adapter as fmp  # noqa: E402

OUT_PATH = ROOT / "outputs" / "_inspector" / "probe_batch_historical_endpoints_pt2.json"


def probe(label, path, params, group):
    data, meta = fmp._api_call(path, params, group=group)
    rows = len(data) if isinstance(data, list) else None
    sample = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        sample = data[0]
    elif isinstance(data, dict):
        sample = data
    return {
        "label": label, "path": path, "params": params,
        "http_status": meta.get("http_status"), "ok": meta.get("ok"),
        "error_short": meta.get("error_short"),
        "row_count": rows,
        "sample_first_row": sample,
        "tail_first_row": data[-1] if isinstance(data, list) and data else None,
    }


probes = []

# (1) Russell 3000 historical constituents — narrowing for Strategy B
probes.append(probe(
    "historical-russell-3000-constituent",
    "historical-russell-3000-constituent",
    None,
    "probe_pt2_russell3000",
))

# (2) most-actives with date param — does this leaderboard honour date,
# unlike biggest-gainers?
probes.append(probe(
    "most-actives with date=2024-06-17",
    "most-actives",
    {"date": "2024-06-17"},
    "probe_pt2_most_actives_dated",
))

result = {
    "started_at_utc": datetime.now(timezone.utc).isoformat(),
    "probes": probes,
    "calls_by_group": fmp.get_call_group_summary(),
}
OUT_PATH.write_text(json.dumps(result, indent=2, default=str))

for p in probes:
    print(f"{p['label']}: HTTP {p['http_status']} ok={p['ok']} rows={p['row_count']}")
    if p.get("sample_first_row"):
        print(f"  sample keys: {sorted(list(p['sample_first_row'].keys()))[:15]}")
        print(f"  excerpt    : {json.dumps(p['sample_first_row'], default=str)[:300]}")
print(f"\ncalls_by_group: {fmp.get_call_group_summary()}")
print(f"sidecar: {OUT_PATH}")
