"""
One-shot endpoint probe (≤ 4 calls).

Inspects the FIELD SHAPE of FMP /stable list endpoints used to build the
master universe. Does not pull full data, does not filter, does not write
the artifact — that's `build_universe_v1.py`'s job.

Goal: confirm which fields are available and whether endpoints paginate,
so the universe builder can be written with correct assumptions.
"""
from __future__ import annotations

import json
import sys
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


def head(label, data, meta, n=2):
    print(f"\n── {label} ──")
    print(f"  HTTP {meta.get('http_status')} ok={meta.get('ok')} "
          f"err={meta.get('error_short')}")
    if isinstance(data, list):
        print(f"  rows={len(data)}")
        if data:
            row = data[0]
            if isinstance(row, dict):
                print(f"  keys={sorted(row.keys())}")
                print(f"  sample[0]={json.dumps(row, default=str)[:300]}")
                if len(data) > 1:
                    print(f"  sample[-1]={json.dumps(data[-1], default=str)[:300]}")
    elif isinstance(data, dict):
        print(f"  dict keys={sorted(data.keys())}")
        print(f"  sample={json.dumps(data, default=str)[:300]}")
    else:
        print(f"  body={str(data)[:300]}")


# 1) stock-list (active US)
d, m = fmp._api_call("stock-list", group="probe_list_active")
head("stock-list", d, m)

# 2) delisted-companies, default page
d, m = fmp._api_call("delisted-companies", group="probe_list_delisted")
head("delisted-companies (no params)", d, m)

# 3) delisted-companies, page=1 (probe pagination)
d, m = fmp._api_call("delisted-companies", {"page": 1},
                      group="probe_list_delisted_p1")
head("delisted-companies page=1", d, m)

# 4) available-traded-list (a possible richer alternative)
d, m = fmp._api_call("available-traded-list",
                      group="probe_list_available_traded")
head("available-traded-list", d, m)

print("\n── call totals ──")
print(json.dumps(fmp.get_call_group_summary(), indent=2))
