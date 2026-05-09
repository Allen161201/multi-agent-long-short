"""
decision_time_discipline block — REQUIRED on every packet (schema §8e).

Also owns the auto-detection of the live sub-mode from US/Eastern wall
clock and the mapping between the generator's sub-mode enum and the
schema's `decision_mode` enum.

Schema-allowed `decision_time_discipline.decision_mode` values:
    pre_market | opening_window | end_of_day_surge | historical_replay

Generator-level sub-modes (from `schema.DecisionSubMode`):
    pre_market | opening_window | end_of_day | intraday_review

Mapping:
    pre_market         -> pre_market
    opening_window     -> opening_window
    end_of_day         -> end_of_day_surge       (closest schema match)
    intraday_review    -> opening_window         (until schema is extended)

The mapping for `intraday_review` is conservative — see the report's
"schema ambiguities" section.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..schema import BlockKey, BlockStatus, DecisionSubMode

ET = ZoneInfo("America/New_York")

SUB_MODE_TO_SCHEMA_MODE = {
    DecisionSubMode.PRE_MARKET:      "pre_market",
    DecisionSubMode.OPENING_WINDOW:  "opening_window",
    DecisionSubMode.END_OF_DAY:      "end_of_day_surge",
    DecisionSubMode.INTRADAY_REVIEW: "opening_window",
}


def auto_detect_sub_mode(now_et: datetime) -> str:
    """Pick a sub-mode from the current US/Eastern wall clock.

    Boundaries:
      - pre_market       : before 09:30 ET (any day, including weekends — no rule check here)
      - opening_window   : 09:30 to 09:45 ET inclusive
      - intraday_review  : 09:45 to 16:15 ET (regular trading hours)
      - end_of_day       : after 16:15 ET
    """
    t = now_et.timetz()
    open_time = time(9, 30, tzinfo=ET)
    open_window_end = time(9, 45, tzinfo=ET)
    eod_time = time(16, 15, tzinfo=ET)

    if t < open_time:
        return DecisionSubMode.PRE_MARKET
    if open_time <= t < open_window_end:
        return DecisionSubMode.OPENING_WINDOW
    if open_window_end <= t < eod_time:
        return DecisionSubMode.INTRADAY_REVIEW
    return DecisionSubMode.END_OF_DAY


def _next_execution_timestamp(decision_dt_et: datetime, sub_mode: str) -> datetime:
    """Conservative execution timestamp per the policy doc.

    Live mode: execution_timestamp is the next valid execution window
    AFTER the decision. We don't ramp into broker integration here — the
    field is for downstream auditability only.
    """
    if sub_mode == DecisionSubMode.PRE_MARKET:
        # Next regular-session open
        return decision_dt_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if sub_mode == DecisionSubMode.OPENING_WINDOW:
        # 09:45 ET — end of opening window
        return decision_dt_et.replace(hour=9, minute=45, second=0, microsecond=0)
    if sub_mode == DecisionSubMode.END_OF_DAY:
        # T+1 next-open per the policy doc end_of_day_surge mode
        next_day = decision_dt_et + timedelta(days=1)
        while next_day.weekday() >= 5:  # skip Sat/Sun
            next_day += timedelta(days=1)
        return next_day.replace(hour=9, minute=30, second=0, microsecond=0)
    # intraday_review — execute at next 30-minute mark, conservative
    minute_offset = 30 - (decision_dt_et.minute % 30)
    return decision_dt_et + timedelta(minutes=minute_offset)


def _allowed_data_classes_for(sub_mode: str) -> list[str]:
    """Return the schema's `allowed_data_classes` list, sub-mode-specific."""
    if sub_mode == DecisionSubMode.PRE_MARKET:
        return ["previous_close", "previous_day_OHLCV",
                "pre_market_quotes_pre_cutoff", "filings_pre_cutoff",
                "macro_pre_cutoff"]
    if sub_mode == DecisionSubMode.OPENING_WINDOW:
        return ["previous_close", "open_print",
                "first_n_minute_OHLCV_pre_cutoff", "filings_pre_cutoff",
                "macro_pre_cutoff"]
    if sub_mode == DecisionSubMode.END_OF_DAY:
        return ["full_day_OHLCV_post_close", "filings_pre_cutoff",
                "macro_pre_cutoff"]
    # intraday_review
    return ["previous_close", "open_print", "intraday_OHLCV_pre_cutoff",
            "filings_pre_cutoff", "macro_pre_cutoff"]


def _mode_rationale(sub_mode: str, decision_dt_et: datetime) -> str:
    if sub_mode == DecisionSubMode.PRE_MARKET:
        return (f"decision_timestamp {decision_dt_et.isoformat()} is before 09:30 ET; "
                "pre-market discipline applies. Executable at next regular-session open.")
    if sub_mode == DecisionSubMode.OPENING_WINDOW:
        return (f"decision_timestamp {decision_dt_et.isoformat()} is in 09:30-09:45 ET window; "
                "opening-window discipline applies — first 10 minutes of OHLCV are the cleanest read.")
    if sub_mode == DecisionSubMode.END_OF_DAY:
        return (f"decision_timestamp {decision_dt_et.isoformat()} is after 16:15 ET; "
                "end_of_day_surge discipline applies. Executable T+1 next-open per policy.")
    return (f"decision_timestamp {decision_dt_et.isoformat()} is in regular trading hours "
            "(09:45-16:15 ET) but not the opening window; intraday_review sub-mode flagged. "
            "This sub-mode is NOT one of the four schema-defined modes — it is a generator-level "
            "label that maps to schema decision_mode='opening_window' for backward compatibility. "
            "See the report's schema-ambiguities section.")


def build(
    *,
    ticker: str,
    decision_timestamp_et: datetime,
    sub_mode: str,
    analysis_run_time_utc: datetime,
) -> dict:
    """Build the decision_time_discipline block.

    The orchestrator fills in `evidence_packet_hash` and `locked_decision_id`
    AFTER computing the hash over the rest of the packet. We seed those
    here with placeholders so the schema's required-field set is intact
    during the hash computation.
    """
    schema_mode = SUB_MODE_TO_SCHEMA_MODE[sub_mode]
    execution_dt = _next_execution_timestamp(decision_timestamp_et, sub_mode)
    return {
        "status": BlockStatus.OK,
        "decision_mode": schema_mode,
        "generator_sub_mode": sub_mode,

        # Four timestamps that pin the packet
        "analysis_run_time": analysis_run_time_utc.astimezone(ET).isoformat(),
        "analysis_run_time_utc": analysis_run_time_utc.isoformat(),
        "decision_timestamp": decision_timestamp_et.isoformat(),
        "allowed_data_cutoff": decision_timestamp_et.isoformat(),
        "execution_timestamp": execution_dt.isoformat(),

        # Look-ahead enforcement (filled by orchestrator post-build)
        "data_after_cutoff_used": False,
        "lookahead_safe": True,

        # Decision identity (filled by orchestrator post-hash)
        "evidence_packet_hash_ref": None,   # echoes envelope.evidence_packet_hash
        "locked_decision_id_ref": None,     # echoes envelope.locked_decision_id
        "immutable_decision_flag": True,

        # Mode-specific allowed-data fingerprint
        "allowed_data_classes": _allowed_data_classes_for(sub_mode),

        # Rationale + sleeve preference
        "mode_rationale": _mode_rationale(sub_mode, decision_timestamp_et),
        "preferred_for_sleeve": ["surge_short"]
            if sub_mode in (DecisionSubMode.PRE_MARKET, DecisionSubMode.OPENING_WINDOW)
            else ["quality_long"] if sub_mode == DecisionSubMode.END_OF_DAY else [],

        # Provenance
        "source": "generator_internal",
    }


def block_key() -> str:
    return BlockKey.DECISION_TIME_DISCIPLINE
