"""
SQLAlchemy ORM models for the SpeakUp application.

Tables:
    - users            : local user profiles
    - words            : English words / topics for practice
    - practice_sessions: individual speaking sessions
    - word_usage       : tracks which words a user has completed (prevents repeats)
    - analysis_results : AI-generated scores and feedback per session (future)

Session Status State Machine
-----------------------------
    PENDING ──► IN_PROGRESS ──► COMPLETED   (word is consumed, no repeat)
       │                   └──► ABANDONED   (word is NOT consumed, can be reassigned)
       └──────────────────────► ABANDONED

Only COMPLETED sessions permanently retire a word for a user (via word_usage table).
ABANDONED sessions leave the word available for future assignment.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Session status constants
# ---------------------------------------------------------------------------

class SessionStatus:
    """
    Named constants for practice session status values.

    Use these instead of raw strings to avoid typos.

    Lifecycle:
        PENDING      — session row created, topic assigned, not yet started
        IN_PROGRESS  — user has clicked Start Practice, recording is active
        COMPLETED    — recording stopped, duration ≥ minimum, word consumed
        ABANDONED    — user dismissed or navigated away; word NOT consumed
    """
    PENDING     = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    ABANDONED   = "abandoned"

    ALL: tuple[str, ...] = (PENDING, IN_PROGRESS, COMPLETED, ABANDONED)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Shared declarative base for all models."""
    pass


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

class User(Base):
    """A local user profile.  One installation typically has one user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Local User")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # relationships
    practice_sessions: Mapped[list[PracticeSession]] = relationship(
        "PracticeSession", back_populates="user", cascade="all, delete-orphan"
    )
    word_usages: Mapped[list[WordUsage]] = relationship(
        "WordUsage", back_populates="user", cascade="all, delete-orphan"
    )
    preferences: Mapped["UserPreferences | None"] = relationship(
        "UserPreferences", back_populates="user", uselist=False,
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# words
# ---------------------------------------------------------------------------

class Word(Base):
    """An English word or topic presented to users for practice."""

    __tablename__ = "words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium"
    )  # easy | medium | hard
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # relationships
    practice_sessions: Mapped[list[PracticeSession]] = relationship(
        "PracticeSession", back_populates="word"
    )
    word_usages: Mapped[list[WordUsage]] = relationship(
        "WordUsage", back_populates="word"
    )

    # constraints & indexes
    __table_args__ = (
        CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="ck_word_difficulty",
        ),
        Index("ix_word_category", "category"),
        Index("ix_word_difficulty", "difficulty"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Word id={self.id} word={self.word!r} difficulty={self.difficulty!r}>"


# ---------------------------------------------------------------------------
# practice_sessions
# ---------------------------------------------------------------------------

class PracticeSession(Base):
    """
    A single speaking session undertaken by a user.

    Status lifecycle — see :class:`SessionStatus`:
        PENDING → IN_PROGRESS → COMPLETED   (word permanently consumed)
                             └→ ABANDONED   (word stays available)

    A word is only retired (added to word_usage) when status reaches COMPLETED.
    Displaying a word, then navigating away, results in ABANDONED — the word
    can be assigned again on the next practice attempt.
    """

    __tablename__ = "practice_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    word_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("words.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | in_progress | completed | abandoned

    # relationships
    user: Mapped[User] = relationship("User", back_populates="practice_sessions")
    word: Mapped[Word] = relationship("Word", back_populates="practice_sessions")
    analysis_result: Mapped[AnalysisResult | None] = relationship(
        "AnalysisResult", back_populates="session", uselist=False,
        cascade="all, delete-orphan"
    )
    word_usage: Mapped[WordUsage | None] = relationship(
        "WordUsage", back_populates="session", uselist=False
    )

    # constraints & indexes
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'abandoned')",
            name="ck_session_status",
        ),
        Index("ix_session_user_id", "user_id"),
        Index("ix_session_word_id", "word_id"),
        Index("ix_session_status", "status"),
        Index("ix_session_started_at", "started_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PracticeSession id={self.id} user_id={self.user_id} "
            f"word_id={self.word_id} status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# word_usage
# ---------------------------------------------------------------------------

class WordUsage(Base):
    """
    Records that a specific user has completed a word.

    The UNIQUE constraint on (user_id, word_id) is what enforces the
    "no-repeat" rule — once a row exists here the word is exhausted for
    that user and must not be assigned again.
    """

    __tablename__ = "word_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    word_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("practice_sessions.id", ondelete="SET NULL"), nullable=True
    )
    used_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # relationships
    user: Mapped[User] = relationship("User", back_populates="word_usages")
    word: Mapped[Word] = relationship("Word", back_populates="word_usages")
    session: Mapped[PracticeSession | None] = relationship(
        "PracticeSession", back_populates="word_usage"
    )

    # The core no-repeat guarantee
    __table_args__ = (
        UniqueConstraint("user_id", "word_id", name="uq_word_usage_user_word"),
        Index("ix_word_usage_user_id", "user_id"),
        Index("ix_word_usage_word_id", "word_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WordUsage id={self.id} user_id={self.user_id} word_id={self.word_id}>"


# ---------------------------------------------------------------------------
# analysis_results
# ---------------------------------------------------------------------------

class AnalysisResult(Base):
    """
    AI-generated analysis scores and feedback for a completed session.
    Populated in a future chunk when speech analysis is implemented.
    """

    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("practice_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one result per session
    )
    pronunciation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    fluency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    grammar_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    vocabulary_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    filler_word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    words_per_minute: Mapped[float | None] = mapped_column(Float, nullable=True)
    pause_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # relationships
    session: Mapped[PracticeSession] = relationship(
        "PracticeSession", back_populates="analysis_result"
    )

    # constraints & indexes
    __table_args__ = (
        CheckConstraint(
            "overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)",
            name="ck_analysis_overall_score_range",
        ),
        Index("ix_analysis_session_id", "session_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AnalysisResult id={self.id} session_id={self.session_id} "
            f"overall_score={self.overall_score}>"
        )


# ---------------------------------------------------------------------------
# user_preferences
# ---------------------------------------------------------------------------

class UserPreferences(Base):
    """
    Per-user preferences for gamification, difficulty, and daily goals.

    One row per user (unique on user_id).  Created lazily on first save.
    Defaults are applied by the repository's get_or_create method so no
    row is required to exist at startup.
    """

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )

    # Difficulty: "all" | "easy" | "medium" | "hard"
    difficulty_preference: Mapped[str] = mapped_column(
        String(20), nullable=False, default="all"
    )

    # Daily goal type: "sessions" | "minutes_5" | "minutes_10"
    daily_goal_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="sessions"
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # relationships
    user: Mapped[User] = relationship("User", back_populates="preferences")

    __table_args__ = (
        CheckConstraint(
            "difficulty_preference IN ('all', 'easy', 'medium', 'hard')",
            name="ck_prefs_difficulty",
        ),
        CheckConstraint(
            "daily_goal_type IN ('sessions', 'minutes_5', 'minutes_10')",
            name="ck_prefs_goal_type",
        ),
        Index("ix_user_preferences_user_id", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<UserPreferences user_id={self.user_id} "
            f"difficulty={self.difficulty_preference!r} goal={self.daily_goal_type!r}>"
        )
