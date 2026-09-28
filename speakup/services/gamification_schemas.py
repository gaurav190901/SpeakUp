"""
speakup/services/gamification_schemas.py
──────────────────────────────────────────
Pure-Python dataclasses for the gamification system.

No Qt, DB, or UI dependencies — safe to import anywhere.
All values come from real DB data; nothing is fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Achievement
# ---------------------------------------------------------------------------

@dataclass
class Achievement:
    """A single unlocked (or locked) milestone."""

    key:         str          # internal identifier, e.g. "first_speech"
    title:       str          # display title, e.g. "First Speech"
    description: str          # one-line description
    icon:        str          # emoji icon
    unlocked:    bool = False # True when the criterion is met
    # Value at which the achievement unlocks (used for progress bar display)
    threshold:   int = 1


# ---------------------------------------------------------------------------
# Streak info
# ---------------------------------------------------------------------------

@dataclass
class StreakInfo:
    """Current and best consecutive-day streak."""

    current_streak:  int = 0    # consecutive days including today
    longest_streak:  int = 0    # all-time best consecutive days
    practiced_today: bool = False

    @property
    def streak_label(self) -> str:
        if self.current_streak == 0:
            return "No active streak"
        if self.current_streak == 1:
            return "1 day streak 🔥"
        return f"{self.current_streak} day streak 🔥"


# ---------------------------------------------------------------------------
# Daily goal
# ---------------------------------------------------------------------------

@dataclass
class DailyGoalProgress:
    """Progress towards today's practice goal."""

    goal_type:        str    # "sessions" | "minutes_5" | "minutes_10"
    goal_label:       str    # e.g. "1 session" or "10 minutes"
    target:           int    # sessions count or minutes
    achieved:         int    # sessions or minutes completed today
    completed:        bool   # True when achieved >= target
    progress_pct:     float  # 0.0–1.0

    @property
    def display_text(self) -> str:
        if self.completed:
            return f"Goal complete! {self.goal_label} ✅"
        remaining = self.target - self.achieved
        unit = "session" if self.goal_type == "sessions" else "min"
        plural = "s" if remaining != 1 else ""
        return f"{remaining} {unit}{plural} remaining"


# ---------------------------------------------------------------------------
# Personal best
# ---------------------------------------------------------------------------

@dataclass
class PersonalBest:
    """Best single-session scores across all time."""

    best_overall_score:    Optional[float] = None
    best_confidence_score: Optional[float] = None
    best_fluency_score:    Optional[float] = None
    best_session_id:       Optional[int]   = None  # session with highest overall


# ---------------------------------------------------------------------------
# Full gamification snapshot
# ---------------------------------------------------------------------------

@dataclass
class GamificationSnapshot:
    """
    All gamification data for one user at one point in time.

    Every field is derived from real database queries — nothing is invented.
    """

    streak:        StreakInfo
    daily_goal:    DailyGoalProgress
    personal_best: PersonalBest
    achievements:  list[Achievement] = field(default_factory=list)

    # Summary stats (mirrors DashboardStatsSchema subset)
    total_sessions:      int   = 0
    total_minutes:       float = 0.0
    topics_completed:    int   = 0

    @property
    def unlocked_achievements(self) -> list[Achievement]:
        return [a for a in self.achievements if a.unlocked]

    @property
    def locked_achievements(self) -> list[Achievement]:
        return [a for a in self.achievements if not a.unlocked]
