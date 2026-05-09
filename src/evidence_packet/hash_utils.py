"""
Deterministic canonical hashing for evidence_packet_v1.

Two concerns this module owns:

1. `compute_evidence_packet_hash(packet)` — sha256 over the canonicalized
   packet JSON, with wall-clock / cache-flag fields stripped first so two
   consecutive runs at the same `decision_timestamp` (warm cache) produce
   the same hash. This is the contract from
   `docs/EVIDENCE_PACKET_V1_DRAFT.md` §14 rule 5.

2. `compute_locked_decision_id(ticker, decision_timestamp, packet_hash)`
   — sha256(ticker | decision_timestamp | evidence_packet_hash). This is
   the schema's §8e definition. The user's task spec also mentioned a
   UUID variant; I went with the schema-deterministic form and flagged
   the divergence so it can be revisited. In live mode "uniqueness
   across runs" still holds because `decision_timestamp` advances.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .schema import HASH_EXCLUDED_LEAF_KEYS, HASH_EXCLUDED_TOP_LEVEL_PATHS

_HASH_MASK_SENTINEL = "<excluded_from_hash>"


def _strip_top_level_path(packet: dict, path: tuple[str, ...]) -> None:
    """Replace the value at the given dotted path with a sentinel (in-place)."""
    if not path:
        return
    cursor: Any = packet
    for key in path[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            return
        cursor = cursor[key]
    if isinstance(cursor, dict):
        if path[-1] in cursor:
            cursor[path[-1]] = _HASH_MASK_SENTINEL


def _strip_leaf_keys(node: Any) -> Any:
    """Recursively replace excluded leaf-key values with the sentinel."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in HASH_EXCLUDED_LEAF_KEYS:
                out[k] = _HASH_MASK_SENTINEL
            else:
                out[k] = _strip_leaf_keys(v)
        return out
    if isinstance(node, list):
        return [_strip_leaf_keys(item) for item in node]
    return node


def _canonicalize(packet: dict) -> str:
    """Return the deterministic canonical JSON for hashing."""
    masked = copy.deepcopy(packet)
    for path in HASH_EXCLUDED_TOP_LEVEL_PATHS:
        # Special case: a path of length 1 means "strip the entire top-level key"
        if len(path) == 1:
            if path[0] in masked:
                masked[path[0]] = _HASH_MASK_SENTINEL
        else:
            _strip_top_level_path(masked, path)
    masked = _strip_leaf_keys(masked)
    return json.dumps(masked, sort_keys=True, separators=(",", ":"), default=str)


def compute_evidence_packet_hash(packet: dict) -> str:
    """sha256 over the canonicalized packet JSON."""
    canon = _canonicalize(packet)
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def compute_locked_decision_id(ticker: str, decision_timestamp: str,
                                 packet_hash: str) -> str:
    """sha256(ticker | decision_timestamp | evidence_packet_hash). Per
    schema §8e. Format mirrors `compute_evidence_packet_hash` for
    consistency. Deterministic for the same inputs."""
    payload = f"{ticker}|{decision_timestamp}|{packet_hash}".encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def canonical_json_for_inspection(packet: dict) -> str:
    """Expose the masked canonical JSON used in hashing — useful when a
    user reports `hash mismatch on identical inputs` and we want to
    diff what actually changed."""
    return _canonicalize(packet)
