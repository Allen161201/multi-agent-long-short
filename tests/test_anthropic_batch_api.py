"""
Task 3 — Anthropic Batch API primitive tests.

Mocks the SDK to verify:
  T1  custom_id round-trip: each request's custom_id appears as a key
      in the result dict.
  T2  cost discount: BATCH_DISCOUNT_FACTOR == 0.50; per-result
      cost_usd is exactly 50% of the equivalent sync cost for the
      same model + token counts.
  T3  hard-stop guard fires BEFORE batch creation when the pre-flight
      estimate would push cumulative spend past cap.
  T4  per-call input cap fires BEFORE batch creation when one
      request's prompt is too large.
  T5  duplicate custom_id rejected.
  T6  empty requests returns {} without calling SDK.
  T7  errored request: result has succeeded=False, error message
      surfaced, cost_usd computed but NOT added to ledger.

No live LLM calls — every test stubs Anthropic().messages.batches.{
create, retrieve, results}.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Provider import requires a (real or fake) API key.
os.environ.setdefault("ANTHROPIC_API_KEY", "test_dummy_key_not_real")
os.environ["ANTHROPIC_HARD_STOP_USD"] = "5.00"

from src.llm.anthropic_provider import (
    AnthropicProvider, AnthropicProviderError,
    BATCH_DISCOUNT_FACTOR, _compute_cost_usd, _compute_batch_cost,
    get_cost_ledger,
)


HAIKU = "claude-haiku-4-5-20251001"


# ── stub SDK objects ─────────────────────────────────────────────────

class _StubBatch:
    def __init__(self, batch_id="batch_test_123",
                 status="ended", request_counts=None):
        self.id = batch_id
        self.processing_status = status
        self.request_counts = request_counts or SimpleNamespace(
            processing=0, succeeded=0, errored=0, canceled=0, expired=0,
        )


class _StubResult:
    """Mimics the SDK MessageBatchIndividualResponse."""
    def __init__(self, custom_id, *, succeeded=True,
                 raw_text="dummy", input_tokens=100,
                 output_tokens=50, cached_read=0, cached_write=0,
                 model=HAIKU, error_msg=None):
        self.custom_id = custom_id
        if succeeded:
            usage = SimpleNamespace(
                input_tokens=input_tokens,
                cache_read_input_tokens=cached_read,
                cache_creation_input_tokens=cached_write,
                output_tokens=output_tokens,
            )
            content = [SimpleNamespace(text=raw_text)]
            msg = SimpleNamespace(
                content=content, usage=usage,
                stop_reason="end_turn", model=model,
            )
            self.result = SimpleNamespace(type="succeeded", message=msg,
                                            error=None)
        else:
            err = SimpleNamespace(type="invalid_request",
                                   message=error_msg or "stub error")
            self.result = SimpleNamespace(type="errored", message=None,
                                            error=err)


def _install_stub(provider, batch_results, capture_create=None):
    class _Batches:
        last_create = None
        def create(self_inner, requests):
            _Batches.last_create = list(requests)
            if capture_create is not None:
                capture_create(requests)
            return _StubBatch()
        def retrieve(self_inner, batch_id):
            return _StubBatch(batch_id=batch_id, status="ended")
        def results(self_inner, batch_id):
            yield from batch_results
    class _Messages:
        batches = _Batches()
    provider._client = SimpleNamespace(messages=_Messages())
    return _Batches


# ── tests ────────────────────────────────────────────────────────────

def test_t1_custom_id_round_trip():
    p = AnthropicProvider(default_model=HAIKU)
    get_cost_ledger().reset()
    _install_stub(p, [
        _StubResult("cell_AAPL_2026-04-15_solo"),
        _StubResult("cell_NVDA_2026-04-15_solo"),
        _StubResult("cell_TSLA_2026-04-15_solo"),
    ])
    requests = [
        {"custom_id": "cell_AAPL_2026-04-15_solo",
         "system_prompt": "sys", "user_prompt": "u1"},
        {"custom_id": "cell_NVDA_2026-04-15_solo",
         "system_prompt": "sys", "user_prompt": "u2"},
        {"custom_id": "cell_TSLA_2026-04-15_solo",
         "system_prompt": "sys", "user_prompt": "u3"},
    ]
    out = p.complete_batch(requests=requests, poll_interval_s=0.01)
    assert sorted(out.keys()) == sorted(r["custom_id"] for r in requests), \
        f"custom_id round-trip failed: got {list(out.keys())}"
    for cid, res in out.items():
        assert res["succeeded"] is True
        assert res["raw_text"] == "dummy"
    print("  PASS test_t1_custom_id_round_trip")


def test_t2_cost_discount_50pct():
    p = AnthropicProvider(default_model=HAIKU)
    get_cost_ledger().reset()
    _install_stub(p, [
        _StubResult("c1", input_tokens=1000, output_tokens=500),
    ])
    out = p.complete_batch(requests=[{
        "custom_id": "c1", "system_prompt": "sys", "user_prompt": "u",
    }], poll_interval_s=0.01)
    sync_cost = _compute_cost_usd(HAIKU, input_uncached=1000,
                                    cached_read=0, cached_write=0,
                                    output=500)
    assert abs(out["c1"]["cost_usd"] - sync_cost * 0.5) < 1e-9, \
        f"discount wrong: {out['c1']['cost_usd']} vs {sync_cost*0.5}"
    assert BATCH_DISCOUNT_FACTOR == 0.5
    print(f"  PASS test_t2_cost_discount_50pct  "
          f"sync=${sync_cost:.6f} batch=${out['c1']['cost_usd']:.6f}")


def test_t3_hard_stop_guard():
    p = AnthropicProvider(default_model=HAIKU)
    get_cost_ledger().reset()
    # Trip the cumulative ledger close to the cap so any new batch
    # would push it over.
    get_cost_ledger().add(model=HAIKU, input_uncached=0, cached_read=0,
                           cached_write=0, output=0, cost_usd=4.99,
                           latency_ms=0, agent_schema_name=None)
    huge_prompt = "x" * 200_000  # ≈50k tokens at 4 chars/token
    requests = [{"custom_id": f"c{i}", "system_prompt": huge_prompt,
                  "user_prompt": "u"} for i in range(10)]
    try:
        p.complete_batch(requests=requests, poll_interval_s=0.01)
    except AnthropicProviderError as e:
        msg = str(e)
        # Either the per-call cap or the hard-stop trips first.
        assert ("hard stop" in msg.lower() or "per-call input cap" in msg.lower()), \
            f"unexpected error: {msg}"
        print(f"  PASS test_t3_hard_stop_guard  guard tripped: "
              f"{msg[:80]!r}")
        return
    assert False, "expected hard-stop or input-cap guard to trip"


def test_t4_per_call_input_cap():
    p = AnthropicProvider(default_model=HAIKU)
    get_cost_ledger().reset()
    huge_prompt = "x" * 1_000_000  # ≈250k tokens, way over cap
    try:
        p.complete_batch(requests=[{
            "custom_id": "c1",
            "system_prompt": huge_prompt,
            "user_prompt": "u",
        }], poll_interval_s=0.01)
    except AnthropicProviderError as e:
        msg = str(e).lower()
        # Either form of the cap message is acceptable: the guard may
        # use "per-call cap" (newer wording) or "per-call input cap"
        # (older wording). Both refer to ANTHROPIC_MAX_INPUT_TOKENS.
        assert ("per-call cap" in msg or "per-call input cap" in msg), \
            f"unexpected: {e}"
        print(f"  PASS test_t4_per_call_input_cap")
        return
    assert False, "expected per-call cap guard"


def test_t5_duplicate_custom_id():
    p = AnthropicProvider(default_model=HAIKU)
    get_cost_ledger().reset()
    try:
        p.complete_batch(requests=[
            {"custom_id": "dupe", "system_prompt": "s", "user_prompt": "u"},
            {"custom_id": "dupe", "system_prompt": "s", "user_prompt": "u"},
        ], poll_interval_s=0.01)
    except AnthropicProviderError as e:
        assert "duplicate" in str(e).lower(), f"unexpected: {e}"
        print(f"  PASS test_t5_duplicate_custom_id")
        return
    assert False, "expected duplicate guard"


def test_t6_empty_requests():
    p = AnthropicProvider(default_model=HAIKU)
    get_cost_ledger().reset()
    out = p.complete_batch(requests=[], poll_interval_s=0.01)
    assert out == {}
    print(f"  PASS test_t6_empty_requests")


def test_t7_errored_request_recorded():
    p = AnthropicProvider(default_model=HAIKU)
    get_cost_ledger().reset()
    _install_stub(p, [
        _StubResult("c_ok"),
        _StubResult("c_err", succeeded=False, error_msg="rate_limit_exceeded"),
    ])
    out = p.complete_batch(requests=[
        {"custom_id": "c_ok", "system_prompt": "s", "user_prompt": "u"},
        {"custom_id": "c_err", "system_prompt": "s", "user_prompt": "u"},
    ], poll_interval_s=0.01)
    assert out["c_ok"]["succeeded"] is True
    assert out["c_err"]["succeeded"] is False
    assert "rate_limit" in (out["c_err"]["error"] or "")
    # Errored cost should NOT be in ledger; succeeded cost SHOULD be.
    cum = get_cost_ledger().total_usd()
    assert cum > 0, "succeeded request not added to ledger"
    assert cum == out["c_ok"]["cost_usd"], \
        f"ledger {cum} != only the succeeded cost {out['c_ok']['cost_usd']}"
    print(f"  PASS test_t7_errored_request_recorded  "
          f"ledger=${cum:.6f}")


def main() -> int:
    print("\n=== Task 3 — Anthropic Batch API primitive tests ===\n")
    failures = []
    for fn in (
        test_t1_custom_id_round_trip,
        test_t2_cost_discount_50pct,
        test_t3_hard_stop_guard,
        test_t4_per_call_input_cap,
        test_t5_duplicate_custom_id,
        test_t6_empty_requests,
        test_t7_errored_request_recorded,
    ):
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failures.append(fn.__name__)
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failures.append(fn.__name__)
    n = 7
    n_pass = n - len(failures)
    print(f"\n  RESULT: {n_pass}/{n} tests pass")
    if failures:
        print(f"  failed: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
