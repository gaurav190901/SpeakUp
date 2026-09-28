"""
speakup/services/dashboard_mapper.py
──────────────────────────────────────
Pure-Python data mapper: converts raw ProgressService output into
display-ready dicts consumed by DashboardPage widgets.

No Qt, no DB, no file I/O — safe to unit-test without any GUI setup.
"""

from __future__ import annotations

from speakup.utils.helpers import format_duration, format_score, score_to_label


# ---------------------------------------------------------------------------
# Stat card mapping
# ---------------------------------------------------------------------------

def map_stat_cards(stats: dict) -> list[dict]:
    """
    Map dashboard stats dict → a list of 6 stat-card display dicts.

    Each card dict:
        key        — internal identifier
        title      — display title
        value      — formatted primary value (string)
        subtitle   — secondary line
        accent     — hex colour for the large number
        delta      — optional ImprovementDelta dict (or None)
    """
    return [
        {
            "key":      "sessions",
            "title":    "Speaking Sessions",
            "value":    str(stats.get("total_sessions", 0) or 0),
            "subtitle": _plural("session", stats.get("total_sessions", 0) or 0),
            "accent":   "#6C63FF",
            "delta":    None,
        },
        {
            "key":      "time",
            "title":    "Practice Time",
            "value":    format_duration(stats.get("total_seconds") or 0),
            "subtitle": "total time spoken",
            "accent":   "#4CAF50",
            "delta":    None,
        },
        {
            "key":      "confidence",
            "title":    "Avg Confidence",
            "value":    _fmt_score(stats.get("avg_confidence")),
            "subtitle": "speaking confidence estimate",
            "accent":   "#FF9800",
            "delta":    None,   # filled by map_delta()
        },
        {
            "key":      "overall",
            "title":    "Avg Overall Score",
            "value":    _fmt_score(stats.get("avg_overall_score")),
            "subtitle": score_to_label(stats.get("avg_overall_score")),
            "accent":   "#2196F3",
            "delta":    None,
        },
        {
            "key":      "fluency",
            "title":    "Avg Fluency",
            "value":    _fmt_score(stats.get("avg_fluency")),
            "subtitle": score_to_label(stats.get("avg_fluency")),
            "accent":   "#00BCD4",
            "delta":    None,
        },
        {
            "key":      "vocabulary",
            "title":    "Avg Vocabulary",
            "value":    _fmt_score(stats.get("avg_vocabulary")),
            "subtitle": score_to_label(stats.get("avg_vocabulary")),
            "accent":   "#9C27B0",
            "delta":    None,
        },
    ]


# ---------------------------------------------------------------------------
# Improvement delta mapping
# ---------------------------------------------------------------------------

def map_delta(delta_raw: dict) -> dict[str, dict]:
    """
    Convert raw improvement delta (from ProgressService.get_improvement_delta)
    into display-ready strings keyed by card key.

    Returns a dict mapping card key → display dict:
        first_str  — e.g. "72"
        last_str   — e.g. "79"
        delta_str  — e.g. "+7" or "-3"
        positive   — bool (True → green, False → red)
    """
    card_map = {
        "overall_score":    "overall",
        "confidence_score": "confidence",
        "fluency_score":    "fluency",
        "words_per_minute": "wpm",
    }
    result: dict[str, dict] = {}
    for raw_key, card_key in card_map.items():
        entry = delta_raw.get(raw_key)
        if entry is None:
            continue
        delta = entry["delta"]
        sign  = "+" if delta >= 0 else ""
        result[card_key] = {
            "first_str":  f"{entry['first']:.0f}",
            "last_str":   f"{entry['last']:.0f}",
            "delta_str":  f"{sign}{delta:.1f}",
            "positive":   delta >= 0,
        }
    return result


# ---------------------------------------------------------------------------
# Chart series mapping
# ---------------------------------------------------------------------------

def map_chart_series(chart_data: dict) -> dict:
    """
    Normalize chart data for the 6 panels, handling None values.

    Returns a dict with the same keys as chart_data but with None values
    replaced by 0 for rendering purposes, plus display metadata.
    """
    xs = chart_data.get("session_numbers", [])
    if not xs:
        return {"has_data": False}

    def _clean(values: list, default=0.0) -> list:
        return [v if v is not None else default for v in values]

    return {
        "has_data":        True,
        "x":               xs,
        "confidence":      _clean(chart_data.get("confidence", [])),
        "overall":         _clean(chart_data.get("overall", [])),
        "fluency":         _clean(chart_data.get("fluency", [])),
        "duration_min":    _clean(chart_data.get("duration_minutes", [])),
        "wpm":             _clean(chart_data.get("words_per_minute", [])),
        "filler_counts":   _clean(chart_data.get("filler_counts", []), default=0),
        "session_count":   len(xs),
    }


# ---------------------------------------------------------------------------
# Recent sessions table mapping
# ---------------------------------------------------------------------------

def map_recent_sessions(history_rows: list[dict]) -> list[dict]:
    """
    Convert rich history rows into display-ready table rows.

    Each output dict:
        session_id, date_str, word, category, duration_str,
        overall_str, confidence_str, topic_display
    """
    from speakup.utils.helpers import format_datetime  # lazy import

    rows: list[dict] = []
    for r in history_rows:
        date_str = format_datetime(r.get("completed_at"), fmt="%d %b %Y")
        rows.append({
            "session_id":    r["session_id"],
            "date_str":      date_str,
            "word":          r.get("word", "—"),
            "category":      r.get("category") or "—",
            "duration_str":  format_duration(r.get("duration_seconds") or 0),
            "overall_str":   _fmt_score(r.get("overall_score")),
            "confidence_str": _fmt_score(r.get("confidence_score")),
            "topic_display": r.get("word", "—"),
            # Pass-through fields needed by SessionResultsPage
            "overall_score":    r.get("overall_score"),
            "confidence_score": r.get("confidence_score"),
            "fluency_score":    r.get("fluency_score"),
            "vocabulary_score": r.get("vocabulary_score"),
            "grammar_score":    r.get("grammar_score"),
            "filler_word_count": r.get("filler_word_count"),
            "words_per_minute": r.get("words_per_minute"),
            "pause_count":      r.get("pause_count"),
            "feedback":         r.get("feedback", ""),
            "duration_seconds": r.get("duration_seconds", 0),
            "transcript":       r.get("transcript", ""),
            "difficulty":       r.get("difficulty"),
        })
    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_score(score: float | None) -> str:
    if score is None:
        return "—"
    return str(int(round(score)))


def _plural(noun: str, count: int) -> str:
    return f"{noun}s" if count != 1 else noun
