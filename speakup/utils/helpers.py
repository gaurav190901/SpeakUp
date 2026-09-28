"""
General-purpose helpers for SpeakUp.

Functions here are pure utilities with no dependencies on PySide6 or
the database layer, so they can be imported freely throughout the app
and in tests without side effects.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Time / duration formatting
# ---------------------------------------------------------------------------

def format_duration(seconds: int | float | None) -> str:
    """
    Convert a duration in seconds to a human-readable string.

    Examples:
        format_duration(75)    → "1m 15s"
        format_duration(3600)  → "1h 0m"
        format_duration(45)    → "45s"
        format_duration(None)  → "—"
    """
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 0:
        return "—"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_minutes(seconds: int | float | None, decimal: bool = False) -> str:
    """
    Convert seconds to a minutes string.

    Examples:
        format_minutes(90)          → "1.5 min"
        format_minutes(90, False)   → "1 min"
        format_minutes(None)        → "—"
    """
    if seconds is None:
        return "—"
    minutes = seconds / 60
    if decimal:
        return f"{minutes:.1f} min"
    return f"{int(minutes)} min"


def format_datetime(dt: datetime | None, fmt: str = "%d %b %Y, %H:%M") -> str:
    """
    Format a datetime for display.

    Returns ``'—'`` when *dt* is None.
    """
    if dt is None:
        return "—"
    return dt.strftime(fmt)


def relative_time(dt: datetime | None) -> str:
    """
    Return a human-friendly relative time string (e.g. "2 hours ago").
    Falls back to formatted date for older items.
    """
    if dt is None:
        return "—"
    now = datetime.utcnow()
    diff: timedelta = now - dt
    total_seconds = int(diff.total_seconds())

    if total_seconds < 60:
        return "just now"
    if total_seconds < 3600:
        m = total_seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if total_seconds < 86400:
        h = total_seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    if total_seconds < 604800:
        d = total_seconds // 86400
        return f"{d} day{'s' if d != 1 else ''} ago"
    return format_datetime(dt, fmt="%d %b %Y")


# ---------------------------------------------------------------------------
# Score / percentage formatting
# ---------------------------------------------------------------------------

def format_score(score: float | None, suffix: str = "") -> str:
    """
    Format a numeric score for display.

    Examples:
        format_score(87.5)       → "87.5"
        format_score(87.5, "%")  → "87.5%"
        format_score(None)       → "—"
    """
    if score is None:
        return "—"
    value = f"{score:.1f}".rstrip("0").rstrip(".")
    return f"{value}{suffix}"


def score_to_label(score: float | None) -> str:
    """
    Convert a 0–100 score to a descriptive label.

    Returns: Excellent / Good / Fair / Needs Work / —
    """
    if score is None:
        return "—"
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 55:
        return "Fair"
    return "Needs Work"


def difficulty_label(difficulty: str | None) -> str:
    """Capitalise and return the difficulty string, or '—' when None."""
    if not difficulty:
        return "—"
    return difficulty.capitalize()


# ---------------------------------------------------------------------------
# String utilities
# ---------------------------------------------------------------------------

def truncate(text: str, max_length: int = 50, ellipsis: str = "…") -> str:
    """
    Truncate *text* to *max_length* characters, appending *ellipsis*.

    Examples:
        truncate("Hello, world!", 8) → "Hello, w…"
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(ellipsis)] + ellipsis


def slugify(text: str) -> str:
    """
    Convert a string to a URL/filename-safe slug.

    Examples:
        slugify("Artificial Intelligence") → "artificial-intelligence"
        slugify("Work-Life Balance!")       → "work-life-balance"
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text


def word_count(text: str | None) -> int:
    """Return the number of words in *text*."""
    if not text:
        return 0
    return len(text.split())


# ---------------------------------------------------------------------------
# Colour utilities (for dynamic UI colouring based on score)
# ---------------------------------------------------------------------------

def score_colour(score: float | None) -> str:
    """
    Return a hex colour string appropriate for the given 0–100 score.

    Uses the Settings theme palette:
        ≥ 85 → success green
        ≥ 55 → warning amber
        < 55 → danger red
        None → muted grey
    """
    from speakup.config.settings import settings  # lazy import

    if score is None:
        return settings.COLOR_TEXT_MUTED
    if score >= 85:
        return settings.COLOR_SUCCESS
    if score >= 55:
        return settings.COLOR_WARNING
    return settings.COLOR_DANGER
