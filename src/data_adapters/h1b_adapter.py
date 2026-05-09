"""
H-1B / LCA Adapter — interface for technical hiring intensity data.
"""
import os
from src.data_adapters.mock_loader import load_h1b_data


def get_h1b_data(ticker: str) -> dict:
    """Fetch H-1B/LCA hiring data for a ticker."""
    if os.environ.get("USE_MOCK_DATA", "true").lower() != "true":
        # Future: parse DOL public CSV downloads
        pass
    return load_h1b_data(ticker)
