"""
Provider factory — central dispatch from string slug to LLMProvider.

The orchestrator NEVER instantiates a provider directly. It calls
get_provider() and goes through whatever the env says.

Slugs (LLM_PROVIDER):
  - "stub" / "deterministic_stub" (default) → DeterministicStubProvider
  - "anthropic"                             → AnthropicProvider

Switching from stub to real Claude is a single env-var change. No code
or prompt edits required. The default remains stub so unattended test
runs (e.g., the Step A6 regression matrix) stay byte-identical and free.
"""
from __future__ import annotations

import logging
import os

from .anthropic_provider import AnthropicProvider, AnthropicProviderError, HAIKU_MODEL
from .deterministic_stub import DeterministicStubProvider
from .provider import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER_SLUG = "deterministic_stub"

_STUB_ALIASES = frozenset({"stub", "deterministic_stub", "det_stub", "det-stub"})
_ANTHROPIC_ALIASES = frozenset({"anthropic", "claude"})


def get_provider(name: str | None = None) -> LLMProvider:
    """Return the LLMProvider matching `name` (or env LLM_PROVIDER, or
    the default deterministic_stub).

    Default invariant: when LLM_PROVIDER is unset, the stub is returned.
    The Anthropic provider is opt-in only. This is what keeps the Step
    A6 regression matrix byte-identical at the frozen runtime hash.
    """
    slug = (
        name or os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER_SLUG
    ).strip().lower()

    if slug in _STUB_ALIASES:
        return DeterministicStubProvider()

    if slug in _ANTHROPIC_ALIASES:
        # AnthropicProvider's constructor enforces ANTHROPIC_API_KEY
        # presence and raises AnthropicProviderError if missing — this
        # is the fail-loud point. We do NOT silently fall back to stub.
        try:
            return AnthropicProvider(default_model=HAIKU_MODEL)
        except AnthropicProviderError:
            raise
        except Exception as e:
            # Any other instantiation error (SDK import failure, etc.)
            # is converted to AnthropicProviderError so the caller sees
            # one error class.
            raise AnthropicProviderError(
                f"Failed to construct AnthropicProvider: "
                f"{type(e).__name__}: {e}"
            ) from e

    raise ValueError(
        f"Unknown LLM_PROVIDER {slug!r}. Allowed: "
        f"{sorted(_STUB_ALIASES | _ANTHROPIC_ALIASES)}."
    )
