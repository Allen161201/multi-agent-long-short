"""
Direct US Treasury position infrastructure (RULES.md §27.10–§27.18).

Supersedes the v0.5–v0.8.6 ETF FI sleeve (BIL/SHY/IEF/TLT). Direct UST
removes three modelling gaps the ETF universe carried:

1. Yield-curve mark-to-market risk on duration ETFs (TLT moves ~10×
   harder than UST yield change implies; ETF NAV ≠ underlying coupon).
2. Dividend accounting (BIL pays monthly; the v0.8.6 replay never
   accrued these — quality_long ETF positions also lost dividend cash).
3. Expense ratio drag (BIL = 0.1357% / yr; not modelled).

Direct UST + hold-to-maturity (HTM) accounting:
- PV computed at purchase from the FRED `treasury_<tenor>` curve
- Daily interest accrual = FV × y / 365 added to cash (no daily MTM)
- Early sale (CRISIS-regime carve-out per §27.13) = MTM at current
  FRED yield for remaining days-to-maturity
- Face value denomination = USD 1,000 per UST instrument (§27.11)

Tenor map: 1m / 3m / 6m / 1y / 2y / 5y / 10y / 30y (§27.10).

NO HTTP calls. NO LLM calls. Pure pricing math + state record helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal


# ── Tenor catalogue (§27.10) ──────────────────────────────────────────
# Days-to-maturity at issue for each allowed tenor. 1y / 2y / 5y / 10y /
# 30y use 365 / 730 / 1825 / 3650 / 10950 (calendar-day approximation,
# matching the FRED `treasury_<tenor>` series we anchor pricing on).
UST_TENOR_DAYS: dict[str, int] = {
    "1m":  30,
    "3m":  91,
    "6m":  182,
    "1y":  365,
    "2y":  730,
    "5y":  1825,
    "10y": 3650,
    "30y": 10950,
}

ALLOWED_TENORS: tuple[str, ...] = tuple(UST_TENOR_DAYS.keys())

# Face value denomination per UST instrument (§27.11).
UST_FACE_VALUE_USD: float = 1000.0


# ── Pricing primitives (§27.11 / §27.12 / §27.13) ─────────────────────

def compute_ust_pv(
    face_value: float,
    yield_pct: float,
    days_to_maturity: int,
) -> float:
    """Present value of a zero-coupon-style UST at purchase (§27.11).

    PV = FV / (1 + y) ** t

    where t = days_to_maturity / 365 (years). yield_pct is the FRED
    `treasury_<tenor>` value, expressed in PERCENT (e.g. 4.85 = 4.85%
    annual yield), so it is divided by 100 inside this function.

    The model is an idealisation: in reality only T-bills (≤1y) are
    pure zero-coupon; T-notes (2y–10y) and T-bonds (30y) pay semi-annual
    coupons. For the purposes of this paper / replay harness the
    zero-coupon discount approximates total-return well enough at the
    monthly cadence and matches the §27.12 daily-interest-accrual model.

    Args:
        face_value: USD face value (typically `UST_FACE_VALUE_USD = 1000`)
        yield_pct: annualised yield in percent (e.g. 4.85)
        days_to_maturity: positive integer days to maturity at purchase

    Returns:
        Purchase PV in USD.
    """
    if face_value <= 0:
        raise ValueError(f"face_value must be > 0; got {face_value!r}")
    if days_to_maturity <= 0:
        raise ValueError(
            f"days_to_maturity must be > 0; got {days_to_maturity!r}"
        )
    y = yield_pct / 100.0
    t_years = days_to_maturity / 365.0
    return face_value / ((1.0 + y) ** t_years)


def accrue_ust_interest(
    face_value: float,
    yield_pct: float,
    days_held: int,
) -> float:
    """Daily-cash interest accrual under the §27.12 HTM model.

    cash_added = FV × y / 365 × days_held

    This is straight-line on FV, NOT compounded — matching the §27.12
    "daily interest = FV × y / 365 added to cash balance" specification.

    Args:
        face_value: USD face value (typically `UST_FACE_VALUE_USD`)
        yield_pct: annualised yield in percent (e.g. 4.85)
        days_held: number of days to accrue (≥ 0)

    Returns:
        USD cash added since last accrual.
    """
    if face_value <= 0:
        raise ValueError(f"face_value must be > 0; got {face_value!r}")
    if days_held < 0:
        raise ValueError(f"days_held must be >= 0; got {days_held!r}")
    y = yield_pct / 100.0
    return face_value * y / 365.0 * days_held


def mark_to_market_ust(
    position: dict,
    current_yield_pct: float,
    today: date | datetime | str,
) -> float:
    """Mark-to-market the UST position at today's FRED yield (§27.13).

    Used when the PM elects to sell the UST early (before maturity). PV
    is recomputed at `current_yield_pct` for the remaining days to
    maturity. The harness elsewhere computes:

        realised = current_PV - original_PV - accrued_interest_to_date

    so this function only returns the current_PV; the harness owns the
    realised P&L tally.

    Args:
        position: a UST position record (see schema below)
        current_yield_pct: today's FRED `treasury_<tenor>` value in
            percent
        today: today's date (date, datetime, or YYYY-MM-DD string)

    Returns:
        Current PV in USD. If maturity_date <= today, returns face_value
        (the position has matured; MTM equals face).
    """
    if not isinstance(position, dict) or position.get("kind") != "ust":
        raise ValueError(
            f"position must be a UST record (kind='ust'); got "
            f"kind={position.get('kind') if isinstance(position, dict) else type(position).__name__!r}"
        )
    today_d = _to_date(today)
    maturity_d = _to_date(position["maturity_date"])
    if maturity_d <= today_d:
        return float(position["face_value"])
    days_remaining = (maturity_d - today_d).days
    return compute_ust_pv(
        face_value=float(position["face_value"]),
        yield_pct=current_yield_pct,
        days_to_maturity=days_remaining,
    )


# ── Position record schema (§27.12) ───────────────────────────────────

@dataclass(frozen=True)
class USTPosition:
    """Canonical UST position record. Matches the dict schema below
    1:1 — kept as a dataclass for type-safety in callers; the wire/EOD
    serialisation uses `to_dict()` and the loose dict shape.

    Fields:
        kind:                always "ust"
        tenor:               one of ALLOWED_TENORS
        face_value:          USD (typically 1000.0 per UST_FACE_VALUE_USD)
        purchase_date:       YYYY-MM-DD ISO date string
        maturity_date:       YYYY-MM-DD ISO date string
        purchase_yield_pct:  FRED treasury_<tenor> at purchase, percent
        purchase_pv:         compute_ust_pv(...) at purchase, USD
        sleeve:              always "fixed_income"
    """
    tenor: Literal["1m", "3m", "6m", "1y", "2y", "5y", "10y", "30y"]
    face_value: float
    purchase_date: str
    maturity_date: str
    purchase_yield_pct: float
    purchase_pv: float
    kind: str = "ust"
    sleeve: str = "fixed_income"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "tenor": self.tenor,
            "face_value": self.face_value,
            "purchase_date": self.purchase_date,
            "maturity_date": self.maturity_date,
            "purchase_yield_pct": self.purchase_yield_pct,
            "purchase_pv": self.purchase_pv,
            "sleeve": self.sleeve,
        }


def build_ust_position(
    *,
    tenor: str,
    yield_pct: float,
    purchase_date: date | datetime | str,
    face_value: float = UST_FACE_VALUE_USD,
) -> dict:
    """Construct a UST position record from a tenor + FRED yield at
    purchase. The returned dict has the schema above (§27.12).
    """
    if tenor not in UST_TENOR_DAYS:
        raise ValueError(
            f"tenor must be one of {ALLOWED_TENORS}; got {tenor!r}"
        )
    purchase_d = _to_date(purchase_date)
    days = UST_TENOR_DAYS[tenor]
    maturity_d = purchase_d + timedelta(days=days)
    pv = compute_ust_pv(face_value, yield_pct, days)
    return USTPosition(
        tenor=tenor,  # type: ignore[arg-type]
        face_value=face_value,
        purchase_date=purchase_d.isoformat(),
        maturity_date=maturity_d.isoformat(),
        purchase_yield_pct=yield_pct,
        purchase_pv=pv,
    ).to_dict()


# ── Helpers ───────────────────────────────────────────────────────────

def _to_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"unsupported date type: {type(value).__name__}")


__all__ = [
    "UST_TENOR_DAYS",
    "ALLOWED_TENORS",
    "UST_FACE_VALUE_USD",
    "USTPosition",
    "compute_ust_pv",
    "accrue_ust_interest",
    "mark_to_market_ust",
    "build_ust_position",
]
