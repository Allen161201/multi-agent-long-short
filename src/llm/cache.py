"""
Disk-based JSON cache for LLM agent outputs.

Layout:
    data/cache/llm/<agent_name>/<cache_key>.json

Cache key:
    sha256(agent_name | model_id | prompt_version | ticker |
           decision_timestamp | evidence_packet_hash)

Atomic writes (temp file + os.replace) so a concurrent reader never
sees a half-written record. Reads are simple json.load. Stats are
fast — they count files and aggregate sizes via os.stat without
loading record bodies (the user explicitly asked for this).

No eviction. We do not auto-purge. If the directory grows too large
the operator deletes a sub-tree by hand. Each prompt-version bump
naturally stops serving old entries (the cache key includes
prompt_version), so the old files become harmless dead weight.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Default project-relative root. Callers can override per-instance.
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "llm"


def build_cache_key(
    *,
    agent_name: str,
    model_id: str,
    prompt_version: str,
    ticker: str,
    decision_timestamp: str,
    evidence_packet_hash: str,
) -> str:
    """Canonical sha256 key. The pipe-delimited concatenation is
    deliberately readable so that an operator hashing the same inputs
    by hand reaches the same key."""
    payload = "|".join([
        agent_name,
        model_id,
        prompt_version,
        ticker,
        decision_timestamp,
        evidence_packet_hash,
    ])
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLMCache:
    """Per-agent directory; one JSON record per cache key.

    Thread-safety: writes use os.replace, which is atomic on Windows
    NTFS and POSIX. Concurrent readers see either the old file or the
    new file, never partial. We keep a process-local stats lock so
    `hits` / `misses` counters don't tear on multi-thread runs.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else _DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self._stats_lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    # ── path helpers ───────────────────────────────────────────────
    def _agent_dir(self, agent_name: str) -> Path:
        d = self.root / agent_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _record_path(self, agent_name: str, cache_key: str) -> Path:
        # cache_key carries the "sha256:" prefix; strip the colon so we
        # do not put colons in filenames (illegal on Windows).
        safe = cache_key.replace(":", "_")
        return self._agent_dir(agent_name) / f"{safe}.json"

    # ── public API ─────────────────────────────────────────────────
    def get(self, agent_name: str, cache_key: str) -> dict | None:
        path = self._record_path(agent_name, cache_key)
        if not path.exists():
            with self._stats_lock:
                self._misses += 1
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            # Corrupt record → treat as miss; do not raise.
            logger.warning("LLMCache.get: corrupt record %s (%s)", path, e)
            with self._stats_lock:
                self._misses += 1
            return None
        with self._stats_lock:
            self._hits += 1
        return rec

    def put(self, agent_name: str, cache_key: str, record: dict) -> Path:
        """Atomic write. Returns the final path."""
        path = self._record_path(agent_name, cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory, then os.replace.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".tmp_", suffix=".json", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            # If anything fails, clean up the temp file so the dir does
            # not accumulate cruft.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return path

    def stats(self) -> dict:
        """File-count + size aggregate. Does NOT load records."""
        total_records = 0
        total_size = 0
        per_agent: dict[str, int] = {}
        if self.root.exists():
            for agent_dir in self.root.iterdir():
                if not agent_dir.is_dir():
                    continue
                count = 0
                for entry in agent_dir.iterdir():
                    if entry.is_file() and entry.suffix == ".json":
                        try:
                            total_size += entry.stat().st_size
                        except OSError:
                            pass
                        count += 1
                per_agent[agent_dir.name] = count
                total_records += count
        with self._stats_lock:
            hits, misses = self._hits, self._misses
        return {
            "hits": hits,
            "misses": misses,
            "total_records": total_records,
            "total_size_bytes": total_size,
            "per_agent": per_agent,
            "root": str(self.root),
        }


def make_record(
    *,
    cache_key: str,
    key_components: dict,
    raw_response: dict,
    parsed_output: dict,
    schema_version: str,
    validation_status: str,
) -> dict:
    """Helper to build the canonical cache record shape."""
    return {
        "cache_key": cache_key,
        "key_components": key_components,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_response": raw_response,
        "parsed_output": parsed_output,
        "schema_version": schema_version,
        "validation_status": validation_status,
    }
