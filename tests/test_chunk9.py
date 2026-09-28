"""
tests/test_chunk9.py — pytest suite for Chunk 9 (Gamification & Progression).

All DB tests use the shared engine/db_session fixtures from conftest.py.
No external APIs, no hardware, no model downloads required.

Covers:
    1.  GamificationSchemas — dataclass properties
    2.  UserPreferences DB model and repository
    3.  GamificationService — achievements engine
    4.  GamificationService — streak calculation
    5.  GamificationService — daily goal progress (sessions)
    6.  GamificationService — daily goal progress (minutes)
    7.  GamificationService — personal best
    8.  GamificationService — full snapshot
    9.  GamificationService — preferences save/load
    10. Settings — gamification config present
    11. DashboardPage — gamification widgets present
    12. SettingsPage — functional preference selectors
    13. Edge cases — no sessions, missing data, goal already met
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from speakup.config.settings import settings                              # noqa: E402
from speakup.database.repository import (                                 # noqa: E402
    AnalysisResultRepository,
    PracticeSessionRepository,
    UserPreferencesRepository,
)
from speakup.services.gamification_schemas import (                       # noqa: E402
    Achievement,
    DailyGoalProgress,
    GamificationSnapshot,
    PersonalBest,
    StreakInfo,
)
from speakup.services.gamification_service import (                       # noqa: E402
    GamificationService,
    _ACHIEVEMENT_DEFS,
    _GOAL_DEFS,
)
from tests.conftest import make_user, make_word                           # noqa: E402


# ---------------------------------------------------------------------------
# Shared helper: create a completed session with analysis data
# ---------------------------------------------------------------------------

def _make_analysed_session(
    db_session,
    user_id: int,
    word_id: int,
    overall: float = 75.0,
    confidence: float = 70.0,
    duration: int = 75,
    completed_at: datetime | None = None,
) -> int:
    repo = PracticeSessionRepository(db_session)
    ps   = repo.create(user_id=user_id, word_id=word_id)
    db_session.flush()
    repo.complete_session(ps.id, duration_seconds=duration)

    if completed_at is not None:
        ps_row = repo.get_by_id(ps.id)
        ps_row.completed_at = completed_at
        db_session.flush()

    ar_repo = AnalysisResultRepository(db_session)
    ar_repo.create(
        session_id=ps.id,
        overall_score=overall,
        confidence_score=confidence,
        fluency_score=72.0,
        words_per_minute=115.0,
        filler_word_count=2,
    )
    db_session.commit()
    return ps.id


# ===========================================================================
# 1. GamificationSchemas — properties
# ===========================================================================

class TestGamificationSchemas:

    def test_streak_label_zero(self):
        s = StreakInfo(current_streak=0)
        assert "No active" in s.streak_label

    def test_streak_label_one(self):
        s = StreakInfo(current_streak=1)
        assert "1 day" in s.streak_label

    def test_streak_label_multiple(self):
        s = StreakInfo(current_streak=7)
        assert "7 day" in s.streak_label
        assert "🔥" in s.streak_label

    def test_daily_goal_progress_pct(self):
        g = DailyGoalProgress(
            goal_type="sessions", goal_label="1 session",
            target=1, achieved=0, completed=False, progress_pct=0.0,
        )
        assert g.progress_pct == 0.0

    def test_daily_goal_completed_text(self):
        g = DailyGoalProgress(
            goal_type="sessions", goal_label="1 session",
            target=1, achieved=1, completed=True, progress_pct=1.0,
        )
        assert "complete" in g.display_text.lower() or "goal" in g.display_text.lower()

    def test_daily_goal_remaining_text(self):
        g = DailyGoalProgress(
            goal_type="minutes_5", goal_label="5 minutes",
            target=5, achieved=2, completed=False, progress_pct=0.4,
        )
        assert "3" in g.display_text

    def test_achievement_unlocked_field(self):
        a = Achievement(key="test", title="T", description="D", icon="X",
                        unlocked=True, threshold=1)
        assert a.unlocked is True

    def test_gamification_snapshot_unlocked_filter(self):
        snap = GamificationSnapshot(
            streak=StreakInfo(),
            daily_goal=DailyGoalProgress(
                goal_type="sessions", goal_label="1 session",
                target=1, achieved=0, completed=False, progress_pct=0.0,
            ),
            personal_best=PersonalBest(),
            achievements=[
                Achievement("a", "A", "D", "X", unlocked=True),
                Achievement("b", "B", "D", "Y", unlocked=False),
            ],
        )
        assert len(snap.unlocked_achievements) == 1
        assert len(snap.locked_achievements)   == 1


# ===========================================================================
# 2. UserPreferences — model and repository
# ===========================================================================

class TestUserPreferencesRepository:

    def test_get_or_create_creates_defaults(self, db_session):
        user = make_user(db_session, "PrefUser1")
        repo = UserPreferencesRepository(db_session)
        prefs = repo.get_or_create(user.id)
        db_session.commit()
        assert prefs is not None
        assert prefs.user_id == user.id

    def test_default_difficulty_is_all(self, db_session):
        user = make_user(db_session, "PrefUser2")
        repo = UserPreferencesRepository(db_session)
        prefs = repo.get_or_create(user.id)
        db_session.commit()
        assert prefs.difficulty_preference == "all"

    def test_default_goal_is_sessions(self, db_session):
        user = make_user(db_session, "PrefUser3")
        repo = UserPreferencesRepository(db_session)
        prefs = repo.get_or_create(user.id)
        db_session.commit()
        assert prefs.daily_goal_type == "sessions"

    def test_save_changes_difficulty(self, db_session):
        user = make_user(db_session, "PrefUser4")
        repo = UserPreferencesRepository(db_session)
        repo.save(user.id, difficulty_preference="hard")
        db_session.commit()
        prefs = repo.get_or_create(user.id)
        assert prefs.difficulty_preference == "hard"

    def test_save_changes_goal(self, db_session):
        user = make_user(db_session, "PrefUser5")
        repo = UserPreferencesRepository(db_session)
        repo.save(user.id, daily_goal_type="minutes_10")
        db_session.commit()
        prefs = repo.get_or_create(user.id)
        assert prefs.daily_goal_type == "minutes_10"

    def test_get_or_create_idempotent(self, db_session):
        user = make_user(db_session, "PrefUser6")
        repo = UserPreferencesRepository(db_session)
        repo.get_or_create(user.id)
        db_session.commit()
        repo.get_or_create(user.id)
        db_session.commit()
        # Only one row should exist
        from sqlalchemy import select
        from speakup.database.models import UserPreferences
        count = db_session.scalar(
            select(__import__("sqlalchemy", fromlist=["func"]).func.count())
            .select_from(UserPreferences)
            .where(UserPreferences.user_id == user.id)
        )
        assert count == 1


# ===========================================================================
# 3. GamificationService — achievements engine
# ===========================================================================

class TestAchievementsEngine:

    def test_all_locked_when_no_sessions(self, engine):
        svc = GamificationService()
        achievements = svc.get_achievements(user_id=1)
        assert all(not a.unlocked for a in achievements)

    def test_first_speech_unlocked_after_one_session(self, db_session):
        user = make_user(db_session, "AchUser1")
        word = make_word(db_session, "AchWord1")
        _make_analysed_session(db_session, user.id, word.id)
        svc = GamificationService()
        achievements = svc.get_achievements(user.id)
        first = next(a for a in achievements if a.key == "first_speech")
        assert first.unlocked is True

    def test_sessions_5_unlocks_at_5(self, db_session):
        user = make_user(db_session, "AchUser2")
        for i in range(5):
            word = make_word(db_session, f"AchWord2_{i}")
            _make_analysed_session(db_session, user.id, word.id)
        svc = GamificationService()
        achievements = svc.get_achievements(user.id)
        s5 = next(a for a in achievements if a.key == "sessions_5")
        assert s5.unlocked is True

    def test_sessions_5_locked_at_4(self, db_session):
        user = make_user(db_session, "AchUser3")
        for i in range(4):
            word = make_word(db_session, f"AchWord3_{i}")
            _make_analysed_session(db_session, user.id, word.id)
        svc = GamificationService()
        achievements = svc.get_achievements(user.id)
        s5 = next(a for a in achievements if a.key == "sessions_5")
        assert s5.unlocked is False

    def test_minutes_10_unlocks_at_10_minutes(self, db_session):
        user  = make_user(db_session, "AchUser4")
        # 2 sessions × 5 minutes = 10 minutes
        for i in range(2):
            word = make_word(db_session, f"AchWord4_{i}")
            _make_analysed_session(db_session, user.id, word.id, duration=300)
        svc = GamificationService()
        achievements = svc.get_achievements(user.id)
        m10 = next(a for a in achievements if a.key == "minutes_10")
        assert m10.unlocked is True

    def test_score_80_unlocks_when_score_exceeds_80(self, db_session):
        user = make_user(db_session, "AchUser5")
        word = make_word(db_session, "AchWord5")
        _make_analysed_session(db_session, user.id, word.id, overall=85.0)
        svc = GamificationService()
        achievements = svc.get_achievements(user.id)
        s80 = next(a for a in achievements if a.key == "score_80")
        assert s80.unlocked is True

    def test_score_90_locked_when_score_is_88(self, db_session):
        user = make_user(db_session, "AchUser6")
        word = make_word(db_session, "AchWord6")
        _make_analysed_session(db_session, user.id, word.id, overall=88.0)
        svc = GamificationService()
        achievements = svc.get_achievements(user.id)
        s90 = next(a for a in achievements if a.key == "score_90")
        assert s90.unlocked is False

    def test_thirteen_achievement_definitions_exist(self):
        assert len(_ACHIEVEMENT_DEFS) == 13

    def test_achievement_icons_are_strings(self):
        for defn in _ACHIEVEMENT_DEFS:
            assert isinstance(defn["icon"], str)
            assert len(defn["icon"]) > 0


# ===========================================================================
# 4. GamificationService — streak calculation
# ===========================================================================

class TestStreakCalculation:

    def test_zero_streak_no_sessions(self, engine):
        svc = GamificationService()
        streak = svc.get_streak(user_id=1)
        assert streak.current_streak == 0
        assert streak.longest_streak == 0
        assert not streak.practiced_today

    def test_streak_of_one_for_today(self, db_session):
        user = make_user(db_session, "StrUser9_1")
        word = make_word(db_session, "StrWord9_1")
        _make_analysed_session(
            db_session, user.id, word.id,
            completed_at=datetime.now(timezone.utc),
        )
        svc = GamificationService()
        streak = svc.get_streak(user.id)
        assert streak.current_streak >= 1
        assert streak.practiced_today is True

    def test_streak_of_three_for_three_consecutive_days(self, db_session):
        user = make_user(db_session, "StrUser9_2")
        now = datetime.now(timezone.utc)
        for days_ago in [2, 1, 0]:
            word = make_word(db_session, f"StrWord9_2_{days_ago}")
            _make_analysed_session(
                db_session, user.id, word.id,
                completed_at=now - timedelta(days=days_ago),
            )
        svc = GamificationService()
        streak = svc.get_streak(user.id)
        assert streak.current_streak == 3

    def test_streak_breaks_on_missed_day(self, db_session):
        user = make_user(db_session, "StrUser9_3")
        now = datetime.now(timezone.utc)
        # Sessions 5 days ago and today — gap in between
        for days_ago in [5, 4, 0]:
            word = make_word(db_session, f"StrWord9_3_{days_ago}")
            _make_analysed_session(
                db_session, user.id, word.id,
                completed_at=now - timedelta(days=days_ago),
            )
        svc = GamificationService()
        streak = svc.get_streak(user.id)
        # Only today counts in the current streak
        assert streak.current_streak == 1

    def test_longest_streak_tracked(self, db_session):
        user = make_user(db_session, "StrUser9_4")
        now = datetime.now(timezone.utc)
        # Five consecutive days 10 days ago
        for i in range(5):
            word = make_word(db_session, f"StrWord9_4_{i}")
            _make_analysed_session(
                db_session, user.id, word.id,
                completed_at=now - timedelta(days=10 + i),
            )
        svc = GamificationService()
        streak = svc.get_streak(user.id)
        assert streak.longest_streak >= 5

    def test_multiple_sessions_same_day_count_as_one_day(self, db_session):
        user = make_user(db_session, "StrUser9_5")
        now  = datetime.now(timezone.utc)
        for i in range(3):
            word = make_word(db_session, f"StrWord9_5_{i}")
            _make_analysed_session(
                db_session, user.id, word.id,
                completed_at=now,  # same day
            )
        svc = GamificationService()
        streak = svc.get_streak(user.id)
        # Three sessions on the same day = 1-day streak, not 3
        assert streak.current_streak == 1


# ===========================================================================
# 5. GamificationService — daily goal (sessions)
# ===========================================================================

class TestDailyGoalSessions:

    def test_zero_progress_when_no_sessions(self, engine):
        svc = GamificationService()
        goal = svc.get_daily_goal_progress(user_id=1, goal_type="sessions")
        assert goal.achieved == 0
        assert not goal.completed
        assert goal.progress_pct == 0.0

    def test_completed_after_one_session_today(self, db_session):
        user = make_user(db_session, "GoalUser9_1")
        word = make_word(db_session, "GoalWord9_1")
        _make_analysed_session(
            db_session, user.id, word.id,
            completed_at=datetime.now(timezone.utc),
        )
        svc = GamificationService()
        goal = svc.get_daily_goal_progress(user.id, goal_type="sessions")
        assert goal.completed is True
        assert goal.achieved >= 1
        assert goal.progress_pct == 1.0

    def test_old_sessions_do_not_count_toward_today(self, db_session):
        user = make_user(db_session, "GoalUser9_2")
        now  = datetime.now(timezone.utc)
        word = make_word(db_session, "GoalWord9_2")
        _make_analysed_session(
            db_session, user.id, word.id,
            completed_at=now - timedelta(days=3),
        )
        svc = GamificationService()
        goal = svc.get_daily_goal_progress(user.id, goal_type="sessions")
        assert goal.achieved == 0
        assert not goal.completed

    def test_goal_type_is_correct(self, engine):
        svc = GamificationService()
        goal = svc.get_daily_goal_progress(user_id=1, goal_type="sessions")
        assert goal.goal_type == "sessions"
        assert goal.target == 1


# ===========================================================================
# 6. GamificationService — daily goal (minutes)
# ===========================================================================

class TestDailyGoalMinutes:

    def test_minutes_5_goal_met_with_5_minutes(self, db_session):
        user = make_user(db_session, "GoalMin9_1")
        word = make_word(db_session, "GoalMinWord9_1")
        # 300 seconds = 5 minutes
        _make_analysed_session(
            db_session, user.id, word.id,
            duration=300,
            completed_at=datetime.now(timezone.utc),
        )
        svc = GamificationService()
        goal = svc.get_daily_goal_progress(user.id, goal_type="minutes_5")
        assert goal.completed is True

    def test_minutes_10_goal_not_met_with_5_minutes(self, db_session):
        user = make_user(db_session, "GoalMin9_2")
        word = make_word(db_session, "GoalMinWord9_2")
        _make_analysed_session(
            db_session, user.id, word.id,
            duration=300,  # 5 minutes
            completed_at=datetime.now(timezone.utc),
        )
        svc = GamificationService()
        goal = svc.get_daily_goal_progress(user.id, goal_type="minutes_10")
        assert not goal.completed

    def test_goal_definitions_have_correct_targets(self):
        assert _GOAL_DEFS["sessions"][1]   == 1
        assert _GOAL_DEFS["minutes_5"][1]  == 5
        assert _GOAL_DEFS["minutes_10"][1] == 10


# ===========================================================================
# 7. GamificationService — personal best
# ===========================================================================

class TestPersonalBest:

    def test_none_when_no_sessions(self, engine):
        svc = GamificationService()
        pb = svc.get_personal_best(user_id=1)
        assert pb.best_overall_score is None
        assert pb.best_confidence_score is None

    def test_tracks_best_overall_score(self, db_session):
        user = make_user(db_session, "PBUser9_1")
        word1 = make_word(db_session, "PBWord9_1a")
        word2 = make_word(db_session, "PBWord9_1b")
        _make_analysed_session(db_session, user.id, word1.id, overall=70.0)
        _make_analysed_session(db_session, user.id, word2.id, overall=88.0)
        svc = GamificationService()
        pb = svc.get_personal_best(user.id)
        assert pb.best_overall_score == pytest.approx(88.0)

    def test_best_session_id_matches_highest_score(self, db_session):
        user  = make_user(db_session, "PBUser9_2")
        word1 = make_word(db_session, "PBWord9_2a")
        word2 = make_word(db_session, "PBWord9_2b")
        _make_analysed_session(db_session, user.id, word1.id, overall=60.0)
        sid_best = _make_analysed_session(db_session, user.id, word2.id, overall=92.0)
        svc = GamificationService()
        pb = svc.get_personal_best(user.id)
        assert pb.best_session_id == sid_best


# ===========================================================================
# 8. GamificationService — full snapshot
# ===========================================================================

class TestGamificationSnapshot:

    def test_snapshot_structure(self, db_session):
        user = make_user(db_session, "SnapUser9_1")
        word = make_word(db_session, "SnapWord9_1")
        _make_analysed_session(
            db_session, user.id, word.id,
            completed_at=datetime.now(timezone.utc),
        )
        svc = GamificationService()
        snap = svc.get_snapshot(user.id)
        assert isinstance(snap, GamificationSnapshot)
        assert isinstance(snap.streak, StreakInfo)
        assert isinstance(snap.daily_goal, DailyGoalProgress)
        assert isinstance(snap.personal_best, PersonalBest)
        assert isinstance(snap.achievements, list)

    def test_snapshot_total_sessions(self, db_session):
        user = make_user(db_session, "SnapUser9_2")
        for i in range(3):
            word = make_word(db_session, f"SnapWord9_2_{i}")
            _make_analysed_session(db_session, user.id, word.id)
        svc = GamificationService()
        snap = svc.get_snapshot(user.id)
        assert snap.total_sessions == 3

    def test_snapshot_topics_completed(self, db_session):
        user = make_user(db_session, "SnapUser9_3")
        for i in range(4):
            word = make_word(db_session, f"SnapWord9_3_{i}")
            _make_analysed_session(db_session, user.id, word.id)
        svc = GamificationService()
        snap = svc.get_snapshot(user.id)
        assert snap.topics_completed == 4


# ===========================================================================
# 9. GamificationService — preferences
# ===========================================================================

class TestGamificationPreferences:

    def test_get_preferences_returns_defaults(self, engine):
        svc = GamificationService()
        prefs = svc.get_preferences(user_id=1)
        assert "difficulty_preference" in prefs
        assert "daily_goal_type" in prefs

    def test_save_and_reload_difficulty(self, db_session):
        user = make_user(db_session, "PrefUser9_1")
        svc  = GamificationService()
        svc.save_preferences(user.id, difficulty_preference="hard")
        prefs = svc.get_preferences(user.id)
        assert prefs["difficulty_preference"] == "hard"

    def test_save_and_reload_goal(self, db_session):
        user = make_user(db_session, "PrefUser9_2")
        svc  = GamificationService()
        svc.save_preferences(user.id, daily_goal_type="minutes_10")
        prefs = svc.get_preferences(user.id)
        assert prefs["daily_goal_type"] == "minutes_10"

    def test_partial_save_preserves_other_fields(self, db_session):
        user = make_user(db_session, "PrefUser9_3")
        svc  = GamificationService()
        svc.save_preferences(user.id, difficulty_preference="easy",
                              daily_goal_type="minutes_5")
        svc.save_preferences(user.id, difficulty_preference="medium")
        prefs = svc.get_preferences(user.id)
        assert prefs["difficulty_preference"] == "medium"
        # goal should be unchanged
        assert prefs["daily_goal_type"] == "minutes_5"


# ===========================================================================
# 10. Settings — gamification config
# ===========================================================================

class TestGamificationSettings:

    def test_default_goal_is_valid(self):
        assert settings.GAMIFICATION_DEFAULT_GOAL in ("sessions", "minutes_5", "minutes_10")

    def test_default_difficulty_is_valid(self):
        assert settings.GAMIFICATION_DEFAULT_DIFFICULTY in ("all", "easy", "medium", "hard")

    def test_settings_are_strings(self):
        assert isinstance(settings.GAMIFICATION_DEFAULT_GOAL, str)
        assert isinstance(settings.GAMIFICATION_DEFAULT_DIFFICULTY, str)


# ===========================================================================
# 11. DashboardPage — gamification widgets
# ===========================================================================

class TestDashboardPageGamification:

    @pytest.fixture
    def page(self):
        from speakup.ui.pages.dashboard_page import DashboardPage
        return DashboardPage()

    def test_streak_label_exists(self, page):
        assert hasattr(page, "_streak_label")

    def test_pb_label_exists(self, page):
        assert hasattr(page, "_pb_label")

    def test_goal_bar_exists(self, page):
        assert hasattr(page, "_goal_bar")

    def test_goal_sublabel_exists(self, page):
        assert hasattr(page, "_goal_sublabel")

    def test_achievements_container_exists(self, page):
        assert hasattr(page, "_achievements_container")

    def test_load_gamification_does_not_raise(self, page):
        page._load_gamification()   # calls with no DB data — must not crash

    def test_load_achievements_empty_state(self, page):
        page._load_achievements([])   # must show empty state label

    def test_load_achievements_with_data_does_not_crash(self, page):
        from speakup.services.gamification_schemas import Achievement
        achievements = [
            Achievement("a", "First Speech", "D", "🎤", unlocked=True, threshold=1),
            Achievement("b", "5 Sessions",   "D", "✋", unlocked=False, threshold=5),
        ]
        page._load_achievements(achievements)


# ===========================================================================
# 12. SettingsPage — functional selectors
# ===========================================================================

class TestSettingsPageFunctional:

    @pytest.fixture
    def page(self):
        from speakup.ui.pages.settings_page import SettingsPage
        return SettingsPage(user_id=1, user_name="Test User")

    def test_preferences_saved_signal_exists(self, page):
        assert hasattr(page, "preferences_saved")

    def test_diff_combo_has_four_options(self, page):
        assert page._diff_combo.count() == 4

    def test_goal_combo_has_three_options(self, page):
        assert page._goal_combo.count() == 3

    def test_save_btn_exists(self, page):
        assert hasattr(page, "_save_btn")

    def test_refresh_does_not_raise(self, page):
        page.refresh()

    def test_difficulty_options_cover_all_levels(self, page):
        texts = [page._diff_combo.itemText(i) for i in range(page._diff_combo.count())]
        combined = " ".join(texts).lower()
        assert "all" in combined or "default" in combined
        assert "easy" in combined or "beginner" in combined
        assert "medium" in combined or "intermediate" in combined
        assert "hard" in combined or "advanced" in combined

    def test_goal_options_cover_all_types(self, page):
        texts = [page._goal_combo.itemText(i) for i in range(page._goal_combo.count())]
        combined = " ".join(texts).lower()
        assert "session" in combined
        assert "5" in combined
        assert "10" in combined


# ===========================================================================
# 13. Edge cases
# ===========================================================================

class TestGamificationEdgeCases:

    def test_snapshot_with_no_data_does_not_crash(self, engine):
        svc = GamificationService()
        snap = svc.get_snapshot(user_id=1)
        assert snap.total_sessions == 0
        assert snap.streak.current_streak == 0
        assert not snap.daily_goal.completed

    def test_goal_already_met_shows_complete(self, db_session):
        user = make_user(db_session, "EdgeUser9_1")
        word = make_word(db_session, "EdgeWord9_1")
        _make_analysed_session(
            db_session, user.id, word.id,
            completed_at=datetime.now(timezone.utc),
        )
        svc  = GamificationService()
        goal = svc.get_daily_goal_progress(user.id, goal_type="sessions")
        assert goal.completed is True
        assert goal.progress_pct == 1.0

    def test_achievements_return_all_thirteen(self, engine):
        svc = GamificationService()
        achievements = svc.get_achievements(user_id=1)
        assert len(achievements) == 13

    def test_achievement_keys_are_unique(self, engine):
        svc = GamificationService()
        achievements = svc.get_achievements(user_id=1)
        keys = [a.key for a in achievements]
        assert len(keys) == len(set(keys))

    def test_topics_10_unlocks_with_10_unique_words(self, db_session):
        user = make_user(db_session, "EdgeUser9_2")
        for i in range(10):
            word = make_word(db_session, f"EdgeWord9_2_{i}")
            _make_analysed_session(db_session, user.id, word.id)
        svc = GamificationService()
        achievements = svc.get_achievements(user.id)
        t10 = next(a for a in achievements if a.key == "topics_10")
        assert t10.unlocked is True

    def test_streak_7_achievement_unlocks_with_7_day_streak(self, db_session):
        user = make_user(db_session, "EdgeUser9_3")
        now  = datetime.now(timezone.utc)
        for days_ago in range(7):
            word = make_word(db_session, f"EdgeWord9_3_{days_ago}")
            _make_analysed_session(
                db_session, user.id, word.id,
                completed_at=now - timedelta(days=days_ago),
            )
        svc = GamificationService()
        achievements = svc.get_achievements(user.id)
        s7 = next(a for a in achievements if a.key == "streak_7")
        assert s7.unlocked is True
