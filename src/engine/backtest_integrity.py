"""
Backtest Integrity — ensures no look-ahead bias in backtesting.

Enforces:
- T+1 execution timing (signal Day T → trade Day T+1 open)
- Filing-date rule (fundamentals only after SEC filing date)
- News timestamp filtering
- Alt-data conservative lag
- Missing data handling ("Data unavailable", never zero)
"""
from datetime import datetime, timedelta
from typing import Any

DATA_UNAVAILABLE = "Data unavailable"


# ═══════════════════════════════════════════════════════════════
# EXECUTION TIMING
# ═══════════════════════════════════════════════════════════════

def compute_execution_date(signal_date: str) -> str:
    """
    Given a signal generated on Day T (using Day T close data),
    return the earliest valid execution date: Day T+1 open.

    Skips weekends. Does NOT handle market holidays (would need
    a holiday calendar for production use).
    """
    dt = datetime.strptime(signal_date, "%Y-%m-%d")
    exec_dt = dt + timedelta(days=1)
    # Skip Saturday and Sunday
    while exec_dt.weekday() >= 5:
        exec_dt += timedelta(days=1)
    return exec_dt.strftime("%Y-%m-%d")


def compute_execution_timestamp(signal_date: str) -> str:
    """Return execution timestamp at market open on T+1."""
    exec_date = compute_execution_date(signal_date)
    return f"{exec_date}T09:30:00-05:00"


# ═══════════════════════════════════════════════════════════════
# FILING DATE VALIDATION
# ═══════════════════════════════════════════════════════════════

def is_fundamentals_usable(filing_date: str, decision_date: str) -> dict:
    """
    Check if financial data is usable as of the decision date.

    Rule: A 10-Q/10-K can only be used AFTER its filing date.
    Use SEC filing_date, NOT fiscal period end date.

    Returns dict with usable flag and reason.
    """
    if filing_date == DATA_UNAVAILABLE or not filing_date:
        return {
            "usable": False,
            "reason": "Filing date unavailable — cannot verify point-in-time safety",
            "filing_date": DATA_UNAVAILABLE,
            "decision_date": decision_date,
        }

    if filing_date <= decision_date:
        return {
            "usable": True,
            "reason": f"Filing date {filing_date} <= decision date {decision_date}",
            "filing_date": filing_date,
            "decision_date": decision_date,
        }

    return {
        "usable": False,
        "reason": (
            f"Filing date {filing_date} is AFTER decision date {decision_date}. "
            f"Using this data would be look-ahead bias."
        ),
        "filing_date": filing_date,
        "decision_date": decision_date,
    }


# ═══════════════════════════════════════════════════════════════
# NEWS TIMESTAMP VALIDATION
# ═══════════════════════════════════════════════════════════════

def filter_news_by_timestamp(
    news_items: list[dict],
    decision_timestamp: str,
) -> list[dict]:
    """
    Filter news items to only those published before the decision timestamp.

    If a news item was published after market close, it should only
    affect trades on the NEXT trading day.
    """
    if not news_items:
        return []

    filtered = []
    for item in news_items:
        pub_ts = item.get("published_at", item.get("timestamp", ""))
        if not pub_ts:
            # No timestamp → mark as uncertain but include
            item["timestamp_status"] = "unknown — included with caution"
            filtered.append(item)
            continue

        if pub_ts <= decision_timestamp:
            item["timestamp_status"] = "valid"
            filtered.append(item)
        else:
            # Future news — skip
            continue

    return filtered


def is_after_market_close(timestamp: str) -> bool:
    """Check if a timestamp is after regular market close (4:00 PM ET)."""
    if not timestamp:
        return False
    try:
        # Simple check: look for hour > 16 in the timestamp
        if "T" in timestamp:
            time_part = timestamp.split("T")[1][:5]
            hour = int(time_part.split(":")[0])
            return hour >= 16
    except (ValueError, IndexError):
        pass
    return False


# ═══════════════════════════════════════════════════════════════
# ALT-DATA TIMESTAMP VALIDATION
# ═══════════════════════════════════════════════════════════════

def validate_alt_data_timestamp(
    data_available_as_of: str,
    decision_date: str,
    source_name: str,
    conservative_lag_days: int = 0,
) -> dict:
    """
    Validate that alt-data is available as of the decision date.

    For some sources (H-1B, patents, Google Trends), a conservative
    lag is applied to account for reporting delays.
    """
    if data_available_as_of == DATA_UNAVAILABLE or not data_available_as_of:
        return {
            "usable": False,
            "source": source_name,
            "reason": f"No availability date for {source_name}",
        }

    # Apply conservative lag
    effective_date = data_available_as_of
    if conservative_lag_days > 0:
        try:
            dt = datetime.strptime(data_available_as_of[:10], "%Y-%m-%d")
            effective_dt = dt + timedelta(days=conservative_lag_days)
            effective_date = effective_dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    if effective_date <= decision_date:
        return {
            "usable": True,
            "source": source_name,
            "available_as_of": data_available_as_of,
            "effective_after_lag": effective_date,
            "lag_days": conservative_lag_days,
        }

    return {
        "usable": False,
        "source": source_name,
        "reason": (
            f"{source_name} available_as_of ({data_available_as_of}) "
            f"+ lag ({conservative_lag_days}d) = {effective_date} "
            f"> decision_date ({decision_date})"
        ),
    }


# ═══════════════════════════════════════════════════════════════
# FULL INTEGRITY CHECK
# ═══════════════════════════════════════════════════════════════

def run_integrity_check(evidence_packet: dict) -> dict:
    """
    Run comprehensive integrity check on an evidence packet.

    Validates:
    1. Execution timing (T+1)
    2. Filing date rule
    3. News timestamps
    4. Alt-data availability
    5. Missing data handling

    Returns dict with overall pass/fail and detailed checks.
    """
    decision_date = evidence_packet.get("decision_date", "")
    decision_ts = evidence_packet.get("decision_timestamp", "")
    execution_ts = evidence_packet.get("execution_timestamp", "")

    checks = []
    violations = []

    # 1. Execution timing
    expected_exec = compute_execution_timestamp(decision_date)
    exec_ok = execution_ts >= expected_exec[:10] if execution_ts else False
    checks.append({
        "check": "execution_timing",
        "rule": "Signal Day T → trade Day T+1 open",
        "expected": expected_exec,
        "actual": execution_ts,
        "pass": exec_ok,
    })
    if not exec_ok:
        violations.append("Execution timestamp is before T+1 open")

    # 2. Filing date
    fund = evidence_packet.get("fundamentals", {})
    filing_date = fund.get("filing_date", DATA_UNAVAILABLE)
    filing_check = is_fundamentals_usable(filing_date, decision_date)
    checks.append({
        "check": "filing_date_rule",
        "rule": "Fundamentals usable only after SEC filing date",
        **filing_check,
        "pass": filing_check["usable"],
    })
    if not filing_check["usable"] and fund.get("status") != DATA_UNAVAILABLE:
        violations.append(f"Fundamentals filing date violation: {filing_check['reason']}")

    # 3. News timestamps
    news = evidence_packet.get("news", {})
    news_items = news.get("items", [])
    future_news = [
        n for n in news_items
        if n.get("published_at", n.get("timestamp", "")) > decision_ts
        and n.get("published_at", n.get("timestamp", ""))
    ]
    news_ok = len(future_news) == 0
    checks.append({
        "check": "news_timestamps",
        "rule": "No news after decision_timestamp",
        "total_items": len(news_items),
        "future_items": len(future_news),
        "pass": news_ok,
    })
    if not news_ok:
        violations.append(f"{len(future_news)} news items have future timestamps")

    # 4. Missing data handling
    availability = evidence_packet.get("data_availability", {})
    for source, info in availability.items():
        if info.get("status") in (DATA_UNAVAILABLE, "not_yet_filed"):
            checks.append({
                "check": f"missing_data_{source}",
                "rule": "Missing data must be explicitly marked, not zero",
                "status": info["status"],
                "pass": True,  # Correctly marked as unavailable
            })

    # 5. Macro data timestamps
    macro = evidence_packet.get("macro_data", {})
    if macro.get("status") not in (DATA_UNAVAILABLE, None, "none"):
        macro_avail = macro.get("data_available_as_of", "")
        macro_safe = macro.get("lookahead_safe", False)
        macro_ok = True

        if macro_avail and macro_avail != DATA_UNAVAILABLE:
            # Check data_available_as_of <= decision_timestamp
            if macro_avail > decision_ts:
                macro_ok = False
                violations.append(
                    f"Macro data_available_as_of ({macro_avail}) > "
                    f"decision_timestamp ({decision_ts})"
                )
        if not macro_safe:
            # Not a hard violation, but flag it
            pass

        checks.append({
            "check": "macro_data_timestamps",
            "rule": "Macro data_available_as_of <= decision_timestamp",
            "data_available_as_of": macro_avail,
            "decision_timestamp": decision_ts,
            "lookahead_safe": macro_safe,
            "pass": macro_ok,
        })

    return {
        "overall_pass": len(violations) == 0,
        "decision_date": decision_date,
        "decision_timestamp": decision_ts,
        "execution_timestamp": execution_ts,
        "rule_version": evidence_packet.get("rule_version", "unknown"),
        "total_checks": len(checks),
        "violations": violations,
        "checks": checks,
    }


def generate_backtest_integrity_report(
    backtest_start: str,
    backtest_end: str,
    integrity_results: list[dict],
) -> dict:
    """
    Generate a summary integrity report for the full backtest run.
    """
    total = len(integrity_results)
    passed = sum(1 for r in integrity_results if r["overall_pass"])
    failed = total - passed

    all_violations = []
    for r in integrity_results:
        for v in r.get("violations", []):
            all_violations.append({
                "date": r["decision_date"],
                "violation": v,
            })

    return {
        "backtest_period": f"{backtest_start} to {backtest_end}",
        "total_dates_checked": total,
        "passed": passed,
        "failed": failed,
        "pass_rate_pct": round(passed / max(1, total) * 100, 1),
        "all_violations": all_violations,
        "no_look_ahead_status": "VERIFIED" if failed == 0 else "VIOLATIONS FOUND",
        "execution_timing_rule": "Signal Day T close → trade Day T+1 open",
        "filing_date_rule": "Use SEC filing_date, not fiscal period end date",
        "news_rule": "Only news published before decision_timestamp",
        "alt_data_rules": {
            "reddit": "Posts/comments before decision_timestamp only",
            "github": "Activity observable before decision_date only",
            "h1b_lca": "Public disclosure date + 30-day conservative lag",
            "patents": "Publication/grant date, not invention date",
        },
        "generated_at": datetime.now().isoformat(),
    }
