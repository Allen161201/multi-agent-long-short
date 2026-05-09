"""
GitHub Adapter — interface for developer ecosystem data.
"""
import os
from src.data_adapters.mock_loader import load_github_data


def get_github_data(ticker: str) -> dict:
    """Fetch GitHub developer ecosystem data for a ticker."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token and os.environ.get("USE_MOCK_DATA", "true").lower() != "true":
        # Future: call GitHub API
        pass
    return load_github_data(ticker)
