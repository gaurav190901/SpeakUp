"""
speakup/data/words/loader.py
────────────────────────────
Loads word/topic data from the bundled JSON file and provides helper
constants used throughout the application.

Usage
-----
    from speakup.data.words.loader import load_topics, CATEGORIES, DIFFICULTY_LABELS

    topics = load_topics()          # list[dict]
    cats   = CATEGORIES             # sorted list of category strings
"""

from __future__ import annotations

import html
import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_TOPICS_JSON = Path(__file__).with_name("topics.json")

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# All categories present in topics.json (sorted for consistent UI display)
CATEGORIES: list[str] = [
    "Big Ideas",
    "Business",
    "Career",
    "Daily Life",
    "Debates",
    "Education",
    "Environment",
    "Everyday Objects",
    "Food",
    "Health",
    "Science",
    "Society",
    "Technology",
    "Travel",
]

# Friendly icon shown next to each category in the UI
CATEGORY_ICONS: dict[str, str] = {
    "Big Ideas":        "💭",
    "Business":         "💼",
    "Career":           "🧑‍💼",
    "Daily Life":       "🏠",
    "Debates":          "⚖️",
    "Education":        "🎓",
    "Environment":      "🌱",
    "Everyday Objects": "🔑",
    "Food":             "🍜",
    "Health":           "💪",
    "Science":          "🔬",
    "Society":          "🤝",
    "Technology":       "💻",
    "Travel":           "✈️",
}


def category_with_icon(category: str | None) -> str:
    """Return ``"<icon>  <category>"`` for display (plain name if no icon)."""
    if not category:
        return "—"
    icon = CATEGORY_ICONS.get(category)
    return f"{icon}  {category}" if icon else category

# DB-level difficulty values → human-readable display labels
DIFFICULTY_DISPLAY: dict[str, str] = {
    "easy":   "Beginner",
    "medium": "Intermediate",
    "hard":   "Advanced",
}

# Reverse map: display label → DB value
DISPLAY_TO_DIFFICULTY: dict[str, str] = {v: k for k, v in DIFFICULTY_DISPLAY.items()}

# Ordered difficulty levels for dropdowns
DIFFICULTY_LABELS: list[str] = ["Beginner", "Intermediate", "Advanced"]

# Coloured marker + plain-language explanation for each level
DIFFICULTY_ICONS: dict[str, str] = {
    "Beginner":     "🟢",
    "Intermediate": "🟡",
    "Advanced":     "🔴",
}
DIFFICULTY_HINTS: dict[str, str] = {
    "Beginner":     "Everyday topic — talk about your own life",
    "Intermediate": "Give your opinion and some reasons",
    "Advanced":     "Bigger questions — explain and compare ideas",
}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_topics() -> list[dict]:
    """
    Load and return all topics from ``topics.json``.

    The result is cached after the first call so the file is only read once
    per process lifetime.

    Returns:
        List of dicts with keys: word, category, difficulty, description.

    Raises:
        FileNotFoundError: if topics.json is missing.
        ValueError: if the JSON is malformed or a record is invalid.
    """
    if not _TOPICS_JSON.exists():
        raise FileNotFoundError(f"Topics file not found: {_TOPICS_JSON}")

    with _TOPICS_JSON.open(encoding="utf-8") as fh:
        raw: list[dict] = json.load(fh)

    validated: list[dict] = []
    for i, item in enumerate(raw):
        missing = [k for k in ("word", "category", "difficulty") if k not in item]
        if missing:
            logger.warning("topics.json record %d missing keys %s — skipped", i, missing)
            continue
        if item["difficulty"] not in ("easy", "medium", "hard"):
            logger.warning(
                "topics.json record %d has invalid difficulty %r — skipped",
                i, item["difficulty"],
            )
            continue
        validated.append(item)

    logger.debug("Loaded %d topics from %s", len(validated), _TOPICS_JSON.name)
    return validated


def get_topics_by_category(category: str) -> list[dict]:
    """Return all topics whose category matches *category* (case-sensitive)."""
    return [t for t in load_topics() if t["category"] == category]


def get_topics_by_difficulty(difficulty_db: str) -> list[dict]:
    """Return topics matching the DB difficulty value ('easy', 'medium', 'hard')."""
    return [t for t in load_topics() if t["difficulty"] == difficulty_db]


def get_topic_hints(word: str | None) -> list[str]:
    """Return the "ideas to talk about" hints for *word* (empty if none)."""
    if not word:
        return []
    for topic in load_topics():
        if topic["word"] == word:
            hints = topic.get("hints") or []
            return [str(h) for h in hints if str(h).strip()]
    return []


def format_hints_html(hints: list[str]) -> str:
    """Render hints as a small "Ideas to talk about" bullet list (Qt rich text)."""
    if not hints:
        return ""
    items = "<br>".join(f"&nbsp;&nbsp;•&nbsp; {html.escape(h)}" for h in hints)
    return f"<b>💡 Ideas to talk about</b><br>{items}"


def get_categories() -> list[str]:
    """Return the sorted list of all categories present in the dataset."""
    return CATEGORIES
