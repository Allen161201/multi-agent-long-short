"""
Integration tests for the 3 backtest browser routes in src/dashboard/app.py:
  GET /api/backtest/runs
  GET /api/backtest/run/<run_id>
  GET /api/backtest/run/<run_id>/history

Direct-execution / __main__-style. Run:
    python tests/test_backtest_routes.py

Uses Flask's test_client — no port binding, no real HTTP. Real
data/backtest/ on disk is read for the happy-path cases (today's smoke
run); the empty-dir case shimmies the directory aside in a try/finally
so the test never destroys real data on failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.dashboard.app import (  # noqa: E402
    app, _DATA_BACKTEST_DIR, _RUN_ID_PATTERN,
)


PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  --  {detail}")


def _existing_run_id() -> str | None:
    """Pick the lexicographically-last existing run_id under data/backtest/.
    Returns None if no runs exist."""
    if not _DATA_BACKTEST_DIR.exists():
        return None
    candidates = sorted(
        p.name for p in _DATA_BACKTEST_DIR.iterdir()
        if p.is_dir() and _RUN_ID_PATTERN.match(p.name)
        and (p / "manifest.json").exists()
    )
    return candidates[-1] if candidates else None


def case_1_runs_listing():
    print("\nCase 1 — /api/backtest/runs returns ok with at least 1 run")
    client = app.test_client()
    r = client.get("/api/backtest/runs")
    check("1a HTTP 200", r.status_code == 200, str(r.status_code))
    j = r.get_json()
    check("1b status == ok", j.get("status") == "ok", str(j.get("status")))
    check("1c run_count >= 1", j.get("run_count", 0) >= 1, str(j))
    check("1d runs is list", isinstance(j.get("runs"), list))
    if j.get("runs"):
        first = j["runs"][0]
        for key in ("run_id", "start_date", "end_date",
                    "trading_days_processed", "trades_filled", "final_nav",
                    "created_at"):
            check(f"1e first row has {key}", key in first, str(first.keys()))


def case_2_runs_sorted_desc():
    print("\nCase 2 — runs sorted by created_at desc (newest first)")
    client = app.test_client()
    r = client.get("/api/backtest/runs")
    runs = r.get_json().get("runs") or []
    if len(runs) < 2:
        check("2a (only one run; trivially sorted)", True)
        return
    timestamps = [x.get("created_at") for x in runs]
    monotonic = all(
        timestamps[i] >= timestamps[i + 1]
        for i in range(len(timestamps) - 1)
    )
    check("2a created_at non-increasing across runs",
          monotonic, str(timestamps))


def case_3_run_detail_known():
    print("\nCase 3 — /api/backtest/run/<known_id> returns 200 + manifest")
    rid = _existing_run_id()
    check("3a precondition: an existing run exists",
          rid is not None, "no runs available")
    if rid is None:
        return
    client = app.test_client()
    r = client.get(f"/api/backtest/run/{rid}")
    check("3b HTTP 200", r.status_code == 200, str(r.status_code))
    j = r.get_json()
    check("3c status == ok", j.get("status") == "ok", str(j.get("status")))
    check("3d run_id round-trips", j.get("run_id") == rid)
    m = j.get("manifest")
    check("3e manifest is dict", isinstance(m, dict))
    if isinstance(m, dict):
        for key in ("run_id", "start_date", "end_date",
                    "trading_days_processed", "totals", "metrics", "per_day"):
            check(f"3f manifest has {key}", key in m, str(list(m.keys())))


def case_4_run_detail_404():
    print("\nCase 4 — bogus run_id rejected (HTTP 404 + status=not_found)")
    client = app.test_client()
    # Lexically valid regex but no such directory.
    bogus = "backtest_19990101_000000_1999-01-01_to_1999-01-01"
    r = client.get(f"/api/backtest/run/{bogus}")
    check("4a HTTP 404", r.status_code == 404, str(r.status_code))
    j = r.get_json()
    check("4b status == not_found", j.get("status") == "not_found",
          str(j.get("status")))
    # Regex-invalid input should also be rejected at 404 (not 500).
    invalid = "not_a_real_run_id"
    r2 = client.get(f"/api/backtest/run/{invalid}")
    check("4c regex-invalid id → HTTP 404",
          r2.status_code == 404, str(r2.status_code))
    check("4d regex-invalid id → status=not_found",
          r2.get_json().get("status") == "not_found",
          str(r2.get_json()))


def case_5_history_known():
    print("\nCase 5 — /api/backtest/run/<id>/history returns rows + metrics")
    rid = _existing_run_id()
    check("5a precondition: an existing run exists",
          rid is not None, "no runs available")
    if rid is None:
        return
    client = app.test_client()
    r = client.get(f"/api/backtest/run/{rid}/history")
    check("5b HTTP 200", r.status_code == 200, str(r.status_code))
    j = r.get_json()
    check("5c status == ok", j.get("status") == "ok", str(j.get("status")))
    check("5d row_count >= 1",
          j.get("row_count", 0) >= 1, str(j.get("row_count")))
    check("5e rows is list", isinstance(j.get("rows"), list))
    check("5f metrics is dict", isinstance(j.get("metrics"), dict))
    check("5g metrics has total_return",
          "total_return" in (j.get("metrics") or {}),
          str((j.get("metrics") or {}).keys()))
    check("5h metrics_status present",
          j.get("metrics_status") in ("ok", "insufficient_data"),
          str(j.get("metrics_status")))


def case_6_history_filter():
    print("\nCase 6 — history with from/to filter narrows rows correctly")
    rid = _existing_run_id()
    if rid is None:
        check("6a (skipped: no runs available)", True)
        return
    client = app.test_client()
    # 1) Get full history first to learn the date range.
    full = client.get(f"/api/backtest/run/{rid}/history").get_json()
    rows_full = full.get("rows") or []
    if not rows_full:
        check("6a (skipped: history empty)", True)
        return
    first_date = rows_full[0]["as_of"]
    # 2) Filter to a single day == first_date → row_count == 1
    r = client.get(f"/api/backtest/run/{rid}/history?from={first_date}&to={first_date}")
    j = r.get_json()
    check("6a HTTP 200", r.status_code == 200, str(r.status_code))
    check("6b filtered row_count == 1",
          j.get("row_count") == 1, str(j.get("row_count")))
    # 3) Filter to a future window → row_count == 0
    r2 = client.get(f"/api/backtest/run/{rid}/history?from=2099-01-01")
    check("6c future-only filter → status=ok",
          r2.get_json().get("status") == "ok",
          str(r2.get_json()))
    check("6d future-only filter → row_count == 0",
          r2.get_json().get("row_count") == 0,
          str(r2.get_json().get("row_count")))


def case_7_path_traversal_blocked():
    print("\nCase 7 — path-traversal attempts rejected")
    client = app.test_client()
    # Flask routes can't match these (they contain slashes), so they 404
    # on the routing layer itself — that's fine. Test that the regex
    # rejection also covers regex-shaped-but-suspicious inputs.
    bad_inputs = [
        "../../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "backtest_../../etc/passwd_2026-01-01_to_2026-01-02",
        "backtest_20260101_000000_2026-01-01_to_2026-01-01\x00",
        "BACKTEST_20260101_000000_2026-01-01_to_2026-01-01",  # case-mismatch
    ]
    rejected = 0
    for bad in bad_inputs:
        r = client.get(f"/api/backtest/run/{bad}")
        if r.status_code == 404:
            rejected += 1
    check("7a all path-traversal/format-bad inputs return 404",
          rejected == len(bad_inputs),
          f"{rejected}/{len(bad_inputs)} rejected")
    # Also verify the regex itself rejects ".." and "/"
    check("7b regex rejects '..'",
          not _RUN_ID_PATTERN.match(".."), "regex matched '..'")
    check("7c regex rejects path-with-slash",
          not _RUN_ID_PATTERN.match("backtest_/etc/passwd"),
          "regex matched slash")


def case_8_empty_backtest_dir():
    print("\nCase 8 — empty data/backtest/ → /api/backtest/runs returns no_data")
    # Rename the directory aside, hit the route, then restore — try/finally
    # so a crash doesn't strand real data.
    if _DATA_BACKTEST_DIR.exists():
        shifted = _DATA_BACKTEST_DIR.with_name(
            _DATA_BACKTEST_DIR.name + "__test_shift"
        )
        try:
            _DATA_BACKTEST_DIR.rename(shifted)
            client = app.test_client()
            r = client.get("/api/backtest/runs")
            check("8a HTTP 200 even with no dir",
                  r.status_code == 200, str(r.status_code))
            j = r.get_json()
            check("8b status == no_data",
                  j.get("status") == "no_data", str(j.get("status")))
            check("8c run_count == 0",
                  j.get("run_count") == 0, str(j.get("run_count")))
            check("8d runs == []",
                  j.get("runs") == [], str(j.get("runs")))
            # Detail route on a regex-valid id should still 404 cleanly.
            r2 = client.get("/api/backtest/run/backtest_20260101_000000_2026-01-01_to_2026-01-01")
            check("8e detail route 404 when no dir",
                  r2.status_code == 404, str(r2.status_code))
        finally:
            if shifted.exists():
                shifted.rename(_DATA_BACKTEST_DIR)
    else:
        # If the dir doesn't exist at all, the route should still return
        # no_data without crashing.
        client = app.test_client()
        j = client.get("/api/backtest/runs").get_json()
        check("8b status == no_data (dir absent)",
              j.get("status") == "no_data", str(j))


def main() -> int:
    print("=" * 70)
    print("test_backtest_routes.py")
    print("=" * 70)
    case_1_runs_listing()
    case_2_runs_sorted_desc()
    case_3_run_detail_known()
    case_4_run_detail_404()
    case_5_history_known()
    case_6_history_filter()
    case_7_path_traversal_blocked()
    case_8_empty_backtest_dir()
    print()
    print("=" * 70)
    print(f"PASS: {len(PASS)}    FAIL: {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("All cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
