"""
Repository layer for SpeakUp.

Each repository class wraps a SQLAlchemy Session and exposes clean,
domain-focused methods.  UI and service code never write raw queries —
they call repository methods instead.

Repositories:
    UserRepository
    WordRepository
    PracticeSessionRepository
    WordUsageRepository
    AnalysisResultRepository
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from speakup.database.models import (
    AnalysisResult,
    PracticeSession,
    SessionStatus,
    User,
    UserCredential,
    UserPreferences,
    Word,
    WordUsage,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UserRepository
# ---------------------------------------------------------------------------

class UserRepository:
    """CRUD operations for the :class:`~speakup.database.models.User` model."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- create -----------------------------------------------------------

    def create(self, name: str) -> User:
        """Create and persist a new user."""
        user = User(name=name)
        self._session.add(user)
        self._session.flush()  # assigns id without full commit
        logger.debug("Created user: %s (id=%s)", name, user.id)
        return user

    # ---- read -------------------------------------------------------------

    def get_by_id(self, user_id: int) -> Optional[User]:
        return self._session.get(User, user_id)

    def get_all(self) -> list[User]:
        return list(self._session.scalars(select(User).order_by(User.created_at)))

    def get_unclaimed_by_name(self, name: str) -> Optional[User]:
        """Find a legacy profile with no login credentials by display name."""
        for user in self.get_all():
            if user.name.casefold() == name.casefold() and user.credential is None:
                return user
        return None

    def get_first(self) -> Optional[User]:
        """Return the first (and usually only) local user."""
        return self._session.scalars(select(User).order_by(User.id)).first()

    def count(self) -> int:
        return self._session.scalar(select(func.count()).select_from(User)) or 0

    # ---- update -----------------------------------------------------------

    def update_name(self, user_id: int, name: str) -> Optional[User]:
        user = self.get_by_id(user_id)
        if user:
            user.name = name
            user.updated_at = datetime.now(timezone.utc)
            self._session.flush()
        return user

    def clear_profile_data(self, user_id: int) -> bool:
        """Delete one profile's sessions, topic usage, and preferences."""
        user = self.get_by_id(user_id)
        if user is None:
            return False

        for practice_session in list(user.practice_sessions):
            self._session.delete(practice_session)
        for word_usage in list(user.word_usages):
            self._session.delete(word_usage)
        if user.preferences is not None:
            self._session.delete(user.preferences)

        self._session.flush()
        return True

    # ---- delete -----------------------------------------------------------

    def delete(self, user_id: int) -> bool:
        user = self.get_by_id(user_id)
        if user:
            self._session.delete(user)
            self._session.flush()
            return True
        return False


class UserCredentialRepository:
    """Persistence methods for local login credentials."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_email(self, email: str) -> Optional[UserCredential]:
        return self._session.scalars(
            select(UserCredential).where(UserCredential.email == email)
        ).first()

    def create(self, user_id: int, email: str, password_hash: str) -> UserCredential:
        credential = UserCredential(
            user_id=user_id,
            email=email,
            password_hash=password_hash,
        )
        self._session.add(credential)
        self._session.flush()
        return credential


# ---------------------------------------------------------------------------
# WordRepository
# ---------------------------------------------------------------------------

class WordRepository:
    """CRUD and query operations for the :class:`~speakup.database.models.Word` model."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- create -----------------------------------------------------------

    def create(
        self,
        word: str,
        category: str | None = None,
        difficulty: str = "medium",
        description: str | None = None,
    ) -> Word:
        """Create and persist a new word/topic."""
        w = Word(
            word=word,
            category=category,
            difficulty=difficulty,
            description=description,
        )
        self._session.add(w)
        self._session.flush()
        logger.debug("Created word: %r (id=%s)", word, w.id)
        return w

    def bulk_create(self, words_data: list[dict]) -> list[Word]:
        """
        Insert multiple words, silently skipping duplicates.

        Each dict should have keys: word, category, difficulty, description.
        """
        created: list[Word] = []
        for data in words_data:
            existing = self.get_by_word(data["word"])
            if existing:
                continue
            w = Word(
                word=data["word"],
                category=data.get("category"),
                difficulty=data.get("difficulty", "medium"),
                description=data.get("description"),
            )
            self._session.add(w)
            created.append(w)
        self._session.flush()
        logger.debug("Bulk created %d words.", len(created))
        return created

    # ---- read -------------------------------------------------------------

    def get_by_id(self, word_id: int) -> Optional[Word]:
        return self._session.get(Word, word_id)

    def get_by_word(self, word: str) -> Optional[Word]:
        return self._session.scalars(
            select(Word).where(Word.word == word)
        ).first()

    def get_all(self) -> list[Word]:
        return list(self._session.scalars(select(Word).order_by(Word.word)))

    def get_by_category(self, category: str) -> list[Word]:
        return list(
            self._session.scalars(
                select(Word).where(Word.category == category).order_by(Word.word)
            )
        )

    def get_by_difficulty(self, difficulty: str) -> list[Word]:
        return list(
            self._session.scalars(
                select(Word)
                .where(Word.difficulty == difficulty)
                .order_by(Word.word)
            )
        )

    def count(self) -> int:
        return self._session.scalar(select(func.count()).select_from(Word)) or 0

    def get_unused_for_user(
        self,
        user_id: int,
        category: str | None = None,
        difficulty: str | None = None,
    ) -> list[Word]:
        """
        Return all words that the given user has NOT yet completed.

        Optionally filter by *category* and/or *difficulty* (DB values:
        ``'easy'``, ``'medium'``, ``'hard'``).

        Only words absent from ``word_usage`` for this user are returned —
        this includes words from abandoned sessions, which are intentionally
        re-available.
        """
        used_subq = (
            select(WordUsage.word_id)
            .where(WordUsage.user_id == user_id)
            .scalar_subquery()
        )
        stmt = select(Word).where(Word.id.not_in(used_subq))
        if category:
            stmt = stmt.where(Word.category == category)
        if difficulty:
            stmt = stmt.where(Word.difficulty == difficulty)
        stmt = stmt.order_by(Word.word)
        return list(self._session.scalars(stmt))

    def get_random_unused_for_user(
        self,
        user_id: int,
        category: str | None = None,
        difficulty: str | None = None,
    ) -> Optional[Word]:
        """
        Return one random word the user has not yet completed.

        Optionally constrain to a specific *category* and/or *difficulty*.
        Returns ``None`` when no matching unused words remain.
        """
        from sqlalchemy.sql.expression import func as sqlfunc  # noqa: PLC0415

        used_subq = (
            select(WordUsage.word_id)
            .where(WordUsage.user_id == user_id)
            .scalar_subquery()
        )
        stmt = select(Word).where(Word.id.not_in(used_subq))
        if category:
            stmt = stmt.where(Word.category == category)
        if difficulty:
            stmt = stmt.where(Word.difficulty == difficulty)
        stmt = stmt.order_by(sqlfunc.random()).limit(1)
        return self._session.scalars(stmt).first()

    def count_unused_for_user(
        self,
        user_id: int,
        category: str | None = None,
        difficulty: str | None = None,
    ) -> int:
        """Return the count of unused words for the given filters."""
        used_subq = (
            select(WordUsage.word_id)
            .where(WordUsage.user_id == user_id)
            .scalar_subquery()
        )
        stmt = select(func.count()).select_from(Word).where(Word.id.not_in(used_subq))
        if category:
            stmt = stmt.where(Word.category == category)
        if difficulty:
            stmt = stmt.where(Word.difficulty == difficulty)
        return self._session.scalar(stmt) or 0

    # ---- delete -----------------------------------------------------------

    def delete(self, word_id: int) -> bool:
        word = self.get_by_id(word_id)
        if word:
            self._session.delete(word)
            self._session.flush()
            return True
        return False


# ---------------------------------------------------------------------------
# PracticeSessionRepository
# ---------------------------------------------------------------------------

class PracticeSessionRepository:
    """CRUD operations for :class:`~speakup.database.models.PracticeSession`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- create -----------------------------------------------------------

    def create(self, user_id: int, word_id: int) -> PracticeSession:
        """
        Create a new practice session in PENDING state.

        The session is created when the topic is assigned and displayed to
        the user.  Merely displaying a topic does NOT consume the word —
        only COMPLETED sessions retire the word via word_usage.
        """
        ps = PracticeSession(
            user_id=user_id,
            word_id=word_id,
            status=SessionStatus.PENDING,
        )
        self._session.add(ps)
        self._session.flush()
        logger.debug(
            "Created practice session id=%s for user=%s word=%s",
            ps.id, user_id, word_id,
        )
        return ps

    # ---- read -------------------------------------------------------------

    def get_by_id(self, session_id: int) -> Optional[PracticeSession]:
        return self._session.get(PracticeSession, session_id)

    def get_by_user(
        self, user_id: int, status: str | None = None
    ) -> list[PracticeSession]:
        stmt = (
            select(PracticeSession)
            .where(PracticeSession.user_id == user_id)
            .order_by(PracticeSession.started_at.desc())
        )
        if status:
            stmt = stmt.where(PracticeSession.status == status)
        return list(self._session.scalars(stmt))

    def get_completed_by_user(self, user_id: int) -> list[PracticeSession]:
        return self.get_by_user(user_id, status="completed")

    def count_by_user(self, user_id: int, status: str | None = None) -> int:
        stmt = select(func.count()).select_from(PracticeSession).where(
            PracticeSession.user_id == user_id
        )
        if status:
            stmt = stmt.where(PracticeSession.status == status)
        return self._session.scalar(stmt) or 0

    def get_total_duration_by_user(self, user_id: int) -> int:
        """Return total practice seconds for a user across all completed sessions."""
        result = self._session.scalar(
            select(func.sum(PracticeSession.duration_seconds))
            .where(PracticeSession.user_id == user_id)
            .where(PracticeSession.status == "completed")
        )
        return result or 0

    # ---- update -----------------------------------------------------------

    def update_status(
        self,
        session_id: int,
        status: str,
        completed_at: datetime | None = None,
        duration_seconds: int | None = None,
        recording_path: str | None = None,
        transcript: str | None = None,
    ) -> Optional[PracticeSession]:
        ps = self.get_by_id(session_id)
        if not ps:
            return None
        ps.status = status
        if completed_at is not None:
            ps.completed_at = completed_at
        if duration_seconds is not None:
            ps.duration_seconds = duration_seconds
        if recording_path is not None:
            ps.recording_path = recording_path
        if transcript is not None:
            ps.transcript = transcript
        self._session.flush()
        return ps

    # ---- delete -----------------------------------------------------------

    def delete(self, session_id: int) -> bool:
        ps = self.get_by_id(session_id)
        if ps:
            self._session.delete(ps)
            self._session.flush()
            return True
        return False

    # ---- lifecycle helpers ------------------------------------------------

    def start_session(self, session_id: int) -> Optional[PracticeSession]:
        """
        Transition a PENDING session to IN_PROGRESS.

        Call this when the user clicks Start Practice and recording begins.
        Does nothing if the session is not in PENDING state.

        Returns the updated session, or None if not found.
        """
        ps = self.get_by_id(session_id)
        if not ps:
            return None
        if ps.status != SessionStatus.PENDING:
            logger.warning(
                "start_session called on session id=%s with status=%r (expected pending)",
                session_id, ps.status,
            )
            return ps
        ps.status = SessionStatus.IN_PROGRESS
        self._session.flush()
        logger.debug("Session id=%s started (in_progress)", session_id)
        return ps

    def complete_session(
        self,
        session_id: int,
        duration_seconds: int,
        recording_path: str | None = None,
        transcript: str | None = None,
    ) -> Optional[PracticeSession]:
        """
        Transition an IN_PROGRESS session to COMPLETED and permanently
        retire the word for the user by inserting a word_usage row.

        This is the ONLY place where a word_usage row is created — ensuring
        that a word is consumed exclusively by successfully completed sessions.

        Args:
            session_id:       The session to complete.
            duration_seconds: Actual recorded duration.
            recording_path:   Path to the saved audio/video file (optional).
            transcript:       STT output text (optional).

        Returns:
            The updated :class:`PracticeSession`, or None if not found or
            already in a terminal state.

        Raises:
            IntegrityError: If the word was already marked used (duplicate
                            completion guard — should not happen in normal flow).
        """
        ps = self.get_by_id(session_id)
        if not ps:
            logger.warning("complete_session: session id=%s not found", session_id)
            return None

        if ps.status == SessionStatus.COMPLETED:
            logger.warning(
                "complete_session called on already-completed session id=%s", session_id
            )
            return ps

        if ps.status not in (SessionStatus.PENDING, SessionStatus.IN_PROGRESS):
            logger.warning(
                "complete_session called on session id=%s with terminal status=%r",
                session_id, ps.status,
            )
            return ps

        # Update session fields
        ps.status = SessionStatus.COMPLETED
        ps.completed_at = datetime.now(timezone.utc)
        ps.duration_seconds = duration_seconds
        if recording_path is not None:
            ps.recording_path = recording_path
        if transcript is not None:
            ps.transcript = transcript
        self._session.flush()

        # Retire the word — guard against duplicate (race condition or bug)
        usage_repo = WordUsageRepository(self._session)
        if not usage_repo.has_used(ps.user_id, ps.word_id):
            usage_repo.mark_used(ps.user_id, ps.word_id, session_id=ps.id)
        else:
            logger.warning(
                "Word id=%s already in word_usage for user id=%s — skipping duplicate mark",
                ps.word_id, ps.user_id,
            )

        logger.info(
            "Session id=%s completed. user=%s word=%s duration=%ss",
            ps.id, ps.user_id, ps.word_id, duration_seconds,
        )
        return ps

    def abandon_session(self, session_id: int) -> Optional[PracticeSession]:
        """
        Transition a PENDING or IN_PROGRESS session to ABANDONED.

        Abandoned sessions do NOT consume the word — it remains available
        for future assignment to the same user.

        Returns the updated session, or None if not found.
        """
        ps = self.get_by_id(session_id)
        if not ps:
            return None

        if ps.status in (SessionStatus.COMPLETED, SessionStatus.ABANDONED):
            logger.debug(
                "abandon_session: session id=%s already in terminal state %r",
                session_id, ps.status,
            )
            return ps

        ps.status = SessionStatus.ABANDONED
        self._session.flush()
        logger.debug("Session id=%s abandoned — word NOT consumed", session_id)
        return ps

    def get_active_session_for_user(
        self, user_id: int
    ) -> Optional[PracticeSession]:
        """
        Return the most recent PENDING or IN_PROGRESS session for the user,
        if any exists.  Used to resume or clean up stale sessions.
        """
        stmt = (
            select(PracticeSession)
            .where(PracticeSession.user_id == user_id)
            .where(
                PracticeSession.status.in_(
                    [SessionStatus.PENDING, SessionStatus.IN_PROGRESS]
                )
            )
            .order_by(PracticeSession.started_at.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def abandon_stale_sessions(self, user_id: int) -> int:
        """
        Abandon all PENDING or IN_PROGRESS sessions for the user.

        Called when the user requests a new topic or navigates away — ensures
        no stale sessions leave orphaned state.

        Returns the number of sessions abandoned.
        """
        stale = (
            self._session.scalars(
                select(PracticeSession)
                .where(PracticeSession.user_id == user_id)
                .where(
                    PracticeSession.status.in_(
                        [SessionStatus.PENDING, SessionStatus.IN_PROGRESS]
                    )
                )
            )
        ).all()
        count = 0
        for ps in stale:
            ps.status = SessionStatus.ABANDONED
            count += 1
        if count:
            self._session.flush()
            logger.debug("Abandoned %d stale sessions for user_id=%s", count, user_id)
        return count


# ---------------------------------------------------------------------------
# WordUsageRepository
# ---------------------------------------------------------------------------

class WordUsageRepository:
    """Operations for the :class:`~speakup.database.models.WordUsage` model."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- create -----------------------------------------------------------

    def mark_used(
        self, user_id: int, word_id: int, session_id: int | None = None
    ) -> WordUsage:
        """
        Record that *user_id* has completed *word_id*.

        Raises :class:`IntegrityError` if the word has already been marked
        used for this user (honouring the UNIQUE constraint).
        """
        wu = WordUsage(user_id=user_id, word_id=word_id, session_id=session_id)
        self._session.add(wu)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            logger.warning(
                "Word id=%s already marked used for user id=%s", word_id, user_id
            )
            raise
        logger.debug("Marked word_id=%s used for user_id=%s", word_id, user_id)
        return wu

    # ---- read -------------------------------------------------------------

    def has_used(self, user_id: int, word_id: int) -> bool:
        """Return True if the user has already completed this word."""
        return (
            self._session.scalar(
                select(func.count())
                .select_from(WordUsage)
                .where(WordUsage.user_id == user_id)
                .where(WordUsage.word_id == word_id)
            )
            or 0
        ) > 0

    def get_used_words_for_user(self, user_id: int) -> list[WordUsage]:
        return list(
            self._session.scalars(
                select(WordUsage)
                .where(WordUsage.user_id == user_id)
                .order_by(WordUsage.used_at.desc())
            )
        )

    def count_used_for_user(self, user_id: int) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(WordUsage)
                .where(WordUsage.user_id == user_id)
            )
            or 0
        )


# ---------------------------------------------------------------------------
# AnalysisResultRepository
# ---------------------------------------------------------------------------

class AnalysisResultRepository:
    """Operations for the :class:`~speakup.database.models.AnalysisResult` model."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- create -----------------------------------------------------------

    def create(self, session_id: int, **scores) -> AnalysisResult:
        """
        Create an analysis result for *session_id*.

        Keyword arguments map directly to column names:
        pronunciation_score, fluency_score, grammar_score,
        vocabulary_score, confidence_score, filler_word_count,
        words_per_minute, pause_count, overall_score, feedback.
        """
        ar = AnalysisResult(session_id=session_id, **scores)
        self._session.add(ar)
        self._session.flush()
        logger.debug("Created analysis result for session_id=%s", session_id)
        return ar

    # ---- read -------------------------------------------------------------

    def get_by_session(self, session_id: int) -> Optional[AnalysisResult]:
        return self._session.scalars(
            select(AnalysisResult).where(AnalysisResult.session_id == session_id)
        ).first()

    def get_average_scores_for_user(self, user_id: int) -> dict:
        """
        Compute average scores across all completed sessions for a user.
        Returns a dict with score keys and float values (None if no data).
        """
        stmt = (
            select(
                func.avg(AnalysisResult.overall_score).label("overall_score"),
                func.avg(AnalysisResult.confidence_score).label("confidence_score"),
                func.avg(AnalysisResult.fluency_score).label("fluency_score"),
                func.avg(AnalysisResult.words_per_minute).label("words_per_minute"),
            )
            .join(PracticeSession, AnalysisResult.session_id == PracticeSession.id)
            .where(PracticeSession.user_id == user_id)
            .where(PracticeSession.status == "completed")
        )
        row = self._session.execute(stmt).one()
        return {
            "overall_score": row.overall_score,
            "confidence_score": row.confidence_score,
            "fluency_score": row.fluency_score,
            "words_per_minute": row.words_per_minute,
        }

    def get_history_for_user(
        self, user_id: int, limit: int = 50
    ) -> list[AnalysisResult]:
        """Return the most recent analysis results for a user."""
        stmt = (
            select(AnalysisResult)
            .join(PracticeSession, AnalysisResult.session_id == PracticeSession.id)
            .where(PracticeSession.user_id == user_id)
            .order_by(PracticeSession.completed_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def get_time_series_for_user(
        self,
        user_id: int,
        since: "datetime | None" = None,
        limit: int = 200,
    ) -> list[dict]:
        """
        Return per-session time-series data for all chart metrics.

        Each dict contains:
            session_id, completed_at, overall_score, confidence_score,
            fluency_score, words_per_minute, filler_word_count, duration_seconds

        Ordered chronologically (oldest first) so chart x-axes go left→right.
        *since* filters to sessions completed on or after that datetime.
        """
        from datetime import timezone  # noqa: PLC0415

        stmt = (
            select(
                PracticeSession.id.label("session_id"),
                PracticeSession.completed_at.label("completed_at"),
                PracticeSession.duration_seconds.label("duration_seconds"),
                AnalysisResult.overall_score.label("overall_score"),
                AnalysisResult.confidence_score.label("confidence_score"),
                AnalysisResult.fluency_score.label("fluency_score"),
                AnalysisResult.words_per_minute.label("words_per_minute"),
                AnalysisResult.filler_word_count.label("filler_word_count"),
            )
            .join(AnalysisResult, AnalysisResult.session_id == PracticeSession.id)
            .where(PracticeSession.user_id == user_id)
            .where(PracticeSession.status == "completed")
            .where(PracticeSession.completed_at.is_not(None))
        )
        if since is not None:
            stmt = stmt.where(PracticeSession.completed_at >= since)
        stmt = stmt.order_by(PracticeSession.completed_at.asc()).limit(limit)

        rows = self._session.execute(stmt).all()
        return [
            {
                "session_id":       r.session_id,
                "completed_at":     r.completed_at,
                "duration_seconds": r.duration_seconds or 0,
                "overall_score":    r.overall_score,
                "confidence_score": r.confidence_score,
                "fluency_score":    r.fluency_score,
                "words_per_minute": r.words_per_minute,
                "filler_word_count": r.filler_word_count,
            }
            for r in rows
        ]

    def get_filtered_averages_for_user(
        self,
        user_id: int,
        since: "datetime | None" = None,
    ) -> dict:
        """
        Like get_average_scores_for_user but supports an optional date cutoff.
        Also returns fluency, vocabulary, grammar, and filler averages
        in addition to the base four.
        """
        stmt = (
            select(
                func.avg(AnalysisResult.overall_score).label("overall_score"),
                func.avg(AnalysisResult.confidence_score).label("confidence_score"),
                func.avg(AnalysisResult.fluency_score).label("fluency_score"),
                func.avg(AnalysisResult.vocabulary_score).label("vocabulary_score"),
                func.avg(AnalysisResult.grammar_score).label("grammar_score"),
                func.avg(AnalysisResult.words_per_minute).label("words_per_minute"),
                func.avg(AnalysisResult.filler_word_count).label("filler_word_count"),
            )
            .join(PracticeSession, AnalysisResult.session_id == PracticeSession.id)
            .where(PracticeSession.user_id == user_id)
            .where(PracticeSession.status == "completed")
        )
        if since is not None:
            stmt = stmt.where(PracticeSession.completed_at >= since)

        row = self._session.execute(stmt).one()
        return {
            "overall_score":    row.overall_score,
            "confidence_score": row.confidence_score,
            "fluency_score":    row.fluency_score,
            "vocabulary_score": row.vocabulary_score,
            "grammar_score":    row.grammar_score,
            "words_per_minute": row.words_per_minute,
            "filler_word_count": row.filler_word_count,
        }

    def get_rich_history_for_user(
        self,
        user_id: int,
        since: "datetime | None" = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        Return recent sessions joined with their analysis and word info.
        Used by the dashboard Recent Sessions table.
        """
        from speakup.database.models import Word  # noqa: PLC0415

        stmt = (
            select(
                PracticeSession.id.label("session_id"),
                PracticeSession.completed_at.label("completed_at"),
                PracticeSession.duration_seconds.label("duration_seconds"),
                PracticeSession.transcript.label("transcript"),
                PracticeSession.recording_path.label("recording_path"),
                Word.word.label("word"),
                Word.category.label("category"),
                Word.difficulty.label("difficulty"),
                AnalysisResult.overall_score.label("overall_score"),
                AnalysisResult.confidence_score.label("confidence_score"),
                AnalysisResult.fluency_score.label("fluency_score"),
                AnalysisResult.vocabulary_score.label("vocabulary_score"),
                AnalysisResult.grammar_score.label("grammar_score"),
                AnalysisResult.filler_word_count.label("filler_word_count"),
                AnalysisResult.words_per_minute.label("words_per_minute"),
                AnalysisResult.pause_count.label("pause_count"),
                AnalysisResult.feedback.label("feedback"),
            )
            .join(AnalysisResult, AnalysisResult.session_id == PracticeSession.id)
            .join(Word, Word.id == PracticeSession.word_id, isouter=True)
            .where(PracticeSession.user_id == user_id)
            .where(PracticeSession.status == "completed")
            .where(PracticeSession.completed_at.is_not(None))
        )
        if since is not None:
            stmt = stmt.where(PracticeSession.completed_at >= since)
        stmt = stmt.order_by(PracticeSession.completed_at.desc()).limit(limit)

        rows = self._session.execute(stmt).all()
        return [
            {
                "session_id":       r.session_id,
                "completed_at":     r.completed_at,
                "duration_seconds": r.duration_seconds or 0,
                "transcript":       r.transcript or "",
                "recording_path":   r.recording_path or "",
                "word":             r.word or "—",
                "category":         r.category,
                "difficulty":       r.difficulty,
                "overall_score":    r.overall_score,
                "confidence_score": r.confidence_score,
                "fluency_score":    r.fluency_score,
                "vocabulary_score": r.vocabulary_score,
                "grammar_score":    r.grammar_score,
                "filler_word_count": r.filler_word_count,
                "words_per_minute": r.words_per_minute,
                "pause_count":      r.pause_count,
                "feedback":         r.feedback or "",
            }
            for r in rows
        ]



# ---------------------------------------------------------------------------
# UserPreferencesRepository
# ---------------------------------------------------------------------------

class UserPreferencesRepository:
    """CRUD for :class:`~speakup.database.models.UserPreferences`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create(self, user_id: int) -> UserPreferences:
        """
        Return the preferences row for *user_id*, creating it with defaults
        if it does not yet exist.
        """
        prefs = self._session.scalars(
            select(UserPreferences).where(UserPreferences.user_id == user_id)
        ).first()
        if prefs is None:
            from speakup.config.settings import settings  # lazy
            prefs = UserPreferences(
                user_id=user_id,
                difficulty_preference=settings.GAMIFICATION_DEFAULT_DIFFICULTY,
                daily_goal_type=settings.GAMIFICATION_DEFAULT_GOAL,
            )
            self._session.add(prefs)
            self._session.flush()
            logger.debug("Created default UserPreferences for user_id=%s", user_id)
        return prefs

    def save(
        self,
        user_id: int,
        difficulty_preference: str | None = None,
        daily_goal_type: str | None = None,
    ) -> UserPreferences:
        """
        Upsert preferences for *user_id*.  Only updates provided fields.
        """
        prefs = self.get_or_create(user_id)
        if difficulty_preference is not None:
            prefs.difficulty_preference = difficulty_preference
        if daily_goal_type is not None:
            prefs.daily_goal_type = daily_goal_type
        self._session.flush()
        logger.debug(
            "Saved UserPreferences user_id=%s difficulty=%r goal=%r",
            user_id, prefs.difficulty_preference, prefs.daily_goal_type,
        )
        return prefs
