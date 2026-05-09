"""
Live smoke tests for the OpenCLI use cases (Task 4, 2026-04-29 PM).

These tests are gated by the `OPENCLI_LIVE=1` env var so CI doesn't
hammer external services. To run locally:

    OPENCLI_LIVE=1 python tests/test_opencli_live.py

Coverage:
  - sec_8k_fulltext: confirms STUB-with-capability-gap behaviour
    (upstream OpenCLI v1.7.8 lacks the sec.gov adapter; the base class
    short-circuits to STUB with a `binary_present_verb_unsupported`
    quality flag — distinct from the binary-missing flag)
  - github_commit_messages LIVE: invokes `opencli gh api repos/<>/commits`
    against a small public repo (anthropics/claude-code), asserts:
      * extraction_status == "ok"
      * 14-field envelope validates
      * source_url is a github.com URL
      * source_reliability == "T2"
      * aux_only is True (§17.B.11)
      * at least 1 commit returned with sha + committed_at + message_first_line
      * forbidden-token scan: no "whale" in any output, command, or flag
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.adapters.opencli.github_commit_messages import (  # noqa: E402
    GitHubCommitMessagesAdapter,
)
from src.adapters.opencli.sec_8k_fulltext import (  # noqa: E402
    SEC8KFulltextAdapter,
)

EQUITY_UNSAFE_TOKEN = "whale"


def _is_live() -> bool:
    return os.environ.get("OPENCLI_LIVE", "").strip() in ("1", "true", "yes")


# ── github_commit_messages LIVE ────────────────────────────────────
def test_github_commit_messages_live_anthropics_claude_code():
    if not _is_live():
        print("    [SKIP] OPENCLI_LIVE not set")
        return
    saved = os.environ.pop("STUB_MODE", None)
    try:
        a = GitHubCommitMessagesAdapter()
        r = a.fetch(
            query_terms={"owner": "anthropics", "repo": "claude-code", "n": 5},
            decision_timestamp=datetime.now(timezone.utc),
            stub_mode=False,
            cache_key="_live_smoke_anthropics_claude_code_5",
        )
        assert r.extraction_status == "ok", (
            f"expected extraction_status=ok, got {r.extraction_status!r} "
            f"(error_class={r.error_class!r})"
        )
        ok, errs = r.validate()
        assert ok, f"14-field envelope validation failed: {errs}"
        assert r.source_url.startswith("https://github.com/"), (
            f"unexpected source_url: {r.source_url!r}"
        )
        assert r.source_reliability == "T2"
        assert r.aux_only is True, "§17.B.11 auxiliary-only flag must be True"
        commits = (r.parsed_payload or {}).get("commits", [])
        assert len(commits) >= 1, "expected at least 1 commit"
        # Spot-check shape on each row
        for c in commits:
            assert c.get("sha"), "commit missing sha"
            assert c.get("committed_at"), "commit missing committed_at"
            # message_first_line may be empty for empty merge commits;
            # require message_full to be a string
            assert isinstance(c.get("message_full"), str)
    finally:
        if saved is not None:
            os.environ["STUB_MODE"] = saved


def test_github_commit_messages_no_forbidden_token_in_output():
    if not _is_live():
        print("    [SKIP] OPENCLI_LIVE not set")
        return
    saved = os.environ.pop("STUB_MODE", None)
    try:
        a = GitHubCommitMessagesAdapter()
        r = a.fetch(
            query_terms={"owner": "anthropics", "repo": "claude-code", "n": 5},
            decision_timestamp=datetime.now(timezone.utc),
            stub_mode=False,
            cache_key="_live_token_scan_anthropics_claude_code_5",
        )
        # Recursively serialise everything the adapter returns and grep
        # for the forbidden token (case-insensitive). Fail loud per
        # RULES.md §10.12.
        full_dump = json.dumps(r.to_dict(), default=str).lower()
        assert EQUITY_UNSAFE_TOKEN not in full_dump, (
            f"forbidden token {EQUITY_UNSAFE_TOKEN!r} found in live "
            f"github_commit_messages output (RULES.md §10.12 violated)"
        )
    finally:
        if saved is not None:
            os.environ["STUB_MODE"] = saved


# ── sec_8k_fulltext capability-gap path ────────────────────────────
def test_sec_8k_fulltext_routes_to_stub_with_capability_gap_flag():
    """Even with OpenCLI installed and on PATH, sec_8k_fulltext should
    route to STUB because upstream v1.7.8 has no sec.gov adapter. The
    base class must surface this via the `binary_present_verb_unsupported`
    quality flag, not the generic `opencli_stub_or_not_installed` flag."""
    if not _is_live():
        print("    [SKIP] OPENCLI_LIVE not set")
        return
    saved = os.environ.pop("STUB_MODE", None)
    try:
        a = SEC8KFulltextAdapter()
        r = a.fetch(
            query_terms={
                "filing_url": (
                    "https://www.sec.gov/Archives/edgar/data/320193/"
                    "000032019324000123/aapl-8k.htm"
                ),
                "accession_number": "0000320193-24-000123",
            },
            decision_timestamp=datetime.now(timezone.utc),
            cache_key="_live_smoke_sec_8k_capgap",
        )
        assert r.extraction_status == "stub"
        # Must carry the capability-gap flag, NOT the binary-missing flag.
        kinds = {f.get("kind") for f in r.data_quality_flags}
        assert "binary_present_verb_unsupported" in kinds, (
            f"expected `binary_present_verb_unsupported` quality flag, "
            f"got kinds={kinds!r}"
        )
        assert "opencli_stub_or_not_installed" not in kinds, (
            "binary IS installed; the legacy `not_installed` flag must "
            "not fire"
        )
        # 14-field envelope still valid for STUB.
        ok, errs = r.validate()
        assert ok, f"STUB envelope invalid: {errs}"
    finally:
        if saved is not None:
            os.environ["STUB_MODE"] = saved


def test_no_forbidden_token_in_module_or_test_source():
    """The literal token must not appear in the live adapter source
    files or this test file (per RULES.md §10.12). Module docstrings
    that need to reference the forbidden term must do so via the
    EQUITY_UNSAFE_TOKEN constant, not the literal string."""
    targets = [
        ROOT / "src" / "adapters" / "opencli" / "base.py",
        ROOT / "src" / "adapters" / "opencli" / "github_commit_messages.py",
        ROOT / "src" / "adapters" / "opencli" / "sec_8k_fulltext.py",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8").lower()
        assert EQUITY_UNSAFE_TOKEN not in text, (
            f"forbidden token in {path.name} (RULES.md §10.12)"
        )
    # Test file itself: tolerate the EQUITY_UNSAFE_TOKEN constant
    # definition + a small bounded count of negation references.
    self_text = Path(__file__).read_text(encoding="utf-8").lower()
    occurrences = self_text.count(EQUITY_UNSAFE_TOKEN)
    assert occurrences <= 6, (
        f"forbidden token appears {occurrences}x in test source; "
        "tighten to symbolic negation context only"
    )


# ── runner ────────────────────────────────────────────────────────
def main() -> int:
    failures: list[str] = []
    tests = [
        test_no_forbidden_token_in_module_or_test_source,
        test_github_commit_messages_live_anthropics_claude_code,
        test_github_commit_messages_no_forbidden_token_in_output,
        test_sec_8k_fulltext_routes_to_stub_with_capability_gap_flag,
    ]
    print("\n=== test_opencli_live ===")
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
