"""
Reddit Adapter — interface for Reddit discussion/attention data.
"""
import os
from src.data_adapters.mock_loader import load_reddit_data


def get_reddit_data(ticker: str) -> dict:
    """Fetch Reddit discussion data for a ticker."""
    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    if client_id and os.environ.get("USE_MOCK_DATA", "true").lower() != "true":
        # Future: call Reddit API (PRAW)
        pass
    return load_reddit_data(ticker)
