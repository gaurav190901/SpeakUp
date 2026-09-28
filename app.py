"""
app.py — SpeakUp application entry point.

Startup sequence:
    1. Initialise logging
    2. Ensure runtime directories exist
    3. Initialise the database (create tables + seed default user)
    4. Seed default words if the word pool is empty
    5. Launch the PySide6 application and main window
"""

from __future__ import annotations

import sys
import logging
import platform
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so ``speakup`` is importable
# when running ``python app.py`` directly from the project root.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Bootstrap logging early (before any other speakup import so all module
# loggers inherit the configuration)
# ---------------------------------------------------------------------------
from speakup.utils.logger import setup_logging  # noqa: E402
from speakup.config.settings import settings    # noqa: E402

setup_logging(level=settings.LOG_LEVEL, log_file=settings.LOG_FILE)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Remaining imports (after logging is ready)
# ---------------------------------------------------------------------------
from PySide6.QtWidgets import QApplication      # noqa: E402
from PySide6.QtCore import Qt                   # noqa: E402
from PySide6.QtGui import QFont                 # noqa: E402

from speakup.database.connection import init_db, get_session  # noqa: E402
from speakup.database.repository import UserRepository         # noqa: E402
from speakup.services.word_service import WordService          # noqa: E402
from speakup.ui.main_window import MainWindow                  # noqa: E402
from speakup.ui.widgets.onboarding import maybe_show_onboarding  # noqa: E402


# ---------------------------------------------------------------------------
# Initialisation helpers
# ---------------------------------------------------------------------------

def _bootstrap_database() -> str:
    """
    Initialise the database and return the default user's name.

    1. Create all tables (idempotent — safe to call on every launch).
    2. Seed the default user if none exists.
    3. Return the current user's display name.
    """
    logger.info("Bootstrapping database…")
    settings.ensure_directories()

    try:
        init_db()
        logger.info("Database initialised successfully.")
    except Exception as exc:
        logger.critical("Database initialisation failed: %s", exc, exc_info=True)
        raise

    # Seed words if the word pool is empty
    try:
        word_service = WordService()
        seeded = word_service.seed_words_if_empty()
        if seeded:
            logger.info("Word pool seeded with %d words.", seeded)
    except Exception as exc:
        logger.warning("Word seeding failed (non-fatal): %s", exc)

    # Fetch the default user name for the window title / footer
    try:
        with get_session() as session:
            user = UserRepository(session).get_first()
            return user.name if user else settings.DEFAULT_USER_NAME
    except Exception as exc:
        logger.warning("Could not retrieve default user (non-fatal): %s", exc)
        return settings.DEFAULT_USER_NAME


def _install_exception_handler(app: QApplication) -> None:
    """
    Install a global uncaught-exception handler that logs and shows a dialog
    instead of crashing silently.
    """
    import traceback as _tb  # noqa: PLC0415

    def _handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        tb_text = "".join(_tb.format_exception(exc_type, exc_value, exc_traceback))
        logger.critical("Uncaught exception:\n%s", tb_text)
        try:
            from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415
            box = QMessageBox()
            box.setWindowTitle("Unexpected Error")
            box.setIcon(QMessageBox.Icon.Critical)
            box.setText(
                f"An unexpected error occurred:\n\n"
                f"{exc_type.__name__}: {exc_value}\n\n"
                "The error has been logged. The application will continue, "
                "but you may want to restart."
            )
            box.setDetailedText(tb_text)
            box.exec()
        except Exception:
            pass   # show dialog failed — already logged

    sys.excepthook = _handler


def _create_application() -> QApplication:
    """
    Create and configure the QApplication instance.

    High-DPI scaling is enabled by default in Qt 6; we just need to
    set an application-level font so it propagates everywhere.
    """
    app = QApplication(sys.argv)
    app.setApplicationName(settings.APP_NAME)
    app.setApplicationVersion(settings.APP_VERSION)
    app.setOrganizationName("SpeakUp")

    # Default font — Segoe UI on Windows, falls back gracefully on other OS
    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
    app.setFont(font)

    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """
    Application entry point.

    Returns the process exit code.
    """
    logger.info(
        "Starting %s v%s (env=%s) | Python %s | %s %s",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.APP_ENV,
        platform.python_version(),
        platform.system(),
        platform.release(),
    )

    try:
        user_name = _bootstrap_database()
    except Exception as exc:
        logger.critical("Database initialisation failed: %s", exc, exc_info=True)
        print(f"[FATAL] Database initialisation failed: {exc}", file=sys.stderr)
        return 1

    app = _create_application()
    _install_exception_handler(app)

    window = MainWindow(user_name=user_name)
    window.show()

    maybe_show_onboarding(parent=window)

    logger.info("MainWindow shown. Entering Qt event loop.")
    exit_code = app.exec()

    logger.info("%s exiting with code %d.", settings.APP_NAME, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
