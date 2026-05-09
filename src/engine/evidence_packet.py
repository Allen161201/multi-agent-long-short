"""
Evidence Packet Builder — creates timestamped, point-in-time evidence packets.

Every agent receives ONLY this packet. No free browsing, no future data.
All data fields carry their own availability timestamps.
Missing data is explicitly marked as "Data unavailable", never zero.

Agent prompt injection:
  "Base your analysis only on the information available as of {decision_timestamp}.
   Do not use knowledge after this date."
"""
from datetime import datetime, timedelta
from typing import Any

RULE_VERSION = "v0.7_credit_spread_sidesignal"
AGENT_PROMPT_VERSION = "v1.0_pre_llm_placeholder"

# Sentinel for missing data — NEVER use 0 or None
DATA_UNAVAILABLE = "Data unavailable"
NOT_EVALUATED = "Not evaluated"


def build_evidence_packet(
    ticker: str,
    decision_date: str,
    data_mode: str = "mock",
    market_data: dict | None = None,
    news_data: list[dict] | None = None,
    fundamentals: dict | None = None,
    reddit_data: dict | None = None,
    github_data: dict | None = None,
    h1b_data: dict | None = None,
    sec_data: dict | None = None,
    patent_data: dict | None = None,
    macro_data: dict | None = None,
) -> dict:
    """
    Build a complete evidence packet for one ticker/date.

    This packet is the ONLY input agents receive.
    All timestamps are validated to ensure no look-ahead bias.
    """
    now = datetime.now().isoformat()

    # Decision timestamp: 16:15 ET — the earliest time at which
    # Day T close prices, daily returns, and top-gainer lists are
    # confirmed and available. The market closes at 16:00, but
    # official close data is not usable until ~16:15.
    # Rule: Do NOT set this to 16:00 (that is the close itself).
    #        Day T close must never be both signal input AND executable price.
    decision_ts = f"{decision_date}T16:15:00-05:00"

    # Execution timestamp: next trading day open (T+1)
    # Simplified: assumes next calendar day; real version should skip weekends/holidays
    dt = datetime.strptime(decision_date, "%Y-%m-%d")
    exec_dt = dt + timedelta(days=1)
    # Skip Saturday/Sunday
    while exec_dt.weekday() >= 5:
        exec_dt += timedelta(days=1)
    execution_ts = exec_dt.strftime("%Y-%m-%d") + "T09:30:00-05:00"

    packet = {
        # ── Core Metadata ──
        "decision_date": decision_date,
        "decision_timestamp": decision_ts,
        "execution_timestamp": execution_ts,
        "rule_version": RULE_VERSION,
        "agent_prompt_version": AGENT_PROMPT_VERSION,
        "data_mode": data_mode,
        "ticker": ticker,
        "packet_built_at": now,

        # ── Agent Prompt Injection ──
        "agent_instruction": (
            f"Base your analysis only on the information available as of "
            f"{decision_ts}. Do not use knowledge after this date."
        ),

        # ── Market Data ──
        "market_data": _stamp_market_data(market_data, decision_date),

        # ── News / Events ──
        "news": _stamp_news(news_data, decision_date),

        # ── Fundamentals ──
        "fundamentals": _stamp_fundamentals(fundamentals, decision_date),

        # ── Alternative Data ──
        "reddit": _stamp_alt_data(reddit_data, "reddit", decision_date),
        "github": _stamp_alt_data(github_data, "github", decision_date),
        "h1b_lca": _stamp_alt_data(h1b_data, "h1b_lca", decision_date,
                                    conservative_lag_days=30),
        "sec_filings": _stamp_alt_data(sec_data, "sec_filings", decision_date),
        "patents": _stamp_alt_data(patent_data, "patents", decision_date),

        # ── Macro / Regime Data ──
        "macro_data": macro_data or {
            "status": DATA_UNAVAILABLE,
            "macro_regime": DATA_UNAVAILABLE,
            "data_available_as_of": DATA_UNAVAILABLE,
            "lookahead_safe": False,
            "source": "none",
        },

        # ── Data Availability Summary ──
        "data_availability": {},
    }

    # Build availability summary
    packet["data_availability"] = _build_availability_summary(packet)

    return packet


def _stamp_market_data(data: dict | None, decision_date: str) -> dict:
    """Stamp market data with availability timestamps."""
    if data is None:
        return {
            "status": DATA_UNAVAILABLE,
            "timestamp": DATA_UNAVAILABLE,
            "available_as_of": DATA_UNAVAILABLE,
            "source": "none",
        }
    return {
        "status": "available",
        # timestamp = when the market actually closed (16:00)
        # available_as_of = when the confirmed close data became usable (16:15)
        "timestamp": data.get("timestamp", f"{decision_date}T16:00:00-05:00"),
        "available_as_of": data.get("available_as_of",
                                     f"{decision_date}T16:15:00-05:00"),
        "source": data.get("source", "mock"),
        **{k: v for k, v in data.items()
           if k not in ("timestamp", "available_as_of", "source")},
    }


def _stamp_news(news_items: list[dict] | None, decision_date: str) -> dict:
    """Stamp news data. Filter out any items with timestamps after decision date."""
    if news_items is None:
        return {
            "status": DATA_UNAVAILABLE,
            "items": [],
            "count": 0,
        }

    decision_ts = f"{decision_date}T16:15:00-05:00"

    # In backtest mode, filter news to only items before decision timestamp
    filtered = []
    for item in news_items:
        pub_ts = item.get("published_at", item.get("timestamp", ""))
        if pub_ts and pub_ts > decision_ts:
            continue  # Skip future news
        filtered.append(item)

    return {
        "status": "available" if filtered else "no_items",
        "items": filtered,
        "count": len(filtered),
        "latest_timestamp": max(
            (i.get("published_at", i.get("timestamp", "")) for i in filtered),
            default=DATA_UNAVAILABLE,
        ),
    }


def _stamp_fundamentals(data: dict | None, decision_date: str) -> dict:
    """Stamp fundamentals. Enforce filing_date rule."""
    if data is None:
        return {
            "status": DATA_UNAVAILABLE,
            "filing_date": DATA_UNAVAILABLE,
            "fiscal_period_end": DATA_UNAVAILABLE,
            "available_as_of": DATA_UNAVAILABLE,
        }

    filing_date = data.get("filing_date", DATA_UNAVAILABLE)
    fiscal_end = data.get("fiscal_period_end",
                          data.get("period", DATA_UNAVAILABLE))

    # Filing date check: fundamental data only usable if filing_date <= decision_date
    usable = True
    if filing_date != DATA_UNAVAILABLE and filing_date > decision_date:
        usable = False

    if not usable:
        return {
            "status": "not_yet_filed",
            "filing_date": filing_date,
            "fiscal_period_end": fiscal_end,
            "available_as_of": DATA_UNAVAILABLE,
            "note": f"Filing date {filing_date} is after decision date {decision_date}. Not usable.",
        }

    return {
        "status": "available",
        "filing_date": filing_date,
        "fiscal_period_end": fiscal_end,
        "available_as_of": filing_date if filing_date != DATA_UNAVAILABLE
                           else DATA_UNAVAILABLE,
        **{k: v for k, v in data.items()
           if k not in ("filing_date", "fiscal_period_end", "period")},
    }


def _stamp_alt_data(
    data: dict | None,
    source_name: str,
    decision_date: str,
    conservative_lag_days: int = 0,
) -> dict:
    """Stamp alternative data with availability and lag."""
    if data is None:
        return {
            "status": DATA_UNAVAILABLE,
            "source": source_name,
            "available_as_of": DATA_UNAVAILABLE,
        }

    available_as_of = data.get("available_as_of", data.get("data_date", ""))

    # Apply conservative lag for certain data sources
    if conservative_lag_days > 0 and not available_as_of:
        dt = datetime.strptime(decision_date, "%Y-%m-%d")
        lag_dt = dt - timedelta(days=conservative_lag_days)
        available_as_of = lag_dt.strftime("%Y-%m-%d")

    return {
        "status": "available",
        "source": source_name,
        "available_as_of": available_as_of or DATA_UNAVAILABLE,
        "conservative_lag_days": conservative_lag_days,
        **{k: v for k, v in data.items()
           if k not in ("available_as_of", "data_date")},
    }


def _build_availability_summary(packet: dict) -> dict:
    """Build a summary of what data is available for this evidence packet."""
    sources = [
        "market_data", "news", "fundamentals",
        "reddit", "github", "h1b_lca", "sec_filings", "patents",
        "macro_data",
    ]
    summary = {}
    for src in sources:
        section = packet.get(src, {})
        status = section.get("status", DATA_UNAVAILABLE)
        available = section.get("available_as_of", DATA_UNAVAILABLE)
        summary[src] = {
            "status": status,
            "available_as_of": available,
            "usable": status not in (DATA_UNAVAILABLE, "not_yet_filed"),
        }
    return summary


def validate_no_lookahead(packet: dict) -> dict:
    """
    Validate that no data in the evidence packet has a timestamp
    after the decision_timestamp.

    Returns dict with validation result and any violations.
    """
    decision_ts = packet["decision_timestamp"]
    violations = []

    # Check market data
    md = packet.get("market_data", {})
    if md.get("available_as_of", "") > decision_ts and md.get("status") != DATA_UNAVAILABLE:
        violations.append(f"market_data.available_as_of ({md['available_as_of']}) > decision_timestamp")

    # Check fundamentals filing date
    fund = packet.get("fundamentals", {})
    filing = fund.get("filing_date", "")
    if filing and filing != DATA_UNAVAILABLE and filing > packet["decision_date"]:
        violations.append(f"fundamentals.filing_date ({filing}) > decision_date")

    # Check news timestamps
    news = packet.get("news", {})
    for item in news.get("items", []):
        ts = item.get("published_at", item.get("timestamp", ""))
        if ts and ts > decision_ts:
            violations.append(f"news item timestamp ({ts}) > decision_timestamp")

    return {
        "valid": len(violations) == 0,
        "violations": violations,
        "decision_timestamp": decision_ts,
        "checks_performed": [
            "market_data timestamp",
            "fundamentals filing_date",
            "news publication timestamps",
        ],
    }
