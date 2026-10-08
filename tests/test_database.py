"""
tests/test_database.py — pytest suite for Chunk 1 database layer.

Tests cover:
    - Database initialisation (tables created, default user seeded)
    - UserRepository  CRUD
    - WordRepository  CRUD + unused-word queries
    - PracticeSessionRepository  CRUD + status updates
    - WordUsageRepository  mark_used + no-repeat constraint
    - AnalysisResultRepository  create + queries
    - Helper functions (format_duration, format_score, etc.)

Fixtures are provided by tests/conftest.py (engine, db_session).
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError

from speakup.database.connection import init_db, get_session, reset_engine
from speakup.database.models import (
    AnalysisResult,
    PracticeSession,
    SessionStatus,
    User,
    Word,
    WordUsage,
)
from speakup.database.repository import (
    AnalysisResultRepository,
    PracticeSessionRepository,
    UserRepository,
    WordRepository,
    WordUsageRepository,
)
# Shared factory helpers from conftest
from tests.conftest import make_user, make_word, make_session


# ---------------------------------------------------------------------------
# Local aliases (keep existing test code unchanged)
# ---------------------------------------------------------------------------
_make_user    = make_user
_make_word    = make_word
_make_session = make_session


# ===========================================================================
# 1. Database initialisation
# ===========================================================================

class TestDatabaseInitialisation:

    def test_tables_created(self, engine):
        """All five expected tables exist after init_db()."""
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "users" in tables
        assert "words" in tables
        assert "practice_sessions" in tables
        assert "word_usage" in tables
        assert "analysis_results" in tables
        assert "user_credentials" in tables

    def test_default_user_seeded(self, db_session):
        """A default 'Local User' is created automatically on first init."""
        repo = UserRepository(db_session)
        count = repo.count()
        assert count == 1
        user = repo.get_first()
        assert user is not None
        assert user.name == "Local User"

    def test_init_db_idempotent(self, engine):
        """Calling init_db() again on an existing DB does not raise or duplicate data."""
        init_db("sqlite:///:memory:")  # already initialised via fixture
        # Just verify no exception is raised

    def test_init_db_can_skip_default_user_for_signup(self, engine):
        init_db("sqlite:///:memory:", seed_default_user=False)
        with get_session() as session:
            assert UserRepository(session).count() == 0


# ===========================================================================
# 2. UserRepository
# ===========================================================================

class TestUserRepository:

    def test_create_user(self, db_session):
        """Creating a user returns an instance with an assigned id."""
        repo = UserRepository(db_session)
        user = repo.create("Alice")
        db_session.commit()
        assert user.id is not None
        assert user.name == "Alice"

    def test_get_by_id(self, db_session):
        user = _make_user(db_session, "Bob")
        repo = UserRepository(db_session)
        fetched = repo.get_by_id(user.id)
        assert fetched is not None
        assert fetched.name == "Bob"

    def test_get_by_id_missing(self, db_session):
        repo = UserRepository(db_session)
        assert repo.get_by_id(9999) is None

    def test_get_all(self, db_session):
        # Default user already exists; add two more
        _make_user(db_session, "Carol")
        _make_user(db_session, "Dave")
        repo = UserRepository(db_session)
        users = repo.get_all()
        assert len(users) >= 3

    def test_get_first(self, db_session):
        repo = UserRepository(db_session)
        user = repo.get_first()
        assert user is not None  # default user seeded by init_db

    def test_count(self, db_session):
        repo = UserRepository(db_session)
        initial = repo.count()
        _make_user(db_session, "Eve")
        assert repo.count() == initial + 1

    def test_update_name(self, db_session):
        user = _make_user(db_session, "Frank")
        repo = UserRepository(db_session)
        updated = repo.update_name(user.id, "Frank Jr.")
        db_session.commit()
        assert updated is not None
        assert updated.name == "Frank Jr."

    def test_update_name_missing(self, db_session):
        repo = UserRepository(db_session)
        result = repo.update_name(9999, "Nobody")
        assert result is None

    def test_delete_user(self, db_session):
        user = _make_user(db_session, "Grace")
        repo = UserRepository(db_session)
        deleted = repo.delete(user.id)
        db_session.commit()
        assert deleted is True
        assert repo.get_by_id(user.id) is None

    def test_delete_missing_user(self, db_session):
        repo = UserRepository(db_session)
        assert repo.delete(9999) is False


class TestAuthenticationService:

    def test_register_and_login_uses_password_hash(self, db_session):
        from speakup.services.auth_service import AuthenticationService
        from speakup.database.repository import UserCredentialRepository

        auth = AuthenticationService()
        account = auth.register("Auth User", "Auth@Example.com", "GoodPassword123")

        credential = UserCredentialRepository(db_session).get_by_email(
            "auth@example.com"
        )
        assert credential is not None
        assert credential.password_hash != "GoodPassword123"
        assert credential.password_hash.startswith("pbkdf2_sha256$")
        assert auth.login("AUTH@example.com", "GoodPassword123") == account
        assert auth.login("auth@example.com", "IncorrectPassword") is None

    def test_register_claims_existing_profile_and_keeps_history(self, db_session):
        from speakup.services.auth_service import AuthenticationService

        user = _make_user(db_session, "Existing Local User")
        word = _make_word(db_session, "Existing User Topic")
        _make_session(db_session, user.id, word.id)

        account = AuthenticationService().register(
            "Existing Local User", "legacy@example.com", "GoodPassword123"
        )

        assert account == (user.id, user.name)
        assert PracticeSessionRepository(db_session).count_by_user(user.id) == 1

    def test_register_rejects_duplicate_email(self, db_session):
        from speakup.services.auth_service import AuthenticationService

        auth = AuthenticationService()
        auth.register("First User", "shared@example.com", "GoodPassword123")
        with pytest.raises(ValueError, match="already exists"):
            auth.register("Second User", "SHARED@example.com", "OtherPassword123")

    def test_register_rejects_short_password(self):
        from speakup.services.auth_service import AuthenticationService

        with pytest.raises(ValueError, match="10 and 128"):
            AuthenticationService().register(
                "Short Password", "short@example.com", "too-short"
            )

    def test_clear_profile_data_preserves_other_profiles(self, db_session):
        from speakup.database.repository import UserPreferencesRepository

        repo = UserRepository(db_session)
        first = repo.create("First profile")
        second = repo.create("Second profile")
        first_word = _make_word(db_session, "First profile topic")
        second_word = _make_word(db_session, "Second profile topic")
        _make_session(db_session, first.id, first_word.id)
        _make_session(db_session, second.id, second_word.id)
        WordUsageRepository(db_session).mark_used(first.id, first_word.id)
        WordUsageRepository(db_session).mark_used(second.id, second_word.id)
        UserPreferencesRepository(db_session).get_or_create(first.id)
        UserPreferencesRepository(db_session).get_or_create(second.id)
        db_session.commit()

        assert repo.clear_profile_data(first.id) is True
        db_session.commit()

        assert repo.get_by_id(first.id) is not None
        assert PracticeSessionRepository(db_session).get_by_user(first.id) == []
        assert WordUsageRepository(db_session).count_used_for_user(first.id) == 0
        assert PracticeSessionRepository(db_session).get_by_user(second.id)
        assert WordUsageRepository(db_session).count_used_for_user(second.id) == 1


# ===========================================================================
# 3. WordRepository
# ===========================================================================

class TestWordRepository:

    def test_create_word(self, db_session):
        repo = WordRepository(db_session)
        word = repo.create("Teamwork", category="Business", difficulty="easy")
        db_session.commit()
        assert word.id is not None
        assert word.word == "Teamwork"
        assert word.category == "Business"
        assert word.difficulty == "easy"

    def test_get_by_id(self, db_session):
        word = _make_word(db_session, "Travel")
        repo = WordRepository(db_session)
        fetched = repo.get_by_id(word.id)
        assert fetched is not None
        assert fetched.word == "Travel"

    def test_get_by_word(self, db_session):
        _make_word(db_session, "Creativity")
        repo = WordRepository(db_session)
        w = repo.get_by_word("Creativity")
        assert w is not None
        assert w.word == "Creativity"

    def test_get_by_word_missing(self, db_session):
        repo = WordRepository(db_session)
        assert repo.get_by_word("NonExistentWord") is None

    def test_get_all(self, db_session):
        repo = WordRepository(db_session)
        initial = repo.count()
        _make_word(db_session, "Innovation")
        _make_word(db_session, "Resilience")
        assert repo.count() == initial + 2

    def test_get_by_category(self, db_session):
        repo = WordRepository(db_session)
        repo.create("Blockchain", category="Technology", difficulty="hard")
        repo.create("Machine Learning", category="Technology", difficulty="hard")
        repo.create("Cooking", category="Lifestyle", difficulty="easy")
        db_session.commit()
        tech_words = repo.get_by_category("Technology")
        assert len(tech_words) >= 2
        assert all(w.category == "Technology" for w in tech_words)

    def test_get_by_difficulty(self, db_session):
        repo = WordRepository(db_session)
        repo.create("Simple Topic", category="General", difficulty="easy")
        db_session.commit()
        easy_words = repo.get_by_difficulty("easy")
        assert len(easy_words) >= 1
        assert all(w.difficulty == "easy" for w in easy_words)

    def test_bulk_create(self, db_session):
        repo = WordRepository(db_session)
        data = [
            {"word": "BulkWord1", "category": "Test", "difficulty": "easy"},
            {"word": "BulkWord2", "category": "Test", "difficulty": "medium"},
            {"word": "BulkWord3", "category": "Test", "difficulty": "hard"},
        ]
        created = repo.bulk_create(data)
        db_session.commit()
        assert len(created) == 3

    def test_bulk_create_skips_duplicates(self, db_session):
        repo = WordRepository(db_session)
        _make_word(db_session, "DuplicateWord")
        data = [
            {"word": "DuplicateWord", "category": "Test", "difficulty": "easy"},
            {"word": "UniqueWord999", "category": "Test", "difficulty": "easy"},
        ]
        created = repo.bulk_create(data)
        db_session.commit()
        # Only the non-duplicate should be created
        assert len(created) == 1
        assert created[0].word == "UniqueWord999"

    def test_get_unused_for_user(self, db_session):
        user = _make_user(db_session, "Henry")
        word1 = _make_word(db_session, "WordA")
        word2 = _make_word(db_session, "WordB")
        word3 = _make_word(db_session, "WordC")

        # Mark word1 as used
        usage_repo = WordUsageRepository(db_session)
        usage_repo.mark_used(user.id, word1.id)
        db_session.commit()

        word_repo = WordRepository(db_session)
        unused = word_repo.get_unused_for_user(user.id)
        unused_ids = {w.id for w in unused}

        assert word1.id not in unused_ids
        assert word2.id in unused_ids
        assert word3.id in unused_ids

    def test_get_random_unused_for_user(self, db_session):
        user = _make_user(db_session, "Ivy")
        _make_word(db_session, "RandomWord1")
        _make_word(db_session, "RandomWord2")

        repo = WordRepository(db_session)
        word = repo.get_random_unused_for_user(user.id)
        assert word is not None

    def test_get_random_unused_returns_none_when_exhausted(self, db_session):
        user = _make_user(db_session, "Jack")
        word = _make_word(db_session, "OnlyWord")

        usage_repo = WordUsageRepository(db_session)
        usage_repo.mark_used(user.id, word.id)
        db_session.commit()

        word_repo = WordRepository(db_session)
        result = word_repo.get_random_unused_for_user(user.id)
        assert result is None

    def test_delete_word(self, db_session):
        word = _make_word(db_session, "DeleteMe")
        repo = WordRepository(db_session)
        deleted = repo.delete(word.id)
        db_session.commit()
        assert deleted is True
        assert repo.get_by_id(word.id) is None


# ===========================================================================
# 4. PracticeSessionRepository
# ===========================================================================

class TestPracticeSessionRepository:

    def test_create_session(self, db_session):
        user = _make_user(db_session, "Karen")
        word = _make_word(db_session, "Confidence")
        repo = PracticeSessionRepository(db_session)
        ps = repo.create(user_id=user.id, word_id=word.id)
        db_session.commit()
        assert ps.id is not None
        assert ps.user_id == user.id
        assert ps.word_id == word.id
        assert ps.status == SessionStatus.PENDING

    def test_get_by_id(self, db_session):
        user = _make_user(db_session, "Leo")
        word = _make_word(db_session, "Motivation")
        ps = _make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        fetched = repo.get_by_id(ps.id)
        assert fetched is not None
        assert fetched.id == ps.id

    def test_get_by_user(self, db_session):
        user = _make_user(db_session, "Mia")
        word1 = _make_word(db_session, "WordX")
        word2 = _make_word(db_session, "WordY")
        _make_session(db_session, user.id, word1.id)
        _make_session(db_session, user.id, word2.id)
        repo = PracticeSessionRepository(db_session)
        sessions = repo.get_by_user(user.id)
        assert len(sessions) == 2

    def test_update_status_to_completed(self, db_session):
        user = _make_user(db_session, "Noah")
        word = _make_word(db_session, "Progress")
        ps = _make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        updated = repo.update_status(
            ps.id,
            status="completed",
            completed_at=datetime.now(timezone.utc),
            duration_seconds=90,
        )
        db_session.commit()
        assert updated is not None
        assert updated.status == "completed"
        assert updated.duration_seconds == 90

    def test_count_by_user(self, db_session):
        user = _make_user(db_session, "Olivia")
        word = _make_word(db_session, "Discipline")
        ps = _make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        repo.update_status(ps.id, status="completed")
        db_session.commit()
        assert repo.count_by_user(user.id, status="completed") == 1
        assert repo.count_by_user(user.id, status="pending") == 0

    def test_get_total_duration(self, db_session):
        user = _make_user(db_session, "Peter")
        word1 = _make_word(db_session, "WordP1")
        word2 = _make_word(db_session, "WordP2")
        ps1 = _make_session(db_session, user.id, word1.id)
        ps2 = _make_session(db_session, user.id, word2.id)
        repo = PracticeSessionRepository(db_session)
        repo.update_status(ps1.id, status="completed", duration_seconds=60)
        repo.update_status(ps2.id, status="completed", duration_seconds=90)
        db_session.commit()
        total = repo.get_total_duration_by_user(user.id)
        assert total == 150

    def test_delete_session(self, db_session):
        user = _make_user(db_session, "Quinn")
        word = _make_word(db_session, "Challenge")
        ps = _make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        deleted = repo.delete(ps.id)
        db_session.commit()
        assert deleted is True
        assert repo.get_by_id(ps.id) is None


# ===========================================================================
# 5. WordUsageRepository — no-repeat constraint
# ===========================================================================

class TestWordUsageRepository:

    def test_mark_used(self, db_session):
        user = _make_user(db_session, "Rachel")
        word = _make_word(db_session, "Persistence")
        repo = WordUsageRepository(db_session)
        wu = repo.mark_used(user.id, word.id)
        db_session.commit()
        assert wu.id is not None
        assert wu.user_id == user.id
        assert wu.word_id == word.id

    def test_has_used_true(self, db_session):
        user = _make_user(db_session, "Sam")
        word = _make_word(db_session, "Endurance")
        repo = WordUsageRepository(db_session)
        repo.mark_used(user.id, word.id)
        db_session.commit()
        assert repo.has_used(user.id, word.id) is True

    def test_has_used_false(self, db_session):
        user = _make_user(db_session, "Tina")
        word = _make_word(db_session, "FreshWord")
        repo = WordUsageRepository(db_session)
        assert repo.has_used(user.id, word.id) is False

    def test_no_repeat_constraint(self, db_session):
        """Marking the same word used twice for the same user raises IntegrityError."""
        user = _make_user(db_session, "Uma")
        word = _make_word(db_session, "UniqueUse")
        repo = WordUsageRepository(db_session)
        repo.mark_used(user.id, word.id)
        db_session.commit()

        # Second attempt must fail
        with pytest.raises(IntegrityError):
            repo.mark_used(user.id, word.id)

    def test_same_word_different_users_allowed(self, db_session):
        """Two different users can independently use the same word."""
        user1 = _make_user(db_session, "Victor")
        user2 = _make_user(db_session, "Wendy")
        word = _make_word(db_session, "SharedWord")

        repo = WordUsageRepository(db_session)
        wu1 = repo.mark_used(user1.id, word.id)
        db_session.commit()
        wu2 = repo.mark_used(user2.id, word.id)
        db_session.commit()

        assert wu1.user_id == user1.id
        assert wu2.user_id == user2.id

    def test_count_used_for_user(self, db_session):
        user = _make_user(db_session, "Xena")
        word1 = _make_word(db_session, "WordCount1")
        word2 = _make_word(db_session, "WordCount2")
        repo = WordUsageRepository(db_session)
        repo.mark_used(user.id, word1.id)
        repo.mark_used(user.id, word2.id)
        db_session.commit()
        assert repo.count_used_for_user(user.id) == 2

    def test_mark_used_with_session_id(self, db_session):
        user = _make_user(db_session, "Yara")
        word = _make_word(db_session, "Linked")
        ps = _make_session(db_session, user.id, word.id)
        repo = WordUsageRepository(db_session)
        wu = repo.mark_used(user.id, word.id, session_id=ps.id)
        db_session.commit()
        assert wu.session_id == ps.id


# ===========================================================================
# 6. AnalysisResultRepository
# ===========================================================================

class TestAnalysisResultRepository:

    def test_create_analysis_result(self, db_session):
        user = _make_user(db_session, "Zara")
        word = _make_word(db_session, "Ambition")
        ps = _make_session(db_session, user.id, word.id)

        repo = AnalysisResultRepository(db_session)
        ar = repo.create(
            session_id=ps.id,
            overall_score=78.5,
            confidence_score=82.0,
            fluency_score=75.0,
            pronunciation_score=80.0,
            grammar_score=77.0,
            vocabulary_score=79.0,
            filler_word_count=3,
            words_per_minute=125.5,
            pause_count=2,
            feedback="Good effort! Work on reducing filler words.",
        )
        db_session.commit()
        assert ar.id is not None
        assert ar.session_id == ps.id
        assert ar.overall_score == 78.5

    def test_get_by_session(self, db_session):
        user = _make_user(db_session, "Aaron")
        word = _make_word(db_session, "Vision")
        ps = _make_session(db_session, user.id, word.id)

        ar_repo = AnalysisResultRepository(db_session)
        ar_repo.create(session_id=ps.id, overall_score=90.0)
        db_session.commit()

        fetched = ar_repo.get_by_session(ps.id)
        assert fetched is not None
        assert fetched.overall_score == 90.0

    def test_get_by_session_missing(self, db_session):
        repo = AnalysisResultRepository(db_session)
        assert repo.get_by_session(9999) is None

    def test_average_scores_no_data(self, db_session):
        user = _make_user(db_session, "Bella")
        repo = AnalysisResultRepository(db_session)
        avgs = repo.get_average_scores_for_user(user.id)
        assert avgs["overall_score"] is None
        assert avgs["confidence_score"] is None

    def test_average_scores_with_data(self, db_session):
        user = _make_user(db_session, "Carlos")
        word1 = _make_word(db_session, "WordAvg1")
        word2 = _make_word(db_session, "WordAvg2")

        ps1 = _make_session(db_session, user.id, word1.id)
        ps2 = _make_session(db_session, user.id, word2.id)

        session_repo = PracticeSessionRepository(db_session)
        session_repo.update_status(ps1.id, status="completed")
        session_repo.update_status(ps2.id, status="completed")

        ar_repo = AnalysisResultRepository(db_session)
        ar_repo.create(session_id=ps1.id, overall_score=80.0, confidence_score=70.0)
        ar_repo.create(session_id=ps2.id, overall_score=60.0, confidence_score=90.0)
        db_session.commit()

        avgs = ar_repo.get_average_scores_for_user(user.id)
        assert avgs["overall_score"] == pytest.approx(70.0)
        assert avgs["confidence_score"] == pytest.approx(80.0)


# ===========================================================================
# 7. Helper functions
# ===========================================================================

class TestHelpers:

    def test_format_duration_seconds(self):
        from speakup.utils.helpers import format_duration
        assert format_duration(45) == "45s"

    def test_format_duration_minutes(self):
        from speakup.utils.helpers import format_duration
        assert format_duration(75) == "1m 15s"

    def test_format_duration_hours(self):
        from speakup.utils.helpers import format_duration
        assert format_duration(3600) == "1h 0m"

    def test_format_duration_none(self):
        from speakup.utils.helpers import format_duration
        assert format_duration(None) == "—"

    def test_format_duration_zero(self):
        from speakup.utils.helpers import format_duration
        assert format_duration(0) == "0s"

    def test_format_score_value(self):
        from speakup.utils.helpers import format_score
        assert format_score(87.5, "%") == "87.5%"

    def test_format_score_none(self):
        from speakup.utils.helpers import format_score
        assert format_score(None) == "—"

    def test_format_score_whole_number(self):
        from speakup.utils.helpers import format_score
        assert format_score(80.0) == "80"

    def test_score_to_label_excellent(self):
        from speakup.utils.helpers import score_to_label
        assert score_to_label(90) == "Excellent"

    def test_score_to_label_good(self):
        from speakup.utils.helpers import score_to_label
        assert score_to_label(72) == "Good"

    def test_score_to_label_fair(self):
        from speakup.utils.helpers import score_to_label
        assert score_to_label(60) == "Fair"

    def test_score_to_label_needs_work(self):
        from speakup.utils.helpers import score_to_label
        assert score_to_label(40) == "Needs Work"

    def test_score_to_label_none(self):
        from speakup.utils.helpers import score_to_label
        assert score_to_label(None) == "—"

    def test_truncate_short(self):
        from speakup.utils.helpers import truncate
        assert truncate("Hello", 10) == "Hello"

    def test_truncate_long(self):
        from speakup.utils.helpers import truncate
        # max_length=8, ellipsis is 1 char → keep first 7 chars + ellipsis
        assert truncate("Hello, world!", 8) == "Hello, …"

    def test_slugify(self):
        from speakup.utils.helpers import slugify
        assert slugify("Artificial Intelligence") == "artificial-intelligence"

    def test_slugify_special_chars(self):
        from speakup.utils.helpers import slugify
        assert slugify("Work-Life Balance!") == "work-life-balance"

    def test_word_count(self):
        from speakup.utils.helpers import word_count
        assert word_count("Hello world this is a test") == 6

    def test_word_count_none(self):
        from speakup.utils.helpers import word_count
        assert word_count(None) == 0

    def test_word_count_empty(self):
        from speakup.utils.helpers import word_count
        assert word_count("") == 0

    def test_format_minutes(self):
        from speakup.utils.helpers import format_minutes
        assert format_minutes(90, decimal=True) == "1.5 min"
        assert format_minutes(90, decimal=False) == "1 min"
        assert format_minutes(None) == "—"

    def test_difficulty_label(self):
        from speakup.utils.helpers import difficulty_label
        assert difficulty_label("easy") == "Easy"
        assert difficulty_label("hard") == "Hard"
        assert difficulty_label(None) == "—"


# ===========================================================================
# 8. Integration: word service seeding
# ===========================================================================

class TestWordServiceIntegration:

    def test_seed_words_if_empty(self, engine):
        """seed_words_if_empty() inserts all words from topics.json on a fresh database."""
        from speakup.services.word_service import WordService
        ws = WordService()
        count = ws.seed_words_if_empty()
        assert count == 140  # 140 words in topics.json (14 categories × 10)

    def test_seed_words_idempotent(self, engine):
        """Calling seed_words_if_empty() twice does not duplicate words."""
        from speakup.services.word_service import WordService
        ws = WordService()
        ws.seed_words_if_empty()
        second = ws.seed_words_if_empty()
        assert second == 0  # nothing inserted the second time

    def test_get_total_word_count(self, engine):
        from speakup.services.word_service import WordService
        ws = WordService()
        ws.seed_words_if_empty()
        assert ws.get_total_word_count() == 140  # full topics.json dataset

    def test_get_next_word_for_user(self, engine):
        """get_next_word_for_user() returns a valid word dict."""
        from speakup.services.word_service import WordService
        ws = WordService()
        ws.seed_words_if_empty()
        word = ws.get_next_word_for_user(user_id=1)
        assert word is not None
        assert "word" in word
        assert "difficulty" in word
