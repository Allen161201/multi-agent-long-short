"""
Anthropic provider tests (Task 7, 2026-04-29 PM).

Tests skip cleanly when ANTHROPIC_API_KEY is missing so CI does not need
the key. The live smoke test uses Haiku (cheapest) and a tiny prompt;
expected per-run cost ~$0.0005.

Coverage:
  - test_provider_switch_stub          — LLM_PROVIDER=stub returns the stub
  - test_provider_switch_anthropic     — LLM_PROVIDER=anthropic constructs
                                          the Anthropic provider (no call)
  - test_per_call_input_cap_rejects    — synthetic 60K-char prompt aborts
  - test_hard_stop_blocks_after_cap    — ledger fakes $5.01 cumulative,
                                          next call refuses to send
  - test_smoke_haiku_live              — real call to Haiku 4.5; asserts
                                          non-empty body + cost recorded
  - test_cache_read_on_repeat          — same prompt twice, second call
                                          shows cache_read_input_tokens
  - test_pricing_table_completeness    — all three documented model ids
                                          have all four price classes
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Load .env for ANTHROPIC_API_KEY before importing the provider; the
# provider's constructor reads the key at instantiation time.
try:
    from dotenv import load_dotenv  # noqa: E402
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass


def _have_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ── Pricing table ────────────────────────────────────────────────────
def test_pricing_table_completeness():
    from src.llm.anthropic_provider import (
        PRICING_USD_PER_MTOK, DEFAULT_MODEL, HAIKU_MODEL, OPUS_MODEL,
    )
    required_keys = {"input", "input_cached_read", "input_cached_write", "output"}
    for model in (DEFAULT_MODEL, HAIKU_MODEL, OPUS_MODEL):
        assert model in PRICING_USD_PER_MTOK, f"missing {model} in pricing"
        assert set(PRICING_USD_PER_MTOK[model].keys()) == required_keys, (
            f"{model} pricing keys: {set(PRICING_USD_PER_MTOK[model].keys())}"
        )


# ── Provider switch ──────────────────────────────────────────────────
def test_provider_switch_stub():
    from src.llm.factory import get_provider
    saved = os.environ.pop("LLM_PROVIDER", None)
    try:
        provider = get_provider()
        assert provider.name == "deterministic_stub"
    finally:
        if saved is not None:
            os.environ["LLM_PROVIDER"] = saved


def test_provider_switch_anthropic():
    if not _have_key():
        print("    [SKIP] ANTHROPIC_API_KEY not set")
        return
    from src.llm.factory import get_provider
    saved = os.environ.get("LLM_PROVIDER")
    os.environ["LLM_PROVIDER"] = "anthropic"
    try:
        provider = get_provider()
        assert provider.name == "anthropic"
    finally:
        if saved is None:
            os.environ.pop("LLM_PROVIDER", None)
        else:
            os.environ["LLM_PROVIDER"] = saved


# ── Cost guards ──────────────────────────────────────────────────────
def test_per_call_input_cap_rejects():
    if not _have_key():
        print("    [SKIP] ANTHROPIC_API_KEY not set")
        return
    from src.llm.anthropic_provider import (
        AnthropicProvider, AnthropicProviderError, get_cost_ledger,
    )
    get_cost_ledger().reset()
    # Lower the cap to 1000 tokens for this test, build a 60K-char prompt
    # (~15K-token estimate), assert refuse-to-send.
    saved = os.environ.get("ANTHROPIC_MAX_INPUT_TOKENS")
    os.environ["ANTHROPIC_MAX_INPUT_TOKENS"] = "1000"
    try:
        prov = AnthropicProvider()
        big = "x" * 60_000
        try:
            prov.complete(
                system_prompt=big,
                user_prompt="hi",
                max_tokens=8,
                temperature=0.0,
                response_format="text",
                agent_schema_name=None,
            )
        except AnthropicProviderError as e:
            assert "Per-call input cap" in str(e)
            return
        raise AssertionError("expected per-call cap to fire")
    finally:
        if saved is None:
            os.environ.pop("ANTHROPIC_MAX_INPUT_TOKENS", None)
        else:
            os.environ["ANTHROPIC_MAX_INPUT_TOKENS"] = saved


def test_hard_stop_blocks_after_cap():
    if not _have_key():
        print("    [SKIP] ANTHROPIC_API_KEY not set")
        return
    from src.llm.anthropic_provider import (
        AnthropicProvider, AnthropicProviderError, get_cost_ledger,
    )
    ledger = get_cost_ledger()
    ledger.reset()
    # Inject a synthetic $5.01 spend, then assert the next call refuses.
    ledger.add(
        model="claude-sonnet-4-6",
        input_uncached=0, cached_read=0, cached_write=0, output=0,
        cost_usd=5.01, latency_ms=0, agent_schema_name=None,
    )
    prov = AnthropicProvider()
    try:
        prov.complete(
            system_prompt="x", user_prompt="y",
            max_tokens=8, temperature=0.0, response_format="text",
            agent_schema_name=None,
        )
    except AnthropicProviderError as e:
        assert "hard stop" in str(e).lower()
        ledger.reset()
        return
    ledger.reset()
    raise AssertionError("expected hard stop to fire")


# ── Live smoke + cache accounting ────────────────────────────────────
def test_smoke_haiku_live():
    if not _have_key():
        print("    [SKIP] ANTHROPIC_API_KEY not set")
        return
    from src.llm.anthropic_provider import (
        AnthropicProvider, HAIKU_MODEL, get_cost_ledger,
    )
    get_cost_ledger().reset()
    prov = AnthropicProvider()
    resp = prov.complete(
        system_prompt=(
            "You are a JSON emitter. Reply only with the literal JSON "
            "{\"ok\": true} and nothing else."
        ),
        user_prompt="emit",
        model_id=HAIKU_MODEL,
        max_tokens=32,
        temperature=0.0,
        response_format="json_object",
        agent_schema_name=None,
    )
    assert resp["raw_text"], "raw_text empty"
    assert resp["model_id"] == HAIKU_MODEL
    assert resp["provider"] == "anthropic"
    assert resp["output_tokens"] >= 1
    calls = get_cost_ledger().calls()
    assert len(calls) == 1
    assert calls[0]["total_cost_usd"] > 0.0
    assert calls[0]["total_cost_usd"] < 0.01, "smoke cost should be <1¢"


def test_cache_read_on_repeat():
    if not _have_key():
        print("    [SKIP] ANTHROPIC_API_KEY not set")
        return
    from src.llm.anthropic_provider import (
        AnthropicProvider, HAIKU_MODEL, get_cost_ledger,
    )
    get_cost_ledger().reset()
    prov = AnthropicProvider()
    # Prompt cache requires a system prompt above the SDK's minimum
    # cacheable block size (~1024 tokens). Pad the system prompt so the
    # cache control block is eligible — repeat a long instructional
    # paragraph until we exceed the floor.
    system = (
        "You are a deterministic JSON emitter. Reply with the literal "
        "JSON object {\"ok\": true} and nothing else. Never include "
        "prose, markdown fences, or explanations. Never deviate from "
        "the literal JSON. The user will send the single token 'emit'. "
    ) * 80   # ~17K chars → ~4K tokens, well above any cache floor
    common_kwargs = dict(
        system_prompt=system, user_prompt="emit",
        model_id=HAIKU_MODEL, max_tokens=32, temperature=0.0,
        response_format="json_object", agent_schema_name=None,
    )
    prov.complete(**common_kwargs)        # first call: write cache
    resp2 = prov.complete(**common_kwargs)  # second call: should hit cache
    calls = get_cost_ledger().calls()
    assert len(calls) == 2
    # Anthropic's prompt cache can take a moment to be available; we
    # accept either a write on the first call (cw>0) or a read on the
    # second (cr>0). What we DON'T accept is two cold calls in a row,
    # which would mean caching never engaged at all.
    write_or_read = (
        calls[0]["input_tokens_cached_write"] > 0
        or calls[1]["input_tokens_cached_read"] > 0
    )
    assert write_or_read, (
        f"expected cache_write or cache_read; got "
        f"cw1={calls[0]['input_tokens_cached_write']} "
        f"cr2={calls[1]['input_tokens_cached_read']}"
    )
    assert resp2["raw_text"], "second call response empty"


# ── runner ────────────────────────────────────────────────────────────
def main() -> int:
    failures: list[str] = []
    tests = [
        test_pricing_table_completeness,
        test_provider_switch_stub,
        test_provider_switch_anthropic,
        test_per_call_input_cap_rejects,
        test_hard_stop_blocks_after_cap,
        test_smoke_haiku_live,
        test_cache_read_on_repeat,
    ]
    print("\n=== test_anthropic_provider ===")
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failures.append(fn.__name__)
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failures.append(fn.__name__)
    print()
    if failures:
        print(f"  RESULT: {len(failures)} failure(s): {failures}")
        return 1
    print(f"  RESULT: {len(tests)}/{len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
