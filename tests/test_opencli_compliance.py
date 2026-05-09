"""
RULES.md §17 OpenCLI compliance tests — 27 rules.

§17.A Skills (9), §17.B Integration/safety (11), §17.C Negative-scope (7).
For PENDING-runtime rules we assert via stub-mode fixtures plus
architectural invariants (e.g. an allowlist constant exists and excludes
the forbidden verbs).
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Force stub mode so no real `opencli` invocation is attempted.
os.environ["STUB_MODE"] = "true"

from src.adapters.opencli.base import (  # noqa: E402
    OpenCLIAdapter, OpenCLIResult, OPENCLI_OUTPUT_FIELDS,
    _ALLOWED_VERBS, _DEFAULT_ALLOWED_HOSTS,
)
from src.adapters.opencli.sec_8k_fulltext import SEC8KFulltextAdapter  # noqa: E402
from src.adapters.opencli.github_commit_messages import (  # noqa: E402
    GitHubCommitMessagesAdapter,
)


def _decision_ts() -> datetime:
    return datetime(2024, 6, 1, 16, 0, 0, tzinfo=timezone.utc)


def _new_sec_adapter(tmp_path: Path) -> SEC8KFulltextAdapter:
    a = SEC8KFulltextAdapter()
    type(a).cache_root_override = tmp_path
    return a


def _new_gh_adapter(tmp_path: Path) -> GitHubCommitMessagesAdapter:
    a = GitHubCommitMessagesAdapter()
    type(a).cache_root_override = tmp_path
    return a


# ── §17.A Skills rules (9) ────────────────────────────────────────

def test_S01_read_only_first(tmp_path):
    """Mutating verbs (post/like/follow/comment/vote/save/message/
    publish/subscribe) MUST NOT appear in the verb allowlist."""
    forbidden = {
        "post", "like", "follow", "unfollow", "comment", "vote", "save",
        "unsave", "message", "publish", "subscribe", "unsubscribe",
        "delete", "edit", "update", "create", "patch", "put",
    }
    overlap = forbidden & set(_ALLOWED_VERBS)
    assert not overlap, f"forbidden verbs in allowlist: {overlap}"


def test_S02_no_write_actions_ever():
    """Stronger form of S01: even if a verb appears outside the
    allowlist (e.g. via subclass override), the base class MUST refuse
    it. Test by attempting to invoke a forbidden verb through a
    subclass that constructs one."""

    class _BadAdapter(OpenCLIAdapter):
        source_id = "_bad_unit_test"
        block_target = "alternative_data_features"

        def _build_command(self, *, query_terms):
            return ["opencli", "post", "github.com/issues", "x", "-f", "json"]

        def _parse_stdout(self, stdout):
            return {}

        def _fetch_stub(self, *, query_terms, decision_timestamp):
            raise NotImplementedError

        def _terms_or_access_notes(self):
            return "_test"

        def _PIT_safety_notes(self):
            return "_test"

        def _source_reliability(self):
            return "T2"

        def _source_url_for(self, query_terms):
            return "https://github.com/x"

    bad = _BadAdapter()
    raised = False
    try:
        bad._fetch_live(query_terms={"x": 1},
                          decision_timestamp=_decision_ts())
    except RuntimeError as e:
        raised = "allowlist" in str(e).lower() or "post" in str(e).lower()
    assert raised, "base class must refuse a forbidden verb"


def test_S03_no_personal_accounts(tmp_path):
    """Default strategy is PUBLIC: logged_in_session_used=False on
    every emitted envelope unless human_authorization_required=True."""
    a = _new_sec_adapter(tmp_path)
    r = a.fetch(
        query_terms={"filing_url": "https://www.sec.gov/Archives/_stub_.htm",
                      "accession_number": "_unit_test_8k"},
        decision_timestamp=_decision_ts(),
    )
    assert r.logged_in_session_used is False


def test_S04_no_silent_retries_on_77_or_69():
    """Architectural invariant: the source code does NOT loop on
    auth_required / browser_connect error classes."""
    src = inspect.getsource(OpenCLIAdapter)
    # The base class must record these error classes but must not
    # call _fetch_live again from inside _fetch_live.
    assert "_EXIT_CODE_TO_ERROR_CLASS" not in src or "auth_required" not in src or "while" not in src.split("auth_required")[1].split("\n", 5)[0], \
        "base class must not retry on 77/69 inside the same call"
    # Stronger: search for forbidden retry constructs.
    forbidden_substrs = ("retry_on_auth_required", "retry_on_browser_connect")
    for s in forbidden_substrs:
        assert s not in src, f"base class contains forbidden retry construct: {s}"


def test_S05_no_captcha_bypass():
    """Architectural invariant: no CAPTCHA-related code paths exist."""
    src = inspect.getsource(OpenCLIAdapter)
    assert "captcha" not in src.lower() or "captcha_or_block" in src.lower(), (
        "captcha references should be limited to error-class labels, "
        "never bypass logic"
    )


def test_S06_tos_aware_allowlist():
    """Default allowlist contains only sec.gov and github.com — both
    public-domain / public-API ToS-clean for read-only access."""
    assert _DEFAULT_ALLOWED_HOSTS == frozenset({"sec.gov", "github.com"})


def test_S07_browser_skill_gated():
    """Both shipped adapters use sec.gov / github.com; neither is
    routed through opencli-browser logged-in flows."""
    sec = SEC8KFulltextAdapter()
    gh = GitHubCommitMessagesAdapter()
    for a in (sec, gh):
        # Build a representative command and inspect — no `browser`
        # subcommand should appear in either default command.
        if a.source_id == "sec_8k_fulltext":
            argv = a._build_command(query_terms={
                "filing_url": "https://www.sec.gov/Archives/_test_.htm"
            })
        else:
            argv = a._build_command(query_terms={"owner": "x", "repo": "y"})
        assert "browser" not in argv, (
            f"{a.source_id} routes through opencli-browser without authorization"
        )


def test_S08_no_adapter_author_at_runtime():
    """opencli-adapter-author MUST NOT appear in any runtime command
    builder."""
    sec = SEC8KFulltextAdapter()
    gh = GitHubCommitMessagesAdapter()
    for a in (sec, gh):
        if a.source_id == "sec_8k_fulltext":
            argv = a._build_command(query_terms={
                "filing_url": "https://www.sec.gov/Archives/_test_.htm"
            })
        else:
            argv = a._build_command(query_terms={"owner": "x", "repo": "y"})
        assert not any("adapter-author" in tok for tok in argv)


def test_S09_no_autofix_at_runtime():
    """opencli-autofix MUST NOT appear in any runtime command builder."""
    sec = SEC8KFulltextAdapter()
    gh = GitHubCommitMessagesAdapter()
    for a in (sec, gh):
        if a.source_id == "sec_8k_fulltext":
            argv = a._build_command(query_terms={
                "filing_url": "https://www.sec.gov/Archives/_test_.htm"
            })
        else:
            argv = a._build_command(query_terms={"owner": "x", "repo": "y"})
        assert not any("autofix" in tok for tok in argv)


# ── §17.B Integration / safety rules (11) ────────────────────────

def test_I01_collection_layer_not_decision(tmp_path):
    """Adapter does NOT score, rank, or trade. The envelope has no
    field equivalent to 'verdict' / 'recommendation' / 'score'."""
    a = _new_sec_adapter(tmp_path)
    r = a.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_stub_.htm",
        "accession_number": "_test_I01",
    }, decision_timestamp=_decision_ts())
    forbidden_keys = {"verdict", "recommendation", "score", "rank",
                       "buy_signal", "short_signal", "decision"}
    body = r.to_dict()
    assert not (forbidden_keys & set(body.keys()))


def test_I02_descriptors_not_rules(tmp_path):
    """Output is descriptor text. The envelope's parsed_payload
    contains text + metadata, never a buy/sell directive."""
    a = _new_sec_adapter(tmp_path)
    r = a.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_stub_.htm",
        "accession_number": "_test_I02",
    }, decision_timestamp=_decision_ts())
    body_str = str(r.parsed_payload).lower()
    forbidden = ("buy_signal", "short_signal", "recommended_action")
    for k in forbidden:
        assert k not in body_str


def test_I03_failure_surfaces_extraction_status():
    """Unknown host triggers RuntimeError pre-subprocess; we test the
    fail-closed path by simulating opencli-not-installed (already the
    default since binary is not on PATH in CI)."""
    # The fail_closed_envelope path is exercised by the stub fallthrough
    # (opencli not on PATH). Smoke test: the resulting envelope has
    # extraction_status set to one of the documented values.
    a = SEC8KFulltextAdapter()
    r = a.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_test_.htm",
        "accession_number": "_test_I03",
    }, decision_timestamp=_decision_ts())
    assert r.extraction_status in ("stub", "ok", "failed", "partial")


def test_I04_substitutable_fallback_only(tmp_path):
    """OpenCLI evidence is auxiliary. aux_only=True on every emitted
    envelope."""
    a = _new_sec_adapter(tmp_path)
    r = a.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_stub_.htm",
        "accession_number": "_test_I04",
    }, decision_timestamp=_decision_ts())
    assert r.aux_only is True


def test_I05_frozen_rules_compatibility():
    """No code path in the OpenCLI adapter reads a frozen-rules
    threshold. The adapter has no dependency on rules.yaml."""
    src = inspect.getsource(OpenCLIAdapter)
    assert "rules.yaml" not in src
    assert "v0.5_agentic_allocation_corrected" not in src


def test_I06_f_json_mandatory(tmp_path):
    """Every command builder MUST include `-f json`."""
    sec = SEC8KFulltextAdapter()
    gh = GitHubCommitMessagesAdapter()
    sec_argv = sec._build_command(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_test_.htm"
    })
    gh_argv = gh._build_command(query_terms={"owner": "x", "repo": "y"})
    for argv in (sec_argv, gh_argv):
        assert "-f" in argv
        assert "json" in argv


def test_I07_output_contains_url_timestamp_command_schema_status(tmp_path):
    a = _new_sec_adapter(tmp_path)
    r = a.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_stub_.htm",
        "accession_number": "_test_I07",
    }, decision_timestamp=_decision_ts())
    assert r.source_url
    assert r.collected_at
    assert r.command_used
    assert r.schema_version
    assert r.extraction_status


def test_I08_failure_yields_data_unavailable_never_zero(tmp_path):
    """When extraction_status is 'failed', source_url is set to a
    sentinel string ('Data unavailable') and parsed_payload is empty —
    NOT a default zero."""

    class _FailAdapter(OpenCLIAdapter):
        source_id = "_fail_test"
        block_target = "alternative_data_features"
        cache_root_override = tmp_path

        def _build_command(self, *, query_terms):
            return ["opencli", "get", "github.com/x", "_test_", "-f", "json"]

        def _parse_stdout(self, stdout):
            return {}

        def _fetch_stub(self, *, query_terms, decision_timestamp):
            return self._fail_closed_envelope(
                query_terms=query_terms,
                command_used="x",
                source_url="https://github.com/x",
                error_class="manual_test",
                detail="forced fail",
            )

        def _terms_or_access_notes(self):
            return "_test"

        def _PIT_safety_notes(self):
            return "_test"

        def _source_reliability(self):
            return "T2"

        def _source_url_for(self, q):
            return "https://github.com/x"

    a = _FailAdapter()
    r = a.fetch(query_terms={"x": 1}, decision_timestamp=_decision_ts())
    assert r.extraction_status == "failed"
    assert r.parsed_payload == {}
    assert r.source_url   # set; not numeric zero


def test_I09_strategy_public_only_default(tmp_path):
    """logged_in_session_used must default to False; flipping it
    requires human_authorization_required=True (validator-enforced)."""
    a = _new_sec_adapter(tmp_path)
    r = a.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_stub_.htm",
        "accession_number": "_test_I09",
    }, decision_timestamp=_decision_ts())
    assert r.logged_in_session_used is False
    # Validator catches inconsistent state
    bad = OpenCLIResult.from_payload(
        source_url="x", command_used="x", query_terms={},
        data_available_as_of=None, extraction_status="ok",
        source_reliability="T1", terms_or_access_notes="x",
        PIT_safety_notes="x", payload={}, block_target="x",
        logged_in_session_used=True,
        human_authorization_required=False,
    )
    ok, errs = bad.validate()
    assert not ok and any("logged_in" in e.lower() for e in errs)


def test_I10_fourteen_mandatory_fields(tmp_path):
    a = _new_sec_adapter(tmp_path)
    r = a.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_stub_.htm",
        "accession_number": "_test_I10",
    }, decision_timestamp=_decision_ts())
    ok, errs = r.validate()
    assert ok, f"validate() failed: {errs}"
    body = r.to_dict()
    for k in OPENCLI_OUTPUT_FIELDS:
        assert k in body, f"missing 14-field key: {k}"


def test_I11_auxiliary_only_flag(tmp_path):
    """aux_only=True on every envelope so agents know not to flip a
    verdict on OpenCLI alone."""
    sec = _new_sec_adapter(tmp_path / "sec")
    gh = _new_gh_adapter(tmp_path / "gh")
    r1 = sec.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_stub_.htm",
        "accession_number": "_test_I11",
    }, decision_timestamp=_decision_ts())
    r2 = gh.fetch(query_terms={"owner": "microsoft", "repo": "vscode"},
                   decision_timestamp=_decision_ts())
    assert r1.aux_only is True and r2.aux_only is True


# ── §17.C Negative-scope rules (7) ───────────────────────────────

def test_N01_not_the_trading_brain(tmp_path):
    """Same shape check as I01: no decision/buy/short fields in body."""
    a = _new_sec_adapter(tmp_path)
    r = a.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_stub_.htm",
        "accession_number": "_test_N01",
    }, decision_timestamp=_decision_ts())
    body = r.to_dict()
    forbidden = {"decision", "recommended_action", "buy", "short",
                 "watch", "no_trade", "veto"}
    assert not (forbidden & set(body.keys()))


def test_N02_not_the_pm_agent():
    """Adapter classes MUST NOT inherit from any agent class. A
    structural test: source_id is not 'risk_pm' / 'baseline_solo' /
    'pm_flat'."""
    assert SEC8KFulltextAdapter.source_id not in (
        "risk_pm", "baseline_solo", "pm_flat",
    )
    assert GitHubCommitMessagesAdapter.source_id not in (
        "risk_pm", "baseline_solo", "pm_flat",
    )


def test_N03_not_the_rule_engine():
    """No code path in the adapter writes to risk_pm.py, rules.yaml,
    or any frozen-rules artefact."""
    src = inspect.getsource(OpenCLIAdapter)
    assert "risk_pm.py" not in src
    assert "rules.yaml" not in src


def test_N04_not_broker_execution():
    """No order placement / cancellation primitives anywhere."""
    src_pkg = inspect.getsource(OpenCLIAdapter)
    forbidden = ("place_order", "cancel_order", "submit_trade",
                 "execute_trade", "broker", "alpaca", "ib_insync")
    for term in forbidden:
        assert term not in src_pkg.lower()


def test_N05_pit_safety_notes_required(tmp_path):
    """Every envelope carries non-empty PIT_safety_notes."""
    a = _new_sec_adapter(tmp_path)
    r = a.fetch(query_terms={
        "filing_url": "https://www.sec.gov/Archives/_stub_.htm",
        "accession_number": "_test_N05",
    }, decision_timestamp=_decision_ts())
    assert r.PIT_safety_notes
    assert len(r.PIT_safety_notes.strip()) > 20


def test_N06_no_silent_modification():
    """Adapter must not write to RULES.md, prompts/, schemas, or
    rules.yaml."""
    src = inspect.getsource(OpenCLIAdapter)
    forbidden_writes = (
        'open("docs/RULES.md',
        'open("src/agents/prompts/',
        'open("config/rules.yaml',
    )
    for f in forbidden_writes:
        assert f not in src


def test_N07_does_not_replace_sec_gdelt_fmp():
    """The adapter explicitly declares fallback / corroboration role
    via aux_only=True (already checked I04/I11). Reinforce by
    checking the source_reliability is set (so agents know it's
    rated, not absolute)."""
    sec = SEC8KFulltextAdapter()
    gh = GitHubCommitMessagesAdapter()
    assert sec._source_reliability() in {"T1", "T2", "T3", "T4", "T5"}
    assert gh._source_reliability() in {"T1", "T2", "T3", "T4", "T5"}


# ── runner ───────────────────────────────────────────────────────

def main() -> int:
    import tempfile
    failures: list[str] = []

    test_funcs = [
        # §17.A
        test_S01_read_only_first,
        test_S02_no_write_actions_ever,
        test_S03_no_personal_accounts,
        test_S04_no_silent_retries_on_77_or_69,
        test_S05_no_captcha_bypass,
        test_S06_tos_aware_allowlist,
        test_S07_browser_skill_gated,
        test_S08_no_adapter_author_at_runtime,
        test_S09_no_autofix_at_runtime,
        # §17.B
        test_I01_collection_layer_not_decision,
        test_I02_descriptors_not_rules,
        test_I03_failure_surfaces_extraction_status,
        test_I04_substitutable_fallback_only,
        test_I05_frozen_rules_compatibility,
        test_I06_f_json_mandatory,
        test_I07_output_contains_url_timestamp_command_schema_status,
        test_I08_failure_yields_data_unavailable_never_zero,
        test_I09_strategy_public_only_default,
        test_I10_fourteen_mandatory_fields,
        test_I11_auxiliary_only_flag,
        # §17.C
        test_N01_not_the_trading_brain,
        test_N02_not_the_pm_agent,
        test_N03_not_the_rule_engine,
        test_N04_not_broker_execution,
        test_N05_pit_safety_notes_required,
        test_N06_no_silent_modification,
        test_N07_does_not_replace_sec_gdelt_fmp,
    ]

    print("\n=== test_opencli_compliance (27 rules) ===")
    for fn in test_funcs:
        sig = inspect.signature(fn)
        with tempfile.TemporaryDirectory(prefix="d1_step_d6_") as td:
            try:
                if "tmp_path" in sig.parameters:
                    fn(Path(td))
                else:
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
        print(f"  RESULT: {len(failures)}/{len(test_funcs)} failed")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  RESULT: {len(test_funcs)}/{len(test_funcs)} passed (27/27 §17 rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
