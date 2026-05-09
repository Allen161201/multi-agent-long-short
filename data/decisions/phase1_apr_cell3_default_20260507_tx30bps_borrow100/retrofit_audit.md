# Retrofit audit — Cell A (Apr default)

Cell: `Cell A (Apr default)`  Phase: `Apr`
Source portfolio dir: `F:\projects\altdata\data\decisions\_portfolios\phase1_apr_cell3_default_20260507`
Output dir: `F:\projects\altdata\data\decisions\phase1_apr_cell3_default_20260507_tx30bps_borrow100`

## Friction summary

- Total tx cost (15 bps × N fills): **$1,683.63** across 41 fills
- Total borrow cost (100% APY × MV daily): **$1,180.46**
- Combined friction: **$2,864.09**

## Trade events (count by kind)

- `entry`: 30
- `exit`: 11

## Borrow cost — top 5 tickers

- `STSS`: $217.72
- `JNVR`: $206.15
- `NAOV`: $137.81
- `BTOG`: $136.08
- `MSPR`: $92.64

## NAV impact (in-window)

- Original final NAV: **$1,130,310.53** (+13.03%)
- Tx-only final NAV: **$1,128,626.90** (+12.86%)
- Combined (tx + borrow) final NAV: **$1,127,446.44** (+12.74%)
- ΔNAV from friction: $-2,864.09 (-0.2534%)

## Short-side hold-period sanity check

- Median short holding period (business days): 1.0
- Short closes observed: 11
- Per RULES.md §5.16, expected median ~3 trading days; holding-period borrow cost <1% in 95%+ cases at 100% midpoint.

## Forward extension (Phase 2 only)

- Forward window: 2025-04-22 → 2025-05-30 (28 trading days)
- Forward tx cost (DFS→COF synthesized legs): **$240.70**
- Forward borrow cost (open shorts × 1.00/365 daily): **$3,080.21**
- Forward NAV (frictionless): $1,176,267.98
- Forward NAV (combined-friction): $1,170,082.99

**Synthesis assumption — DFS→COF (2025-05-19)**: sell DFS leg at 2025-05-16 close, buy COF leg at 2025-05-19 close; ratio 1.0192 COF / DFS share; cash component $0.00 (per merger terms). Each leg charged 15 bps on its own notional.
**ANSS merger 2025-07-17 OUT OF CACHE**: Polygon cache ends 2025-05-30, so ANSS held flat at last available close with no synthesized legs (no tx event in forward window for ANSS).

## Convention notes

- Borrow accrues entry day INCLUSIVE, cover day EXCLUSIVE.
- Borrow rate: flat 100% APY ÷ 365 = ~0.27397%/day applied to abs(short market value at EOD).
- Tx cost rate: flat 15 bps single-side per fill (= 30 bps round-trip).
- Cash held at 0% interest in forward extension; UST face accrued at proxy 4.32% APY (presentation_assets parity).
- Median surge-short business days computed as round(calendar_days × 5/7); approximation only.