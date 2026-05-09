"""
Anti-hindsight enforcement (R1-R6) for evidence_packet_v1.

This module is the single source of truth for which fields, value patterns,
and audit checks are required to keep a packet free of hindsight bias —
i.e. of facts and labels that exist today but did NOT exist as of
`decision_timestamp`. See `docs/HINDSIGHT_POLICY.md` for prose definitions,
H1-H6 problem categories, and R1-R6 rule statements.

Two callers:
  - block builders: read FORBIDDEN_IN_REPLAY and CURRENT_STATE_PREFIXES
    when deciding what to emit.
  - generator.py: calls run_hindsight_audit(packet, ...) at the end of
    packet build to populate envelope.hindsight_safe / hindsight_violations
    and to drive the post-build PIT removal pass.

Design choice: live mode is permissive — current-state fields are tagged
with `uses_current_state: true` rather than removed. Replay mode (when
implemented) is strict — the same fields are removed and counted in
records_removed_by_pit_filter. The forbidden list is the same in both
modes; only the action differs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

# ────────────────────────────────────────────────────────────────────
# R3 — Forbidden hindsight fields (canonical list)
#
# Any of these field PATHS, if present in a historical-replay packet,
# constitutes a hindsight leak. In live mode they may legitimately
# appear because "now" is the only available frame; in that case the
# block emitting them MUST set `uses_current_state: true` so a future
# replay engine can refuse them.
# ────────────────────────────────────────────────────────────────────

# Exact dotted-path matches (case-sensitive). The path is interpreted
# top-down through dicts; list elements are recursed into without an
# index.
FORBIDDEN_FIELD_PATHS_REPLAY: tuple[tuple[str, ...], ...] = (
    # Profile-level "current truth" that didn't exist historically
    ("profile", "isActivelyTrading"),
    ("profile", "is_actively_trading"),
    ("profile", "delistedDate"),
    ("profile", "delisted_date"),

    # Live quote fields are NEVER PIT — they're a wall-clock snapshot
    # The PIT anchor is the EOD close. Replay must use historical EOD
    # rather than these.
    ("price_snapshot", "live_quote"),
    ("price_snapshot", "last_quote_timestamp"),
    ("price_snapshot", "last_price"),

    # FMP DCF is a model-derived display field that updates over time;
    # using it for replay leaks today's DCF input assumptions.
    ("valuation_snapshot", "DCF_value_display_only"),
    ("valuation_snapshot", "DCF_gap_display_only"),
    ("valuation_snapshot", "DCF_current_price_used"),

    # Pass 8 Step B2-mini-B (2026-05-05) — uses_current_state is the
    # live-mode tagging mechanism (R3/R5); under replay strict mode it
    # has no purpose and downstream agents have been observed treating
    # the boolean itself as a hindsight marker (B2-mini-A ACON forensic).
    # Strip both the flag and its rationale field so the agent never
    # sees them under historical_replay.
    ("price_snapshot", "uses_current_state"),
    ("price_snapshot", "uses_current_state_reason"),
    ("valuation_snapshot", "uses_current_state"),
    ("valuation_snapshot", "uses_current_state_reason"),
)

# Substring patterns: any leaf KEY containing one of these patterns is
# treated as a current-state field. `current_*` is the explicit one
# mandated by R3; we add a few common aliases for safety.
CURRENT_STATE_KEY_PREFIXES: tuple[str, ...] = (
    "current_",
)

CURRENT_STATE_KEY_INFIXES: tuple[str, ...] = (
    "_current_",  # e.g. some_current_value
)

# Exclusion: field names that LOOK like current-state by prefix rule but
# are actually policy / PIT-status META-FLAGS (booleans describing the
# block's own PIT status, not holding current-state data values). These
# do not constitute hindsight leaks. Update by name, never by regex —
# this list is auditable on its own.
CURRENT_STATE_META_FLAG_EXCLUSIONS: frozenset[str] = frozenset({
    "current_snapshot_not_PIT_safe",
    "current_state_not_PIT_safe",
    "is_current_snapshot",
    # Pass 8 Step B2-mini-B (2026-05-05): TTM financial ratios renamed with
    # the `_pit` suffix carry an audit-friendly signal that the value has
    # been PIT-vetted. The leading `current_` is part of the well-known
    # "current ratio" accounting metric (current assets / current
    # liabilities, TTM trailing) and is NOT a hindsight marker. Excluded
    # so the prefix-based detector does not produce false positives.
    "current_ratio_ttm_pit",
})


# ────────────────────────────────────────────────────────────────────
# R4 — Delisting status comes from the price-tail detector ONLY.
# (Wired in blocks/price.py at consumer time. This file only documents
# the rule; the wiring is a code-level invariant in the price block.)
# ────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────
# Audit walker
# ────────────────────────────────────────────────────────────────────

def _walk_paths(node: Any, prefix: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    """Yield (path_tuple, leaf_value) for every leaf in a nested dict/list.
    Lists are walked but list indices are not part of the path — the same
    forbidden path matches every list element."""
    if isinstance(node, dict):
        for k, v in node.items():
            sub = prefix + (k,)
            if isinstance(v, (dict, list)):
                yield from _walk_paths(v, sub)
            else:
                yield sub, v
    elif isinstance(node, list):
        for item in node:
            yield from _walk_paths(item, prefix)


def _path_matches(actual: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    """Pattern matches actual if pattern appears as a contiguous suffix of actual.
    e.g. pattern=("profile","isActivelyTrading") matches both
    ("profile","isActivelyTrading") and ("foo","profile","isActivelyTrading")."""
    if len(pattern) > len(actual):
        return False
    return tuple(actual[-len(pattern):]) == pattern


def _key_is_current_state(key: str) -> bool:
    if key in CURRENT_STATE_META_FLAG_EXCLUSIONS:
        return False
    if any(key.startswith(p) for p in CURRENT_STATE_KEY_PREFIXES):
        return True
    if any(p in key for p in CURRENT_STATE_KEY_INFIXES):
        return True
    return False


# ────────────────────────────────────────────────────────────────────
# Public API — used by generator.py
# ────────────────────────────────────────────────────────────────────

def detect_hindsight_violations(
    packet: dict,
    *,
    decision_mode: str,
    is_replay: bool,
) -> list[dict]:
    """Return a list of structured violations.

    Live mode policy:
      - forbidden-field hits → recorded as 'tagged_current_state' (NOT a
        violation) IF the enclosing block carries uses_current_state: true.
        Otherwise → violation.
      - current_* keys → recorded as 'tagged_current_state' IF tagged;
        otherwise → violation.

    Replay mode policy (gated by is_replay flag):
      - any forbidden-field hit OR current_* key → violation, regardless
        of the uses_current_state flag. The orchestrator will then run
        the PIT-removal pass.
    """
    violations: list[dict] = []
    if not isinstance(packet, dict):
        return violations

    for path, value in _walk_paths(packet):
        if value is None:
            continue
        leaf_key = path[-1] if path else ""
        # Skip self-referential / known meta paths
        if leaf_key in ("hindsight_violations", "lookahead_violations",
                         "FORBIDDEN_FIELD_PATHS_REPLAY",
                         "CURRENT_STATE_KEY_PREFIXES"):
            continue

        # Is this a forbidden-path hit?
        forbidden_hit = any(_path_matches(path, p) for p in FORBIDDEN_FIELD_PATHS_REPLAY)
        # Is this a current-state-key hit?
        current_state_hit = _key_is_current_state(leaf_key)
        if not (forbidden_hit or current_state_hit):
            continue

        # Determine if the enclosing block has uses_current_state: true
        block_path = path[:1] if path else ()
        block = packet.get(block_path[0]) if block_path else None
        block_tagged = isinstance(block, dict) and block.get("uses_current_state") is True

        if is_replay:
            # Strict: any hit is a violation regardless of tagging.
            violations.append({
                "path": ".".join(path),
                "kind": ("forbidden_field" if forbidden_hit else "current_state_key"),
                "policy": "replay_strict",
                "block_uses_current_state_tag": block_tagged,
            })
            continue

        # Live mode: tagged blocks pass; untagged ones violate.
        if not block_tagged:
            violations.append({
                "path": ".".join(path),
                "kind": ("forbidden_field" if forbidden_hit else "current_state_key"),
                "policy": "live_untagged_current_state",
                "block_uses_current_state_tag": False,
                "remediation": (
                    "either remove the field, OR set the enclosing block's "
                    "uses_current_state: true with a one-line rationale"
                ),
            })
    return violations


def scrub_forbidden_replay_paths(
    packet: dict,
) -> tuple[dict, list[str]]:
    """Pass 8 Step B1.7 (2026-05-04) — physically remove forbidden-replay
    field paths from the packet.

    Default-mode packet build leaves these fields in place (tagged
    `uses_current_state: True`) so the live-mode pipeline can use them.
    Replay-mode strict callers want them GONE so downstream agents
    cannot accidentally read them. This function walks the packet and
    deletes any leaf whose path ends with a suffix in
    FORBIDDEN_FIELD_PATHS_REPLAY.

    Returns (mutated_packet, list_of_removed_paths). The mutation is
    in-place; the return value is the same dict for chaining. Removed
    paths are dotted strings ("price_snapshot.live_quote", etc.).

    Called only when the generator's strict_pit_mode=True. Default
    behavior preserves the regression matrix's frozen byte-identical
    hash (the §22 baseline at sha256:a1538e90...).
    """
    removed_paths: list[str] = []

    def _scrub(node, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            keys_to_delete: list[str] = []
            for k, v in list(node.items()):
                child_path = path + (k,)
                # If THIS key matches any forbidden suffix → delete here.
                if any(_path_matches(child_path, p)
                       for p in FORBIDDEN_FIELD_PATHS_REPLAY):
                    keys_to_delete.append(k)
                    removed_paths.append(".".join(child_path))
                    continue
                # Otherwise recurse.
                _scrub(v, child_path)
            for k in keys_to_delete:
                del node[k]
        elif isinstance(node, list):
            for item in node:
                _scrub(item, path)

    _scrub(packet, ())
    return packet, removed_paths


def remove_post_cutoff_records(
    packet: dict,
    *,
    cutoff_dt_utc: datetime,
) -> tuple[dict, dict[str, int]]:
    """R2 hard filter — physically remove any list-row whose `as_of` /
    `published_at_utc` / `accepted_date` exceeds the cutoff.

    Returns (mutated_packet, removed_count_per_block).

    This is *separate* from the lookahead block-level check, which only
    flags the BLOCK's top-level as_of. R2 is the per-row variant.
    """
    from .generator import _parse_as_of_to_utc  # local import to avoid cycle

    removed: dict[str, int] = {}

    def _row_is_post_cutoff(row: dict) -> bool:
        for k in ("as_of", "published_at_utc", "published_at",
                   "accepted_date", "filing_date", "available_as_of"):
            v = row.get(k)
            if isinstance(v, str):
                dt = _parse_as_of_to_utc(v)
                if dt is not None and dt > cutoff_dt_utc:
                    return True
        return False

    for block_key, block in packet.items():
        if not isinstance(block, dict):
            continue
        for field_key, field_val in list(block.items()):
            if not isinstance(field_val, list):
                continue
            kept = []
            n_removed = 0
            for row in field_val:
                if isinstance(row, dict) and _row_is_post_cutoff(row):
                    n_removed += 1
                    continue
                kept.append(row)
            if n_removed:
                block[field_key] = kept
                removed[f"{block_key}.{field_key}"] = removed.get(
                    f"{block_key}.{field_key}", 0
                ) + n_removed
    return packet, removed


def run_hindsight_audit(
    packet: dict,
    *,
    decision_mode: str,
    cutoff_dt_utc: datetime,
) -> dict:
    """Apply R1-R6 to the packet. Returns an audit report:

        {
          "hindsight_safe":           bool,
          "hindsight_violations":     [...],     # R3 + current-state hits
          "records_removed_by_pit":   {...},     # R2 per-block counters
          "hindsight_filter_actions": [...],     # R6 audit trail
        }

    The orchestrator merges these into envelope/source_list. We deliberately
    do NOT modify the packet in place beyond R2 row removals — block-level
    flags are the orchestrator's responsibility.
    """
    is_replay = (decision_mode == "historical_replay")

    # R2 — strip post-cutoff rows from any list within any block. In live
    # mode the cutoff equals "now", so this should be a no-op for normal
    # tickers. We still run it for symmetry and to populate the counters.
    packet, removed = remove_post_cutoff_records(packet, cutoff_dt_utc=cutoff_dt_utc)
    filter_actions = [
        {"action": "pit_row_removed", "block_field": k, "count": v,
         "cutoff_utc": cutoff_dt_utc.isoformat()}
        for k, v in removed.items()
    ]

    # R3 + current-state audit
    violations = detect_hindsight_violations(
        packet, decision_mode=decision_mode, is_replay=is_replay,
    )

    return {
        "hindsight_safe": (len(violations) == 0),
        "hindsight_violations": violations,
        "records_removed_by_pit": removed,
        "hindsight_filter_actions": filter_actions,
    }
