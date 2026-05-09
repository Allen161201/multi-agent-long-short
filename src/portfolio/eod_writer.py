"""
EOD state writer (D4 step 1 — orchestrates position_book persistence).

Pairs decision JSON files with their evidence packets, applies each
decision to the position book, marks-to-market via the injected
price_fetcher, and writes data/portfolio/<date>_eod_state.json.

This module owns all I/O. The position_book stays pure-Python with no
file or HTTP side-effects.

NO IMPORTS of sizing.py, reinvestment.py, cost_model.py, fill_model.py
(all DO-NOT-WIRE per their headers).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .position_book import (
    DEFAULT_STARTING_CASH, PositionBook, PositionBookEvent,
)
from .nav_history import append_nav_row

RULE_VERSION_DEFAULT = "v0.7_credit_spread_sidesignal"
NAV_HISTORY_FILENAME = "pnl_history.csv"


def _decision_pair_paths(decision_file: Path) -> Path:
    """Given …/<date>/<cutoff>/<TICKER>_<candidate>_decision.json,
    return the matching <TICKER>_evidence_packet.json in the same dir."""
    name = decision_file.name
    if "_decision.json" not in name:
        raise ValueError(
            f"decision file name must end with _decision.json: {name!r}"
        )
    ticker = name.split("_", 1)[0]
    return decision_file.parent / f"{ticker}_evidence_packet.json"


def _entry_price_from_packet(packet: dict, ticker: str) -> float:
    """Extract the PIT-safe entry-price anchor from an evidence packet's
    price_snapshot block. Prefers `last_eod_close`; falls back to
    `last_price` if missing (and surfaces a warning via raise on
    truly empty)."""
    ps = packet.get("price_snapshot")
    if not isinstance(ps, dict):
        raise ValueError(
            f"evidence packet for {ticker} has no price_snapshot block"
        )
    eod = ps.get("last_eod_close")
    if isinstance(eod, (int, float)) and eod > 0:
        return float(eod)
    last = ps.get("last_price")
    if isinstance(last, (int, float)) and last > 0:
        return float(last)
    raise ValueError(
        f"evidence packet for {ticker} has no usable price anchor "
        f"(last_eod_close={eod!r}, last_price={last!r})"
    )


def _load_decision_dict(decision_file: Path) -> dict:
    with decision_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_evidence_packet(packet_file: Path) -> dict:
    with packet_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def _prior_eod_path(output_dir: Path, today: date) -> Path | None:
    """Find the most recent <YYYY-MM-DD>_eod_state.json older than
    today, if any."""
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return None
    candidates: list[tuple[date, Path]] = []
    for p in output_dir.glob("*_eod_state.json"):
        stem = p.stem.replace("_eod_state", "")
        try:
            d = datetime.strptime(stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < today:
            candidates.append((d, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def write_eod_state(
    *,
    decision_files: list[Path],
    price_fetcher: Callable[[str], float],
    output_dir: Path,
    as_of_date: date | None = None,
    starting_cash: float = DEFAULT_STARTING_CASH,
    rule_version: str = RULE_VERSION_DEFAULT,
) -> Path:
    """Materialize <YYYY-MM-DD>_eod_state.json for `as_of_date`.

    Workflow:
      1. Load existing book from `output_dir/<prev_date>_eod_state.json`
         if any; else start with `starting_cash`.
      2. Apply each decision in order. Each decision file must have a
         sibling `<TICKER>_evidence_packet.json` for entry-price anchor.
      3. Mark-to-market via `price_fetcher`.
      4. Write today's EOD state.

    Returns the written path. Raises (does not silently fall back) on:
      - missing evidence packet for any decision file
      - missing price_snapshot.last_eod_close + last_price
      - price_fetcher returning non-positive / raising
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    today = as_of_date or date.today()

    # ── 1. Load prior or start fresh ──────────────────────────────
    prior_path = _prior_eod_path(output_dir, today)
    if prior_path is not None:
        book = PositionBook.load_from_file(prior_path)
    else:
        book = PositionBook(cash_balance=starting_cash)

    # ── 2. Apply decisions ────────────────────────────────────────
    decisions_processed: list[str] = []
    events: list[PositionBookEvent] = []
    for dec_file in decision_files:
        dec_file = Path(dec_file)
        decision_dict = _load_decision_dict(dec_file)
        ticker = decision_dict.get("ticker") or "<no_ticker>"
        packet_file = _decision_pair_paths(dec_file)
        if not packet_file.exists():
            raise FileNotFoundError(
                f"evidence packet missing for decision {dec_file}: "
                f"expected {packet_file}"
            )
        packet = _load_evidence_packet(packet_file)
        entry_price = _entry_price_from_packet(packet, ticker)
        ep_hash = (
            decision_dict.get("evidence_packet_hash")
            or packet.get("envelope", {}).get("evidence_packet_hash")
            or "<no_hash>"
        )
        emitted = book.apply_decision(
            decision_dict=decision_dict,
            entry_price=entry_price,
            evidence_packet_hash=ep_hash,
        )
        events.extend(emitted)
        dec_id = (
            (decision_dict.get("final_decision") or {}).get("locked_decision_id")
            or "<no_decision_id>"
        )
        decisions_processed.append(dec_id)

    # ── 3. Mark to market ─────────────────────────────────────────
    book.mark_to_market(price_fetcher, as_of_iso=today.isoformat())
    book.last_eod_timestamp = today.isoformat()

    # ── 4. Compose & write ────────────────────────────────────────
    sleeves = ("quality_long", "surge_short", "fixed_income")
    payload = {
        "schema_version": "eod_state_v1",
        "as_of": today.isoformat(),
        "rule_version": rule_version,
        "cash_balance": book.cash_balance,
        "total_nav": book.get_total_nav(),
        "positions": [p.to_dict() for p in book.positions],
        "sleeve_exposure": {
            s: book.get_sleeve_exposure(s) for s in sleeves
        },
        "concentration": book.get_position_concentration(),
        "audit": {
            "decisions_processed": decisions_processed,
            "events": [e.to_dict() for e in events],
            "prior_state_loaded_from": str(prior_path) if prior_path else None,
        },
    }

    out_path = output_dir / f"{today.isoformat()}_eod_state.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    # NAV-history append. Wrapped: a CSV failure must NOT prevent the
    # EOD JSON from being persisted, since the JSON is the source of
    # truth and the CSV is a derived view that can be rebuilt from JSONs.
    history_path = output_dir / NAV_HISTORY_FILENAME
    try:
        append_nav_row(out_path, history_path)
    except Exception as e:  # surface, don't swallow
        import sys as _sys
        print(
            f"[eod_writer] WARN: append_nav_row failed for {out_path}: "
            f"{type(e).__name__}: {e}",
            file=_sys.stderr,
        )

    return out_path


def make_packet_anchor_price_fetcher(
    packet_lookup: dict[str, dict],
) -> Callable[[str], float]:
    """Build a price_fetcher that returns last_eod_close from a pre-
    loaded {ticker → evidence_packet} mapping. Useful for smoke tests
    where you want MTM at the same price as entry (zero PnL baseline)
    without hitting FMP."""
    def _fetch(ticker: str) -> float:
        pkt = packet_lookup.get(ticker)
        if pkt is None:
            raise KeyError(f"no evidence packet for {ticker}")
        return _entry_price_from_packet(pkt, ticker)
    return _fetch


__all__ = [
    "RULE_VERSION_DEFAULT",
    "write_eod_state",
    "make_packet_anchor_price_fetcher",
]
