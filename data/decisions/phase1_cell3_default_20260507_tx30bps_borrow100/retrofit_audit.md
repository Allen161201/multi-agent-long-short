# Retrofit audit — Multi+ADaS (Mar)

Cell: `Multi+ADaS (Mar)`  Phase: `Mar`
Source portfolio dir: `F:\projects\altdata\data\decisions\_portfolios\phase1_cell3_default_20260507`
Output dir: `F:\projects\altdata\data\decisions\phase1_cell3_default_20260507_tx30bps_borrow100`

## Friction summary

- Total tx cost (15 bps × N fills): **$1,569.30** across 27 fills
- Total borrow cost (100% APY × MV daily): **$3,137.55**
- Combined friction: **$4,706.84**

## Trade events (count by kind)

- `entry`: 24
- `exit`: 3

## Borrow cost — top 5 tickers

- `BTOG`: $275.54
- `RDFN`: $262.58
- `CMRX`: $260.97
- `GV`: $248.60
- `CKPT`: $246.76

## NAV impact (in-window)

- Original final NAV: **$1,138,317.45** (+13.83%)
- Tx-only final NAV: **$1,136,748.15** (+13.67%)
- Combined (tx + borrow) final NAV: **$1,133,610.61** (+13.36%)
- ΔNAV from friction: $-4,706.84 (-0.4135%)

## Short-side hold-period sanity check

- Median short holding period (business days): 5.0
- Short closes observed: 3
- Per RULES.md §5.16, expected median ~3 trading days; holding-period borrow cost <1% in 95%+ cases at 100% midpoint.

## Convention notes

- Borrow accrues entry day INCLUSIVE, cover day EXCLUSIVE.
- Borrow rate: flat 100% APY ÷ 365 = ~0.27397%/day applied to abs(short market value at EOD).
- Tx cost rate: flat 15 bps single-side per fill (= 30 bps round-trip).
- Cash held at 0% interest in forward extension; UST face accrued at proxy 4.32% APY (presentation_assets parity).
- Median surge-short business days computed as round(calendar_days × 5/7); approximation only.