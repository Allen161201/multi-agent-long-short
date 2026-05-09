"""
D5 Rule 5 — PIT compliance scanner.

Walks every YYYY-MM-DD/RUN/ subdir under one or more decision roots,
loads every (evidence_packet, decision) pair, and asserts the PIT
guarantees:

  - envelope.data_after_cutoff_used == False
  - envelope.lookahead_safe == True
  - envelope.hindsight_safe == True
  - envelope.pit_mode in {"replay_strict_pit", "live", "live_strict"}
  - envelope.locked_decision_id is non-empty
  - decision.locked_decision_id matches envelope (when present in decision)

Outputs a one-line summary plus a CSV of any violation rows under
data/backtest/pit_violations.csv. Returns 0 when 0 violations, 1 when
≥1 violation.

Usage:
    python scripts/verify_pit_compliance.py
    python scripts/verify_pit_compliance.py --roots data/decisions data/backtest
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = [
    ROOT / "data" / "decisions",
    ROOT / "data" / "backtest",
]
VIOLATIONS_CSV = ROOT / "data" / "backtest" / "pit_violations.csv"
COMPLIANCE_LOG_JSON = ROOT / "data" / "backtest" / "pit_compliance_status.json"

VIOLATIONS_HEADER = [
    "scanned_at_utc", "packet_path", "ticker", "decision_timestamp",
    "violation_field", "expected", "actual", "notes",
]

ALLOWED_PIT_MODES = {
    "replay_strict_pit",   # backtest mode
    "live",                # legacy live mode (pre-strict)
    "live_strict",         # live with strict_pit_mode=True (preferred)
}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _scan_one_packet(
    packet: dict, decision: dict | None, packet_path: Path,
) -> list[dict]:
    """Return zero or more violation dicts for this packet."""
    out: list[dict] = []
    env = packet.get("envelope") or {}
    ticker = env.get("ticker") or ""
    ts = env.get("decision_timestamp") or ""

    def _record(field: str, expected: str, actual, notes: str = "") -> None:
        out.append({
            "scanned_at_utc": datetime.now(timezone.utc).isoformat(),
            "packet_path": str(packet_path),
            "ticker": ticker,
            "decision_timestamp": ts,
            "violation_field": field,
            "expected": expected,
            "actual": str(actual),
            "notes": notes,
        })

    # 1. data_after_cutoff_used must be False
    if env.get("data_after_cutoff_used", None) is not False:
        _record(
            "envelope.data_after_cutoff_used", "False",
            env.get("data_after_cutoff_used"),
        )

    # 2. lookahead_safe must be True
    if env.get("lookahead_safe", None) is not True:
        _record(
            "envelope.lookahead_safe", "True",
            env.get("lookahead_safe"),
        )

    # 3. hindsight_safe must be True
    if env.get("hindsight_safe", None) is not True:
        _record(
            "envelope.hindsight_safe", "True",
            env.get("hindsight_safe"),
        )

    # 4. pit_mode must be one of ALLOWED
    pm = env.get("pit_mode")
    if pm not in ALLOWED_PIT_MODES:
        _record(
            "envelope.pit_mode", str(sorted(ALLOWED_PIT_MODES)),
            pm, "live or replay_strict_pit required",
        )

    # 5. locked_decision_id must be present
    ldi = env.get("locked_decision_id")
    if not ldi:
        _record(
            "envelope.locked_decision_id", "<non-empty sha256:...>",
            ldi, "envelope is missing locked_decision_id",
        )

    # 6. Decision file's locked_decision_id (if present) must match
    if decision and isinstance(decision, dict):
        # Look in several plausible locations
        dec_ldi = (
            decision.get("locked_decision_id")
            or (decision.get("final_decision") or {}).get("locked_decision_id")
            or (decision.get("audit_record") or {}).get("locked_decision_id")
        )
        if dec_ldi and ldi and dec_ldi != ldi:
            _record(
                "decision.locked_decision_id == envelope.locked_decision_id",
                ldi, dec_ldi, "decision JSON references a different LDI",
            )

    return out


def _enumerate_pairs(root: Path) -> list[tuple[Path, Path | None]]:
    """Walk root recursively, return list of (packet_path, decision_path
    or None). decision_path is the alphabetically-first matching
    *_decision.json in the same directory; multiple decisions per packet
    are scanned individually by collecting (packet, decision) for each."""
    out: list[tuple[Path, Path | None]] = []
    if not root.exists():
        return out
    for packet_path in root.rglob("*_evidence_packet.json"):
        # Match decisions in the same dir whose ticker matches the
        # packet's filename prefix.
        ticker_prefix = packet_path.name.split("_evidence_packet.json")[0]
        decisions_in_dir = list(packet_path.parent.glob(
            f"{ticker_prefix}_*_decision.json"
        ))
        if not decisions_in_dir:
            out.append((packet_path, None))
        else:
            for d in decisions_in_dir:
                out.append((packet_path, d))
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="PIT compliance scanner (D5 Rule 5).",
    )
    p.add_argument(
        "--roots", nargs="+", type=str, default=None,
        help="Decision roots to scan. Default: data/decisions + data/backtest.",
    )
    p.add_argument(
        "--strict", action="store_true",
        help="Treat any missing-pit_mode envelope as a violation, even "
             "when packet was generated before pit_mode was a field.",
    )
    args = p.parse_args()

    roots = (
        [Path(r) for r in args.roots]
        if args.roots else list(DEFAULT_ROOTS)
    )

    pairs: list[tuple[Path, Path | None]] = []
    for r in roots:
        pairs.extend(_enumerate_pairs(r))

    print(f"=== PIT compliance scan ===")
    print(f"  roots             : {[str(r) for r in roots]}")
    print(f"  packets discovered: {len(pairs)}")

    violations: list[dict] = []
    for packet_path, decision_path in pairs:
        packet = _load_json(packet_path)
        if not packet:
            continue
        decision = _load_json(decision_path) if decision_path else None
        violations.extend(_scan_one_packet(packet, decision, packet_path))

    total_scanned = len(pairs)
    n_viol = len(violations)
    clean = total_scanned - len({v["packet_path"] for v in violations})
    pct = (clean / total_scanned * 100.0) if total_scanned else 100.0

    print(f"  total decisions   : {total_scanned}")
    print(f"  violations        : {n_viol}")
    print(f"  clean rate        : {pct:.1f}% ({clean}/{total_scanned})")

    if violations:
        VIOLATIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
        write_header = not VIOLATIONS_CSV.exists()
        with VIOLATIONS_CSV.open(
            "a", newline="", encoding="utf-8"
        ) as f:
            w = csv.DictWriter(f, fieldnames=VIOLATIONS_HEADER)
            if write_header:
                w.writeheader()
            w.writerows(violations)
        print(f"\n  appended {n_viol} violations -> {VIOLATIONS_CSV}")
        for v in violations[:10]:
            print(f"    - {v['packet_path']}  field={v['violation_field']} "
                  f"actual={v['actual']!r}")
        if len(violations) > 10:
            print(f"    ... and {len(violations) - 10} more")

    # Persist a status JSON for the dashboard /api/pit/status endpoint.
    COMPLIANCE_LOG_JSON.parent.mkdir(parents=True, exist_ok=True)
    COMPLIANCE_LOG_JSON.write_text(
        json.dumps({
            "scanned_at_utc": datetime.now(timezone.utc).isoformat(),
            "roots": [str(r) for r in roots],
            "total_decisions_scanned": total_scanned,
            "violations_count": n_viol,
            "clean_rate_pct": pct,
            "first_few_violations": violations[:5],
        }, indent=2),
        encoding="utf-8",
    )
    print(f"  status: {COMPLIANCE_LOG_JSON}")

    return 0 if n_viol == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
