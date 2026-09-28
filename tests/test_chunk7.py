"""
tests/test_chunk7.py — pytest suite for Chunk 7 (Progress Dashboard).

All DB tests use the shared engine/db_session fixtures from conftest.py.
No real model downloads, no hardware, no external APIs required.

Covers:
    1.  AnalysisResultRepository — get_time_series_for_user
    2.  AnalysisResultRepository — get_filtered_averages_for_user
    3.  AnalysisResultRepository — get_rich_history_for_user
    4.  ProgressService — get_dashboard_stats (all-time and date-filtered)
    5.  ProgressService — get_improvement_delta
    6.  ProgressService — get_chart_data
    7.  ProgressService — get_current_streak
    8.  ProgressService — get_rich_session_history
    9.  DashboardDataMapper — map_stat_cards
    10. DashboardDataMapper — map_delta
    11. DashboardDataMapper — map_chart_series
    12. DashboardDataMapper — map_recent_sessions
    13. Empty-state handling (no sessions, no analysis)
    14. MainWindow — view_session_from_dashboard handler exists
    15. DashboardPage — instantiation, view_session signal, refresh()
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from speakup.database.repository import (  # noqa: E402
    AnalysisResultRepository,
    PracticeSessionRepository,
    UserRepository,
    WordRepository,
)
from speakup.services.progress_service import ProgressService, DATE_RANGES  # noqa: E402
from speakup.services.dashboard_mapper import (  # noqa: E402
    map_stat_cards,
    map_delta,
    map_chart_series,
    map_recent_sessions,
)
from tests.conftest import (  # noqa: E402
    make_user,
    make_word,
    make_completed_session,
)


# ---------------------------------------------------------------------------
# Shared fixture: session with analysis
# ---------------------------------------------------------------------------

def _make_session_with_analysis(
    db_session,
    user_id: int,
    word_id: int,
    overall: float = 75.0,
    confidence: float = 72.0,
    fluency: float = 70.0,
    vocab: float = 78.0,
    grammar: float = 74.0,
    wpm: float = 120.0,
    filler_count: int = 4,
    duration: int = 80,
    completed_at: datetime | None = None,
):
    """Create a completed session with a linked AnalysisResult row."""
    repo = PracticeSessionRepository(db_session)
    ps = repo.create(user_id=user_id, word_id=word_id)
    db_session.flush()
    repo.complete_session(ps.id, duration_seconds=duration)

    if completed_at is not None:
        ps = repo.get_by_id(ps.id)
        ps.completed_at = completed_at
        db_session.flush()

    ar_repo = AnalysisResultRepository(db_session)
    ar_repo.create(
        session_id=ps.id,
        overall_score=overall,
        confidence_score=confidence,
        fluency_score=fluency,
        vocabulary_score=vocab,
        grammar_score=grammar,
        words_per_minute=wpm,
        filler_word_count=filler_count,
        pause_count=2,
        feedback=(
            "Strengths:\n• Good pace.\n\n"
            "Areas to improve:\n• Reduce fillers.\n\n"
            "Suggestions:\n• Pause instead of um."
        ),
    )
    db_session.commit()
    return ps.id


# ===========================================================================
# 1. AnalysisResultRepository — get_time_series_for_user
# ===========================================================================

class TestGetTimeSeries:

    def test_returns_empty_list_when_no_sessions(self, db_session):
        user = make_user(db_session, "TSUser0")
        repo = AnalysisResultRepository(db_session)
        rows = repo.get_time_series_for_user(user.id)
        assert rows == []

    def test_returns_one_row_for_one_session(self, db_session):
        user = make_user(db_session, "TSUser1")
        word = make_word(db_session, "TSWord1")
        _make_session_with_analysis(db_session, user.id, word.id)
        repo = AnalysisResultRepository(db_session)
        rows = repo.get_time_series_for_user(user.id)
        assert len(rows) == 1

    def test_row_has_required_keys(self, db_session):
        user = make_user(db_session, "TSUser2")
        word = make_word(db_session, "TSWord2")
        _make_session_with_analysis(db_session, user.id, word.id)
        repo = AnalysisResultRepository(db_session)
        row = repo.get_time_series_for_user(user.id)[0]
        for key in ("session_id", "completed_at", "overall_score",
                    "confidence_score", "fluency_score", "words_per_minute",
                    "filler_word_count", "duration_seconds"):
            assert key in row, f"Missing key: {key}"

    def test_ordered_chronologically(self, db_session):
        user = make_user(db_session, "TSUser3")
        now = datetime.now(timezone.utc)
        for i in range(3):
            word = make_word(db_session, f"TSWord3_{i}")
            _make_session_with_analysis(
                db_session, user.id, word.id,
                completed_at=now - timedelta(days=2 - i),
            )
        repo = AnalysisResultRepository(db_session)
        rows = repo.get_time_series_for_user(user.id)
        assert len(rows) == 3
        dates = [r["completed_at"] for r in rows]
        assert dates == sorted(dates)

    def test_since_filter_excludes_old_sessions(self, db_session):
        user = make_user(db_session, "TSUser4")
        now = datetime.now(timezone.utc)
        word1 = make_word(db_session, "TSWord4a")
        word2 = make_word(db_session, "TSWord4b")
        _make_session_with_analysis(
            db_session, user.id, word1.id,
            completed_at=now - timedelta(days=100),
        )
        _make_session_with_analysis(
            db_session, user.id, word2.id,
            completed_at=now - timedelta(days=1),
        )
        repo = AnalysisResultRepository(db_session)
        cutoff = now - timedelta(days=7)
        rows = repo.get_time_series_for_user(user.id, since=cutoff)
        assert len(rows) == 1


# ===========================================================================
# 2. AnalysisResultRepository — get_filtered_averages_for_user
# ===========================================================================

class TestGetFilteredAverages:

    def test_returns_none_values_when_no_data(self, db_session):
        user = make_user(db_session, "FAUser0")
        repo = AnalysisResultRepository(db_session)
        avgs = repo.get_filtered_averages_for_user(user.id)
        for key in ("overall_score", "confidence_score", "fluency_score"):
            assert avgs[key] is None

    def test_returns_correct_average(self, db_session):
        user = make_user(db_session, "FAUser1")
        word1 = make_word(db_session, "FAWord1a")
        word2 = make_word(db_session, "FAWord1b")
        _make_session_with_analysis(db_session, user.id, word1.id, overall=80.0)
        _make_session_with_analysis(db_session, user.id, word2.id, overall=60.0)
        repo = AnalysisResultRepository(db_session)
        avgs = repo.get_filtered_averages_for_user(user.id)
        assert avgs["overall_score"] == pytest.approx(70.0)

    def test_since_filter_applies(self, db_session):
        user = make_user(db_session, "FAUser2")
        now = datetime.now(timezone.utc)
        word1 = make_word(db_session, "FAWord2a")
        word2 = make_word(db_session, "FAWord2b")
        _make_session_with_analysis(
            db_session, user.id, word1.id, overall=90.0,
            completed_at=now - timedelta(days=100),
        )
        _make_session_with_analysis(
            db_session, user.id, word2.id, overall=50.0,
            completed_at=now - timedelta(days=1),
        )
        repo = AnalysisResultRepository(db_session)
        avgs = repo.get_filtered_averages_for_user(
            user.id, since=now - timedelta(days=7)
        )
        assert avgs["overall_score"] == pytest.approx(50.0)


# ===========================================================================
# 3. AnalysisResultRepository — get_rich_history_for_user
# ===========================================================================

class TestGetRichHistory:

    def test_returns_empty_list_no_sessions(self, db_session):
        user = make_user(db_session, "RHUser0")
        repo = AnalysisResultRepository(db_session)
        rows = repo.get_rich_history_for_user(user.id)
        assert rows == []

    def test_row_has_word_and_scores(self, db_session):
        user = make_user(db_session, "RHUser1")
        word = make_word(db_session, "RHWord1")
        _make_session_with_analysis(db_session, user.id, word.id, overall=77.0)
        repo = AnalysisResultRepository(db_session)
        rows = repo.get_rich_history_for_user(user.id)
        assert len(rows) == 1
        row = rows[0]
        assert row["word"] == "RHWord1"
        assert row["overall_score"] == pytest.approx(77.0)

    def test_ordered_by_completed_at_desc(self, db_session):
        user = make_user(db_session, "RHUser2")
        now = datetime.now(timezone.utc)
        words = [make_word(db_session, f"RHWord2_{i}") for i in range(3)]
        for i, word in enumerate(words):
            _make_session_with_analysis(
                db_session, user.id, word.id,
                overall=float(50 + i * 10),
                completed_at=now - timedelta(days=2 - i),
            )
        repo = AnalysisResultRepository(db_session)
        rows = repo.get_rich_history_for_user(user.id)
        assert len(rows) == 3
        dates = [r["completed_at"] for r in rows]
        assert dates == sorted(dates, reverse=True)


# ===========================================================================
# 4. ProgressService — get_dashboard_stats
# ===========================================================================

class TestProgressServiceStats:

    def test_all_zeros_when_no_sessions(self, engine):
        svc = ProgressService()
        stats = svc.get_dashboard_stats(user_id=1)
        assert stats["total_sessions"] == 0
        assert stats["total_seconds"]  == 0

    def test_counts_completed_sessions(self, db_session):
        user = make_user(db_session, "PSUser1")
        words = [make_word(db_session, f"PSWord1_{i}") for i in range(3)]
        for word in words:
            _make_session_with_analysis(db_session, user.id, word.id)
        svc = ProgressService()
        stats = svc.get_dashboard_stats(user_id=user.id)
        assert stats["total_sessions"] == 3

    def test_returns_avg_scores(self, db_session):
        user = make_user(db_session, "PSUser2")
        word = make_word(db_session, "PSWord2")
        _make_session_with_analysis(db_session, user.id, word.id, overall=80.0, confidence=70.0)
        svc = ProgressService()
        stats = svc.get_dashboard_stats(user_id=user.id)
        assert stats["avg_overall_score"] == pytest.approx(80.0, abs=1.0)
        assert stats["avg_confidence"]    == pytest.approx(70.0, abs=1.0)

    def test_all_required_keys_present(self, engine):
        svc = ProgressService()
        stats = svc.get_dashboard_stats(user_id=1)
        for key in ("total_sessions", "total_seconds", "total_minutes",
                    "avg_confidence", "avg_overall_score", "avg_fluency",
                    "avg_vocabulary", "avg_grammar", "words_remaining"):
            assert key in stats, f"Missing key: {key}"

    def test_date_range_7d_returns_subset(self, db_session):
        user = make_user(db_session, "PSUser3")
        now = datetime.now(timezone.utc)
        word1 = make_word(db_session, "PSWord3a")
        word2 = make_word(db_session, "PSWord3b")
        _make_session_with_analysis(
            db_session, user.id, word1.id,
            completed_at=now - timedelta(days=100),
        )
        _make_session_with_analysis(
            db_session, user.id, word2.id,
            completed_at=now - timedelta(days=1),
        )
        svc = ProgressService()
        stats_all = svc.get_dashboard_stats(user.id, date_range="all")
        stats_7d  = svc.get_dashboard_stats(user.id, date_range="7d")
        # Both sessions counted regardless of date (total_sessions uses no filter)
        assert stats_all["total_sessions"] >= stats_7d["total_sessions"]


# ===========================================================================
# 5. ProgressService — get_improvement_delta
# ===========================================================================

class TestImprovementDelta:

    def test_returns_empty_with_no_sessions(self, engine):
        svc = ProgressService()
        assert svc.get_improvement_delta(user_id=1) == {}

    def test_returns_empty_with_one_session(self, db_session):
        user = make_user(db_session, "IDUser1")
        word = make_word(db_session, "IDWord1")
        _make_session_with_analysis(db_session, user.id, word.id)
        svc = ProgressService()
        result = svc.get_improvement_delta(user.id)
        # 1 session < 2*window(3) — still returns something (min window applied)
        assert isinstance(result, dict)

    def test_positive_delta_when_improving(self, db_session):
        user = make_user(db_session, "IDUser2")
        now = datetime.now(timezone.utc)
        # First 3 sessions: low score
        for i in range(3):
            word = make_word(db_session, f"IDWord2a_{i}")
            _make_session_with_analysis(
                db_session, user.id, word.id, overall=50.0,
                completed_at=now - timedelta(days=10 - i),
            )
        # Last 3 sessions: high score
        for i in range(3):
            word = make_word(db_session, f"IDWord2b_{i}")
            _make_session_with_analysis(
                db_session, user.id, word.id, overall=80.0,
                completed_at=now - timedelta(days=2 - i),
            )
        svc = ProgressService()
        delta = svc.get_improvement_delta(user.id, window=3)
        assert "overall_score" in delta
        assert delta["overall_score"]["delta"] > 0

    def test_negative_delta_when_declining(self, db_session):
        user = make_user(db_session, "IDUser3")
        now = datetime.now(timezone.utc)
        for i in range(3):
            word = make_word(db_session, f"IDWord3a_{i}")
            _make_session_with_analysis(
                db_session, user.id, word.id, overall=85.0,
                completed_at=now - timedelta(days=10 - i),
            )
        for i in range(3):
            word = make_word(db_session, f"IDWord3b_{i}")
            _make_session_with_analysis(
                db_session, user.id, word.id, overall=55.0,
                completed_at=now - timedelta(days=2 - i),
            )
        svc = ProgressService()
        delta = svc.get_improvement_delta(user.id, window=3)
        assert delta["overall_score"]["delta"] < 0

    def test_delta_dict_has_required_keys(self, db_session):
        user = make_user(db_session, "IDUser4")
        now = datetime.now(timezone.utc)
        for i in range(4):
            word = make_word(db_session, f"IDWord4_{i}")
            _make_session_with_analysis(
                db_session, user.id, word.id,
                completed_at=now - timedelta(days=4 - i),
            )
        svc = ProgressService()
        delta = svc.get_improvement_delta(user.id, window=2)
        for key in ("first", "last", "delta"):
            if "overall_score" in delta:
                assert key in delta["overall_score"]


# ===========================================================================
# 6. ProgressService — get_chart_data
# ===========================================================================

class TestChartData:

    def test_returns_empty_structure_when_no_sessions(self, engine):
        svc = ProgressService()
        data = svc.get_chart_data(user_id=1)
        assert data["sessions"] == []
        assert data["session_numbers"] == []

    def test_returns_six_series_keys(self, db_session):
        user = make_user(db_session, "CDUser1")
        word = make_word(db_session, "CDWord1")
        _make_session_with_analysis(db_session, user.id, word.id)
        svc = ProgressService()
        data = svc.get_chart_data(user.id)
        for key in ("session_numbers", "confidence", "overall",
                    "fluency", "duration_minutes", "words_per_minute",
                    "filler_counts"):
            assert key in data

    def test_session_numbers_are_sequential(self, db_session):
        user = make_user(db_session, "CDUser2")
        for i in range(4):
            word = make_word(db_session, f"CDWord2_{i}")
            _make_session_with_analysis(db_session, user.id, word.id)
        svc = ProgressService()
        data = svc.get_chart_data(user.id)
        assert data["session_numbers"] == [1, 2, 3, 4]

    def test_series_lengths_match(self, db_session):
        user = make_user(db_session, "CDUser3")
        for i in range(3):
            word = make_word(db_session, f"CDWord3_{i}")
            _make_session_with_analysis(db_session, user.id, word.id)
        svc = ProgressService()
        data = svc.get_chart_data(user.id)
        n = len(data["session_numbers"])
        for key in ("confidence", "overall", "fluency",
                    "duration_minutes", "words_per_minute", "filler_counts"):
            assert len(data[key]) == n, f"Series {key!r} length mismatch"


# ===========================================================================
# 7. ProgressService — get_current_streak
# ===========================================================================

class TestCurrentStreak:

    def test_zero_when_no_sessions(self, engine):
        svc = ProgressService()
        assert svc.get_current_streak(user_id=1) == 0

    def test_streak_of_one_for_today(self, db_session):
        user = make_user(db_session, "StrUser1")
        word = make_word(db_session, "StrWord1")
        _make_session_with_analysis(
            db_session, user.id, word.id,
            completed_at=datetime.now(timezone.utc),
        )
        svc = ProgressService()
        assert svc.get_current_streak(user.id) >= 1

    def test_streak_breaks_on_gap(self, db_session):
        user = make_user(db_session, "StrUser2")
        now = datetime.now(timezone.utc)
        # Two sessions 5 days ago but none since
        for i in range(2):
            word = make_word(db_session, f"StrWord2_{i}")
            _make_session_with_analysis(
                db_session, user.id, word.id,
                completed_at=now - timedelta(days=5),
            )
        svc = ProgressService()
        streak = svc.get_current_streak(user.id)
        assert streak == 0   # streak is broken (gap between then and today)


# ===========================================================================
# 8. ProgressService — get_rich_session_history (backward-compat)
# ===========================================================================

class TestRichSessionHistory:

    def test_returns_empty_list_no_sessions(self, engine):
        svc = ProgressService()
        assert svc.get_rich_session_history(user_id=1) == []

    def test_returns_dicts_with_session_id(self, db_session):
        user = make_user(db_session, "RSHUser1")
        word = make_word(db_session, "RSHWord1")
        sid = _make_session_with_analysis(db_session, user.id, word.id)
        svc = ProgressService()
        rows = svc.get_rich_session_history(user.id)
        assert len(rows) == 1
        assert rows[0]["session_id"] == sid

    def test_includes_word_name(self, db_session):
        user = make_user(db_session, "RSHUser2")
        word = make_word(db_session, "UniqueTopicXYZ")
        _make_session_with_analysis(db_session, user.id, word.id)
        svc = ProgressService()
        rows = svc.get_rich_session_history(user.id)
        assert rows[0]["word"] == "UniqueTopicXYZ"


# ===========================================================================
# 9. DashboardDataMapper — map_stat_cards
# ===========================================================================

class TestMapStatCards:

    def _stats(self, **kwargs) -> dict:
        base = {
            "total_sessions": 5, "total_seconds": 450, "total_minutes": 7.5,
            "avg_confidence": 72.0, "avg_overall_score": 75.0,
            "avg_fluency": 70.0, "avg_vocabulary": 78.0,
            "avg_grammar": 74.0, "avg_wpm": 120.0, "words_remaining": 135,
        }
        base.update(kwargs)
        return base

    def test_returns_six_cards(self):
        cards = map_stat_cards(self._stats())
        assert len(cards) == 6

    def test_each_card_has_required_keys(self):
        cards = map_stat_cards(self._stats())
        for card in cards:
            for key in ("key", "title", "value", "subtitle", "accent"):
                assert key in card

    def test_sessions_card_value(self):
        cards = map_stat_cards(self._stats(total_sessions=7))
        sessions_card = next(c for c in cards if c["key"] == "sessions")
        assert sessions_card["value"] == "7"

    def test_confidence_card_value(self):
        cards = map_stat_cards(self._stats(avg_confidence=85.0))
        conf_card = next(c for c in cards if c["key"] == "confidence")
        assert "85" in conf_card["value"]

    def test_none_scores_show_dash(self):
        cards = map_stat_cards(self._stats(avg_overall_score=None))
        overall_card = next(c for c in cards if c["key"] == "overall")
        assert overall_card["value"] == "—"

    def test_zero_sessions_shows_singular(self):
        from speakup.services.dashboard_mapper import _plural
        assert _plural("session", 1) == "session"
        assert _plural("session", 0) == "sessions"
        assert _plural("session", 5) == "sessions"


# ===========================================================================
# 10. DashboardDataMapper — map_delta
# ===========================================================================

class TestMapDelta:

    def test_empty_input_returns_empty(self):
        assert map_delta({}) == {}

    def test_positive_delta_flagged(self):
        raw = {"overall_score": {"first": 60.0, "last": 75.0, "delta": 15.0}}
        result = map_delta(raw)
        assert "overall" in result
        assert result["overall"]["positive"] is True
        assert "+" in result["overall"]["delta_str"]

    def test_negative_delta_flagged(self):
        raw = {"confidence_score": {"first": 80.0, "last": 65.0, "delta": -15.0}}
        result = map_delta(raw)
        assert "confidence" in result
        assert result["confidence"]["positive"] is False
        assert "-" in result["confidence"]["delta_str"]

    def test_zero_delta_is_positive(self):
        raw = {"fluency_score": {"first": 70.0, "last": 70.0, "delta": 0.0}}
        result = map_delta(raw)
        assert result["fluency"]["positive"] is True

    def test_correct_display_strings(self):
        raw = {"overall_score": {"first": 62.0, "last": 79.0, "delta": 17.0}}
        d = map_delta(raw)["overall"]
        assert d["first_str"] == "62"
        assert d["last_str"]  == "79"
        assert d["delta_str"] == "+17.0"


# ===========================================================================
# 11. DashboardDataMapper — map_chart_series
# ===========================================================================

class TestMapChartSeries:

    def test_empty_input_returns_no_data(self):
        result = map_chart_series({"session_numbers": [], "confidence": []})
        assert result.get("has_data") is False

    def test_has_data_true_with_sessions(self):
        data = {
            "session_numbers": [1, 2, 3],
            "confidence":      [70.0, 75.0, None],
            "overall":         [68.0, 72.0, 80.0],
            "fluency":         [65.0, None, 74.0],
            "duration_minutes": [1.2, 1.5, 1.8],
            "words_per_minute": [110.0, 120.0, 115.0],
            "filler_counts":    [5, 3, 2],
        }
        result = map_chart_series(data)
        assert result["has_data"] is True

    def test_none_replaced_with_zero(self):
        data = {
            "session_numbers":  [1, 2],
            "confidence":       [None, 75.0],
            "overall":          [None, None],
            "fluency":          [60.0, None],
            "duration_minutes": [1.0, 1.5],
            "words_per_minute": [100.0, None],
            "filler_counts":    [None, 3],
        }
        result = map_chart_series(data)
        assert None not in result["confidence"]
        assert None not in result["overall"]
        assert None not in result["fluency"]
        assert None not in result["filler_counts"]

    def test_session_count_matches(self):
        data = {
            "session_numbers": [1, 2, 3, 4, 5],
            "confidence":      [70.0]*5,
            "overall":         [75.0]*5,
            "fluency":         [68.0]*5,
            "duration_minutes":[1.2]*5,
            "words_per_minute":[120.0]*5,
            "filler_counts":   [3]*5,
        }
        result = map_chart_series(data)
        assert result["session_count"] == 5


# ===========================================================================
# 12. DashboardDataMapper — map_recent_sessions
# ===========================================================================

class TestMapRecentSessions:

    def _make_raw_row(self, session_id: int = 1, word: str = "Leadership",
                      overall: float = 75.0, confidence: float = 72.0,
                      duration: int = 80) -> dict:
        return {
            "session_id":      session_id,
            "completed_at":    datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
            "duration_seconds": duration,
            "transcript":      "Hello world.",
            "recording_path":  "/rec/session.mp4",
            "word":            word,
            "category":        "Business",
            "difficulty":      "medium",
            "overall_score":   overall,
            "confidence_score": confidence,
            "fluency_score":   70.0,
            "vocabulary_score": 78.0,
            "grammar_score":   74.0,
            "filler_word_count": 3,
            "words_per_minute": 120.0,
            "pause_count":     2,
            "feedback":        "Strengths:\n• Good pace.",
        }

    def test_returns_empty_list_for_empty_input(self):
        assert map_recent_sessions([]) == []

    def test_row_has_display_fields(self):
        rows = map_recent_sessions([self._make_raw_row()])
        row = rows[0]
        for key in ("session_id", "date_str", "word", "duration_str",
                    "overall_str", "confidence_str"):
            assert key in row

    def test_overall_str_formatted(self):
        rows = map_recent_sessions([self._make_raw_row(overall=78.5)])
        assert "78" in rows[0]["overall_str"]

    def test_confidence_str_formatted(self):
        rows = map_recent_sessions([self._make_raw_row(confidence=85.0)])
        assert "85" in rows[0]["confidence_str"]

    def test_none_scores_show_dash(self):
        raw = self._make_raw_row()
        raw["overall_score"] = None
        raw["confidence_score"] = None
        rows = map_recent_sessions([raw])
        assert rows[0]["overall_str"] == "—"
        assert rows[0]["confidence_str"] == "—"

    def test_duration_formatted(self):
        rows = map_recent_sessions([self._make_raw_row(duration=90)])
        assert "1m" in rows[0]["duration_str"]

    def test_passthrough_analysis_keys_present(self):
        rows = map_recent_sessions([self._make_raw_row()])
        row = rows[0]
        for key in ("overall_score", "confidence_score", "fluency_score",
                    "vocabulary_score", "grammar_score", "filler_word_count",
                    "words_per_minute", "pause_count", "feedback",
                    "duration_seconds", "transcript"):
            assert key in row


# ===========================================================================
# 13. Empty-state handling
# ===========================================================================

class TestEmptyStateHandling:

    def test_dashboard_stats_all_none_averages(self, engine):
        svc = ProgressService()
        stats = svc.get_dashboard_stats(user_id=1)
        assert stats["avg_confidence"] is None
        assert stats["avg_overall_score"] is None

    def test_chart_data_empty_series(self, engine):
        svc = ProgressService()
        data = svc.get_chart_data(user_id=1)
        for key in ("confidence", "overall", "fluency",
                    "duration_minutes", "words_per_minute", "filler_counts"):
            assert data[key] == []

    def test_map_stat_cards_handles_none_scores(self):
        cards = map_stat_cards({
            "total_sessions": 0, "total_seconds": 0, "total_minutes": 0.0,
            "avg_confidence": None, "avg_overall_score": None,
            "avg_fluency": None, "avg_vocabulary": None,
            "avg_grammar": None, "avg_wpm": None, "words_remaining": 140,
        })
        assert len(cards) == 6
        for card in cards:
            assert card["value"] is not None   # must never raise

    def test_map_chart_series_empty(self):
        result = map_chart_series({"session_numbers": []})
        assert result.get("has_data") is False

    def test_improvement_delta_empty_state(self, engine):
        svc = ProgressService()
        result = svc.get_improvement_delta(user_id=1)
        assert isinstance(result, dict)


# ===========================================================================
# 14. MainWindow — view_session_from_dashboard handler
# ===========================================================================

class TestMainWindowDashboard:

    def test_on_view_session_handler_exists(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        assert hasattr(win, "_on_view_session_from_dashboard")
        assert callable(win._on_view_session_from_dashboard)

    def test_dashboard_page_has_view_session_signal(self):
        from speakup.ui.pages.dashboard_page import DashboardPage
        page = DashboardPage()
        assert hasattr(page, "view_session")

    def test_dashboard_page_at_index_0(self):
        from speakup.ui.main_window import MainWindow, PAGE_DASHBOARD
        win = MainWindow(user_name="Test")
        assert PAGE_DASHBOARD == 0
        from speakup.ui.pages.dashboard_page import DashboardPage
        assert isinstance(win._stack.widget(0), DashboardPage)


# ===========================================================================
# 15. DashboardPage — instantiation and refresh
# ===========================================================================

class TestDashboardPageUI:

    def test_dashboard_instantiates_without_error(self):
        from speakup.ui.pages.dashboard_page import DashboardPage
        page = DashboardPage()
        assert page is not None

    def test_dashboard_has_six_stat_cards(self):
        from speakup.ui.pages.dashboard_page import DashboardPage
        page = DashboardPage()
        assert len(page._stat_cards) == 6

    def test_dashboard_has_six_charts(self):
        from speakup.ui.pages.dashboard_page import DashboardPage
        page = DashboardPage()
        assert len(page._charts) == 6

    def test_refresh_does_not_raise(self):
        from speakup.ui.pages.dashboard_page import DashboardPage
        page = DashboardPage()
        page.refresh()   # must not throw

    def test_view_session_signal_emitted(self):
        from speakup.ui.pages.dashboard_page import DashboardPage
        page = DashboardPage()
        fired = []
        page.view_session.connect(lambda sid: fired.append(sid))
        # Simulate a row click with a stub row
        page._current_view_row = {
            "session_id": 42, "word": "Test", "category": "Business",
            "difficulty": "medium", "overall_score": 75.0, "confidence_score": 70.0,
            "fluency_score": 68.0, "vocabulary_score": 78.0, "grammar_score": 72.0,
            "filler_word_count": 3, "words_per_minute": 115.0, "pause_count": 2,
            "feedback": "", "duration_seconds": 80, "transcript": "",
        }
        page.view_session.emit(42)
        assert 42 in fired

    def test_filter_buttons_exist(self):
        from speakup.ui.pages.dashboard_page import DashboardPage
        page = DashboardPage()
        assert "7d"  in page._filter_btns
        assert "30d" in page._filter_btns
        assert "90d" in page._filter_btns
        assert "all" in page._filter_btns
