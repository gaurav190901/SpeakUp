"""
ProgressService — aggregates user progress statistics for the Dashboard.

Provides real queries for:
    - Summary stat cards (with date filtering)
    - Time-series chart data (confidence, overall, fluency, duration, WPM, fillers)
    - Improvement delta (first N vs last N sessions)
    - Rich session history (for the Recent Sessions table)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from speakup.database.connection import get_session
from speakup.database.repository import (
    AnalysisResultRepository,
    PracticeSessionRepository,
    WordRepository,
    WordUsageRepository,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Date-range constants
# ---------------------------------------------------------------------------

DATE_RANGES = {
    "7d":  7,
    "30d": 30,
    "90d": 90,
    "all": None,
}


def _since_days(days: int | None) -> datetime | None:
    """Return a UTC datetime *days* ago, or None for 'all time'."""
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


class ProgressService:
    """
    Computes progress metrics shown on the Dashboard and History pages.
    All methods return plain dicts or lists of dicts — no ORM objects.
    """

    # ------------------------------------------------------------------ #
    # Summary stats (date-filtered)                                        #
    # ------------------------------------------------------------------ #

    def get_dashboard_stats(
        self, user_id: int, date_range: str = "all"
    ) -> dict:
        """
        Return aggregate stats for the stat cards.

        Keys:
            total_sessions    — int
            total_seconds     — int
            total_minutes     — float
            avg_confidence    — float | None
            avg_overall_score — float | None
            avg_fluency       — float | None
            avg_vocabulary    — float | None
            avg_grammar       — float | None
            avg_wpm           — float | None
            words_remaining   — int
        """
        since = _since_days(DATE_RANGES.get(date_range))

        with get_session() as session:
            session_repo  = PracticeSessionRepository(session)
            analysis_repo = AnalysisResultRepository(session)
            word_repo     = WordRepository(session)
            usage_repo    = WordUsageRepository(session)

            total_sessions = session_repo.count_by_user(user_id, status="completed")
            total_seconds  = session_repo.get_total_duration_by_user(user_id)
            averages       = analysis_repo.get_filtered_averages_for_user(
                user_id, since=since
            )
            total_words    = word_repo.count()
            used_words     = usage_repo.count_used_for_user(user_id)

        return {
            "total_sessions":   total_sessions,
            "total_seconds":    total_seconds,
            "total_minutes":    round(total_seconds / 60, 1),
            "avg_confidence":   averages.get("confidence_score"),
            "avg_overall_score": averages.get("overall_score"),
            "avg_fluency":      averages.get("fluency_score"),
            "avg_vocabulary":   averages.get("vocabulary_score"),
            "avg_grammar":      averages.get("grammar_score"),
            "avg_wpm":          averages.get("words_per_minute"),
            "words_remaining":  max(0, total_words - used_words),
        }

    # ------------------------------------------------------------------ #
    # Improvement delta                                                    #
    # ------------------------------------------------------------------ #

    def get_improvement_delta(
        self,
        user_id: int,
        window: int = 3,
        date_range: str = "all",
    ) -> dict:
        """
        Compare the average of the first *window* sessions vs the last
        *window* sessions for key metrics.

        Returns a dict mapping metric_key → {'first': float, 'last': float, 'delta': float}.
        Only populated when there are at least 2 * window completed sessions.
        Returns an empty dict when not enough data exists.
        """
        since = _since_days(DATE_RANGES.get(date_range))

        with get_session() as session:
            rows = AnalysisResultRepository(session).get_time_series_for_user(
                user_id, since=since
            )

        if len(rows) < 2:
            return {}

        # Use all sessions if fewer than 2*window exist
        w = min(window, max(1, len(rows) // 2))
        first_rows = rows[:w]
        last_rows  = rows[-w:]

        metrics = [
            ("overall_score",    "overall_score"),
            ("confidence_score", "confidence_score"),
            ("fluency_score",    "fluency_score"),
            ("words_per_minute", "words_per_minute"),
        ]
        result: dict = {}
        for label, key in metrics:
            first_vals = [r[key] for r in first_rows if r.get(key) is not None]
            last_vals  = [r[key] for r in last_rows  if r.get(key) is not None]
            if not first_vals or not last_vals:
                continue
            first_avg = sum(first_vals) / len(first_vals)
            last_avg  = sum(last_vals)  / len(last_vals)
            result[label] = {
                "first": round(first_avg, 1),
                "last":  round(last_avg,  1),
                "delta": round(last_avg - first_avg, 1),
            }
        return result

    # ------------------------------------------------------------------ #
    # Time-series chart data                                               #
    # ------------------------------------------------------------------ #

    def get_chart_data(
        self,
        user_id: int,
        date_range: str = "all",
    ) -> dict:
        """
        Return all time-series data needed for the six dashboard charts.

        Keys in returned dict:
            sessions          — list[dict] (each row from time-series query)
            session_numbers   — list[int]  (1, 2, 3, … for x-axis labels)
            confidence        — list[float | None]
            overall           — list[float | None]
            fluency           — list[float | None]
            duration_minutes  — list[float]
            words_per_minute  — list[float | None]
            filler_counts     — list[int | None]
        """
        since = _since_days(DATE_RANGES.get(date_range))

        with get_session() as session:
            rows = AnalysisResultRepository(session).get_time_series_for_user(
                user_id, since=since
            )

        n = len(rows)
        if n == 0:
            return {
                "sessions":         [],
                "session_numbers":  [],
                "confidence":       [],
                "overall":          [],
                "fluency":          [],
                "duration_minutes": [],
                "words_per_minute": [],
                "filler_counts":    [],
            }

        return {
            "sessions":         rows,
            "session_numbers":  list(range(1, n + 1)),
            "confidence":       [r["confidence_score"]  for r in rows],
            "overall":          [r["overall_score"]     for r in rows],
            "fluency":          [r["fluency_score"]     for r in rows],
            "duration_minutes": [round((r["duration_seconds"] or 0) / 60, 2) for r in rows],
            "words_per_minute": [r["words_per_minute"]  for r in rows],
            "filler_counts":    [r["filler_word_count"] for r in rows],
        }

    # kept for backward-compat
    def get_confidence_over_time(self, user_id: int) -> list[dict]:
        data = self.get_chart_data(user_id)
        return [
            {"date": r["completed_at"], "confidence_score": r["confidence_score"]}
            for r in data["sessions"]
        ]

    def get_score_over_time(self, user_id: int) -> list[dict]:
        data = self.get_chart_data(user_id)
        return [
            {"date": r["completed_at"], "overall_score": r["overall_score"]}
            for r in data["sessions"]
        ]

    # ------------------------------------------------------------------ #
    # Rich history (Recent Sessions table)                                 #
    # ------------------------------------------------------------------ #

    def get_rich_session_history(
        self,
        user_id: int,
        date_range: str = "all",
        limit: int = 50,
    ) -> list[dict]:
        """
        Return recent completed sessions with analysis scores for the
        Recent Sessions table.  Each dict is ready for direct UI display.
        """
        since = _since_days(DATE_RANGES.get(date_range))

        with get_session() as session:
            rows = AnalysisResultRepository(session).get_rich_history_for_user(
                user_id, since=since, limit=limit
            )
        return rows

    # kept for backward-compat with HistoryPage
    def get_session_history(self, user_id: int) -> list[dict]:
        return self.get_rich_session_history(user_id, date_range="all")

    # ------------------------------------------------------------------ #
    # Streak / gamification                                                #
    # ------------------------------------------------------------------ #

    def get_current_streak(self, user_id: int) -> int:
        """
        Return the number of consecutive calendar days the user has practiced.
        0 when no sessions exist.
        """
        with get_session() as session:
            rows = AnalysisResultRepository(session).get_time_series_for_user(
                user_id, since=None, limit=365
            )

        if not rows:
            return 0

        today = datetime.now(timezone.utc).date()
        dates = sorted(
            set(
                r["completed_at"].date()
                if hasattr(r["completed_at"], "date")
                else r["completed_at"]
                for r in rows
                if r.get("completed_at")
            ),
            reverse=True,
        )

        streak = 0
        expected = today
        for d in dates:
            if d == expected:
                streak += 1
                expected -= timedelta(days=1)
            elif d < expected:
                break
        return streak
