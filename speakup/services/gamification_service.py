"""
speakup/services/gamification_service.py
──────────────────────────────────────────
GamificationService — achievements, streaks, goals, personal bests.

All data is derived from real database records.
No values are fabricated or hard-coded for display purposes.

Achievement definitions
───────────────────────
Each achievement has a key, title, description, icon, and threshold.
The engine checks the user's real session/usage/time data against each
threshold and sets `unlocked=True` when the criterion is met.

Achievement catalogue
─────────────────────
  first_speech        — complete 1 session
  sessions_5          — complete 5 sessions
  sessions_10         — complete 10 sessions
  sessions_25         — complete 25 sessions
  sessions_50         — complete 50 sessions
  minutes_10          — accumulate 10 minutes of speaking
  minutes_100         — accumulate 100 minutes of speaking
  streak_3            — 3-day streak
  streak_7            — 7-day streak
  topics_10           — complete 10 unique topics
  topics_50           — complete 50 unique topics
  score_80            — achieve overall score ≥ 80 in any session
  score_90            — achieve overall score ≥ 90 in any session
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from speakup.config.settings import settings
from speakup.database.connection import get_session
from speakup.database.repository import (
    AnalysisResultRepository,
    PracticeSessionRepository,
    UserPreferencesRepository,
    WordUsageRepository,
)
from speakup.services.gamification_schemas import (
    Achievement,
    DailyGoalProgress,
    GamificationSnapshot,
    PersonalBest,
    StreakInfo,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Achievement catalogue
# ---------------------------------------------------------------------------

_ACHIEVEMENT_DEFS: list[dict] = [
    {
        "key":         "first_speech",
        "title":       "First Speech",
        "description": "Complete your very first practice session.",
        "icon":        "🎤",
        "threshold":   1,
        "metric":      "total_sessions",
    },
    {
        "key":         "sessions_5",
        "title":       "5 Sessions",
        "description": "Complete 5 practice sessions.",
        "icon":        "✋",
        "threshold":   5,
        "metric":      "total_sessions",
    },
    {
        "key":         "sessions_10",
        "title":       "10 Sessions",
        "description": "Complete 10 practice sessions.",
        "icon":        "🔟",
        "threshold":   10,
        "metric":      "total_sessions",
    },
    {
        "key":         "sessions_25",
        "title":       "25 Sessions",
        "description": "Complete 25 practice sessions.",
        "icon":        "🥈",
        "threshold":   25,
        "metric":      "total_sessions",
    },
    {
        "key":         "sessions_50",
        "title":       "50 Sessions",
        "description": "Complete 50 practice sessions.",
        "icon":        "🥇",
        "threshold":   50,
        "metric":      "total_sessions",
    },
    {
        "key":         "minutes_10",
        "title":       "10 Minutes Speaking",
        "description": "Accumulate 10 minutes of practice time.",
        "icon":        "⏱️",
        "threshold":   10,    # minutes
        "metric":      "total_minutes",
    },
    {
        "key":         "minutes_100",
        "title":       "100 Minutes Speaking",
        "description": "Accumulate 100 minutes of practice time.",
        "icon":        "⏰",
        "threshold":   100,
        "metric":      "total_minutes",
    },
    {
        "key":         "streak_3",
        "title":       "3 Day Streak",
        "description": "Practice on 3 consecutive days.",
        "icon":        "🔥",
        "threshold":   3,
        "metric":      "streak",
    },
    {
        "key":         "streak_7",
        "title":       "7 Day Streak",
        "description": "Practice on 7 consecutive days.",
        "icon":        "💥",
        "threshold":   7,
        "metric":      "streak",
    },
    {
        "key":         "topics_10",
        "title":       "10 Topics Completed",
        "description": "Complete 10 unique topics.",
        "icon":        "📚",
        "threshold":   10,
        "metric":      "topics_completed",
    },
    {
        "key":         "topics_50",
        "title":       "50 Topics Completed",
        "description": "Complete 50 unique topics.",
        "icon":        "🏆",
        "threshold":   50,
        "metric":      "topics_completed",
    },
    {
        "key":         "score_80",
        "title":       "Score 80",
        "description": "Achieve an overall score of 80 or higher.",
        "icon":        "⭐",
        "threshold":   80,
        "metric":      "best_overall_score",
    },
    {
        "key":         "score_90",
        "title":       "Score 90",
        "description": "Achieve an overall score of 90 or higher.",
        "icon":        "🌟",
        "threshold":   90,
        "metric":      "best_overall_score",
    },
]

# Goal definitions: key → (label, target_value, unit)
_GOAL_DEFS: dict[str, tuple[str, int]] = {
    "sessions":   ("1 session",   1),    # target = 1 completed session today
    "minutes_5":  ("5 minutes",   5),    # target = 5 minutes total today
    "minutes_10": ("10 minutes", 10),    # target = 10 minutes total today
}


class GamificationService:
    """
    Computes all gamification data from real database records.
    """

    # ------------------------------------------------------------------ #
    # Main entry point                                                      #
    # ------------------------------------------------------------------ #

    def get_snapshot(self, user_id: int) -> GamificationSnapshot:
        """
        Return a complete GamificationSnapshot for *user_id*.

        Performs a small number of DB queries and returns a fully populated
        dataclass — no fake data, no placeholders.
        """
        with get_session() as session:
            ps_repo    = PracticeSessionRepository(session)
            usage_repo = WordUsageRepository(session)
            ar_repo    = AnalysisResultRepository(session)
            prefs_repo = UserPreferencesRepository(session)

            prefs = prefs_repo.get_or_create(user_id)

            total_sessions = ps_repo.count_by_user(user_id, status="completed")
            total_seconds  = ps_repo.get_total_duration_by_user(user_id)
            total_minutes  = round(total_seconds / 60, 1)
            topics_completed = usage_repo.count_used_for_user(user_id)

            # Personal best
            personal_best = self._compute_personal_best(ar_repo, ps_repo, user_id)

            # Streak
            streak = self._compute_streak(ar_repo, user_id)

            # Today's goal progress
            daily_goal = self._compute_daily_goal(
                ps_repo, user_id, prefs.daily_goal_type
            )

        # Achievements (computed from the aggregated data, not raw DB rows)
        metrics = {
            "total_sessions":   total_sessions,
            "total_minutes":    total_minutes,
            "streak":           streak.current_streak,
            "topics_completed": topics_completed,
            "best_overall_score": personal_best.best_overall_score or 0,
        }
        achievements = self._compute_achievements(metrics)

        return GamificationSnapshot(
            streak=streak,
            daily_goal=daily_goal,
            personal_best=personal_best,
            achievements=achievements,
            total_sessions=total_sessions,
            total_minutes=total_minutes,
            topics_completed=topics_completed,
        )

    # ------------------------------------------------------------------ #
    # Individual calculators                                                #
    # ------------------------------------------------------------------ #

    def get_streak(self, user_id: int) -> StreakInfo:
        with get_session() as session:
            return self._compute_streak(
                AnalysisResultRepository(session), user_id
            )

    def get_achievements(self, user_id: int) -> list[Achievement]:
        with get_session() as session:
            ps_repo    = PracticeSessionRepository(session)
            usage_repo = WordUsageRepository(session)
            ar_repo    = AnalysisResultRepository(session)

            total_sessions   = ps_repo.count_by_user(user_id, status="completed")
            total_seconds    = ps_repo.get_total_duration_by_user(user_id)
            topics_completed = usage_repo.count_used_for_user(user_id)

            personal_best = self._compute_personal_best(ar_repo, ps_repo, user_id)
            streak        = self._compute_streak(ar_repo, user_id)

        metrics = {
            "total_sessions":    total_sessions,
            "total_minutes":     round(total_seconds / 60, 1),
            "streak":            streak.current_streak,
            "topics_completed":  topics_completed,
            "best_overall_score": personal_best.best_overall_score or 0,
        }
        return self._compute_achievements(metrics)

    def get_daily_goal_progress(
        self, user_id: int, goal_type: str | None = None
    ) -> DailyGoalProgress:
        with get_session() as session:
            prefs_repo = UserPreferencesRepository(session)
            ps_repo    = PracticeSessionRepository(session)

            if goal_type is None:
                prefs     = prefs_repo.get_or_create(user_id)
                goal_type = prefs.daily_goal_type

            return self._compute_daily_goal(ps_repo, user_id, goal_type)

    def get_personal_best(self, user_id: int) -> PersonalBest:
        with get_session() as session:
            return self._compute_personal_best(
                AnalysisResultRepository(session),
                PracticeSessionRepository(session),
                user_id,
            )

    def get_preferences(self, user_id: int) -> dict:
        """Return the user's current preferences as a plain dict."""
        with get_session() as session:
            prefs = UserPreferencesRepository(session).get_or_create(user_id)
            return {
                "difficulty_preference": prefs.difficulty_preference,
                "daily_goal_type":       prefs.daily_goal_type,
            }

    def save_preferences(
        self,
        user_id: int,
        difficulty_preference: str | None = None,
        daily_goal_type: str | None = None,
    ) -> None:
        """Persist user preferences."""
        with get_session() as session:
            UserPreferencesRepository(session).save(
                user_id,
                difficulty_preference=difficulty_preference,
                daily_goal_type=daily_goal_type,
            )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compute_streak(
        ar_repo: AnalysisResultRepository, user_id: int
    ) -> StreakInfo:
        """Compute current streak and longest streak from session data."""
        rows = ar_repo.get_time_series_for_user(user_id, since=None, limit=365)
        if not rows:
            return StreakInfo()

        today = datetime.now(timezone.utc).date()

        # Collect unique practice dates
        dates: list = sorted(
            set(
                r["completed_at"].date()
                if hasattr(r["completed_at"], "date")
                else r["completed_at"]
                for r in rows
                if r.get("completed_at")
            ),
            reverse=True,
        )

        practiced_today = bool(dates and dates[0] == today)

        # Current streak
        current = 0
        expected = today
        for d in dates:
            if d == expected:
                current += 1
                expected -= timedelta(days=1)
            elif d < expected:
                break

        # Longest streak (full pass)
        longest = 1
        run     = 1
        for i in range(1, len(dates)):
            prev = dates[i - 1]
            curr = dates[i]
            if (prev - curr).days == 1:
                run += 1
                longest = max(longest, run)
            else:
                run = 1

        return StreakInfo(
            current_streak=current,
            longest_streak=max(longest, current),
            practiced_today=practiced_today,
        )

    @staticmethod
    def _compute_daily_goal(
        ps_repo: PracticeSessionRepository,
        user_id: int,
        goal_type: str,
    ) -> DailyGoalProgress:
        """Compute today's goal progress from completed sessions."""
        goal_label, target = _GOAL_DEFS.get(goal_type, ("1 session", 1))
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Fetch today's completed sessions
        from sqlalchemy import select as sa_select  # noqa: PLC0415
        from speakup.database.models import PracticeSession  # noqa: PLC0415
        from speakup.database.connection import get_session as _gs  # noqa: PLC0415

        # Use the already-open session if possible (we receive ps_repo)
        # Reconstruct from available data:
        completed_today = ps_repo.get_by_user(user_id, status="completed")
        today_sessions  = [
            ps for ps in completed_today
            if ps.completed_at is not None
            and (
                ps.completed_at.replace(tzinfo=timezone.utc)
                if ps.completed_at.tzinfo is None
                else ps.completed_at
            ) >= today_start
        ]

        if goal_type == "sessions":
            achieved = len(today_sessions)
        else:
            # minutes goal — sum duration_seconds for today's sessions
            total_s  = sum(ps.duration_seconds or 0 for ps in today_sessions)
            achieved = int(total_s // 60)

        completed = achieved >= target
        pct       = min(1.0, achieved / target) if target > 0 else 0.0

        return DailyGoalProgress(
            goal_type=goal_type,
            goal_label=goal_label,
            target=target,
            achieved=achieved,
            completed=completed,
            progress_pct=pct,
        )

    @staticmethod
    def _compute_personal_best(
        ar_repo: AnalysisResultRepository,
        ps_repo: PracticeSessionRepository,
        user_id: int,
    ) -> PersonalBest:
        """Find the best session scores across all completed sessions."""
        rows = ar_repo.get_time_series_for_user(user_id, since=None)
        if not rows:
            return PersonalBest()

        best_overall    = None
        best_confidence = None
        best_fluency    = None
        best_session_id = None

        for r in rows:
            s = r.get("overall_score")
            if s is not None and (best_overall is None or s > best_overall):
                best_overall    = s
                best_session_id = r["session_id"]
            c = r.get("confidence_score")
            if c is not None and (best_confidence is None or c > best_confidence):
                best_confidence = c
            f = r.get("fluency_score")
            if f is not None and (best_fluency is None or f > best_fluency):
                best_fluency = f

        return PersonalBest(
            best_overall_score=best_overall,
            best_confidence_score=best_confidence,
            best_fluency_score=best_fluency,
            best_session_id=best_session_id,
        )

    @staticmethod
    def _compute_achievements(metrics: dict) -> list[Achievement]:
        """Check each achievement definition against current metrics."""
        result: list[Achievement] = []
        for defn in _ACHIEVEMENT_DEFS:
            metric_key = defn["metric"]
            value      = metrics.get(metric_key, 0) or 0
            threshold  = defn["threshold"]
            result.append(Achievement(
                key=defn["key"],
                title=defn["title"],
                description=defn["description"],
                icon=defn["icon"],
                unlocked=(value >= threshold),
                threshold=threshold,
            ))
        return result
