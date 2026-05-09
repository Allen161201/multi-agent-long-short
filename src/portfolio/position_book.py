"""
Position book (D4 step 1 — data layer only).

Pure data layer for tracking open positions across pipeline runs. No I/O
inside the core classes — file persistence lives at the bottom and is
explicitly opt-in. No imports of `sizing.py`, `reinvestment.py`,
`cost_model.py`, `fill_model.py` (all DO-NOT-WIRE per their headers).

Scope (this task):
  - Open / add / partial-close / full-close bookkeeping driven by PM
    execution_plan (side + size_pct).
  - Mark-to-market via injected `price_fetcher` callable (no HTTP here).
  - JSON round-trip serialization for EOD writeback.

Out of scope (deferred to follow-up tasks):
  - Realized P&L accrual on close (events emit a close marker, but the
    book does not yet maintain a realized-P&L ledger).
  - Sleeve-cap / per-position-cap enforcement (PM is the authority; book
    records what it's told). Helpers `get_sleeve_exposure` and
    `get_position_concentration` expose the data for downstream checks.
  - Cash-flow accounting (cash adjusts only by trade notional in this
    cut; borrow / commission / dividends are deferred).

Decision dispatch rule (drawn from PM's execution_plan):
  side="long"   + size_pct > 0  → open or extend long
  side="short"  + size_pct > 0  → open or extend short
  side="sell"   + size_pct > 0  → reduce long by size_pct (delta)
  side="cover"  + size_pct > 0  → reduce short by size_pct (delta)
  side="none"   OR  size_pct == 0 → no-op
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

DEFAULT_STARTING_CASH = 1_000_000.0

Side = Literal["long", "short"]
Sleeve = Literal["quality_long", "surge_short", "fixed_income"]
PositionStatus = Literal["open", "closed_full", "closed_partial", "covered_partial"]
EventKind = Literal[
    "open", "add", "reduce_partial", "close_full", "no_op",
]


@dataclass
class Position:
    ticker: str
    side: Side
    size_pct: float
    size_shares: float | None
    entry_price: float
    entry_timestamp: str               # ISO-8601 with tz
    entry_decision_id: str
    candidate_type: str                # "quality_long" | "surge_short"
    evidence_packet_hash: str
    current_price: float
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    cost_basis: float
    last_marked_at: str | None
    status: PositionStatus
    sleeve: Sleeve

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(**d)


@dataclass
class PositionBookEvent:
    """Audit record of a single book mutation. Returned by apply_decision
    so the EOD writer can persist a per-day event list."""
    timestamp: str
    kind: EventKind
    ticker: str
    side: Side | None
    size_pct_delta: float
    decision_id: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _sleeve_for(candidate_type: str, side: Side) -> Sleeve:
    """Map candidate_type + side → sleeve. quality_long always lands in
    quality_long; surge_short always lands in surge_short. Fixed-income
    is reserved (BIL codification deferred)."""
    if candidate_type == "quality_long":
        return "quality_long"
    if candidate_type == "surge_short":
        return "surge_short"
    # Fall back to side-derived sleeve so unknown candidate types still book.
    return "quality_long" if side == "long" else "surge_short"


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PositionBook:
    def __init__(
        self,
        positions: list[Position] | None = None,
        cash_balance: float = DEFAULT_STARTING_CASH,
        last_eod_timestamp: str | None = None,
    ) -> None:
        self.positions: list[Position] = list(positions) if positions else []
        self.cash_balance: float = float(cash_balance)
        self.last_eod_timestamp: str | None = last_eod_timestamp

    # ── persistence ───────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "schema_version": "position_book_v1",
            "cash_balance": self.cash_balance,
            "last_eod_timestamp": self.last_eod_timestamp,
            "positions": [p.to_dict() for p in self.positions],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PositionBook":
        positions = [Position.from_dict(p) for p in d.get("positions", [])]
        return cls(
            positions=positions,
            cash_balance=float(d.get("cash_balance", DEFAULT_STARTING_CASH)),
            last_eod_timestamp=d.get("last_eod_timestamp"),
        )

    @classmethod
    def load_from_file(cls, path: Path) -> "PositionBook":
        with Path(path).open("r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def save_to_file(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2,
                      default=str)

    # ── decision dispatch ─────────────────────────────────────────
    def apply_decision(
        self,
        *,
        decision_dict: dict,
        entry_price: float,
        evidence_packet_hash: str,
        as_of_nav: float | None = None,
    ) -> list[PositionBookEvent]:
        """Apply one PM decision to the book. Returns audit events.

        `decision_dict` is the full pipeline-output dict (top-level
        keys include `final_decision` + `evidence_packet_hash` etc. —
        what `run_all_agents_for_candidate` emits). `entry_price` is
        resolved by the caller from the paired evidence packet
        (price_snapshot.last_eod_close).

        `as_of_nav` is the portfolio NAV used to convert size_pct → shares.
        If None, NAV is computed on-the-fly from current cash + marked
        positions (see `get_total_nav`).
        """
        fd = decision_dict.get("final_decision") or {}
        ticker = fd.get("ticker") or decision_dict.get("ticker")
        candidate_type = fd.get("candidate_type") or decision_dict.get("candidate_type")
        decision_id = fd.get("locked_decision_id") or "<no_decision_id>"
        plan = fd.get("execution_plan") or {}
        side = (plan.get("side") or "none").lower()
        size_pct = float(plan.get("size_pct_of_portfolio") or 0.0)
        execution_ts = plan.get("execution_timestamp") or fd.get("decision_timestamp") \
                        or decision_dict.get("decision_timestamp") or _now_iso_utc()

        # No-op cases.
        if side == "none" or size_pct <= 0.0:
            return [PositionBookEvent(
                timestamp=execution_ts, kind="no_op", ticker=ticker or "",
                side=None, size_pct_delta=0.0, decision_id=decision_id,
                note=f"side={side!r} size_pct={size_pct}",
            )]

        if not ticker:
            raise ValueError("apply_decision: decision lacks ticker")
        if entry_price <= 0:
            raise ValueError(
                f"apply_decision: entry_price must be > 0; got {entry_price!r}"
            )

        # ── open / add long ───────────────────────────────────────
        if side == "long":
            return self._open_or_add(
                ticker=ticker, side="long",
                candidate_type=candidate_type or "quality_long",
                size_pct=size_pct, entry_price=entry_price,
                entry_ts=execution_ts, decision_id=decision_id,
                evidence_packet_hash=evidence_packet_hash,
                as_of_nav=as_of_nav,
            )

        # ── open / add short ──────────────────────────────────────
        if side == "short":
            return self._open_or_add(
                ticker=ticker, side="short",
                candidate_type=candidate_type or "surge_short",
                size_pct=size_pct, entry_price=entry_price,
                entry_ts=execution_ts, decision_id=decision_id,
                evidence_packet_hash=evidence_packet_hash,
                as_of_nav=as_of_nav,
            )

        # ── reduce long (sell) ────────────────────────────────────
        if side == "sell":
            return self._reduce_or_close(
                ticker=ticker, side="long",
                size_pct_delta=size_pct,
                event_ts=execution_ts, decision_id=decision_id,
            )

        # ── reduce short (cover) ──────────────────────────────────
        if side == "cover":
            return self._reduce_or_close(
                ticker=ticker, side="short",
                size_pct_delta=size_pct,
                event_ts=execution_ts, decision_id=decision_id,
            )

        # Unknown side — surface, don't silently no-op.
        raise ValueError(
            f"apply_decision: unknown execution_plan.side={side!r} "
            f"(expected long | short | sell | cover | none)"
        )

    def _open_or_add(
        self, *, ticker: str, side: Side, candidate_type: str,
        size_pct: float, entry_price: float, entry_ts: str,
        decision_id: str, evidence_packet_hash: str,
        as_of_nav: float | None,
    ) -> list[PositionBookEvent]:
        nav = as_of_nav if as_of_nav is not None else self.get_total_nav()
        # If nav is zero / negative (degenerate), fall back to cash.
        sizing_nav = nav if nav > 0 else self.cash_balance

        existing = self._find_open(ticker, side)
        if existing is None:
            # Open new position.
            shares = (size_pct / 100.0) * sizing_nav / entry_price
            pos = Position(
                ticker=ticker, side=side,
                size_pct=size_pct, size_shares=shares,
                entry_price=entry_price, entry_timestamp=entry_ts,
                entry_decision_id=decision_id, candidate_type=candidate_type,
                evidence_packet_hash=evidence_packet_hash,
                current_price=entry_price,
                current_value=shares * entry_price,
                unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
                cost_basis=shares * entry_price,
                last_marked_at=entry_ts, status="open",
                sleeve=_sleeve_for(candidate_type, side),
            )
            self.positions.append(pos)
            # Cash adjustment: long buys reduce cash; short sells add cash.
            notional = shares * entry_price
            self.cash_balance += -notional if side == "long" else notional
            return [PositionBookEvent(
                timestamp=entry_ts, kind="open", ticker=ticker, side=side,
                size_pct_delta=size_pct, decision_id=decision_id,
                note=f"opened {side} {ticker} size_pct={size_pct} "
                     f"shares={shares:.4f} @ {entry_price}",
            )]

        # Add to existing — increase size_pct by delta. Cost basis is
        # weighted average of prior + new lot.
        added_shares = (size_pct / 100.0) * sizing_nav / entry_price
        new_shares = (existing.size_shares or 0.0) + added_shares
        new_cost = existing.cost_basis + added_shares * entry_price
        existing.size_shares = new_shares
        existing.size_pct += size_pct
        existing.cost_basis = new_cost
        existing.entry_price = new_cost / new_shares if new_shares else entry_price
        # MTM is refreshed by mark_to_market(); keep current_price unchanged here.
        existing.current_value = new_shares * existing.current_price
        existing.unrealized_pnl, existing.unrealized_pnl_pct = \
            _compute_pnl(existing)
        notional = added_shares * entry_price
        self.cash_balance += -notional if side == "long" else notional
        return [PositionBookEvent(
            timestamp=entry_ts, kind="add", ticker=ticker, side=side,
            size_pct_delta=size_pct, decision_id=decision_id,
            note=f"added {side} {ticker} +{size_pct}% "
                 f"(now {existing.size_pct}%, shares {new_shares:.4f})",
        )]

    def _reduce_or_close(
        self, *, ticker: str, side: Side, size_pct_delta: float,
        event_ts: str, decision_id: str,
    ) -> list[PositionBookEvent]:
        existing = self._find_open(ticker, side)
        if existing is None:
            # Reducing a position that doesn't exist — surface as no_op
            # with note. Don't raise: PM may have intended a reduce that
            # races against an earlier full close. Audit shows it.
            return [PositionBookEvent(
                timestamp=event_ts, kind="no_op", ticker=ticker, side=side,
                size_pct_delta=0.0, decision_id=decision_id,
                note=f"reduce ignored — no open {side} position for {ticker}",
            )]

        # Compute delta in shares from delta in size_pct, anchored on
        # the position's CURRENT size_pct ↔ size_shares ratio (so the
        # delta semantics stay self-consistent even if NAV moved).
        if existing.size_pct <= 0:
            shares_per_pct = 0.0
        else:
            shares_per_pct = (existing.size_shares or 0.0) / existing.size_pct
        shares_to_remove = size_pct_delta * shares_per_pct

        # Clamp at full close.
        if size_pct_delta >= existing.size_pct - 1e-9:
            shares_to_remove = existing.size_shares or 0.0
            return self._close_full(
                position=existing, shares_removed=shares_to_remove,
                event_ts=event_ts, decision_id=decision_id,
            )

        # Partial reduce.
        new_shares = (existing.size_shares or 0.0) - shares_to_remove
        # Cash on partial reduce: long sell returns notional (current
        # price); short cover spends notional.
        proceeds = shares_to_remove * existing.current_price
        self.cash_balance += proceeds if side == "long" else -proceeds
        # Cost basis reduces proportionally (keeps avg-cost intact).
        cost_removed = (
            existing.cost_basis * (shares_to_remove / (existing.size_shares or 1.0))
        )
        existing.cost_basis -= cost_removed
        existing.size_shares = new_shares
        existing.size_pct -= size_pct_delta
        existing.current_value = new_shares * existing.current_price
        existing.unrealized_pnl, existing.unrealized_pnl_pct = \
            _compute_pnl(existing)
        existing.status = "closed_partial" if side == "long" else "covered_partial"
        return [PositionBookEvent(
            timestamp=event_ts, kind="reduce_partial", ticker=ticker, side=side,
            size_pct_delta=-size_pct_delta, decision_id=decision_id,
            note=f"reduced {side} {ticker} by {size_pct_delta}% "
                 f"(remaining {existing.size_pct}%, shares {new_shares:.4f})",
        )]

    def _close_full(
        self, *, position: Position, shares_removed: float,
        event_ts: str, decision_id: str,
    ) -> list[PositionBookEvent]:
        # Cash adjustment at current_price.
        proceeds = shares_removed * position.current_price
        self.cash_balance += (
            proceeds if position.side == "long" else -proceeds
        )
        # Realized P&L tracking is DEFERRED — record the closure event
        # and remove the position from the open list. A follow-up task
        # will append realized rows to a separate ledger.
        delta_pct = position.size_pct
        self.positions = [p for p in self.positions if p is not position]
        return [PositionBookEvent(
            timestamp=event_ts, kind="close_full", ticker=position.ticker,
            side=position.side, size_pct_delta=-delta_pct,
            decision_id=decision_id,
            note=f"closed full {position.side} {position.ticker} "
                 f"({shares_removed:.4f} sh @ {position.current_price}) — "
                 f"realized P&L tracking deferred",
        )]

    # ── mark-to-market ────────────────────────────────────────────
    def mark_to_market(
        self, price_fetcher: Callable[[str], float],
        as_of_iso: str | None = None,
    ) -> None:
        """Refresh current_price for every open position via the
        injected price_fetcher. No HTTP inside this layer."""
        ts = as_of_iso or _now_iso_utc()
        for p in self.positions:
            try:
                price = float(price_fetcher(p.ticker))
            except Exception as e:
                # Surface, don't silently swallow. Mark with last known
                # current_price; downstream code can flag stale data.
                raise RuntimeError(
                    f"price_fetcher({p.ticker!r}) raised {type(e).__name__}: {e}"
                ) from e
            if price <= 0:
                raise ValueError(
                    f"price_fetcher({p.ticker!r}) returned non-positive {price!r}"
                )
            p.current_price = price
            p.current_value = (p.size_shares or 0.0) * price
            p.unrealized_pnl, p.unrealized_pnl_pct = _compute_pnl(p)
            p.last_marked_at = ts

    # ── queries ───────────────────────────────────────────────────
    def get_positions(
        self, *, side: Side | None = None,
        sleeve: Sleeve | None = None,
        status: PositionStatus | None = None,
    ) -> list[Position]:
        out = list(self.positions)
        if side is not None:
            out = [p for p in out if p.side == side]
        if sleeve is not None:
            out = [p for p in out if p.sleeve == sleeve]
        if status is not None:
            out = [p for p in out if p.status == status]
        return out

    def get_total_nav(self) -> float:
        """NAV = cash + sum(current_value × side_sign).

        Long current_value contributes positively; short current_value
        contributes the inverse of price movement (the cash leg already
        captured the proceeds at entry, so the short's mark-to-market
        adjusts NAV by entry_value − current_value)."""
        nav = self.cash_balance
        for p in self.positions:
            if p.side == "long":
                nav += p.current_value
            else:
                # Short: NAV credit at entry was cost_basis; current
                # liability is current_value. Net contribution =
                # cost_basis − current_value (positive when price falls).
                nav += p.cost_basis - p.current_value
        return nav

    def get_sleeve_exposure(self, sleeve: Sleeve) -> float:
        return sum(
            (p.current_value or 0.0)
            for p in self.positions if p.sleeve == sleeve
        )

    def get_position_concentration(self) -> dict[str, float]:
        nav = self.get_total_nav()
        if nav <= 0:
            return {}
        out: dict[str, float] = {}
        for p in self.positions:
            out[p.ticker] = out.get(p.ticker, 0.0) + (
                (p.current_value / nav) * 100.0
            )
        return out

    # ── internals ─────────────────────────────────────────────────
    def _find_open(self, ticker: str, side: Side) -> Position | None:
        for p in self.positions:
            if p.ticker == ticker and p.side == side and p.status != "closed_full":
                return p
        return None


def _compute_pnl(p: Position) -> tuple[float, float]:
    """Unrealized P&L and pct.

    Long  : pnl = current_value − cost_basis
    Short : pnl = cost_basis − current_value   (price↓ ⇒ profit)
    pct uses cost_basis as the denominator. Returns (0.0, 0.0) if
    cost_basis is zero (degenerate)."""
    if p.cost_basis <= 0:
        return 0.0, 0.0
    if p.side == "long":
        pnl = p.current_value - p.cost_basis
    else:
        pnl = p.cost_basis - p.current_value
    return pnl, (pnl / p.cost_basis) * 100.0


__all__ = [
    "DEFAULT_STARTING_CASH",
    "Position",
    "PositionBookEvent",
    "PositionBook",
    "Side",
    "Sleeve",
    "PositionStatus",
    "EventKind",
]
