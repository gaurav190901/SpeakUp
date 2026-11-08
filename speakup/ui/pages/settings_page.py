"""
SettingsPage — application preferences.

Sections:
    Profile      — display name (read-only; future: editable)
    Practice     — difficulty preference + daily goal (SAVED to DB)
    Appearance   — theme info
    Data         — local-only notice
    About        — version/tech info
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings
from speakup.services.gamification_service import GamificationService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reusable widgets
# ---------------------------------------------------------------------------

class SettingsSection(QFrame):
    """A titled card grouping related settings."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsSection")

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(24, 20, 24, 20)
        self._outer.setSpacing(16)

        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        self._outer.addWidget(title_label)

        self._body = QVBoxLayout()
        self._body.setSpacing(12)
        self._outer.addLayout(self._body)

        self.setStyleSheet(f"""
            #SettingsSection {{
                background-color: {settings.COLOR_BG_CARD};
                border: 1px solid {settings.COLOR_BORDER};
                border-radius: 12px;
            }}
            #SectionTitle {{
                font-size: 15px;
                font-weight: 600;
                color: {settings.COLOR_TEXT_PRIMARY};
                padding-bottom: 4px;
                border-bottom: 1px solid {settings.COLOR_BORDER};
            }}
        """)

    def add_row(self, widget: QWidget) -> None:
        self._body.addWidget(widget)


class SettingsRow(QWidget):
    """A label + control row."""

    def __init__(
        self,
        label: str,
        control: QWidget,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(2)
        lbl = QLabel(label)
        lbl.setObjectName("RowLabel")
        left.addWidget(lbl)
        if description:
            desc = QLabel(description)
            desc.setObjectName("RowDescription")
            desc.setWordWrap(True)
            left.addWidget(desc)

        layout.addLayout(left, stretch=1)
        layout.addWidget(control)

        self.setStyleSheet(f"""
            #RowLabel {{ font-size: 14px; font-weight: 500; color: {settings.COLOR_TEXT_PRIMARY}; }}
            #RowDescription {{ font-size: 12px; color: {settings.COLOR_TEXT_MUTED}; }}
        """)


class ReadOnlyField(QLineEdit):
    def __init__(self, value: str, parent: QWidget | None = None) -> None:
        super().__init__(value, parent)
        self.setReadOnly(True)
        self.setFixedWidth(220)
        self.setStyleSheet(f"""
            QLineEdit {{
                background-color: rgba(255,255,255,0.04);
                color: {settings.COLOR_TEXT_SECONDARY};
                border: 1px solid {settings.COLOR_BORDER};
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
            }}
        """)


class _SaveableCombo(QComboBox):
    """A styled combo box that can signal changes."""

    def __init__(self, options: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(220)
        for opt in options:
            self.addItem(opt)
        self.setStyleSheet(f"""
            QComboBox {{
                background-color: {settings.COLOR_BG_DARK};
                color: {settings.COLOR_TEXT_PRIMARY};
                border: 1px solid {settings.COLOR_BORDER};
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
            }}
            QComboBox::drop-down {{ border: none; padding-right: 12px; }}
            QComboBox QAbstractItemView {{
                background-color: {settings.COLOR_BG_CARD};
                color: {settings.COLOR_TEXT_PRIMARY};
                border: 1px solid {settings.COLOR_BORDER};
                selection-background-color: {settings.COLOR_ACCENT};
                padding: 4px;
            }}
        """)


# ---------------------------------------------------------------------------
# SettingsPage
# ---------------------------------------------------------------------------

# Difficulty options: display label → DB value
_DIFFICULTY_OPTIONS: list[tuple[str, str]] = [
    ("All Levels (default)", "all"),
    ("Beginner (Easy)",      "easy"),
    ("Intermediate",         "medium"),
    ("Advanced (Hard)",      "hard"),
]

# Goal options: display label → DB value
_GOAL_OPTIONS: list[tuple[str, str]] = [
    ("1 session per day",   "sessions"),
    ("5 minutes per day",   "minutes_5"),
    ("10 minutes per day",  "minutes_10"),
]


class SettingsPage(QWidget):
    """Application settings with functional Practice preferences."""

    # Emitted when the user saves changes — MainWindow / PracticePage can listen
    preferences_saved = Signal()
    # Emitted when the user clicks "Manage Data" — MainWindow navigates
    open_data_management = Signal()

    def __init__(
        self, user_id: int = 1, user_name: str = "Local User",
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._user_id   = user_id
        self._user_name = user_name
        self._gam_svc   = GamificationService()
        self._build_ui()
        self._load_preferences()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("SettingsContent")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(32, 32, 32, 32)
        self._layout.setSpacing(24)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Page header
        page_title = QLabel("Settings")
        page_title.setObjectName("PageTitle")
        page_subtitle = QLabel("Manage your profile and practice preferences")
        page_subtitle.setObjectName("PageSubtitle")
        self._layout.addWidget(page_title)
        self._layout.addWidget(page_subtitle)

        self._layout.addWidget(self._build_profile_section())
        self._layout.addWidget(self._build_practice_section())
        self._layout.addWidget(self._build_appearance_section())
        self._layout.addWidget(self._build_data_section())
        self._layout.addWidget(self._build_about_section())
        self._layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._apply_styles()

    # ------------------------------------------------------------------ #
    # Section builders                                                      #
    # ------------------------------------------------------------------ #

    def _build_profile_section(self) -> SettingsSection:
        sec = SettingsSection("👤  Profile")
        sec.add_row(SettingsRow(
            "Display Name",
            ReadOnlyField(self._user_name),
            "Stored locally on this device.",
        ))
        sec.add_row(SettingsRow(
            "Account Type",
            ReadOnlyField("Local (offline)"),
        ))
        return sec

    def _build_practice_section(self) -> SettingsSection:
        sec = SettingsSection("🎤  Practice & Goals")

        # Difficulty preference
        self._diff_combo = _SaveableCombo([d[0] for d in _DIFFICULTY_OPTIONS])
        sec.add_row(SettingsRow(
            "Difficulty Level",
            self._diff_combo,
            "Controls which topics are offered in Practice. "
            "You can change this at any time — no progress is lost.",
        ))

        # Daily goal
        self._goal_combo = _SaveableCombo([g[0] for g in _GOAL_OPTIONS])
        sec.add_row(SettingsRow(
            "Daily Practice Goal",
            self._goal_combo,
            "Shown as a progress bar on the Dashboard. Choose what feels motivating.",
        ))

        # Save button
        save_row = QWidget()
        save_layout = QHBoxLayout(save_row)
        save_layout.setContentsMargins(0, 4, 0, 0)

        self._save_btn = QPushButton("Save Practice Preferences")
        self._save_btn.setObjectName("SavePrefsBtn")
        self._save_btn.setFixedHeight(38)
        self._save_btn.setFixedWidth(240)
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)

        self._save_status = QLabel("")
        self._save_status.setObjectName("SaveStatus")

        save_layout.addWidget(self._save_btn)
        save_layout.addSpacing(12)
        save_layout.addWidget(self._save_status)
        save_layout.addStretch()
        sec.add_row(save_row)

        return sec

    def _build_appearance_section(self) -> SettingsSection:
        sec = SettingsSection("🎨  Appearance")
        sec.add_row(SettingsRow(
            "Theme",
            ReadOnlyField("Dark"),
            "A calm dark theme that's easy on the eyes.",
        ))
        return sec

    def _build_data_section(self) -> SettingsSection:
        sec = SettingsSection("💾  Data & Privacy")
        uses_openai = (
            settings.TRANSCRIPTION_BACKEND == "openai_whisper"
            and settings.has_openai_key()
        )
        sec.add_row(SettingsRow(
            "Speech-to-text",
            ReadOnlyField("OpenAI Whisper (online)" if uses_openai
                          else "On this computer (offline)"),
            "Your recording's audio is sent to OpenAI to be transcribed."
            if uses_openai else
            "Transcription runs on this computer. Nothing is uploaded.",
        ))
        sec.add_row(SettingsRow(
            "Data Folder",
            ReadOnlyField(str(settings.APP_DATA_DIR)),
            "Your account, practice history and recordings are saved here.",
        ))

        # "Manage Data" button — navigates to DataManagementPage
        manage_btn = QPushButton("Manage Data →")
        manage_btn.setObjectName("SavePrefsBtn")
        manage_btn.setFixedHeight(36)
        manage_btn.setFixedWidth(180)
        manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        manage_btn.clicked.connect(self.open_data_management.emit)
        manage_btn.setAccessibleName("Open Data Management page")
        sec.add_row(manage_btn)
        return sec

    def _build_about_section(self) -> SettingsSection:
        sec = SettingsSection("ℹ️  About SpeakUp")
        for label, value in [
            ("Version",   settings.APP_VERSION),
            ("Framework", "PySide6  (Qt 6)"),
            ("Database",  "SQLite  via SQLAlchemy"),
            ("Python",    "3.11+"),
        ]:
            sec.add_row(SettingsRow(label, ReadOnlyField(value)))
        return sec

    # ------------------------------------------------------------------ #
    # Preferences load / save                                               #
    # ------------------------------------------------------------------ #

    def _load_preferences(self) -> None:
        """Load current preferences and update combo boxes."""
        try:
            prefs = self._gam_svc.get_preferences(self._user_id)
            diff  = prefs.get("difficulty_preference", "all")
            goal  = prefs.get("daily_goal_type", "sessions")

            diff_idx = next(
                (i for i, (_, v) in enumerate(_DIFFICULTY_OPTIONS) if v == diff), 0
            )
            self._diff_combo.setCurrentIndex(diff_idx)

            goal_idx = next(
                (i for i, (_, v) in enumerate(_GOAL_OPTIONS) if v == goal), 0
            )
            self._goal_combo.setCurrentIndex(goal_idx)
        except Exception as exc:
            logger.warning("Could not load preferences: %s", exc)

    def _on_save(self) -> None:
        """Persist the selected difficulty and goal to the DB."""
        try:
            diff_display = self._diff_combo.currentText()
            goal_display = self._goal_combo.currentText()

            diff_db = next(
                v for d, v in _DIFFICULTY_OPTIONS if d == diff_display
            )
            goal_db = next(
                v for d, v in _GOAL_OPTIONS if d == goal_display
            )

            self._gam_svc.save_preferences(
                self._user_id,
                difficulty_preference=diff_db,
                daily_goal_type=goal_db,
            )
            self._save_status.setText("✅  Saved")
            self._save_status.setStyleSheet(
                f"color: {settings.COLOR_SUCCESS}; font-size: 13px;"
            )
            logger.info(
                "Preferences saved: difficulty=%r goal=%r", diff_db, goal_db
            )
            self.preferences_saved.emit()
        except Exception as exc:
            logger.error("Failed to save preferences: %s", exc)
            self._save_status.setText("⚠️  Save failed")
            self._save_status.setStyleSheet(
                f"color: {settings.COLOR_DANGER}; font-size: 13px;"
            )

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        """Reload preferences from DB (call when navigating to this page)."""
        self._load_preferences()
        self._save_status.setText("")

    # ------------------------------------------------------------------ #
    # Styles                                                                #
    # ------------------------------------------------------------------ #

    def _apply_styles(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #SettingsScroll, #SettingsContent {{
                background-color: {c.COLOR_BG_DARK};
            }}
            #PageTitle {{
                font-size: 24px;
                font-weight: 700;
                color: {c.COLOR_TEXT_PRIMARY};
            }}
            #PageSubtitle {{
                font-size: 14px;
                color: {c.COLOR_TEXT_SECONDARY};
            }}
            #SavePrefsBtn {{
                background-color: {c.COLOR_ACCENT};
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
            }}
            #SavePrefsBtn:hover {{
                background-color: {c.COLOR_ACCENT_HOVER};
            }}
        """)
