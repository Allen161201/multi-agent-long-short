# LLM Integration Layer (Step 5)

> **NOTE — Rules consolidated 2026-04-28 (D1 Step B).** The operational rules formerly in §2 (cache key derivation + candidate_type extension), §4b (topologies and cache semantics), and §5 (logging discipline) have been extracted to `docs/RULES.md` §15 (Cache Key) and §16 (Coordination Topology) and §19.17 (logging discipline). **For rule guidance, read `docs/RULES.md`.** The architecture diagram (§1), provider-switch mechanism (§3), and stub-→-Anthropic swap recipe (§4) are preserved as runtime/infrastructure context.

**Status:** Active. Provider = `deterministic_stub` (no real API). Real Anthropic wiring lands in Step 6.
**Schema authority:** `RULES.md` (canonical for rule statements); `src/agents/schemas.py` (canonical for pydantic models). The former `docs/AGENT_OUTPUT_SCHEMA_DRAFT.md` was deleted in B4 — rules from it landed in `RULES.md` §3, §5, §10, §11, §12.

## 1. Architecture (text diagram)

```
scripts/run_agents.py          # CLI front-end
        │
        ▼
src/evidence_packet/…  ───────►  evidence_packet (dict)
        │
        ▼
src/agents/runner.py
   run_all_agents_for_candidate(packet, candidate_type)
        │
        ├─► narrative_event ─────┐
        ├─► alt_data_verify ─────┤
        ├─► fund_net_val   ──────┼──► upstream_agent_outputs
        ├─► surge_short OR quality_long  ─┘
        └─► risk_pm  (consumes upstream_agent_outputs)
                │
                ▼
        run_agent(...)
                │
        ┌───────┴────────┐
        ▼                ▼
LLMCache.get      LLMProvider.complete
(disk JSON)             │
        ▲               ├─ DeterministicStubProvider  (Step 5 default)
        │               └─ AnthropicProvider          (Step 6)
        │                       (raises NotImplementedError today)
        │                       │
        ▼                       ▼
LLMCache.put              ProviderResponse
        │                       │
        └─► validate_agent_output(schema, payload)
                                │
                                ▼
                    parsed_output (dict, schema-valid)
                    +  validation_status: "ok" | "schema_failed_returned_needs_more_evidence"
```

## 2. Cache key derivation — REMOVED (extracted to `RULES.md` §15)

The cache-key formula, the storage layout, the no-eviction policy, and the candidate_type extension (Step A1, 2026-04-28) all live in `RULES.md` §15.1 through §15.7. `RULES.md` §15.5 is the corrected statement of the candidate_type extension and supersedes the prior phrasing here ("appends to the segment") — the actual code path builds a 7-pipe-token payload, not a modification of the 6th token.

## 3. Provider switch mechanism

```python
from src.llm.factory import get_provider
provider = get_provider()        # reads env LLM_PROVIDER, default "deterministic_stub"
```

Override at process scope:

```bash
export LLM_PROVIDER=anthropic    # or set in .env
```

Allowed slugs: `deterministic_stub`, `anthropic`. Unknown slugs raise `ValueError`. Calling `AnthropicProvider.complete(...)` today raises `NotImplementedError` so a misconfigured environment fails loudly rather than running real inference by accident.

## 4. How to swap stub → Anthropic in Step 6

For future-you executing Step 6:

1. **Install the SDK.** `pip install anthropic` (and pin a version in requirements).
2. **Uncomment two lines in `src/llm/anthropic_provider.py`:**
   - the `from anthropic import Anthropic` import at the top of the file
   - the `self._client = Anthropic(api_key=self._api_key)` line in `__init__`
3. **Implement `complete()`** in that class — call `self._client.messages.create(...)`, extract `resp.content[0].text` into `raw_text`, capture `resp.usage.input_tokens` / `output_tokens`, measure latency with `time.perf_counter()`, return a `ProviderResponse` TypedDict. The function-level docstring already lists the exact call shape.
4. **Set the env variables** (do not commit them):
   - `LLM_PROVIDER=anthropic`
   - `ANTHROPIC_API_KEY=...`
5. **Clear the LLM cache** for the agents whose `model_id` will change from `stub-v1` to a real Claude model id — the cache key includes `model_id`, so old records will not be served, but the stale files are dead weight: `rm -rf data/cache/llm/`.
6. **Re-run the validation harness** in `scripts/run_agents.py --ticker AAPL --candidate-type quality_long` and confirm:
   - `provider="anthropic"` in every cached `raw_response`
   - `validation_status="ok"` on every parsed output (if Claude returns malformed JSON, the runner's fail-closed branch records `schema_failed_returned_needs_more_evidence` — that signals a prompt-engineering issue, not a code bug)
   - cache hit rate on second run = 100%
7. **No prompt edits needed.** The system prompts already contain the anti-hindsight clause, the JSON-only output instruction, and the alt-data-emphasis clause. If you need to revise a prompt, bump that agent's `PROMPT_VERSION` per `docs/PROMPT_VERSIONING_POLICY.md` so old cache entries become unreachable but stay on disk for replay.

## 4b. Topologies and cache semantics — REMOVED (extracted to `RULES.md` §15 and §16)

The three-axis specification (`agent_mode`/`topology`/`enabled_blocks`) is in `RULES.md` §16.1–§16.6. The cross-topology cache semantics table is captured by `RULES.md` §15.5 (candidate_type extension), §15.6 (specialists unchanged across topologies), and §15.7 (`pm_flat` separate namespace).

## 5. Logging discipline — REMOVED (extracted to `RULES.md` §19.17)

The one-INFO-line-per-agent format and the rule that full prompts/packets never go to the log stream live in `RULES.md` §19.17.
