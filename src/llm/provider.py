"""
LLMProvider — abstract base class.

Every provider returns the SAME response shape so callers never branch on
which backend is in use. The cache, schema validator, and orchestrator
treat all providers uniformly.

Step 5: only DeterministicStubProvider is functional.
Step 6: AnthropicProvider becomes functional behind the same interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypedDict


class ProviderResponse(TypedDict):
    """Uniform return shape for every LLMProvider.complete() call."""
    raw_text: str         # the model's response body, JSON-encoded by the caller's contract
    model_id: str         # the exact model identifier the call ran against
    input_tokens: int     # 0 for the stub
    output_tokens: int    # 0 for the stub
    latency_ms: int       # provider-side latency, integer milliseconds
    stop_reason: str      # "end_turn" | "max_tokens" | "stub" | provider-specific
    provider: str         # short slug: "deterministic_stub" | "anthropic" | ...
    cache_used: bool      # whether the provider itself served from a (provider-internal) cache


class LLMProvider(ABC):
    """Abstract interface for any LLM backend.

    The agent orchestrator calls `complete(...)` once per agent run and
    expects valid JSON in `raw_text` (the orchestrator parses + validates
    against the agent's pydantic schema). Providers MUST NOT raise on a
    malformed model output — surface a structured failure via raw_text
    and let the orchestrator's schema validation collapse to the
    fail-closed `needs_more_evidence` skeleton.
    """

    name: str = "abstract"

    @abstractmethod
    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model_id: str,
        max_tokens: int,
        temperature: float,
        response_format: str,
    ) -> ProviderResponse:
        """Run a single LLM completion.

        Args:
            system_prompt:  The role/context block.
            user_prompt:    The per-call payload (typically the rendered
                            user template containing the evidence packet).
            model_id:       Provider-specific model id (e.g.
                            "claude-sonnet-4-5-20250929" or "stub-v1").
            max_tokens:     Hard cap on output tokens.
            temperature:    Sampling temperature in [0.0, 1.0].
            response_format: Provider hint, e.g. "json_object" or "text".

        Returns:
            ProviderResponse — see TypedDict above.
        """
        raise NotImplementedError
