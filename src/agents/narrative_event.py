"""
Agent 2: Narrative / Event Agent
Reads news, press releases, filing headlines. Classifies event type.
Determines whether event appears to be real value creation or promotional narrative.
"""
from src.data_adapters.mock_loader import load_news_events


# Event classification keywords
EVENT_KEYWORDS = {
    "confirmed_acquisition": ["acquisition", "acquired", "merger", "buyout"],
    "fda_approval": ["fda", "approval", "breakthrough therapy", "priority review", "pdufa"],
    "major_real_contract": ["licensing deal", "contract", "partnership", "$"],
    "earnings_surprise_real": ["revenue grows", "beats expectations", "record revenue", "gross bookings grow"],
    "ai_pivot": ["pivot to ai", "ai transformation", "ai-powered", "ai platform", "rebrands"],
    "crypto_pivot": ["blockchain", "tokenization", "crypto", "token launch"],
    "vague_partnership": ["strategic partnership", "joint venture", "collaboration"],
    "meme_squeeze": ["squeeze", "short interest", "wsb", "wallstreetbets", "reddit", "moonshot"],
    "platform_story_no_evidence": ["revolutionary", "platform", "disrupt", "transform"],
}


def classify_event(ticker: str, news_items: list[dict] | None = None) -> dict:
    """
    Classify the event driving a stock's movement.

    Returns:
        dict with event_type, is_real_value, confidence, evidence
    """
    if news_items is None:
        news_items = load_news_events(ticker)

    if not news_items:
        return {
            "ticker": ticker,
            "event_type": "unknown",
            "is_real_value": False,
            "confidence": "low",
            "evidence": ["No news events found"],
            "headlines": [],
        }

    # Score each event type based on keyword matches across all news
    all_text = " ".join(
        (item.get("headline", "") + " " + item.get("snippet", "")).lower()
        for item in news_items
    )

    type_scores = {}
    for event_type, keywords in EVENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in all_text)
        if score > 0:
            type_scores[event_type] = score

    if not type_scores:
        return {
            "ticker": ticker,
            "event_type": "unknown",
            "is_real_value": False,
            "confidence": "low",
            "evidence": ["No recognizable event pattern"],
            "headlines": [n["headline"] for n in news_items],
        }

    # Pick the dominant event type
    primary_event = max(type_scores, key=type_scores.get)

    # Determine if this is real value creation
    REAL_VALUE_TYPES = {
        "confirmed_acquisition", "fda_approval",
        "major_real_contract", "earnings_surprise_real",
    }
    PROMOTIONAL_TYPES = {
        "ai_pivot", "crypto_pivot", "vague_partnership",
        "meme_squeeze", "platform_story_no_evidence",
    }

    is_real = primary_event in REAL_VALUE_TYPES
    is_promotional = primary_event in PROMOTIONAL_TYPES

    # Check for mixed signals (e.g., real contract + meme squeeze)
    has_real = any(t in type_scores for t in REAL_VALUE_TYPES)
    has_promotional = any(t in type_scores for t in PROMOTIONAL_TYPES)

    if has_real and has_promotional:
        confidence = "low"
        evidence_note = "Mixed signals: both real and promotional patterns detected"
    elif is_real:
        confidence = "high"
        evidence_note = f"Real value event: {primary_event}"
    elif is_promotional:
        confidence = "high" if type_scores[primary_event] >= 2 else "medium"
        evidence_note = f"Promotional narrative: {primary_event}"
    else:
        confidence = "medium"
        evidence_note = f"Event classified as: {primary_event}"

    return {
        "ticker": ticker,
        "event_type": primary_event,
        "is_real_value": is_real and not is_promotional,
        "confidence": confidence,
        "evidence": [evidence_note] + [f"Matched: {k} (score={v})" for k, v in type_scores.items()],
        "headlines": [n["headline"] for n in news_items],
        "all_event_types": type_scores,
    }


def run(tickers: list[str]) -> dict:
    """Run narrative classification for a list of tickers."""
    results = {}
    for ticker in tickers:
        results[ticker] = classify_event(ticker)
    return {
        "agent": "narrative_event",
        "classifications": results,
    }
