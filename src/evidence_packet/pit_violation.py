"""
PITViolationError — raised when strict-PIT replay mode detects an
adapter row whose `as_of` is after the configured cutoff.

The default packet path FLAGS such rows (envelope.lookahead_safe=False,
data_quality_flags entry with severity=critical) but does not raise —
the agent layer's §5.4 lookahead veto then collapses the decision to
veto. That existing behaviour is preserved unchanged for live mode.

Strict-PIT replay mode (Task 7, 2026-04-29 PM) is for the as-of replay
runs that drive the paper §Methods comparison. In that mode we want
the GENERATOR to abort BEFORE producing a packet at all, so the
operator sees the violation instead of accepting a quietly-degraded
cutoff. The runner / smoke driver opts into strict mode by passing
`strict_pit_mode=True` to `generate_evidence_packet`.

This is intentionally a separate exception class so callers can match
on it (e.g., the smoke driver logs a clear "PIT violation, aborting
replay" message rather than swallowing it as a generic ValueError).
"""
from __future__ import annotations

from typing import Any


class PITViolationError(RuntimeError):
    """A point-in-time violation was detected in strict-PIT mode.

    The exception carries the violations list verbatim so the caller
    can render a useful diagnostic without having to re-derive it.
    """

    def __init__(self, message: str, *, violations: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.violations: list[dict[str, Any]] = violations or []


__all__ = ["PITViolationError"]
