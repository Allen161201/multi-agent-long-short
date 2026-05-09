"""
8-ticker live-adapter coverage verify (Chunk-final, 2026-05-02).

For each of the 8 tickers that fired during the 5-day replay
(HTCO/AKAN/RDAC/AIOS/ARE/AVB/BRO/CINF), build the packet with the
full live_adapters tuple and print:
  - which sources delivered (manifest summary)
  - tier distribution (T1/T2/T3/T4)
  - block status for the 4 finalized blocks
  - top-level fields populated by the new finalizers

NO LLM is invoked. NO replay rerun. Pure packet-generation only.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from src.evidence_packet.generator import generate_evidence_packet  # noqa: E402

LIVE_ADAPTERS = (
    "wikipedia_pageviews",
    "sec_edgar",
    "github_public",
    "sec_8k_fulltext",
    "github_commit_messages",
    "sec_form4",
    "sec_13f",
    "polygon_news",
    "fmp_sentiment",
)

TICKERS = ["HTCO", "AKAN", "RDAC", "AIOS", "ARE", "AVB", "BRO", "CINF"]
# Mid-window timestamp inside the replay window. Wednesday 09:30 ET.
DECISION_TS = "2026-04-29T09:30:00-04:00"


def summarize(packet: dict, ticker: str) -> dict:
    manifest = packet.get("alt_data_manifest", {})
    calls = manifest.get("calls", [])

    delivered = []
    skipped = []
    failed = []
    for c in calls:
        sid = c.get("source_id")
        if not c.get("called"):
            skipped.append((sid, c.get("skip_reason", "?")))
            continue
        rows = c.get("returned_rows", 0)
        status = c.get("extraction_status")
        flag = c.get("source_flag")
        if rows > 0 and status in ("ok", "stub"):
            delivered.append((sid, rows, flag))
        else:
            failed.append((sid, status, c.get("error_class")))

    fc = packet.get("filing_confirmation") or {}
    so = packet.get("sentiment_community_ownership_evidence") or {}
    ii = packet.get("information_integrity_assessment") or {}
    ng = packet.get("narrative_price_gap_assessment") or {}

    return {
        "ticker": ticker,
        "delivered": delivered,
        "skipped": skipped,
        "failed": failed,
        "filing_confirmation_status": fc.get("status"),
        "filing_confirmation_score":  fc.get("filing_support_score"),
        "filing_confirmation_url":    fc.get("filing_url"),
        "sentiment_status":           so.get("status"),
        "sentiment_subblocks":        [
            k for k in ("market_sentiment", "community_size",
                        "ownership_positioning")
            if isinstance(so.get(k), dict)
            and so[k].get("status") == "ok"
        ],
        "integrity_status":           ii.get("status"),
        "integrity_tier_dist":        ii.get("source_tier_distribution"),
        "integrity_use_as_primary":   ii.get("use_as_primary_signal_allowed"),
        "narrative_evidence_count":   len(ng.get("evidence_used", [])),
    }


def main() -> int:
    print("=" * 88)
    print(f"8-ticker live-adapter coverage verify @ {DECISION_TS}")
    print("=" * 88)

    rows: list[dict] = []
    for t in TICKERS:
        try:
            packet = generate_evidence_packet(
                ticker=t,
                decision_timestamp=DECISION_TS,
                live_adapters=LIVE_ADAPTERS,
            )
            rows.append(summarize(packet, t))
        except Exception as e:
            print(f"  {t}: PACKET BUILD FAILED — {type(e).__name__}: {e}")
            rows.append({"ticker": t, "error": str(e)})

    # ── per-ticker detail ──
    for r in rows:
        if "error" in r:
            print(f"\n[{r['ticker']}] BUILD ERROR: {r['error']}")
            continue
        print(f"\n[{r['ticker']}]")
        print(f"  delivered   : {[(s, n, f) for s, n, f in r['delivered']]}")
        print(f"  failed      : {r['failed']}")
        print(f"  skipped     : {r['skipped']}")
        print(f"  filing_conf : status={r['filing_confirmation_status']:<8s} "
              f"score={r['filing_confirmation_score']} "
              f"url={'YES' if r['filing_confirmation_url'] else 'NO'}")
        print(f"  sentiment   : status={r['sentiment_status']:<14s} "
              f"sub={r['sentiment_subblocks']}")
        print(f"  integrity   : status={r['integrity_status']:<22s} "
              f"tiers={r['integrity_tier_dist']} "
              f"use_as_primary={r['integrity_use_as_primary']}")
        print(f"  narrative   : evidence_pointers={r['narrative_evidence_count']}")

    # ── coverage matrix ──
    print()
    print("=" * 100)
    print("Coverage matrix (delivered=Y / not-delivered=. / failed=F / skipped=S)")
    print("=" * 100)
    cols = list(LIVE_ADAPTERS)
    header = f"  {'ticker':6s}  " + "  ".join(f"{c:>22s}" for c in cols)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        if "error" in r:
            continue
        delivered_set = {s for s, _, _ in r["delivered"]}
        failed_set = {s for s, _, _ in r["failed"]}
        skipped_set = {s for s, _ in r["skipped"]}
        cells = []
        for c in cols:
            if c in delivered_set:
                cells.append(f"{'Y':>22s}")
            elif c in failed_set:
                cells.append(f"{'F':>22s}")
            elif c in skipped_set:
                cells.append(f"{'S':>22s}")
            else:
                cells.append(f"{'.':>22s}")
        print(f"  {r['ticker']:6s}  " + "  ".join(cells))

    # ── verdict per ticker ──
    print()
    print("=" * 88)
    print("Verdict — does this ticker now have enough evidence to overturn the "
          "8 misses?")
    print("=" * 88)
    for r in rows:
        if "error" in r:
            print(f"  {r['ticker']:6s}  ERROR")
            continue
        score = 0
        score += 2 if r["filing_confirmation_status"] == "ok" else 0
        score += 2 if r["sentiment_status"] == "ok" else 0
        score += 2 if r["integrity_status"] in ("ok",
                                                  "insufficient_evidence") else 0
        score += 1 if r["narrative_evidence_count"] >= 5 else 0
        verdict = (
            "STRONG"   if score >= 6 else
            "MODERATE" if score >= 4 else
            "WEAK"     if score >= 2 else
            "NONE"
        )
        print(f"  {r['ticker']:6s}  score={score}/7  verdict={verdict}  "
              f"(filing={r['filing_confirmation_status']}, "
              f"sentiment={r['sentiment_status']}, "
              f"integrity={r['integrity_status']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
