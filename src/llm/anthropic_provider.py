"""
AnthropicProvider — Task 7 (2026-04-29 PM): real LLM wiring.

Drop-in for DeterministicStubProvider. Same ProviderResponse shape; same
.complete() signature. The orchestrator picks one or the other purely
via env var LLM_PROVIDER ∈ {stub, deterministic_stub, anthropic}.

Design points:
  - Default model: claude-sonnet-4-6 (Sonnet 4.6, $3 / $15 per MTok).
  - Cheaper calibration model: claude-haiku-4-5-20251001 ($1 / $5).
  - Premium escalation: claude-opus-4-7 ($5 / $25). Caller-explicit only.
  - Per-call model override is supported via the model_id kwarg, so a
    calibration sweep can choose Haiku and production can choose Sonnet
    without prompt edits.
  - Prompt cache: every call sends `system` as a list with a single
    `cache_control: {type: "ephemeral"}` block. The system prompt is the
    long, static portion; the per-call evidence-packet payload lives in
    the user message and is NOT cached. 5-minute ephemeral TTL — we
    intentionally never use the 1-hour beta tier; the default cadence is
    well within 5 minutes for live runs.
  - 60 second per-call timeout. On timeout we re-raise the underlying
    SDK error wrapped as AnthropicProviderError so the runner sees a
    clear context. No silent retry.
  - Token + cost accounting: every successful call records the four
    token classes (uncached / cached_read / cached_write / output) and
    a USD cost computed from the module-level PRICING_USD_PER_MTOK
    table. Operators can update pricing in one place when Anthropic
    changes a list price.
  - Cost guard 1 (cumulative): a process-local running USD total is
    incremented after every call. If the cumulative crosses the hard
    stop (default $5.00, env ANTHROPIC_HARD_STOP_USD), the next call
    raises before sending. This is the safety net for calibration loops
    and replay sweeps.
  - Cost guard 2 (per-call): if the estimated input tokens (count_tokens
    via the SDK, falling back to char/4 heuristic) exceed the per-call
    cap (default 50_000, env ANTHROPIC_MAX_INPUT_TOKENS), we abort the
    call BEFORE sending. This catches accidentally bloated evidence
    packets without paying for them.
  - Logging discipline: NEVER log the API key. NEVER log the system
    prompt body or the user-prompt body. Logs include model id, token
    counts, cost, latency, cache hit/write counts, and an 8-char sha256
    fingerprint of the API key (so operators can confirm the right key
    is loaded after a rotation without ever exposing the secret).

Compatibility with DeterministicStubProvider:
  - Both accept the same kwargs to .complete().
  - Both return a ProviderResponse TypedDict.
  - The runner does not branch on provider name — it consumes
    raw_response["raw_text"] and parses JSON.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any

from .provider import LLMProvider, ProviderResponse

logger = logging.getLogger(__name__)


# ── Pricing (USD per million tokens, list price as of 2026-04-29) ────
# Operators updating pricing change ONLY this table. The provider's cost
# computation reads from here at call time, never bakes a number into a
# call site.
PRICING_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "input_cached_read": 0.30,
        "input_cached_write": 3.75,
        "output": 15.00,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "input_cached_read": 0.10,
        "input_cached_write": 1.25,
        "output": 5.00,
    },
    "claude-opus-4-7": {
        "input": 5.00,
        "input_cached_read": 0.50,
        "input_cached_write": 6.25,
        "output": 25.00,
    },
}

DEFAULT_MODEL = "claude-sonnet-4-6"
HAIKU_MODEL = "claude-haiku-4-5-20251001"
OPUS_MODEL = "claude-opus-4-7"

DEFAULT_HARD_STOP_USD = 5.00
DEFAULT_MAX_INPUT_TOKENS = 50_000
DEFAULT_TIMEOUT_S = 300  # bumped 60→300 (2026-05-03 robustness fix);
                          # PM calls under Pass 6 prompts can take >60s
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.0


# ── Errors ────────────────────────────────────────────────────────────
class AnthropicProviderError(RuntimeError):
    """Raised by AnthropicProvider on any non-recoverable failure
    (missing key, timeout, hard-stop trip, per-call cap trip, model not
    in pricing table, malformed SDK response). The runner translates
    these into a clear log + abort; we never silently fall back to the
    stub."""


# ── Cost ledger (process-local) ───────────────────────────────────────
class _CostLedger:
    """Process-singleton USD spend tracker.

    A single process invocation accumulates spend across however many
    .complete() calls happen. The hard-stop check fires synchronously on
    every call, so a long-running calibration cannot accidentally bleed
    past the cap.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_usd = 0.0
        self._calls: list[dict[str, Any]] = []

    def add(self, *, model: str, input_uncached: int, cached_read: int,
            cached_write: int, output: int, cost_usd: float,
            latency_ms: int, agent_schema_name: str | None) -> None:
        with self._lock:
            self._total_usd += cost_usd
            self._calls.append({
                "model": model,
                "input_tokens_uncached": input_uncached,
                "input_tokens_cached_read": cached_read,
                "input_tokens_cached_write": cached_write,
                "output_tokens": output,
                "total_cost_usd": cost_usd,
                "latency_ms": latency_ms,
                "agent_schema_name": agent_schema_name,
                "cumulative_cost_usd": self._total_usd,
            })

    def total_usd(self) -> float:
        with self._lock:
            return self._total_usd

    def calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._calls)

    def reset(self) -> None:
        with self._lock:
            self._total_usd = 0.0
            self._calls.clear()


_LEDGER = _CostLedger()


def get_cost_ledger() -> _CostLedger:
    """Public accessor — used by the runner / smoke driver to surface
    a per-decision cost summary."""
    return _LEDGER


# ── Helpers ───────────────────────────────────────────────────────────
def _api_key_fingerprint(key: str) -> str:
    """Return sha256(key)[:8] so operators can verify which key is
    loaded without ever logging the key itself. Mirrors the FMP
    adapter's get_key_fingerprint() pattern."""
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _estimate_input_tokens(system_prompt: str, user_prompt: str) -> int:
    """Cheap pre-flight estimate. We use char/4 (Anthropic's published
    heuristic for English) rather than calling count_tokens (which
    itself counts as a paid API call on some plans). This estimate is
    only used by the per-call cap; the actual billing uses the response
    usage block."""
    return (len(system_prompt) + len(user_prompt)) // 4


def _compute_cost_usd(
    model: str,
    *,
    input_uncached: int,
    cached_read: int,
    cached_write: int,
    output: int,
) -> float:
    """Compute USD cost from the module pricing table. Raises if the
    model is not in the table — operators must update PRICING when
    routing a new model."""
    if model not in PRICING_USD_PER_MTOK:
        raise AnthropicProviderError(
            f"Model {model!r} not in PRICING_USD_PER_MTOK. Add a row "
            f"before routing this model."
        )
    p = PRICING_USD_PER_MTOK[model]
    cost = (
        input_uncached * p["input"] +
        cached_read * p["input_cached_read"] +
        cached_write * p["input_cached_write"] +
        output * p["output"]
    ) / 1_000_000.0
    return cost


# Anthropic Batch API list pricing is 50% of synchronous list price for
# every token class. We apply the discount at the cost-compute layer so
# the underlying PRICING_USD_PER_MTOK table stays the single source of
# truth for sync pricing.
BATCH_DISCOUNT_FACTOR = 0.50


def _compute_batch_cost(
    model: str, *, input_uncached: int, cached_read: int,
    cached_write: int, output: int,
) -> float:
    sync_cost = _compute_cost_usd(
        model, input_uncached=input_uncached, cached_read=cached_read,
        cached_write=cached_write, output=output,
    )
    return sync_cost * BATCH_DISCOUNT_FACTOR


def _compute_batch_cost_estimate(
    model: str, total_input_tokens: int, total_output_tokens: int,
) -> float:
    """Pre-flight pessimistic cost estimate for a batch — assumes
    none of the input is cached (worst case) and uses the supplied
    output-token total. Used for the hard-stop guard."""
    sync_cost = _compute_cost_usd(
        model, input_uncached=total_input_tokens, cached_read=0,
        cached_write=0, output=total_output_tokens,
    )
    return sync_cost * BATCH_DISCOUNT_FACTOR


# ── Provider ──────────────────────────────────────────────────────────
class AnthropicProvider(LLMProvider):
    """Anthropic-backed LLMProvider with prompt cache + cost guards."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = DEFAULT_MODEL,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise AnthropicProviderError(
                "ANTHROPIC_API_KEY is not set in environment. The "
                "Anthropic provider requires a real key. Either set the "
                "env var or use LLM_PROVIDER=stub."
            )
        self._default_model = default_model
        self._timeout_s = timeout_s

        # Validate that the default model has a pricing row.
        if default_model not in PRICING_USD_PER_MTOK:
            raise AnthropicProviderError(
                f"Default model {default_model!r} not in PRICING_USD_PER_MTOK."
            )

        # Lazy SDK import so the provider module itself can be imported
        # in environments where the SDK is missing (the constructor will
        # then raise and the test layer can skip cleanly).
        try:
            from anthropic import Anthropic  # noqa: WPS433
        except ImportError as e:
            raise AnthropicProviderError(
                f"anthropic SDK is not installed ({e}). "
                f"`pip install anthropic` first."
            ) from e

        self._client = Anthropic(api_key=self._api_key, timeout=float(timeout_s))
        logger.info(
            "AnthropicProvider initialised: default_model=%s "
            "key_fingerprint=%s timeout_s=%d",
            self._default_model,
            _api_key_fingerprint(self._api_key),
            self._timeout_s,
        )

    # ── public API ─────────────────────────────────────────────────
    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model_id: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        response_format: str = "json_object",
        agent_schema_name: str | None = None,
    ) -> ProviderResponse:
        """Run a single completion against Anthropic.

        Defaults: claude-sonnet-4-6, temperature=0, max_tokens=1024.
        Per-call model_id override supported.

        The system_prompt is sent as a cache-control block; the
        user_prompt is sent as the per-call payload (uncached). On
        success the response is returned as ProviderResponse with the
        full token / cost trail recorded into the process ledger.
        """
        model = model_id or self._default_model

        # Cost guards — both fire BEFORE the SDK call.
        self._check_per_call_input_cap(system_prompt, user_prompt)
        self._check_hard_stop()

        # Build the messages.create request. cache_control on the system
        # block tells Anthropic to cache the (long, static) system prompt
        # for 5 minutes. The user message stays uncached because it
        # carries the per-call evidence packet payload.
        system_blocks = [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }]
        # If response_format hints "json_object", we rely on the prompt's
        # own "JSON only, no fences" instruction. Anthropic does not
        # expose a native JSON-mode toggle on every model; the prompts
        # are written to enforce JSON-only output.
        _ = response_format  # accepted for interface compatibility

        t0 = time.perf_counter()
        try:
            resp = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_blocks,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            # Wrap any SDK error (timeout, 4xx, 5xx, network) so the
            # runner sees a clear AnthropicProviderError context. The
            # exception message comes from the SDK; we add the model
            # and agent name for log triage. NEVER include prompt body.
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            raise AnthropicProviderError(
                f"Anthropic call failed: model={model} "
                f"agent={agent_schema_name!r} elapsed_ms={elapsed_ms} "
                f"error_type={type(e).__name__}: {e}"
            ) from e
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Extract response text. content is a list of typed blocks; for
        # our prompts (JSON-only output, no tool use) the first block is
        # always a text block.
        try:
            raw_text = resp.content[0].text  # type: ignore[attr-defined]
        except (AttributeError, IndexError) as e:
            raise AnthropicProviderError(
                f"Anthropic response had unexpected shape: model={model} "
                f"stop_reason={getattr(resp, 'stop_reason', None)!r}: {e}"
            ) from e

        # Token accounting from the SDK's usage block. The four classes
        # are surfaced explicitly so the cost math is transparent.
        usage = getattr(resp, "usage", None)
        input_uncached = int(getattr(usage, "input_tokens", 0) or 0)
        cached_read = int(
            getattr(usage, "cache_read_input_tokens", 0) or 0
        )
        cached_write = int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        cost_usd = _compute_cost_usd(
            model,
            input_uncached=input_uncached,
            cached_read=cached_read,
            cached_write=cached_write,
            output=output_tokens,
        )

        _LEDGER.add(
            model=model,
            input_uncached=input_uncached,
            cached_read=cached_read,
            cached_write=cached_write,
            output=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            agent_schema_name=agent_schema_name,
        )

        logger.info(
            "AnthropicProvider.complete model=%s agent=%s "
            "in_unc=%d in_cr=%d in_cw=%d out=%d cost_usd=%.5f "
            "cum_usd=%.5f latency_ms=%d",
            model, agent_schema_name,
            input_uncached, cached_read, cached_write, output_tokens,
            cost_usd, _LEDGER.total_usd(), latency_ms,
        )

        return ProviderResponse(
            raw_text=raw_text,
            model_id=model,
            input_tokens=input_uncached + cached_read + cached_write,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            stop_reason=str(getattr(resp, "stop_reason", "end_turn")),
            provider="anthropic",
            cache_used=(cached_read > 0),
        )

    # ── Batch API (Task 3 — 50% discount for backtest cells) ────────
    def complete_batch(
        self,
        *,
        requests: list[dict],
        poll_interval_s: float = 5.0,
        max_wait_s: float = 86400,
        progress_callback=None,
    ) -> dict[str, dict]:
        """Submit a batch of completion requests at 50% list-price.

        Anthropic Messages Batches API processes up to 100,000 requests
        per batch with 24-hour SLA (typically minutes for moderate
        batches). Cost: 50% of the standard messages.create pricing.
        Used by Task 4 backtest to fan out the per-cell agent calls
        cheaply.

        requests: list of dicts each containing:
            custom_id (str)        — unique within the batch
            system_prompt (str)    — cached via cache_control
            user_prompt (str)      — per-call payload
            model_id (str, optional)        — defaults to provider default
            max_tokens (int, optional)      — defaults to DEFAULT_MAX_TOKENS
            temperature (float, optional)   — defaults to DEFAULT_TEMPERATURE
            agent_schema_name (str, optional) — for cost-ledger attribution

        Returns: dict mapping custom_id -> result dict:
            {
              "raw_text": str | None,
              "model_id": str,
              "input_tokens": int,         (sum of all classes)
              "input_uncached": int,
              "cached_read": int,
              "cached_write": int,
              "output_tokens": int,
              "cost_usd": float,           (50% discount applied)
              "stop_reason": str | None,
              "succeeded": bool,
              "error": str | None,
            }

        Cost guard: a pre-flight estimate based on input prompts is
        compared against the cumulative ledger + hard-stop cap. If
        the estimate would push past the cap, the batch is rejected
        BEFORE submission. Per-call input cap is enforced per request.

        Raises AnthropicProviderError on:
            - estimated cost would exceed hard stop
            - SDK error during create / poll / results
            - poll exceeds max_wait_s
        """
        if not requests:
            return {}
        if len(requests) > 100_000:
            raise AnthropicProviderError(
                f"Batch size {len(requests)} > 100000 (Anthropic cap). "
                f"Split into multiple batches."
            )

        # Per-request input cap + cost pre-flight.
        cum = _LEDGER.total_usd()
        cap_str = os.environ.get("ANTHROPIC_HARD_STOP_USD")
        cap = float(cap_str) if cap_str else DEFAULT_HARD_STOP_USD
        per_call_cap = int(os.environ.get(
            "ANTHROPIC_MAX_INPUT_TOKENS", DEFAULT_MAX_INPUT_TOKENS
        ))
        est_input_tokens = 0
        seen_ids: set[str] = set()
        for r in requests:
            cid = r.get("custom_id")
            if not cid or not isinstance(cid, str):
                raise AnthropicProviderError(
                    "Every batch request must carry a non-empty custom_id"
                )
            if cid in seen_ids:
                raise AnthropicProviderError(
                    f"duplicate custom_id in batch: {cid!r}"
                )
            seen_ids.add(cid)
            est = _estimate_input_tokens(
                r.get("system_prompt", ""), r.get("user_prompt", ""),
            )
            if est > per_call_cap:
                raise AnthropicProviderError(
                    f"Batch request {cid!r} estimated {est} input "
                    f"tokens > per-call cap {per_call_cap}."
                )
            est_input_tokens += est

        # Pre-flight cost estimate at batch (50%) pricing. We assume
        # each call returns DEFAULT_MAX_TOKENS output (worst case) and
        # use the most-expensive model in the request set.
        models_in_batch = {r.get("model_id") or self._default_model
                           for r in requests}
        worst_model = max(
            models_in_batch,
            key=lambda m: PRICING_USD_PER_MTOK.get(m, {}).get("output", 0)
        )
        worst_max_out = max(
            int(r.get("max_tokens") or DEFAULT_MAX_TOKENS) for r in requests
        )
        # 50% batch discount applied at cost computation time.
        est_cost = _compute_batch_cost_estimate(
            worst_model, est_input_tokens,
            len(requests) * worst_max_out,
        )
        if cum + est_cost >= cap:
            raise AnthropicProviderError(
                f"Batch cost estimate ${est_cost:.4f} would push "
                f"cumulative ${cum:.4f} past hard stop ${cap:.4f}. "
                f"Override via ANTHROPIC_HARD_STOP_USD or split batch."
            )

        # Build SDK request list.
        sdk_requests = []
        for r in requests:
            model = r.get("model_id") or self._default_model
            if model not in PRICING_USD_PER_MTOK:
                raise AnthropicProviderError(
                    f"Batch request {r['custom_id']} model {model!r} "
                    f"not in PRICING_USD_PER_MTOK."
                )
            sdk_requests.append({
                "custom_id": r["custom_id"],
                "params": {
                    "model": model,
                    "max_tokens": int(r.get("max_tokens", DEFAULT_MAX_TOKENS)),
                    "temperature": float(r.get("temperature",
                                                  DEFAULT_TEMPERATURE)),
                    "system": [{
                        "type": "text",
                        "text": r.get("system_prompt", ""),
                        "cache_control": {"type": "ephemeral"},
                    }],
                    "messages": [{
                        "role": "user",
                        "content": r.get("user_prompt", ""),
                    }],
                },
            })

        # Submit batch.
        t_submit = time.perf_counter()
        try:
            batch = self._client.messages.batches.create(requests=sdk_requests)
        except Exception as e:
            raise AnthropicProviderError(
                f"Anthropic batch create failed: error_type={type(e).__name__}: {e}"
            ) from e

        batch_id = batch.id
        logger.info(
            "AnthropicProvider.complete_batch submitted: "
            "batch_id=%s n_requests=%d est_input_tokens=%d "
            "est_cost_usd=%.4f hard_stop_usd=%.4f",
            batch_id, len(requests), est_input_tokens,
            est_cost, cap,
        )

        # Poll until processing_status == "ended".
        deadline = time.perf_counter() + max_wait_s
        while True:
            try:
                batch = self._client.messages.batches.retrieve(batch_id)
            except Exception as e:
                raise AnthropicProviderError(
                    f"Anthropic batch retrieve failed: batch_id={batch_id} "
                    f"error_type={type(e).__name__}: {e}"
                ) from e
            status = getattr(batch, "processing_status", None)
            counts = getattr(batch, "request_counts", None)
            if progress_callback is not None:
                try:
                    progress_callback({
                        "batch_id": batch_id,
                        "status": status,
                        "request_counts": (counts.model_dump() if hasattr(counts, "model_dump") else counts),
                        "elapsed_s": time.perf_counter() - t_submit,
                    })
                except Exception:
                    pass  # never let a progress callback break a batch
            if status == "ended":
                break
            if status in ("canceled", "expired"):
                raise AnthropicProviderError(
                    f"Anthropic batch terminal-bad status={status!r} "
                    f"batch_id={batch_id}"
                )
            if time.perf_counter() > deadline:
                raise AnthropicProviderError(
                    f"Anthropic batch poll timed out after "
                    f"{max_wait_s:.0f}s; batch_id={batch_id} status={status!r}"
                )
            time.sleep(poll_interval_s)

        # Stream results.
        results: dict[str, dict] = {}
        try:
            for r in self._client.messages.batches.results(batch_id):
                cid = getattr(r, "custom_id", None)
                if cid is None:
                    continue
                result_obj = getattr(r, "result", None)
                rtype = getattr(result_obj, "type", None) if result_obj else None
                model = self._default_model
                raw_text = None
                input_uncached = cached_read = cached_write = 0
                output_tokens = 0
                stop_reason = None
                error = None
                succeeded = False

                if rtype == "succeeded":
                    msg = getattr(result_obj, "message", None)
                    if msg is None:
                        error = "succeeded result missing message"
                    else:
                        try:
                            raw_text = msg.content[0].text  # type: ignore[attr-defined]
                        except (AttributeError, IndexError):
                            error = "succeeded message had unexpected content shape"
                        usage = getattr(msg, "usage", None)
                        if usage is not None:
                            input_uncached = int(getattr(usage, "input_tokens", 0) or 0)
                            cached_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                            cached_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
                            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                        stop_reason = str(getattr(msg, "stop_reason", "end_turn"))
                        model = str(getattr(msg, "model", model))
                        succeeded = (raw_text is not None) and (error is None)
                elif rtype == "errored":
                    err = getattr(result_obj, "error", None)
                    error = (
                        f"errored: {getattr(err, 'type', '?')}: "
                        f"{getattr(err, 'message', '?')}"
                    )
                elif rtype == "canceled":
                    error = "canceled"
                elif rtype == "expired":
                    error = "expired"
                else:
                    error = f"unknown_result_type={rtype!r}"

                cost_usd = _compute_batch_cost(
                    model,
                    input_uncached=input_uncached,
                    cached_read=cached_read,
                    cached_write=cached_write,
                    output=output_tokens,
                )
                # Attribute to ledger only on success (errored requests
                # are still partially billed by Anthropic for tokens
                # consumed before the error, but the batch billing
                # documents this; we conservatively record only succeeded).
                if succeeded:
                    _LEDGER.add(
                        model=model,
                        input_uncached=input_uncached,
                        cached_read=cached_read,
                        cached_write=cached_write,
                        output=output_tokens,
                        cost_usd=cost_usd,
                        latency_ms=0,  # batch latency is global, not per-call
                        agent_schema_name=None,
                    )
                results[cid] = {
                    "raw_text": raw_text,
                    "model_id": model,
                    "input_tokens": input_uncached + cached_read + cached_write,
                    "input_uncached": input_uncached,
                    "cached_read": cached_read,
                    "cached_write": cached_write,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                    "stop_reason": stop_reason,
                    "succeeded": succeeded,
                    "error": error,
                }
        except Exception as e:
            raise AnthropicProviderError(
                f"Anthropic batch results stream failed: batch_id={batch_id} "
                f"error_type={type(e).__name__}: {e}"
            ) from e

        elapsed_total = time.perf_counter() - t_submit
        n_ok = sum(1 for v in results.values() if v["succeeded"])
        total_cost = sum(v["cost_usd"] for v in results.values())
        logger.info(
            "AnthropicProvider.complete_batch finished: "
            "batch_id=%s elapsed_s=%.0f n_results=%d n_ok=%d "
            "total_cost_usd=%.4f cum_usd=%.4f",
            batch_id, elapsed_total, len(results), n_ok,
            total_cost, _LEDGER.total_usd(),
        )
        return results

    # ── guards ─────────────────────────────────────────────────────
    def _check_per_call_input_cap(
        self, system_prompt: str, user_prompt: str
    ) -> None:
        cap = int(os.environ.get(
            "ANTHROPIC_MAX_INPUT_TOKENS", DEFAULT_MAX_INPUT_TOKENS
        ))
        est = _estimate_input_tokens(system_prompt, user_prompt)
        if est > cap:
            raise AnthropicProviderError(
                f"Per-call input cap exceeded: estimated {est} input "
                f"tokens > cap {cap}. Override via "
                f"ANTHROPIC_MAX_INPUT_TOKENS or shrink the evidence packet."
            )

    def _check_hard_stop(self) -> None:
        cap_str = os.environ.get("ANTHROPIC_HARD_STOP_USD")
        cap = float(cap_str) if cap_str else DEFAULT_HARD_STOP_USD
        cum = _LEDGER.total_usd()
        if cum >= cap:
            raise AnthropicProviderError(
                f"Cumulative spend hard stop tripped: ${cum:.4f} >= "
                f"cap ${cap:.4f}. Override via ANTHROPIC_HARD_STOP_USD "
                f"or call get_cost_ledger().reset() between runs."
            )


__all__ = [
    "AnthropicProvider",
    "AnthropicProviderError",
    "PRICING_USD_PER_MTOK",
    "DEFAULT_MODEL",
    "HAIKU_MODEL",
    "OPUS_MODEL",
    "DEFAULT_HARD_STOP_USD",
    "DEFAULT_MAX_INPUT_TOKENS",
    "DEFAULT_TIMEOUT_S",
    "get_cost_ledger",
]
