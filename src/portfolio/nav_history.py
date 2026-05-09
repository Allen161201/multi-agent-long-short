"""
NAV history appender (D4 step 1 cont. — time-series substrate).

After each EOD writer fires, a single row is appended to
data/portfolio/pnl_history.csv. This is the input that downstream
Sharpe/Sortino/maxDD/return-curve modules read; it is intentionally
plain-text CSV with stdlib parsing (no pandas dependency).

Schema (one row per EOD state):

    as_of, total_nav, cash_balance, positions_value, num_positions,
    daily_return, cumulative_return, rule_version

Semantics:
  - First row : daily_return is empty (CSV cell ""), cumulative_return = 0.0.
                The base NAV used for cumulative_return is this row's NAV.
  - Subsequent rows : daily_return    = (nav[t] - nav[t-1]) / nav[t-1]
                      cumulative_return = (nav[t] - nav[0])  / nav[0]
  - Replay (same as_of as latest)  : REPLACES the existing row in place
                                     (idempotent re-runs of EOD on same day).
  - Out-of-order (earlier as_of)   : raises ValueError.

Failure surfaces:
  - Missing eod_state file        → FileNotFoundError
  - Malformed eod_state JSON       → ValueError with diagnostic
  - CSV write fails               → OSError propagates
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

CSV_HEADER = [
    "as_of",
    "total_nav",
    "cash_balance",
    "positions_value",
    "num_positions",
    "daily_return",
    "cumulative_return",
    "rule_version",
]

_DATE_FMT = "%Y-%m-%d"


# ── public API ────────────────────────────────────────────────────

def append_nav_row(eod_state_path: Path, history_csv_path: Path) -> dict:
    """Append (or replace-by-date) one row to pnl_history.csv from the
    given EOD state JSON. Returns the appended row as a dict."""
    eod_state_path = Path(eod_state_path)
    history_csv_path = Path(history_csv_path)

    if not eod_state_path.exists():
        raise FileNotFoundError(f"eod_state file not found: {eod_state_path}")

    try:
        with eod_state_path.open("r", encoding="utf-8") as f:
            eod = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"eod_state JSON malformed at {eod_state_path}: {e}"
        ) from e

    # Validate required fields up-front so the error is precise.
    for k in ("as_of", "total_nav", "cash_balance", "positions",
              "rule_version"):
        if k not in eod:
            raise ValueError(
                f"eod_state at {eod_state_path} missing required key {k!r}; "
                f"keys present: {sorted(eod)}"
            )

    as_of_str = str(eod["as_of"])
    try:
        as_of_dt = datetime.strptime(as_of_str, _DATE_FMT).date()
    except ValueError as e:
        raise ValueError(
            f"eod_state.as_of must be YYYY-MM-DD, got {as_of_str!r}: {e}"
        ) from e

    total_nav = float(eod["total_nav"])
    cash_balance = float(eod["cash_balance"])
    positions_value = total_nav - cash_balance
    num_positions = len(eod.get("positions") or [])
    rule_version = str(eod.get("rule_version") or "")

    # Load existing rows.
    existing = load_nav_history(history_csv_path)

    # Resolve replay vs. append vs. out-of-order.
    matching_idx: int | None = None
    base_nav: float | None = None
    prior_nav: float | None = None

    if existing:
        last_date = datetime.strptime(existing[-1]["as_of"], _DATE_FMT).date()
        if as_of_dt < last_date:
            # Could still be replay against a non-tail row, BUT the spec
            # forbids that — only replay-of-latest is allowed.
            for i, row in enumerate(existing):
                if row["as_of"] == as_of_str:
                    if i != len(existing) - 1:
                        raise ValueError(
                            f"nav_history append: as_of={as_of_str} matches "
                            f"row {i} of {len(existing)} (not the tail). "
                            f"In-place replacement only allowed on the most "
                            f"recent row; appending earlier dates would "
                            f"break the chronological invariant."
                        )
                    matching_idx = i
                    break
            else:
                raise ValueError(
                    f"nav_history must be appended chronologically; "
                    f"got as_of={as_of_str} but latest row is {last_date}"
                )
        elif as_of_dt == last_date:
            matching_idx = len(existing) - 1

        base_nav = float(existing[0]["total_nav"])
        # prior_nav is the row immediately before the one being written.
        if matching_idx is not None and matching_idx > 0:
            prior_nav = float(existing[matching_idx - 1]["total_nav"])
        elif matching_idx is None:
            prior_nav = float(existing[-1]["total_nav"])
        # else matching_idx == 0 (replay first row) → prior_nav stays None

    # Compute daily / cumulative returns.
    if prior_nav is None:
        daily_return: float | None = None
    else:
        if prior_nav == 0:
            daily_return = 0.0
        else:
            daily_return = (total_nav - prior_nav) / prior_nav

    if base_nav is None or matching_idx == 0:
        # First-ever row OR replaying the very first row → base IS this row.
        cumulative_return = 0.0
    else:
        if base_nav == 0:
            cumulative_return = 0.0
        else:
            cumulative_return = (total_nav - base_nav) / base_nav

    new_row: dict = {
        "as_of": as_of_str,
        "total_nav": total_nav,
        "cash_balance": cash_balance,
        "positions_value": positions_value,
        "num_positions": num_positions,
        "daily_return": daily_return,
        "cumulative_return": cumulative_return,
        "rule_version": rule_version,
    }

    # Materialize the full row set, then rewrite the CSV.
    if matching_idx is not None:
        existing[matching_idx] = new_row
        rows_to_write = existing
    else:
        rows_to_write = existing + [new_row]

    _write_csv(history_csv_path, rows_to_write)
    return new_row


def load_nav_history(history_csv_path: Path) -> list[dict]:
    """Parse pnl_history.csv → list of dicts sorted by as_of asc.
    Returns [] if file missing. Numeric columns coerced; daily_return
    is None when the cell is empty."""
    history_csv_path = Path(history_csv_path)
    if not history_csv_path.exists():
        return []
    rows: list[dict] = []
    with history_csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows.append(_coerce_row(raw))
    rows.sort(key=lambda r: r["as_of"])
    return rows


def get_nav_at(history_csv_path: Path, as_of: str) -> float | None:
    """Look up total_nav for a specific as_of (YYYY-MM-DD). Returns
    None if not found (and None if the file doesn't exist)."""
    for row in load_nav_history(history_csv_path):
        if row["as_of"] == as_of:
            return float(row["total_nav"])
    return None


# ── internals ─────────────────────────────────────────────────────

def _coerce_row(raw: dict) -> dict:
    """Coerce a raw csv.DictReader row (all str) to typed values."""
    daily_raw = raw.get("daily_return", "")
    daily = None if daily_raw == "" else float(daily_raw)
    return {
        "as_of": raw["as_of"],
        "total_nav": float(raw["total_nav"]),
        "cash_balance": float(raw["cash_balance"]),
        "positions_value": float(raw["positions_value"]),
        "num_positions": int(raw["num_positions"]),
        "daily_return": daily,
        "cumulative_return": float(raw["cumulative_return"]),
        "rule_version": raw.get("rule_version", ""),
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for r in rows:
            out = dict(r)
            # daily_return = None → empty cell (not "None" / "null").
            if out.get("daily_return") is None:
                out["daily_return"] = ""
            writer.writerow(out)


__all__ = [
    "CSV_HEADER",
    "append_nav_row",
    "load_nav_history",
    "get_nav_at",
]
