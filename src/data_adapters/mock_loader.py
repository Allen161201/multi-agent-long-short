"""
Mock Data Loader — loads all sample JSON data files.
Used when USE_MOCK_DATA=true or when API keys are not available.
"""
import json
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sample"


def _load_json(filename: str) -> dict | list:
    with open(SAMPLE_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def load_top_gainers(date: str | None = None) -> list[dict]:
    """Load top gainers. If date given, filter to that date."""
    data = _load_json("top_gainers.json")
    if date:
        for entry in data:
            if entry["date"] == date:
                return entry["gainers"]
        return []
    # Return all dates
    return data


def load_fundamentals(ticker: str | None = None) -> dict:
    """Load fundamental data. If ticker given, return that ticker's data."""
    data = _load_json("fundamentals.json")
    if ticker:
        return data.get(ticker, {})
    return data


def load_news_events(ticker: str | None = None) -> dict | list:
    """Load news/event data."""
    data = _load_json("news_events.json")
    if ticker:
        return data.get(ticker, [])
    return data


def load_reddit_data(ticker: str | None = None) -> dict:
    """Load Reddit alternative data."""
    data = _load_json("alt_data_reddit.json")
    if ticker:
        return data.get(ticker, {})
    return data


def load_github_data(ticker: str | None = None) -> dict:
    """Load GitHub alternative data."""
    data = _load_json("alt_data_github.json")
    if ticker:
        return data.get(ticker, {})
    return data


def load_h1b_data(ticker: str | None = None) -> dict:
    """Load H-1B/LCA alternative data."""
    data = _load_json("alt_data_h1b.json")
    if ticker:
        return data.get(ticker, {})
    return data


def load_sec_data(ticker: str | None = None) -> dict:
    """Load SEC filing alternative data."""
    data = _load_json("alt_data_sec.json")
    if ticker:
        return data.get(ticker, {})
    return data


def load_thematic_research() -> dict:
    """Load thematic research / market need data."""
    return _load_json("thematic_research.json")


def get_available_dates() -> list[str]:
    """Return sorted list of available sample dates."""
    data = _load_json("top_gainers.json")
    return sorted([entry["date"] for entry in data])

