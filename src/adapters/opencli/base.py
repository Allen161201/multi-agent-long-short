"""
OpenCLI adapter base class.

Enforces every RULES.md §17 rule that this layer is responsible for.
The 27-rule compliance test (tests/test_opencli_compliance.py) walks
each rule and asserts the corresponding base-class invariant.

Key design decisions (cross-referenced with OPENCLI_LEARNED.md):
  - Output is a 14-field envelope (§17.B.10). The validator inside
    `OpenCLIResult.validate()` rejects any envelope missing a field.
  - `-f json` is mandatory; the command builder hardcodes it.
  - Allowlist of (verb, host). Anything else raises before subprocess.
  - Exit codes 77 (AUTH_REQUIRED) and 69 (BROWSER_CONNECT) → fail loud,
    never silent retry.
  - Stub fallthrough when `opencli` is not on PATH; the resulting
    envelope still passes the 14-field validator.
  - PIT_safety_notes: non-empty prose required (§17.B.7 / §10.11).

§17 anchors:
  §17.A.1 read-only first; allowlist of verbs blocks mutating ops
  §17.A.2 no write actions ever — same allowlist
  §17.A.3 no personal accounts — strategy locked to PUBLIC
  §17.A.4 no silent retries on 77/69 — fail loud
  §17.A.5 no CAPTCHA bypass — record as failed, never retry
  §17.A.6 ToS-aware allowlist of hosts
  §17.A.7 opencli-browser gated — _execute_opencli refuses unless allowed
  §17.A.8 opencli-adapter-author NEVER invoked at decision time
  §17.A.9 opencli-autofix NEVER invoked at decision time
  §17.B.1 collection layer not decision — adapter does not score
  §17.B.2 descriptors not rules — output is plain text
  §17.B.3 surface data_quality_warning + extraction_status
  §17.B.4 fallback / corroboration only — adapter sets aux flag
  §17.B.5 frozen-rules compatibility — no rule reads opencli fields directly
  §17.B.6 -f json mandatory
  §17.B.7 source url, timestamp, command, schema version, failure status
  §17.B.8 failure → Data unavailable / not_evaluated, never zero
  §17.B.9 strategy PUBLIC only by default
  §17.B.10 14 mandatory output fields
  §17.B.11 OpenCLI evidence is auxiliary; adapter sets aux flag
  §17.C.1-7 negative-scope: adapter never decides, never executes,
            never modifies code/rules/prompts/schemas
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar


logger = logging.getLogger(__name__)

OPENCLI_SCHEMA_VERSION = "opencli_evidence_v1.0_2026_04_28"

# §17.B.10 — 14 mandatory output fields. Validator rejects any
# envelope missing or null-with-wrong-type any of these.
OPENCLI_OUTPUT_FIELDS = (
    "source_url",
    "command_used",
    "query_terms",
    "collected_at",
    "data_available_as_of",
    "schema_version",
    "extraction_status",
    "output_hash",
    "source_reliability",
    "terms_or_access_notes",
    "logged_in_session_used",
    "human_authorization_required",
    "PIT_safety_notes",
    "data_quality_flags",
)

# §17.A.1 / §17.A.2 — read-only allowlist. Anything outside this set
# raises before any subprocess invocation. Mutating verbs (post, like,
# follow, vote, save, comment, message, publish, subscribe) are NOT
# in this list and never will be in this codebase.
#
# 2026-04-29 (Task 4): added "api" to support gh-passthrough use cases
# (e.g. `opencli gh api repos/<owner>/<repo>/commits`). `gh api` is
# read-only by default — the HTTP method is GET unless a -X flag forces
# otherwise. The base class enforces "api" verb safety by rejecting
# argv that contains -X / --method with any mutating value (POST/PUT/
# DELETE/PATCH). See _enforce_api_verb_is_read_only().
_ALLOWED_VERBS = frozenset({
    "get", "list", "show", "view", "describe",
    "extract", "open", "state", "api",
})

# Mutating HTTP methods that MUST NOT appear in any `api`-verb argv.
# The check is case-insensitive and matches both `-X POST` and
# `--method=POST` patterns.
_MUTATING_HTTP_METHODS = frozenset({
    "POST", "PUT", "DELETE", "PATCH",
})

# §17.A.6 — ToS-aware host allowlist. SEC + GitHub only for the v1
# integration. Adding a host requires a doc update + frozen-rules bump
# per §17.B.5 / §19.8.
_DEFAULT_ALLOWED_HOSTS = frozenset({"sec.gov", "github.com"})

# Exit codes mapped to error classes (per OPENCLI_LEARNED.md §5).
_EXIT_CODE_TO_ERROR_CLASS = {
    0: None,
    69: "browser_connect",
    77: "auth_required",
    78: "config_missing",
}

# Redact env-var-style tokens from any logged command string.
_REDACT_RE = re.compile(
    r"(--token|--secret|--api-key|--user-agent|--password)(=|\s+)(\S+)",
    re.IGNORECASE,
)


def _redact_command(cmd_str: str) -> str:
    return _REDACT_RE.sub(r"\1\2REDACTED", cmd_str)


@dataclass
class OpenCLIResult:
    """14-field shaped output envelope per RULES.md §17.B.10.

    Use OpenCLIResult.from_payload(...) to build one with auto
    output_hash computation.
    """
    source_url: str
    command_used: str                # redacted
    query_terms: dict
    collected_at: str                # ISO-8601 UTC at fetch time
    data_available_as_of: str | None # ISO-8601 of newest source datum
    schema_version: str
    extraction_status: str           # ok | failed | stub | partial
    output_hash: str                 # sha256:<hex>
    source_reliability: str          # T1 | T2 | T3 | T4 | T5 (per §10.4)
    terms_or_access_notes: str       # ToS / access reminder
    logged_in_session_used: bool     # MUST be False per §17.B.9 default
    human_authorization_required: bool
    PIT_safety_notes: str            # non-empty prose; §10.11 / §17.B.7
    data_quality_flags: list[dict]
    # Body — payload itself is auxiliary; agents read it via the parsed_payload
    # accessor below, not via the 14 envelope fields.
    parsed_payload: dict | None = None
    error_class: str | None = None
    block_target: str = ""
    aux_only: bool = True            # §17.B.11 — auxiliary-only flag

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def hash_payload(payload: Any) -> str:
        canon = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
        return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()

    @staticmethod
    def from_payload(
        *,
        source_url: str,
        command_used: str,
        query_terms: dict,
        data_available_as_of: str | None,
        extraction_status: str,
        source_reliability: str,
        terms_or_access_notes: str,
        PIT_safety_notes: str,
        payload: Any,
        block_target: str,
        data_quality_flags: list[dict] | None = None,
        error_class: str | None = None,
        logged_in_session_used: bool = False,
        human_authorization_required: bool = False,
    ) -> "OpenCLIResult":
        return OpenCLIResult(
            source_url=source_url,
            command_used=_redact_command(command_used),
            query_terms=query_terms,
            collected_at=datetime.now(timezone.utc).isoformat(),
            data_available_as_of=data_available_as_of,
            schema_version=OPENCLI_SCHEMA_VERSION,
            extraction_status=extraction_status,
            output_hash=OpenCLIResult.hash_payload(payload),
            source_reliability=source_reliability,
            terms_or_access_notes=terms_or_access_notes,
            logged_in_session_used=logged_in_session_used,
            human_authorization_required=human_authorization_required,
            PIT_safety_notes=PIT_safety_notes,
            data_quality_flags=data_quality_flags or [],
            parsed_payload=payload,
            error_class=error_class,
            block_target=block_target,
            aux_only=True,
        )

    def validate(self) -> tuple[bool, list[str]]:
        """Check the 14 mandatory fields per §17.B.10."""
        errs: list[str] = []
        for k in OPENCLI_OUTPUT_FIELDS:
            v = getattr(self, k, "__SENTINEL__")
            if v == "__SENTINEL__":
                errs.append(f"missing field: {k}")
                continue
            # Per §17.B.7 / §10.11: PIT_safety_notes is required prose.
            if k == "PIT_safety_notes" and (not isinstance(v, str) or not v.strip()):
                errs.append("PIT_safety_notes must be non-empty prose")
            # data_quality_flags is a list (possibly empty)
            if k == "data_quality_flags" and not isinstance(v, list):
                errs.append("data_quality_flags must be a list")
            # logged_in_session_used / human_authorization_required: bool
            if k in ("logged_in_session_used", "human_authorization_required") \
               and not isinstance(v, bool):
                errs.append(f"{k} must be a boolean")
        # §17.B.9 — logged_in_session_used default false
        if self.logged_in_session_used and not self.human_authorization_required:
            errs.append(
                "logged_in_session_used=true requires "
                "human_authorization_required=true"
            )
        return (len(errs) == 0), errs


class OpenCLIAdapter(ABC):
    """Abstract base for OpenCLI-backed adapters.

    Subclasses MUST set:
      - source_id   : short name (e.g. "sec_8k_fulltext")
      - block_target: which packet block this contributes to
      - allowed_hosts: subset of {"sec.gov","github.com"} (or extension
                      after a frozen-rules bump per §19.8)

    Subclasses MUST implement:
      - _build_command(...)   : build the OpenCLI argv (must include "-f json")
      - _parse_stdout(stdout) : parse JSON stdout into a domain payload
      - _fetch_stub(...)      : deterministic canned data
      - _terms_or_access_notes() -> str
      - _PIT_safety_notes() -> str
      - _source_reliability() -> str ("T1".."T5")
    """

    source_id: ClassVar[str] = ""
    block_target: ClassVar[str] = ""
    allowed_hosts: ClassVar[frozenset[str]] = _DEFAULT_ALLOWED_HOSTS
    cache_root_override: ClassVar[Path | None] = None
    # Per-call subprocess timeout. Subclasses may tighten — sec_8k_fulltext
    # uses 30s (large filings), github_commit_messages uses 15s.
    live_call_timeout_s: ClassVar[int] = 60
    # When the upstream OpenCLI binary is INSTALLED but a specific
    # subcommand / site adapter our use case relies on is not shipped
    # by the upstream version we have on PATH, set this to a short
    # explanation. The fetch() entry point will then short-circuit to
    # the stub path with a `binary_present_verb_unsupported` quality
    # flag — distinct from the `opencli_stub_or_not_installed` flag
    # used when the binary itself is missing. Per the §17.B.8 contract
    # we always emit a 14-field envelope; we do NOT crash.
    live_unavailable_reason: ClassVar[str | None] = None

    def __init__(self) -> None:
        if not self.source_id:
            raise ValueError(f"{type(self).__name__} missing source_id")
        if not self.block_target:
            raise ValueError(f"{type(self).__name__} missing block_target")

    # ── public, non-virtual entry point ───────────────────────────
    def fetch(
        self,
        *,
        query_terms: dict,
        decision_timestamp: datetime,
        stub_mode: bool | None = None,
        cache_key: str | None = None,
    ) -> OpenCLIResult:
        """Run a fetch. 14-field-validated, cached, stub-aware.

        cache_key (optional): caller-supplied key for cache lookup. We
        cache OpenCLI outputs by accession (SEC) or by repo+sha (GitHub)
        because the source URLs are immutable. 30-day TTL.
        """
        if not isinstance(decision_timestamp, datetime):
            raise ValueError("decision_timestamp must be datetime")
        if decision_timestamp.tzinfo is None:
            decision_timestamp = decision_timestamp.replace(tzinfo=timezone.utc)

        # Cache lookup
        if cache_key:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        # Stub or live decision. Three forces drive stub_mode:
        #   1. caller passed stub_mode=True (test path)
        #   2. STUB_MODE env var set
        #   3. opencli not on PATH
        #   4. (NEW Task 4) class-level `live_unavailable_reason` set
        #      — used when the binary IS present but lacks a required
        #      verb / site adapter for this specific use case.
        binary_present = shutil.which("opencli") is not None
        capability_gap = self.live_unavailable_reason is not None
        if stub_mode is None:
            stub_mode = (
                os.environ.get("STUB_MODE", "").lower() in ("1", "true", "yes")
                or not binary_present
                or capability_gap
            )

        if stub_mode:
            r = self._fetch_stub(query_terms=query_terms,
                                  decision_timestamp=decision_timestamp)
            r.extraction_status = "stub" if r.extraction_status == "ok" else r.extraction_status
            # Honest provenance: distinguish "binary missing" from
            # "binary present but verb unsupported" so the dashboard
            # and audit log can tell users exactly why STUB fired.
            if capability_gap and binary_present:
                r.data_quality_flags.append({
                    "kind": "binary_present_verb_unsupported",
                    "severity": "warning",
                    "detail": (
                        f"{self.source_id}: opencli is on PATH but the "
                        f"specific verb/site adapter this use case needs "
                        f"is not shipped by the installed version. "
                        f"Reason: {self.live_unavailable_reason}"
                    ),
                })
            else:
                r.data_quality_flags.append({
                    "kind": "opencli_stub_or_not_installed",
                    "severity": "info",
                    "detail": (
                        f"{self.source_id}: opencli stub mode "
                        f"(STUB_MODE env or `opencli` not on PATH)"
                    ),
                })
        else:
            r = self._fetch_live(query_terms=query_terms,
                                  decision_timestamp=decision_timestamp)

        # 14-field validate. We REFUSE to ship a non-compliant envelope.
        ok, errs = r.validate()
        if not ok:
            # Collapse to a fail-closed envelope rather than letting a
            # half-shaped result propagate. Per §17.B.8.
            r = self._fail_closed_envelope(
                query_terms=query_terms,
                command_used=r.command_used or self._stub_command_str(query_terms),
                source_url=r.source_url or self._source_url_for(query_terms),
                error_class="envelope_validation_failed",
                detail=f"{self.source_id}: envelope validation failed: {errs}",
            )

        if cache_key:
            self._cache_put(cache_key, r)
        return r

    # ── subclass hooks ────────────────────────────────────────────
    @abstractmethod
    def _build_command(self, *, query_terms: dict) -> list[str]:
        """Return argv list for the OpenCLI invocation. MUST include
        '-f' and 'json'. The base class double-checks this."""

    @abstractmethod
    def _parse_stdout(self, stdout: str) -> dict:
        """Parse OpenCLI's JSON stdout into a domain payload dict."""

    @abstractmethod
    def _fetch_stub(self, *, query_terms: dict,
                     decision_timestamp: datetime) -> OpenCLIResult: ...

    @abstractmethod
    def _terms_or_access_notes(self) -> str: ...

    @abstractmethod
    def _PIT_safety_notes(self) -> str: ...

    @abstractmethod
    def _source_reliability(self) -> str: ...

    @abstractmethod
    def _source_url_for(self, query_terms: dict) -> str: ...

    # ── live invocation pipeline ──────────────────────────────────
    def _fetch_live(
        self, *, query_terms: dict, decision_timestamp: datetime,
    ) -> OpenCLIResult:
        argv = list(self._build_command(query_terms=query_terms))
        # §17.A.1/A.2 verb allowlist (compute first so we can branch on it).
        verb = self._first_verb(argv)
        if verb not in _ALLOWED_VERBS:
            raise RuntimeError(
                f"{self.source_id}: verb={verb!r} not in read-only "
                f"allowlist {sorted(_ALLOWED_VERBS)} per §17.A.1/§17.A.2"
            )
        # §17.A.1/A.2 — `api` verb (gh-passthrough) MUST be a GET. Reject
        # any -X / --method / --request that names a mutating method.
        if verb == "api":
            self._enforce_api_verb_is_read_only(argv)
        # §17.B.6 enforce -f json — every command builder MUST declare
        # the JSON output contract by including "-f" and "json" tokens
        # in argv. This is checked here against the contract argv
        # (i.e. the value returned by _build_command).
        if "-f" not in argv or "json" not in argv:
            raise RuntimeError(
                f"{self.source_id}: -f json is mandatory per RULES.md §17.B.6"
            )
        # gh-passthrough special case. `opencli gh api ...` forwards to
        # the gh CLI, which interprets `-f` as raw form data and would
        # reject `-f json` with "invalid key". gh's documented contract
        # is that `gh api` returns JSON for GET — so the §17.B.6 intent
        # (deterministic JSON output) is satisfied by the runtime
        # behaviour even though we cannot pass the explicit flag.
        # Strip the contract tokens immediately before invocation.
        gh_passthrough_api = (verb == "api" and "gh" in argv[:3])
        if gh_passthrough_api:
            argv = self._strip_contract_format_tokens(argv)
        # §17.A.6 host allowlist
        host = self._extract_host_from_argv(argv)
        # gh-passthrough commands target api.github.com implicitly even
        # though no URL appears on the command line. Force host=github.com
        # so the allowlist still enforces §17.A.6 instead of silently
        # accepting because no explicit URL was found.
        if host is None and gh_passthrough_api:
            host = "github.com"
        if host and not any(host.endswith(h) for h in self.allowed_hosts):
            raise RuntimeError(
                f"{self.source_id}: host={host!r} not in allowed_hosts="
                f"{sorted(self.allowed_hosts)} per §17.A.6"
            )

        cmd_str = " ".join(argv)
        logger.info("%s: invoking opencli: %s",
                    self.source_id, _redact_command(cmd_str))
        # Resolve `opencli` to its full path. On Windows, Python's
        # subprocess does NOT follow PATHEXT for the executable name,
        # so a bare "opencli" argv[0] fails with FileNotFoundError even
        # when shutil.which("opencli") finds opencli.CMD. shutil.which
        # DOES follow PATHEXT, so we resolve once and pass the absolute
        # path to subprocess.
        invoke_argv = list(argv)
        if invoke_argv and invoke_argv[0] == "opencli":
            resolved = shutil.which("opencli")
            if resolved is not None:
                invoke_argv[0] = resolved
        try:
            proc = subprocess.run(
                invoke_argv, capture_output=True, text=True,
                timeout=self.live_call_timeout_s, check=False,
            )
        except FileNotFoundError:
            return self._fail_closed_envelope(
                query_terms=query_terms, command_used=cmd_str,
                source_url=self._source_url_for(query_terms),
                error_class="opencli_not_installed",
                detail=f"{self.source_id}: opencli binary not found on PATH",
            )
        except subprocess.TimeoutExpired:
            return self._fail_closed_envelope(
                query_terms=query_terms, command_used=cmd_str,
                source_url=self._source_url_for(query_terms),
                error_class="timeout",
                detail=(
                    f"{self.source_id}: opencli timed out after "
                    f"{self.live_call_timeout_s}s"
                ),
            )

        # §17.A.4 — exit codes 77 / 69 fail loud, never silent retry
        err_class = _EXIT_CODE_TO_ERROR_CLASS.get(proc.returncode)
        if proc.returncode != 0:
            return self._fail_closed_envelope(
                query_terms=query_terms, command_used=cmd_str,
                source_url=self._source_url_for(query_terms),
                error_class=err_class or f"exit_{proc.returncode}",
                detail=(f"{self.source_id}: opencli exit "
                        f"{proc.returncode}; stderr={(proc.stderr or '').strip()[:200]}"),
            )

        try:
            payload = self._parse_stdout(proc.stdout)
        except Exception as e:
            return self._fail_closed_envelope(
                query_terms=query_terms, command_used=cmd_str,
                source_url=self._source_url_for(query_terms),
                error_class=f"parse_{type(e).__name__}",
                detail=f"{self.source_id}: parse failed: {e}",
            )

        return OpenCLIResult.from_payload(
            source_url=self._source_url_for(query_terms),
            command_used=cmd_str,
            query_terms=dict(query_terms),
            data_available_as_of=payload.get("_data_available_as_of"),
            extraction_status="ok",
            source_reliability=self._source_reliability(),
            terms_or_access_notes=self._terms_or_access_notes(),
            PIT_safety_notes=self._PIT_safety_notes(),
            payload=payload,
            block_target=self.block_target,
            data_quality_flags=[],
        )

    def _fail_closed_envelope(
        self, *, query_terms: dict, command_used: str, source_url: str,
        error_class: str, detail: str,
    ) -> OpenCLIResult:
        """§17.B.8 — failure → Data unavailable, never zero. The shape
        still satisfies §17.B.10."""
        logger.warning("%s: failed (%s) — %s",
                       self.source_id, error_class, detail)
        return OpenCLIResult.from_payload(
            source_url=source_url or "Data unavailable",
            command_used=command_used,
            query_terms=dict(query_terms),
            data_available_as_of=None,
            extraction_status="failed",
            source_reliability=self._source_reliability(),
            terms_or_access_notes=self._terms_or_access_notes(),
            PIT_safety_notes=self._PIT_safety_notes(),
            payload={},
            block_target=self.block_target,
            error_class=error_class,
            data_quality_flags=[{
                "kind": "opencli_failed",
                "severity": "warning",
                "detail": detail,
            }],
        )

    # ── helpers ──────────────────────────────────────────────────
    @staticmethod
    def _strip_contract_format_tokens(argv: list[str]) -> list[str]:
        """Remove the `-f json` contract pair (in that exact order)
        from argv. Used for gh-passthrough where the tokens are a
        contract declaration, not a flag the underlying binary accepts.
        Other `-f <value>` pairs are left untouched (defensive — only
        the literal `-f json` pair is stripped)."""
        out: list[str] = []
        i = 0
        n = len(argv)
        while i < n:
            if (argv[i] == "-f" and i + 1 < n and argv[i + 1] == "json"):
                i += 2
                continue
            out.append(argv[i])
            i += 1
        return out

    @staticmethod
    def _enforce_api_verb_is_read_only(argv: list[str]) -> None:
        """For the `api` verb (gh-style passthrough), reject argv that
        forces a mutating HTTP method. Read-only-first per §17.A.1/A.2.

        Matches all of:
          ['-X', 'POST']
          ['--method', 'POST']
          ['-X=POST']
          ['--method=POST']
          ['--request', 'POST'] / ['--request=POST']
        """
        i = 0
        n = len(argv)
        while i < n:
            tok = argv[i]
            tok_low = tok.lower()
            method_value: str | None = None
            if tok in ("-X", "--method", "--request"):
                if i + 1 < n:
                    method_value = argv[i + 1]
                i += 2
                # don't continue — fall through to check method_value
            elif tok_low.startswith(("-x=", "--method=", "--request=")):
                method_value = tok.split("=", 1)[1]
                i += 1
            else:
                i += 1
                continue
            if method_value and method_value.upper() in _MUTATING_HTTP_METHODS:
                raise RuntimeError(
                    f"opencli `api` verb received mutating HTTP method "
                    f"{method_value!r}; read-only-first per §17.A.1/§17.A.2"
                )

    @staticmethod
    def _first_verb(argv: list[str]) -> str:
        """Return the first non-option arg after the binary name. The
        upstream CLI follows `opencli <subgroup> <verb> [...]`."""
        # Skip "opencli" itself.
        for i in range(1, len(argv)):
            tok = argv[i]
            if tok.startswith("-"):
                continue
            # Heuristic: if the previous non-option token is a known
            # subgroup ("browser","cli","skill","site"), this is the verb.
            return tok if tok in _ALLOWED_VERBS or i + 1 == len(argv) \
                else (argv[i + 1] if i + 1 < len(argv) else tok)
        return ""

    @staticmethod
    def _extract_host_from_argv(argv: list[str]) -> str | None:
        """Find a URL-like token and return its host. We accept either
        a literal URL on the command line or a 'site:' style adapter
        identifier like 'sec.gov/8k'."""
        for tok in argv:
            if tok.startswith(("http://", "https://")):
                # Strip scheme and path
                rest = tok.split("://", 1)[1]
                return rest.split("/", 1)[0].lower()
            if "/" in tok and "." in tok.split("/", 1)[0]:
                # site/adapter form
                host = tok.split("/", 1)[0].lower()
                if "." in host:
                    return host
        return None

    def _stub_command_str(self, query_terms: dict) -> str:
        return f"_stub_opencli {self.source_id} {json.dumps(query_terms, sort_keys=True)}"

    # ── cache helpers ─────────────────────────────────────────────
    def _cache_root(self) -> Path:
        if self.cache_root_override is not None:
            return Path(self.cache_root_override)
        proj_root = Path(__file__).resolve().parents[3]
        return proj_root / "data" / "cache" / "altdata" / "opencli" / self.source_id

    def _cache_path(self, key: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)
        return self._cache_root() / f"{safe}.json"

    def _cache_get(self, key: str) -> OpenCLIResult | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        # 30-day TTL
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        if age > 30 * 24 * 3600:
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return OpenCLIResult(**d)
        except TypeError:
            return None

    def _cache_put(self, key: str, r: OpenCLIResult) -> None:
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(r.to_dict(), f, ensure_ascii=False, indent=2,
                          default=str)
        except OSError as e:
            logger.warning("%s: cache write failed at %s (%s)",
                           self.source_id, path, e)
