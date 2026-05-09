"""
Backtest / Replay — comparison of decisions WITH vs WITHOUT Alt-Data Agent.
Now includes: backtest integrity validation, T+1 execution timing, rule_version stamping.
"""
import json
from pathlib import Path
from datetime import datetime

from src.engine.orchestrator import run_pipeline
from src.engine.evidence_packet import RULE_VERSION
from src.engine.backtest_integrity import (
    run_integrity_check, generate_backtest_integrity_report,
)
from src.data_adapters.mock_loader import get_available_dates


REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "outputs" / "reports"

# Backtest window configuration (from frozen rules)
PREFERRED_START = "2019-01-01"
MINIMUM_START = "2020-01-01"
BACKTEST_END = "latest"


def run_backtest(dates: list[str] | None = None, regime: str = "weakening") -> dict:
    """
    Run backtest across sample dates, comparing:
    - Full system (with Alt-Data Agent)
    - Baseline (without Alt-Data Agent)

    Validates integrity for every decision.
    Returns comparison + integrity report.
    """
    if dates is None:
        dates = get_available_dates()

    results_with_alt = []
    results_without_alt = []
    all_integrity_results = []

    for date in dates:
        # Full system run
        full = run_pipeline(date, regime=regime, skip_alt_data=False)
        results_with_alt.append(full)

        # Baseline run (skip alt data)
        baseline = run_pipeline(date, regime=regime, skip_alt_data=True)
        results_without_alt.append(baseline)

        # Collect integrity results
        for ticker, packet in full.get("evidence_packets", {}).items():
            integrity = run_integrity_check(packet)
            all_integrity_results.append(integrity)

    # Compare decisions
    comparison = compare_results(results_with_alt, results_without_alt)

    # Add integrity report
    start = dates[0] if dates else PREFERRED_START
    end = dates[-1] if dates else "latest"
    comparison["integrity_report"] = generate_backtest_integrity_report(
        start, end, all_integrity_results,
    )
    comparison["rule_version"] = RULE_VERSION
    comparison["backtest_config"] = {
        "preferred_start": PREFERRED_START,
        "minimum_start": MINIMUM_START,
        "actual_start": start,
        "actual_end": end,
        "execution_timing": "Signal Day T close -> trade Day T+1 open",
        "filing_date_rule": "Use SEC filing_date, not fiscal period end date",
        "regime_used": regime,
    }

    # Save report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"backtest_comparison_{ts}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, default=str)

    return comparison


def compare_results(with_alt: list[dict], without_alt: list[dict]) -> dict:
    """Compare full system vs baseline decisions."""
    comparison = {
        "timestamp": datetime.now().isoformat(),
        "dates_analyzed": [r["date"] for r in with_alt],
        "with_alt_data": {"total_decisions": 0, "shorts": 0, "buys": 0, "watches": 0, "no_trades": 0, "vetoes": 0},
        "without_alt_data": {"total_decisions": 0, "shorts": 0, "buys": 0, "watches": 0, "no_trades": 0, "vetoes": 0},
        "decision_changes": [],
        "confidence_changes": [],
        "quality_improvement": {},
    }

    for full, baseline in zip(with_alt, without_alt):
        date = full["date"]

        # Aggregate counts
        for key, result_list in [("with_alt_data", [full]), ("without_alt_data", [baseline])]:
            for r in result_list:
                s = r["summary"]
                comparison[key]["total_decisions"] += s["total_candidates"]
                comparison[key]["shorts"] += s["short"]
                comparison[key]["buys"] += s["buy"]
                comparison[key]["watches"] += s["watch"]
                comparison[key]["no_trades"] += s["no_trade"]
                comparison[key]["vetoes"] += s["veto"]

        # Find decision changes
        full_decisions = {d["ticker"]: d for d in full["decisions"]}
        baseline_decisions = {d["ticker"]: d for d in baseline["decisions"]}

        for ticker in set(list(full_decisions.keys()) + list(baseline_decisions.keys())):
            fd = full_decisions.get(ticker, {})
            bd = baseline_decisions.get(ticker, {})

            # Decision change
            if fd.get("decision") != bd.get("decision"):
                comparison["decision_changes"].append({
                    "date": date,
                    "ticker": ticker,
                    "candidate_type": fd.get("candidate_type", bd.get("candidate_type", "")),
                    "with_alt_data": fd.get("decision", "N/A"),
                    "without_alt_data": bd.get("decision", "N/A"),
                    "with_alt_reason": fd.get("reason", ""),
                    "without_alt_reason": bd.get("reason", ""),
                })

            # Confidence change
            if fd.get("confidence") != bd.get("confidence"):
                comparison["confidence_changes"].append({
                    "date": date,
                    "ticker": ticker,
                    "with_alt_confidence": fd.get("confidence", "N/A"),
                    "without_alt_confidence": bd.get("confidence", "N/A"),
                })

    # Calculate quality improvement
    total_changes = len(comparison["decision_changes"])
    shorts_prevented = sum(
        1 for c in comparison["decision_changes"]
        if c["without_alt_data"] == "short" and c["with_alt_data"] in ("no_trade", "watch")
    )
    shorts_added = sum(
        1 for c in comparison["decision_changes"]
        if c["with_alt_data"] == "short" and c["without_alt_data"] in ("no_trade", "watch")
    )
    upgraded_to_watch = sum(
        1 for c in comparison["decision_changes"]
        if c["without_alt_data"] == "no_trade" and c["with_alt_data"] == "watch"
    )
    watch_to_buy = sum(
        1 for c in comparison["decision_changes"]
        if c["without_alt_data"] == "watch" and c["with_alt_data"] == "buy"
    )
    buy_to_watch = sum(
        1 for c in comparison["decision_changes"]
        if c["without_alt_data"] == "buy" and c["with_alt_data"] == "watch"
    )

    comparison["quality_improvement"] = {
        "total_decision_changes": total_changes,
        "total_confidence_changes": len(comparison["confidence_changes"]),
        "false_shorts_prevented": shorts_prevented,
        "new_shorts_identified": shorts_added,
        "upgraded_to_watch": upgraded_to_watch,
        "watch_to_buy_upgrades": watch_to_buy,
        "buy_to_watch_downgrades": buy_to_watch,
        "alt_data_impact_pct": round(total_changes / max(1, comparison["with_alt_data"]["total_decisions"]) * 100, 1),
    }

    return comparison


def print_backtest_report(comparison: dict):
    """Print a clean backtest comparison report."""
    print("\n" + "=" * 80)
    print("  BACKTEST COMPARISON: With vs Without Alt-Data Agent")
    print(f"  Rule Version: {comparison.get('rule_version', 'unknown')}")
    print("=" * 80)
    print(f"  Dates analyzed: {', '.join(comparison['dates_analyzed'])}")

    # Backtest config
    config = comparison.get("backtest_config", {})
    if config:
        print(f"  Period: {config.get('actual_start', '?')} to {config.get('actual_end', '?')}")
        print(f"  Execution: {config.get('execution_timing', 'N/A')}")

    w = comparison["with_alt_data"]
    wo = comparison["without_alt_data"]

    print(f"\n  {'Metric':<30} {'With Alt-Data':>15} {'Without Alt-Data':>18}")
    print(f"  {'─' * 65}")
    print(f"  {'Total Decisions':<30} {w['total_decisions']:>15} {wo['total_decisions']:>18}")
    print(f"  {'Shorts':<30} {w['shorts']:>15} {wo['shorts']:>18}")
    print(f"  {'Buys':<30} {w['buys']:>15} {wo['buys']:>18}")
    print(f"  {'Watches':<30} {w['watches']:>15} {wo['watches']:>18}")
    print(f"  {'No-Trades':<30} {w['no_trades']:>15} {wo['no_trades']:>18}")
    print(f"  {'Vetoes':<30} {w['vetoes']:>15} {wo['vetoes']:>18}")

    qi = comparison["quality_improvement"]
    print(f"\n  ── Quality Impact ──")
    print(f"  Decision changes:         {qi['total_decision_changes']}")
    print(f"  Confidence changes:       {qi['total_confidence_changes']}")
    print(f"  False shorts prevented:   {qi['false_shorts_prevented']}")
    print(f"  New shorts identified:    {qi['new_shorts_identified']}")
    print(f"  Upgraded to watch:        {qi['upgraded_to_watch']}")
    print(f"  Watch->Buy upgrades:      {qi['watch_to_buy_upgrades']}")
    print(f"  Buy->Watch downgrades:    {qi['buy_to_watch_downgrades']}")
    print(f"  Alt-data impact:          {qi['alt_data_impact_pct']}% of decisions changed")

    if comparison["decision_changes"]:
        print(f"\n  ── Decision Changes ──")
        print(f"  {'Date':<12} {'Ticker':<8} {'Type':<14} {'With Alt-Data':<15} {'Without Alt-Data':<18}")
        print(f"  {'─' * 70}")
        for c in comparison["decision_changes"]:
            print(f"  {c['date']:<12} {c['ticker']:<8} {c['candidate_type']:<14} "
                  f"{c['with_alt_data']:<15} {c['without_alt_data']:<18}")

    # Integrity report
    integrity = comparison.get("integrity_report", {})
    if integrity:
        print(f"\n  ── Backtest Integrity ──")
        print(f"  No-look-ahead status: {integrity.get('no_look_ahead_status', 'unknown')}")
        print(f"  Dates checked: {integrity.get('total_dates_checked', 0)}")
        print(f"  Pass rate: {integrity.get('pass_rate_pct', 0)}%")
        violations = integrity.get("all_violations", [])
        if violations:
            print(f"  Violations ({len(violations)}):")
            for v in violations[:5]:
                print(f"    [!] {v['date']}: {v['violation']}")

    print("=" * 80 + "\n")


def main():
    """Run backtest and print report."""
    comparison = run_backtest()
    print_backtest_report(comparison)


if __name__ == "__main__":
    main()
