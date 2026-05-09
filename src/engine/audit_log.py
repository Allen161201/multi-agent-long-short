"""
Audit Log — structured logging for all agent decisions.
Now stamps rule_version, evidence packet metadata, and data_mode.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from src.engine.evidence_packet import RULE_VERSION, AGENT_PROMPT_VERSION

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "outputs" / "audit_logs"


def ensure_output_dir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def write_audit_log(date: str, run_data: dict) -> str:
    """Write a full audit log for one orchestrator run."""
    ensure_output_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"audit_{date}_{timestamp}.json"
    filepath = OUTPUT_DIR / filename

    # Build evidence packet summary (don't store full raw data in log)
    evidence_summary = {}
    for ticker, packet in run_data.get("evidence_packets", {}).items():
        avail = packet.get("data_availability", {})
        evidence_summary[ticker] = {
            "decision_date": packet.get("decision_date"),
            "execution_timestamp": packet.get("execution_timestamp"),
            "data_mode": packet.get("data_mode"),
            "data_availability": {
                src: info.get("status", "unknown")
                for src, info in avail.items()
            },
        }

    # Build integrity summary
    integrity_summary = {}
    for ticker, result in run_data.get("integrity_results", {}).items():
        integrity_summary[ticker] = {
            "valid": result.get("valid", False),
            "violations": result.get("violations", []),
        }

    log_entry = {
        "run_timestamp": datetime.now().isoformat(),
        "run_date": date,
        "system": "alt_data_agentic_long_short",
        "rule_version": run_data.get("rule_version", RULE_VERSION),
        "agent_prompt_version": run_data.get("agent_prompt_version", AGENT_PROMPT_VERSION),
        "data_mode": run_data.get("data_mode", "mock"),
        "decision_timestamp": run_data.get("decision_timestamp"),
        "execution_timestamp": run_data.get("execution_timestamp"),
        "regime": run_data.get("regime"),
        "skip_alt_data": run_data.get("skip_alt_data", False),
        "evidence_summary": evidence_summary,
        "integrity_summary": integrity_summary,
        "allocation": run_data.get("allocation"),
        "decisions": run_data.get("decisions"),
        "summary": run_data.get("summary"),
        "agent_outputs": _sanitize_for_log(run_data.get("agent_outputs", {})),
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(log_entry, f, indent=2, default=str)

    return str(filepath)


def _sanitize_for_log(agent_outputs: dict) -> dict:
    """Remove large raw data from agent outputs for the log file."""
    sanitized = {}
    for agent_name, output in agent_outputs.items():
        if isinstance(output, dict):
            # Keep everything except very large nested structures
            sanitized[agent_name] = {
                k: v for k, v in output.items()
                if k not in ("all_gainers",)  # skip the full gainer list
            }
        else:
            sanitized[agent_name] = output
    return sanitized


def format_decisions_table(decisions: list[dict]) -> str:
    """Format decisions as a readable terminal table."""
    if not decisions:
        return "  No decisions generated."

    lines = []
    header = (f"  {'Ticker':<8} {'Type':<14} {'Decision':<12} "
              f"{'Confidence':<12} {'Position':>12}  Reason")
    lines.append(header)
    lines.append("  " + "─" * 100)

    for d in decisions:
        pos = f"${d.get('position_size', 0):,.0f}" if d.get("position_size", 0) > 0 else "—"
        reason = d.get("reason", "")
        if len(reason) > 50:
            reason = reason[:47] + "..."
        lines.append(
            f"  {d['ticker']:<8} {d.get('candidate_type', ''):<14} "
            f"{d['decision']:<12} {d.get('confidence', 'n/a'):<12} {pos:>12}  {reason}"
        )

    return "\n".join(lines)
