"""
MainWindow — the application shell.

Layout
------
┌─────────────────────────────────────────────┐
│  Sidebar (fixed 220 px)  │  Content stack   │
│  ─────────────────────── │  ─────────────── │
│  [logo / app name]        │  DashboardPage   │
│                           │  PracticePage    │
│  ◉ Dashboard              │  HistoryPage     │
│  ○ Practice               │  SettingsPage    │
│  ○ History                │                  │
│  ○ Settings               │                  │
│                           │                  │
│  [user name at bottom]    │                  │
└─────────────────────────────────────────────┘

The sidebar uses a QButtonGroup so only one nav button is active at a time.
Page switching is handled by a QStackedWidget.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings
from speakup.ui.pages.dashboard_page import DashboardPage
from speakup.ui.pages.data_management_page import DataManagementPage
from speakup.ui.pages.history_page import HistoryPage
from speakup.ui.pages.practice_page import PracticePage
from speakup.ui.pages.recording_page import RecordingPage
from speakup.ui.pages.session_results_page import SessionResultsPage
from speakup.ui.pages.settings_page import SettingsPage
from speakup.ui.pages.transcription_result_page import TranscriptionResultPage
from speakup.services.word_service import WordService
from speakup.ui.widgets.auth_dialog import AuthDialog

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page index constants
# ---------------------------------------------------------------------------
PAGE_DASHBOARD    = 0
PAGE_PRACTICE     = 1
PAGE_HISTORY      = 2
PAGE_SETTINGS     = 3
PAGE_RECORDING    = 4   # No sidebar nav button — launched from PracticePage
PAGE_TRANSCRIPTION = 5  # No sidebar nav button — launched after recording
PAGE_RESULTS      = 6   # No sidebar nav button — launched after transcription
PAGE_DATA         = 7   # No sidebar nav button — launched from Settings

_NAV_ITEMS = [
    (PAGE_DASHBOARD, "Dashboard",  "📊"),
    (PAGE_PRACTICE,  "Practice",   "🎤"),
    (PAGE_HISTORY,   "History",    "📋"),
    (PAGE_SETTINGS,  "Settings",   "⚙️"),
]


class MainWindow(QMainWindow):
    """Top-level application window."""

    profile_switch_requested = Signal(int, str)
    logout_requested = Signal()

    def __init__(
        self, user_id: int = 1, user_name: str = "Local User"
    ) -> None:
        super().__init__()
        self._user_id = user_id
        self._user_name = user_name
        self._setup_window()
        self._build_ui()
        self._apply_styles()
        logger.info("MainWindow initialised for user: %s", user_name)

    # ------------------------------------------------------------------ #
    # Window setup                                                          #
    # ------------------------------------------------------------------ #

    def _setup_window(self) -> None:
        self.setWindowTitle(settings.APP_NAME)
        self.setMinimumSize(settings.WINDOW_MIN_WIDTH, settings.WINDOW_MIN_HEIGHT)
        self.resize(settings.WINDOW_DEFAULT_WIDTH, settings.WINDOW_DEFAULT_HEIGHT)
        self.setObjectName("MainWindow")

    def closeEvent(self, event) -> None:
        """
        Handle main window close event.
        Ensures active page worker threads and services (recording camera/mic,
        transcription workers, analysis threads) are cleanly stopped before exit.
        """
        logger.info("MainWindow closing. Cleaning up background services...")
        try:
            if hasattr(self, "_recording_page") and self._recording_page:
                self._recording_page.cleanup()
            if hasattr(self, "_transcription_page") and self._transcription_page:
                self._transcription_page.cleanup()
        except Exception as exc:
            logger.warning("Error during MainWindow close cleanup: %s", exc)

        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        # Root widget
        root = QWidget()
        root.setObjectName("RootWidget")
        self.setCentralWidget(root)

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ---- Sidebar ----
        self._sidebar = self._build_sidebar()
        root_layout.addWidget(self._sidebar)

        # ---- Thin divider line ----
        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        divider.setFixedWidth(1)
        divider.setFrameShape(QFrame.Shape.VLine)
        root_layout.addWidget(divider)

        # ---- Content area ----
        self._stack = QStackedWidget()
        self._stack.setObjectName("ContentStack")

        self._dashboard_page      = DashboardPage(
            user_id=self._user_id, user_name=self._user_name
        )
        self._practice_page       = PracticePage(user_id=self._user_id)
        self._history_page        = HistoryPage(user_id=self._user_id)
        self._settings_page       = SettingsPage(
            user_id=self._user_id, user_name=self._user_name
        )
        self._recording_page      = RecordingPage()
        self._transcription_page  = TranscriptionResultPage()
        self._results_page        = SessionResultsPage()
        self._data_page           = DataManagementPage(user_id=self._user_id)
        self._data_page.profile_data_deleted.connect(self._on_profile_data_deleted)

        self._stack.addWidget(self._dashboard_page)      # index 0
        self._stack.addWidget(self._practice_page)       # index 1
        self._stack.addWidget(self._history_page)        # index 2
        self._stack.addWidget(self._settings_page)       # index 3
        self._stack.addWidget(self._recording_page)      # index 4
        self._stack.addWidget(self._transcription_page)  # index 5
        self._stack.addWidget(self._results_page)        # index 6
        self._stack.addWidget(self._data_page)           # index 7

        # Connect PracticePage → RecordingPage navigation
        self._practice_page.navigate_to_recording.connect(self._on_start_recording)

        # Connect RecordingPage completion/abandon signals back
        self._recording_page.session_completed.connect(self._on_session_completed)
        self._recording_page.session_abandoned.connect(self._on_session_abandoned)

        # Connect TranscriptionResultPage signals back to MainWindow
        self._transcription_page.transcription_saved.connect(
            self._on_transcription_saved
        )
        self._transcription_page.transcription_skipped.connect(
            self._on_transcription_skipped
        )

        # Connect SessionResultsPage navigation signals
        self._results_page.practice_again.connect(self._on_practice_again)
        self._results_page.go_to_dashboard.connect(self._on_go_to_dashboard)

        # Connect DashboardPage view_session signal
        self._dashboard_page.view_session.connect(self._on_view_session_from_dashboard)
        self._dashboard_page.start_practice_requested.connect(
            lambda: self._navigate_to(PAGE_PRACTICE)
        )

        # Connect HistoryPage view_session → results page
        self._history_page.view_session.connect(self._on_view_session_from_history)

        # Connect SettingsPage preferences_saved → refresh practice page difficulty
        self._settings_page.preferences_saved.connect(
            self._practice_page._apply_saved_difficulty
        )

        # Connect SettingsPage → DataManagementPage navigation
        if hasattr(self._settings_page, "open_data_management"):
            self._settings_page.open_data_management.connect(
                lambda: self._navigate_to(PAGE_DATA)
            )

        root_layout.addWidget(self._stack, stretch=1)

        # Default page
        self._navigate_to(PAGE_DASHBOARD)

        # Keyboard shortcuts
        self._install_shortcuts()

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(settings.SIDEBAR_WIDTH)
        sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- Logo / app name ----
        logo_container = QWidget()
        logo_container.setObjectName("LogoContainer")
        logo_container.setFixedHeight(72)
        logo_layout = QHBoxLayout(logo_container)
        logo_layout.setContentsMargins(20, 0, 20, 0)
        logo_layout.setSpacing(12)
        logo_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        logo_badge = QLabel("🎙️")
        logo_badge.setObjectName("LogoBadge")
        logo_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_badge.setFixedSize(38, 38)
        logo_layout.addWidget(logo_badge)

        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        app_label = QLabel(settings.APP_NAME)
        app_label.setObjectName("AppNameLabel")
        name_col.addWidget(app_label)
        tagline = QLabel("Speaking Practice")
        tagline.setObjectName("TaglineLabel")
        name_col.addWidget(tagline)
        logo_layout.addLayout(name_col)
        logo_layout.addStretch()

        layout.addWidget(logo_container)

        # ---- Separator ----
        layout.addWidget(self._make_h_separator())

        # ---- Nav buttons ----
        nav_container = QWidget()
        nav_container.setObjectName("NavContainer")
        nav_layout = QVBoxLayout(nav_container)
        nav_layout.setContentsMargins(12, 16, 12, 16)
        nav_layout.setSpacing(4)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: dict[int, QPushButton] = {}

        for page_idx, label, icon_char in _NAV_ITEMS:
            btn = self._make_nav_button(icon_char, label, page_idx)
            self._nav_group.addButton(btn, page_idx)
            self._nav_buttons[page_idx] = btn
            nav_layout.addWidget(btn)

        nav_layout.addStretch()
        layout.addWidget(nav_container, stretch=1)

        # ---- Separator ----
        layout.addWidget(self._make_h_separator())

        # ---- User info footer ----
        footer = QWidget()
        footer.setObjectName("SidebarFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(16, 16, 16, 14)
        footer_layout.setSpacing(12)

        user_row = QHBoxLayout()
        user_row.setSpacing(10)
        initials = "".join(part[0] for part in self._user_name.split()[:2]).upper() or "?"
        avatar = QLabel(initials)
        avatar.setObjectName("UserAvatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(36, 36)
        user_row.addWidget(avatar)

        user_col = QVBoxLayout()
        user_col.setSpacing(0)
        user_label = QLabel(self._user_name)
        user_label.setObjectName("UserNameLabel")
        user_col.addWidget(user_label)
        account_label = QLabel("Local account")
        account_label.setObjectName("AccountTypeLabel")
        user_col.addWidget(account_label)
        user_row.addLayout(user_col, stretch=1)
        footer_layout.addLayout(user_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        switch_profile_button = QPushButton("⇄  Switch")
        switch_profile_button.setObjectName("SwitchProfileButton")
        switch_profile_button.setFixedHeight(30)
        switch_profile_button.setCursor(Qt.CursorShape.PointingHandCursor)
        switch_profile_button.setToolTip("Sign in to another account or create one")
        switch_profile_button.clicked.connect(self._on_switch_profile)
        self._switch_profile_button = switch_profile_button
        actions.addWidget(switch_profile_button)

        sign_out_button = QPushButton("Sign out")
        sign_out_button.setObjectName("SignOutButton")
        sign_out_button.setFixedHeight(30)
        sign_out_button.setCursor(Qt.CursorShape.PointingHandCursor)
        sign_out_button.clicked.connect(self.logout_requested.emit)
        actions.addWidget(sign_out_button)
        footer_layout.addLayout(actions)

        version_label = QLabel(f"v{settings.APP_VERSION}")
        version_label.setObjectName("VersionLabel")
        footer_layout.addWidget(version_label)

        layout.addWidget(footer)

        return sidebar

    def _on_switch_profile(self) -> None:
        dialog = AuthDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        account = dialog.authenticated_account
        if account and account[0] != self._user_id:
            self.profile_switch_requested.emit(*account)

    def _on_profile_data_deleted(self) -> None:
        self._practice_page.reset_after_profile_data_deleted()
        self._dashboard_page._load_all()
        self._history_page._load_history()

    # ------------------------------------------------------------------ #
    # Navigation                                                            #
    # ------------------------------------------------------------------ #

    def _navigate_to(self, page_index: int) -> None:
        self._stack.setCurrentIndex(page_index)

        btn = self._nav_buttons.get(page_index)
        if btn and not btn.isChecked():
            btn.setChecked(True)

        page_names = {
            PAGE_DASHBOARD:     "Dashboard",
            PAGE_PRACTICE:      "Practice",
            PAGE_HISTORY:       "History",
            PAGE_SETTINGS:      "Settings",
            PAGE_RECORDING:     "Recording",
            PAGE_TRANSCRIPTION: "Transcription",
            PAGE_RESULTS:       "Results",
            PAGE_DATA:          "Data Management",
        }
        logger.debug("Navigated to %s", page_names.get(page_index, page_index))

    def _install_shortcuts(self) -> None:
        """Install application-wide keyboard shortcuts."""
        shortcuts = [
            ("Ctrl+D", lambda: self._navigate_to(PAGE_DASHBOARD)),
            ("Ctrl+P", lambda: self._navigate_to(PAGE_PRACTICE)),
            ("Ctrl+H", lambda: self._navigate_to(PAGE_HISTORY)),
            ("Ctrl+,", lambda: self._navigate_to(PAGE_SETTINGS)),
            ("Ctrl+N", self._shortcut_new_topic),
            ("Escape", self._shortcut_escape),
        ]
        for key, handler in shortcuts:
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(handler)

    def _shortcut_new_topic(self) -> None:
        """Ctrl+N — request a new topic if on Practice page."""
        if self._stack.currentIndex() == PAGE_PRACTICE:
            self._practice_page._on_new_topic()

    def _shortcut_escape(self) -> None:
        """Escape — abandon current recording / go back to safe page."""
        idx = self._stack.currentIndex()
        if idx == PAGE_RECORDING:
            self._recording_page._on_abandon()
        elif idx in (PAGE_TRANSCRIPTION, PAGE_RESULTS, PAGE_DATA):
            self._navigate_to(PAGE_PRACTICE)

    def _on_start_recording(self, topic: dict, session_id: int) -> None:
        """
        Called when PracticePage emits ``navigate_to_recording``.
        Prepares the RecordingPage then switches to it.
        """
        logger.info(
            "Navigating to RecordingPage  session_id=%s  word=%r",
            session_id, topic.get("word"),
        )
        self._recording_page.prepare(topic, session_id)
        self._switch_profile_button.setEnabled(False)
        self._navigate_to(PAGE_RECORDING)

    def _on_session_completed(
        self, session_id: int, duration: int, recording_path: str
    ) -> None:
        """
        Called when RecordingPage emits ``session_completed``.

        Stores the session (without transcript yet), cleans up recording
        resources, then navigates to TranscriptionResultPage which will
        handle async transcription and emit ``transcription_saved``.
        """
        logger.info(
            "Session completed  session_id=%s  duration=%ds  path=%s",
            session_id, duration, recording_path,
        )

        # Store session as completed (transcript=None for now — will be
        # updated when transcription_saved signal fires)
        try:
            word_service = WordService()
            word_service.complete_practice(
                session_id=session_id,
                duration_seconds=duration,
                recording_path=recording_path,
                # transcript stored later via _on_transcription_saved
            )
        except Exception as exc:
            logger.error("Failed to complete practice session: %s", exc)

        # Release camera/mic resources
        self._recording_page.cleanup()
        self._switch_profile_button.setEnabled(True)

        # Clear the completed session so Practice is ready when revisited.
        self._practice_page.on_recording_completed()

        # Navigate to transcription page — keep topic from recording page
        topic = self._recording_page._topic or {}
        self._transcription_page.prepare(
            session_id=session_id,
            recording_path=recording_path,
            topic=topic,
        )
        self._navigate_to(PAGE_TRANSCRIPTION)

    def _on_session_abandoned(self, session_id: int) -> None:
        """
        Called when RecordingPage emits ``session_abandoned``.
        Abandons the session and returns to Practice page.
        """
        logger.info("Session abandoned  session_id=%s", session_id)
        try:
            word_service = WordService()
            word_service.abandon_practice(session_id)
        except Exception as exc:
            logger.error("Failed to abandon session: %s", exc)

        self._recording_page.cleanup()
        self._switch_profile_button.setEnabled(True)
        self._navigate_to(PAGE_PRACTICE)
        self._practice_page.on_recording_abandoned()

    def _on_transcription_saved(self, session_id: int, transcript: str) -> None:
        """
        Called when TranscriptionResultPage has a completed transcript.

        Updates the practice_session.transcript field in the database.
        Does NOT navigate — the user still sees the transcript and clicks Continue.
        """
        logger.info(
            "Transcription saved  session_id=%s  words=%d",
            session_id, len(transcript.split()),
        )
        try:
            from speakup.database.connection import get_session as db_get_session
            from speakup.database.repository import PracticeSessionRepository
            with db_get_session() as session:
                PracticeSessionRepository(session).update_status(
                    session_id=session_id,
                    status="completed",
                    transcript=transcript,
                )
        except Exception as exc:
            logger.error("Failed to store transcript: %s", exc)

    def _on_transcription_skipped(self, session_id: int) -> None:
        """
        Called when the user clicks Continue on TranscriptionResultPage.

        Collects the latest analysis result (already stored in the DB by
        TranscriptionResultPage's async worker) and navigates to the
        Results page.  Falls back to Practice if no analysis data exists.
        """
        logger.info("Transcription page Continue  session_id=%s", session_id)

        # Fetch the stored analysis result and session metadata
        try:
            from speakup.services.analysis_service import AnalysisService
            from speakup.database.connection import get_session as db_gs
            from speakup.database.repository import PracticeSessionRepository

            analysis_svc    = AnalysisService()
            analysis_result = analysis_svc.get_result_for_session(session_id) or {}

            duration_seconds = 0
            with db_gs() as db_session:
                ps = PracticeSessionRepository(db_session).get_by_id(session_id)
                if ps:
                    duration_seconds = ps.duration_seconds or 0

        except Exception as exc:
            logger.error("Could not load results for session %s: %s", session_id, exc)
            analysis_result  = {}
            duration_seconds = 0

        # Retrieve transcript from the transcription page (already decoded)
        transcript = ""
        if self._transcription_page._transcript_result:
            transcript = self._transcription_page._transcript_result.to_plain_text()

        # Get topic from transcription page state
        topic = self._transcription_page._topic or {}

        # Clean up transcription page resources
        self._transcription_page.cleanup()

        # Enrich analysis_result with the ui_dict extras if we only have the
        # DB-stored dict (which lacks strengths/improvements/suggestions).
        # Re-run feedback generation from stored feedback text if needed.
        if analysis_result and not analysis_result.get("strengths"):
            analysis_result = self._enrich_analysis_result(analysis_result)

        # Prepare and navigate to the results page
        self._results_page.prepare(
            session_id=session_id,
            topic=topic,
            analysis_result=analysis_result,
            duration_seconds=duration_seconds,
            transcript=transcript,
        )
        self._navigate_to(PAGE_RESULTS)

    def _enrich_analysis_result(self, db_result: dict) -> dict:
        """
        The DB-stored result (from get_result_for_session) only has the 9
        core columns.  Re-run the analysis pipeline minimally to recover
        the structured feedback lists and extra UI keys.
        """
        try:
            from speakup.services.analysis_service import AnalysisService
            svc = AnalysisService()
            feedback_text = db_result.get("feedback", "") or ""

            # Parse strengths/improvements/suggestions from the stored text
            strengths:    list[str] = []
            improvements: list[str] = []
            suggestions:  list[str] = []

            section = None
            for line in feedback_text.splitlines():
                stripped = line.strip()
                if stripped.startswith("Strengths:"):
                    section = "strengths"
                elif stripped.startswith("Areas to improve:"):
                    section = "improvements"
                elif stripped.startswith("Suggestions:"):
                    section = "suggestions"
                elif stripped.startswith("•") and section:
                    text = stripped.lstrip("•").strip()
                    if section == "strengths":
                        strengths.append(text)
                    elif section == "improvements":
                        improvements.append(text)
                    elif section == "suggestions":
                        suggestions.append(text)

            enriched = dict(db_result)
            enriched.update({
                "strengths":    strengths,
                "improvements": improvements,
                "suggestions":  suggestions,
                "type_token_ratio": db_result.get("type_token_ratio"),
                "total_words":  db_result.get("total_words"),
                "filler_rate":  db_result.get("filler_rate"),
            })
            return enriched
        except Exception as exc:
            logger.warning("Could not enrich analysis result: %s", exc)
            return db_result

    def _on_view_session_from_history(self, session_id: int) -> None:
        """History page View button → show full session results."""
        logger.info("View session from history  session_id=%s", session_id)
        row = self._history_page.get_current_row()
        if not row:
            return
        analysis_result = {
            "overall_score":    row.get("overall_score"),
            "fluency_score":    row.get("fluency_score"),
            "vocabulary_score": row.get("vocabulary_score"),
            "grammar_score":    row.get("grammar_score"),
            "confidence_score": row.get("confidence_score"),
            "filler_word_count": row.get("filler_word_count"),
            "words_per_minute": row.get("words_per_minute"),
            "pause_count":      row.get("pause_count"),
            "feedback":         row.get("feedback", ""),
        }
        analysis_result = self._enrich_analysis_result(analysis_result)
        topic = {
            "word":               row.get("word", "—"),
            "category":           row.get("category"),
            "difficulty":         row.get("difficulty"),
            "difficulty_display": row.get("difficulty"),
        }
        self._results_page.prepare(
            session_id=session_id,
            topic=topic,
            analysis_result=analysis_result,
            duration_seconds=row.get("duration_seconds", 0),
            transcript=row.get("transcript", ""),
        )
        self._navigate_to(PAGE_RESULTS)

    def _on_practice_again(self) -> None:
        """User clicked Practice Again on the Results page."""
        logger.info("Practice Again requested from Results page")
        self._navigate_to(PAGE_PRACTICE)

    def _on_go_to_dashboard(self) -> None:
        """User clicked Back to Dashboard on the Results page."""
        logger.info("Back to Dashboard from Results page")
        self._navigate_to(PAGE_DASHBOARD)
        # Refresh dashboard stats
        if hasattr(self._dashboard_page, "refresh"):
            self._dashboard_page.refresh()

    def _on_view_session_from_dashboard(self, session_id: int) -> None:
        """
        Called when the user clicks View on a row in the Dashboard's
        Recent Sessions table.  Loads the full session data and navigates
        to SessionResultsPage.
        """
        logger.info("View session from dashboard  session_id=%s", session_id)

        row = self._dashboard_page.get_current_view_row()

        if not row:
            logger.warning("No row data for session_id=%s", session_id)
            return

        # Build analysis_result dict from the row (already has all analysis keys)
        analysis_result = {
            "overall_score":    row.get("overall_score"),
            "fluency_score":    row.get("fluency_score"),
            "vocabulary_score": row.get("vocabulary_score"),
            "grammar_score":    row.get("grammar_score"),
            "confidence_score": row.get("confidence_score"),
            "filler_word_count": row.get("filler_word_count"),
            "words_per_minute": row.get("words_per_minute"),
            "pause_count":      row.get("pause_count"),
            "feedback":         row.get("feedback", ""),
        }

        # Enrich with structured feedback lists
        analysis_result = self._enrich_analysis_result(analysis_result)

        topic = {
            "word":               row.get("word", "—"),
            "category":           row.get("category"),
            "difficulty":         row.get("difficulty"),
            "difficulty_display": row.get("difficulty"),
        }

        self._results_page.prepare(
            session_id=session_id,
            topic=topic,
            analysis_result=analysis_result,
            duration_seconds=row.get("duration_seconds", 0),
            transcript=row.get("transcript", ""),
        )
        self._navigate_to(PAGE_RESULTS)

    # ------------------------------------------------------------------ #
    # Widget factories                                                      #
    # ------------------------------------------------------------------ #

    def _make_nav_button(self, icon: str, label: str, page_idx: int) -> QPushButton:
        btn = QPushButton(f"  {icon}   {label}")
        btn.setObjectName("NavButton")
        btn.setCheckable(True)
        btn.setFixedHeight(44)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.clicked.connect(lambda checked, idx=page_idx: self._navigate_to(idx))
        btn.setAccessibleName(f"Navigate to {label}")
        btn.setToolTip(label)
        return btn

    @staticmethod
    def _make_h_separator() -> QFrame:
        sep = QFrame()
        sep.setObjectName("HSeparator")
        sep.setFixedHeight(1)
        sep.setFrameShape(QFrame.Shape.HLine)
        return sep

    # ------------------------------------------------------------------ #
    # Stylesheet                                                            #
    # ------------------------------------------------------------------ #

    def _apply_styles(self) -> None:
        c = settings  # shorthand

        self.setStyleSheet(f"""

/* ── Root ─────────────────────────────────────────────────────────── */
QMainWindow, #RootWidget {{
    background-color: {c.COLOR_BG_DARK};
    color: {c.COLOR_TEXT_PRIMARY};
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 14px;
}}

/* ── Sidebar ──────────────────────────────────────────────────────── */
#Sidebar {{
    background-color: {c.COLOR_BG_SIDEBAR};
}}

#SidebarDivider, #HSeparator {{
    background-color: {c.COLOR_BORDER};
    color: {c.COLOR_BORDER};
}}

/* Logo area */
#LogoContainer {{
    background-color: {c.COLOR_BG_SIDEBAR};
}}

#AppNameLabel {{
    font-size: 18px;
    font-weight: 700;
    color: {c.COLOR_TEXT_PRIMARY};
    letter-spacing: 0.5px;
}}

#LogoBadge {{
    font-size: 18px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2A2366, stop:1 #1B1840);
    border: 1px solid rgba(108, 99, 255, 0.45);
    border-radius: 10px;
}}

#TaglineLabel {{
    font-size: 11px;
    color: {c.COLOR_TEXT_MUTED};
    letter-spacing: 0.3px;
}}

/* Nav buttons */
#NavButton {{
    background-color: transparent;
    color: {c.COLOR_TEXT_SECONDARY};
    border: none;
    border-radius: 8px;
    padding: 0px 12px;
    text-align: left;
    font-size: 14px;
    font-weight: 400;
}}

#NavButton:hover {{
    background-color: rgba(108, 99, 255, 0.12);
    color: {c.COLOR_TEXT_PRIMARY};
}}

#NavButton:checked {{
    background-color: rgba(108, 99, 255, 0.20);
    color: {c.COLOR_ACCENT};
    font-weight: 600;
}}

#NavButton:pressed {{
    background-color: rgba(108, 99, 255, 0.28);
}}

/* Footer */
#SidebarFooter {{
    background-color: {c.COLOR_BG_SIDEBAR};
}}

#UserAvatar {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8A82FF, stop:1 #5A50E6);
    color: #FFFFFF;
    border-radius: 18px;
    font-size: 13px;
    font-weight: 700;
}}

#UserNameLabel {{
    font-size: 13px;
    font-weight: 600;
    color: {c.COLOR_TEXT_PRIMARY};
}}

#AccountTypeLabel {{
    font-size: 11px;
    color: {c.COLOR_TEXT_MUTED};
}}

#SwitchProfileButton, #SignOutButton {{
    background-color: transparent;
    border: 1px solid {c.COLOR_BORDER};
    border-radius: 8px;
    padding: 0px 10px;
    font-size: 12px;
    font-weight: 600;
}}

#SwitchProfileButton {{
    color: {c.COLOR_TEXT_SECONDARY};
}}

#SwitchProfileButton:hover {{
    color: {c.COLOR_TEXT_PRIMARY};
    border-color: {c.COLOR_ACCENT};
    background-color: rgba(108, 99, 255, 0.10);
}}

#SignOutButton {{
    color: #FF8A80;
}}

#SignOutButton:hover {{
    border-color: {c.COLOR_DANGER};
    background-color: rgba(244, 67, 54, 0.10);
}}

#VersionLabel {{
    font-size: 11px;
    color: {c.COLOR_TEXT_MUTED};
}}

/* ── Content stack ────────────────────────────────────────────────── */
#ContentStack {{
    background-color: {c.COLOR_BG_DARK};
}}

/* ── Scrollbars ───────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {c.COLOR_BG_DARK};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {c.COLOR_BORDER};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c.COLOR_TEXT_MUTED};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background: {c.COLOR_BG_DARK};
    height: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {c.COLOR_BORDER};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c.COLOR_TEXT_MUTED};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ── General widget defaults ─────────────────────────────────────── */
QLabel {{
    color: {c.COLOR_TEXT_PRIMARY};
    background: transparent;
}}

QPushButton {{
    background-color: {c.COLOR_ACCENT};
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: 600;
}}

QPushButton:hover {{
    background-color: {c.COLOR_ACCENT_HOVER};
}}

QPushButton:pressed {{
    background-color: #5a52e0;
}}

QPushButton:disabled {{
    background-color: {c.COLOR_BORDER};
    color: {c.COLOR_TEXT_MUTED};
}}

QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {c.COLOR_BG_CARD};
    color: {c.COLOR_TEXT_PRIMARY};
    border: 1px solid {c.COLOR_BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 14px;
    selection-background-color: {c.COLOR_ACCENT};
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {c.COLOR_ACCENT};
}}

QComboBox {{
    background-color: {c.COLOR_BG_CARD};
    color: {c.COLOR_TEXT_PRIMARY};
    border: 1px solid {c.COLOR_BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 14px;
}}

QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}

QComboBox QAbstractItemView {{
    background-color: {c.COLOR_BG_CARD};
    color: {c.COLOR_TEXT_PRIMARY};
    border: 1px solid {c.COLOR_BORDER};
    selection-background-color: {c.COLOR_ACCENT};
}}

QToolTip {{
    background-color: {c.COLOR_BG_CARD};
    color: {c.COLOR_TEXT_PRIMARY};
    border: 1px solid {c.COLOR_BORDER};
    padding: 6px 10px;
    border-radius: 6px;
    font-size: 12px;
}}
        """)
