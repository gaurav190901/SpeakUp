"""Build the data shown on a session's results page."""

from __future__ import annotations

from speakup.database.connection import get_session
from speakup.database.repository import PracticeSessionRepository
from speakup.data.words.loader import DIFFICULTY_DISPLAY, category_with_icon
from speakup.utils.helpers import format_duration

SCORE_LABELS = (
    ("overall_score",    "Overall"),
    ("fluency_score",    "Fluency"),
    ("vocabulary_score", "Vocabulary"),
    ("grammar_score",    "Grammar"),
    ("confidence_score", "Confidence"),
)


def parse_feedback_text(text: str | None) -> dict[str, list[str]]:
    """Split the stored feedback text into strengths / improvements / suggestions."""
    sections: dict[str, list[str]] = {"strengths": [], "improvements": [], "suggestions": []}
    current = None
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Strengths:"):
            current = "strengths"
        elif stripped.startswith("Areas to improve:"):
            current = "improvements"
        elif stripped.startswith("Suggestions:"):
            current = "suggestions"
        elif stripped.startswith("•") and current:
            item = stripped.lstrip("•").strip()
            if item:
                sections[current].append(item)
    return sections


def score_band(score: float | None) -> str:
    """CSS modifier for colouring a 0–100 score."""
    if score is None:
        return "none"
    if score >= 75:
        return "good"
    if score >= 50:
        return "ok"
    return "low"


def load_session_result(session_id: int, user_id: int) -> dict | None:
    """Everything the results page needs, or None if not this user's session."""
    from speakup.services.analysis_service import AnalysisService  # noqa: PLC0415

    with get_session() as db:
        ps = PracticeSessionRepository(db).get_by_id(session_id)
        if ps is None or ps.user_id != user_id:
            return None
        word = ps.word
        data = {
            "session_id": ps.id,
            "status": ps.status,
            "topic": word.word if word else "—",
            "category": category_with_icon(word.category) if word else "—",
            "difficulty": DIFFICULTY_DISPLAY.get(word.difficulty, word.difficulty) if word else "—",
            "transcript": ps.transcript or "",
            "duration_seconds": ps.duration_seconds or 0,
            "duration_text": format_duration(ps.duration_seconds or 0),
            "completed_at": ps.completed_at,
        }

    analysis = AnalysisService().get_result_for_session(session_id) or {}
    data["has_analysis"] = bool(analysis)
    data["scores"] = [
        {
            "key": key,
            "label": label,
            "value": analysis.get(key),
            "band": score_band(analysis.get(key)),
        }
        for key, label in SCORE_LABELS
    ]
    data["wpm"] = analysis.get("words_per_minute")
    data["filler_count"] = analysis.get("filler_word_count")
    data["pause_count"] = analysis.get("pause_count")
    data["word_count"] = len(data["transcript"].split())
    data.update(parse_feedback_text(analysis.get("feedback")))
    return data
