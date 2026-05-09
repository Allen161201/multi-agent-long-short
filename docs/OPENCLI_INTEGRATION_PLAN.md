# OpenCLI Integration Plan (Phase OpenCLI-Prep — 2026-04-26)

> **NOTE — Rules consolidated 2026-04-28 (D1 Step B).** The operational rules formerly in §2 ("What OpenCLI is NOT" — 6 negative-scope rules) and §4 ("Safety rules" — 10 numbered rules) have been extracted to `docs/RULES.md` §17 (OpenCLI Subsystem Rules). **For rule guidance, read `docs/RULES.md`.** This file's remaining content is preserved as integration-plan / staging context. Per the user's 2026-04-28 directive, OpenCLI runtime integration is in-scope; rules in `RULES.md` §17 are ACTIVE with `INTEGRATION_STATUS: PENDING`. The status table in §6 below is historical (as of 2026-04-26) — do not treat it as authority for current integration state.

This is a **plan**, not an implementation. No live OpenCLI command runs from this project. No website is scraped. No browser session is automated. No personal account is used. No production pipeline integration is wired.

> **Companion docs:** `docs/OPENCLI_SKILLS_NOTES.md` (skill summaries — also a `RULES_FILE_PARTIAL`, see banner there).

---

## 1. Purpose

OpenCLI is an **optional, read-only web/community/public-data extraction layer** for cases where no clean official API exists. Specifically:

- It supplements (it does not replace) official APIs (FMP, FRED, SEC EDGAR, GDELT, GitHub).
- It is the candidate fallback when a piece of evidence is only available as a public webpage, a public discussion thread, or a public community feed.
- It is a tool the **Alt-Data Verification Agent** (Agent 03) may consult during the *Narrative-Price Dislocation Verification* loop. The agent — not OpenCLI — interprets the evidence.
- All OpenCLI output is wrapped in the evidence-packet schema with provenance fields (URL, command, query, timestamp, schema version, extraction status, source reliability, ToS notes, PIT note, output hash).

## 2. What OpenCLI is NOT — REMOVED (extracted to `RULES.md` §17.C)

The 6 negative-scope rules (NOT-the-trading-brain / NOT-the-PM-agent / NOT-the-rule-engine / NOT-broker-execution / NOT-substitute-for-PIT-evidence / NOT-allowed-to-silently-modify) live in `RULES.md` §17.C.1 through §17.C.6.

## 3. Proposed future use cases (none authorized today)

When and only when explicitly authorized, OpenCLI may support:

- **Market sentiment collection** — public AI-source summaries (e.g. `opencli grok` / `opencli doubao`) treated as *one* corroborating signal, never as a primary thesis.
- **Community size extraction** — public subreddit subscriber counts, public HackerNews submission counts, public dev-community headcount where the data is publicly displayed.
- **Reddit / HackerNews / Twitter/X attention** — *only* via clearly public, anonymous endpoints, *only* in volumes that respect each site's ToS and rate limits, and *only* with explicit per-site authorization.
- **Developer community evidence** — corroboration of GitHub API signals via HackerNews / dev-Reddit search; the GitHub official API remains the primary source.
- **Public webpage evidence extraction** — read-only `opencli browser open / state / extract` against a public press-release URL the system already cites in `news_event_summary`.
- **Public product/community discussion** — Reddit / HackerNews search of the *public* threads referenced by the catalyst event; never private subreddits, never DM contents.
- **Optional fallback when official APIs do not exist** — a clearly bounded last resort, never the first source.

## 4. Safety rules — REMOVED (extracted to `RULES.md` §17.B)

The 10 enforced safety rules (read-only first / no logged-in automation / no credentials / no personal-account use / no trading actions / no hidden scraping / respect ToS / deterministic JSON output / required output fields / failure → Data unavailable) live in `RULES.md` §17.A and §17.B. Each carries `INTEGRATION_STATUS: PENDING` per the 2026-04-28 user directive.

## 5. Proposed future integration stages

Numbering is **OpenCLI-internal**; it does **not** consume any of the project's existing 3B → 3C → 4A-C adapter stage numbers. None of the stages below is authorized today.

| Stage | What's done | Gate |
|---|---|---|
| **OC-1 Docs only** *(today)* | Skill docs installed; integration plan + framework + evidence-packet + schema drafts updated to mention OpenCLI as optional. | — |
| **OC-2 Skill review** | Read each installed `SKILL.md`. Done as part of OC-1 today. | — |
| **OC-3 Local doctor** | Run `opencli doctor` once locally to confirm bridge / extension status. **No data fetched.** | Explicit user authorization. |
| **OC-4 Public read-only demo** | One single public command, e.g. `opencli hackernews top -f json --limit 5`, captured + hashed + reviewed offline. **No tickers, no narratives, no trading context.** | Explicit per-command authorization. |
| **OC-5 Schema validation** | Validate the captured demo output against the proposed `opencli_evidence` schema. Identify gaps, propose schema edits. **Still no pipeline.** | Schema review. |
| **OC-6 Evidence-packet staging** | Add an `opencli_evidence` block to a prototype evidence packet behind a feature flag (`enable_opencli_evidence: false` by default). Not consumed by any agent. | Schema lock + audit-table extension. |
| **OC-7 Alt-Data Agent integration** | Allow Agent 03 to read `opencli_evidence` and emit the optional fields. Risk/PM still rules-only. | Frozen-rules version bump (e.g. `v0.6_evidence_packet_v1` or `v0.7_opencli_evidence`) **only** if a rule references OpenCLI fields, plus end-to-end test, plus human review. |

Stages OC-3 → OC-7 are gated. Each requires its own go-ahead in a separate task prompt.

## 6. Current status (snapshot 2026-04-26 — historical, do not treat as canonical)

> **Per the 2026-04-28 user directive, OpenCLI is in-scope. The status table below is the as-of-2026-04-26 snapshot; canonical integration status is `INTEGRATION_STATUS: PENDING` per `RULES.md` §17.**

| Item | Status (as of 2026-04-26) |
|---|---|
| Skills installed (`opencli-adapter-author`, `opencli-autofix`, `opencli-browser`, `opencli-usage`, `smart-search`) | **installed** to `~\.agents\skills\` |
| Skill docs reviewed | **yes** — see `docs/OPENCLI_SKILLS_NOTES.md` |
| `opencli` binary installed (`npm install -g @jackwener/opencli`) | **not installed** |
| Browser Bridge Chrome extension installed | **not installed** |
| `opencli doctor` ever run | **not run** |
| Live OpenCLI commands | **disabled** |
| Browser automation | **disabled** |
| Logged-in sessions | **disabled** |
| Production integration | **disabled** |
| Evidence-packet integration | **not started** |
| Frozen rules touched | **none** (`RULE_VERSION` still `v0.5_agentic_allocation_corrected`) |
| LLM connected | **no** |
| External APIs newly connected | **none** |
| Heavy FMP calls | **none** |
| Pipeline / backtest / universe scan run | **none** |
