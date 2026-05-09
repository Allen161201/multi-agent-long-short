"""
Throwaway: minimal credit-propagation ping (Step 1, Option A).

ONE Anthropic messages.create call against Haiku 4.5 to confirm the
credit_balance_too_low blocker is resolved. No retries, no fallbacks.

Will be deleted after the verification task closes. Underscore prefix
marks it as not part of the canonical script set.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

MODEL = "claude-haiku-4-5-20251001"
PRICING = {"input": 1.00, "output": 5.00}  # USD per million tokens
PREFIX = "sk-ant-api03-"


def fingerprint(key: str) -> str:
    body = key[len(PREFIX):] if key.startswith(PREFIX) else key
    return body[:8]


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ABORT  ANTHROPIC_API_KEY not set")
        return 2

    print("=== Credit-propagation ping (Step 1, Option A) ===")
    print(f"  model         : {MODEL}")
    print(f"  key fp (8)    : {fingerprint(api_key)}")
    print(f"  key length    : {len(api_key)}")
    print(f"  hash-of-key   : {hashlib.sha256(api_key.encode()).hexdigest()[:8]}")

    try:
        from anthropic import Anthropic
    except ImportError as e:
        print(f"ABORT  anthropic SDK missing: {e}")
        return 3

    client = Anthropic(api_key=api_key, timeout=30.0)

    t0 = time.perf_counter()
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16,
            temperature=0,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"\nFAIL  exception after {elapsed:.2f}s")
        print(f"  type     : {type(e).__name__}")
        print(f"  message  : {e}")
        status = getattr(e, "status_code", None)
        if status is not None:
            print(f"  HTTP     : {status}")
        body = getattr(e, "body", None)
        if body is not None:
            print(f"  body     : {body}")
        if "credit_balance_too_low" in str(e):
            print("\n  >>> credit_balance_too_low STILL PRESENT — STOP, do not proceed to Step 2")
            return 10
        return 11
    elapsed = time.perf_counter() - t0

    text_parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    response_text = "".join(text_parts)

    usage = resp.usage
    in_tok = usage.input_tokens
    out_tok = usage.output_tokens
    cost = (in_tok / 1_000_000) * PRICING["input"] + (out_tok / 1_000_000) * PRICING["output"]

    print("\nOK  HTTP 200 (no exception raised)")
    print(f"  wall-clock    : {elapsed:.3f}s")
    print(f"  response text : {response_text!r}")
    print(f"  stop_reason   : {resp.stop_reason}")
    print(f"  input tokens  : {in_tok}")
    print(f"  output tokens : {out_tok}")
    print(f"  cost USD      : ${cost:.6f}")
    print(f"  message id    : {resp.id}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(99)
