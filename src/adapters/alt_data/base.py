"""
Alt-data adapter base class.

Subclasses implement two narrow hooks:
  - _fetch_live(...)  : real network fetch
  - _fetch_stub(...)  : deterministic canned data
The base class owns:
  - PIT cutoff filter (drops rows with as_of > decision_timestamp)
  - on-disk cache lookup / write
  - stub fallthrough (env STUB_MODE=true, missing creds, network error)
  - source_flag normalisation per RULES.md §11.3 / R-ALTDATA-09
  - self-throttle scaffolding (each subclass tunes the rate)

A subclass cannot bypass the PIT filter or the cache: fetch() is
non-virtual; subclasses override only the private hooks.

RULES.md anchors:
  - §11.1 every feature carries a timestamp (`as_of`)
  - §11.2 missing data is `Data unavailable`, never zero
  - §11.3 source_flag ∈ {live_<src>, cache, mock_fallback, live_<src>_failed}
  - §11.4 aggregations across sources record a manifest
  - §11.5 no lookahead — only data with as_of <= decision_timestamp
  - §11.6 outputs are descriptors, not trading rules
  - §11.8 missing data = `Data unavailable / not_evaluated`
  - §11.9 source / fallback labels must be honest
  - §19.15 sticky pause on 429/402; never silent retry beyond cap
  - §19.16 no PII; redact credentials in logs
"""
from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, ClassVar


logger = logging.getLogger(__name__)

DATA_UNAVAILABLE = "Data unavailable"
NOT_EVALUATED = "not_evaluated"

# Canonical source_flag values per RULES.md §11.3. The live_<src> and
# live_<src>_failed values are derived per-adapter from source_id.
SOURCE_FLAG_CACHE = "cache"
SOURCE_FLAG_MOCK_FALLBACK = "mock_fallback"


@dataclass
class AltDataResult:
    """Canonical return shape for an adapter call.

    All adapters return one of these. The packet generator merges
    rows[] into the relevant block subsection and lifts manifest into
    the top-level alt_data_manifest.
    """
    source_id: str
    block_target: str
    rows: list[dict] = field(default_factory=list)
    source_flag: str = ""              # see §11.3 enum
    data_quality_flags: list[dict] = field(default_factory=list)
    data_available_as_of: datetime | None = None
    manifest: dict = field(default_factory=dict)
    extraction_status: str = "ok"      # ok | failed | stub | partial
    error_class: str | None = None     # populated on failure

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.data_available_as_of is not None:
            d["data_available_as_of"] = self.data_available_as_of.isoformat()
        return d


def _stub_mode_active() -> bool:
    return os.environ.get("STUB_MODE", "").strip().lower() in ("1", "true", "yes")


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class AltDataAdapter(ABC):
    """Abstract base for free-public-source alt-data adapters.

    Subclasses MUST set:
      - source_id   : e.g. "wikipedia_pageviews"
      - block_target: which packet block this contributes to
                      ("alternative_data_features" |
                       "filing_confirmation" |
                       "sentiment_community_ownership_evidence")

    Subclasses MUST implement:
      - _fetch_live(ticker, as_of, decision_timestamp) -> AltDataResult
      - _fetch_stub(ticker, as_of, decision_timestamp) -> AltDataResult
      - credentials_present() -> bool   (default: True for no-cred sources)
    """

    source_id: ClassVar[str] = ""
    block_target: ClassVar[str] = ""

    # Subclasses tune these.
    rate_limit_per_second: ClassVar[float] = 10.0
    sticky_pause_seconds_on_429: ClassVar[int] = 60
    cache_root_override: ClassVar[Path | None] = None

    def __init__(self) -> None:
        if not self.source_id:
            raise ValueError(f"{type(self).__name__} missing class attr `source_id`")
        if not self.block_target:
            raise ValueError(f"{type(self).__name__} missing class attr `block_target`")
        self._last_call_ts: float = 0.0
        self._sticky_pause_until: float = 0.0

    # ── public, non-virtual entry point ───────────────────────────
    def fetch(
        self,
        *,
        ticker: str,
        as_of: datetime,
        decision_timestamp: datetime,
        stub_mode: bool | None = None,
    ) -> AltDataResult:
        """Run a fetch. PIT-filtered, cached, stub-aware.

        stub_mode=None (default): consult STUB_MODE env var + credential
        availability. stub_mode=True forces stub. stub_mode=False forces
        live attempt (still falls through to stub on network failure).
        """
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("ticker must be a non-empty string")
        ticker = ticker.strip().upper()

        as_of = _ensure_utc(as_of)
        decision_timestamp = _ensure_utc(decision_timestamp)
        if as_of > decision_timestamp:
            # Caller asked for data after the PIT cutoff. Honour the
            # cutoff, not the request.
            as_of = decision_timestamp

        # Decide path: live vs stub.
        if stub_mode is None:
            stub_mode = _stub_mode_active() or not self.credentials_present()

        # ── 1. Cache lookup (single read; no write inside fetch) ──
        cache_path = self._cache_path(ticker, as_of)
        cached = self._cache_get(cache_path)
        if cached is not None:
            cached.source_flag = SOURCE_FLAG_CACHE
            cached.rows = self._pit_filter(cached.rows, decision_timestamp)
            return cached

        # ── 2. Stub or live ──
        if stub_mode:
            result = self._fetch_stub_safe(ticker, as_of, decision_timestamp)
        else:
            result = self._fetch_live_safe(ticker, as_of, decision_timestamp)

        # ── 3. PIT cutoff filter is the base class's responsibility ──
        result.rows = self._pit_filter(result.rows, decision_timestamp)

        # ── 4. Set data_available_as_of from row max(as_of) ──
        max_as_of = self._max_row_as_of(result.rows)
        if max_as_of is not None:
            result.data_available_as_of = max_as_of

        # ── 5. Cache write (only on successful real or stub fetch) ──
        # Cache stub outputs too — same reasoning as live: identical
        # (ticker, as_of_date) inputs deserve identical outputs.
        if result.extraction_status in ("ok", "stub"):
            self._cache_put(cache_path, result)

        return result

    # ── subclass override hooks ───────────────────────────────────
    @abstractmethod
    def _fetch_live(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult: ...

    @abstractmethod
    def _fetch_stub(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult: ...

    def credentials_present(self) -> bool:
        """Override in subclasses that require a credential. Default
        True (no-credential sources fall through to live unless
        STUB_MODE is set)."""
        return True

    # ── internal helpers ──────────────────────────────────────────
    def _fetch_live_safe(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        """Wrap _fetch_live with throttle + 429 sticky pause + try/except.

        Per §19.15 (sticky pause on 429/402; never silent retry beyond
        cap) and §5.12 (bounded retry — zero on 401/402/429): if the
        sticky pause is in effect, we skip the live call and fall
        through to a stub_fallback result with extraction_status
        marked.
        """
        now = time.monotonic()
        if now < self._sticky_pause_until:
            wait_remaining = self._sticky_pause_until - now
            logger.warning(
                "%s: sticky pause active (%.1fs remaining); falling through to stub",
                self.source_id, wait_remaining,
            )
            r = self._fetch_stub(ticker, as_of, decision_timestamp)
            r.source_flag = self._failed_source_flag()
            r.extraction_status = "failed"
            r.error_class = "rate_limit_sticky_pause"
            r.data_quality_flags.append({
                "kind": "rate_limit_sticky_pause",
                "severity": "warning",
                "detail": f"{self.source_id}: sticky pause until "
                          f"+{wait_remaining:.0f}s; stubbed",
            })
            return r

        # Self-throttle: cap req rate at rate_limit_per_second.
        min_gap = 1.0 / max(self.rate_limit_per_second, 0.1)
        gap = now - self._last_call_ts
        if gap < min_gap:
            time.sleep(min_gap - gap)
        self._last_call_ts = time.monotonic()

        try:
            r = self._fetch_live(ticker, as_of, decision_timestamp)
        except Exception as e:  # pragma: no cover (live-only)
            logger.warning(
                "%s: live fetch failed (%s: %s); falling through to stub",
                self.source_id, type(e).__name__, e,
            )
            r = self._fetch_stub(ticker, as_of, decision_timestamp)
            r.source_flag = self._failed_source_flag()
            r.extraction_status = "failed"
            r.error_class = type(e).__name__
            r.data_quality_flags.append({
                "kind": "adapter_live_fetch_failed",
                "severity": "warning",
                "detail": f"{self.source_id} live fetch raised {type(e).__name__}; "
                          f"stub data substituted (NOT ground truth)",
            })
        return r

    def _fetch_stub_safe(
        self, ticker: str, as_of: datetime, decision_timestamp: datetime,
    ) -> AltDataResult:
        r = self._fetch_stub(ticker, as_of, decision_timestamp)
        # Honour the contract that stub rows carry mock_fallback unless
        # the subclass already set something more specific.
        if not r.source_flag:
            r.source_flag = SOURCE_FLAG_MOCK_FALLBACK
        if r.extraction_status == "ok":
            r.extraction_status = "stub"
        return r

    def _pit_filter(self, rows: list[dict], decision_timestamp: datetime) -> list[dict]:
        """RULES.md §11.5: at decision time T, only rows with as_of <= T
        survive. Rows missing as_of are kept (the subclass is
        responsible for emitting an as_of on every row per §11.1; this
        layer logs but does not silently drop)."""
        kept: list[dict] = []
        for row in rows:
            as_of_val = row.get("as_of")
            row_dt = _coerce_to_utc(as_of_val) if as_of_val else None
            if row_dt is None:
                # Subclass violated §11.1; let it through but flag.
                logger.warning(
                    "%s: row missing as_of: %s",
                    self.source_id, _short_repr(row),
                )
                kept.append(row)
                continue
            if row_dt > decision_timestamp:
                continue
            kept.append(row)
        return kept

    def _max_row_as_of(self, rows: list[dict]) -> datetime | None:
        max_dt: datetime | None = None
        for row in rows:
            dt = _coerce_to_utc(row.get("as_of"))
            if dt is None:
                continue
            if max_dt is None or dt > max_dt:
                max_dt = dt
        return max_dt

    # ── cache helpers ─────────────────────────────────────────────
    def _cache_root(self) -> Path:
        if self.cache_root_override is not None:
            return Path(self.cache_root_override)
        # data/cache/altdata/<source_id>/ relative to project root
        # (src/adapters/alt_data/base.py -> ../../../.. = project root)
        proj_root = Path(__file__).resolve().parents[3]
        return proj_root / "data" / "cache" / "altdata" / self.source_id

    def _cache_path(self, ticker: str, as_of: datetime) -> Path:
        as_of_date = as_of.date().isoformat()
        return self._cache_root() / ticker / f"{as_of_date}.json"

    def _cache_get(self, path: Path) -> AltDataResult | None:
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("%s: corrupt cache record %s (%s); ignoring",
                           self.source_id, path, e)
            return None
        # Reconstruct AltDataResult shape; data_available_as_of stays
        # ISO string, normalize back to datetime.
        dav = rec.get("data_available_as_of")
        if isinstance(dav, str):
            try:
                rec["data_available_as_of"] = datetime.fromisoformat(
                    dav.replace("Z", "+00:00")
                )
            except ValueError:
                rec["data_available_as_of"] = None
        try:
            return AltDataResult(
                source_id=rec["source_id"],
                block_target=rec["block_target"],
                rows=rec.get("rows", []),
                source_flag=rec.get("source_flag", ""),
                data_quality_flags=rec.get("data_quality_flags", []),
                data_available_as_of=rec.get("data_available_as_of"),
                manifest=rec.get("manifest", {}),
                extraction_status=rec.get("extraction_status", "ok"),
                error_class=rec.get("error_class"),
            )
        except KeyError:
            return None

    def _cache_put(self, path: Path, result: AltDataResult) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2,
                          default=str)
        except OSError as e:
            logger.warning("%s: cache write failed at %s (%s)",
                           self.source_id, path, e)

    # ── source-flag helpers ───────────────────────────────────────
    def _live_source_flag(self) -> str:
        return f"live_{self.source_id}"

    def _failed_source_flag(self) -> str:
        return f"live_{self.source_id}_failed"

    # ── 429 sticky pause hook ─────────────────────────────────────
    def _trip_sticky_pause(self, reason: str) -> None:
        """Subclass calls this when it observes 429/402. The next
        fetch() call within the pause window short-circuits to stub
        (NEVER silently retried — §5.12)."""
        self._sticky_pause_until = (
            time.monotonic() + self.sticky_pause_seconds_on_429
        )
        logger.warning(
            "%s: tripped %ds sticky pause (%s)",
            self.source_id, self.sticky_pause_seconds_on_429, reason,
        )


# ── module-level coerce helper ────────────────────────────────────
def _coerce_to_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            # Try date-only
            try:
                d = datetime.strptime(s, "%Y-%m-%d")
            except ValueError:
                return None
        return _ensure_utc(d)
    return None


def _short_repr(d: dict) -> str:
    s = json.dumps(d, default=str)
    return s if len(s) < 120 else s[:120] + "..."
