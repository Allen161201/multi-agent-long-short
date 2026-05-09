# LLM_ASSISTED_SYSTEM_DESIGN — methodology, not trading logic

> **NOTE — Rules consolidated 2026-04-28 (D1 Step B).** The operational rules formerly in §0 (7 hard rules for any LLM use), §9 (promotion-path firewall + rule-vs-suggestion test), and §10 (5 human-approval gates) have been extracted to `docs/RULES.md` §19 (Meta-Governance & Promotion Path). **For rule guidance, read `docs/RULES.md`.** This file's §1–§8 are preserved as LLM-engineering methodology and are source material for the paper's methodology / reproducibility section.

**Created:** 2026-04-26
**Scope:** how Large Language Models may assist us in **designing, testing, and improving** the Alt-Data Agentic Long-Short system.

> **This document is a system-engineering workflow.**
>
> - It is **NOT** trading logic.
> - It is **NOT** the production prompt that the eventual agent will run.
> - It does **NOT** authorize an LLM to change rules, code, risk limits, or trading decisions.
> - It does **NOT** authorize live LLM API calls today. No LLM is wired into the running pipeline. Agent 4.5 still emits the deterministic `needs_more_evidence` placeholder.

The user-provided "reverse engineering / LLM-assisted design" prompt list is adapted below into a project-specific engineering methodology.

---

## 0. Hard rules for any LLM use in this project — REMOVED (extracted to `RULES.md` §19.1–§19.7)

The 7 hard rules (no-generic-prompts / version-everything / log-and-human-approve / no-silent-changes / no-LLM-as-authority / no-LLM-execution / rule-engine-is-source-of-truth) live in `RULES.md` §19.1 through §19.7. Note: the §0 #7 wording "16:15 ET decision time" has been superseded by the 4-mode formulation per `RULES.md` §9.4 — the canonical wording in §19.7 reads "the four canonical decision modes (`pre_market` / `opening_window` / `end_of_day_surge` / `historical_replay`)" instead of the bare "16:15 ET decision time".

---

## 1. How LLMs assist architecture decomposition

**Goal.** Break a fuzzy product requirement ("agentic long-short with alt-data verification") into tractable modules with defined inputs / outputs / failure modes.

**LLM-assisted activities (non-runtime).**
- Brainstorm the agent topology (current: 5-agent sequential + Agent 4.5 placeholder).
- Identify orthogonal concerns (data ingestion vs. evidence packaging vs. agent reasoning vs. rule enforcement vs. risk PM vs. audit logging).
- Spot missing modules (e.g. "we have rate-limit + cache, do we have a cache-key rotation story?").
- Surface single-points-of-failure (e.g. "the dotenv override flag was missing").

**Output.** Markdown architecture sketches — never auto-merged.

**Forbidden.** Proposing a new agent that decides positions without going through Risk/PM. Proposing to bypass the rule engine "for speed."

---

## 2. How LLMs assist agent role design

**Goal.** Ensure each agent has a clear scope, a single responsibility, and can be evaluated independently.

For our 5-agent pipeline:

| Agent | Scope | Output schema (draft) |
|---|---|---|
| 01 Market Screener | Apply mechanical 50% / 1M / $2 / exclusion screen | candidate list |
| 02 Narrative / Event | Classify catalyst type from news / SEC | catalyst_type, specificity_score |
| 03 Alt-Data Verify | *Narrative-Price Dislocation Verification* | evidence_score, verdict |
| 04 Fund / Net / Val | Score fundamentals, network effect, valuation | quality scores, valuation_assessment |
| 05 Risk / PM | Apply rule engine + final decision | decision, position_size, audit_log |

**LLM-assisted activities.**
- Pressure-test role boundaries ("if the Narrative agent classifies catalyst, what should Alt-Data do if catalyst is unverifiable?").
- Draft input contracts ("the Alt-Data agent must receive `news_event_summary`, `filing_confirmation`, and `catalyst_type` from the previous agent").
- Generate adversarial scenarios for unit tests.

**Forbidden.** An LLM defining new agents at runtime, or reordering the pipeline based on a per-ticker prompt.

---

## 3. How LLMs assist prompt-template design

**Goal.** Build production prompts that are deterministic in *structure* even if the model output varies in *content*.

**Required template anatomy** (every production prompt must have these sections):
1. **System role** — restate guardrails in one paragraph (16:15 ET, T+1, no margin, …).
2. **Decision domain boundary** — exactly what the agent decides vs. what the rule engine enforces.
3. **Evidence packet (input)** — JSON, timestamped, marked `data_available_as_of`.
4. **Reasoning instruction** — "base your analysis only on the information available as of {decision_timestamp}; do not use knowledge after this date."
5. **Output schema** — a strict JSON schema with required fields, including `confidence`, `evidence_used`, `evidence_missing`, `invalidation_conditions`, `schema_version`, `prompt_version`.
6. **Refusal clause** — when evidence is insufficient, the agent must emit `recommended_action: needs_more_evidence` and explain *why*.

**LLM-assisted activities.**
- Iterate prompt wording for clarity.
- Generate adversarial inputs that test "evidence insufficient" path.
- Spot ambiguous instructions.

**Forbidden.** Inserting a phrase like "use your best judgment" or "act as an experienced trader" at production. Such phrases delegate guardrails away from the rule engine.

---

## 4. How LLMs assist schema design and JSON validation

**Goal.** Every agent output passes machine validation before reaching Risk/PM.

**Engineering practice.**
- Schema lives in `src/agents/agent_decision_schema.py` (already exists for surge-short, draft for the other agents — see `docs/AGENT_OUTPUT_SCHEMA_DRAFT.md`).
- A pre-PM validator rejects malformed JSON. On rejection, Risk/PM treats the agent as `needs_more_evidence` and never trades.
- Each prompt version is paired with one schema version.

**LLM-assisted activities.**
- Suggest field names and enums.
- Spot fields that are ambiguous or under-constrained.
- Generate negative test cases (missing fields, wrong types, out-of-range numerics).

**Forbidden.** Loosening a required field to "optional" because a model failed to populate it. Failure modes are signal, not friction.

---

## 5. How LLMs assist PM aggregation design

**Goal.** Risk / PM agent (Agent 05) is the *only* place where multiple agent decisions become a position size and an action.

**Engineering practice.**
- PM aggregation is rule-based, not LLM-based. The LLM may *suggest* aggregation logic; the actual aggregation lives in `src/agents/risk_pm.py` and `config/rules.yaml`.
- PM enforces every veto condition: insufficient evidence, hard risk limit breached, missing data persists beyond tolerance.
- PM does NOT re-introduce the "regime restricts equity on quality score" logic that v0.5 deliberately removed.

**LLM-assisted activities.**
- Propose aggregation rules for new agent outputs (e.g. how `narrative_price_gap_assessment` should weight against `quality_score`).
- Identify failure modes ("what if the alt-data agent is confident but the fund agent is not?").
- Sanity-check that no proposed rule conflicts with v0.5.

**Forbidden.** PM as a stochastic LLM that sometimes overrides the rule engine.

---

## 6. How LLMs assist backtest design

**Goal.** Backtests must be honest about look-ahead, transaction costs, survivorship, and post-publication decay.

**Engineering practice (pre-LLM today; LLM-assisted in design only).**
- T+1 execution: signal at Day-T close → trade at Day-T+1 open.
- Filing-date rule: 10-Q / 10-K usable only after `filing_date`.
- News-rule: after-close news → trade next trading day.
- Missing data → "Data unavailable", never zero.
- Transaction-cost model (planned): commissions + bid-ask + price impact + short-sale rebate.

**LLM-assisted activities.**
- Draft adversarial test scenarios (a 10-K with future-dated `filing_date`; a quote whose `timestamp_unix` is in the future; a regime-change boundary day).
- Spot look-ahead leaks in evidence-packet construction.
- Suggest holdout-period structures (rolling-window vs. block-bootstrap).

**Forbidden.** An LLM "running" a backtest interactively and reporting numbers. Backtests run in code; results land in `outputs/audit_logs/` with a frozen rule version stamped in.

---

## 7. How LLMs assist error handling and testing

**Goal.** Every error path is named, logged, and recoverable or escalable.

**Engineering practice.**
- Error classes are typed: `MissingApiKey`, `RateLimitPaused`, `HTTP4xx`, `InvalidJSON`, `FMPError`, `SchemaValidationFailed`.
- Sensitive data (API key) is redacted at every logging boundary (`_redact()`).
- Sticky-pause + cache + dashboard quota flag are clearable via the `/api/fmp/clear_cache` operator endpoint.

**LLM-assisted activities.**
- Generate error-path test cases (rate-limit hit during a multi-block ticker_inspector call; an HTTP 401 mid-pipeline; a partial JSON body).
- Suggest retry / backoff policies — but the rule engine and rate limiter remain the authority on actual retry behavior.
- Review log-line drafts to make sure no key / no PII / no sensitive value can leak.

**Forbidden.** An LLM auto-applying a fix to production code. The path is always: suggestion → diff → human review → commit.

---

## 8. How LLMs assist cost optimization

**Goal.** Keep FMP, OpenAI / Anthropic, FRED costs predictable.

**Engineering practice.**
- FMP rate-limit cap (600 / min internal vs. 750 / min plan ceiling).
- TTL cache (`quote 5min · profile 24h · fundamentals 12h · search 12h · ...`).
- Search debounce 450ms + min-2-char gate before any `/api/fmp/search` call.
- Pipeline auto-run is **disabled**; `/api/run` is manual-only.

**LLM-assisted activities.**
- Audit call-group counters (`call_groups`) for unexpected spikes.
- Suggest where caches should be coarser / finer.
- Estimate the per-ticker LLM token budget under a planned prompt design.

**Forbidden.** An LLM authorizing its own retries beyond the rate limiter. An LLM raising the rate-limit cap.

---

## 9. Promotion path firewall — REMOVED (extracted to `RULES.md` §19.8 and §19.9)

The promotion path (`LLM suggestion → docs/<topic>.md → schema → prototype → PR → frozen-rules bump → active rule`) is `RULES.md` §19.8. The rule-vs-suggestion test is `RULES.md` §19.9. The anti-patterns (`momentum_score >= 70` example, etc.) are illustrative and live as guidance — not as rules — in this file's history; they are not extracted to RULES.md.

---

## 10. Human approval gates — REMOVED (extracted to `RULES.md` §19.10)

The 5 human-approval gates (documentation gate / schema gate / test gate / versioning gate / operator-readiness gate) live in `RULES.md` §19.10.

---

## Summary

LLMs help us *think faster and more thoroughly* about architecture, schemas, error paths, and tests. They do **not** decide trades, change rules, or take operator-level actions. The discipline that has produced v0.5 (frozen-rules versioning, audit logs, source flags, manual pipeline runs, redacted logging) is exactly what keeps LLM assistance safe.
