"""
WordService — manages the word/topic pool and word assignment logic.

Responsibilities
----------------
- Seed the database from the bundled topics.json on first launch
- Load words from an external JSON file on demand
- Assign a random unused word to a user (with optional filters)
- Track which words are consumed (via COMPLETED sessions only)
- Report pool exhaustion with a clear message
- Expose filtering helpers for the UI (categories, difficulty levels)

Session lifecycle contract
--------------------------
    assign_topic()        → creates a PENDING session, returns the topic
    start_session()       → transitions session to IN_PROGRESS
    complete_session()    → transitions to COMPLETED, retires the word
    abandon_session()     → transitions to ABANDONED, word stays available
    request_new_topic()   → abandons any active session, assigns a new one

Only COMPLETED sessions permanently consume a word. Displaying a topic,
then clicking "New Topic", results in ABANDONED — the word can be shown
again on a future run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from speakup.data.words.loader import (
    CATEGORIES,
    DIFFICULTY_DISPLAY,
    DISPLAY_TO_DIFFICULTY,
    DIFFICULTY_LABELS,
    get_topic_hints,
    load_topics,
)
from speakup.database.connection import get_session
from speakup.database.models import SessionStatus
from speakup.database.repository import (
    PracticeSessionRepository,
    WordRepository,
    WordUsageRepository,
)

logger = logging.getLogger(__name__)

# Human-readable message shown when all words have been completed
POOL_EXHAUSTED_MESSAGE = (
    "You've completed all available topics. "
    "Add more topics or reset your practice history."
)


class WordService:
    """
    High-level operations around words, topic assignment, and session lifecycle.

    All database access goes through repository classes — this layer
    contains only business logic.
    """

    # ------------------------------------------------------------------ #
    # Seeding / loading                                                     #
    # ------------------------------------------------------------------ #

    def seed_words_if_empty(self) -> int:
        """
        Insert the bundled topics into the database if the words table is empty.

        Returns the number of words inserted (0 if already seeded).
        """
        with get_session() as session:
            repo = WordRepository(session)
            if repo.count() > 0:
                logger.debug("Words already seeded, skipping.")
                return 0
            topics = load_topics()
            created = repo.bulk_create(topics)
            logger.info("Seeded %d words into database.", len(created))
            return len(created)

    def sync_bundled_topics(self) -> int:
        """
        Bring existing database topics in line with the bundled topics.json.

        The words table is only seeded once, so later improvements to the
        bundled wording or category names would otherwise never reach an
        existing database. Topics are matched by their title (``word``); the
        category, difficulty and description are refreshed, and any new
        bundled topics are added. Practice history is untouched because word
        ids do not change.

        Returns the number of topics updated or added.
        """
        changed = 0
        with get_session() as session:
            repo = WordRepository(session)
            for topic in load_topics():
                existing = repo.get_by_word(topic["word"])
                if existing is None:
                    repo.bulk_create([topic])
                    changed += 1
                    continue
                updates = {
                    "category": topic.get("category"),
                    "difficulty": topic["difficulty"],
                    "description": topic.get("description"),
                }
                if any(getattr(existing, k) != v for k, v in updates.items()):
                    for key, value in updates.items():
                        setattr(existing, key, value)
                    changed += 1
        if changed:
            logger.info("Synced %d topics from bundled topics.json.", changed)
        return changed

    def load_words_from_file(self, file_path: str | Path) -> int:
        """
        Load words from a JSON file and insert any that don't already exist.

        The file must contain a JSON array of objects with at minimum the
        keys ``word``, ``category``, and ``difficulty``.

        Returns the number of new words inserted.

        Raises:
            FileNotFoundError: if *file_path* does not exist.
            ValueError: if the file contains invalid JSON or missing keys.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Word file not found: {path}")

        with path.open(encoding="utf-8") as fh:
            try:
                data: list[dict] = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

        if not isinstance(data, list):
            raise ValueError(f"Expected a JSON array in {path}, got {type(data).__name__}")

        with get_session() as session:
            repo = WordRepository(session)
            created = repo.bulk_create(data)
            logger.info("Loaded %d new words from %s.", len(created), path.name)
            return len(created)

    # ------------------------------------------------------------------ #
    # Topic assignment                                                      #
    # ------------------------------------------------------------------ #

    def assign_topic(
        self,
        user_id: int,
        category: str | None = None,
        difficulty_label: str | None = None,
    ) -> dict:
        """
        Assign a random unused topic to *user_id* and create a PENDING session.

        Optionally constrain by *category* (exact match) and/or
        *difficulty_label* (human-readable: ``'Beginner'``, ``'Intermediate'``,
        ``'Advanced'``).

        Returns a dict with keys:
            session_id, word_id, word, category, difficulty,
            difficulty_display, description, exhausted (bool), message (str)

        When *exhausted* is True the dict also contains the pool-exhausted
        message and ``word`` / ``session_id`` are None.
        """
        # Convert display label → DB value
        difficulty_db = DISPLAY_TO_DIFFICULTY.get(difficulty_label or "", None)

        with get_session() as session:
            session_repo = PracticeSessionRepository(session)
            word_repo = WordRepository(session)

            # Abandon any stale pending/in-progress sessions first so they
            # don't block the user from seeing a new topic
            session_repo.abandon_stale_sessions(user_id)

            word = word_repo.get_random_unused_for_user(
                user_id,
                category=category,
                difficulty=difficulty_db,
            )

            if word is None:
                logger.info(
                    "Word pool exhausted for user_id=%s (category=%r difficulty=%r)",
                    user_id, category, difficulty_label,
                )
                return {
                    "session_id": None,
                    "word_id": None,
                    "word": None,
                    "category": category,
                    "difficulty": difficulty_db,
                    "difficulty_display": difficulty_label,
                    "description": None,
                    "exhausted": True,
                    "message": POOL_EXHAUSTED_MESSAGE,
                }

            # Create a PENDING session (word NOT yet consumed)
            ps = session_repo.create(user_id=user_id, word_id=word.id)

            return {
                "session_id": ps.id,
                "word_id": word.id,
                "word": word.word,
                "category": word.category,
                "difficulty": word.difficulty,
                "difficulty_display": DIFFICULTY_DISPLAY.get(word.difficulty, word.difficulty),
                "description": word.description,
                "hints": get_topic_hints(word.word),
                "exhausted": False,
                "message": "",
            }

    def request_new_topic(
        self,
        user_id: int,
        current_session_id: int | None,
        category: str | None = None,
        difficulty_label: str | None = None,
    ) -> dict:
        """
        Abandon the current session (if any) and assign a fresh topic.

        The word from the abandoned session is NOT consumed — it stays
        available for future assignment.

        Returns the same dict shape as :meth:`assign_topic`.
        """
        if current_session_id is not None:
            with get_session() as session:
                PracticeSessionRepository(session).abandon_session(current_session_id)
                logger.debug(
                    "Abandoned session id=%s on new topic request", current_session_id
                )

        return self.assign_topic(
            user_id=user_id,
            category=category,
            difficulty_label=difficulty_label,
        )

    # ------------------------------------------------------------------ #
    # Session lifecycle delegates                                           #
    # ------------------------------------------------------------------ #

    def start_practice(self, session_id: int) -> bool:
        """
        Transition the session to IN_PROGRESS.

        Returns True on success, False if the session was not found.
        """
        with get_session() as session:
            ps = PracticeSessionRepository(session).start_session(session_id)
            return ps is not None

    def complete_practice(
        self,
        session_id: int,
        duration_seconds: int,
        recording_path: str | None = None,
        transcript: str | None = None,
    ) -> bool:
        """
        Mark the session COMPLETED and permanently retire the word.

        Returns True on success, False if the session was not found.
        """
        with get_session() as session:
            ps = PracticeSessionRepository(session).complete_session(
                session_id=session_id,
                duration_seconds=duration_seconds,
                recording_path=recording_path,
                transcript=transcript,
            )
            return ps is not None and ps.status == SessionStatus.COMPLETED

    def abandon_practice(self, session_id: int) -> bool:
        """
        Mark the session ABANDONED.  Word is NOT consumed.

        Returns True on success, False if the session was not found.
        """
        with get_session() as session:
            ps = PracticeSessionRepository(session).abandon_session(session_id)
            return ps is not None

    # ------------------------------------------------------------------ #
    # Queries                                                               #
    # ------------------------------------------------------------------ #

    def get_next_word_for_user(
        self,
        user_id: int,
        category: str | None = None,
        difficulty_label: str | None = None,
    ) -> Optional[dict]:
        """
        Return one random unused word as a plain dict, or None if exhausted.

        Does NOT create a session or consume the word.  Use
        :meth:`assign_topic` when you want full session management.
        """
        difficulty_db = DISPLAY_TO_DIFFICULTY.get(difficulty_label or "", None)
        with get_session() as session:
            repo = WordRepository(session)
            word = repo.get_random_unused_for_user(
                user_id, category=category, difficulty=difficulty_db
            )
            if word is None:
                return None
            return {
                "id": word.id,
                "word": word.word,
                "category": word.category,
                "difficulty": word.difficulty,
                "difficulty_display": DIFFICULTY_DISPLAY.get(word.difficulty, word.difficulty),
                "description": word.description,
            }

    def get_unused_count_for_user(
        self,
        user_id: int,
        category: str | None = None,
        difficulty_label: str | None = None,
    ) -> int:
        """Return the count of unused words, optionally filtered."""
        difficulty_db = DISPLAY_TO_DIFFICULTY.get(difficulty_label or "", None)
        with get_session() as session:
            return WordRepository(session).count_unused_for_user(
                user_id, category=category, difficulty=difficulty_db
            )

    def get_total_word_count(self) -> int:
        """Return the total number of words in the database."""
        with get_session() as session:
            return WordRepository(session).count()

    def get_all_words(self) -> list[dict]:
        """Return all words as plain dicts."""
        with get_session() as session:
            return [
                {
                    "id": w.id,
                    "word": w.word,
                    "category": w.category,
                    "difficulty": w.difficulty,
                    "difficulty_display": DIFFICULTY_DISPLAY.get(w.difficulty, w.difficulty),
                    "description": w.description,
                }
                for w in WordRepository(session).get_all()
            ]

    def is_pool_exhausted(
        self,
        user_id: int,
        category: str | None = None,
        difficulty_label: str | None = None,
    ) -> bool:
        """Return True when no unused words remain for the given filters."""
        return self.get_unused_count_for_user(
            user_id, category=category, difficulty_label=difficulty_label
        ) == 0

    # ------------------------------------------------------------------ #
    # Filter helpers (used by UI dropdowns)                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_categories() -> list[str]:
        """Return the sorted list of all topic categories."""
        return CATEGORIES

    @staticmethod
    def get_difficulty_labels() -> list[str]:
        """Return human-readable difficulty labels in order: Beginner → Advanced."""
        return DIFFICULTY_LABELS

    @staticmethod
    def difficulty_display(db_value: str) -> str:
        """Convert a DB difficulty value to its display label."""
        return DIFFICULTY_DISPLAY.get(db_value, db_value)

    @staticmethod
    def difficulty_db_value(display_label: str) -> str | None:
        """Convert a display label back to its DB value, or None if unknown."""
        return DISPLAY_TO_DIFFICULTY.get(display_label)
