"""
CLI entrypoint for the v1 evidence packet generator.

Usage:
    python scripts/generate_evidence_packet.py --ticker AAPL
    python scripts/generate_evidence_packet.py --ticker AAPL --decision-mode live
    python scripts/generate_evidence_packet.py --ticker AAPL --decision-sub-mode opening_window

The generator only supports `--decision-mode live` today; replay raises
NotImplementedError. Output is written to:
    outputs/evidence_packets/<TICKER>_<UTC_TIMESTAMP>.json
and a human-readable summary is printed to stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

# Load .env without printing secrets
ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    import os
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from evidence_packet import generate_evidence_packet  # noqa: E402

OUT_DIR = ROOT / "outputs" / "evidence_packets"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _summary_lines(packet: dict) -> list[str]:
    env = packet["envelope"]
    dtd = packet["decision_time_discipline"]
    price = packet["price_snapshot"]
    macro = packet["macro_regime"]
    fund = packet["fundamental_snapshot"]
    val = packet["valuation_snapshot"]
    news = packet["news_event_summary"]
    cal = packet["corporate_calendar"]
    qf = packet["data_quality_flags"]
    bt = packet.get("build_telemetry", {})

    lines = []
    lines.append(f"Ticker:                {env['ticker']}")
    lines.append(f"Schema version:        {env['schema_version']}")
    lines.append(f"Generator version:     {env['generator_version']}")
    lines.append(f"Decision mode:         {env['decision_mode']} ({env['decision_sub_mode']})")
    lines.append(f"Schema decision_mode:  {dtd['decision_mode']}")
    lines.append(f"Decision timestamp:    {env['decision_timestamp']}")
    lines.append(f"Allowed data cutoff:   {env['allowed_data_cutoff']}")
    lines.append(f"Analysis run time UTC: {env['analysis_run_time_utc']}")
    lines.append(f"Lookahead safe:        {env['lookahead_safe']}")
    lines.append(f"Data after cutoff:     {env['data_after_cutoff_used']}")
    lines.append(f"Evidence packet hash:  {env['evidence_packet_hash']}")
    lines.append(f"Locked decision id:    {env['locked_decision_id']}")
    lines.append(f"Immutable:             {env['immutable_decision_flag']}")
    lines.append("")
    lines.append("─── block status ─────────────────────────────────────")
    block_keys = ("price_snapshot", "macro_regime", "fundamental_snapshot",
                   "valuation_snapshot", "news_event_summary",
                   "filing_confirmation", "corporate_calendar",
                   "alternative_data_features",
                   "information_integrity_assessment",
                   "sentiment_community_ownership_evidence",
                   "narrative_price_gap_assessment")
    for k in block_keys:
        b = packet.get(k, {})
        lines.append(f"  {k:46s}  {b.get('status','?')}")
    lines.append("")
    lines.append("─── price snapshot summary ──────────────────────────")
    lines.append(f"  last_eod_close:    {price.get('last_eod_close')}")
    lines.append(f"  last_eod_date:     {price.get('last_eod_date')}")
    lines.append(f"  return_5d_pct:     {price.get('return_5d_pct')}")
    lines.append(f"  return_20d_pct:    {price.get('return_20d_pct')}")
    lines.append(f"  return_60d_pct:    {price.get('return_60d_pct')}")
    lines.append(f"  rel_vol_vs_20d:    {price.get('relative_volume_vs_20d')}")
    lines.append(f"  last_quote_post_cutoff: {price.get('last_quote_after_cutoff')}")
    lines.append("")
    lines.append("─── macro regime ─────────────────────────────────────")
    lines.append(f"  regime:            {macro.get('macro_regime')}")
    lines.append(f"  stress_score:      {macro.get('stress_score')}")
    lines.append(f"  equity_discipline: {macro.get('equity_discipline')}")
    lines.append(f"  source:            {macro.get('source')}")
    lines.append("")
    lines.append("─── fundamentals ─────────────────────────────────────")
    fw = fund.get("filed_window_used", {}) or {}
    inc = fw.get("income_statement") or {}
    lines.append(f"  status:            {fund.get('status')}")
    lines.append(f"  PIT income:        {inc.get('fiscal_year')} {inc.get('period')} "
                  f"(accepted {inc.get('accepted_date')})")
    sq = fund.get("snapshot_quarter", {}) or {}
    lines.append(f"  revenue:           {sq.get('revenue')}")
    lines.append(f"  net_income:        {sq.get('net_income')}")
    lines.append("")
    lines.append("─── valuation TTM (display) ──────────────────────────")
    vi = val.get("valuation_inputs", {}) or {}
    lines.append(f"  PE / PB / EV-EBITDA: {vi.get('pe_ratio')} / {vi.get('price_to_book')} / {vi.get('ev_to_ebitda')}")
    lines.append(f"  DCF gap (display):   {val.get('DCF_gap_display_only')}")
    lines.append("")
    lines.append("─── news ─────────────────────────────────────────────")
    lines.append(f"  items_count:           {news.get('items_count')}")
    lines.append(f"  items_dropped_post_cutoff: {news.get('items_dropped_post_cutoff')}")
    if news.get("items"):
        latest = news["items"][0]
        lines.append(f"  latest:               {latest.get('published_at')} | {latest.get('publisher')}")
        title = latest.get("title") or ""
        lines.append(f"     {title[:90]}")
    lines.append("")
    lines.append("─── calendar ─────────────────────────────────────────")
    lines.append(f"  events_near_decision: {cal.get('events_near_decision_count')}")
    lines.append("")
    lines.append("─── api calls + flags ────────────────────────────────")
    lines.append(f"  api_calls_total_for_packet: {bt.get('api_calls_total_for_packet')}")
    lines.append(f"  per-block: {bt.get('api_calls_per_block')}")
    lines.append(f"  data_quality_flags: {len(qf)}")
    for f in qf[:5]:
        lines.append(f"    [{f.get('severity')}] {f.get('kind')}: {f.get('detail','')[:90]}")
    if len(qf) > 5:
        lines.append(f"    ... and {len(qf) - 5} more")
    return lines


def main():
    parser = argparse.ArgumentParser(description="Generate a v1 evidence packet for one ticker (live mode).")
    parser.add_argument("--ticker", required=True, help="Ticker symbol, e.g. AAPL")
    parser.add_argument("--decision-mode", default="live",
                          help="Decision mode (only 'live' supported in v1)")
    parser.add_argument("--decision-sub-mode", default=None,
                          choices=("pre_market", "opening_window",
                                    "end_of_day", "intraday_review"),
                          help="Optional override; auto-detected from US/Eastern wall clock if omitted")
    parser.add_argument("--decision-timestamp", default=None,
                          help="Optional ISO-8601 timestamp override (live mode usually leaves this unset)")
    parser.add_argument("--no-write", action="store_true",
                          help="Print summary but do not write JSON file")
    args = parser.parse_args()

    packet = generate_evidence_packet(
        ticker=args.ticker,
        decision_mode=args.decision_mode,
        decision_timestamp=args.decision_timestamp,
        decision_sub_mode=args.decision_sub_mode,
    )

    if not args.no_write:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = OUT_DIR / f"{packet['envelope']['ticker']}_{ts}.json"
        out_path.write_text(json.dumps(packet, indent=2, default=str))
        print(f"Wrote {out_path}")
        print()

    for line in _summary_lines(packet):
        print(line)


if __name__ == "__main__":
    main()
