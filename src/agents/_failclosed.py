"""Schema-valid fail-closed payload builder.

When LLMJsonParseError exhausts the retry budget (§5.12 cap = 2 retries
/ 3 attempts total), the runner needs a parsed_output that:

  1. Validates against the agent's pydantic schema (so downstream cache
     records and consumers don't choke), and
  2. Carries a "no real model output here" signal so PM / risk / valuation
     consumers don't act on it as if it were a decision.

We get (1) for free by reusing build_stub_skeleton — the same path the
deterministic stub provider uses, which is schema-valid by construction
for every registered role. We stamp (2) by overriding validation_status
and a forensics annotation field.

The skeleton's per-role default values are already the most cautious
legal values from each enum (e.g., verdict="unverifiable",
decision_hint="no_trade", recommendation_to_pm="needs_more_evidence",
catalyst_type="unknown"). Leaving those untouched is what makes this
fail-closed in the trading sense — no role-specific override needed.
"""
from __future__ import annotations

from typing import Any

from src.llm.deterministic_stub import build_stub_skeleton


FAILCLOSED_VALIDATION_STATUS = (
    "schema_failed_returned_needs_more_evidence_after_retry"
)


def build_failclosed_output(
    agent_schema_name: str, error_summary: str
) -> dict[str, Any]:
    """Return a schema-valid `parsed_output` for the named agent role,
    stamped to mark this as a JSON-parse fail-closed (not a real model
    decision).

    Behaviour:
      - Calls build_stub_skeleton(agent_schema_name); raises KeyError on
        unknown role names (same surface as the stub provider).
      - Replaces reasoning_summary with the error summary so post-hoc
        forensics show why the agent failed-closed without changing any
        enum-typed field.
      - Stamps validation_status to the post-retry sentinel.
      - Sets evidence_missing to the JSON-parse marker so downstream PM
        ranking treats this as missing-evidence rather than a real call.
    """
    payload = build_stub_skeleton(agent_schema_name)
    if not isinstance(payload, dict):
        raise TypeError(
            f"build_stub_skeleton({agent_schema_name!r}) did not return "
            f"a dict; cannot construct fail-closed payload."
        )

    # reasoning_summary is a free-form string in the common envelope —
    # safe to overwrite without enum-validity concerns.
    if "reasoning_summary" in payload:
        payload["reasoning_summary"] = error_summary[:1000]

    # evidence_missing is a free-form list of strings.
    if "evidence_missing" in payload:
        payload["evidence_missing"] = [
            "llm_output_was_not_valid_json_after_retry"
        ]

    # NOTE: We deliberately do NOT inject validation_status into the
    # payload. It is not part of any agent's pydantic schema (the
    # schemas use extra="forbid"), and adding it here would break the
    # very property this helper exists to provide — schema validity.
    # The runner stamps validation_status onto the cache record envelope
    # via make_record(...), which is where downstream consumers should
    # read it from. Keeping the FAILCLOSED_VALIDATION_STATUS constant
    # here so callers (the runner) have a single source of truth for the
    # sentinel string.
    return payload
