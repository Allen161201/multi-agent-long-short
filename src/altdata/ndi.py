"""
NDI — Narrative Divergence Index (RULES.md §23).

Cross-source narrative-disagreement metric on a single underlying event.
NDI ∈ [0.0, 1.0]; NaN/None when fewer than 2 sources share an
event_cluster.

Phase 1 status (D4 2026-05-01): the API is final and the module is
importable. The live-LLM extraction path is wired through whatever
LLMProvider is passed in. Under the deterministic stub provider (the
default), `compute_ndi` returns None with a clear stub-mode rationale
in the result dict — Phase 2 swaps the provider to AnthropicProvider
and the same code path produces real NDI values.

Public API:
    compute_ndi(
        news_items, *, decision_timestamp_utc=None, window_hours=24,
        provider=None, cache_dir=None,
    ) -> NDIResult

Per RULES.md §23 consumers:
    - narrative_event_agent reads NDI as framing input.
    - risk_agent reads NDI as sizing modulator (§23.3 thresholds).
    - alt_data_verification_agent MUST NOT consume NDI (§23.4 — circular).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, TypedDict

# Default cache root (created on first use).
_DEFAULT_CACHE_ROOT = (
    Path(__file__).resolve().parent.parent.parent / "data" / "altdata"
    / "ndi_cache"
)


class NDIResult(TypedDict, total=False):
    """Return shape for compute_ndi.

    `score` is the NDI value in [0.0, 1.0] when computed, None when not
    computable (insufficient sources, stub provider, etc.). `mode`
    explains why a value is None.
    """
    score: Optional[float]
    mode: str            # "computed" | "insufficient_sources" | "stub" | "no_news"
    n_sources: int
    n_items_considered: int
    rationale: str
    cache_hit: bool
    extracted_frames: list[dict]    # per-source (publisher/site, stance, causal_chain)


@dataclass(frozen=True)
class _Frame:
    """LLM-extracted narrative frame for a single news item / source."""
    source_id: str
    stance: str           # hawkish | dovish | neutral | ambiguous
    causal_chain: str     # short prose summary


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _is_stub_provider(provider: Any) -> bool:
    """Return True when the caller is on the deterministic stub. The
    stub has no narrative-extraction capability so NDI degrades to
    None+stub_mode rather than emitting a fabricated number."""
    if provider is None:
        return True
    name = getattr(provider, "name", "")
    return name in ("deterministic_stub", "stub")


def _parse_iso(ts: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 parse to a tz-aware UTC datetime. Accepts
    'YYYY-MM-DD HH:MM:SS' (FMP wall-clock) and ISO strings with offset."""
    if not ts:
        return None
    s = str(ts).strip()
    try:
        if " " in s and "T" not in s:
            # FMP 'YYYY-MM-DD HH:MM:SS' — treated as US/Eastern by news.py
            # convention; for NDI window grouping we only need ordering,
            # not timezone fidelity, so naive UTC is acceptable here.
            d = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return d.replace(tzinfo=timezone.utc)
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _filter_window(
    news_items: list[dict],
    decision_timestamp_utc: Optional[datetime],
    window_hours: int,
) -> list[dict]:
    """Keep items whose published_at is within `window_hours` before the
    decision_timestamp. When decision_timestamp_utc is None we anchor on
    the most recent item in the list (so the function is testable
    standalone)."""
    if not news_items:
        return []
    parsed = []
    for item in news_items:
        pub = _parse_iso(
            item.get("published_at_utc") or item.get("published_at")
        )
        if pub is None:
            continue
        parsed.append((pub, item))
    if not parsed:
        return []
    if decision_timestamp_utc is None:
        # Anchor on the latest published item — same effect as a rolling
        # window ending at "now".
        decision_timestamp_utc = max(p[0] for p in parsed)
    window_start = decision_timestamp_utc - timedelta(hours=window_hours)
    return [
        item for (pub, item) in parsed
        if window_start <= pub <= decision_timestamp_utc
    ]


def _source_id(item: dict) -> str:
    """Stable per-source identity. Prefers `publisher`, falls back to
    `site`, then domain from URL, then a hash of the title."""
    for k in ("publisher", "site"):
        v = item.get(k)
        if v:
            return str(v)
    url = item.get("url") or ""
    if url and "://" in url:
        try:
            return url.split("://", 1)[1].split("/", 1)[0]
        except IndexError:
            pass
    title = item.get("title") or ""
    return "src_" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:8]


def _cache_key(news_items: list[dict], event_cluster_id: str) -> str:
    """Deterministic cache key from cluster_id + sorted source_ids +
    title fingerprints. Replay-friendly."""
    sigs = []
    for it in news_items:
        sigs.append(
            _source_id(it) + "|" + (it.get("title") or "")[:120]
        )
    blob = event_cluster_id + "::" + "||".join(sorted(sigs))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _cache_path(cache_dir: Optional[Path], cluster_id: str, key: str) -> Path:
    root = cache_dir or _DEFAULT_CACHE_ROOT
    safe_cluster = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in cluster_id
    )[:60] or "unclustered"
    return root / safe_cluster / f"{key}.json"


# ────────────────────────────────────────────────────────────────────
# Live-LLM frame extraction + pairwise divergence
# ────────────────────────────────────────────────────────────────────
#
# These two functions are wired through any LLMProvider passed to
# compute_ndi. They are intentionally lightweight: NDI frame-extraction
# does NOT need the full agent infrastructure (no per-agent schema, no
# audit-record overhead). The provider signature is the same one used
# by src.agents.runner.

_FRAME_SYSTEM = (
    "You extract a single news article's narrative stance on its "
    "underlying event. Stance is one of {hawkish, dovish, neutral, "
    "ambiguous}. Causal chain is at most 25 words explaining what the "
    "article claims caused or will cause the event. Output JSON only "
    "matching the schema; no prose, no fences."
)

_FRAME_SCHEMA_INSTRUCTION = (
    'Output exactly: {"stance": "<hawkish|dovish|neutral|ambiguous>", '
    '"causal_chain": "<<=25 words>"}'
)

_DISTANCE_SYSTEM = (
    "You compare two news-article narrative frames on the same event. "
    "Return a divergence score in [0.0, 1.0]: 0 = identical framing, "
    "1 = directly contradictory. Output JSON only matching the schema."
)

_DISTANCE_SCHEMA_INSTRUCTION = (
    'Output exactly: {"divergence": <float 0..1>}'
)


def _extract_frame(provider: Any, item: dict) -> Optional[_Frame]:
    """Single-call frame extraction. Returns None on any failure (the
    caller treats None as "skip this source from the divergence
    calculation")."""
    title = (item.get("title") or "").strip()
    excerpt = (item.get("text_excerpt") or item.get("text") or "").strip()
    if not title and not excerpt:
        return None
    user_prompt = (
        f"Article title: {title}\n"
        f"Article excerpt: {excerpt[:1200]}\n\n"
        f"{_FRAME_SCHEMA_INSTRUCTION}"
    )
    try:
        resp = provider.complete(
            system_prompt=_FRAME_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=128,
            temperature=0.0,
            response_format="json_object",
            agent_schema_name="NDIFrameExtraction",
        )
        text = resp.get("raw_text") if isinstance(resp, dict) else getattr(
            resp, "raw_text", ""
        )
        # Strip code fences if any
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        payload = json.loads(text)
        stance = str(payload.get("stance", "ambiguous")).strip().lower()
        if stance not in {"hawkish", "dovish", "neutral", "ambiguous"}:
            stance = "ambiguous"
        causal = str(payload.get("causal_chain", "")).strip()
        return _Frame(
            source_id=_source_id(item),
            stance=stance,
            causal_chain=causal,
        )
    except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError):
        return None
    except Exception:  # pylint: disable=broad-except
        return None


def _pairwise_divergence(provider: Any, fa: _Frame, fb: _Frame) -> Optional[float]:
    """LLM-judge symmetric divergence. None on any failure."""
    user_prompt = (
        f"Frame A — source={fa.source_id}, stance={fa.stance}, "
        f"causal_chain={fa.causal_chain!r}\n"
        f"Frame B — source={fb.source_id}, stance={fb.stance}, "
        f"causal_chain={fb.causal_chain!r}\n\n"
        f"{_DISTANCE_SCHEMA_INSTRUCTION}"
    )
    try:
        resp = provider.complete(
            system_prompt=_DISTANCE_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=64,
            temperature=0.0,
            response_format="json_object",
            agent_schema_name="NDIDivergence",
        )
        text = resp.get("raw_text") if isinstance(resp, dict) else getattr(
            resp, "raw_text", ""
        )
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        payload = json.loads(text)
        d = float(payload.get("divergence"))
        if not math.isfinite(d):
            return None
        return max(0.0, min(1.0, d))
    except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError):
        return None
    except Exception:  # pylint: disable=broad-except
        return None


# ────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────

def compute_ndi(
    news_items: list[dict],
    *,
    event_cluster_id: str = "unclustered",
    decision_timestamp_utc: Optional[datetime] = None,
    window_hours: int = 24,
    provider: Any = None,
    cache_dir: Optional[Path] = None,
) -> NDIResult:
    """Compute NDI for a set of news items.

    See module docstring for full contract.

    Returns an NDIResult dict. `score` is None when:
      - news_items is empty
      - <2 distinct sources within the window
      - provider is the deterministic stub (no narrative-extraction
        capability)
      - all per-source frame extractions fail (e.g. titles all empty)
    """
    base = NDIResult(
        score=None,
        mode="computed",
        n_sources=0,
        n_items_considered=0,
        rationale="",
        cache_hit=False,
        extracted_frames=[],
    )

    if not news_items:
        base["mode"] = "no_news"
        base["rationale"] = "news_items list was empty"
        return base

    in_window = _filter_window(
        news_items, decision_timestamp_utc, window_hours
    )
    base["n_items_considered"] = len(in_window)

    # Distinct sources gate — §23.2 requires ≥2 sources for NDI to be
    # defined.
    by_source: dict[str, dict] = {}
    for item in in_window:
        sid = _source_id(item)
        # Keep first-seen item per source within the window.
        by_source.setdefault(sid, item)
    base["n_sources"] = len(by_source)

    if len(by_source) < 2:
        base["mode"] = "insufficient_sources"
        base["rationale"] = (
            f"only {len(by_source)} distinct source(s) within "
            f"{window_hours}h window; §23.2 requires ≥2"
        )
        return base

    # Cache check
    cache_p = _cache_path(
        cache_dir, event_cluster_id,
        _cache_key(list(by_source.values()), event_cluster_id),
    )
    if cache_p.exists():
        try:
            cached = json.loads(cache_p.read_text(encoding="utf-8"))
            cached["cache_hit"] = True
            return cached
        except (OSError, json.JSONDecodeError):
            pass  # fall through to recompute

    # Stub-provider gate — §23 says NaN-equivalent under stub.
    if _is_stub_provider(provider):
        base["mode"] = "stub"
        base["rationale"] = (
            "deterministic_stub provider has no narrative-extraction "
            "capability; NDI deferred to live-LLM phase per §23 / D4 plan"
        )
        return base

    # Live-LLM path — extract frames, compute pairwise divergence.
    frames: list[_Frame] = []
    for sid, item in by_source.items():
        f = _extract_frame(provider, item)
        if f is not None:
            frames.append(f)

    base["extracted_frames"] = [asdict(f) for f in frames]

    if len(frames) < 2:
        base["mode"] = "insufficient_sources"
        base["rationale"] = (
            f"only {len(frames)} frame(s) successfully extracted; "
            "frame-extraction failures count as 'unverifiable source' "
            "per §11.2"
        )
        return base

    distances: list[float] = []
    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            d = _pairwise_divergence(provider, frames[i], frames[j])
            if d is not None:
                distances.append(d)

    if not distances:
        base["mode"] = "insufficient_sources"
        base["rationale"] = "all pairwise divergence calls failed"
        return base

    score = float(sum(distances) / len(distances))
    base["score"] = max(0.0, min(1.0, score))
    base["rationale"] = (
        f"mean pairwise divergence over {len(distances)} pair(s) "
        f"from {len(frames)} frame(s)"
    )

    # Persist
    try:
        cache_p.parent.mkdir(parents=True, exist_ok=True)
        cache_p.write_text(
            json.dumps(base, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass

    return base


__all__ = ["compute_ndi", "NDIResult"]
