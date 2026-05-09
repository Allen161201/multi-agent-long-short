"""
D5 Task 1.2 — NDI live LLM frame-extraction verification.

Picks tickers that should have ≥2 distinct news sources within a 24h
window before a chosen cutoff. Generates the evidence packet (live FMP
news), runs the runner's _maybe_patch_ndi under live AnthropicProvider
(claude-haiku-4-5), and asserts:

  - mode == "computed"
  - n_sources >= 2
  - score is float in [0.0, 1.0]
  - extracted_frames has >= 2 entries
  - rationale is non-empty
  - PIT: every news_item used has published_at_utc < cutoff

If 3 candidate tickers all hit no_news, the script STOPs and prints
the FMP news adapter behavior so the user can decide whether the
adapter has a bug.

Cost: ~$0.05-$0.40 depending on news count (frame extraction + pairwise
divergence calls).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

os.environ["LLM_PROVIDER"] = "anthropic"

HAIKU_MODEL = "claude-haiku-4-5-20251001"
TIMEOUT_S = 300
# Tickers known to have heavy news coverage. We try them in order; first
# one that produces ≥2 sources within the 24h window wins. Cutoffs are
# chosen so the 24h pre-cutoff window includes weekday news cycles.
CANDIDATES = [
    ("NVDA", "2026-04-29T16:15:00-04:00"),
    ("AAPL", "2026-04-30T16:15:00-04:00"),
    ("TSLA", "2026-04-30T16:15:00-04:00"),
    ("META", "2026-04-30T16:15:00-04:00"),
    ("MSFT", "2026-04-30T16:15:00-04:00"),
]


def _parse_iso(ts) -> datetime | None:
    if not ts:
        return None
    s = str(ts).strip()
    try:
        if " " in s and "T" not in s:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ABORT  ANTHROPIC_API_KEY not set")
        return 2

    from src.evidence_packet import generate_evidence_packet
    from src.altdata.ndi import compute_ndi
    from src.llm.anthropic_provider import AnthropicProvider, get_cost_ledger

    ledger = get_cost_ledger()
    ledger.reset()

    provider = AnthropicProvider(default_model=HAIKU_MODEL, timeout_s=TIMEOUT_S)

    print("=== D5 Task 1.2 — NDI live LLM frame-extraction verify ===")
    print(f"  model={HAIKU_MODEL}")

    no_news_tickers: list[tuple[str, str, int]] = []
    chosen_idx = None

    for idx, (ticker, cutoff) in enumerate(CANDIDATES):
        print(f"\n  [{idx+1}/{len(CANDIDATES)}] Trying {ticker} @ {cutoff} ...")
        try:
            packet = generate_evidence_packet(
                ticker=ticker, decision_timestamp=cutoff, strict_pit_mode=True,
            )
        except Exception as e:
            print(f"    skip: {type(e).__name__}: {e}")
            continue

        news_block = packet.get("news_event_summary") or {}
        items = news_block.get("items") or []
        print(f"    news_block.items count: {len(items)}")

        # Probe with stub provider first to find n_sources without paying $.
        from src.llm.deterministic_stub import DeterministicStubProvider
        stub = DeterministicStubProvider()
        anchor = _parse_iso(cutoff)
        probe = compute_ndi(
            items, event_cluster_id=f"{ticker}:1d",
            decision_timestamp_utc=anchor, window_hours=24, provider=stub,
        )
        print(f"    probe (stub): mode={probe.get('mode')} "
              f"n_sources={probe.get('n_sources')} "
              f"n_items_considered={probe.get('n_items_considered')}")
        if (probe.get("n_sources") or 0) < 2:
            no_news_tickers.append((ticker, cutoff, len(items)))
            continue

        # Live LLM call — frame extraction + pairwise divergence
        print(f"    >> running live NDI compute on Haiku...")
        live = compute_ndi(
            items, event_cluster_id=f"{ticker}:1d",
            decision_timestamp_utc=anchor, window_hours=24, provider=provider,
        )
        cost = ledger.total_usd()
        print(f"    live result: mode={live.get('mode')} "
              f"score={live.get('score')} "
              f"n_sources={live.get('n_sources')} "
              f"frames={len(live.get('extracted_frames') or [])} "
              f"cost=${cost:.5f}")

        # ── Assertions per Task 1.2 spec ────────────────────────────
        ok = True

        def _check(label, cond):
            nonlocal ok
            marker = "PASS" if cond else "FAIL"
            print(f"      [{marker}] {label}")
            if not cond:
                ok = False

        _check('mode == "computed"', live.get("mode") == "computed")
        _check("n_sources >= 2", (live.get("n_sources") or 0) >= 2)
        score = live.get("score")
        _check("score is float", isinstance(score, float))
        _check("score in [0.0, 1.0]",
               isinstance(score, float) and 0.0 <= score <= 1.0)
        frames = live.get("extracted_frames") or []
        _check("extracted_frames has >= 2", len(frames) >= 2)
        _check("rationale non-empty",
               bool((live.get("rationale") or "").strip()))

        # PIT — every news item used must have timestamp < cutoff
        any_lookahead = False
        for it in items:
            pub = _parse_iso(it.get("published_at_utc")
                             or it.get("published_at"))
            if pub and pub > anchor:
                any_lookahead = True
                print(f"      LOOKAHEAD ITEM: {it.get('title', '')[:80]} "
                      f"@ {it.get('published_at_utc')}")
        _check("no news_item with timestamp > cutoff (PIT)",
               not any_lookahead)

        if ok:
            print(f"\n  ✓ {ticker} verified live NDI compute. Cost ${cost:.5f}")
            chosen_idx = idx
            # Persist
            out = ROOT / "data" / "altdata" / "_ndi_live_verify_result.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "ticker": ticker, "cutoff": cutoff, "live_result": live,
                "n_news_items": len(items), "cost_usd": cost,
            }, indent=2, default=str), encoding="utf-8")
            print(f"  saved: {out}")
            return 0
        else:
            print(f"  {ticker}: live compute happened but assertions failed.")
            chosen_idx = idx
            return 1

    # If we got here, all CANDIDATES had <2 sources
    print("\n  ALL CANDIDATES hit no_news / insufficient_sources.")
    print("  Per-ticker probe results:")
    for t, c, n in no_news_tickers:
        print(f"    {t} @ {c}: {n} news items in packet, <2 distinct sources in 24h")
    print("\n  This may indicate:")
    print("    - FMP /news/stock-latest only returns very recent items")
    print("    - The 24h pre-cutoff window genuinely has no diverse coverage "
          "for these tickers on the chosen dates")
    print("    - Adapter bug: items have publisher field empty → all collapse "
          "to one source_id")
    return 1


if __name__ == "__main__":
    import traceback
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(99)
