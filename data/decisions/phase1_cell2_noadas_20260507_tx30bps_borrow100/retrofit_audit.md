# Retrofit audit — Multi-noADaS (Mar)

Cell: `Multi-noADaS (Mar)`  Phase: `Mar`
Source portfolio dir: `F:\projects\altdata\data\decisions\_portfolios\phase1_cell2_noadas_20260507`
Output dir: `F:\projects\altdata\data\decisions\phase1_cell2_noadas_20260507_tx30bps_borrow100`

## Friction summary

- Total tx cost (15 bps × N fills): **$1,277.55** across 29 fills
- Total borrow cost (100% APY × MV daily): **$2,560.04**
- Combined friction: **$3,837.59**

## Trade events (count by kind)

- `entry`: 24
- `exit`: 5

## Borrow cost — top 5 tickers

- `BTOG`: $275.54
- `CMRX`: $260.97
- `RDFN`: $249.26
- `GV`: $248.60
- `SUNE`: $241.65

## NAV impact (in-window)

- Original final NAV: **$1,128,226.98** (+12.82%)
- Tx-only final NAV: **$1,126,949.43** (+12.69%)
- Combined (tx + borrow) final NAV: **$1,124,389.39** (+12.44%)
- ΔNAV from friction: $-3,837.59 (-0.3401%)

## Short-side hold-period sanity check

- Median short holding period (business days): 3.0
- Short closes observed: 5
- Per RULES.md §5.16, expected median ~3 trading days; holding-period borrow cost <1% in 95%+ cases at 100% midpoint.

## Convention notes

- Borrow accrues entry day INCLUSIVE, cover day EXCLUSIVE.
- Borrow rate: flat 100% APY ÷ 365 = ~0.27397%/day applied to abs(short market value at EOD).
- Tx cost rate: flat 15 bps single-side per fill (= 30 bps round-trip).
- Cash held at 0% interest in forward extension; UST face accrued at proxy 4.32% APY (presentation_assets parity).
- Median surge-short business days computed as round(calendar_days × 5/7); approximation only.