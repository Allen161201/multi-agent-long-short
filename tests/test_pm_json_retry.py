"""
D5 Task 1.1 — PM JSON retry policy in run_agent.

Six cases (script-style; run with `python tests/test_pm_json_retry.py`):

  1. Stub bypass        — DeterministicStubProvider takes max_attempts=1
                          (no retry path).
  2. First-attempt OK   — valid JSON on attempt 1; no retry telemetry,
                          no CSV row.
  3. Recovered att. 2   — malformed → valid; telemetry recovered=True,
                          attempts_made=2; CSV has {failed_will_retry,
                          recovered_attempt_2}.
  4. Exhausted          — malformed × 3; parsed_output is fail-closed,
                          schema-valid; CSV has {failed_will_retry,
                          failed_will_retry, exhausted}.
  5. Schema validity    — build_failclosed_output validates against the
                          pydantic schema for every registered role.
  6. CSV append          — header written once across two runs;
                          subsequent rows appended without re-headering.

The tests never make a real HTTP call — provider.complete is replaced
with a deterministic scripted stub.
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.agents import runner as runner_mod  # noqa: E402
from src.agents._failclosed import (  # noqa: E402
    FAILCLOSED_VALIDATION_STATUS,
    build_failclosed_output,
)
from src.agents.schemas import SCHEMA_REGISTRY  # noqa: E402
from src.llm.cache import LLMCache  # noqa: E402
from src.llm.deterministic_stub import (  # noqa: E402
    DeterministicStubProvider,
    build_stub_skeleton,
)
from src.llm.provider import LLMProvider  # noqa: E402


# ── Test scaffolding ────────────────────────────────────────────────


class _ScriptedProvider(LLMProvider):
    """Returns each scripted raw_text in order. Asserts on overflow.

    `name` defaults to "scripted-anthropic" so the runner does NOT take
    the deterministic-stub bypass branch — that lets us exercise the
    retry path on a non-stub provider as it would behave in production.
    """

    def __init__(self, *raw_texts: str, provider_name: str = "scripted-anthropic"):
        self._scripted = list(raw_texts)
        self._idx = 0
        self.call_count = 0
        self.last_user_prompt: str = ""
        self.user_prompts: list[str] = []
        self.name = provider_name  # set on instance, NOT class

    def complete(self, **kwargs) -> dict[str, Any]:
        if self._idx >= len(self._scripted):
            raise AssertionError(
                f"unexpected call #{self._idx + 1}; only "
                f"{len(self._scripted)} scripted"
            )
        text = self._scripted[self._idx]
        self._idx += 1
        self.call_count += 1
        self.last_user_prompt = kwargs.get("user_prompt", "")
        self.user_prompts.append(self.last_user_prompt)
        return {
            "raw_text": text,
            "model_id": "scripted-v1",
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 1,
            "stop_reason": "end_turn",
            "provider": self.name,
            "cache_used": False,
        }


def _valid_pm_json() -> str:
    """A schema-valid PM payload — built from build_stub_skeleton so it
    actually validates against PMAgentOutput."""
    return json.dumps(build_stub_skeleton("PMAgentOutput"))


def _malformed_json() -> str:
    """Truncated JSON — parse_llm_json fails."""
    return '{"agent_schema_name": "PMAgentOutput", "decision": "watch"'


def _build_packet(
    ticker: str = "AAPL", ts: str = "2026-04-29T16:15:00-04:00"
) -> dict:
    return {
        "envelope": {
            "ticker": ticker,
            "decision_timestamp": ts,
            "evidence_packet_hash": "sha256:test_hash_1234567890",
        },
        "news_event_summary": {"items": []},
    }


def _check(label: str, cond: bool, detail: str = "") -> bool:
    marker = "PASS" if cond else "FAIL"
    print(f"  [{marker}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _redirect_csv(tmp_csv: Path):
    """Context-managed redirect of the runner's CSV path. Returns a
    callable that restores the original path."""
    orig = runner_mod._PM_JSON_FAILURES_CSV
    runner_mod._PM_JSON_FAILURES_CSV = tmp_csv

    def restore() -> None:
        runner_mod._PM_JSON_FAILURES_CSV = orig

    return restore


# ── Cases ───────────────────────────────────────────────────────────


def case_1_stub_bypass() -> bool:
    print("\nCase 1 — DeterministicStubProvider takes the no-retry path")
    with tempfile.TemporaryDirectory() as tmp:
        cache = LLMCache(root=Path(tmp))
        tmp_csv = Path(tmp) / "pm_json_failures.csv"
        restore = _redirect_csv(tmp_csv)
        try:
            provider = DeterministicStubProvider()
            packet = _build_packet()
            rec = runner_mod.run_agent(
                agent_name="pm",
                evidence_packet=packet,
                candidate_type="quality_long",
                provider=provider,
                cache=cache,
                force_refresh=True,
            )
            ok = True
            ok &= _check(
                "provider.name == 'deterministic_stub'",
                provider.name == "deterministic_stub",
            )
            ok &= _check(
                "validation_status is 'ok' (skeleton validates)",
                rec["validation_status"] == "ok",
                f"got {rec['validation_status']!r}",
            )
            ok &= _check(
                "no _pm_json_retry telemetry stamped",
                "_pm_json_retry" not in rec["parsed_output"],
            )
            ok &= _check(
                "no CSV rows written",
                len(_read_csv_rows(tmp_csv)) == 0,
            )
        finally:
            restore()
    return ok


def case_2_first_attempt_success() -> bool:
    print("\nCase 2 — first-attempt success: no retry, no telemetry")
    with tempfile.TemporaryDirectory() as tmp:
        cache = LLMCache(root=Path(tmp))
        tmp_csv = Path(tmp) / "pm_json_failures.csv"
        restore = _redirect_csv(tmp_csv)
        try:
            provider = _ScriptedProvider(_valid_pm_json())
            packet = _build_packet()
            rec = runner_mod.run_agent(
                agent_name="pm",
                evidence_packet=packet,
                candidate_type="quality_long",
                provider=provider,
                cache=cache,
                force_refresh=True,
            )
            ok = True
            ok &= _check(
                "provider called exactly once",
                provider.call_count == 1,
                f"got {provider.call_count}",
            )
            ok &= _check(
                "validation_status == 'ok'",
                rec["validation_status"] == "ok",
                f"got {rec['validation_status']!r}",
            )
            ok &= _check(
                "no _pm_json_retry telemetry stamped",
                "_pm_json_retry" not in rec["parsed_output"],
            )
            ok &= _check(
                "no CSV rows written",
                len(_read_csv_rows(tmp_csv)) == 0,
            )
            ok &= _check(
                "user_prompt did NOT contain retry clarifier #1",
                runner_mod._RETRY_CLARIFIER_1 not in provider.user_prompts[0],
            )
        finally:
            restore()
    return ok


def case_3_recovered_attempt_2() -> bool:
    print("\nCase 3 — malformed → valid; recovered on attempt 2")
    with tempfile.TemporaryDirectory() as tmp:
        cache = LLMCache(root=Path(tmp))
        tmp_csv = Path(tmp) / "pm_json_failures.csv"
        restore = _redirect_csv(tmp_csv)
        try:
            provider = _ScriptedProvider(_malformed_json(), _valid_pm_json())
            packet = _build_packet()
            rec = runner_mod.run_agent(
                agent_name="pm",
                evidence_packet=packet,
                candidate_type="quality_long",
                provider=provider,
                cache=cache,
                force_refresh=True,
            )
            ok = True
            ok &= _check(
                "provider called exactly twice",
                provider.call_count == 2,
                f"got {provider.call_count}",
            )
            ok &= _check(
                "validation_status == 'ok' (recovered)",
                rec["validation_status"] == "ok",
                f"got {rec['validation_status']!r}",
            )
            tel = rec["parsed_output"].get("_pm_json_retry") or {}
            ok &= _check(
                "telemetry: recovered=True, attempts_made=2",
                tel.get("recovered") is True
                and tel.get("attempts_made") == 2,
                f"got {tel}",
            )
            ok &= _check(
                "attempt 2 prompt contained retry clarifier #1",
                runner_mod._RETRY_CLARIFIER_1 in provider.user_prompts[1],
            )
            ok &= _check(
                "attempt 2 prompt did NOT contain final-attempt clarifier #2",
                runner_mod._RETRY_CLARIFIER_2 not in provider.user_prompts[1],
            )
            rows = _read_csv_rows(tmp_csv)
            ok &= _check(
                "csv has exactly 2 rows",
                len(rows) == 2, f"got {len(rows)}",
            )
            if len(rows) == 2:
                ok &= _check(
                    "row 1 outcome=failed_will_retry, attempt=1",
                    rows[0]["final_outcome"] == "failed_will_retry"
                    and rows[0]["attempt_number"] == "1",
                    f"got {rows[0]}",
                )
                ok &= _check(
                    "row 2 outcome=recovered_attempt_2, attempt=2",
                    rows[1]["final_outcome"] == "recovered_attempt_2"
                    and rows[1]["attempt_number"] == "2",
                    f"got {rows[1]}",
                )
        finally:
            restore()
    return ok


def case_4_exhausted() -> bool:
    print("\nCase 4 — malformed × 3: exhausted, fail-closed payload")
    with tempfile.TemporaryDirectory() as tmp:
        cache = LLMCache(root=Path(tmp))
        tmp_csv = Path(tmp) / "pm_json_failures.csv"
        restore = _redirect_csv(tmp_csv)
        try:
            provider = _ScriptedProvider(
                _malformed_json(), _malformed_json(), _malformed_json()
            )
            packet = _build_packet()
            rec = runner_mod.run_agent(
                agent_name="pm",
                evidence_packet=packet,
                candidate_type="quality_long",
                provider=provider,
                cache=cache,
                force_refresh=True,
            )
            ok = True
            ok &= _check(
                "provider called exactly three times",
                provider.call_count == 3,
                f"got {provider.call_count}",
            )
            ok &= _check(
                "validation_status is the after_retry sentinel",
                rec["validation_status"] == FAILCLOSED_VALIDATION_STATUS,
                f"got {rec['validation_status']!r}",
            )
            # parsed_output should be schema-valid (build_failclosed_output
            # uses build_stub_skeleton, which validates by construction).
            cls = SCHEMA_REGISTRY["PMAgentOutput"]
            try:
                # Strip telemetry before validating — _pm_json_retry isn't
                # part of the pydantic schema.
                payload_for_validation = {
                    k: v for k, v in rec["parsed_output"].items()
                    if k != "_pm_json_retry"
                }
                cls.model_validate(payload_for_validation)
                schema_valid = True
                schema_err = ""
            except Exception as e:  # pragma: no cover - reported as test fail
                schema_valid = False
                schema_err = str(e)[:200]
            ok &= _check(
                "fail-closed payload validates against PMAgentOutput",
                schema_valid, schema_err,
            )
            ok &= _check(
                "evidence_missing flagged as JSON-parse failure",
                "llm_output_was_not_valid_json_after_retry"
                in (rec["parsed_output"].get("evidence_missing") or []),
            )
            tel = rec["parsed_output"].get("_pm_json_retry") or {}
            ok &= _check(
                "telemetry: recovered=False, attempts_made=3",
                tel.get("recovered") is False
                and tel.get("attempts_made") == 3,
                f"got {tel}",
            )
            ok &= _check(
                "attempt 3 prompt contained final-attempt clarifier #2",
                runner_mod._RETRY_CLARIFIER_2 in provider.user_prompts[2],
            )
            rows = _read_csv_rows(tmp_csv)
            ok &= _check(
                "csv has exactly 3 rows",
                len(rows) == 3, f"got {len(rows)}",
            )
            if len(rows) == 3:
                outcomes = [r["final_outcome"] for r in rows]
                ok &= _check(
                    "outcomes=[failed_will_retry, failed_will_retry, exhausted]",
                    outcomes == [
                        "failed_will_retry",
                        "failed_will_retry",
                        "exhausted",
                    ],
                    f"got {outcomes}",
                )
        finally:
            restore()
    return ok


def case_5_failclosed_schema_valid_per_role() -> bool:
    print("\nCase 5 — fail-closed payload validates for every registered role")
    ok = True
    for schema_name in sorted(SCHEMA_REGISTRY):
        try:
            payload = build_failclosed_output(
                schema_name, "synthetic test error"
            )
            cls = SCHEMA_REGISTRY[schema_name]
            cls.model_validate(payload)
            ok &= _check(f"{schema_name}: validates", True)
        except Exception as e:
            ok &= _check(
                f"{schema_name}: validates", False, str(e)[:200],
            )
    return ok


def case_6_csv_append_discipline() -> bool:
    print("\nCase 6 — CSV header written once; subsequent rows appended")
    with tempfile.TemporaryDirectory() as tmp:
        cache = LLMCache(root=Path(tmp))
        tmp_csv = Path(tmp) / "pm_json_failures.csv"
        restore = _redirect_csv(tmp_csv)
        try:
            # Run 1: 2 attempts, 1 failure row + 1 recovery row → 2 rows.
            provider1 = _ScriptedProvider(_malformed_json(), _valid_pm_json())
            runner_mod.run_agent(
                agent_name="pm",
                evidence_packet=_build_packet("AAPL"),
                candidate_type="quality_long",
                provider=provider1,
                cache=cache,
                force_refresh=True,
            )
            after_run1 = _read_csv_rows(tmp_csv)
            # Run 2: a different ticker → different cache_key. 3 failures.
            provider2 = _ScriptedProvider(
                _malformed_json(), _malformed_json(), _malformed_json()
            )
            runner_mod.run_agent(
                agent_name="pm",
                evidence_packet=_build_packet("MSFT"),
                candidate_type="quality_long",
                provider=provider2,
                cache=cache,
                force_refresh=True,
            )
            after_run2 = _read_csv_rows(tmp_csv)

            ok = True
            ok &= _check(
                "after run 1: csv has 2 rows",
                len(after_run1) == 2, f"got {len(after_run1)}",
            )
            ok &= _check(
                "after run 2: csv has 5 rows total",
                len(after_run2) == 5, f"got {len(after_run2)}",
            )
            # Header written exactly once — read raw bytes and count lines
            # whose first cell is "timestamp_utc".
            with tmp_csv.open(encoding="utf-8") as f:
                header_count = sum(
                    1 for line in f if line.startswith("timestamp_utc,")
                )
            ok &= _check(
                "header line appears exactly once",
                header_count == 1, f"got {header_count}",
            )
        finally:
            restore()
    return ok


def main() -> int:
    print("=== PM JSON retry tests (D5 Task 1.1, 6 cases) ===")
    results = [
        case_1_stub_bypass(),
        case_2_first_attempt_success(),
        case_3_recovered_attempt_2(),
        case_4_exhausted(),
        case_5_failclosed_schema_valid_per_role(),
        case_6_csv_append_discipline(),
    ]
    passed = sum(results)
    total = len(results)
    if passed == total:
        print(f"\nRESULT: {passed}/{total} cases PASS")
        return 0
    print(f"\nRESULT: {passed}/{total} cases PASS — failures present")
    return 1


if __name__ == "__main__":
    sys.exit(main())
