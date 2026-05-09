"""
Unit test for src/agents/_json_parse.parse_llm_json.

Direct-execution / __main__-style test (matches the
test_regression_matrix.py convention; pytest is not installed in the
project's interpreters). Run with:

    python tests/test_json_parse_helper.py

Exit code 0 on all-pass, 1 on any failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.agents._json_parse import LLMJsonParseError, parse_llm_json  # noqa: E402


def _check(label: str, actual, expected) -> tuple[bool, str]:
    ok = actual == expected
    return ok, f"  {('PASS' if ok else 'FAIL'):4s}  {label}  expected={expected!r}  actual={actual!r}"


def _check_raises(label: str, fn) -> tuple[bool, str]:
    try:
        result = fn()
    except LLMJsonParseError as e:
        return True, f"  PASS  {label}  raised LLMJsonParseError: {e}"
    except Exception as e:  # pragma: no cover
        return False, f"  FAIL  {label}  raised {type(e).__name__} (expected LLMJsonParseError): {e}"
    return False, f"  FAIL  {label}  did not raise; returned {result!r}"


def main() -> int:
    print("\n=== parse_llm_json unit test ===\n")
    rows: list[tuple[bool, str]] = []

    # 1. Plain JSON.
    rows.append(_check(
        "1. plain JSON",
        parse_llm_json('{"a": 1}'),
        {"a": 1},
    ))

    # 2. ```json fenced.
    rows.append(_check(
        "2. ```json fenced",
        parse_llm_json('```json\n{"a": 1}\n```'),
        {"a": 1},
    ))

    # 2b. Uppercase ```JSON tag.
    rows.append(_check(
        "2b. ```JSON fenced (uppercase tag)",
        parse_llm_json('```JSON\n{"a": 1}\n```'),
        {"a": 1},
    ))

    # 3. ``` (no language) fenced.
    rows.append(_check(
        "3. ``` fenced (no language tag)",
        parse_llm_json('```\n{"a": 1}\n```'),
        {"a": 1},
    ))

    # 4. Fenced with leading/trailing whitespace and newlines.
    rows.append(_check(
        "4. fenced with surrounding whitespace",
        parse_llm_json('   \n\n```json\n{"a": 1, "b": [2, 3]}\n```\n\n   '),
        {"a": 1, "b": [2, 3]},
    ))

    # 5. JSON with surrounding prose (fallback path).
    rows.append(_check(
        "5. surrounding prose (fallback)",
        parse_llm_json('Here is the answer:\n{"a": 1}\nThanks!'),
        {"a": 1},
    ))

    # 6. Truly malformed.
    rows.append(_check_raises(
        "6. malformed input raises",
        lambda: parse_llm_json("not json at all"),
    ))

    # 7. Empty string.
    rows.append(_check_raises(
        "7. empty string raises",
        lambda: parse_llm_json(""),
    ))

    # Bonus: realistic Haiku 4.5 output shape from this morning's smoke.
    rows.append(_check(
        "8. realistic Haiku-style fenced output",
        parse_llm_json(
            '```json\n'
            '{\n'
            '  "agent_id": "Agent_02",\n'
            '  "decision_or_assessment": "insufficient",\n'
            '  "confidence": "low"\n'
            '}\n'
            '```'
        ),
        {
            "agent_id": "Agent_02",
            "decision_or_assessment": "insufficient",
            "confidence": "low",
        },
    ))

    for ok, msg in rows:
        print(msg)

    n_pass = sum(1 for ok, _ in rows if ok)
    n_total = len(rows)
    print(f"\n  RESULT: {n_pass}/{n_total} cases passed")
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
