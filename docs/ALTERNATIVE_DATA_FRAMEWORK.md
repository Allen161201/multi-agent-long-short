# ALTERNATIVE_DATA_FRAMEWORK — Narrative-Price Dislocation Verification

> **NOTE — Rules consolidated 2026-04-28 (D1 Step B).** The operational rules formerly in §4 (cross-cutting design principles — 5 numbered rules), §6 (anti-rule "what is NOT" list), §9.5 (six hard adapter rules), §10 (information integrity & pollution defense — `STATUS: PARTIALLY_DEFERRED` in `RULES.md` §10), and §11 (sentiment / community / ownership hard rules) have been extracted to `docs/RULES.md` (canonical). Section-specific extraction targets:
> - §4 → `RULES.md` §11.1–§11.5 (alt-data source rules)
> - §6 → `RULES.md` §11.6 (descriptors-not-rules) and §10 (information integrity)
> - §9.5 → `RULES.md` §11.6, §11.8–§11.11
> - §10 → `RULES.md` §10.1–§10.8
> - §11 hard rules → `RULES.md` §11.13, §11.14, §10.6, §10.7, §10.12 ("whale" literal forbidden)
> - §3.F output role hard rules (quality-long buy gate `combined_quality_score ≥ 50` + `fundamental_score ≥ 55`) → `RULES.md` §3.1 (INTERIM placeholder; conflicting `fundamental_score ≥ 55` is **suspended pending Step C** per §20.2)
>
> **For rule guidance, read `docs/RULES.md`.** This file is preserved as the **core alt-data architecture document** and is the primary source for the paper's methodology section. Sections §0–§3 (core idea / framing / scenarios / schema / feature-group definitions), §5 (target architecture), §7 (promotion path), §8 (open design questions), and the descriptor schemas inside §9, §10, §11 remain authoritative for design and paper-writing reference. Any future updates to the rules originally extracted from this file should land in `RULES.md`, not back here.

**Created:** 2026-04-26
**Status:** Schema and design only. **Not implemented yet. No thresholds. No trading decisions. No new rules.** No external alt-data API is connected (no SEC, no Reddit, no GitHub, no H1B, no LLM).

---

## 0. Core idea

When price moves sharply, ask:

> **Does external alternative evidence support the market narrative, or is price reacting more than the evidence justifies?**

This single question covers both sides of our long-short mandate:

- **Surge-Short candidates** — price went up; is there real value-creating evidence behind it, or is this hype?
- **Quality-Long candidates** — price went down; is the selloff macro-driven and indiscriminate (good company at an acceptable price), or company-specific (broken thesis)?

The alt-data agent's job is **verification**, not narrative invention. It reads timestamped evidence and reports whether the public narrative is *supported*, *weakly supported*, *contradicted*, or *unverifiable*.

---

## 1. Why "narrative-price dislocation" rather than "alt-data signal"

Most alt-data products are sold as alpha signals (e.g. credit-card panel → revenue surprise → return). That framing has known problems:

- Anomaly decay after publication (McLean-Pontiff 2016): factor returns drop ~35 % after academic publication.
- Selection bias in vendor backtests.
- Regime dependence: a feature that works in expansion may fail in contraction.

We instead frame alt-data as **verification of an existing narrative**. The narrative comes first (an actual price move + an actual catalyst). Alt-data either supports or contradicts it. This:

1. Anchors the agent's reasoning to *something concrete* (the catalyst), not a black-box score.
2. Makes "no signal" a valid output. If alt-data is silent, the agent says so and the rule engine returns `no_trade`.
3. Reduces overfitting: we're not back-fitting alt-data to past returns; we're asking whether *this* narrative on *this* day is verifiable.

---

## 2. Two scenarios this framework supports

### Scenario A — Surge-Short (price spike, narrative under scrutiny)

After the 50 % / 1M / $2 mechanical screen passes a candidate, the alt-data agent asks:
- Is the surge supported by **real external evidence** (verifiable contract, financial event, regulatory filing)?
- Is the catalyst **specific** (a named counterparty, a dollar value, a date) or **vague** ("AI pivot announced")?
- Is the price reaction **disproportionate** to the evidence (e.g. $50M contract → $4B mcap jump)?
- Is this **real value creation** or **temporary attention**?

### Scenario B — Quality-Long (price drawdown, thesis under scrutiny)

For a quality-long candidate already passing fundamentals:
- Is the selloff **macro-driven** (sector / market / regime correlated) or **company-specific**?
- Did broad-market stress create an **acceptable price for a good company**?
- Is the thesis still **intact** (no new fundamental breakage)?
- Are there **company-specific red flags** (governance, dilution, going concern, key-person departure)?

The same alt-data feature schema serves both scenarios. The interpretation context differs.

---

## 3. Feature groups (schema-only — no thresholds, no decisions)

The following feature groups are **descriptors of the evidence packet**. They are **not buy/sell rules**. The agent reasons over them; the rule engine does not branch on them.

### A. News / Event Attention Features

| Field | Type | Definition |
|---|---|---|
| `news_attention_count_1d` | int | Count of news mentions in the last 1 trading day. |
| `news_attention_count_3d` | int | …last 3 trading days. |
| `news_attention_count_7d` | int | …last 7 trading days. |
| `news_attention_zscore_60d` | float | Z-score of `news_attention_count_3d` against the 60-day rolling mean and std. |
| `source_diversity_score` | float ∈ [0,1] | Distinct-source ratio: `unique_sources / total_articles`. |
| `repeated_press_release_flag` | bool | True if ≥ 2 articles share substantially identical wording (suggests one PR being syndicated). |
| `event_recency_hours` | float | Hours since the most-recent triggering article. |

### B. Narrative Specificity Features

| Field | Type | Definition |
|---|---|---|
| `catalyst_type` | string enum | One of: `earnings_surprise`, `major_contract`, `vague_partnership`, `ai_pivot`, `crypto_pivot`, `delisting_relief`, `meme_squeeze`, `phase_1_or_2_biotech_result`, `fda_event`, `litigation_court_update`, `financing_dilution_event`, `foreign_listing_compliance_update`, `turnaround_story`, `platform_story`, `retail_traffic_growth`, `other`. |
| `catalyst_specificity_score` | float ∈ [0,1] | Higher = more named entities, dollar values, dates. Computed by the agent, not hard-coded. |
| `vague_narrative_flag` | bool | True if catalyst lacks named counterparty / amount / timeline. |
| `ai_pivot_flag` | bool | Catalyst contains "AI", "machine learning", "LLM", and the issuer's prior business is unrelated. |
| `crypto_pivot_flag` | bool | Same shape, crypto-related. |
| `financing_flag` | bool | At-the-market offering, registered direct, PIPE, convertible, or share dilution. |
| `fda_event_flag` | bool | FDA approval / CRL / breakthrough designation / phase result. |
| `earnings_event_flag` | bool | Within 48h of earnings or pre-announcement. |
| `contract_or_partnership_flag` | bool | Specific commercial contract or partnership announcement. |

> **These flags are evidence descriptors only.** They are not automatic short or long triggers. The agent's job is to *interpret* a combination of flags in context; the rule engine never branches on a single flag.

### C. Filing / Disclosure Confirmation Features

| Field | Type | Definition |
|---|---|---|
| `SEC_8K_confirmation` | bool | An 8-K was filed referencing the catalyst, on or before the news-event timestamp. |
| `filing_date` | ISO date | The SEC `filing_date` (point-in-time-safe input). |
| `accepted_datetime` | ISO datetime | SEC `accepted_datetime` (often more accurate than `filing_date` for intraday usability). |
| `press_release_confirmation` | bool | An issuer-issued press release matches the catalyst. |
| `earnings_call_confirmation` | bool | An earnings call transcript references the catalyst. |
| `filing_support_score` | float ∈ [0,1] | Composite: confirmation count / specificity. **Computed by the agent**, not the rule engine. |

> **PIT-safety rule (already in the engine):** 10-Q / 10-K usable only after `filing_date`. News content usable after `publication_timestamp`. After-close items → next trading day. This framework reuses those rules; it does not relax them.

### D. Retail / Meme Attention Features *(optional — not connected)*

| Field | Type | Definition |
|---|---|---|
| `reddit_mention_spike` | float | Z-score of subreddit mentions vs. 30-day mean. |
| `wallstreetbets_flag` | bool | True if a `r/wallstreetbets` post crosses N upvotes within K hours. |
| `meme_language_flag` | bool | Catalyst language matches a "diamond hands / to the moon" lexicon. |
| `abnormal_retail_attention_score` | float | Composite normalized score. |

> **Reddit ingestion is NOT enabled today.** If/when enabled, all access is read-only, with public-API rate limits respected, no PII stored, and source URLs + timestamps logged.

### E. Fundamental / Valuation Context Features

These come from FMP and are **already partly available** in the project today. They are listed here for completeness so the alt-data agent can read them alongside the alt-data features:

| Field | Type | Definition |
|---|---|---|
| `valuation_support_score` | float | Composite from valuation-aware scoring (already in `quality_long_rules`). |
| `quality_score` | float | Combined quality score. |
| `profitability_support` | enum | One of `strong / mixed / weak / data_unavailable`. |
| `leverage_risk` | enum | `low / moderate / high / data_unavailable`. |
| `DCF_gap_display_only` | float | (FMP DCF − price) / price. **Display only — not point-in-time-safe.** |
| `current_snapshot_not_PIT_safe` | bool | True for any TTM snapshot block (FMP `key-metrics-ttm`, `ratios-ttm`, etc.). |

### F. Narrative-Price Gap Score *(conceptual — schema only, no formula yet)*

A scalar that describes the gap between observed price reaction and externally verifiable evidence. **We define the meaning, not a formula.**

| Score band | Meaning |
|---|---|
| **Low gap** | Big move + strong, specific, multi-source, filing-confirmed evidence. Price reaction is in the ballpark of evidence. |
| **Medium gap** | Move and evidence are roughly aligned but the evidence is only partial / weak / single-source. |
| **High gap** | Big move + vague / unverifiable / single-source / dilution-flavored / "AI pivot" evidence. Price reaction looks disproportionate. |

> **No threshold is hard-coded today.** The agent classifies the gap qualitatively; the rule engine does not branch on the score. If we ever encode a numeric mapping, that's a frozen-rules version bump (v0.6+) with human review.

### Output role of feature group F

The agent emits its `narrative_price_gap_assessment` as part of its JSON (see `docs/AGENT_OUTPUT_SCHEMA_DRAFT.md`). The Risk/PM agent uses it as *one* input among many. Specifically:

- For **Surge-Short**: a high gap *increases the agent's confidence that the surge is unsupported* — but the rule engine still requires the v0.4 sleeve discipline (50/1M/$2 screen, baseline exclusions, 1 % initial / 100 %-rise add ladder, 10 % cap, agentic exit).
- For **Quality-Long**: a low gap *increases the agent's confidence that a selloff is dislocational* — but the rule engine still requires `quality_long` thresholds (`fundamental_score ≥ 55`, `combined_quality_score ≥ 50`, valuation pass).

In both cases, **the agent's gap assessment never overrides the rule engine.** It is one of many evidence elements feeding the agent's `recommended_action`.

---

## 4. Cross-cutting design principles — REMOVED (extracted to `RULES.md` §11.1–§11.5)

The 5 numbered cross-cutting rules (every-feature-has-a-timestamp / missing-data-is-Data-unavailable-never-zero / every-feature-has-a-source-flag / aggregations-require-manifest / no-look-ahead-injection) live in `RULES.md` §11.1 through §11.5. Note: §11.3 (mock_fallback) was clarified in B3 per CONFLICTS.md C-07 — `mock_fallback` rows are PERMITTED at decision time but the agent MUST NOT treat them as ground truth.

---

## 5. Where this framework lives in the system (target — not yet wired)

```
+--- evidence packet (per ticker, per decision_timestamp) ---+
|                                                            |
|   [Existing today]                                         |
|   - price_snapshot   (FMP /quote)                          |
|   - macro_regime     (FRED-derived classification)         |
|   - fundamentals_snapshot (TTM, not PIT-safe — flagged)    |
|                                                            |
|   [Drafted for the alt-data agent]                         |
|   - news_event_summary (group A,B)                         |
|   - filing_confirmation (group C)                          |
|   - retail_meme_attention (group D — optional)             |
|   - valuation_context (group E)                            |
|   - narrative_price_gap_assessment (group F)               |
|                                                            |
|   - data_quality_flags                                     |
|   - PIT_safety_flags                                       |
|   - source_list                                            |
|   - agent_ready_notes                                      |
+------------------------------------------------------------+
                            |
                            v
                   Alt-Data Verification Agent (Agent 03)
                            |
                            v
                   { evidence_score, verdict, gap_assessment, ... }
                            |
                            v
                   Risk/PM Agent (Agent 05) — rule-engine-final
```

Today, feature groups A-D are **drafted but not wired**. Group E is partly available via FMP. Group F is conceptual. The evidence packet skeleton is documented in `docs/EVIDENCE_PACKET_V1_DRAFT.md`.

---

## 6. What is explicitly NOT in this framework — REMOVED (extracted to `RULES.md` §11.6, §10, and §17)

The 6 anti-rules of this section were extracted as follows:
- "No buy/sell/short signal from a feature alone" → `RULES.md` §11.6 (adapter outputs are descriptors, not trading rules) and §10.2 (sentiment is a descriptor, never a decision rule).
- "No automatic threshold for the narrative-price gap score" → covered by `RULES.md` §11.6 + §19.9 (rule-vs-suggestion test).
- "No external API connection today" → superseded by user 2026-04-28 directive (OpenCLI in-scope); see `RULES.md` §11.11 and §17.
- "No replacement of v0.5 regime allocation" → `RULES.md` §4.5 (regime allocation policy unchanged) and §17.B.5 (frozen-rules compatibility).
- "No replacement of Surge Short v0.4" → `RULES.md` §2 (Surge-Short v0.4 sleeve discipline preserved) and §17.B.5.
- "No prediction of future returns" → `RULES.md` §11.6 + §19.5 (no LLM authoritative on facts about the world).

---

## 7. How a feature gets promoted from this doc into production

```
feature defined in this doc (schema only)
   ↓
mock data fixtures + unit tests for the feature extractor
   ↓
adapter implementation (live_<source> + mock_fallback)
   ↓
evidence-packet integration with timestamps + source flags
   ↓
agent prompt + output schema updated (versioned)
   ↓
audit-log captures the new field
   ↓
human review + frozen-rules version bump if any rule reads the field
   ↓
active in pipeline
```

No feature skips a step.

---

## 8. Open design questions (to revisit when we have data)

These are recorded so future designers can pick up the thread; they are **not** decided.

1. **How granular should `catalyst_type` be?** A long enum invites mis-classification; a short one buckets too aggressively.
2. **How to score `catalyst_specificity_score` without an LLM?** A purely lexical score is brittle; an LLM-scored value is non-deterministic. A hybrid (lexical features + LLM-confirmed extraction) may be acceptable with versioning.
3. **What is the right baseline for `news_attention_zscore_60d`?** Total mentions, source-weighted mentions, or lexicon-weighted mentions?
4. **How do we calibrate the gap-score categories** without overfitting to a recent window?
5. **What's the right alt-data aggregation when sources disagree?** Majority vote, source-quality weighting, agent-judgment-only?

These questions are **research questions**, not rule questions. Their resolution lives in `docs/`, not in `config/rules.yaml`.

---

## 9. Industry-Specific Hard-to-Fake Alternative Evidence Adapter Registry

**Added:** 2026-04-26 (Phase 3B). **Status: documentation only.** No adapter is wired into production. No external API is connected. The registry below is a *design contract* describing what each adapter would provide *if* it were implemented and *if* it were authorized for use.

### 9.1 Why an adapter registry instead of a single alt-data feed

The Narrative-Price Dislocation Verification framework (sections 1-6) is **shared across all candidates**: every ticker gets the same question — "is the price move supported by external evidence?" — and the same six feature groups (A-F).

But the *evidence sources that are hard for management or market narratives to fake* differ by industry:

- A SaaS company's "engineering velocity" claim is testable against public GitHub commit history.
- A semiconductor firm's "AI capability" claim is testable against patent semantic clusters and technical-publication count.
- A biotech firm's "Phase 2 progress" claim is testable against ClinicalTrials.gov + PubMed.
- A regional bank's "deposit stability" claim is testable against FDIC call reports.

Generic sentiment scores fail this test — they aggregate claims that are *easy* to manipulate. The adapter registry is a way of **routing each candidate to the externally-verifiable evidence sources that best apply to its industry**, while keeping the unifying agent-level question the same.

### 9.2 Adapter contract (every adapter must satisfy)

Every adapter — when implemented — must produce evidence rows with this shape:

```jsonc
{
  "adapter_id":         "software_devtools_adapter",
  "ticker":             "AAPL",
  "primary_industry":   "technology_ai_software",
  "as_of":              "2026-03-15T16:15:00-04:00",
  "source":             "live_<source> | cache | mock_fallback | live_<source>_failed",
  "features":           { /* adapter-specific, see registry below */ },
  "features_missing":   ["string", "..."],
  "pit_safety":         "pit_safe | live_snapshot_only | mixed",
  "source_list":        [ /* manifest entries with url + retrieved_at */ ],
  "data_quality_warnings": [ /* {kind, severity, detail} */ ]
}
```

**Common rules** (from the existing framework):
- `source` is never `live_*` if the actual upstream call failed.
- Missing features are listed by name (`features_missing`); they are never silently zero.
- `pit_safety` is asserted per row; agents must respect it.
- The adapter can refuse and emit `features_missing: ["all"]` with a `data_quality_warning`. Refusal is a valid output.

### 9.3 The registry

For each industry the table below names: source(s), expected features (descriptors only), point-in-time considerations, free vs. paid feasibility, data-quality risks, and which sleeve(s) it fits.

> **Reading discipline:** every cell is a *design hint*, not a rule. None of the listed signals is an entry / exit trigger. The agent reads the descriptors and reasons about whether the narrative is supported, contradicted, or unverified.

---

#### Adapter 1 — `software_devtools_adapter` *(Software / SaaS / DevTools)*

| Item | Value |
|---|---|
| **Industries** | Software / SaaS / dev-tools / open-source-centric tech |
| **Source(s)** | GitHub public API, npm registry, PyPI, optionally Stack Overflow developer survey |
| **Expected features** (descriptors) | `github_commits_30d`, `github_active_contributors_30d`, `github_external_contributor_share`, `github_issue_close_rate`, `dependency_graph_in_degree`, `npm_weekly_downloads_zscore_60d`, `pypi_weekly_downloads_zscore_60d`, `developer_ecosystem_adoption_band` (`growing` / `stable` / `declining` / `not_evaluated`) |
| **Use case** | Verify developer adoption, engineering velocity, product mind-share. Distinguish "real shipping team" from "pivot announcement." |
| **PIT considerations** | GitHub event timestamps are reliable. `pit_safe` for historical commit / issue rows. Download counters are weekly snapshots; a current-week reading is `live_snapshot_only`. |
| **Free feasibility** | Mostly free (GitHub public API ratelimited; npm/PyPI registries free). |
| **Data-quality risks** | Mapping company → repositories is non-trivial and must be **audited** (a wrong mapping invalidates the signal). Vendored / mirrored repos can inflate counts. Bot-driven commits should be filtered. |
| **Suitable for** | **Surge-Short** (testing AI / pivot narratives) and **Quality-Long** (engineering depth as quality evidence). |

---

#### Adapter 2 — `semiconductor_patent_adapter` *(Semiconductor / AI Hardware / Infrastructure)*

| Item | Value |
|---|---|
| **Industries** | Semiconductor, AI hardware, GPU / TPU / accelerator vendors, advanced infrastructure |
| **Source(s)** | USPTO PatentsView API, Google Patents (read-only), arXiv / IEEE Xplore for technical publications, GitHub for hardware-adjacent ecosystems (CUDA / Verilog / OpenCL repos) |
| **Expected features** | `patent_filings_12m`, `patent_filings_zscore_5y`, `patent_semantic_cluster_label` (e.g. "transformer accelerator", "memory hierarchy"), `patent_citation_in_degree_zscore`, `technical_publication_count_12m`, `cuda_or_hardware_ecosystem_activity_band` |
| **Use case** | Verify technical depth, R&D direction, AI / hardware capability. Distinguish "we have AI" from "we have AI patents in the right cluster being cited." |
| **PIT considerations** | Patents publish ~18 months after filing — so `filing_date` and `publication_date` are different and both must be tracked. Citations are inherently retroactive; the row's `as_of` should be a publication-date cutoff. |
| **Free feasibility** | PatentsView and arXiv are free; bulk patent text downloads have throttle limits. |
| **Data-quality risks** | Patent counts measure *filing volume*, not *quality*. Semantic clusters require an offline classifier; clusters can drift. Slower-moving than catalysts; useful as **structural evidence**, not short-term confirmation. |
| **Suitable for** | **Quality-Long** primarily; **Surge-Short** secondary (testing whether a "we are an AI hardware company" pivot has any patent footprint). |

---

#### Adapter 3 — `biotech_clinical_adapter` *(Biotech / Pharma)*

| Item | Value |
|---|---|
| **Industries** | Biotech, pharmaceutical, drug-development |
| **Source(s)** | ClinicalTrials.gov public API, PubMed Entrez API, FDA Drugs@FDA, SEC EDGAR (8-K and S-1) |
| **Expected features** | `active_trial_count`, `trial_phase_distribution` (% of trials in Phase 1 / 2 / 3), `enrollment_progress_band`, `trial_endpoint_design_summary` (text), `pubmed_publications_24m`, `fda_pathway` (`fast_track` / `breakthrough` / `priority_review` / `standard` / `not_applicable`), `funding_runway_months` (from latest 10-Q + cash burn), `comparable_drug_outcome_summary` |
| **Use case** | Verify trial progress, scientific credibility, regulatory milestones. Distinguish "Phase 2 announced" from "trial is enrolling on time, has prespecified endpoints, peer-reviewed comparators exist." |
| **PIT considerations** | ClinicalTrials.gov updates are timestamped — `pit_safe` for historical rows. PubMed publication dates are stable. SEC `filing_date` rule applies. |
| **Free feasibility** | Free (NIH / FDA APIs are public). |
| **Data-quality risks** | Phase numbering is meaningful but not predictive on its own. **Do not hard-code "Phase 2 success = buy."** Trial endpoints can be redesigned mid-study; agents must read trial-design fields, not just phase number. |
| **Suitable for** | **Surge-Short** (testing biotech-spike claims) and **Quality-Long** (pipeline depth as quality evidence). |

---

#### Adapter 4 — `consumer_traffic_adapter` *(Retail / Restaurants / Consumer)*

| Item | Value |
|---|---|
| **Industries** | Retail, restaurants, consumer brands, app-driven services |
| **Source(s)** | App-store rankings (when authorized), public review platforms (Yelp / Google Maps / OpenTable) where licensing allows, store-count from press releases / SEC filings, optional foot-traffic proxies (only via licensed providers, not scraping) |
| **Expected features** | `app_store_rank_change_30d`, `review_volume_zscore_60d`, `review_sentiment_band` (`improving` / `stable` / `degrading` / `not_evaluated`), `store_count_trend`, `foot_traffic_zscore_60d` (if licensed), `discounting_signal_band`, `inventory_signal_band` |
| **Use case** | Verify demand trend, store traffic, consumer attention. Distinguish "guidance raised" from "app rank rising and review volume up." |
| **PIT considerations** | App-store rank is daily snapshot — `live_snapshot_only` unless archived. Review timestamps are stable. Store-count from filings is `pit_safe`. |
| **Free feasibility** | App-store rankings and review counts often **require license / paid access** for systematic use. SEC-filed store counts are free. |
| **Data-quality risks** | Heavy noise; review volume can be brigaded. Scraping ToS must be respected (read-only adapters via licensed providers only). |
| **Suitable for** | **Surge-Short** (testing "viral demand" narratives) and **Quality-Long** (ongoing demand as quality evidence). |

---

#### Adapter 5 — `bank_regulatory_adapter` *(Banks / Financials)*

| Item | Value |
|---|---|
| **Industries** | Commercial banks, regional banks, broader US financial institutions |
| **Source(s)** | FDIC call reports, FFIEC, OCC enforcement actions, SEC 10-Q/10-K, Federal Reserve H.8 release |
| **Expected features** | `total_deposits_qoq_change`, `uninsured_deposit_share`, `branch_count_qoq_change`, `delinquency_rate_band`, `nonperforming_loan_ratio_band`, `tier_1_capital_ratio`, `compliance_or_risk_hiring_signal_band` (when surfaceable from filings), `enforcement_action_present_flag` |
| **Use case** | Verify balance-sheet stress, deposit risk, operating footprint. Distinguish "we are well-capitalized" from "deposits are stable QoQ and call-report ratios are within historical norms." |
| **PIT considerations** | Call-report `as_of_date` is quarter-end; *availability* lags by 30-60 days. Use `as_of_date + availability_lag` as the PIT timestamp. |
| **Free feasibility** | Free (regulatory data is public). |
| **Data-quality risks** | Bank ratios are dense and inter-correlated; agent must reason across them, not on a single ratio. Definitional changes across reporting periods occur. |
| **Suitable for** | **Quality-Long** primarily (stress testing); **Surge-Short** for narrative-driven moves on regional-bank tickers. |

---

#### Adapter 6 — `airline_travel_adapter` *(Airlines / Travel)*

| Item | Value |
|---|---|
| **Industries** | Airlines, travel, aerospace operators |
| **Source(s)** | BTS (Bureau of Transportation Statistics), DOT data, OpenSky / ADS-B for flight density (where accessible and licensed), TSA throughput, public on-time / cancellation rates |
| **Expected features** | `flight_density_band`, `load_factor_band`, `on_time_rate_band`, `cancellation_rate_band`, `tsa_throughput_zscore_60d`, `industry_capacity_trend_band` |
| **Use case** | Verify utilization, capacity, demand recovery. Distinguish "guidance raised" from "actual flight density is up and TSA throughput is rising." |
| **PIT considerations** | BTS publishes with multi-month lag; TSA data is daily and `pit_safe`. ADS-B-derived metrics are near-real-time. |
| **Free feasibility** | BTS / TSA / DOT free; ADS-B feeds vary (some free, some paid). |
| **Data-quality risks** | Airline ticker → flight-density mapping requires a fleet / hub mapping. Industry-level signals are stronger than firm-level for low-coverage tickers. |
| **Suitable for** | **Quality-Long** (industry-level confirmation); **Surge-Short** secondary. |

---

#### Adapter 7 — `energy_commodity_adapter` *(Energy / Commodities)*

| Item | Value |
|---|---|
| **Industries** | Oil & gas, mining, utilities, midstream / pipeline, commodity-linked equities |
| **Source(s)** | EIA (US Energy Information Administration), USGS, public commodity-price feeds (free tier of FMP / Yahoo / etc.), satellite / oil-storage proxies via licensed providers, pipeline / shipping proxies via public databases where available |
| **Expected features** | `commodity_price_band`, `inventory_zscore_60d`, `production_volume_band`, `permit_filings_count_12m`, `operating_cost_curve_band`, `reserves_disclosure_summary` (text from latest 10-K) |
| **Use case** | Verify commodity exposure and supply / demand cycle. Distinguish "we benefit from oil" from "WTI is up X% and our hedge book is consistent with that exposure." |
| **PIT considerations** | EIA publishes weekly with a Wednesday release; use `release_date`, not `report_date`. Reserves disclosures are `filing_date`-bound. |
| **Free feasibility** | EIA / USGS free; high-quality satellite / storage proxies often paid. **Free MVP must use EIA first.** |
| **Data-quality risks** | Commodity exposure is hedged; raw commodity move ≠ company P&L move. Agents must read the hedge / production split. |
| **Suitable for** | **Quality-Long** primarily; **Surge-Short** for narrative-driven energy spikes. |

---

#### Adapter 8 — `auto_mobility_adapter` *(Auto / EV / Mobility)*

| Item | Value |
|---|---|
| **Industries** | Auto OEMs, EV makers, charging networks, mobility / rideshare |
| **Source(s)** | NHTSA recall database, dealer-inventory proxies (when licensed), state DMV registration data where free, public charging-network or app data when accessible, SEC 10-Q for production updates |
| **Expected features** | `recall_count_12m`, `recall_severity_band`, `dealer_inventory_days_supply_band`, `state_registration_growth_band` (limited coverage), `charging_network_uptime_band`, `production_volume_qoq_change`, `delivery_volume_qoq_change` |
| **Use case** | Verify demand, quality issues, production stress. Distinguish "deliveries beat" from "registrations match deliveries and recalls are not spiking." |
| **PIT considerations** | NHTSA recalls are timestamped — `pit_safe`. Dealer inventory is weekly / monthly snapshot. |
| **Free feasibility** | NHTSA free. Registration data varies by state (some free, some paid). Inventory often paid. |
| **Data-quality risks** | Scraping limitations are real; respect robots.txt / ToS / rate limits. Recall volume ≠ recall severity (a single severe recall outweighs many minor ones). |
| **Suitable for** | **Surge-Short** (testing demand-spike narratives, recall-driven moves) and **Quality-Long** (long-run production discipline). |

---

#### Adapter 9 — `default_sec_gdelt_adapter` *(Default / fallback)*

| Item | Value |
|---|---|
| **Industries** | Any ticker for which no specialized adapter applies, or for which the specialized adapter returned `features_missing: ["all"]`. |
| **Source(s)** | SEC EDGAR (8-K, 10-Q, 10-K, S-1, S-3), GDELT global news event database, FMP (`/quote`, `/profile`, `/historical-price`, `/calendar`) |
| **Expected features** | All of feature groups A (news / event attention) + B (narrative specificity) + C (filing / disclosure confirmation) + E (fundamental / valuation context). The default adapter is essentially the *unmodified* Narrative-Price Dislocation Verification framework. |
| **Use case** | Default narrative / event validation for all companies when no specialized adapter exists. Always available as a fallback. |
| **PIT considerations** | SEC `filing_date` rule applies. GDELT events are timestamped. FMP TTM snapshots remain `live_snapshot_only`. |
| **Free feasibility** | SEC EDGAR and GDELT are free. FMP is the project's existing paid feed. |
| **Data-quality risks** | Coverage is broad but not deep — the default adapter is a *minimum*, not an answer. When a candidate triggers a specialized industry, the corresponding adapter should be preferred. |
| **Suitable for** | **Surge-Short** and **Quality-Long** — both. Always available. |

---

### 9.4 Adapter selection logic (sketch — not implemented)

The Alt-Data Verification Agent receives, as part of its evidence packet, a `primary_industry` hint (derived from FMP `/profile.industry` mapping; mapping itself is curated, not derived from the LLM). It selects an adapter as follows:

```
if primary_industry maps to a specialized adapter AND adapter has data:
    use that adapter
elif primary_industry maps to a specialized adapter BUT adapter returned features_missing: ["all"]:
    fall back to default_sec_gdelt_adapter
    set fallback_to_default_adapter = true
else:
    use default_sec_gdelt_adapter
```

Two adapters MAY run in parallel (for example: a software ticker uses both `software_devtools_adapter` and `default_sec_gdelt_adapter`); in that case the agent's `selected_adapter_id` is the primary one and the default's findings are folded into `industry_specific_evidence_used`.

### 9.5 Hard rules for every adapter — REMOVED (extracted to `RULES.md` §11.6–§11.11)

The 6 hard adapter rules (descriptors-not-trading-rules / agent-interprets-in-context / missing=Data-unavailable-never-zero / honest-source-labels / promotion-path / explicit-authorization) live in `RULES.md` §11.6, §11.7, §11.8, §11.9, §11.10, §11.11. Note: §11.11's "not connected today" wording was superseded by the user's 2026-04-28 directive — OpenCLI runtime integration is in-scope; rules in `RULES.md` §17 are ACTIVE with `INTEGRATION_STATUS: PENDING`.

### 9.6 Roll-out staging (proposed; **not authorized this round**)

| Stage | What's connected | Gate |
|---|---|---|
| **3B (today)** | Documentation only — registry + UI placeholder. Live alt-data sources connected: **0**. | — |
| 3C | `default_sec_gdelt_adapter` skeleton: mock data fixtures + agent reads them; SEC + GDELT calls still NOT made. | Mock fixtures + unit tests + audit-log capture. |
| 4A | `default_sec_gdelt_adapter` first live wire: SEC EDGAR (rate-limited, cached, source-flagged). GDELT: postponed. | New adapter file under `src/data_adapters/sec_adapter.py` with the same discipline as `fmp_adapter.py`. Frozen-rules bump v0.6 if any rule reads SEC fields. |
| 4B | Add `software_devtools_adapter` (GitHub public API, npm, PyPI). | Additional rate-limit / cache config; mapping audit. |
| 4C onwards | One specialized adapter per phase, in order: biotech → semiconductor → bank-regulatory → consumer-traffic → energy-commodity → airline-travel → auto-mobility. | One frozen-rules version bump per adapter that lands rule-reading code. |

> **None of stages 3C-4C are authorized today.** This staging exists only to make the discipline that must precede each one explicit.

---

### 9.7 Optional auxiliary adapter — `opencli_public_web_adapter` *(added 2026-04-26, OpenCLI-Prep phase)*

This is **not** an additional industry-specific adapter. It is an **optional auxiliary tool** the agent may invoke when the primary industry adapter (Adapter 1-9 above) cannot supply a piece of corroborating evidence and an official API does not exist.

> See companion docs: `docs/OPENCLI_INTEGRATION_PLAN.md`, `docs/OPENCLI_SKILLS_NOTES.md`. Schema lives in `docs/EVIDENCE_PACKET_V1_DRAFT.md` §8c.

| Field | Value |
|---|---|
| **Adapter ID** | `opencli_public_web_adapter` |
| **Status (today)** | docs only · not connected · disabled |
| **Tool** | OpenCLI (https://github.com/jackwener/opencli) — uniform `opencli <site> <command>` surface over public sites and (when authorized) browser sessions. |
| **Industries** | All — used as a *cross-cutting auxiliary*, not as the primary adapter for any ticker. |
| **Purpose** | Optional collection of public/community/web evidence when official APIs are unavailable or insufficient. |
| **Potential use cases** | Reddit community size and mention activity (public anonymous endpoints) · HackerNews developer attention · Twitter/X public sentiment **only if** legally and technically accessible without login · public webpage evidence extraction (read-only) · public community size · public product/community discussion · developer-ecosystem corroboration when no clean API exists. |
| **Strategy preference** | `PUBLIC` only by default. `COOKIE`/`HEADER`/`UI` strategies (logged-in flows) require explicit per-task human authorization. |
| **Source flag** | `live_opencli` on success · `live_opencli_failed` on failure · `not_connected` everywhere today. |
| **Free feasibility** | Free in principle (anonymous public reads). Costly in operational risk (DOM drift, ToS, rate limits). |
| **PIT considerations** | Most OpenCLI reads return *current-state* data. They are **not point-in-time-safe** unless the page itself carries an immutable timestamp (e.g. a Reddit post's creation time, an HN item ID), and even then only the post is anchored — surrounding context (votes, comment count) is current. Every OpenCLI block must carry a `PIT_safety_notes` line. |
| **Data-quality risks** | DOM/API drift; soft 404s and empty results that look successful; rate limiting and CAPTCHA returning HTTP 200 + empty payload; bot-detection masking content; sentiment bias / brigading; sock-puppet activity inflating community size; site policy changes. |
| **Sleeve suitability** | Useful as **corroboration** for *Surge Short v0.4* candidates (retail attention, meme-burst evidence) and for *Quality Long* candidates' qualitative narrative checks. **Never** the primary evidence for either sleeve. |

#### Warnings

- OpenCLI output is **evidence only** — never a hard-coded trading rule.
- OpenCLI output **must not** directly trigger `BUY` / `SHORT` / `WATCH` / `NO_TRADE`.
- Browser/session-backed outputs must be logged with the full command, source URL, query, timestamps, and an output hash.
- Any logged-in or session-backed access requires **explicit, per-task user authorization** that is itself audit-logged.
- ToS and site-policy review is a precondition for adding any new site under this adapter.
- If an OpenCLI read cannot be validated (auth, parse error, soft 404, rate limit, DOM drift, empty payload), the agent records `Data unavailable / not evaluated`, not zero.
- A `data_quality_warning` of `severity = critical` involving OpenCLI evidence collapses the agent's `recommendation_to_pm` to `needs_more_evidence`.
- Use `source_reliability` labels per source: high (clearly public + well-documented schema), medium (public but DOM-dependent), low (community-driven content, sentiment-prone), unknown (default for any new site).

#### Relationship to the existing framework

OpenCLI **can** contribute to:

- `alternative_data_features.market_sentiment` (auxiliary corroboration only)
- `alternative_data_features.community_size_metrics`
- `alternative_data_features.mention_activity` (meme/retail attention)
- `industry_specific_evidence_used` for `software_devtools_adapter` (developer-community corroboration)
- `news_event_summary` corroboration for any catalyst that has a public web URL

OpenCLI **does NOT replace**:

- SEC filing confirmation — SEC EDGAR remains authoritative.
- GDELT / news-event evidence — GDELT remains authoritative.
- FMP price/fundamental context — FMP remains authoritative.
- Risk/PM Agent 07's final-decision authority — unchanged.
- Point-in-time safety checks — OpenCLI evidence has its own PIT note and is generally weaker than filing-anchored or daily-bar-anchored sources.

#### Promotion path

`opencli_public_web_adapter` follows the same promotion path as Adapters 1-9 (§9.5 rule 5): **doc → schema in `EVIDENCE_PACKET_V1_DRAFT` §8c and `AGENT_OUTPUT_SCHEMA_DRAFT` Agent 03 → mock fixtures → adapter wrapper with rate-limit + cache + sticky-pause + redacted logging → integration test → human review → frozen-rules version bump only if any rule reads OpenCLI fields**.

It also has its own OpenCLI-internal staging (OC-1 → OC-7) in `docs/OPENCLI_INTEGRATION_PLAN.md §5`. The two staging tracks must agree before any commit lands.

> **Today: documentation only. Adapter status `not_connected`. No OpenCLI command runs from the production pipeline. No live alt-data row in any evidence packet currently uses OpenCLI.**

---

## 10. Information Integrity and Pollution Defense Layer *(added 2026-04-26)*

> **§10 — Rules extracted to `RULES.md` §10 (Information Integrity & Pollution Defense).** The 12 operational rules from this section (core principle + 5-tier source hierarchy + pollution-risk collapse + social_only_warning + coordinated_campaign_warning verifier output rule + use_as_primary_signal_allowed default-false + OpenCLI auxiliary-only + critical data-quality blocking + opencli_pit_safety_warning required + "whale" literal forbidden) live in `RULES.md` §10.1 through §10.12. The descriptor schemas, definitions, and rationale prose below remain authoritative for design and paper-writing reference. Where this section's prose contradicts `RULES.md` §10, `RULES.md` wins.

This layer sits **across** every adapter (Adapters 1-9 in §9 and the auxiliary `opencli_public_web_adapter` in §9.7). It is not a new adapter; it is a **classification + verification discipline** the Alt-Data Verification Agent applies to every claim before that claim is allowed to support a thesis.

### 10.1 Purpose

Prevent **polluted, manipulated, coordinated, low-quality, or fabricated** alternative-data signals from being treated as reliable investment evidence.

### 10.2 Core concern

A hedge fund, activist short seller, coordinated group, bot network, or rumor campaign may flood Reddit / X / Facebook / forums / Discord-like communities / public discussion boards with negative or positive claims about a company. The volume of those claims is real; the *truth* of those claims is not.

### 10.3 Core principle (non-negotiable)

> **Social/community data may indicate attention. It cannot, by itself, confirm a thesis.**

Two corollaries:

1. **Sentiment is a descriptor, never a decision rule.** No rule of the form *"negative sentiment ⇒ short"* or *"positive sentiment ⇒ buy"* is allowed in this system, today or ever, in any frozen-rules version.
2. **High attention + low credibility = pollution risk, not alpha.** A spike in mentions without a primary-source anchor is treated as a **risk flag**, not as a signal direction.

### 10.4 Source hierarchy (5 tiers)

The agent classifies every piece of alt-data evidence into one of these tiers. The tier governs how much *confirmation weight* the evidence can carry.

| Tier | Examples | Confirmation weight |
|---|---|---|
| **T1 — Primary / official** | SEC filings (`acceptedDate`/`filingDate`-anchored), regulator data (FDA, FAA, FTC, FERC, CFPB), official company disclosures (8-K, 10-K/Q, press releases on the issuer's IR site), exchange notices. | **High.** Can confirm a thesis. |
| **T2 — High-reputation corroboration** | Reputable financial news (WSJ, Reuters, Bloomberg, FT, Nikkei), audited earnings-call transcripts, verified press releases, official exchange/regulator notices. | **High.** Can corroborate T1; can confirm a thesis when T1 references it. |
| **T3 — Aggregated news / event data** | GDELT, news aggregators, industry trade publications, broad media coverage. | **Medium.** Can corroborate; alone usually insufficient. |
| **T4 — Community / social / forum** | Reddit, X/Twitter, Facebook / forum posts, HackerNews, public discussion boards. | **Low.** Attention flag only. Cannot confirm. |
| **T5 — Low-confidence / anonymous / duplicated** | Anonymous screenshots, repeated copy-paste claims, no-source claims, one-sided rumor bursts, suspicious coordinated narratives, unsigned PDFs, untraceable Telegram/Discord forwards. | **None.** Risk flag only. Cannot corroborate. |

#### Hard rules across the hierarchy

- T4/5 sources MAY raise an **attention flag** that draws the agent's investigation to a ticker.
- T4/5 sources MUST NOT confirm an investment thesis alone.
- A T4/5 claim becomes investable only when corroborated by T1/T2 *or* by an industry-specific hard-to-fake adapter signal (§9 Adapters 1-9).
- If corroboration is missing, agent output is `needs_more_evidence` or `unverified_claim`. Never `support_thesis`.
- A `pollution_risk_level = high` overrides any T4/T5 directional read and collapses the agent's recommendation to `needs_more_evidence` or `pollution_risk_high`.

### 10.5 Pollution-risk feature concepts (schema only — no formula, no threshold)

The following descriptors are recorded in `evidence_packet.information_integrity_assessment` (§8d) and consumed by the Alt-Data Verification Agent. **No scoring formula is locked in this round; no threshold is hard-coded.** They are concept handles for the agent to reason over.

| Concept | What it captures |
|---|---|
| `source_tier_distribution` | Histogram of how many evidence rows landed in each of T1-T5. |
| `primary_source_found` | Bool. Was at least one T1 row found for the *specific claim*? |
| `reputable_source_confirmation` | Bool. Was the claim corroborated by ≥1 T2 row? |
| `official_disclosure_confirmation` | Bool. Was a regulator/issuer disclosure located? |
| `claim_without_primary_source_ratio` | Fraction of T4/T5 rows lacking a T1/T2 anchor. |
| `near_duplicate_text_ratio` | Fraction of T4/T5 rows that are near-duplicates of each other. |
| `post_burstiness` | Concentration of posting activity within a short window vs. baseline. |
| `abnormal_post_velocity` | Posts per minute / hour vs. the source's own typical rate. |
| `low_reputation_source_share` | Share of evidence from accounts/sources with no credibility signal. |
| `account_age_distribution` | Distribution of source-account ages, when extractable. |
| `cross_platform_copy_paste_similarity` | Lexical similarity across platforms — same wording on Reddit + X + forums = coordinated risk. |
| `single_source_amplification` | Are many "voices" tracing back to the same originator? |
| `coordinated_campaign_risk` | Composite: high duplication + high velocity + low account age + same wording. |
| `bot_like_pattern_risk` | Heuristic: posting cadence regularity, generic phrasing, lack of profile depth. |
| `rumor_without_evidence_flag` | Bool. Claim is specific (named customer lost / lawsuit imminent / executive departure) but no T1/T2 anchor exists. |
| `evidence_credibility_score` | Agent-assigned, schema-only today (no formula). Range and units defined when implementation begins. |
| `pollution_risk_level` | `low \| medium \| high \| unknown`. Headline output of this layer. |

### 10.6 Agent interpretation procedure (claim-first, not signal-first)

The Alt-Data Verification Agent treats every alt-data row as **a claim to be verified**, not as a feature to be regressed.

```
1. Extract the claim.
   — "Company X lost major customer Y."
   — "Company X is delaying product Z."
   — "Company X is under SEC investigation."

2. Try to verify it against T1 sources.
   — SEC filings (8-K, 10-K, 10-Q, S-1, 13D/G).
   — Issuer press release on the IR site.
   — Regulator notice (FDA / FAA / FTC / FERC / CFPB).

3. If no T1 hit, try T2.
   — WSJ / Reuters / Bloomberg / FT.

4. Try industry-specific hard-to-fake adapters (§9.1-9.3).
   — software_devtools_adapter / semiconductor_patent_adapter / biotech_clinical_adapter / etc.

5. Compute the pollution-risk descriptors (§10.5).

6. Emit one of:
   — claim_status = corroborated
   — claim_status = partially_corroborated
   — claim_status = unverified
   — claim_status = contradicted
   — claim_status = not_evaluated
```

#### Decision mapping for the agent's `recommendation_to_pm`

| `claim_status` | `pollution_risk_level` | `recommendation_to_pm` |
|---|---|---|
| `corroborated` | low / medium | may be `support_thesis` *if other agent inputs agree* |
| `corroborated` | high | `needs_more_evidence` (corroboration exists but environment is polluted) |
| `partially_corroborated` | any | `needs_more_evidence` |
| `unverified` | low / medium | `needs_more_evidence` |
| `unverified` | high | `pollution_risk_high` |
| `contradicted` | any | `challenge_thesis` |
| `not_evaluated` | any | `not_evaluated` |

This table is the **only place** in the system where `pollution_risk_high` is produced. It exists specifically to surface the *"loud but not verifiable"* case — which is the textbook profile of a coordinated rumor attack.

### 10.7 Integration with existing layers

- The pollution-defense layer reads from every adapter (§9 + §9.7) and from `news_event_summary` / `filing_confirmation`.
- It writes only into `evidence_packet.information_integrity_assessment` (§8d) and Agent 03's optional output fields.
- It does **not** modify v0.5 frozen rules. It does **not** modify Surge Short v0.4. It does **not** modify regime allocation.
- It does **not** introduce a "social sentiment" feature into the rule engine. The rule engine continues to read only T1/T2-anchored facts and the existing price/macro/fundamental fields.
- A future frozen-rules version bump may introduce a `pollution_risk_blocks_buy` veto condition (analogous to `insufficient_evidence`), but **only** through the established doc → schema → prototype → human review → version-bump pipeline. Not in this round.

### 10.8 Status today (2026-04-26)

| Item | Status |
|---|---|
| Pollution-defense framework documented | **yes** (this section) |
| Tier classification implemented in code | **no** — concept handles only |
| Scoring formula for `evidence_credibility_score` | **not defined** |
| Threshold for `pollution_risk_level` | **not defined** |
| Live coordinated-campaign detection | **no** — no live social adapter is connected |
| Production rule reads any pollution-risk field | **no** |
| Frozen rules touched | **no** (`RULE_VERSION` still `v0.5_agentic_allocation_corrected`) |

---

## 11. Sentiment, Community Size, and Ownership Positioning Evidence *(added 2026-04-26)*

> **§11 — Hard rules extracted to `RULES.md` §11 (Alt-Data Source Rules) and §10.** The hard-rule subsections from §11.A (sentiment-is-descriptor-never-rule), §11.B (high-attention-low-credibility=pollution), §11.C (sentiment/community/ownership are descriptor-only layers; 13F PIT enforcement), §11.D (no-"whale"-literal), and §11.E (regime-allocation policy) live in `RULES.md` §11.13, §11.14, §10.2, §10.3, §10.12, and §4.5 respectively. The descriptor schemas, source-tier mappings, and definitions below remain authoritative for design and paper-writing reference. Where this section's prose contradicts `RULES.md`, `RULES.md` wins.

This section maps the four professor-requested project dimensions — **market sentiment**, **community size**, **top owners**, and **large/"whale" holders** — into the Alt-Data framework as **descriptor layers**. They are not adapters and they are not rules. They are evidence categories the Alt-Data Verification Agent reasons over and Risk/PM (Agent 07) treats as positioning context.

> Schema contract: `docs/EVIDENCE_PACKET_V1_DRAFT.md §8f (sentiment_community_ownership_evidence)`.
> Agent contract: `AGENT_OUTPUT_SCHEMA_DRAFT.md` Agent 03 + Agent 07 sentiment/community/ownership fields.
> Pollution-defense interaction: §10 above.

### 11.A Market Sentiment

**Definition.** Market sentiment is a **descriptive evidence layer**, not a trading rule. It captures how positively or negatively a corpus of news or community text is talking about a ticker — but not whether those claims are *true*, *primary-source-anchored*, or *unpolluted*.

**Possible future sources** (none connected today):

| Source | Tier (§10.4) | Notes |
|---|---|---|
| GDELT / news-tone aggregation | T3 | Free, broad, point-in-time-able if event timestamps are preserved. |
| Reputable financial news (WSJ / Reuters / Bloomberg / FT) | T2 | Authoritative when accessible; usually behind paywalls. |
| OpenCLI-assisted public web/community evidence | T4 / T5 (auxiliary) | See §9.7. Auxiliary corroboration only; never primary signal. |
| Reddit / X / HackerNews | T4 | Only when legally and technically accessible without login. |
| FMP `news` endpoint (if available under current plan) | T2/T3 depending on source | Inherits the existing FMP discipline (rate limits, cache, source flags). |

**Potential features** (schema-only this round; no formula, no threshold):

- `news_sentiment_score`
- `sentiment_change_1d`, `sentiment_change_3d`, `sentiment_change_7d`
- `negative_news_intensity`, `positive_news_intensity`
- `source_tier_weighted_sentiment` — sentiment averaged with §10.4 tier weights so T1/T2 dominates T4/T5
- `sentiment_pollution_risk` — composes §10.5 pollution descriptors with sentiment direction
- `sentiment_source_count`
- `sentiment_credibility_adjustment`

**Hard rule (non-negotiable, today and in any future frozen-rules version).**

> **Never implement "negative sentiment ⇒ short" or "positive sentiment ⇒ buy."**

Sentiment may *inform* an agent's verdict, *flag* a pollution risk, or *adjust* a position-size recommendation, but it does not — and will not — directly produce a trade direction in the rule engine.

### 11.B Community Size

**Definition.** Community size measures **public / developer / retail attention and engagement** around a ticker, product, or codebase. It does **not** prove investment-thesis validity.

**Possible future sources** (none connected today):

| Source | Tier | Notes |
|---|---|---|
| Reddit community size & mention count | T4 | Public anonymous endpoints only; ToS + rate-limit review required. |
| HackerNews discussion count | T4 | Public, anonymous. |
| GitHub stars / contributors / forks | T2-ish (developer T4 cross) | Authoritative for *codebase* attention, less so for *company* attention. |
| npm / PyPI download counts | T2-ish | Authoritative usage signal for software / dev-tool tickers. |
| OpenCLI-assisted public community extraction | T4 / T5 auxiliary | §9.7. |

**Potential features** (schema-only this round):

- `community_member_count`
- `mention_count_1d`, `mention_count_7d`, `mention_count_30d`
- `mention_zscore_60d`
- `developer_community_growth`
- `GitHub_contributor_count`
- `package_download_growth`
- `abnormal_community_attention_flag`
- `meme_attention_flag`

**Hard rule.**

> **Community attention can indicate attention, adoption, or speculation, but it cannot confirm a buy/short thesis alone.**

Community-size signals must be paired with §10.4 T1/T2 corroboration *or* with an industry-specific hard-to-fake adapter row (§9 Adapters 1-9) before they support a thesis. A spike in `mention_count_1d` without a primary-source anchor is a §10 pollution-risk flag, not a buy.

### 11.C Top Owners / Smart-Money Positioning

**Definition.** Ownership evidence is **positioning context**. It helps the agent and Risk/PM evaluate institutional sponsorship, crowding, hedge-fund interest, large-holder concentration, and squeeze risk. It is one of the most useful descriptor layers for both sleeves: for `quality_long` (institutional sponsorship), and for `surge_short` (crowding / squeeze risk).

**Possible future sources** (none connected today):

| Source | Notes |
|---|---|
| FMP institutional ownership / 13F endpoints | If available under the current plan; check `/stable/institutional-ownership/*` surface before connecting. |
| FMP ETF / mutual-fund holder endpoints | Same — verify availability + cost. |
| SEC 13F filings (public) | Authoritative public fallback; quarterly cadence; T1 source. |
| Insider ownership sources | Form 4 filings on EDGAR; T1. |
| Company proxy filings (DEF 14A) | Annual cadence; T1. |

**Potential features** (schema-only this round):

- `institutional_ownership_pct`
- `top_10_holder_concentration`
- `hedge_fund_holder_count`
- `hedge_fund_ownership_change`
- `new_13f_positions_count`
- `sold_out_positions_count`
- `ETF_MF_holder_exposure`
- `insider_ownership_pct`
- `ownership_crowding_flag`
- `smart_money_support_score`
- `squeeze_risk_from_crowding`

**PIT warning (critical).**

> **13F data is delayed.** It must be timestamped by `report_date`, `filing_date`, and `accepted_datetime` where available. Do **not** use current ownership data as if it were historically available. A packet built for `decision_timestamp = 2026-03-15T09:40:00-04:00` may only consume 13F rows whose `accepted_datetime ≤ 2026-03-15T09:40:00-04:00`. The most recent 13F before that cutoff is typically a Q4-prior or Q3-prior snapshot — that is the data the system was actually able to know at decision time.

This applies equally in `historical_replay` and `pre_market` / `opening_window` modes (§8e).

**Hard rules.**

> **Do NOT implement any of the following:**
>
> - "hedge fund owns it ⇒ buy"
> - "hedge fund sold it ⇒ short"
> - "high institutional ownership ⇒ safe"
> - "low ownership ⇒ bad"

Ownership signals are **descriptors**. They feed the agent's `ownership_positioning_assessment` and Risk/PM's `crowding_risk_flag` / `institutional_sponsorship_note`. They do not move the v0.5 frozen-rules buy/short gates and they will not in any future frozen-rules version without the standard doc → schema → prototype → human review → version-bump pipeline.

### 11.D Large Holders / "Whale" Translation for Equities

**Translation rule.** For equities, the crypto-style term **"whale holders"** is imprecise and risky (it implies behavioral significance the data does not always support). When the user, the dashboard, or an agent says "whales", the system translates it into **one or more** of the following equity-safe terms, each with a precise meaning:

| Equity-safe term | Meaning |
|---|---|
| **Large institutional holders** | 13F filers whose position in the ticker exceeds a (configurable) threshold of shares-outstanding. |
| **Concentrated 13F holders** | Holders whose ticker-specific position is in the top-N by shares or by % of float. |
| **Large insider holders** | Officers / directors / 10%-owners reported on Form 4, ranked by holding size. |
| **Hedge fund holders** | Subset of 13F filers classified as hedge funds in a known reference list. |
| **Ownership concentration** | Composite descriptor: `top_10_holder_concentration`, `top_25_holder_concentration`, `gini_holder` if computable. |
| **ETF / MF concentration** | Share of float held by ETFs and mutual funds. |
| **Smart-money / positioning evidence** | Summary descriptor combining 13F deltas + insider signals; **never** a buy/short trigger. |

These are **evidence descriptors**, not conclusions. Each can be high or low for many different reasons (passive-vs-active flows, index inclusion, share-class structure, recent IPO, secondary offering). The agent's job is to surface the descriptor and explain *why* it is what it is for this specific ticker; the rule engine's job is to remain unmoved by descriptors alone.

### 11.E Integration with existing layers

- The §11 layer reads from FMP ownership endpoints (when wired), SEC 13F (when wired), and §9.7 OpenCLI-assisted community/sentiment evidence (when wired).
- It writes only into `evidence_packet.sentiment_community_ownership_evidence` (§8f) and into Agent 03 / Agent 07 optional output fields.
- It interacts with §10 pollution defense: any community-size or sentiment row from a T4/T5 source goes through §10's claim-first verification before being allowed to support a thesis.
- It interacts with §8e decision-time discipline: every 13F / insider / sentiment row carries its own `as_of`, and packets reject rows whose `as_of > allowed_data_cutoff`.
- It does **not** modify v0.5 frozen rules. It does **not** modify Surge Short v0.4. It does **not** modify regime allocation. It does **not** introduce a sentiment-as-rule, an ownership-as-rule, or a community-size-as-rule into the engine.

### 11.F Status today (2026-04-26)

| Item | Status |
|---|---|
| Sentiment / community / ownership descriptor layer documented | **yes** (this section) |
| Live FMP ownership endpoint wired | **no** — feasibility check pending |
| Live SEC 13F adapter wired | **no** |
| Live insider-ownership (Form 4) source wired | **no** |
| News-sentiment scoring formula | **not defined** |
| Community-size aggregation pipeline | **not implemented** |
| `whale_holder` literal in code or rules | **none** — translated equity-safe terms only |
| Production rule reads any §11 field | **no** |
| Frozen rules touched | **no** (`RULE_VERSION` still `v0.5_agentic_allocation_corrected`) |
