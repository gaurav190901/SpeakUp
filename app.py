"""
app.py — SpeakUp application entry point.

Startup sequence:
    1. Initialise logging
    2. Launch the PySide6 application and show the startup screen
    3. Ensure runtime directories exist
    4. Initialise the database and seed default words if needed
    5. Build and show the main window
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
from PySide6.QtWidgets import QApplication, QDialog, QSplashScreen  # noqa: E402
from PySide6.QtCore import QRect, Qt                       # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402

from speakup.database.bootstrap import bootstrap_database      # noqa: E402
from speakup.ui.main_window import MainWindow                  # noqa: E402
from speakup.ui.widgets.onboarding import maybe_show_onboarding  # noqa: E402
from speakup.ui.widgets.auth_dialog import AuthDialog          # noqa: E402


# ---------------------------------------------------------------------------
# Initialisation helpers
# ---------------------------------------------------------------------------

def _bootstrap_database() -> None:
    """Initialise the database and seed default words if needed."""
    bootstrap_database()


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


def _create_splash_screen() -> QSplashScreen:
    """Show a branded startup screen while local data and pages load."""
    pixmap = QPixmap(440, 220)
    pixmap.fill(QColor(settings.COLOR_BG_DARK))

    painter = QPainter(pixmap)
    painter.setPen(QColor(settings.COLOR_TEXT_PRIMARY))
    painter.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
    painter.drawText(QRect(24, 56, 392, 48), Qt.AlignmentFlag.AlignCenter, settings.APP_NAME)
    painter.fillRect(QRect(194, 116, 52, 3), QColor(settings.COLOR_ACCENT))
    painter.setPen(QColor(settings.COLOR_TEXT_SECONDARY))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(QRect(24, 130, 392, 28), Qt.AlignmentFlag.AlignCenter, "Speaking Practice")
    painter.end()

    splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
    splash.showMessage(
        "Preparing your workspace...",
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
        QColor(settings.COLOR_TEXT_SECONDARY),
    )
    splash.show()
    QApplication.processEvents()
    return splash


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

    app = _create_application()
    _install_exception_handler(app)
    splash = _create_splash_screen()

    try:
        _bootstrap_database()
    except Exception as exc:
        splash.close()
        logger.critical("Database initialisation failed: %s", exc, exc_info=True)
        print(f"[FATAL] Database initialisation failed: {exc}", file=sys.stderr)
        return 1

    splash.showMessage(
        "Sign in or create an account...",
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
        QColor(settings.COLOR_TEXT_SECONDARY),
    )
    app.processEvents()
    auth_dialog = AuthDialog(splash)
    if auth_dialog.exec() != QDialog.DialogCode.Accepted:
        splash.close()
        return 0

    account = auth_dialog.authenticated_account
    if account is None:
        splash.close()
        return 0

    user_id, user_name = account
    window = MainWindow(user_id=user_id, user_name=user_name)
    window.show()
    splash.finish(window)

    active_window = [window]

    def _switch_profile(next_user_id: int, next_user_name: str) -> None:
        previous_window = active_window[0]
        next_window = MainWindow(
            user_id=next_user_id,
            user_name=next_user_name,
        )
        next_window.profile_switch_requested.connect(_switch_profile)
        next_window.logout_requested.connect(_sign_out)
        active_window[0] = next_window
        next_window.show()
        previous_window.close()

    def _sign_out() -> None:
        previous_window = active_window[0]
        previous_window.hide()
        sign_in_dialog = AuthDialog()
        if sign_in_dialog.exec() != QDialog.DialogCode.Accepted:
            previous_window.close()
            app.quit()
            return

        next_account = sign_in_dialog.authenticated_account
        if next_account is None:
            previous_window.close()
            app.quit()
            return
        _switch_profile(*next_account)

    window.profile_switch_requested.connect(_switch_profile)
    window.logout_requested.connect(_sign_out)

    maybe_show_onboarding(parent=window)

    logger.info("MainWindow shown. Entering Qt event loop.")
    exit_code = app.exec()

    logger.info("%s exiting with code %d.", settings.APP_NAME, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
