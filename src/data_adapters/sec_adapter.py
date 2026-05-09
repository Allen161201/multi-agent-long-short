"""
SEC EDGAR Adapter — interface for filing text data.
"""
import os
from src.data_adapters.mock_loader import load_sec_data


def get_sec_filings(ticker: str) -> dict:
    """Fetch SEC filing analysis for a ticker."""
    user_agent = os.environ.get("SEC_USER_AGENT", "")
    if user_agent and os.environ.get("USE_MOCK_DATA", "true").lower() != "true":
        # Future: call EDGAR full-text search API
        pass
    return load_sec_data(ticker)
