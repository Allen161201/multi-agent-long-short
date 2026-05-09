# Multi-Agent Long-Short Equity System with Anti-Pollution Alternative-Data Architecture

FIN 580 Final Project · Menos AI Sponsored Track · UIUC Spring 2026
**Author:** Xun (Allen) Li, CFA

## Overview
A multi-agent LLM-driven long-short equity system spanning surge-short, quality-long, and direct US Treasury sleeves. Decomposes the investment process into seven specialized agents (one rule-based macro classifier and six LLM agents) operating under strict point-in-time discipline and a four-tier anti-pollution source taxonomy.

## Four design contributions
1. **Anti-pollution source taxonomy** — alternative-data sources tiered by manipulation cost; social media structurally excluded.
2. **Cover authority discipline** — fundamental agent excluded from cover decisions to prevent profit-taking suppression on poor-fundamentals shorts.
3. **Three-layer network-effect verification** — moat claims require evidence from at least two of three independent layers (cryptographic / attention / financial).
4. **Deterministic macro regime classifier** — rule-based five-state classifier (Crisis / Poor / Normal / Strengthening / Overheat) deliberately not delegated to LLM for reproducibility and contamination resistance.

## Empirical results

### Phase 1 — March 2025 (21 trading days, 3-cell coordination ablation)

| Cell | Configuration | Final NAV | Return | MaxDD |
|------|---------------|-----------|--------|-------|
| 1 | Single-agent baseline | $1,030,639 | +3.06% | −0.32% |
| 2 | Multi-agent without ADaS | $1,124,389 | +12.44% | −0.40% |
| 3 | Multi-agent with ADaS (default) | $1,133,611 | +13.36% | −0.71% |

Bucket A coordination value: **+9.38 percentage points** | Bucket B ADaS marginal value: **+0.92 percentage points**

### Phase 2 — April 2025 (15 trading days, SEC alt-data ablation)

| Cell | Configuration | In-window | Forward (5/30) |
|------|---------------|-----------|----------------|
| A | Default + SEC alt-data | +12.74% | +17.01% |
| B | No SEC alt-data | +14.68% | +17.72% |

The April SEC ablation is inconclusive: forensic inspection revealed SEC adapters returned empty content for all candidate tickers (CIK lookup gap, FMP plan limits). The +1.93pp in-window gap reflects LLM signal-selection variance on substantively identical inputs, not SEC information value. Documented in paper §10.

### Friction model (Menos AI sponsored track)
- Transaction cost: 15 bps single-side (= 30 bps round-trip)
- Borrow cost on shorts: 100% annualized, charged daily
- Initial capital: $1,000,000

## Strict point-in-time discipline (V1–V7)
A seven-anchor framework guards against look-ahead and hindsight contamination. V7 was added late in development after a routine audit detected a silent FRED leak in replay mode that would have inflated the decision-vintage Federal Funds Rate by approximately 100 basis points. The fix and regression-hash relock are documented in paper §4.

## Rule documentation
Rule taxonomy and decision logic are documented at the architectural level in the project paper (Section 5: Multi-Agent Architecture; Section 7: Portfolio Construction and Risk Management; Appendix A: Rule Section Index). The full canonical `RULES.md` is retained privately by the author; numeric thresholds are proprietary IP.

## Repository structure
```
altdata/
├── docs/                        Project paper, presentation, public reproducibility notes
│                                (RULES.md is private — not in public tree)
├── src/
│   ├── agents/                 8-agent inventory + 7-agent active topology by candidate type
│   ├── data_adapters/          FMP / FRED / Polygon / SEC EDGAR / GitHub / Wikipedia adapters
│   ├── evidence_packet/        PIT-disciplined evidence packet builder
│   ├── portfolio/              Position state, cover evaluation, FI review, transaction cost helpers
│   ├── rules/                  Surge-short § rules, hindsight rules, logic audit
│   ├── engine/                 P&L backtest engine
│   ├── llm/                    Anthropic / stub provider, on-disk cache, factory
│   └── dashboard/              Flask + Plotly dashboard (templates + static)
├── tests/                      138 / 138 pytest pass · regression hash sha256:626266c7…
├── scripts/                    Backtest harness + retrofit scripts
├── config/                     Frozen rule YAML
├── data/decisions/             Friction-adjusted backtest artifacts (5 cells, 2 phases)
└── README.md
```

## Reproducibility
- All five backtest cell artifacts are committed under `data/decisions/replay_phase1_*` and `data/decisions/phase1_*_tx30bps_borrow100/`
- LLM cache key: SHA256 over (agent_name | model_id | prompt_version | ticker | decision_timestamp | evidence_packet_hash); deterministic replay supported via stub provider
- Canonical AAPL evidence packet hash locked in `tests/test_regression_matrix.py::EXPECTED_AAPL_PACKET_HASH = sha256:626266c71956d0baec9252d6c9845388a3c324b193d707d2e0e0d75ae2d979bb`
- Five fresh-process runs produce byte-identical hash

## Setup
```bash
# 1. Clone and enter repo
git clone <this-repo>
cd altdata

# 2. Python environment (Python 3.11+ recommended)
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt

# 3. API keys
cp .env.example .env
# Edit .env with your keys: FMP_API_KEY, ANTHROPIC_API_KEY, FRED_API_KEY, POLYGON_API_KEY

# 4. Verify regression
pytest tests/test_regression_matrix.py -v
# Expected: PASS, hash matches sha256:626266c7…

# 5. Launch dashboard
python -m src.dashboard.app
# Visit http://localhost:5000
```

## Limitations
The project is a partial proof of concept. Seventeen explicit limitations are documented in paper §12. The most significant for reproduction:
- 1-month + 15-day backtest sample sizes (insufficient for risk-adjusted statistics like Sharpe / Sortino)
- LLM stochasticity on fresh-process invocations (only cache-hit replay is fully deterministic)
- April SEC ablation inconclusive due to empty adapter responses (paper §10 forensic finding)
- BF-B dual-class share fundamental-data null from FMP

## License
Proprietary numeric thresholds (surge-percentage gates, position-size ladders, drawdown-trigger levels, regime-allocation caps) are retained privately and not published in this repository. Rule taxonomy and decision logic are released for academic review at the architectural level via the project paper, under the FIN 580 course context. Code is provided as-is for reproducibility. No warranty.

## Contact
Xun (Allen) Li · UIUC · xunli4@illinois.edu
