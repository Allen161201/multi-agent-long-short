"""
LLM integration layer (Step 5 — preparation; Step 6 wires real providers).

Public surface:
    LLMProvider               -- abstract base class
    DeterministicStubProvider -- returns fail-closed skeletons (current default)
    AnthropicProvider         -- skeleton; raises in Step 5; wired in Step 6
    get_provider              -- factory keyed off env var LLM_PROVIDER
    LLMCache                  -- disk-based JSON cache (per-agent dirs)
"""
from .provider import LLMProvider
from .deterministic_stub import DeterministicStubProvider
from .anthropic_provider import AnthropicProvider
from .factory import get_provider
from .cache import LLMCache, build_cache_key

__all__ = [
    "LLMProvider",
    "DeterministicStubProvider",
    "AnthropicProvider",
    "get_provider",
    "LLMCache",
    "build_cache_key",
]
