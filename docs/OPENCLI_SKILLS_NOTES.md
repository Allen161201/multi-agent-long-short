# OpenCLI Skills — Learning Notes (Phase OpenCLI-Prep)

> **NOTE — Rules consolidated 2026-04-28 (D1 Step B).** The operational rules formerly in §4 ("MUST NOT be used yet"), §6 ("Behaviors to AVOID"), §7 ("Required output contract for ANY future OpenCLI evidence"), and §9 ("Why OpenCLI is optional and not the trading brain") have been extracted to `docs/RULES.md` §17 (OpenCLI Subsystem Rules). **For rule guidance, read `docs/RULES.md`.** This file's remaining sections (1, 2, 3, 5, 8, 10) are preserved as skill-installation context and adapter-class taxonomy. Per the user's 2026-04-28 directive, OpenCLI runtime integration is in-scope; rules in `RULES.md` §17 are ACTIVE with `INTEGRATION_STATUS: PENDING`.

**Date:** 2026-04-26
**Status:** Skills installed locally (5 skill docs in `~/.agents/skills/`). The `opencli` binary itself is **NOT** installed. No live OpenCLI command has been run from this project. No browser automation has been triggered. No website has been scraped. No personal account or login session has been used.

> **Role of OpenCLI in this project:**
> OpenCLI is an **optional alternative-data collection adapter layer**. It is **not** the trading brain, **not** the PM agent, **not** the rule engine, **not** broker execution. Any OpenCLI output is **evidence only** and must be interpreted by an Alt-Data Verification Agent and approved by the Risk/PM Agent before influencing a trade. OpenCLI output is never a hard-coded trading rule.

---

## 1. Repository inspected

- **Source:** https://github.com/jackwener/opencli
- **Inspection:** completed via WebFetch on 2026-04-26.
- **One-line description:** OpenCLI exposes websites, browser sessions, Electron apps, and local CLIs through a uniform `opencli <site> <command>` surface, returning structured envelopes (`json`, `yaml`, `csv`, `md`, `table`, `plain`) so agents can drive them without screen-scraping. ~90+ adapters cover platforms such as `hackernews`, `reddit`, `twitter`, `bilibili`, `xiaohongshu`, `zhihu`, `amazon`, `spotify`.
- **Strategies declared by adapters:** `PUBLIC` (no browser), `COOKIE`, `HEADER`, `INTERCEPT`, `UI`, `LOCAL`. Browser-backed strategies reuse the user's logged-in Chrome session via the OpenCLI Browser Bridge extension.
- **Exit codes:** Unix-style — `0` success, `66` empty result, `69` browser/bridge unavailable, `77` auth required.

## 2. Skills installed locally

Install command attempted (read-only, no network beyond the npm registry + the OpenCLI repo):

```bash
npx --yes skills add jackwener/opencli -y -g
```

Result: **all 5 skills installed successfully** to `~\.agents\skills\` and symlinked into Claude Code + Kiro CLI.

| # | Skill | Snyk risk | Description (one-line, paraphrased from SKILL.md) |
|---|-------|-----------|----------------------------------------------------|
| 1 | `opencli-adapter-author` | Med | Walks an agent end-to-end through writing a new adapter for a site (recon → API discovery → field decode → adapter scaffold → `opencli browser verify`). |
| 2 | `opencli-browser` | **High** | Low-level browser automation — open, click, type, select, find, extract, network capture. **This is the skill that drives a live Chrome window**, including logged-in flows. |
| 3 | `opencli-usage` | Med | Top-level orientation: what `opencli` is, install, the three pillars (adapter commands · browser driving · external-CLI passthrough), universal flags, env vars. |
| 4 | `opencli-autofix` | Med | Diagnose + auto-repair broken adapters when a site changes its DOM/API. Has hard stops on `AUTH_REQUIRED`, `BROWSER_CONNECT`, CAPTCHA. |
| 5 | `smart-search` | Med | Routing layer that picks the right OpenCLI source for a free-form search query (AI sources / social / tech / news / shopping / travel). Has a per-question call budget. |

The `opencli` binary itself was **NOT** installed. Skills give the agent the *knowledge* of how to use OpenCLI; they do not provide the runtime.

## 3. Which skills are relevant to this project

Relevant **for documentation and design only** at this stage:

- **`opencli-usage`** — defines the surface and output schema (`-f json`, structured envelopes, `match_level`, `matches_n`). This is the contract any future evidence-collection wrapper would consume.
- **`opencli-browser`** — relevant only when we eventually need to extract from a site that has no clean public API (e.g. a public Reddit thread, a public HackerNews item). **Driving a logged-in browser is out of scope today.**
- **`smart-search`** — relevant if we ever want a "given a ticker + narrative, find the most recent retail/dev attention signal" routing layer. Today, **we do not invoke any search.**

Relevant **but not used yet:**

- **`opencli-adapter-author`** — would be the path to writing a custom site adapter (e.g. for an industry-specific public dataset that's web-only). We are not authoring adapters today.
- **`opencli-autofix`** — relevant only after at least one OpenCLI command is wired into the pipeline and starts breaking on DOM drift. Not applicable yet.

## 4. Which skills MUST NOT be used yet — REMOVED (extracted to `RULES.md` §17.A.7–§17.A.9)

The 3 skill-gating rules (`opencli-browser` Bash-gating / `opencli-adapter-author` no-authoring / `opencli-autofix` no-patching) live in `RULES.md` §17.A.7, §17.A.8, §17.A.9.

## 5. Safe, read-only future use cases (illustrative — none authorized today)

If/when authorized, the following classes of OpenCLI calls are cleanly read-only and do not require a logged-in session:

| Class | Example | Strategy | Data privacy |
|---|---|---|---|
| Public anonymous endpoints | `opencli hackernews top -f json --limit 20` | `PUBLIC` | No login; respect site terms. |
| Public anonymous searches | `opencli hackernews search "<ticker>" -f json` | `PUBLIC` | Same. |
| Public site recon (read-only) | `opencli browser state` after `opencli browser open <public_url>` | `UI` (public) | No login required if URL is public. |
| Anonymous Reddit JSON | The legacy `*.json` Reddit endpoint via a `PUBLIC` adapter | `PUBLIC` | No login; subject to Reddit's API rate limits + ToS. |

The point: a clearly public, no-login, low-volume read can be useful for **community size / mention activity / developer attention** evidence — but only as **evidence descriptors** that an agent later interprets.

## 6. Behaviors to AVOID — REMOVED (extracted to `RULES.md` §17.A and §17.C)

The 7 avoid-behavior rules (no-logged-in-browser-flow / no-write-actions / no-personal-account-use / no-silent-retries / no-CAPTCHA-bypass / no-ToS-forbidden-scraping / no-production-pipeline-integration-as-decision) live in `RULES.md` §17.A.1–§17.A.6 and §17.C.1.

## 7. Required output contract for ANY future OpenCLI evidence — REMOVED (extracted to `RULES.md` §17.B.10)

The 14-field output contract and the failure semantics rule live in `RULES.md` §17.B.10 (output contract) and §17.B.8 (failure → `Data unavailable / not_evaluated`).

## 8. How OpenCLI could support the alt-data evidence framework (later)

Each row below is a *potential* support — none is wired today.

| Evidence type | OpenCLI adapter / skill | Maps to evidence-packet block |
|---|---|---|
| Market sentiment | `opencli grok` / `opencli doubao` summaries (AI-source) when official API unavailable | `alternative_data_features.market_sentiment` |
| Community size | `opencli reddit subreddit <name> -f json` (subscriber count, activity) | `alternative_data_features.community_size_metrics` |
| Reddit/HackerNews/X attention | `opencli hackernews top/search`, `opencli reddit hot/search`, `opencli twitter trending/search` | `alternative_data_features.mention_activity` |
| Developer community | Combined: official GitHub API (preferred) + OpenCLI HackerNews search as corroboration | `industry_specific_evidence_used` (software_devtools_adapter) |
| Public web corroboration | `opencli browser open / state / extract` against a public press release URL | Catalyst confirmation in `news_event_summary` |
| Public product/community discussion | Reddit / HackerNews search by ticker or product name | `alternative_data_features.qualitative_corroboration` |

**OpenCLI does NOT replace:** SEC filing confirmation (use SEC EDGAR directly), GDELT/news-event evidence (use GDELT directly), FMP price/fundamental context, Risk/PM decision authority, or the point-in-time safety of an evidence packet.

## 9. Why OpenCLI is *optional* and not the trading brain — REMOVED (extracted to `RULES.md` §17.B.1–§17.B.5)

The 5 system-intent rules (collection-not-decision-layer / descriptors-not-rules / fails-noisily / substitutable-with-official-API-preferred / frozen-rules-compatibility) live in `RULES.md` §17.B.1 through §17.B.5.

## 10. Status today

| Item | Status |
|---|---|
| OpenCLI repo inspected | **yes** (WebFetch, 2026-04-26) |
| Skill docs installed locally | **yes** (5 skills, `~\.agents\skills\opencli-*` and `smart-search`) |
| `opencli` binary installed | **no** |
| `opencli doctor` run | **no** |
| Browser Bridge extension installed | **no** |
| Live OpenCLI command run from this project | **no** |
| Public read-only OpenCLI demo run | **no** |
| Logged-in OpenCLI flow run | **no — and not authorized** |
| OpenCLI output integrated into evidence packet | **no** |
| Production pipeline integration | **no** |
| Frozen rules touched | **no** (`RULE_VERSION` still `v0.5_agentic_allocation_corrected`) |
| LLM connected | **no** |
