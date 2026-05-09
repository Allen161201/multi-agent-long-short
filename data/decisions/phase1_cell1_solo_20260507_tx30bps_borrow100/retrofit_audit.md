# Retrofit audit — Solo (Mar)

Cell: `Solo (Mar)`  Phase: `Mar`
Source portfolio dir: `F:\projects\altdata\data\decisions\_portfolios\phase1_cell1_solo_20260507`
Output dir: `F:\projects\altdata\data\decisions\phase1_cell1_solo_20260507_tx30bps_borrow100`

## Friction summary

- Total tx cost (15 bps × N fills): **$930.88** across 13 fills
- Total borrow cost (100% APY × MV daily): **$755.37**
- Combined friction: **$1,686.25**

## Trade events (count by kind)

- `entry`: 11
- `exit`: 2

## Borrow cost — top 5 tickers

- `BTOG`: $275.54
- `SUNE`: $241.80
- `RDUS`: $173.81
- `ACON`: $36.69
- `PRTG`: $27.54

## NAV impact (in-window)

- Original final NAV: **$1,032,325.26** (+3.23%)
- Tx-only final NAV: **$1,031,394.38** (+3.14%)
- Combined (tx + borrow) final NAV: **$1,030,639.01** (+3.06%)
- ΔNAV from friction: $-1,686.25 (-0.1633%)

## Short-side hold-period sanity check

- Median short holding period (business days): 11.0
- Short closes observed: 2
- Per RULES.md §5.16, expected median ~3 trading days; holding-period borrow cost <1% in 95%+ cases at 100% midpoint.

## Convention notes

- Borrow accrues entry day INCLUSIVE, cover day EXCLUSIVE.
- Borrow rate: flat 100% APY ÷ 365 = ~0.27397%/day applied to abs(short market value at EOD).
- Tx cost rate: flat 15 bps single-side per fill (= 30 bps round-trip).
- Cash held at 0% interest in forward extension; UST face accrued at proxy 4.32% APY (presentation_assets parity).
- Median surge-short business days computed as round(calendar_days × 5/7); approximation only.