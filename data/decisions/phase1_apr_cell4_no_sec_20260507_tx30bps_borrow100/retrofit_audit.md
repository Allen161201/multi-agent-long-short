# Retrofit audit — Cell B (Apr no-SEC)

Cell: `Cell B (Apr no-SEC)`  Phase: `Apr`
Source portfolio dir: `F:\projects\altdata\data\decisions\_portfolios\phase1_apr_cell4_no_sec_20260507`
Output dir: `F:\projects\altdata\data\decisions\phase1_apr_cell4_no_sec_20260507_tx30bps_borrow100`

## Friction summary

- Total tx cost (15 bps × N fills): **$917.00** across 41 fills
- Total borrow cost (100% APY × MV daily): **$1,186.05**
- Combined friction: **$2,103.05**

## Trade events (count by kind)

- `entry`: 30
- `exit`: 11

## Borrow cost — top 5 tickers

- `STSS`: $221.69
- `JNVR`: $209.87
- `NAOV`: $140.29
- `BTOG`: $138.53
- `MSPR`: $94.86

## NAV impact (in-window)

- Original final NAV: **$1,148,865.09** (+14.89%)
- Tx-only final NAV: **$1,147,948.09** (+14.79%)
- Combined (tx + borrow) final NAV: **$1,146,762.04** (+14.68%)
- ΔNAV from friction: $-2,103.05 (-0.1831%)

## Short-side hold-period sanity check

- Median short holding period (business days): 1.0
- Short closes observed: 11
- Per RULES.md §5.16, expected median ~3 trading days; holding-period borrow cost <1% in 95%+ cases at 100% midpoint.

## Forward extension (Phase 2 only)

- Forward window: 2025-04-22 → 2025-05-30 (28 trading days)
- Forward tx cost (DFS→COF synthesized legs): **$245.44**
- Forward borrow cost (open shorts × 1.00/365 daily): **$2,785.55**
- Forward NAV (frictionless): $1,182,315.69
- Forward NAV (combined-friction): $1,177,181.64

**Synthesis assumption — DFS→COF (2025-05-19)**: sell DFS leg at 2025-05-16 close, buy COF leg at 2025-05-19 close; ratio 1.0192 COF / DFS share; cash component $0.00 (per merger terms). Each leg charged 15 bps on its own notional.
**ANSS merger 2025-07-17 OUT OF CACHE**: Polygon cache ends 2025-05-30, so ANSS held flat at last available close with no synthesized legs (no tx event in forward window for ANSS).

## Convention notes

- Borrow accrues entry day INCLUSIVE, cover day EXCLUSIVE.
- Borrow rate: flat 100% APY ÷ 365 = ~0.27397%/day applied to abs(short market value at EOD).
- Tx cost rate: flat 15 bps single-side per fill (= 30 bps round-trip).
- Cash held at 0% interest in forward extension; UST face accrued at proxy 4.32% APY (presentation_assets parity).
- Median surge-short business days computed as round(calendar_days × 5/7); approximation only.