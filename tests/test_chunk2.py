"""
tests/test_chunk2.py — pytest suite for Chunk 2.

Covers:
    1.  Loader — topics.json loading and validation
    2.  WordRepository — filtering (category, difficulty, combined)
    3.  WordRepository — random selection distribution
    4.  Session state machine — PENDING → IN_PROGRESS → COMPLETED
    5.  Session state machine — PENDING/IN_PROGRESS → ABANDONED
    6.  No-repeat guarantee — only COMPLETED sessions consume a word
    7.  Abandoned sessions — word remains available
    8.  Exhausted word pool — service returns correct signal
    9.  Concurrent/duplicate completion protection
    10. WordService — assign_topic / request_new_topic / filters
    11. WordService — load_words_from_file
    12. WordService — is_pool_exhausted
    13. Repository helpers — abandon_stale_sessions, get_active_session
    14. Loader display helpers
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from speakup.database.connection import get_session, reset_engine
from speakup.database.models import SessionStatus
from speakup.database.repository import (
    PracticeSessionRepository,
    UserRepository,
    WordRepository,
    WordUsageRepository,
)
from speakup.services.word_service import POOL_EXHAUSTED_MESSAGE, WordService

# Shared fixtures (engine, db_session) come from conftest.py
from tests.conftest import (
    make_abandoned_session,
    make_completed_session,
    make_session,
    make_user,
    make_word,
)


# ===========================================================================
# 1. Loader
# ===========================================================================

class TestLoader:

    def test_load_topics_returns_list(self):
        from speakup.data.words.loader import load_topics
        topics = load_topics()
        assert isinstance(topics, list)
        assert len(topics) > 0

    def test_load_topics_count(self):
        from speakup.data.words.loader import load_topics
        assert len(load_topics()) == 140

    def test_all_required_keys_present(self):
        from speakup.data.words.loader import load_topics
        for t in load_topics():
            assert "word" in t
            assert "category" in t
            assert "difficulty" in t

    def test_difficulty_values_valid(self):
        from speakup.data.words.loader import load_topics
        valid = {"easy", "medium", "hard"}
        for t in load_topics():
            assert t["difficulty"] in valid, \
                f"Invalid difficulty {t['difficulty']!r} for {t['word']!r}"

    def test_14_categories(self):
        from speakup.data.words.loader import load_topics, CATEGORIES
        found = {t["category"] for t in load_topics()}
        assert found == set(CATEGORIES)

    def test_10_topics_per_category(self):
        from speakup.data.words.loader import load_topics, CATEGORIES
        from collections import Counter
        counts = Counter(t["category"] for t in load_topics())
        for cat in CATEGORIES:
            assert counts[cat] == 10, f"Category {cat!r} has {counts[cat]} topics"

    def test_no_duplicate_words(self):
        from speakup.data.words.loader import load_topics
        words = [t["word"] for t in load_topics()]
        assert len(words) == len(set(words)), "Duplicate words found in topics.json"

    def test_difficulty_display_map(self):
        from speakup.data.words.loader import DIFFICULTY_DISPLAY
        assert DIFFICULTY_DISPLAY["easy"]   == "Beginner"
        assert DIFFICULTY_DISPLAY["medium"] == "Intermediate"
        assert DIFFICULTY_DISPLAY["hard"]   == "Advanced"

    def test_display_to_difficulty_reverse(self):
        from speakup.data.words.loader import DISPLAY_TO_DIFFICULTY
        assert DISPLAY_TO_DIFFICULTY["Beginner"]     == "easy"
        assert DISPLAY_TO_DIFFICULTY["Intermediate"] == "medium"
        assert DISPLAY_TO_DIFFICULTY["Advanced"]     == "hard"

    def test_get_topics_by_category(self):
        from speakup.data.words.loader import get_topics_by_category
        tech = get_topics_by_category("Technology")
        assert len(tech) == 10
        assert all(t["category"] == "Technology" for t in tech)

    def test_get_topics_by_difficulty(self):
        from speakup.data.words.loader import get_topics_by_difficulty
        easy = get_topics_by_difficulty("easy")
        assert len(easy) > 0
        assert all(t["difficulty"] == "easy" for t in easy)

    def test_load_topics_is_cached(self):
        """Calling load_topics() twice returns the exact same list object."""
        from speakup.data.words.loader import load_topics
        assert load_topics() is load_topics()


# ===========================================================================
# 2. WordRepository — filtering
# ===========================================================================

class TestWordRepositoryFiltering:

    def _seed_words(self, session) -> tuple:
        """Seed a small controlled set: 2 easy Tech, 1 hard Tech, 1 easy Health."""
        repo = WordRepository(session)
        w1 = repo.create("TechEasy1",   category="Technology", difficulty="easy")
        w2 = repo.create("TechEasy2",   category="Technology", difficulty="easy")
        w3 = repo.create("TechHard1",   category="Technology", difficulty="hard")
        w4 = repo.create("HealthEasy1", category="Health",     difficulty="easy")
        session.commit()
        return w1, w2, w3, w4

    def test_filter_by_category(self, db_session):
        user = make_user(db_session, "FilterCat")
        self._seed_words(db_session)
        repo = WordRepository(db_session)
        tech = repo.get_unused_for_user(user.id, category="Technology")
        cats = {w.category for w in tech}
        assert cats == {"Technology"}

    def test_filter_by_difficulty(self, db_session):
        user = make_user(db_session, "FilterDiff")
        self._seed_words(db_session)
        repo = WordRepository(db_session)
        easy = repo.get_unused_for_user(user.id, difficulty="easy")
        diffs = {w.difficulty for w in easy}
        assert diffs == {"easy"}

    def test_filter_combined_category_and_difficulty(self, db_session):
        user = make_user(db_session, "FilterBoth")
        self._seed_words(db_session)
        repo = WordRepository(db_session)
        result = repo.get_unused_for_user(
            user.id, category="Technology", difficulty="easy"
        )
        assert len(result) == 2
        assert all(w.category == "Technology" for w in result)
        assert all(w.difficulty == "easy"     for w in result)

    def test_filter_returns_empty_for_no_match(self, db_session):
        user = make_user(db_session, "FilterNone")
        self._seed_words(db_session)
        repo = WordRepository(db_session)
        result = repo.get_unused_for_user(
            user.id, category="Technology", difficulty="medium"
        )
        assert result == []

    def test_no_filter_returns_all(self, db_session):
        user = make_user(db_session, "FilterAll")
        self._seed_words(db_session)
        repo = WordRepository(db_session)
        result = repo.get_unused_for_user(user.id)
        # 4 custom words + default "Local User" default seed words are 0 here
        # (in-memory DB only has what we seeded)
        assert len(result) == 4

    def test_random_unused_respects_category_filter(self, db_session):
        user = make_user(db_session, "RandCat")
        self._seed_words(db_session)
        repo = WordRepository(db_session)
        for _ in range(10):  # run 10 times — should always respect filter
            word = repo.get_random_unused_for_user(
                user.id, category="Health"
            )
            assert word is not None
            assert word.category == "Health"

    def test_random_unused_respects_difficulty_filter(self, db_session):
        user = make_user(db_session, "RandDiff")
        self._seed_words(db_session)
        repo = WordRepository(db_session)
        for _ in range(10):
            word = repo.get_random_unused_for_user(
                user.id, difficulty="hard"
            )
            assert word is not None
            assert word.difficulty == "hard"

    def test_count_unused_with_filters(self, db_session):
        user = make_user(db_session, "CountFilt")
        self._seed_words(db_session)
        repo = WordRepository(db_session)
        assert repo.count_unused_for_user(user.id)                            == 4
        assert repo.count_unused_for_user(user.id, category="Technology")     == 3
        assert repo.count_unused_for_user(user.id, difficulty="easy")         == 3
        assert repo.count_unused_for_user(
            user.id, category="Technology", difficulty="easy"
        ) == 2


# ===========================================================================
# 3. Random selection — statistical distribution
# ===========================================================================

class TestRandomSelection:

    def test_random_selection_is_actually_random(self, db_session):
        """
        With 5 words available, running get_random_unused_for_user 50 times
        should return at least 2 distinct words (probability of all being the
        same word by chance is (1/5)^49 ≈ 0, effectively impossible).
        """
        user = make_user(db_session, "RandDist")
        for i in range(5):
            make_word(db_session, f"RandWord{i}", difficulty="easy")
        repo = WordRepository(db_session)
        seen = set()
        for _ in range(50):
            w = repo.get_random_unused_for_user(user.id)
            assert w is not None
            seen.add(w.id)
        assert len(seen) >= 2, "Random selection returned the same word every time"

    def test_random_returns_none_when_all_consumed(self, db_session):
        user = make_user(db_session, "RandExh")
        word = make_word(db_session, "OnlyOne")
        make_completed_session(db_session, user.id, word.id)
        repo = WordRepository(db_session)
        assert repo.get_random_unused_for_user(user.id) is None

    def test_random_returns_word_when_abandoned_not_consumed(self, db_session):
        """Abandoned sessions must not remove the word from the available pool."""
        user = make_user(db_session, "RandAban")
        word = make_word(db_session, "AbanWord")
        make_abandoned_session(db_session, user.id, word.id)
        repo = WordRepository(db_session)
        result = repo.get_random_unused_for_user(user.id)
        assert result is not None
        assert result.id == word.id


# ===========================================================================
# 4. Session state machine — COMPLETED path
# ===========================================================================

class TestSessionStateMachineCompleted:

    def test_new_session_has_pending_status(self, db_session):
        user = make_user(db_session, "SMPending")
        word = make_word(db_session, "PendingWord")
        ps = make_session(db_session, user.id, word.id)
        assert ps.status == SessionStatus.PENDING

    def test_start_session_transitions_to_in_progress(self, db_session):
        user = make_user(db_session, "SMStart")
        word = make_word(db_session, "StartWord")
        ps = make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        updated = repo.start_session(ps.id)
        db_session.commit()
        assert updated is not None
        assert updated.status == SessionStatus.IN_PROGRESS

    def test_complete_session_transitions_to_completed(self, db_session):
        user = make_user(db_session, "SMComplete")
        word = make_word(db_session, "CompleteWord")
        ps = make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        repo.start_session(ps.id)
        completed = repo.complete_session(ps.id, duration_seconds=90)
        db_session.commit()
        assert completed is not None
        assert completed.status == SessionStatus.COMPLETED
        assert completed.duration_seconds == 90
        assert completed.completed_at is not None

    def test_complete_from_pending_also_works(self, db_session):
        """A PENDING session can be completed directly (start is optional)."""
        user = make_user(db_session, "SMPendComp")
        word = make_word(db_session, "PendCompWord")
        ps = make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        completed = repo.complete_session(ps.id, duration_seconds=65)
        db_session.commit()
        assert completed.status == SessionStatus.COMPLETED

    def test_complete_session_creates_word_usage_row(self, db_session):
        user = make_user(db_session, "SMUsage")
        word = make_word(db_session, "UsageWord")
        ps = make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        repo.complete_session(ps.id, duration_seconds=70)
        db_session.commit()
        usage_repo = WordUsageRepository(db_session)
        assert usage_repo.has_used(user.id, word.id) is True

    def test_complete_session_links_usage_to_session(self, db_session):
        user = make_user(db_session, "SMLink")
        word = make_word(db_session, "LinkedWord")
        ps = make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        repo.complete_session(ps.id, duration_seconds=70)
        db_session.commit()
        usage_repo = WordUsageRepository(db_session)
        usages = usage_repo.get_used_words_for_user(user.id)
        assert any(u.session_id == ps.id for u in usages)

    def test_start_then_complete_full_lifecycle(self, db_session):
        user = make_user(db_session, "SMFull")
        word = make_word(db_session, "FullWord")
        repo = PracticeSessionRepository(db_session)

        ps = repo.create(user.id, word.id)
        db_session.commit()
        assert ps.status == SessionStatus.PENDING

        repo.start_session(ps.id)
        db_session.commit()
        db_session.refresh(ps)
        assert ps.status == SessionStatus.IN_PROGRESS

        repo.complete_session(ps.id, duration_seconds=80)
        db_session.commit()
        db_session.refresh(ps)
        assert ps.status == SessionStatus.COMPLETED

        assert WordUsageRepository(db_session).has_used(user.id, word.id)


# ===========================================================================
# 5. Session state machine — ABANDONED path
# ===========================================================================

class TestSessionStateMachineAbandoned:

    def test_abandon_pending_session(self, db_session):
        user = make_user(db_session, "AbPending")
        word = make_word(db_session, "AbPendWord")
        ps = make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        repo.abandon_session(ps.id)
        db_session.commit()
        db_session.refresh(ps)
        assert ps.status == SessionStatus.ABANDONED

    def test_abandon_in_progress_session(self, db_session):
        user = make_user(db_session, "AbInProg")
        word = make_word(db_session, "AbInProgWord")
        ps = make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        repo.start_session(ps.id)
        repo.abandon_session(ps.id)
        db_session.commit()
        db_session.refresh(ps)
        assert ps.status == SessionStatus.ABANDONED

    def test_abandon_does_not_consume_word(self, db_session):
        user = make_user(db_session, "AbNoConsume")
        word = make_word(db_session, "AbNoConsumeWord")
        ps = make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        repo.abandon_session(ps.id)
        db_session.commit()
        assert not WordUsageRepository(db_session).has_used(user.id, word.id)

    def test_abandoned_word_available_for_reassignment(self, db_session):
        user = make_user(db_session, "AbReassign")
        word = make_word(db_session, "AbReassignWord")
        make_abandoned_session(db_session, user.id, word.id)
        repo = WordRepository(db_session)
        unused = repo.get_unused_for_user(user.id)
        assert any(w.id == word.id for w in unused)

    def test_abandon_completed_session_is_no_op(self, db_session):
        """Calling abandon on an already-completed session must not change status."""
        user = make_user(db_session, "AbCompleted")
        word = make_word(db_session, "AbCompWord")
        ps = make_completed_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        result = repo.abandon_session(ps.id)
        db_session.commit()
        assert result.status == SessionStatus.COMPLETED  # unchanged


# ===========================================================================
# 6. No-repeat guarantee — only COMPLETED sessions consume a word
# ===========================================================================

class TestNoRepeatGuarantee:

    def test_completed_word_excluded_from_unused(self, db_session):
        user = make_user(db_session, "NRComplete")
        word = make_word(db_session, "ConsumedWord")
        make_completed_session(db_session, user.id, word.id)
        repo = WordRepository(db_session)
        unused = repo.get_unused_for_user(user.id)
        assert not any(w.id == word.id for w in unused)

    def test_pending_word_still_in_unused(self, db_session):
        user = make_user(db_session, "NRPending")
        word = make_word(db_session, "PendingStillAvail")
        # Just create a PENDING session — word should NOT be consumed
        make_session(db_session, user.id, word.id)
        repo = WordRepository(db_session)
        unused = repo.get_unused_for_user(user.id)
        assert any(w.id == word.id for w in unused)

    def test_in_progress_word_still_in_unused(self, db_session):
        user = make_user(db_session, "NRInProgress")
        word = make_word(db_session, "InProgStillAvail")
        ps = make_session(db_session, user.id, word.id)
        PracticeSessionRepository(db_session).start_session(ps.id)
        db_session.commit()
        unused = WordRepository(db_session).get_unused_for_user(user.id)
        assert any(w.id == word.id for w in unused)

    def test_abandoned_word_still_in_unused(self, db_session):
        user = make_user(db_session, "NRAbandoned")
        word = make_word(db_session, "AbandonedStillAvail")
        make_abandoned_session(db_session, user.id, word.id)
        unused = WordRepository(db_session).get_unused_for_user(user.id)
        assert any(w.id == word.id for w in unused)

    def test_multiple_sessions_for_same_word_only_first_completion_counts(
        self, db_session
    ):
        """
        If a user somehow creates two sessions for the same word and completes
        the first, the second completion attempt must not crash (duplicate guard).
        """
        user = make_user(db_session, "NRDupe")
        word = make_word(db_session, "DupeWord")
        repo = PracticeSessionRepository(db_session)

        ps1 = repo.create(user.id, word.id)
        db_session.commit()
        repo.complete_session(ps1.id, duration_seconds=60)
        db_session.commit()

        ps2 = repo.create(user.id, word.id)
        db_session.commit()
        # Second completion: word already in word_usage — should not raise
        result = repo.complete_session(ps2.id, duration_seconds=60)
        db_session.commit()
        assert result is not None
        assert result.status == SessionStatus.COMPLETED

    def test_different_users_same_word_independent(self, db_session):
        """Completing a word for user A must not affect user B's pool."""
        user_a = make_user(db_session, "NRUserA")
        user_b = make_user(db_session, "NRUserB")
        word = make_word(db_session, "SharedTopicNR")

        make_completed_session(db_session, user_a.id, word.id)

        repo = WordRepository(db_session)
        # Word consumed for A
        assert not any(
            w.id == word.id
            for w in repo.get_unused_for_user(user_a.id)
        )
        # Word still available for B
        assert any(
            w.id == word.id
            for w in repo.get_unused_for_user(user_b.id)
        )


# ===========================================================================
# 7. Exhausted word pool
# ===========================================================================

class TestExhaustedPool:

    def _exhaust_all(self, db_session, user, words):
        """Complete a session for every word in *words*."""
        for word in words:
            make_completed_session(db_session, user.id, word.id)

    def test_get_random_unused_returns_none_when_exhausted(self, db_session):
        user = make_user(db_session, "ExhRandom")
        words = [make_word(db_session, f"ExhW{i}") for i in range(3)]
        self._exhaust_all(db_session, user, words)
        assert WordRepository(db_session).get_random_unused_for_user(user.id) is None

    def test_count_unused_is_zero_when_exhausted(self, db_session):
        user = make_user(db_session, "ExhCount")
        words = [make_word(db_session, f"ExhC{i}") for i in range(3)]
        self._exhaust_all(db_session, user, words)
        assert WordRepository(db_session).count_unused_for_user(user.id) == 0

    def test_exhausted_for_category_filter(self, db_session):
        """Exhaust only Tech words; Health words remain."""
        user = make_user(db_session, "ExhCat")
        tech = [make_word(db_session, f"TechExh{i}", category="Technology")
                for i in range(2)]
        make_word(db_session, "HealthStays", category="Health")
        self._exhaust_all(db_session, user, tech)

        repo = WordRepository(db_session)
        assert repo.count_unused_for_user(user.id, category="Technology") == 0
        assert repo.count_unused_for_user(user.id, category="Health")     == 1

    def test_word_service_assign_topic_returns_exhausted_flag(self, engine):
        """WordService.assign_topic() returns exhausted=True with the message."""
        ws = WordService()
        # Seed 1 word and exhaust it
        with get_session() as session:
            word = WordRepository(session).create(
                "OnlyTopicExh", category="Test", difficulty="easy"
            )
        with get_session() as session:
            make_completed_session(session, 1, word.id)

        result = ws.assign_topic(user_id=1)
        # The default user (id=1) now has no unused words from our minimal seed
        # (this relies on the in-memory DB seeded by engine fixture having no
        #  bundled words — only the one we created above)
        assert result["exhausted"] is True
        assert result["word"] is None
        assert result["session_id"] is None
        assert POOL_EXHAUSTED_MESSAGE in result["message"]

    def test_is_pool_exhausted_true_when_all_done(self, engine):
        ws = WordService()
        # Only the default "Local User" (id=1) exists from init_db seed
        # No words seeded → pool immediately exhausted for any filter
        assert ws.is_pool_exhausted(1, category="NonExistentCategory") is True

    def test_is_pool_exhausted_false_when_words_remain(self, engine):
        ws = WordService()
        ws.seed_words_if_empty()  # seeds 140 words
        assert ws.is_pool_exhausted(1) is False


# ===========================================================================
# 8. Concurrent / duplicate selection protection
# ===========================================================================

class TestConcurrentProtection:

    def test_double_complete_does_not_raise(self, db_session):
        """Calling complete_session twice must be idempotent."""
        user = make_user(db_session, "DblComp")
        word = make_word(db_session, "DblCompWord")
        repo = PracticeSessionRepository(db_session)
        ps = repo.create(user.id, word.id)
        db_session.commit()

        repo.complete_session(ps.id, duration_seconds=60)
        db_session.commit()
        # Second call — word already in usage, session already completed
        result = repo.complete_session(ps.id, duration_seconds=60)
        db_session.commit()
        assert result is not None
        assert result.status == SessionStatus.COMPLETED

    def test_double_mark_used_raises_integrity_error(self, db_session):
        """Directly calling mark_used twice still raises IntegrityError."""
        user = make_user(db_session, "DblMark")
        word = make_word(db_session, "DblMarkWord")
        repo = WordUsageRepository(db_session)
        repo.mark_used(user.id, word.id)
        db_session.commit()
        with pytest.raises(IntegrityError):
            repo.mark_used(user.id, word.id)

    def test_abandon_stale_sessions_clears_pending(self, db_session):
        user = make_user(db_session, "StaleUser")
        word1 = make_word(db_session, "StaleW1")
        word2 = make_word(db_session, "StaleW2")
        repo = PracticeSessionRepository(db_session)
        ps1 = repo.create(user.id, word1.id)
        ps2 = repo.create(user.id, word2.id)
        repo.start_session(ps2.id)
        db_session.commit()

        count = repo.abandon_stale_sessions(user.id)
        db_session.commit()
        assert count == 2
        db_session.refresh(ps1)
        db_session.refresh(ps2)
        assert ps1.status == SessionStatus.ABANDONED
        assert ps2.status == SessionStatus.ABANDONED

    def test_abandon_stale_leaves_completed_unchanged(self, db_session):
        user = make_user(db_session, "StaleComp")
        word = make_word(db_session, "StaleCompW")
        ps = make_completed_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        count = repo.abandon_stale_sessions(user.id)
        db_session.commit()
        assert count == 0
        db_session.refresh(ps)
        assert ps.status == SessionStatus.COMPLETED

    def test_get_active_session_returns_pending(self, db_session):
        user = make_user(db_session, "ActivePend")
        word = make_word(db_session, "ActivePendW")
        ps = make_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        active = repo.get_active_session_for_user(user.id)
        assert active is not None
        assert active.id == ps.id

    def test_get_active_session_returns_none_when_all_terminal(self, db_session):
        user = make_user(db_session, "ActiveNone")
        word = make_word(db_session, "ActiveNoneW")
        make_completed_session(db_session, user.id, word.id)
        repo = PracticeSessionRepository(db_session)
        assert repo.get_active_session_for_user(user.id) is None


# ===========================================================================
# 9. WordService — assign_topic and request_new_topic
# ===========================================================================

class TestWordServiceAssignment:

    def test_assign_topic_returns_topic_dict(self, engine):
        ws = WordService()
        ws.seed_words_if_empty()
        result = ws.assign_topic(user_id=1)
        assert result["exhausted"] is False
        assert result["word"] is not None
        assert result["session_id"] is not None
        assert "difficulty_display" in result

    def test_assign_topic_creates_pending_session(self, engine):
        ws = WordService()
        ws.seed_words_if_empty()
        result = ws.assign_topic(user_id=1)
        with get_session() as session:
            ps = session.get(
                __import__(
                    "speakup.database.models", fromlist=["PracticeSession"]
                ).PracticeSession,
                result["session_id"],
            )
            assert ps is not None
            assert ps.status == SessionStatus.PENDING

    def test_assign_topic_with_category_filter(self, engine):
        ws = WordService()
        ws.seed_words_if_empty()
        result = ws.assign_topic(user_id=1, category="Technology")
        assert result["exhausted"] is False
        assert result["category"] == "Technology"

    def test_assign_topic_with_difficulty_filter(self, engine):
        ws = WordService()
        ws.seed_words_if_empty()
        result = ws.assign_topic(user_id=1, difficulty_label="Beginner")
        assert result["exhausted"] is False
        assert result["difficulty"] == "easy"
        assert result["difficulty_display"] == "Beginner"

    def test_assign_topic_abandons_previous_stale_session(self, engine):
        """assign_topic() must abandon any pre-existing PENDING sessions."""
        ws = WordService()
        ws.seed_words_if_empty()
        result1 = ws.assign_topic(user_id=1)
        sid1 = result1["session_id"]

        # Assign again without explicitly abandoning first
        result2 = ws.assign_topic(user_id=1)
        assert result2["session_id"] != sid1

        # First session must now be abandoned
        with get_session() as session:
            from speakup.database.models import PracticeSession
            ps1 = session.get(PracticeSession, sid1)
            assert ps1.status == SessionStatus.ABANDONED

    def test_request_new_topic_abandons_current(self, engine):
        ws = WordService()
        ws.seed_words_if_empty()
        result1 = ws.assign_topic(user_id=1)
        sid1 = result1["session_id"]

        result2 = ws.request_new_topic(user_id=1, current_session_id=sid1)
        assert result2["session_id"] != sid1
        assert result2["exhausted"] is False

        with get_session() as session:
            from speakup.database.models import PracticeSession
            ps1 = session.get(PracticeSession, sid1)
            assert ps1.status == SessionStatus.ABANDONED

    def test_request_new_topic_abandoned_word_available_again(self, engine):
        """
        After requesting a new topic, the original topic's word must be
        back in the available pool (not consumed).
        """
        ws = WordService()
        ws.seed_words_if_empty()
        result1 = ws.assign_topic(user_id=1)
        word_id1 = result1["word_id"]

        ws.request_new_topic(user_id=1, current_session_id=result1["session_id"])

        # The word from the first assignment is NOT consumed
        with get_session() as session:
            unused_ids = {
                w.id for w in
                WordRepository(session).get_unused_for_user(1)
            }
        assert word_id1 in unused_ids

    def test_complete_practice_consumes_word(self, engine):
        ws = WordService()
        ws.seed_words_if_empty()
        before = ws.get_unused_count_for_user(1)
        result = ws.assign_topic(user_id=1)
        ws.start_practice(result["session_id"])
        ws.complete_practice(result["session_id"], duration_seconds=65)
        after = ws.get_unused_count_for_user(1)
        assert after == before - 1

    def test_abandon_practice_does_not_consume_word(self, engine):
        ws = WordService()
        ws.seed_words_if_empty()
        before = ws.get_unused_count_for_user(1)
        result = ws.assign_topic(user_id=1)
        ws.abandon_practice(result["session_id"])
        after = ws.get_unused_count_for_user(1)
        assert after == before  # unchanged

    def test_get_unused_count_filtered(self, engine):
        ws = WordService()
        ws.seed_words_if_empty()
        total    = ws.get_unused_count_for_user(1)
        tech     = ws.get_unused_count_for_user(1, category="Technology")
        beginner = ws.get_unused_count_for_user(1, difficulty_label="Beginner")
        assert total > tech
        assert total > beginner
        assert tech == 10   # 10 Technology topics in dataset


# ===========================================================================
# 10. WordService — load_words_from_file
# ===========================================================================

class TestLoadWordsFromFile:

    def test_load_valid_file(self, engine):
        ws = WordService()
        data = [
            {"word": "FileWord1", "category": "Test", "difficulty": "easy",
             "description": "From file."},
            {"word": "FileWord2", "category": "Test", "difficulty": "medium",
             "description": "From file."},
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            tmp_path = f.name

        inserted = ws.load_words_from_file(tmp_path)
        assert inserted == 2
        assert ws.get_total_word_count() == 2  # only these 2 in empty DB

    def test_load_skips_duplicates(self, engine):
        ws = WordService()
        data = [{"word": "DupeFileWord", "category": "Test", "difficulty": "easy"}]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            tmp_path = f.name

        first  = ws.load_words_from_file(tmp_path)
        second = ws.load_words_from_file(tmp_path)
        assert first  == 1
        assert second == 0

    def test_load_nonexistent_file_raises(self, engine):
        ws = WordService()
        with pytest.raises(FileNotFoundError):
            ws.load_words_from_file("/nonexistent/path/words.json")

    def test_load_invalid_json_raises(self, engine):
        ws = WordService()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            f.write("not valid json {{{{")
            tmp_path = f.name

        with pytest.raises(ValueError):
            ws.load_words_from_file(tmp_path)


# ===========================================================================
# 11. WordService — get_categories / difficulty helpers
# ===========================================================================

class TestWordServiceHelpers:

    def test_get_categories_returns_14(self):
        ws = WordService()
        cats = ws.get_categories()
        assert len(cats) == 14
        assert "Technology" in cats
        assert "Daily Life" in cats

    def test_get_difficulty_labels(self):
        ws = WordService()
        labels = ws.get_difficulty_labels()
        assert labels == ["Beginner", "Intermediate", "Advanced"]

    def test_difficulty_display_conversion(self):
        ws = WordService()
        assert ws.difficulty_display("easy")   == "Beginner"
        assert ws.difficulty_display("medium") == "Intermediate"
        assert ws.difficulty_display("hard")   == "Advanced"

    def test_difficulty_db_value_conversion(self):
        ws = WordService()
        assert ws.difficulty_db_value("Beginner")     == "easy"
        assert ws.difficulty_db_value("Intermediate") == "medium"
        assert ws.difficulty_db_value("Advanced")     == "hard"
        assert ws.difficulty_db_value("Unknown")      is None

    def test_pool_exhausted_message_not_empty(self):
        assert POOL_EXHAUSTED_MESSAGE
        assert "completed" in POOL_EXHAUSTED_MESSAGE.lower()
