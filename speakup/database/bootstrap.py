"""Shared start-up routine for the desktop and web apps."""

from __future__ import annotations

import logging

from speakup.config.settings import settings
from speakup.database.connection import init_db, migrate_legacy_database

logger = logging.getLogger(__name__)


def bootstrap_database() -> None:
    """Initialise the database and make sure the topic pool is up to date.

    1. Create all tables (idempotent — safe to call on every launch).
    2. Move a legacy database into the per-user data folder (packaged app).
    3. Seed topics into an empty database, or refresh the bundled wording.
    """
    from speakup.services.word_service import WordService  # noqa: PLC0415

    logger.info("Bootstrapping database…")
    settings.ensure_directories()

    try:
        if settings.IS_FROZEN and settings.DATABASE_PATH is not None:
            migrate_legacy_database(
                settings.DATABASE_PATH, settings.LEGACY_DATABASE_PATHS
            )
        init_db(seed_default_user=False)
        logger.info("Database initialised successfully.")
    except Exception as exc:
        logger.critical("Database initialisation failed: %s", exc, exc_info=True)
        raise

    try:
        word_service = WordService()
        seeded = word_service.seed_words_if_empty()
        if seeded:
            logger.info("Word pool seeded with %d words.", seeded)
        else:
            word_service.sync_bundled_topics()
    except Exception as exc:
        logger.warning("Word seeding failed (non-fatal): %s", exc)
