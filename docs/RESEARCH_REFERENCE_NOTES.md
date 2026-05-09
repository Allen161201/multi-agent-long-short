# RESEARCH_REFERENCE_NOTES — Alt-Data Agentic Long-Short

> **NOTE — Anti-rules consolidated 2026-04-28 (D1 Step B).** The "What must NOT become hard-coded rules" subsections in §1, §2, §3, §4 of this file have been extracted to `docs/RULES.md` §21 (Anti-Rules — meta-guidance, non-operational). **For rule guidance, read `docs/RULES.md`.** This file's academic summaries (Lecture 6 / Lecture 9 / Blueprint / Stefanini), the cross-cutting takeaways in §5, and the translation-rules procedure in §6 are preserved as research/design references. Nothing in this file is a trading rule; the anti-rules section in `RULES.md` §21 explicitly marks each anti-rule as ADVISORY meta-guidance constraining future RULE DESIGN, not as an operational trigger.

**Created:** 2026-04-26
**Purpose:** Capture concepts from four FIN 580 reference PDFs as **research / design references** for system design. **Nothing in this file is a trading rule.** None of the PDF claims are converted into code. Any production behavior change requires a separately versioned rule file and human approval.

> **Hard ground rule** — these references inspire *what to look for* and *what to be careful of*. They never become hard-coded buy/sell/short conclusions. The current rule engine (v0.5_agentic_allocation_corrected) and Surge Short v0.4 sleeve are unchanged.

---

## Parsing Status

| File | Pages | Parsed | Notes |
|---|---|---|---|
| `Basics of Quantitative Models.pdf` | 54 | ✅ full text | Tony Zhang, FIN 580, Lecture 6 Parts I & II — Factor-based Quantimental Model + Liquidity. |
| `Lecture 9 Market Anomaly.pdf` | 35 | ✅ full text | CAPM, Size, Value, Momentum, Fama-French 3-factor, double-sort, post-publication decay. |
| `A Blueprint to a Better Quantitative Value Strategy.pdf` | 6 | ⚠ image-only | PyPDF2 returned 0 chars (likely scanned image). Visual render not available in this environment (`pdftoppm` missing). Title-level summary only; we DO NOT cite specific claims we could not read. |
| `Stefanini Chap 3.pdf` | 20 | ⚠ image-only | Same situation. Title-level summary only. |

If higher-fidelity readings of the two image-only PDFs are needed later, possible options are: (a) installing `pdftoppm` / `poppler` to enable the IDE's visual PDF reader, (b) running an offline OCR pass with explicit human authorization, (c) replacing the file with a text-selectable copy. None of these are currently authorized.

---

## 1. Basics of Quantitative Models — Tony Zhang (FIN 580 Lecture 6)

### What it teaches (relevant to this project)

**Part I — Factor-based Quantimental investing**
- *Seven tenets* of quantimental strategy: markets are mostly efficient, pure arbitrage doesn't exist, statistical arbitrage from systematic information processing, factor-model-based forecasting, models grounded in economic theory, persistent and stable patterns, deviations from benchmark only when uncertainty is small enough.
- *Multi-factor model* form: `r_P = α + β₁f₁ + … + β_K f_K + ε`.
- *Information Ratio* decomposition: `IR = IC · √BR`. IC is the information coefficient, BR is the breadth of bets. Improve IR by either better signals (IC) or more uncorrelated bets (BR).
- *Factor zoo*: well-known anomalies — Value (P/B, P/S, P/Div), Size, Neglected-Firm (analyst coverage), January effect, PEG, IPO, index inclusion, Momentum, Analyst forecast revisions, Insider trading, Stock buybacks.
- *Three traps*: data mining (fitting noise), parameter stability (assuming stationarity), parameter uncertainty (ignoring standard errors).
- *Three model families*: fundamental factor (P/B, Size — exposure observable, premium estimated), economic factor (GDP, inflation — premium observable, exposure estimated), alt-data factor (sentiment, textual, geolocation).
- *Z-score screening*: `z_i = (β_i − μ) / σ` standardizes a stock's exposure to a fundamental factor; rank/screen on z-score.
- *Model selection criteria*: economic-theory consistency, ability to combine factor types, ease of implementation, data needs, intuitive appeal.

**Part II — Liquidity**
- *Liquidity = immediacy × depth × width*. No single number captures it.
- *Five trading-cost components*: commissions, bid-ask spread, price impact, opportunity cost, short-sale costs (rebate rate, "hot/special" stocks).
- *Implementation shortfall*: gap between paper return and actual return; turnover × roundtrip cost is a rough estimate.
- *Liquidity premium*: liquid assets cost more / earn less; long-horizon investors are best positioned to harvest illiquidity premium.
- *Liquidity-time-variation*: when prices fall, liquidity falls. Correlation between market returns and liquidity innovations is ~0.52 in down months but ~0.03 in up months.
- *Volume ≠ liquidity*: a Campbell-Grossman-Wang (1993) reversal pattern after high-volume moves; Chordia-Roll-Subrahmanyam (2002) strengthens this on down days.
- *Flight to liquidity* during crises — emerging-market bonds get cheaper, T-bills get more valuable.
- *Practical strategies to manage costs*: stay consistent (style-switching kills turnover), estimate cost of waiting, consider dark pools, minimize orders, do post-trade analysis.

### How this inspires our system (design references only)

- The "Information Ratio = IC × √BR" mental model maps onto our roadmap: agents help raise IC (better narrative-vs-evidence alignment); diversification across signals (Surge-Short, Quality-Long, Alt-Data, Macro-regime) raises BR. **This is a design inspiration, not a metric we currently optimize.**
- The seven tenets reinforce our existing philosophy: rule engine enforces guardrails, agents reason on evidence; we already encode "no margin", "no derivatives", "missing data ≠ zero", "audit log required".
- Trap: data-mining → we do not promote any backtest result into a hard-coded rule without (i) economic justification, (ii) a frozen rule-version bump, (iii) human review.
- Liquidity-aware execution is a design hook for future backtest cost modeling. Today, no liquidity rule is hard-coded; the framework already specifies the 50% / 1M / $2 surge-short screen, which is itself a *minimum-tradability* guardrail.

### What must NOT become hard-coded rules — REMOVED (extracted to `RULES.md` §21.1–§21.3)

---

## 2. Lecture 9 Market Anomalies (FIN 580)

### What it teaches

- **CAPM as a baseline of "no anomaly"**: `E[R_i] = R_f + β_i (E[R_m] − R_f)`. Market is the only priced factor; all alphas should be zero.
- **Size effect** (Banz 1981, Fama-French): smallest-decile stocks outperformed largest by ~8.5 %/yr in 1927–2012, t = 2.6. CAPM β slightly higher for small stocks but not enough to absorb the gap. **Critically — the lecture explicitly notes the size effect "disappeared after 1981" but came back in the 21st century.** Time-varying premia, not a perpetual edge.
- **Value effect**: B/M deciles. Value − Growth ≈ 6.1 %/yr, t = 2.3. Higher CAPM α, higher β. Value underperformed in the 1990s, came back in the 2000s. Fama-French story: HML captures distress risk; value firms more likely to fail in bad times. Counter-evidence (Lakonishok-Shleifer-Vishny 1994; Daniel-Titman 1997 characteristics-vs-betas debate).
- **Double-sort 5×5 size × B/M**: value effect is strongest among small stocks; size effect is strongest among value stocks; small-growth is the "graveyard quadrant".
- **Fama-French 3-factor model** reduces but does not eliminate alphas; Gibbons-Ross-Shanken test — CAPM rejected.
- **Momentum** (Jegadeesh-Titman 1993): top-30% past 12-2-month returns minus bottom-30% earned ~9.6 %/yr, t = 5.7. CAPM and FF both fail to explain; FF adjustment makes momentum profits *bigger*. Pervasive globally except Japan. **Sometimes crashes during market rebounds (e.g. 2009: -83 %).** Earnings momentum (post-earnings announcement drift) is related but distinct.
- **Risk vs. characteristics debate** — are returns explained by factor betas (covariance with priced factors) or by stock characteristics themselves (Daniel-Titman 1997)? Open question.
- **Anomaly decay** — McLean-Pontiff (2012) post-publication study: average anomaly return drops 35 % after academic publication.

### How this inspires our system

- **Multiple regimes / multiple horizons** is a design principle. Our regime-aware allocation (Normal / Weakening / Poor / Crisis with v0.5 discipline labels) is a coarse echo of "factor premia are time-varying."
- **The McLean-Pontiff 35 % decay finding is a *humility prior* for any research-based feature**: when an alt-data signal is added, expect post-publication shrinkage; do not size as if backtest IR persists.
- **Momentum crashes during reversals** ↔ our Surge-Short discipline. Our 16:15 ET / T+1 timing rule, the 1 % initial / +1 % per 100 % add ladder, the 10 % sleeve cap, and the agentic exit (no mechanical P&L stop) are the *structural answer* to the "momentum can crash" problem. **None of this changes — it is already the right answer.**
- **Fama-French value premium ≠ a buy rule** — our quality-long sleeve already requires `fundamental_score ≥ 55`, network-effect evidence, and valuation margin of safety; "low B/M" alone is not a signal in our engine.

### What must NOT become hard-coded rules — REMOVED (extracted to `RULES.md` §21.4–§21.6)

---

## 3. A Blueprint to a Better Quantitative Value Strategy *(image-only — title summary only)*

### What we know from the title

A 6-page paper whose title points to *quantitative value-investing methodology*. The natural research-reference framing of this title is:

- Improving the standard book-to-market value factor by combining valuation with quality / margin-of-safety screens.
- Critiquing naïve P/B and replacing with cash-flow-based valuation (P/E, EV/EBIT, EV/EBITDA, FCF yield) — a common Wesley Gray / Alpha Architect-style argument.
- Layering momentum or earnings-quality overlays on top of value.

**We did NOT extract the body text of this PDF.** Any specific claim, threshold, or formula attributed to the paper would be a fabrication. We reference only the *category of work* it represents.

### Inspiration *category* — already embedded in our system

- The principle "quality + valuation discipline beats valuation alone" is **already encoded** in our quality-long rules (`fundamental_score`, `valuation_assessment`, `combined_quality_score`, the watch-vs-buy decision hint). We do not need to extract specific thresholds from this paper.

### What must NOT happen — REMOVED (extracted to `RULES.md` §21.7)

---

## 4. Stefanini Chapter 3 *(image-only — title summary only)*

### What we know

A chapter from a hedge-fund-strategies textbook (Filippo Stefanini, *Investment Strategies of Hedge Funds* is the standard reference). Chapter 3 of that book is *Long/Short Equity*. Without the body text, we treat this as a **research-reference category** rather than a source of citable claims.

### Inspiration *category* — already embedded

- Long/short equity is the broad mandate of our system: a Surge-Short sleeve, a Quality-Long sleeve, regime-aware allocation, no margin, no derivatives. The textbook chapter format we expect (style description, factor exposures, common pitfalls, drawdown profile) overlaps with what our PROJECT_HANDOFF.md and frozen rule files already document.

### What must NOT happen — REMOVED (extracted to `RULES.md` §21.7 covers the same anti-rule for the Stefanini chapter)

---

## 5. Cross-cutting design takeaways (from the two parsable PDFs)

1. **Anomalies are time-varying and decay after publication.** Treat any alt-data feature as a hypothesis with a sunset date, not a permanent edge.
2. **Trading costs and liquidity matter early, not late.** Future backtests must model commissions, bid-ask spread, price impact, and short-sale fees. The 50 % / 1M / $2 surge screen is itself a liquidity guardrail.
3. **IR = IC · √BR is a planning tool, not a rule.** Adding more uncorrelated bets is more productive than chasing one signal harder.
4. **Risk vs. characteristics is unresolved.** Our system is not committed to either side; the agent-decision boundary already says "agent decides interpretation."
5. **Data mining and parameter instability are existential risks.** Every backtest result that turns into a rule must (a) survive out-of-sample, (b) be economically grounded, (c) clear human review with a frozen-rules version bump.

---

## 6. Translation rules — how a research idea may (eventually) enter the system

```
research idea (PDF / lecture / paper)
    ↓
docs/RESEARCH_REFERENCE_NOTES.md         ← we are here
    ↓
docs/<framework>.md (specific design doc, schema-only)
    ↓
human-reviewed schema/test plan
    ↓
prototype with mock data, audit logs
    ↓
new frozen-rules version bump (v0.X) + PR + human approval
    ↓
active rule (config/rules.yaml points to new version)
```

**No research idea ever goes from PDF → rule engine in a single step.** No research idea ever becomes a hard-coded buy/sell/short conclusion. The agent layer interprets evidence; the rule engine enforces guardrails.
