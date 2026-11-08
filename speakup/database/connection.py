"""
Database connection management for SpeakUp.

Provides:
    - Engine creation with SQLite-specific pragmas (WAL, foreign keys)
    - Session factory (SessionLocal)
    - init_db()   — creates all tables on first launch
    - get_session() — context-manager helper for safe session use
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Generator, Iterable

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from speakup.config.settings import settings
from speakup.database.models import Base

logger = logging.getLogger(__name__)


def migrate_legacy_database(
    destination: Path, legacy_paths: Iterable[Path]
) -> bool:
    """Copy the best available legacy SQLite database without losing WAL data."""
    if destination.exists():
        return False

    candidates: list[tuple[Path, bool]] = []
    for path in dict.fromkeys(legacy_paths):
        if path == destination or not path.is_file():
            continue
        try:
            with closing(
                sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
            ) as source:
                tables = {
                    row[0]
                    for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if "users" not in tables:
                    continue
                has_credentials = (
                    "user_credentials" in tables
                    and source.execute(
                        "SELECT EXISTS (SELECT 1 FROM user_credentials)"
                    ).fetchone()[0]
                )
                integrity = source.execute("PRAGMA quick_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    continue
            candidates.append((path, bool(has_credentials)))
        except sqlite3.Error:
            logger.warning("Skipping unreadable legacy database: %s", path, exc_info=True)

    if not candidates:
        return False

    # Prefer a database with local sign-in accounts over an empty packaged copy.
    source_path = next(
        (path for path, has_credentials in candidates if has_credentials),
        candidates[0][0],
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix="speakup-", suffix=".db", delete=False
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)

    try:
        with closing(
            sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
        ) as source:
            with closing(sqlite3.connect(temporary_path)) as target:
                source.backup(target)
        os.replace(temporary_path, destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    logger.info("Migrated local database from %s to %s.", source_path, destination)
    return True


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """
    Fire after every new SQLite connection is established.

    - WAL journal mode  → better concurrent read performance
    - Foreign key enforcement (SQLite disables it by default)
    - Busy timeout     → avoid immediate 'database is locked' errors
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.close()


def create_db_engine(database_url: str | None = None) -> Engine:
    """
    Create and configure a SQLAlchemy engine.

    Args:
        database_url: Override the URL from settings (useful in tests).

    Returns:
        Configured :class:`~sqlalchemy.engine.Engine`.
    """
    url = database_url or settings.DATABASE_URL

    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        url,
        connect_args=connect_args,
        echo=settings.is_development(),  # SQL logging in dev mode
    )

    if url.startswith("sqlite"):
        event.listen(engine, "connect", _apply_sqlite_pragmas)

    logger.debug("Database engine created: %s", url)
    return engine


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

# Module-level default engine & session factory (initialised lazily)
_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def _get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=_get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionLocal


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Provide a transactional database session.

    Usage::

        with get_session() as session:
            user = session.get(User, 1)

    Automatically commits on success and rolls back on any exception.
    """
    factory = _get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db(
    database_url: str | None = None,
    *,
    seed_default_user: bool = True,
) -> Engine:
    """
    Initialise the database:

    1. Create the engine (using *database_url* override when supplied).
    2. Run ``CREATE TABLE IF NOT EXISTS`` for every model.
    3. Optionally seed a default user if the users table is empty.

    Args:
        database_url: Optional URL override (used by tests to point at an
                      in-memory or temp-file database).
        seed_default_user: Create a legacy default profile when none exist.

    Returns:
        The :class:`~sqlalchemy.engine.Engine` that was used.
    """
    global _engine, _SessionLocal

    engine = create_db_engine(database_url)

    # Replace the module-level singletons so all subsequent get_session()
    # calls use the same engine (important when tests pass a custom URL).
    _engine = engine
    _SessionLocal = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created / verified.")
    except Exception as exc:
        logger.error("Failed to create database tables: %s", exc)
        raise

    if seed_default_user:
        _seed_default_user()
    return engine


def _seed_default_user() -> None:
    """Insert a default local user if no users exist yet."""
    # Import here to avoid circular imports at module load time
    from speakup.database.models import User  # noqa: PLC0415

    try:
        with get_session() as session:
            count = session.query(User).count()
            if count == 0:
                default_user = User(name=settings.DEFAULT_USER_NAME)
                session.add(default_user)
                logger.info(
                    "Default user '%s' created.", settings.DEFAULT_USER_NAME
                )
    except Exception as exc:
        logger.error("Failed to seed default user: %s", exc)
        raise


def reset_engine() -> None:
    """
    Tear down the module-level engine and session factory.

    Primarily used by tests that need a fresh in-memory database per test.
    """
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
