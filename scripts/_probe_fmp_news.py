"""Probe FMP news endpoints to understand date range and PIT behaviour."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.data_adapters import fmp_adapter as fmp


def main() -> int:
    for ticker in ("NVDA", "AAPL", "TSLA"):
        p = fmp.get_news_latest(ticker, limit=20)
        print(f"\n--- {ticker} via /news/stock-latest ---")
        print(f"  status     : {p.get('status')}")
        print(f"  row_count  : {p.get('row_count')}")
        items = p.get("items") or []
        if items:
            print("  first 5 dates:")
            for it in items[:5]:
                pd = it.get("publishedDate")
                pub = it.get("publisher")
                site = it.get("site")
                print(f"    {pd!r}  publisher={pub!r}  site={site!r}")
            print("  last 5 dates:")
            for it in items[-5:]:
                print(f"    {it.get('publishedDate')!r}")
            # Date range summary
            dates = [it.get("publishedDate") for it in items if it.get("publishedDate")]
            if dates:
                print(f"  date range: {min(dates)} .. {max(dates)}")
            # Distinct publishers
            pubs = {it.get("publisher") for it in items if it.get("publisher")}
            print(f"  distinct publishers ({len(pubs)}): {sorted(pubs)[:10]}")
        else:
            print("  NO ITEMS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
