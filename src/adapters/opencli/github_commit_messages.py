"""
OpenCLI use case: GitHub commit messages for a mapped repo.

For tech-sector tickers flagged as AI-pivot / platform-pivot / similar
narrative-heavy catalysts, fetch the most-recent N commit messages
from the relevant repos. Complements the metadata-only github_public
adapter (which returns weekly aggregates) by providing actual commit
message text — a hard-to-fake narrative-verification signal.

LIVE invocation path (Task 4, 2026-04-29 PM):
  Upstream OpenCLI v1.7.8 does NOT ship a native `github.com/commits`
  site adapter. It does ship a `gh` passthrough that routes commands
  to the local `gh` CLI binary. We use:
      opencli gh api repos/<owner>/<repo>/commits?per_page=N
  This is read-only by default (HTTP GET); the base class enforces
  read-only-first by rejecting argv that forces -X POST/PUT/DELETE/PATCH
  per §17.A.1/A.2. The `-f json` mandate is satisfied implicitly:
  `gh api` always returns JSON for GET (documented contract).

Design decisions:
  - Cache key = owner|repo|branch|N — cheap to invalidate by changing N
  - 30-day TTL on cache
  - Per-call timeout: 15 s (light call; ~1-3 KB JSON per commit)
  - Stub mode produces deterministic fake commits per repo
  - Only operates on github.com hosts; other hosts are refused at the
    base-class allowlist (§17.A.6)

Block target: alternative_data_features (tech_activity.narrative subsection).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from .base import OpenCLIAdapter, OpenCLIResult


class GitHubCommitMessagesAdapter(OpenCLIAdapter):
    source_id = "github_commit_messages"
    block_target = "alternative_data_features"
    allowed_hosts = frozenset({"github.com"})
    # Light call (GitHub API commits endpoint is paginated, ~1-3 KB per
    # commit). 15 s is generous; spec target.
    live_call_timeout_s = 15

    def _build_command(self, *, query_terms: dict) -> list[str]:
        owner = query_terms["owner"]
        repo = query_terms["repo"]
        n = int(query_terms.get("n", 20))
        # We declare the `-f json` contract token in argv per §17.B.6
        # (the compliance test asserts every command builder declares
        # JSON output). `gh api` itself rejects `-f json` because gh
        # uses `-f` for raw form data, so the base class's _fetch_live
        # strips the contract tokens just before subprocess.run when
        # the verb is `api` and the subgroup is `gh`. The contract
        # declaration is what §17.B.6 cares about; gh's documented
        # behaviour (returning JSON for GET) is what makes it true at
        # runtime.
        return [
            "opencli", "gh", "api",
            f"repos/{owner}/{repo}/commits?per_page={n}",
            "-f", "json",
        ]

    def _parse_stdout(self, stdout: str) -> dict:
        """Parse the GitHub API commits-list shape (passed through by
        `opencli gh api`). Each element looks like:

            {
              "sha": "...",
              "commit": {
                "author": {"name": "...", "email": "...", "date": "..."},
                "committer": {...},
                "message": "..."
              },
              "author": {"login": "...", ...} | null,
              "committer": {"login": "...", ...} | null,
              ...
            }
        """
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise ValueError(f"opencli stdout not JSON: {e}")
        if not isinstance(payload, list):
            # Some gh error envelopes are dicts, e.g. {"message": "Not Found"}.
            if isinstance(payload, dict) and "message" in payload:
                raise ValueError(
                    f"github api error: {payload.get('message')}"
                )
            raise ValueError("opencli stdout did not deserialise to list")
        commits: list[dict] = []
        latest_dt: str | None = None
        for c in payload:
            if not isinstance(c, dict):
                continue
            sha = c.get("sha")
            commit_obj = c.get("commit") or {}
            author_obj = commit_obj.get("author") or {}
            msg = (commit_obj.get("message") or "").strip()
            ts = author_obj.get("date") or (
                (commit_obj.get("committer") or {}).get("date")
            )
            login_holder = c.get("author") or c.get("committer") or {}
            author_login = (
                login_holder.get("login") if isinstance(login_holder, dict)
                else None
            )
            commits.append({
                "sha": sha,
                "message_first_line": msg.splitlines()[0] if msg else "",
                "message_full": msg,
                "committed_at": ts,
                "author_login": author_login or author_obj.get("name"),
            })
            if isinstance(ts, str) and (latest_dt is None or ts > latest_dt):
                latest_dt = ts
        return {
            "commits": commits,
            "n_returned": len(commits),
            "_data_available_as_of": latest_dt,
        }

    def _fetch_stub(self, *, query_terms: dict,
                     decision_timestamp: datetime) -> OpenCLIResult:
        owner = query_terms.get("owner", "_stub_owner")
        repo = query_terms.get("repo", "_stub_repo")
        n = int(query_terms.get("n", 5))
        seed = int(hashlib.sha256(f"{owner}/{repo}".encode("utf-8")).hexdigest()[:8], 16)
        commits: list[dict] = []
        latest_dt = None
        for i in range(n):
            ts = (decision_timestamp - timedelta(days=i + 1)).replace(
                hour=(seed + i) % 24, minute=(seed + i * 3) % 60, second=0,
                microsecond=0,
            )
            ts_iso = ts.isoformat()
            if latest_dt is None or ts_iso > latest_dt:
                latest_dt = ts_iso
            commits.append({
                "sha": f"_stub_{seed:08x}_{i:02d}",
                "message_first_line": (
                    f"_STUB_ commit {i} on {owner}/{repo}"
                ),
                "message_full": (
                    f"_STUB_ commit {i} on {owner}/{repo}\n\n"
                    "Stub text emitted by github_commit_messages OpenCLI "
                    "adapter; not ground truth."
                ),
                "committed_at": ts_iso,
                "author_login": f"_stub_dev_{i}",
            })
        payload = {
            "commits": commits,
            "n_returned": len(commits),
        }
        return OpenCLIResult.from_payload(
            source_url=f"https://github.com/{owner}/{repo}/commits",
            command_used=self._stub_command_str(query_terms),
            query_terms=dict(query_terms),
            data_available_as_of=latest_dt,
            extraction_status="stub",
            source_reliability=self._source_reliability(),
            terms_or_access_notes=self._terms_or_access_notes(),
            PIT_safety_notes=self._PIT_safety_notes(),
            payload=payload,
            block_target=self.block_target,
            data_quality_flags=[{
                "kind": "stub_data",
                "severity": "info",
                "detail": (
                    "github_commit_messages: stub mode (deterministic, "
                    "not ground truth)"
                ),
            }],
        )

    def _terms_or_access_notes(self) -> str:
        return (
            "GitHub public-repo commit messages are publicly readable via "
            "api.github.com; OpenCLI may use a logged-in session for higher "
            "rate limits, but our adapter is configured PUBLIC-only per "
            "RULES.md §17.B.9. No ToS conflict for read-only public-repo access."
        )

    def _PIT_safety_notes(self) -> str:
        return (
            "Each commit's `committed_at` is the point-in-time anchor. The "
            "agent layer must drop any commit with committed_at > "
            "allowed_data_cutoff (already PIT-filtered when the row enters "
            "the evidence packet). The OpenCLI invocation itself is wall-clock; "
            "the underlying commits are immutable once authored."
        )

    def _source_reliability(self) -> str:
        return "T2"   # GitHub = industry primary, public

    def _source_url_for(self, query_terms: dict) -> str:
        owner = query_terms.get("owner", "_unknown")
        repo = query_terms.get("repo", "_unknown")
        return f"https://github.com/{owner}/{repo}/commits"
