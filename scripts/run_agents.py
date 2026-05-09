"""
CLI: generate an evidence packet for a ticker, run an agent
configuration against it, print a one-page summary, save the full result.

Three orthogonal axes (D1 Step A2 / A3 / A4):
    --agent-mode       multi (default) or solo (single-agent baseline)
    --topology         pipeline (default) or flat (parallel ensemble)
                       — ignored when --agent-mode=solo
    --enabled-blocks   comma-separated block ids, default all enabled
                       — see docs/EVIDENCE_PACKET_BLOCK_IDS.md

Examples:
    # Original baseline regression (multi/pipeline/all blocks):
    python scripts/run_agents.py --ticker AAPL --candidate-type quality_long

    # Single-agent baseline (solo):
    python scripts/run_agents.py --ticker AAPL --candidate-type quality_long --agent-mode solo

    # Flat ensemble topology:
    python scripts/run_agents.py --ticker NVDA --candidate-type surge_short --topology flat

    # Text-only data-stream ablation:
    python scripts/run_agents.py --ticker UBER --candidate-type quality_long \\
        --enabled-blocks news_event_summary,filing_confirmation,narrative_price_gap_assessment,decision_time_discipline

    # Cross of the above (flat + text-only):
    python scripts/run_agents.py --ticker UBER --candidate-type quality_long \\
        --topology flat \\
        --enabled-blocks news_event_summary,filing_confirmation,narrative_price_gap_assessment,decision_time_discipline

Step 5: provider defaults to deterministic_stub via env var
LLM_PROVIDER (set in .env or shell). NO real LLM calls.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Make src/ importable when run from repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Load .env so LLM_PROVIDER / FMP_API_KEY / FRED_API_KEY are present.
ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from evidence_packet import generate_evidence_packet  # noqa: E402
from src.agents.runner import run_all_agents_for_candidate  # noqa: E402
from src.llm.cache import LLMCache  # noqa: E402
from src.llm.factory import get_provider  # noqa: E402

ET = ZoneInfo("America/New_York")
OUT_DIR = ROOT / "outputs" / "agent_runs"


def _format_short(parsed: dict | None, agent_name: str) -> str:
    """One-line agent summary for the console table."""
    if parsed is None:
        return f"  {agent_name:18s}  (no output)"
    # Pull the per-agent decision-ish field name we care about most.
    for fld in (
        "recommended_action", "decision", "recommendation_to_pm",
        "decision_hint", "value_creation_assessment",
        "decision_or_assessment",
    ):
        if fld in parsed:
            v = parsed[fld]
            break
    else:
        v = "?"
    conf = parsed.get("confidence", "?")
    val = parsed.get("validation_status", "ok")
    short_val = "" if val == "ok" else f"  ⚠ {val}"
    return f"  {agent_name:18s}  decision={v:25s}  confidence={conf:6s}{short_val}"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ticker", required=True, type=str)
    p.add_argument(
        "--candidate-type",
        required=True,
        choices=("surge_short", "quality_long"),
    )
    p.add_argument(
        "--agent-mode",
        choices=("multi", "solo"),
        default="multi",
        help=(
            "multi (default) runs the 4-specialist + PM configuration "
            "selected by --topology; solo runs the single-agent "
            "baseline_solo only and ignores --topology."
        ),
    )
    p.add_argument(
        "--topology",
        choices=("pipeline", "flat"),
        default="pipeline",
        help=(
            "pipeline (default) is the original sequential flow; flat "
            "runs the 4 specialists in parallel against the raw packet, "
            "then aggregates with pm_flat. Ignored when "
            "--agent-mode=solo."
        ),
    )
    p.add_argument(
        "--enabled-blocks",
        type=str,
        default=None,
        help=(
            "Comma-separated block ids to enable. Default = all 12 "
            "enabled (byte-identical to the pre-Step-A3 generator). "
            "See docs/EVIDENCE_PACKET_BLOCK_IDS.md for the canonical "
            "block-id list."
        ),
    )
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="Bypass the LLM cache for this run.",
    )
    p.add_argument(
        "--decision-timestamp",
        type=str,
        default=None,
        help="ISO ET timestamp; default = now-30min (cache-friendly).",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt="%H:%M:%S",
    )

    ticker = args.ticker.strip().upper()
    if args.decision_timestamp:
        dt = datetime.fromisoformat(args.decision_timestamp)
    else:
        dt = (datetime.now(ET).replace(microsecond=0) - timedelta(minutes=30))
    decision_ts = dt.isoformat()

    enabled_blocks_set = None
    if args.enabled_blocks:
        enabled_blocks_set = {
            tok.strip()
            for tok in args.enabled_blocks.split(",")
            if tok.strip()
        }

    print(f"\n{'='*72}")
    print(f"  Agent run  ticker={ticker}  candidate={args.candidate_type}")
    print(f"  agent_mode={args.agent_mode}  topology={args.topology}")
    print(f"  decision_timestamp={decision_ts}")
    eb_repr = (
        sorted(enabled_blocks_set) if enabled_blocks_set is not None
        else "<all 12 enabled (default)>"
    )
    print(f"  enabled_blocks={eb_repr}")
    print(f"  provider env LLM_PROVIDER={os.environ.get('LLM_PROVIDER', '(unset)')}")
    print(f"{'='*72}\n")

    print(f"[1/3] Generating evidence packet (live mode) ...")
    packet = generate_evidence_packet(
        ticker=ticker, decision_mode="live", decision_timestamp=decision_ts,
        enabled_blocks=enabled_blocks_set,
    )
    env = packet.get("envelope", {})
    print(f"      packet_hash    = {env.get('evidence_packet_hash')}")
    print(f"      lookahead_safe = {env.get('lookahead_safe')}  "
          f"hindsight_safe = {env.get('hindsight_safe')}")
    if "enabled_blocks" in env:
        print(f"      enabled_blocks = {env['enabled_blocks']}")

    print(f"\n[2/3] Running agents "
          f"(mode={args.agent_mode}, topology={args.topology}) ...")
    provider = get_provider()
    cache = LLMCache()
    result = run_all_agents_for_candidate(
        evidence_packet=packet,
        candidate_type=args.candidate_type,
        provider=provider,
        cache=cache,
        force_refresh=args.force_refresh,
        agent_mode=args.agent_mode,
        topology=args.topology,
    )

    print()
    print(f"  agent              decision / verdict / hint                    confidence")
    print(f"  {'-'*70}")
    for name, parsed in result["agent_outputs"].items():
        print(_format_short(parsed, name))

    summ = result["cache_summary"]
    print()
    print(f"  cache_summary     hits={summ['hits']}  misses={summ['misses']}  "
          f"per_agent={summ['per_agent']}")
    print(f"  provider          {result['provider']}")

    print(f"\n[3/3] Saving full result ...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    utc_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Filename includes the agent_mode + topology so grep/glob over the
    # outputs directory can isolate one cell of the ablation matrix.
    mode_tag = (
        "solo" if args.agent_mode == "solo"
        else f"{args.agent_mode}_{args.topology}"
    )
    blocks_tag = "all" if enabled_blocks_set is None else (
        f"sub{len(enabled_blocks_set)}"
    )
    out_path = OUT_DIR / (
        f"{ticker}_{args.candidate_type}_{mode_tag}_{blocks_tag}_{utc_tag}.json"
    )
    full = {
        "ticker": ticker,
        "candidate_type": args.candidate_type,
        "agent_mode": args.agent_mode,
        "topology": args.topology if args.agent_mode != "solo" else "solo",
        "enabled_blocks": (sorted(enabled_blocks_set)
                            if enabled_blocks_set is not None else None),
        "decision_timestamp": decision_ts,
        "force_refresh": bool(args.force_refresh),
        "envelope": env,
        "agent_run_result": result,
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2, default=str)
    print(f"      saved: {out_path}")

    final = result.get("final_decision") or {}
    print(f"\n  FINAL DECISION (Risk/PM):")
    print(f"     decision        = {final.get('decision')}")
    print(f"     position_size%  = {final.get('position_size_pct')}")
    reason = final.get("reason") or ""
    print(f"     reason          = {reason[:120]}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
