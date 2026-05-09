"""
Defensive JSON parser for LLM raw_text outputs.

Some models (notably Haiku 4.5) wrap structured output in markdown
code fences despite explicit "no fences" prompt instructions. This
helper centralises a fence-tolerant parse so the runner can stay on a
single chokepoint and the synthetic fail-closed envelope path only
fires for genuinely unrecoverable output.

Behavior, in order:
  1. Strip leading/trailing whitespace.
  2. If the text starts with ``` (with or without a language tag),
     strip the opening fence line and the matching trailing ```.
  3. Strip whitespace again.
  4. json.loads. Return the parsed object on success.
  5. Fallback: regex-extract the largest balanced {...} substring and
     try json.loads on it. Defensive against agents that emit prose
     before/after a JSON object.
  6. If all attempts fail, raise LLMJsonParseError with the original
     raw_text attached for downstream logging.

No silent retries. No prompt edits. The fail-closed envelope path
already in runner.py is preserved — only WHAT triggers it changes.
"""
from __future__ import annotations

import json
import re
from typing import Any


class LLMJsonParseError(ValueError):
    """Raised when raw LLM text cannot be parsed as JSON even after
    fence-stripping and largest-{...}-block extraction."""

    def __init__(self, message: str, raw_text: str, last_error: Exception | None = None) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.last_error = last_error


_FENCE_OPEN_RE = re.compile(r"^```[A-Za-z0-9_+\-]*\s*\n")
_FENCE_CLOSE_RE = re.compile(r"\n```\s*$")


def _strip_fence(text: str) -> str:
    """If text is wrapped in a markdown code fence, return the inner
    content. Tolerates ```json, ```JSON, ```, optional whitespace, and
    optional trailing newline before the closing fence."""
    open_match = _FENCE_OPEN_RE.match(text)
    if not open_match:
        return text
    inner = text[open_match.end():]
    close_match = _FENCE_CLOSE_RE.search(inner)
    if close_match:
        inner = inner[: close_match.start()]
    elif inner.endswith("```"):
        inner = inner[: -3]
    return inner


def _largest_brace_block(text: str) -> str | None:
    """Return the substring spanning the first '{' to the last '}'
    if both exist and the first index precedes the last. Otherwise
    None. This is a coarse fallback — json.loads still has to accept
    the substring."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def parse_llm_json(raw_text: str) -> Any:
    """Parse `raw_text` as JSON, tolerating markdown fences and
    surrounding prose. Raises LLMJsonParseError if all attempts fail."""
    if raw_text is None:
        raise LLMJsonParseError("raw_text is None", raw_text="")
    text = raw_text.strip()
    if not text:
        raise LLMJsonParseError("raw_text is empty", raw_text=raw_text)

    text = _strip_fence(text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        first_error = e

    candidate = _largest_brace_block(text)
    if candidate is not None:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise LLMJsonParseError(
                f"json.loads failed after fence-strip and brace-extract: {e}",
                raw_text=raw_text,
                last_error=e,
            ) from e

    raise LLMJsonParseError(
        f"json.loads failed and no balanced brace block found: {first_error}",
        raw_text=raw_text,
        last_error=first_error,
    )
