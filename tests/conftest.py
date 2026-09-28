"""
tests/conftest.py — shared pytest fixtures and path configuration.

Fixtures defined here are available to ALL test files without import.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from speakup.database.connection import init_db, reset_engine  # noqa: E402
from speakup.database.models import PracticeSession, User, Word  # noqa: E402
from speakup.database.repository import (  # noqa: E402
    PracticeSessionRepository,
    UserRepository,
    WordRepository,
    WordUsageRepository,
)


# ---------------------------------------------------------------------------
# Core DB fixtures (shared across all test files)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def engine():
    """
    Fresh in-memory SQLite database for each test function.
    Tears down the engine singletons after each test.
    """
    reset_engine()
    eng = init_db("sqlite:///:memory:")
    yield eng
    reset_engine()


@pytest.fixture(scope="function")
def db_session(engine):
    """
    SQLAlchemy Session bound to the in-memory test database.
    Rolled back and closed after every test.
    """
    Session = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    session = Session()
    yield session
    session.rollback()
    session.close()


# ---------------------------------------------------------------------------
# Factory helpers (available as plain functions in both test files)
# ---------------------------------------------------------------------------

def make_user(session, name: str = "Test User") -> User:
    repo = UserRepository(session)
    user = repo.create(name)
    session.commit()
    return user


def make_word(
    session,
    word: str = "Leadership",
    category: str = "Business",
    difficulty: str = "medium",
    description: str = "Test description.",
) -> Word:
    repo = WordRepository(session)
    w = repo.create(word=word, category=category, difficulty=difficulty,
                    description=description)
    session.commit()
    return w


def make_session(session, user_id: int, word_id: int) -> PracticeSession:
    repo = PracticeSessionRepository(session)
    ps = repo.create(user_id=user_id, word_id=word_id)
    session.commit()
    return ps


def make_completed_session(session, user_id: int, word_id: int) -> PracticeSession:
    """Create a session and immediately complete it (word is consumed)."""
    repo = PracticeSessionRepository(session)
    ps = repo.create(user_id=user_id, word_id=word_id)
    session.flush()
    repo.complete_session(ps.id, duration_seconds=75)
    session.commit()
    return session.get(PracticeSession, ps.id)


def make_abandoned_session(session, user_id: int, word_id: int) -> PracticeSession:
    """Create a session and immediately abandon it (word is NOT consumed)."""
    repo = PracticeSessionRepository(session)
    ps = repo.create(user_id=user_id, word_id=word_id)
    session.flush()
    repo.abandon_session(ps.id)
    session.commit()
    return session.get(PracticeSession, ps.id)
